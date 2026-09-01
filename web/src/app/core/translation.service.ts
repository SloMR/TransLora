// Per-document orchestration: the prepass, the batch worker pool, the
// after-the-fact checks that flag cues, the capped repair of the batches they
// land in, and stitching the translated blocks back into the source document.

import { Injectable } from '@angular/core';
import {
  DEFAULT_BATCH_SIZE,
  DEFAULT_CONCURRENCY,
  DEFAULT_CONTEXT_OVERLAP,
  DEFAULT_DIALECT,
  DEFAULT_FIX_FLAGGED,
  DEFAULT_FORMALITY,
  DEFAULT_FULL_ATTRIBUTION,
  DEFAULT_MAX_RETRIES,
  DEFAULT_REFINE_ATTRIBUTION,
  DEFAULT_REFLOW,
  DEFAULT_REVIEW,
  DEFAULT_SCAN_BUDGET,
  DEFAULT_SEND_TEMPERATURE,
  DEFAULT_VERIFY_ADEQUACY,
  Formality,
  REQUEST_TIMEOUT_SECS,
  SYSTEMATIC_CAUSE_MIN,
} from './constants';
import { effectiveNorms, grammarNoteFor } from './languages';
import {
  BatchFlag,
  BatchOptions,
  BatchResult,
  retryFlaggedBatch,
  translateBatch,
} from './batch-runner';
import {
  ChatClient,
  ProviderConfig,
  effectiveDialect,
  throwIfCancelled,
} from './chat-client';
import {
  FileContext,
  PhraseSplit,
  findInconsistentPhrases,
  phraseSplitMessage,
} from './context-pass';
import { detectVariantDrift, normalizeDiacritics, variantDriftMessage } from './repair';
import { verifyAdequacy } from './adequacy';
import {
  CallKind,
  RunStats,
  emptyCallCounts,
  fixFlaggedCap,
  systematicRepairCap,
} from './run-stats';
import { ScanOptions, extractFileContext, refineSceneAttribution } from './context-scan';
import { SubtitleBlock, splitBatches } from './srt-parser';
import { SubtitleDocument } from './subtitle-formats/types';

// Kept on the service's own surface: the app imports these from here.
export { TranslationCancelledError } from './chat-client';
export type { ProviderConfig } from './chat-client';
export type { Formality };

export interface TranslationProgress {
  currentBatch: number;
  totalBatches: number;
}

export interface QualityOptions {
  contextOverlap?: number;
  scanBudget?: number;
  refineAttribution?: boolean;
  review?: boolean;
  /** Re-wrap a translated cue to the source's line count without a model call. */
  reflow?: boolean;
  /** One capped retry for the batches the run flagged. */
  fixFlagged?: boolean;
  /** Back-translate a sample of the file and flag cues that lost meaning. */
  verifyAdequacy?: boolean;
  /** Attribute speakers in every scene with a cast, not just mixed-gender ones. */
  fullAttribution?: boolean;
  /** 0 or unset = the target script's own default. */
  maxLineChars?: number;
  /** 'auto' leaves the register to the model. */
  formality?: Formality;
  /** Free text, e.g. "Egyptian Arabic"; also steers the prepass register. */
  dialect?: string;
  /** false = never send `temperature`, for an endpoint known to reject it.
   * Otherwise the first 400 that names it teaches the run the same thing. */
  sendTemperature?: boolean;
  /** Send the review pass to its own provider. Each field falls back to the
   * main provider's, so a stronger reviewer can be one field's worth of change.
   * The key is held in memory for the run, exactly like the main one. */
  reviewApiUrl?: string;
  reviewApiKey?: string;
  reviewModel?: string;
  timeoutMs?: number;
  /** Called once per distinct message when part of the pipeline is skipped. */
  onNotice?: (message: string) => void;
  /** What the finished run cost: LLM calls by pass, blocks, elapsed time. */
  onStats?: (stats: RunStats) => void;
}

/** Everything the run carries into a batch or a prepass call. */
type RunOptions = BatchOptions & ScanOptions;

/** A batch index and everything flagged in it. */
interface FlaggedBatch {
  index: number;
  flags: BatchFlag[];
}

@Injectable({ providedIn: 'root' })
export class TranslationService {
  constructor(private chat: ChatClient) {}

  async translateDocument(
    doc: SubtitleDocument,
    sourceLang: string,
    targetLang: string,
    provider: ProviderConfig,
    batchSize = DEFAULT_BATCH_SIZE,
    concurrency = DEFAULT_CONCURRENCY,
    maxRetries = DEFAULT_MAX_RETRIES,
    onProgress?: (p: TranslationProgress) => void,
    cancelSignal?: AbortSignal,
    quality: QualityOptions = {},
  ): Promise<string> {
    if (doc.blocks.length === 0) {
      throw new Error('No subtitle blocks found in file');
    }
    throwIfCancelled(cancelSignal);

    const startedAt = performance.now();
    const calls = emptyCallCounts();
    const contextOverlap = quality.contextOverlap ?? DEFAULT_CONTEXT_OVERLAP;
    const scanBudget = quality.scanBudget ?? DEFAULT_SCAN_BUDGET;
    const refineAttribution = quality.refineAttribution ?? DEFAULT_REFINE_ATTRIBUTION;
    const review = quality.review ?? DEFAULT_REVIEW;
    const fixFlagged = quality.fixFlagged ?? DEFAULT_FIX_FLAGGED;
    const checkAdequacy = quality.verifyAdequacy ?? DEFAULT_VERIFY_ADEQUACY;
    const fullAttribution = quality.fullAttribution ?? DEFAULT_FULL_ATTRIBUTION;
    const run: RunOptions = {
      timeoutMs: quality.timeoutMs && quality.timeoutMs > 0
        ? quality.timeoutMs
        : REQUEST_TIMEOUT_SECS * 1000,
      notify: onceEach(quality.onNotice),
      count: (kind: CallKind) => { calls[kind]++; },
      reflow: quality.reflow ?? DEFAULT_REFLOW,
      reviewProvider: reviewProviderFor(provider, quality),
      norms: effectiveNorms(targetLang, quality.maxLineChars ?? 0),
      formality: quality.formality ?? DEFAULT_FORMALITY,
      dialect: quality.dialect ?? DEFAULT_DIALECT,
      sendTemperature: quality.sendTemperature ?? DEFAULT_SEND_TEMPERATURE,
    };
    // A zero or NaN concurrency would start no workers and "succeed" untranslated.
    const workers = Math.max(1, Math.floor(concurrency) || DEFAULT_CONCURRENCY);

    const fileContext = await extractFileContext(
      this.chat, doc.blocks, sourceLang, targetLang, provider, scanBudget, run,
      cancelSignal,
    );
    if (refineAttribution && !fileContext.isEmpty()) {
      await refineSceneAttribution(
        this.chat, fileContext, doc.blocks, provider, workers, run, cancelSignal,
        fullAttribution, grammarNoteFor(run.norms.script) !== '',
      );
    }

    const batches = splitBatches(doc.blocks, batchSize);
    const results: BatchResult[] = new Array(batches.length);

    let nextIdx = 0;
    let completed = 0;
    const emit = () => onProgress?.({
      currentBatch: completed,
      totalBatches: batches.length,
    });

    // Cancel plus first fatal failure, so siblings stop paying for discarded work.
    const stop = new AbortController();
    const onCancel = () => stop.abort();
    cancelSignal?.addEventListener('abort', onCancel, { once: true });
    let fatalError: unknown = null;

    const worker = async () => {
      while (true) {
        if (stop.signal.aborted) return;
        const i = nextIdx++;
        if (i >= batches.length) return;
        try {
          results[i] = await translateBatch(
            this.chat, batches[i], sourceLang, targetLang, provider, maxRetries,
            fileContext, tailBefore(batches, i, contextOverlap), contextOverlap,
            review, run, stop.signal,
          );
        } catch (err) {
          // First error wins: the siblings it aborts only report cancellation.
          fatalError ??= err;
          stop.abort();
          return;
        }
        completed++;
        emit();
      }
    };

    emit();
    const workerCount = Math.min(workers, batches.length);
    try {
      await Promise.all(Array.from({ length: workerCount }, worker));
    } finally {
      cancelSignal?.removeEventListener('abort', onCancel);
    }
    throwIfCancelled(cancelSignal);
    if (fatalError) throw fatalError;

    const flagged: FlaggedBatch[] = results.map((r, index) => ({
      index,
      flags: r ? [...r.flags] : [],
    }));

    if (checkAdequacy) {
      const extra = await verifyAdequacy(
        this.chat, batches, results.map((r) => r.blocks), sourceLang, provider,
        workers, run, cancelSignal,
      );
      for (const [index, flags] of extra) {
        for (const flag of flags) run.notify(flag.message);
        flagged[index]!.flags.push(...flags);
      }
    }

    // File-level, and before the repair so the batches a split phrase landed in
    // can be re-issued: no batch can see that the file rendered it two ways.
    const splits = findInconsistentPhrases(
      doc.blocks, results.flatMap((r) => (r ? r.blocks : [])),
    );
    for (const split of splits) run.notify(phraseSplitMessage(split));
    if (splits.length) flagPhraseSplits(flagged, batches, splits);

    if (fixFlagged) {
      await this.repairFlagged(
        batches, results, flagged, sourceLang, targetLang, provider,
        fileContext, contextOverlap, workers, run, cancelSignal,
      );
    }

    const translated: SubtitleBlock[] = [];
    for (const r of results) if (r) translated.push(...r.blocks);
    // A hole in `results` would rebuild the untranslated source and call it done.
    if (translated.length !== doc.blocks.length) {
      throw new Error(
        `Internal error: assembled ${translated.length} of ${doc.blocks.length} blocks`,
      );
    }
    quality.onStats?.({
      calls,
      blocks: doc.blocks.length,
      elapsedMs: performance.now() - startedAt,
      dialect: effectiveDialect(this.chat.dialectFor(provider), run),
    });
    // File-level: only the whole file says whether its baseline is vocalized,
    // or which variant of the target language it ended up written in.
    const finished = normalizeDiacritics(translated, run.norms.script);
    const drift = detectVariantDrift(finished, run.norms.script, run.dialect);
    if (drift) run.notify(variantDriftMessage(drift));
    return doc.rebuild(finished);
  }

  /** One retry each for the flagged batches, chosen by cause rather than by
   * position and capped at a share of the file. Whatever the cap leaves out is
   * reported, never dropped quietly. */
  private async repairFlagged(
    batches: SubtitleBlock[][],
    results: BatchResult[],
    flagged: FlaggedBatch[],
    sourceLang: string,
    targetLang: string,
    provider: ProviderConfig,
    fileContext: FileContext,
    contextOverlap: number,
    workers: number,
    run: RunOptions,
    cancelSignal?: AbortSignal,
  ): Promise<void> {
    // Indexed by batch, so the list is already deduplicated and in block order.
    const targets = flagged.filter((entry) => entry.flags.length > 0);
    if (!targets.length) return;
    const causes = groupByCause(targets);
    // The wider ceiling is for a failure that repeats; a scatter of one-offs
    // still gets the ordinary cap.
    const systematic = [...causes.values()].some(
      (group) => group.length >= SYSTEMATIC_CAUSE_MIN);
    const cap = systematic
      ? systematicRepairCap(batches.length)
      : fixFlaggedCap(batches.length);
    const chosen = chooseByCause(causes, cap);
    if (targets.length > chosen.length) {
      run.notify(
        `${targets.length} flagged batch(es) across ${causes.size} cause(s); `
        + `repairing ${chosen.length} (cap ${cap}), `
        + `leaving ${targets.length - chosen.length}`,
      );
    }

    let nextIdx = 0;
    const worker = async () => {
      while (true) {
        throwIfCancelled(cancelSignal);
        const i = nextIdx++;
        if (i >= chosen.length) return;
        const { index, flags } = chosen[i]!;
        results[index] = await retryFlaggedBatch(
          this.chat, batches[index]!, sourceLang, targetLang, provider,
          fileContext, tailBefore(batches, index, contextOverlap),
          results[index]!, flags, run, cancelSignal,
        );
      }
    };
    const workerCount = Math.min(Math.max(1, workers), chosen.length);
    await Promise.all(Array.from({ length: workerCount }, worker));
  }
}


/** One flag per batch a split phrase landed in, all under the one cause: a
 * phrase the file rendered three ways is a single systematic problem, and
 * grouping it as one is what buys it the wider repair ceiling. */
function flagPhraseSplits(
  flagged: FlaggedBatch[], batches: SubtitleBlock[][], splits: PhraseSplit[],
): void {
  const batchOf = new Map<number, number>();
  batches.forEach((batch, index) => {
    for (const block of batch) batchOf.set(block.number, index);
  });
  for (const split of splits) {
    const message = phraseSplitMessage(split);
    const seen = new Set<number>();
    for (const block of split.blocks) {
      const index = batchOf.get(block);
      if (index === undefined || seen.has(index)) continue;
      seen.add(index);
      flagged[index]!.flags.push({
        block,
        message,
        problem: `block ${block}: the recurring phrase '${split.phrase}' is `
          + 'rendered differently elsewhere in the file; use one wording for it',
        cause: `phrase:${split.phrase}`,
      });
    }
  }
}

/** The flagged batches each cause landed in, in block order. A batch flagged
 * twice for one cause is one entry; a batch with two causes is under both. */
function groupByCause(targets: FlaggedBatch[]): Map<string, FlaggedBatch[]> {
  const causes = new Map<string, FlaggedBatch[]>();
  for (const entry of targets) {
    for (const cause of new Set(entry.flags.map((flag) => flag.cause))) {
      const group = causes.get(cause);
      if (group) group.push(entry);
      else causes.set(cause, [entry]);
    }
  }
  return causes;
}

/** Which flagged batches to re-issue: rarest cause first, so one failure
 * repeated across the file cannot crowd out the one-offs, and earliest block
 * first within a cause. Sorted back into block order to be sent. */
function chooseByCause(
  causes: Map<string, FlaggedBatch[]>, cap: number,
): FlaggedBatch[] {
  // Stable, so causes of equal size keep the order they were first flagged in.
  const groups = [...causes.values()].sort((a, b) => a.length - b.length);
  const chosen: FlaggedBatch[] = [];
  const taken = new Set<number>();
  for (const group of groups) {
    if (chosen.length >= cap) break;
    for (const entry of group) {
      if (chosen.length >= cap) break;
      if (taken.has(entry.index)) continue;
      taken.add(entry.index);
      chosen.push(entry);
    }
  }
  return chosen.sort((a, b) => a.index - b.index);
}

/** Where the review pass goes: the main provider, field by field, unless the
 * run overrode one. */
export function reviewProviderFor(
  provider: ProviderConfig, quality: QualityOptions,
): ProviderConfig {
  return {
    apiUrl: quality.reviewApiUrl?.trim() || provider.apiUrl,
    apiKey: quality.reviewApiKey?.trim() || provider.apiKey,
    model: quality.reviewModel?.trim() || provider.model,
  };
}

/** The previous batch's tail, shown to a batch as read-only context. */
function tailBefore(
  batches: SubtitleBlock[][], index: number, contextOverlap: number,
): SubtitleBlock[] {
  return index > 0 && contextOverlap > 0
    ? batches[index - 1]!.slice(-contextOverlap)
    : [];
}

// A per-batch failure would otherwise notify once for every batch in the file.
function onceEach(fn?: (message: string) => void): (message: string) => void {
  if (!fn) return () => undefined;
  const seen = new Set<string>();
  return (message: string) => {
    if (seen.has(message)) return;
    seen.add(message);
    fn(message);
  };
}
