// Translating one batch: the two retry budgets, the recursive split, the
// review pass, and the deterministic repair of a batch once it validates.

import { HttpErrorResponse } from '@angular/common/http';
import { ATTEMPTS_BEFORE_SPLIT, Formality } from './constants';
import {
  ChatClient,
  ChatOptions,
  ProviderConfig,
  ReasoningBudgetError,
  TranslationCancelledError,
  describeError,
  extractServerMessage,
  isRetryableStatus,
  retryAfterHeader,
  retryDelayMs,
  stripMarkdownFences,
  throwIfCancelled,
} from './chat-client';
import { FileContext, driftCause, driftPhrase } from './context-pass';
import { SubtitleNorms, grammarNoteFor } from './languages';
import {
  ScriptLeak,
  dialogueDashLines,
  findCrossCueShifts,
  enforceLineLength,
  findTags,
  normalizeRtlPunctuation,
  reflowToLineCount,
  repairTags,
  restoreDialogueDashes,
  restoreTerminalPunctuation,
  scriptLeaks,
  shiftMessage,
} from './repair';
import { CallKind } from './run-stats';
import {
  SubtitleBlock,
  parseLite,
  serializeLite,
  validateBatch,
} from './srt-parser';
import {
  REVIEW_SYSTEM_PROMPT,
  SYSTEM_PROMPT,
  buildFixFlaggedUserMessage,
  buildReviewUserMessage,
  buildUserMessage,
} from './translation-prompt';

/** What one batch needs from the run's settings. */
export interface BatchOptions extends ChatOptions {
  /** Tallies one LLM call so the run can report what it cost. */
  count: (kind: CallKind) => void;
  /** Re-wrap a translated cue to the source's line count without a model call. */
  reflow: boolean;
  /** Where the review pass goes; the main provider unless one was overridden. */
  reviewProvider: ProviderConfig;
  norms: SubtitleNorms;
  formality: Formality;
  dialect: string;
}

/** One problem a finished batch was flagged for: the warning a person reads,
 * the line the model is shown when the batch is re-issued, and the cause the
 * repair pass groups by. */
export interface BatchFlag {
  block: number;
  message: string;
  problem: string;
  /** WHAT went wrong, never where: one term drifting across sixteen batches
   * has to be recognisable as one problem, not sixteen. */
  cause: string;
}

/** An accepted batch and everything that looked wrong with it. */
export interface BatchResult {
  blocks: SubtitleBlock[];
  flags: BatchFlag[];
}

async function reviewBatch(
  chat: ChatClient,
  batch: SubtitleBlock[],
  firstPass: SubtitleBlock[],
  fileContext: FileContext,
  provider: ProviderConfig,
  run: BatchOptions,
  cancelSignal?: AbortSignal,
): Promise<SubtitleBlock[]> {
  if (!fileContext.hasCorrections(batch)) return firstPass;
  const glossary = fileContext.renderForBatch(batch);
  if (!glossary.trim()) return firstPass;
  try {
    run.count('review');
    const raw = await chat.callChat(
      REVIEW_SYSTEM_PROMPT,
      buildReviewUserMessage(
        batch, firstPass, glossary, grammarNoteFor(run.norms.script),
      ),
      provider, maxTokensFor(serializeLite(batch), batch.length), run, cancelSignal,
    );
    const parsed = parseLite(stripMarkdownFences(raw));
    if (parsed.length !== batch.length) return firstPass;
    // Keep the reviewer's own numbers so validation catches a renumbered reply.
    const revised = parsed.map((b, i) => ({
      number: b.number,
      timestamp: batch[i].timestamp,
      text: b.text,
    }));
    return validateBatch(batch, revised).ok ? revised : firstPass;
  } catch (err) {
    if (err instanceof TranslationCancelledError) throw err;
    console.warn('Review failed, keeping first-pass:', describeError(err));
    run.notify('Review pass failed - keeping the first-pass translation');
    return firstPass;
  }
}

export async function translateBatch(
  chat: ChatClient,
  inputBlocks: SubtitleBlock[],
  sourceLang: string,
  targetLang: string,
  provider: ProviderConfig,
  maxRetries: number,
  fileContext: FileContext,
  prevTail: SubtitleBlock[],
  contextOverlap: number,
  review: boolean,
  run: BatchOptions,
  cancelSignal?: AbortSignal,
  isSplitChild = false,
): Promise<BatchResult> {
  throwIfCancelled(cancelSignal);

  const canSplit = inputBlocks.length > 1;
  // Separate budgets: splitting fixes a model that merges blocks, not a 429.
  const validationBudget = canSplit ? ATTEMPTS_BEFORE_SPLIT : Math.max(1, maxRetries);
  const transportBudget = Math.max(1, maxRetries);
  // A split child is reviewed once by its root, after reassembly.
  const reviewHere = review && !isSplitChild;
  const firstBlockNum = inputBlocks[0].number;

  const batchWire = serializeLite(inputBlocks);
  const userMessage = batchUserMessage(
    inputBlocks, sourceLang, targetLang, fileContext, prevTail, run,
  );
  let validationFailures = 0;
  let transportFailures = 0;
  let lastError = '';

  while (validationFailures < validationBudget && transportFailures < transportBudget) {
    throwIfCancelled(cancelSignal);
    try {
      run.count('translate');
      const raw = await chat.callChat(
        SYSTEM_PROMPT, userMessage, provider,
        maxTokensFor(batchWire, inputBlocks.length), run, cancelSignal,
      );
      // Keep the model's own numbers: rewriting them makes validation unfalsifiable.
      // Wire format strips timestamps; reattach positionally.
      let output = parseLite(stripMarkdownFences(raw)).map((b, i) => ({
        number: b.number,
        timestamp: inputBlocks[i]?.timestamp ?? '',
        text: b.text,
      }));
      const check = validateBatch(inputBlocks, output);
      if (check.ok) {
        if (reviewHere) {
          output = await reviewBatch(
            chat, inputBlocks, output, fileContext, run.reviewProvider, run,
            cancelSignal,
          );
        }
        return finalizeBatch(inputBlocks, output, fileContext, run, !isSplitChild);
      }

      validationFailures++;
      lastError = `validation: ${check.error}`;
      console.warn(
        `Batch validation failed (${validationFailures}/${validationBudget}):`,
        check.error,
      );
      if (validationFailures < validationBudget) {
        await sleep(Math.min(validationFailures, 3) * 1000, cancelSignal);
      }

    } catch (err: unknown) {
      if (err instanceof TranslationCancelledError) {
        throw err;
      }
      // The budget was already doubled to its ceiling inside the transport;
      // asking again only buys the same empty reply.
      if (err instanceof ReasoningBudgetError) {
        throw new Error(`${err.message} (block ${firstBlockNum})`);
      }

      // null = never reached HTTP (timeout, unusable body); retryable.
      const status = err instanceof HttpErrorResponse ? err.status : null;
      transportFailures++;
      lastError = extractServerMessage(err) || (err as Error)?.message || String(err);

      console.warn(
        `Batch request failed (${transportFailures}/${transportBudget})`
          + (status === null ? ':' : ` [HTTP ${status}]:`),
        lastError,
      );

      if (status !== null && !isRetryableStatus(status)) {
        throw new Error(`HTTP ${status}: ${lastError} (block ${firstBlockNum})`);
      }

      // Status 0 is CORS, DNS or offline, not a busy server: one retry, not the budget.
      if (status === 0 && transportFailures >= 2) {
        throw new Error(`${lastError} (block ${firstBlockNum})`);
      }

      if (transportFailures < transportBudget) {
        const delay = retryDelayMs(transportFailures, retryAfterHeader(err));
        if (status === 429) {
          console.warn(`Rate limited — waiting ${Math.round(delay / 1000)}s...`);
        }
        await sleep(delay, cancelSignal);
      }
    }
  }

  // Recursive split: halve on persistent validation failure. Terminates at
  // N=1 where count mismatch is impossible.
  if (validationFailures >= validationBudget && canSplit) {
    const mid = Math.floor(inputBlocks.length / 2);
    const left = inputBlocks.slice(0, mid);
    const right = inputBlocks.slice(mid);
    console.warn(
      `Batch splitting ${inputBlocks.length} -> ${left.length} + ${right.length} blocks`,
    );
    // Sequential: parallel halves would oversubscribe the worker pool slot.
    const leftResult = await translateBatch(
      chat, left, sourceLang, targetLang, provider, maxRetries, fileContext,
      prevTail, contextOverlap, review, run, cancelSignal, true,
    );
    const rightPrev =
      contextOverlap > 0 ? left.slice(-contextOverlap) : [];
    const rightResult = await translateBatch(
      chat, right, sourceLang, targetLang, provider, maxRetries, fileContext,
      rightPrev, contextOverlap, review, run, cancelSignal, true,
    );
    let combined = [...leftResult.blocks, ...rightResult.blocks];
    if (reviewHere) {
      combined = await reviewBatch(
        chat, inputBlocks, combined, fileContext, run.reviewProvider, run,
        cancelSignal,
      );
    }
    return finalizeBatch(inputBlocks, combined, fileContext, run, !isSplitChild);
  }

  const attempts = validationFailures + transportFailures;
  throw new Error(
    `Batch failed after ${attempts} attempt(s) (block ${firstBlockNum})`
      + (lastError ? `: ${lastError}` : ''),
  );
}


/** Deterministic repair of an accepted batch: restore dropped formatting tags,
 * re-wrap to the source's line count, put back the sentence-final mark, then
 * flag glossary drift and suspected bleeding. None of this can fail a file —
 * a tag we cannot restore is cosmetic. `report` is false for a split half, so
 * the root warns once for the batch. */
function finalizeBatch(
  batch: SubtitleBlock[],
  output: SubtitleBlock[],
  fileContext: FileContext,
  run: BatchOptions,
  report: boolean,
): BatchResult {
  const { maxCharsPerLine: maxChars, script } = run.norms;
  const flags: BatchFlag[] = [];
  const repaired = output.map((out, i) => {
    const src = batch[i]!;
    const fixed = repairTags(src.text, out.text);
    if (!fixed.ok) flags.push(tagFlag(src, out));
    // Before the reflow: it leaves a cue with speaker dashes alone, so the
    // dashes have to be back before it looks.
    const dashed = restoreDialogueDashes(src.text, fixed.text);
    if (!dashed.ok) flags.push(dashFlag(src, fixed.text));
    let text = dashed.text;
    if (run.reflow) {
      text = reflowToLineCount(text, src.text.split('\n').length, maxChars, script);
      text = enforceLineLength(text, maxChars, script);
    }
    text = normalizeRtlPunctuation(text, script);
    text = restoreTerminalPunctuation(src.text, text, script);
    const leak = scriptLeaks(src.text, text, script)[0];
    if (leak) flags.push(leakFlag(src, leak));
    return { number: out.number, timestamp: out.timestamp, text };
  });

  for (const drift of fileContext.driftEntries(batch, repaired)) {
    flags.push({
      block: drift.block,
      message: `Block ${drift.block}: ${driftPhrase(drift)}`,
      problem: `block ${drift.block}: the ${driftPhrase(drift)}`,
      cause: driftCause(drift),
    });
  }
  for (const shift of findCrossCueShifts(batch, repaired)) {
    flags.push({
      block: shift.first,
      message: shiftMessage(shift),
      problem: `block ${shift.first}: text from the next cue appears here `
        + `('${shift.run}')`,
      cause: 'shift',
    });
  }

  if (report) for (const flag of flags) run.notify(flag.message);
  return { blocks: repaired, flags };
}

function tagFlag(src: SubtitleBlock, out: SubtitleBlock): BatchFlag {
  const sourceTags = findTags(src.text);
  const outputTags = findTags(out.text);
  const named = sourceTags.join('...');
  return {
    block: src.number,
    message: `Block ${src.number}: formatting tags changed `
      + `(${sourceTags.join(',')} -> ${outputTags.join(',')})`,
    problem: outputTags.length
      ? `block ${src.number}: the formatting tags ${named} were changed to `
        + outputTags.join('...')
      : `block ${src.number}: the formatting tags ${named} were dropped`,
    cause: 'tags',
  };
}

function dashFlag(src: SubtitleBlock, outputText: string): BatchFlag {
  const source = dialogueDashLines(src.text);
  const output = dialogueDashLines(outputText);
  return {
    block: src.number,
    message: `Block ${src.number}: speaker dashes changed (${source} -> ${output})`,
    problem: `block ${src.number}: the source cue opens ${source} line(s) with a `
      + `dialogue dash and the translation opens ${output}; keep one line per speaker`,
    cause: 'dashes',
  };
}

function leakFlag(src: SubtitleBlock, leak: ScriptLeak): BatchFlag {
  return {
    block: src.number,
    message: `Block ${src.number}: ${leak.message}`,
    problem: `block ${src.number}: ${leak.message}`,
    cause: `leak:${leak.script}`,
  };
}

/** The user message a batch is translated with — rebuilt identically for the
 * flagged retry so the model sees the first attempt's request plus the fixes. */
function batchUserMessage(
  inputBlocks: SubtitleBlock[],
  sourceLang: string,
  targetLang: string,
  fileContext: FileContext,
  prevTail: SubtitleBlock[],
  run: BatchOptions,
): string {
  return buildUserMessage(
    sourceLang, targetLang, serializeLite(inputBlocks),
    fileContext.renderForBatch(inputBlocks), prevTail,
    run.norms.maxCharsPerLine, run.norms.maxLines, run.formality, run.dialect,
  );
}

/** One focused retry of a batch the run flagged. The retry is kept only if it
 * validates AND leaves strictly fewer flags behind, so it can never make the
 * file worse; anything else keeps the original. Costs one call. */
export async function retryFlaggedBatch(
  chat: ChatClient,
  inputBlocks: SubtitleBlock[],
  sourceLang: string,
  targetLang: string,
  provider: ProviderConfig,
  fileContext: FileContext,
  prevTail: SubtitleBlock[],
  previous: BatchResult,
  flags: BatchFlag[],
  run: BatchOptions,
  cancelSignal?: AbortSignal,
): Promise<BatchResult> {
  if (!flags.length || !inputBlocks.length) return previous;
  const wire = serializeLite(inputBlocks);
  const userMessage = buildFixFlaggedUserMessage(
    batchUserMessage(inputBlocks, sourceLang, targetLang, fileContext, prevTail, run),
    flags.map((flag) => flag.problem),
  );
  try {
    run.count('repair');
    const raw = await chat.callChat(
      SYSTEM_PROMPT, userMessage, provider,
      maxTokensFor(wire, inputBlocks.length), run, cancelSignal,
    );
    const parsed = parseLite(stripMarkdownFences(raw)).map((b, i) => ({
      number: b.number,
      timestamp: inputBlocks[i]?.timestamp ?? '',
      text: b.text,
    }));
    if (!validateBatch(inputBlocks, parsed).ok) return previous;
    const retried = finalizeBatch(inputBlocks, parsed, fileContext, run, false);
    if (retried.flags.length >= flags.length) return previous;
    run.notify(
      `Block ${inputBlocks[0]!.number}: re-translated after ${flags.length} `
      + `flag(s); ${retried.flags.length} left`,
    );
    return retried;
  } catch (err) {
    if (err instanceof TranslationCancelledError) throw err;
    console.warn('Flagged-batch retry failed, keeping the original:', describeError(err));
    run.notify('Flagged-batch repair failed - keeping the first translation');
    return previous;
  }
}

// Payload-derived: a flat per-block budget truncates batches with long cues.
function maxTokensFor(wire: string, blockCount: number): number {
  return Math.max(300, wire.length + 60 * blockCount);
}


// Cancellable: a pending backoff must not outlive the run that scheduled it.
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  throwIfCancelled(signal);

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, ms);

    const onAbort = () => {
      cleanup();
      reject(new TranslationCancelledError());
    };

    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
    };

    signal?.addEventListener('abort', onAbort, { once: true });
  });
}
