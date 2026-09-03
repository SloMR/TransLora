import { CallKind } from './run-stats';

export type RunStage = 'prepass' | 'batches' | 'checking' | 'repairing';

/** Where a file's run is: the stage it is in, that stage's own count, and one
 * percent for the whole run — so the bar moves through every step and parks
 * nowhere, not at 0% while the file is read nor at 100% while it is repaired. */
export interface RunProgress {
  stage: RunStage;
  /** The stage's own count: batches for the batch stage, calls for the rest. */
  done: number;
  total: number;
  percent: number;
}

/** What each stage is doing, in the words the page uses. */
export const STAGE_LABELS: Record<RunStage, string> = {
  prepass: 'reading the file first: names, terms and who speaks',
  batches: 'translating',
  checking: 'checking the meaning survived',
  repairing: 'repairing the flagged lines',
};

const STAGE_OF: Record<CallKind, RunStage> = {
  scan: 'prepass',
  attribution: 'prepass',
  translate: 'batches',
  review: 'batches',
  backTranslation: 'checking',
  repair: 'repairing',
};

// The scan is one call that sends the whole file and waits for a glossary
// back: it takes about as long as this many batch calls.
const SCAN_UNITS = 4;

/** The run's progress meter. Every model call is one unit of work; each
 * stage is planned at what the projection would price it, then revised as
 * the run learns the real number. A stage that turns out bigger grows the
 * total; the percent never goes backwards — it waits — and reads 100 only
 * when the file is finished. */
export class ProgressMeter {
  private stage: RunStage = 'prepass';
  private readonly planned: Record<RunStage, number> = zero();
  private readonly calls: Record<RunStage, number> = zero();
  private batchesDone = 0;
  private batchesTotal = 0;
  private scanPlanned = false;
  private scanDone = false;
  private shown = 0;
  private finished = false;

  constructor(private readonly report?: (progress: RunProgress) => void) {}

  /** How many calls a stage is expected to make; never less than it already has. */
  plan(stage: RunStage, calls: number): void {
    if (stage === 'prepass') this.scanPlanned = true;
    this.planned[stage] = Math.max(calls, this.calls[stage]);
    this.emit();
  }

  begin(stage: RunStage): void {
    this.stage = stage;
    this.emit();
  }

  /** One call of `kind` went out. Counted at the request, not the reply: with
   * a handful in flight the bar leads by that handful, never by more. */
  count(kind: CallKind): void {
    const stage = STAGE_OF[kind];
    if (kind === 'scan') this.scanDone = true;
    this.calls[stage]++;
    if (this.calls[stage] > this.planned[stage]) this.planned[stage] = this.calls[stage];
    this.emit();
  }

  /** The batch stage counts finished batches, not calls: that is the number
   * the row shows, and a batch's retries and reviews would inflate it. */
  batches(done: number, total: number): void {
    this.batchesDone = done;
    this.batchesTotal = total;
    this.emit();
  }

  finish(): void {
    this.finished = true;
    this.emit();
  }

  snapshot(): RunProgress {
    const done = this.stage === 'batches' ? this.batchesDone : this.calls[this.stage];
    const total = this.stage === 'batches' ? this.batchesTotal : this.planned[this.stage];
    return { stage: this.stage, done, total, percent: this.percent() };
  }

  private percent(): number {
    if (this.finished) return 100;
    const extra = SCAN_UNITS - 1;
    const doneUnits = sum(this.calls) + (this.scanDone ? extra : 0);
    const plannedUnits = sum(this.planned) + (this.scanPlanned ? extra : 0);
    const raw = plannedUnits > 0 ? Math.floor((100 * doneUnits) / plannedUnits) : 0;
    this.shown = Math.max(this.shown, Math.min(99, raw));
    return this.shown;
  }

  private emit(): void {
    this.report?.(this.snapshot());
  }
}

function zero(): Record<RunStage, number> {
  return { prepass: 0, batches: 0, checking: 0, repairing: 0 };
}

function sum(counts: Record<RunStage, number>): number {
  return counts.prepass + counts.batches + counts.checking + counts.repairing;
}
