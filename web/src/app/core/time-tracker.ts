import { Signal, computed, signal } from '@angular/core';

interface TimedItem {
  timeMs?: number;
}

interface ProgressItem {
  currentBatch?: number;
  totalBatches?: number;
}

/**
 * Tracks elapsed time, per-file average, and ETA for a translation run.
 * Owns its own ticker (updates every 500 ms while running) and exposes
 * pre-formatted strings the template can render directly.
 */
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

  // --- Raw numbers -------------------------------------------------------

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

  /**
   * Remaining wall-clock time in ms, or null until we have any progress.
   *
   * Uses fractional file-equivalents: completed files count as 1, in-progress
   * files contribute `currentBatch / totalBatches`. Dividing elapsed time by
   * that fraction gives a rate we can extrapolate from — so ETA starts showing
   * as soon as any file reports its first batch, including single-file runs.
   */
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

  // --- Formatted for templates ------------------------------------------

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

  // --- Control ----------------------------------------------------------

  /** Start a fresh run: resets totals, captures the start time, starts ticking. */
  begin(): void {
    const now = performance.now();
    this.totalMs.set(0);
    this.startMs.set(now);
    this.nowMs.set(now);
    this.running.set(true);
    this.startTicker();
  }

  /**
   * Continue an existing run after idle (e.g. retry-failed). Elapsed time
   * picks up where the previous `finish()` left off instead of resetting to 0.
   */
  resume(): void {
    const now = performance.now();
    const prev = this.totalMs();
    this.startMs.set(now - prev);
    this.nowMs.set(now);
    this.running.set(true);
    this.startTicker();
  }

  /** Freeze the final elapsed time and stop ticking. */
  finish(): void {
    this.totalMs.set(performance.now() - this.startMs());
    this.running.set(false);
    this.stopTicker();
  }

  /** Clear the frozen total (used when the user resets the UI). */
  reset(): void {
    this.totalMs.set(0);
  }

  /** Stop the ticker — call from ngOnDestroy. */
  destroy(): void {
    this.stopTicker();
  }

  /** '42s', '3m 20s', '1h 5m'. Public so the template can format one-off values. */
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
