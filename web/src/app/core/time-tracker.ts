import { Signal, computed, signal } from '@angular/core';

interface TimedItem {
  timeMs?: number;
}

interface ProgressItem {
  currentBatch?: number;
  totalBatches?: number;
}

// Elapsed / avg / ETA for a translation run. Owns its own 500ms ticker and
// exposes pre-formatted strings for the template.
export class TimeTracker {
  private startMs = signal(0);
  private nowMs = signal(0);
  private totalMs = signal(0);
  private running = signal(false);
  private timerId: ReturnType<typeof setInterval> | null = null;

  constructor(
    private doneFiles: Signal<TimedItem[]>,
    private inProgressFiles: Signal<ProgressItem[]>,
    private pendingCount: Signal<number>,
  ) {}

  elapsedMs = computed(() => {
    this.nowMs(); // subscribe to ticks
    return this.running()
      ? performance.now() - this.startMs()
      : this.totalMs();
  });

  avgFileMs = computed(() => {
    const done = this.doneFiles();
    if (done.length === 0) return 0;
    return done.reduce((s, f) => s + (f.timeMs ?? 0), 0) / done.length;
  });

  // Fractional file-equivalents: in-progress files contribute
  // currentBatch/totalBatches, so ETA shows up as soon as any file reports a
  // first batch (including single-file runs).
  etaMs = computed<number | null>(() => {
    const done = this.doneFiles();
    const inProgress = this.inProgressFiles();
    const pending = this.pendingCount();
    const totalFiles = done.length + inProgress.length + pending;
    if (totalFiles === 0) return null;

    let fractionalDone = done.length;
    for (const f of inProgress) {
      if (f.totalBatches && f.totalBatches > 0) {
        fractionalDone += (f.currentBatch ?? 0) / f.totalBatches;
      }
    }
    if (fractionalDone <= 0) return null;
    if (fractionalDone >= totalFiles) return 0;

    const elapsed = this.elapsedMs();
    if (elapsed <= 0) return null;
    return (elapsed * (totalFiles - fractionalDone)) / fractionalDone;
  });

  elapsedFormatted = computed(() => this.formatMs(this.elapsedMs()));

  totalFormatted = computed(() => {
    const ms = this.totalMs();
    return ms > 0 ? this.formatMs(ms) : '';
  });

  avgFormatted = computed(() => {
    const ms = this.avgFileMs();
    return ms > 0 ? this.formatMs(ms) : '';
  });

  etaFormatted = computed(() => {
    const ms = this.etaMs();
    return ms === null || ms <= 0 ? '' : this.formatMs(ms);
  });

  begin(): void {
    const now = performance.now();
    this.totalMs.set(0);
    this.startMs.set(now);
    this.nowMs.set(now);
    this.running.set(true);
    this.startTicker();
  }

  // Continues after idle (e.g. retry-failed) so elapsed time picks up where
  // finish() left off instead of resetting.
  resume(): void {
    const now = performance.now();
    const prev = this.totalMs();
    this.startMs.set(now - prev);
    this.nowMs.set(now);
    this.running.set(true);
    this.startTicker();
  }

  finish(): void {
    this.totalMs.set(performance.now() - this.startMs());
    this.running.set(false);
    this.stopTicker();
  }

  reset(): void {
    this.totalMs.set(0);
  }

  destroy(): void {
    this.stopTicker();
  }

  formatMs(ms: number): string {
    const totalSec = Math.round(ms / 1000);
    if (totalSec < 60) return `${totalSec}s`;
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    const mm = m % 60;
    return `${h}h ${mm}m`;
  }

  private startTicker(): void {
    this.stopTicker();
    this.timerId = setInterval(() => {
      this.nowMs.set(performance.now());
    }, 500);
  }

  private stopTicker(): void {
    if (this.timerId !== null) {
      clearInterval(this.timerId);
      this.timerId = null;
    }
  }
}
