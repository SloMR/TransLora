// The prepass calls: one scan for the file's glossary, then one small
// attribution call per scene that mixes known genders. Both are best-effort —
// on failure the pipeline continues without the hints.

import { SCAN_MAX_TOKENS } from './constants';
import {
  ChatClient,
  ChatOptions,
  ProviderConfig,
  TranslationCancelledError,
  describeError,
  stripMarkdownFences,
  throwIfCancelled,
} from './chat-client';
import { parseAttributionResponse, parseContextResponse } from './context-parse';
import {
  FileContext,
  enrichScenesWithBlockText,
  genderMark,
  needsAttribution,
  recurringPhrases,
  serializeForScan,
  type CharacterHint,
  type SceneHint,
} from './context-pass';
import {
  ATTRIBUTION_SYSTEM_PROMPT,
  CONTEXT_SYSTEM_PROMPT,
  buildAttributionUserMessage,
  buildScanUserMessage,
} from './translation-prompt';
import { CallKind } from './run-stats';
import { SubtitleBlock } from './srt-parser';

/** What the prepass needs from the run's settings. */
export interface ScanOptions extends ChatOptions {
  /** Tallies one LLM call so the run can report what it cost. */
  count: (kind: CallKind) => void;
  dialect: string;
}

export async function extractFileContext(
  chat: ChatClient,
  blocks: SubtitleBlock[],
  sourceLang: string,
  targetLang: string,
  provider: ProviderConfig,
  scanBudget: number,
  run: ScanOptions,
  cancelSignal?: AbortSignal,
): Promise<FileContext> {
  const userMessage = buildScanUserMessage(
    sourceLang, targetLang, serializeForScan(blocks, scanBudget), run.dialect,
    recurringPhrases(blocks),
  );
  try {
    run.count('scan');
    const raw = await chat.callChat(
      CONTEXT_SYSTEM_PROMPT, userMessage, provider, SCAN_MAX_TOKENS, run, cancelSignal,
    );
    const ctx = parseContextResponse(stripMarkdownFences(raw));
    if (ctx.isEmpty()) return ctx;
    return enrichScenesWithBlockText(ctx, blocks);
  } catch (err) {
    if (err instanceof TranslationCancelledError) throw err;
    console.warn('Context scan failed, proceeding without:', describeError(err));
    run.notify('Glossary scan failed - continuing without context hints');
    return new FileContext();
  }
}

export async function refineSceneAttribution(
  chat: ChatClient,
  ctx: FileContext,
  blocks: SubtitleBlock[],
  provider: ProviderConfig,
  concurrency: number,
  run: ScanOptions,
  cancelSignal?: AbortSignal,
  full = false,
  targetInflects = false,
): Promise<void> {
  const genderBy = new Map(
    ctx.characters.map((h) => [h.source.toLowerCase(), h.gender]),
  );
  const targets = ctx.scenes.filter(
    (s) => needsAttribution(s, genderBy, full, targetInflects),
  );
  if (!targets.length) return;

  let nextIdx = 0;
  let failures = 0;
  const worker = async () => {
    while (true) {
      throwIfCancelled(cancelSignal);
      const i = nextIdx++;
      if (i >= targets.length) return;
      const scene = targets[i];
      const prompt = buildSceneAttributionMessage(scene, blocks, ctx.characters);
      if (!prompt) continue;
      try {
        run.count('attribution');
        const raw = await chat.callChat(
          ATTRIBUTION_SYSTEM_PROMPT, prompt.message, provider,
          prompt.lineCount * 20 + 100, run, cancelSignal,
        );
        scene.attribution = parseAttributionResponse(
          stripMarkdownFences(raw), scene, ctx.characters,
        );
      } catch (err) {
        if (err instanceof TranslationCancelledError) throw err;
        failures++;
        console.warn(
          `Attribution failed for blocks ${scene.start}-${scene.end}:`,
          describeError(err),
        );
      }
    }
  };

  const workerCount = Math.min(concurrency, targets.length);
  await Promise.all(Array.from({ length: workerCount }, worker));
  if (failures) {
    run.notify('Speaker attribution failed - continuing without per-block speakers');
  }
}


function buildSceneAttributionMessage(
  scene: SceneHint,
  blocks: SubtitleBlock[],
  characters: CharacterHint[],
): { message: string; lineCount: number } | null {
  const present = new Set(scene.participants);
  const roster = characters
    .filter((h) => present.has(h.source))
    .map((h) => `- ${h.source} (${genderMark(h.gender) || '?'})`)
    .join('\n');
  // Walk the blocks that exist: an inflated range would drive the token budget.
  const sceneLines = blocks
    .filter((b) => b.number >= scene.start && b.number <= scene.end)
    .map((b) => `[${b.number}] ${b.text.replace(/\n/g, ' ')}`);
  if (!roster || !sceneLines.length) return null;
  return {
    message: buildAttributionUserMessage(roster, sceneLines),
    lineCount: sceneLines.length,
  };
}
