import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
  signal,
} from '@angular/core';
import { describeDialect } from '../core/chat-client';
import { downloadBlob, downloadText } from '../core/download';
import { FileStatus, RepairedFlag } from '../core/file-types';
import { STAGE_LABELS } from '../core/run-progress';
import {
  addCallCounts,
  blocksPerSecond,
  describeCalls,
  emptyCallCounts,
  totalCalls,
} from '../core/run-stats';
import { TimeTracker } from '../core/time-tracker';

// Progress and outcome of a run: the per-file rows, the run controls, what the
// run said about its own work, and saving a translation (or all of them,
// zipped) to disk.
@Component({
  selector: 'app-run-results',
  templateUrl: './run-results.component.html',
  styleUrl: './run-results.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RunResultsComponent {
  fileStatuses = input.required<FileStatus[]>();
  doneFiles = input.required<FileStatus[]>();
  failedFiles = input.required<FileStatus[]>();
  inProgressFiles = input.required<FileStatus[]>();
  overallProgressPercent = input.required<number>();
  tracker = input.required<TimeTracker>();
  isTranslating = input.required<boolean>();
  isCancelling = input.required<boolean>();
  canRetryFailed = input.required<boolean>();
  isDone = input.required<boolean>();
  targetLangCode = input.required<string>();

  cancelled = output<void>();
  retried = output<void>();
  restarted = output<void>();
  /** Output names now written to disk, so the shell can drop its unsaved-work warning. */
  saved = output<string[]>();

  isZipping = signal(false);

  // What the finished files actually cost, summed across the run.
  private runCalls = computed(() => this.doneFiles().reduce(
    (sum, f) => (f.stats ? addCallCounts(sum, f.stats.calls) : sum),
    emptyCallCounts(),
  ));

  callTotal = computed(() => totalCalls(this.runCalls()));
  callBreakdown = computed(() => describeCalls(this.runCalls()));

  // What the provider had to be asked for differently; blank for an endpoint
  // that took the defaults, which is every endpoint that works.
  dialectLabel = computed(() => {
    for (const f of this.doneFiles()) {
      const dialect = f.stats?.dialect;
      const described = dialect ? describeDialect(dialect) : '';
      if (described) return described;
    }
    return '';
  });

  /** What the run is doing right now: the first file still in flight names
   * its step and how far through it is, so the bar is never unexplained. */
  stageLabel = computed(() => {
    const p = this.inProgressFiles()[0]?.progress;
    if (!p) return '';
    if (p.stage === 'prepass') return STAGE_LABELS.prepass;
    if (p.stage === 'batches') return `${STAGE_LABELS.batches} batch ${p.done} of ${p.total}`;
    return `${STAGE_LABELS[p.stage]}, ${p.done} of ${p.total}`;
  });

  /** What the repair fixed across the finished files, by kind, most first. */
  repairedCount = computed(() =>
    this.doneFiles().reduce((sum, f) => sum + (f.review?.repaired.length ?? 0), 0));

  repairedBreakdown = computed(() => {
    const counts = new Map<string, number>();
    for (const f of this.doneFiles()) {
      for (const flag of f.review?.repaired ?? []) {
        const family = causeFamily(flag.cause);
        counts.set(family, (counts.get(family) ?? 0) + 1);
      }
    }
    return [...counts].sort((a, b) => b[1] - a[1])
      .map(([family, n]) => `${family} ${n}`).join(', ');
  });

  /** The cues the run could not put right, with the file when there are several. */
  leftFlagged = computed<{ key: string; where: string; reason: string }[]>(() => {
    const done = this.doneFiles();
    const many = done.length > 1;
    const out: { key: string; where: string; reason: string }[] = [];
    for (const f of done) {
      for (const cue of f.review?.cues ?? []) {
        for (const message of cue.flags) {
          out.push({
            key: `${f.outputName}\u0000${cue.number}\u0000${message}`,
            where: many ? `${f.outputName}, line ${cue.number}` : `Line ${cue.number}`,
            reason: message.replace(/^Block \d+: /, ''),
          });
        }
      }
    }
    return out;
  });

  flaggedCueCount = computed(() =>
    this.doneFiles().reduce((sum, f) => sum + flaggedIn(f), 0));

  // What the run came to, once it stopped: every file, some files, or none.
  outcome = computed<'complete' | 'partial' | 'failed'>(() => {
    const failed = this.failedFiles().length;
    if (failed === 0) return 'complete';
    return this.doneFiles().length === 0 ? 'failed' : 'partial';
  });

  outcomeTitle = computed(() => {
    switch (this.outcome()) {
      case 'failed': return 'Translation failed';
      case 'partial': return 'Translation finished with failures';
      default: return 'Translation complete';
    }
  });

  throughputLabel = computed(() => {
    const blocks = this.doneFiles().reduce((sum, f) => sum + (f.stats?.blocks ?? 0), 0);
    const rate = blocksPerSecond(blocks, this.tracker().elapsedMs());
    return rate >= 10 ? rate.toFixed(0) : rate.toFixed(1);
  });

  cancelTranslation() {
    this.cancelled.emit();
  }

  retryFailed() {
    this.retried.emit();
  }

  reset() {
    this.restarted.emit();
  }

  flaggedIn(f: FileStatus): number {
    return flaggedIn(f);
  }

  downloadFile(f: FileStatus) {
    if (!f.content) return;
    downloadText(f.content, f.outputName);
    this.saved.emit([f.outputName]);
  }

  /** The one button: a single finished file downloads as itself, several as a zip. */
  download() {
    const done = this.doneFiles();
    if (done.length === 1) this.downloadFile(done[0]!);
    else void this.downloadAll();
  }

  downloadLabel(): string {
    if (this.isZipping()) return 'Zipping...';
    return this.doneFiles().length === 1 ? 'Download file' : 'Download all';
  }

  async downloadAll() {
    const done = this.doneFiles();
    if (done.length === 0 || this.isZipping()) return;

    this.isZipping.set(true);
    try {
      // Loaded on demand: nobody pays for the zip library until they ask for a zip.
      const { default: JSZip } = await import('jszip');
      const zip = new JSZip();
      for (const f of done) {
        if (f.content) zip.file(f.outputName, f.content);
      }
      const blob = await zip.generateAsync({
        type: 'blob',
        compression: 'DEFLATE',
        compressionOptions: { level: 6 },
      });
      downloadBlob(blob, this.makeZipName());
      this.saved.emit(done.map((f) => f.outputName));
    } finally {
      this.isZipping.set(false);
    }
  }

  private makeZipName(): string {
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`;
    return `translora-${this.targetLangCode()}-${stamp}.zip`;
  }

}

/** The kind of problem a flag's cause names, in the words the card uses. */
function causeFamily(cause: RepairedFlag['cause']): string {
  if (cause === 'tags') return 'formatting tags';
  if (cause === 'dashes') return 'speaker dashes';
  if (cause === 'shift') return 'text bleeding between lines';
  if (cause === 'adequacy') return 'lost meaning';
  if (cause.startsWith('leak:')) return 'foreign script';
  if (cause.startsWith('term:')) return 'glossary terms';
  if (cause.startsWith('name:')) return 'character names';
  if (cause.startsWith('phrase:')) return 'inconsistent phrases';
  return cause;
}

function flaggedIn(f: FileStatus): number {
  return f.review?.cues.filter((cue) => cue.flags.length > 0).length ?? 0;
}
