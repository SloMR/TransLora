import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
  signal,
} from '@angular/core';
import JSZip from 'jszip';
import { describeDialect } from '../core/chat-client';
import { FileStatus } from '../core/file-types';
import {
  addCallCounts,
  blocksPerSecond,
  describeCalls,
  emptyCallCounts,
  totalCalls,
} from '../core/run-stats';
import { TimeTracker } from '../core/time-tracker';

// Progress and outcome of a run: the per-file rows, the run controls, and
// saving a translation (or all of them, zipped) to disk.
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

  downloadFile(f: FileStatus) {
    if (!f.content) return;
    this.downloadBlob(f.content, f.outputName);
    this.saved.emit([f.outputName]);
  }

  async downloadAll() {
    const done = this.doneFiles();
    if (done.length === 0 || this.isZipping()) return;

    this.isZipping.set(true);
    try {
      const zip = new JSZip();
      for (const f of done) {
        if (f.content) zip.file(f.outputName, f.content);
      }
      const blob = await zip.generateAsync({
        type: 'blob',
        compression: 'DEFLATE',
        compressionOptions: { level: 6 },
      });
      this.downloadBlobRaw(blob, this.makeZipName());
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

  private downloadBlob(content: string, filename: string) {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    this.downloadBlobRaw(blob, filename);
  }

  private downloadBlobRaw(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    // Firefox and Safari fetch the blob on a later task; revoking now cancels the download.
    setTimeout(() => {
      a.remove();
      URL.revokeObjectURL(url);
    }, 10_000);
  }

  fileProgressPercent(f: FileStatus): number {
    if (!f.totalBatches) return 0;
    return Math.round(((f.currentBatch ?? 0) / f.totalBatches) * 100);
  }
}
