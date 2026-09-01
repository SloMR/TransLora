import { Injectable, Signal, computed, inject, signal } from '@angular/core';
import { errMessage } from '../error-message';
import { FileStatus, UploadedFile } from './file-types';
import { RunStats } from './run-stats';
import { TimeTracker } from './time-tracker';
import {
  ProviderConfig,
  QualityOptions,
  TranslationCancelledError,
  TranslationService,
} from './translation.service';

/** Everything a queued file needs, re-read per file so a mid-run edit applies. */
export interface RunRequest {
  provider: ProviderConfig;
  sourceLang: string;
  targetLang: string;
  batchSize: number;
  concurrency: number;
  maxRetries: number;
  parallelFiles: number;
  quality: QualityOptions;
}

/** What the queue reads back from its owner while a run is in flight. */
export interface RunSource {
  files: Signal<UploadedFile[]>;
  outputName: (name: string) => string;
  request: () => RunRequest;
}

// The multi-file run queue: a worker pool bounded by parallelFiles, one
// AbortController per run, cancellation, retry-of-failed, and the per-file
// status every progress view reads.
@Injectable({ providedIn: 'root' })
export class TranslationRunnerService {
  isTranslating = signal(false);
  isCancelling = signal(false);
  fileStatuses = signal<FileStatus[]>([]);

  private readonly translationService = inject(TranslationService);

  private source: RunSource | null = null;

  private workQueue: number[] = [];
  private activeWorkers = 0;
  private runController: AbortController | null = null;
  private cancelRequested = false;
  // Re-read per file so a retry mid-run uses the current config, not the spawn-time copy.
  private activeProvider: ProviderConfig = { apiUrl: '', apiKey: '', model: '' };

  doneFiles = computed(() =>
    this.fileStatuses().filter((f) => f.status === 'done')
  );
  failedFiles = computed(() =>
    this.fileStatuses().filter((f) => f.status === 'failed')
  );
  inProgressFiles = computed(() =>
    this.fileStatuses().filter((f) => f.status === 'translating')
  );
  pendingCount = computed(
    () =>
      this.fileStatuses().filter((f) => f.status === 'pending').length
  );

  overallProgressPercent = computed(() => {
    const all = this.fileStatuses();
    if (all.length === 0) return 0;
    let sum = 0;
    for (const f of all) {
      if (f.status === 'done' || f.status === 'failed') {
        sum += 1;
      } else if (f.status === 'translating' && f.totalBatches) {
        sum += (f.currentBatch ?? 0) / f.totalBatches;
      }
    }
    return Math.round((sum / all.length) * 100);
  });

  tracker = new TimeTracker(
    this.doneFiles,
    this.inProgressFiles,
    this.pendingCount,
  );

  canRetryFailed = computed(() => this.failedFiles().length > 0 && !this.isCancelling());

  isDone = computed(
    () =>
      !this.isTranslating() &&
      (this.doneFiles().length > 0 || this.failedFiles().length > 0)
  );

  hasResults = computed(
    () =>
      this.isTranslating() ||
      this.doneFiles().length > 0 ||
      this.failedFiles().length > 0
  );

  /** Named once by the owner; the queue then reads files and settings live. */
  connect(source: RunSource) {
    this.source = source;
  }

  start() {
    const source = this.source;
    if (!source) return;

    this.isCancelling.set(false);

    // Initialize statuses (fresh run)
    this.fileStatuses.set(
      source.files().map((f) => ({
        name: f.name,
        outputName: source.outputName(f.name),
        blockCount: f.blockCount,
        status: 'pending' as const,
      }))
    );
    this.enqueue(source.files().map((_, i) => i), false);
  }

  retryFailed() {
    if (!this.canRetryFailed()) return;

    this.isCancelling.set(false);

    const retryIndices: number[] = [];
    this.fileStatuses.update((statuses) =>
      statuses.map((s, i) => {
        if (s.status !== 'failed') return s;
        retryIndices.push(i);
        return {
          ...s,
          status: 'pending' as const,
          currentBatch: undefined,
          totalBatches: undefined,
          error: undefined,
          timeMs: undefined,
        };
      })
    );

    if (retryIndices.length === 0) return;

    this.enqueue(retryIndices, true);
  }

  // Pushes indices onto the shared queue and ensures enough workers are running.
  // Safe to call mid-run: idle workers pick up new items, or fresh ones spawn
  // up to the request's parallelFiles.
  private enqueue(indices: number[], isRetry: boolean) {
    if (indices.length === 0) return;

    const wasIdle = this.activeWorkers === 0;
    this.workQueue.push(...indices);

    if (wasIdle) {
      this.cancelRequested = false;
      this.runController = new AbortController();
      if (isRetry) {
        this.tracker.resume();
      } else {
        this.tracker.begin();
      }
      this.isTranslating.set(true);
      this.isCancelling.set(false);
    }

    if (!this.runController) return;

    const request = this.source!.request();
    this.activeProvider = request.provider;

    const desired = Math.min(request.parallelFiles, this.workQueue.length + this.activeWorkers);
    while (this.activeWorkers < desired) {
      this.spawnWorker(this.runController.signal);
    }
  }

  cancel() {
    if (!this.isTranslating() || this.isCancelling()) return;

    this.cancelRequested = true;
    this.isCancelling.set(true);
    this.workQueue = [];
    this.runController?.abort();
  }

  /** Drops one row's status; the queue restarts from scratch after a removal. */
  removeAt(index: number) {
    // Drop only this row: fileStatuses holds the only copy of the other translations.
    this.fileStatuses.update((s) => s.filter((_, i) => i !== index));
    this.workQueue = [];
  }

  clear() {
    this.workQueue = [];
    this.fileStatuses.set([]);
    this.tracker.reset();
  }

  destroy() {
    this.cancelRequested = true;
    this.workQueue = [];
    this.runController?.abort();
    this.tracker.destroy();
  }

  private spawnWorker(cancelSignal: AbortSignal) {
    this.activeWorkers++;
    void (async () => {
      try {
        while (this.workQueue.length > 0 && !cancelSignal.aborted) {
          const idx = this.workQueue.shift()!;
          await this.translateOne(idx, this.activeProvider, cancelSignal);
        }
      } finally {
        this.activeWorkers--;
        if (this.activeWorkers === 0 && this.workQueue.length === 0) {
          const cancelled = this.cancelRequested || cancelSignal.aborted;
          this.runController = null;
          this.tracker.finish();

          if (cancelled) {
            this.cancelRequested = false;
            // Mark still-pending/translating entries as failed so they remain
            // retryable from the UI.
            this.fileStatuses.update((arr) =>
              arr.map((s) =>
                s.status === 'pending' || s.status === 'translating'
                  ? {
                      ...s,
                      status: 'failed' as const,
                      error: 'Cancelled',
                      currentBatch: undefined,
                      totalBatches: undefined,
                    }
                  : s,
              ),
            );
          }

          this.isCancelling.set(false);
          this.isTranslating.set(false);
        }
      }
    })();
  }

  private async translateOne(idx: number, provider: ProviderConfig, cancelSignal: AbortSignal) {
    // Queued by index, so a row removed mid-run can leave an index with no file.
    const f = this.source!.files()[idx];
    if (!f) return;
    const fileStart = performance.now();

    if (cancelSignal.aborted || this.cancelRequested) return;
    this.updateFileStatus(idx, { status: 'translating' });

    const request = this.source!.request();
    let stats: RunStats | undefined;

    try {
      const content = await this.translationService.translateDocument(
        f.doc,
        request.sourceLang,
        request.targetLang,
        provider,
        request.batchSize,
        request.concurrency,
        request.maxRetries,
        (progress) => {
          if (cancelSignal.aborted || this.cancelRequested) return;
          this.updateFileStatus(idx, {
            currentBatch: progress.currentBatch,
            totalBatches: progress.totalBatches,
          });
        },
        cancelSignal,
        // The run reports its own call breakdown; the queue only carries it.
        { ...request.quality, onStats: (s) => { stats = s; } },
      );

      if (cancelSignal.aborted || this.cancelRequested) return;
      this.updateFileStatus(idx, {
        status: 'done',
        content,
        stats,
        timeMs: performance.now() - fileStart,
      });
    } catch (err) {
      if (
        err instanceof TranslationCancelledError ||
        cancelSignal.aborted ||
        this.cancelRequested
      ) {
        return;
      }

      this.updateFileStatus(idx, {
        status: 'failed',
        error: errMessage(err, 'Translation failed'),
        timeMs: performance.now() - fileStart,
      });
    }
  }

  private updateFileStatus(idx: number, update: Partial<FileStatus>) {
    this.fileStatuses.update((arr) =>
      arr.map((s, i) => (i === idx ? { ...s, ...update } : s)),
    );
  }
}
