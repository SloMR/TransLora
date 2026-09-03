// Per-document orchestration: the prepass, the batch worker pool, the
// after-the-fact checks that flag cues, the capped repair of the batches they
// land in, and stitching the translated blocks back into the source document.

import { Injectable, inject } from '@angular/core';
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
  adequacySampleSize,
  fixFlaggedCap,
  systematicRepairCap,
} from './run-stats';
import { ProgressMeter, RunProgress } from './run-progress';
import { ScanOptions, extractFileContext, refineSceneAttribution } from './context-scan';
import { SubtitleBlock, splitBatches } from './srt-parser';
import { SubtitleDocument } from './subtitle-formats/types';

// Kept on the service's own surface: the app imports these from here.
export { TranslationCancelledError } from './chat-client';
export type { ProviderConfig } from './chat-client';
export type { Formality };

/** The file's progress, every step included: see `RunProgress`. */
export type TranslationProgress = RunProgress;

/** The translation as it will ship, cue by cue, and every flag that survived
 * the run — the detectors' verdict on the final text, plus the file-level and
 * adequacy flags no repair re-checks. */
export interface ReviewResult {
  blocks: SubtitleBlock[];
  /** What is still wrong with the shipped text. */
  flags: BatchFlag[];
  /** Every flag the run raised, repaired or not, so the host can say what was
   * fixed as well as what was left. One entry per cue and cause. */
  raised: BatchFlag[];
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
  /** Free text, e.g. "Saudi Arabic"; also steers the prepass register. */
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
  /** The finished cues and their surviving flags, for a review view. */
  onReview?: (review: ReviewResult) => void;
}

/** Everything the run carries into a batch or a prepass call. */
type RunOptions = BatchOptions & ScanOptions;

/** A batch index and everything flagged in it. */
interface FlaggedBatch {
  index: number;
  flags: BatchFlag[];
  /** How many of `flags` came from the detectors; the rest were appended by
   * the adequacy check or the phrase pass, which no repair re-evaluates. */
  detectorCount: number;
}

@Injectable({ providedIn: 'root' })
export class TranslationService {
  private chat = inject(ChatClient);


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
    const meter = new ProgressMeter(onProgress);
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
      count: (kind: CallKind) => { calls[kind]++; meter.count(kind); },
      reflow: quality.reflow ?? DEFAULT_REFLOW,
      reviewProvider: reviewProviderFor(provider, quality),
      norms: effectiveNorms(targetLang, quality.maxLineChars ?? 0),
      formality: quality.formality ?? DEFAULT_FORMALITY,
      dialect: quality.dialect ?? DEFAULT_DIALECT,
      sendTemperature: quality.sendTemperature ?? DEFAULT_SEND_TEMPERATURE,
    };
    // A zero or NaN concurrency would start no workers and "succeed" untranslated.
    const workers = Math.max(1, Math.floor(concurrency) || DEFAULT_CONCURRENCY);

    // The bar's plan, priced like the pre-run estimate and revised as the run
    // learns: how many scenes need a speaker call, how many batches a review,
    // how many retries the flags earn. Nothing here is a promise, only a pace.
    const batchCount = Math.ceil(doc.blocks.length / Math.max(1, batchSize));
    meter.plan('prepass', 1 + (refineAttribution ? Math.ceil(batchCount / 4) : 0));
    meter.plan('batches', batchCount * (review ? 2 : 1));
    if (checkAdequacy) meter.plan('checking', adequacySampleSize(batchCount));
    if (fixFlagged) meter.plan('repairing', fixFlaggedCap(batchCount));

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
    // Now the glossary is known, so is which batches earn a review call.
    const reviewed = review ? batches.filter((b) => fileContext.hasCorrections(b)).length : 0;
    meter.plan('batches', batches.length + reviewed);
    meter.batches(0, batches.length);
    meter.begin('batches');

    let nextIdx = 0;
    let completed = 0;
    const emit = () => meter.batches(completed, batches.length);

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
      detectorCount: r ? r.flags.length : 0,
    }));

    if (checkAdequacy) {
      meter.begin('checking');
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

    // Snapshot before any repair: the repairs below rewrite what survives.
    const raised = dedupeFlags(flagged.flatMap((entry) => entry.flags));
    let repaired: RepairOutcome = { attempted: new Set(), accepted: new Set() };
    if (fixFlagged && flagged.some((entry) => entry.flags.length > 0)) {
      meter.begin('repairing');
      repaired = await this.repairFlagged(
        batches, results, flagged, sourceLang, targetLang, provider,
        fileContext, contextOverlap, workers, run, cancelSignal, meter,
      );
      await this.repairCues(
        batches, results, flagged, repaired, sourceLang, targetLang, provider,
        fileContext, contextOverlap, workers, run, cancelSignal, meter,
      );
    } else {
      // Nothing to repair: the bar should not wait for calls that never come.
      meter.plan('repairing', 0);
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
    quality.onReview?.({
      blocks: finished,
      flags: survivingFlags(results, flagged, repaired.accepted),
      raised,
    });
    meter.finish();
    return doc.rebuild(finished);
  }

  /** One retry each for the flagged batches, chosen by cause rather than by
   * position and capped at a share of the file. Whatever the cap leaves out is
   * reported, never dropped quietly. Returns the batches it tried and the ones
   * whose retry it kept. */
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
    cancelSignal: AbortSignal | undefined,
    meter: ProgressMeter,
  ): Promise<RepairOutcome> {
    const outcome: RepairOutcome = { attempted: new Set(), accepted: new Set() };
    // Indexed by batch, so the list is already deduplicated and in block order.
    const targets = flagged.filter((entry) => entry.flags.length > 0);
    if (!targets.length) return outcome;
    const causes = groupByCause(targets);
    // The wider ceiling is for a failure that repeats; a scatter of one-offs
    // still gets the ordinary cap.
    const systematic = [...causes.values()].some(
      (group) => group.length >= SYSTEMATIC_CAUSE_MIN);
    const cap = systematic
      ? systematicRepairCap(batches.length)
      : fixFlaggedCap(batches.length);
    const chosen = chooseByCause(causes, cap);
    meter.plan('repairing', chosen.length);
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
        outcome.attempted.add(index);
        const retried = await retryFlaggedBatch(
          this.chat, batches[index]!, sourceLang, targetLang, provider,
          fileContext, tailBefore(batches, index, contextOverlap),
          results[index]!, flags, run, cancelSignal,
        );
        if (retried !== results[index]) {
          results[index] = retried;
          outcome.accepted.add(index);
        }
      }
    };
    const workerCount = Math.min(Math.max(1, workers), chosen.length);
    await Promise.all(Array.from({ length: workerCount }, worker));
    return outcome;
  }

  /** The narrower second pass: a cue still flagged after the batch retries is
   * re-issued on its own, its problems named. A correction the model let slide
   * inside a ten-cue batch is usually followed when the cue is all there is to
   * do. Same acceptance rule as the batch retry, so it cannot make the file
   * worse, and capped at the same share of the file's cues. A one-cue batch
   * the batch pass already retried is not offered the same retry twice. */
  private async repairCues(
    batches: SubtitleBlock[][],
    results: BatchResult[],
    flagged: FlaggedBatch[],
    repaired: RepairOutcome,
    sourceLang: string,
    targetLang: string,
    provider: ProviderConfig,
    fileContext: FileContext,
    contextOverlap: number,
    workers: number,
    run: RunOptions,
    cancelSignal: AbortSignal | undefined,
    meter: ProgressMeter,
  ): Promise<void> {
    const where = new Map<number, { index: number; position: number }>();
    batches.forEach((batch, index) => {
      batch.forEach((block, position) => where.set(block.number, { index, position }));
    });
    const leftover = new Map<number, BatchFlag[]>();
    for (const flag of survivingFlags(results, flagged, repaired.accepted)) {
      const spot = where.get(flag.block);
      if (!spot) continue;
      if (batches[spot.index]!.length === 1 && repaired.attempted.has(spot.index)) continue;
      const list = leftover.get(flag.block);
      if (list) list.push(flag); else leftover.set(flag.block, [flag]);
    }
    if (!leftover.size) return;
    const source = batches.flat();
    const numbers = [...leftover.keys()].sort((a, b) => a - b);
    const cap = fixFlaggedCap(source.length);
    const chosen = numbers.slice(0, cap);
    meter.plan('repairing', repaired.attempted.size + chosen.length);
    if (chosen.length < numbers.length) {
      run.notify(
        `${numbers.length} flagged line(s) left after the batch retries; `
        + `re-translating ${chosen.length} on their own (cap ${cap}), `
        + `leaving ${numbers.length - chosen.length}`,
      );
    }
    const at = new Map(source.map((block, i) => [block.number, i]));
    let kept = 0;
    let nextIdx = 0;
    const worker = async () => {
      while (true) {
        throwIfCancelled(cancelSignal);
        const i = nextIdx++;
        if (i >= chosen.length) return;
        const number = chosen[i]!;
        const { index, position } = where.get(number)!;
        const flags = leftover.get(number)!;
        const start = at.get(number)!;
        const previous: BatchResult = { blocks: [results[index]!.blocks[position]!], flags };
        // Its neighbours before it, as the batch would have shown them.
        const prevTail = contextOverlap > 0
          ? source.slice(Math.max(0, start - contextOverlap), start)
          : [];
        const retried = await retryFlaggedBatch(
          this.chat, [source[start]!], sourceLang, targetLang, provider,
          fileContext, prevTail, previous, flags, run, cancelSignal,
        );
        if (retried === previous) continue;
        const result = results[index]!;
        const blocks = [...result.blocks];
        blocks[position] = retried.blocks[0]!;
        results[index] = {
          blocks,
          flags: [...result.flags.filter((f) => f.block !== number), ...retried.flags],
        };
        const entry = flagged[index]!;
        entry.flags = [
          ...entry.flags.slice(0, entry.detectorCount),
          ...entry.flags.slice(entry.detectorCount).filter((f) => f.block !== number),
        ];
        kept++;
      }
    };
    const workerCount = Math.min(Math.max(1, workers), chosen.length);
    await Promise.all(Array.from({ length: workerCount }, worker));
    run.notify(`Repaired ${kept}/${chosen.length} flagged line(s) on their own`);
  }
}

/** Which batches the repair pass tried, and which of those retries it kept. */
interface RepairOutcome {
  attempted: Set<number>;
  accepted: Set<number>;
}


/** What is still wrong with the file. A batch's detector flags are re-read
 * from whatever text it ends up shipping, so a retry that fixed a leak clears
 * it; the adequacy and phrase flags were never re-measured, so they stand
 * unless the batch they belong to was re-translated and the retry kept. */
function survivingFlags(
  results: BatchResult[], flagged: FlaggedBatch[], accepted: ReadonlySet<number>,
): BatchFlag[] {
  const out: BatchFlag[] = [];
  results.forEach((result, index) => {
    out.push(...result.flags);
    if (accepted.has(index)) return;
    const entry = flagged[index]!;
    out.push(...entry.flags.slice(entry.detectorCount));
  });
  return dedupeFlags(out);
}

/** One flag per cue and cause, in cue order. */
function dedupeFlags(flags: BatchFlag[]): BatchFlag[] {
  const seen = new Set<string>();
  const out: BatchFlag[] = [];
  for (const flag of flags) {
    const key = `${flag.block}\u0000${flag.cause}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(flag);
  }
  return out.sort((a, b) => a.block - b.block);
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
