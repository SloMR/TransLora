// The back-translation spot check: a sample of finished batches is translated
// back to the source language and compared with the source it came from, so a
// cue that quietly dropped half its meaning gets flagged for repair.
//
// Opt-in — it costs one call per sampled batch, about a fifth of the file.

import { ADEQUACY_MIN_OVERLAP } from './constants';
import { BatchFlag, BatchOptions } from './batch-runner';
import {
  ChatClient,
  ProviderConfig,
  TranslationCancelledError,
  describeError,
  stripMarkdownFences,
  throwIfCancelled,
} from './chat-client';
import { contentWords } from './repair';
import { SubtitleBlock, parseLite, serializeLite } from './srt-parser';
import {
  BACK_TRANSLATION_SYSTEM_PROMPT,
  buildBackTranslationUserMessage,
} from './translation-prompt';
import { adequacySampleSize, sampleIndices } from './run-stats';

/** Share of the source cue's distinct words the back-translation returned.
 * A source cue with no words at all counts as fully covered. */
export function tokenOverlap(sourceText: string, backText: string): number {
  const wanted = new Set(contentWords(sourceText));
  if (!wanted.size) return 1;
  const returned = new Set(contentWords(backText));
  let hits = 0;
  for (const word of wanted) if (returned.has(word)) hits++;
  return hits / wanted.size;
}

/** Flags for every cue in the sample whose meaning did not survive the round
 * trip, keyed by the batch index they belong to. Best-effort: a failed
 * back-translation is a missing opinion, never a failed file. */
export async function verifyAdequacy(
  chat: ChatClient,
  batches: SubtitleBlock[][],
  outputs: SubtitleBlock[][],
  sourceLang: string,
  provider: ProviderConfig,
  concurrency: number,
  run: BatchOptions,
  cancelSignal?: AbortSignal,
): Promise<Map<number, BatchFlag[]>> {
  const found = new Map<number, BatchFlag[]>();
  const sample = sampleIndices(batches.length, adequacySampleSize(batches.length))
    .filter((index) => outputs[index]?.length);
  if (!sample.length) return found;

  let nextIdx = 0;
  let failures = 0;
  const worker = async () => {
    while (true) {
      throwIfCancelled(cancelSignal);
      const i = nextIdx++;
      if (i >= sample.length) return;
      const index = sample[i]!;
      const output = outputs[index]!;
      const wire = serializeLite(output);
      try {
        run.count('backTranslation');
        const raw = await chat.callChat(
          BACK_TRANSLATION_SYSTEM_PROMPT,
          buildBackTranslationUserMessage(sourceLang, wire),
          provider, maxTokensFor(wire, output.length), run, cancelSignal,
        );
        const flags = compareBackTranslation(
          batches[index]!, parseLite(stripMarkdownFences(raw)),
        );
        if (flags.length) found.set(index, flags);
      } catch (err) {
        if (err instanceof TranslationCancelledError) throw err;
        failures++;
        console.warn('Back-translation failed:', describeError(err));
      }
    }
  };

  const workerCount = Math.min(Math.max(1, concurrency), sample.length);
  await Promise.all(Array.from({ length: workerCount }, worker));
  if (failures) {
    run.notify('Adequacy check failed for some batches - continuing without them');
  }
  return found;
}

// Matched by block number, so a reply that dropped or reordered a block only
// costs us the cues it actually lost.
function compareBackTranslation(
  batch: SubtitleBlock[], back: SubtitleBlock[],
): BatchFlag[] {
  const byNumber = new Map(back.map((b) => [b.number, b.text]));
  const flags: BatchFlag[] = [];
  for (const src of batch) {
    const returned = byNumber.get(src.number);
    if (returned === undefined) continue;
    const overlap = tokenOverlap(src.text, returned);
    if (overlap >= ADEQUACY_MIN_OVERLAP) continue;
    flags.push({
      block: src.number,
      message: `Block ${src.number}: the back-translation recovered only `
        + `${Math.round(overlap * 100)}% of the source wording`,
      problem: `block ${src.number}: the translation leaves out part of what `
        + 'the source says',
      cause: 'adequacy',
    });
  }
  return flags;
}

// Same shape as the translation budget: a flat per-block figure truncates a
// batch of long cues, and a truncated reply reads as lost meaning.
function maxTokensFor(wire: string, blockCount: number): number {
  return Math.max(300, wire.length + 60 * blockCount);
}
