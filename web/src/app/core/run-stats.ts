// What a run costs: the LLM calls it made, counted by the pass that made
// them, and the projection shown before a run starts so the price is visible
// while it can still be changed.

import { ProviderDialect } from './chat-client';
import {
  ADEQUACY_MIN_BATCHES,
  ADEQUACY_SAMPLE_FRACTION,
  ESTIMATED_SECS_PER_CALL,
  FIX_FLAGGED_FRACTION,
  FIX_FLAGGED_MIN,
  SYSTEMATIC_REPAIR_FRACTION,
} from './constants';

export const CALL_KINDS = [
  'scan', 'attribution', 'translate', 'review', 'repair', 'backTranslation',
] as const;

export type CallKind = (typeof CALL_KINDS)[number];
export type CallCounts = Record<CallKind, number>;

export const CALL_KIND_LABELS: Record<CallKind, string> = {
  scan: 'scan',
  attribution: 'attribution',
  translate: 'translate',
  review: 'review',
  repair: 'repair',
  backTranslation: 'back-translation',
};

export function emptyCallCounts(): CallCounts {
  return {
    scan: 0, attribution: 0, translate: 0, review: 0, repair: 0,
    backTranslation: 0,
  };
}

export function addCallCounts(a: CallCounts, b: CallCounts): CallCounts {
  const sum = emptyCallCounts();
  for (const kind of CALL_KINDS) sum[kind] = a[kind] + b[kind];
  return sum;
}

export function totalCalls(counts: CallCounts): number {
  return CALL_KINDS.reduce((sum, kind) => sum + counts[kind], 0);
}

/** "1 scan, 38 translate, 2 repair" — passes that made no call are left out. */
export function describeCalls(counts: CallCounts): string {
  return CALL_KINDS
    .filter((kind) => counts[kind] > 0)
    .map((kind) => `${counts[kind]} ${CALL_KIND_LABELS[kind]}`)
    .join(', ');
}

/** What one finished file cost. */
export interface RunStats {
  calls: CallCounts;
  blocks: number;
  elapsedMs: number;
  /** The request shape the provider turned out to need. */
  dialect?: ProviderDialect;
}

export function blocksPerSecond(blocks: number, elapsedMs: number): number {
  return elapsedMs > 0 ? (blocks * 1000) / elapsedMs : 0;
}

// Rounding up, never to nearest: the two trees must agree, and Python rounds
// halves to even where JavaScript rounds them up.
/** How many batches the flagged-batch repair may re-issue. */
export function fixFlaggedCap(batchCount: number): number {
  return Math.max(FIX_FLAGGED_MIN, Math.ceil(batchCount * FIX_FLAGGED_FRACTION));
}

/** The wider ceiling for a file where one cause keeps recurring: the ordinary
 * cap answers a handful of one-offs, and leaves a systematic failure almost
 * entirely unrepaired. */
export function systematicRepairCap(batchCount: number): number {
  return Math.max(
    FIX_FLAGGED_MIN, Math.ceil(batchCount * SYSTEMATIC_REPAIR_FRACTION));
}

/** How many batches the adequacy check back-translates. */
export function adequacySampleSize(batchCount: number): number {
  if (batchCount <= 0) return 0;
  return Math.min(
    batchCount,
    Math.max(ADEQUACY_MIN_BATCHES, Math.ceil(batchCount * ADEQUACY_SAMPLE_FRACTION)),
  );
}

/** Every Nth batch, so the sample spans the file rather than its opening. */
export function sampleIndices(batchCount: number, wanted: number): number[] {
  if (batchCount <= 0 || wanted <= 0) return [];
  const take = Math.min(batchCount, wanted);
  const step = batchCount / take;
  const picked: number[] = [];
  for (let i = 0; i < take; i++) picked.push(Math.floor(i * step));
  return picked;
}

export interface ProjectionInput {
  /** Blocks per queued file; each file is batched on its own. */
  blockCounts: number[];
  batchSize: number;
  /** Requests in flight at once: concurrency x parallel files. */
  lanes: number;
  review: boolean;
  fixFlagged: boolean;
  verifyAdequacy: boolean;
}

export interface RunProjection {
  calls: CallCounts;
  total: number;
  estimateMs: number;
  secsPerCall: number;
}

/** The call breakdown a run would make, and how long it would take. Speaker
 * attribution is absent on purpose: how many scenes qualify is only known once
 * the scan has replied, so counting it here would be a guess dressed as a
 * number. Review is an upper bound; repair is projected at the ordinary cap,
 * since the wider systematic ceiling depends on what the run turns out to
 * flag — quoting it for every file would overstate the ordinary one fivefold. */
export function projectRun(input: ProjectionInput): RunProjection {
  const calls = emptyCallCounts();
  const batchSize = Math.max(1, input.batchSize);
  for (const blocks of input.blockCounts) {
    if (blocks <= 0) continue;
    const batches = Math.ceil(blocks / batchSize);
    calls.scan += 1;
    calls.translate += batches;
    if (input.review) calls.review += batches;
    if (input.fixFlagged) calls.repair += fixFlaggedCap(batches);
    if (input.verifyAdequacy) calls.backTranslation += adequacySampleSize(batches);
  }
  const total = totalCalls(calls);
  const lanes = Math.max(1, input.lanes);
  return {
    calls,
    total,
    estimateMs: (total / lanes) * ESTIMATED_SECS_PER_CALL * 1000,
    secsPerCall: ESTIMATED_SECS_PER_CALL,
  };
}
