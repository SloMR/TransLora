import { signal } from '@angular/core';

import { TimeTracker } from './time-tracker';

interface FakeFile {
  timeMs?: number;
  currentBatch?: number;
  totalBatches?: number;
}

function setup(initial: {
  done?: FakeFile[];
  inProgress?: FakeFile[];
  pending?: number;
}) {
  const done = signal<FakeFile[]>(initial.done ?? []);
  const inProgress = signal<FakeFile[]>(initial.inProgress ?? []);
  const pending = signal<number>(initial.pending ?? 0);
  const tracker = new TimeTracker(done, inProgress, pending);
  return { tracker, done, inProgress, pending };
}

describe('TimeTracker', () => {
  let nowValue = 0;

  beforeEach(() => {
    nowValue = 1000;
    spyOn(performance, 'now').and.callFake(() => nowValue);
    jasmine.clock().install();
  });

  afterEach(() => {
    jasmine.clock().uninstall();
  });

  describe('formatMs', () => {
    const { tracker } = setup({});
    it('formats sub-minute durations as seconds', () => {
      expect(tracker.formatMs(0)).toBe('0s');
      expect(tracker.formatMs(59_000)).toBe('59s');
    });
    it('formats minutes', () => {
      expect(tracker.formatMs(60_000)).toBe('1m 0s');
      expect(tracker.formatMs(200_000)).toBe('3m 20s');
    });
    it('formats hours', () => {
      expect(tracker.formatMs(3_600_000)).toBe('1h 0m');
      expect(tracker.formatMs(3_900_000)).toBe('1h 5m');
    });
  });

  describe('elapsedMs', () => {
    it('is 0 before begin()', () => {
      const { tracker } = setup({});
      expect(tracker.elapsedMs()).toBe(0);
    });

    it('grows while running and freezes after finish()', () => {
      const { tracker } = setup({});
      tracker.begin();
      nowValue += 5_000;
      // Advance the internal ticker so nowMs signal updates.
      jasmine.clock().tick(500);
      expect(tracker.elapsedMs()).toBe(5_000);

      tracker.finish();
      nowValue += 10_000;
      jasmine.clock().tick(500);
      expect(tracker.elapsedMs()).toBe(5_000);
      tracker.destroy();
    });
  });

  describe('etaMs (fraction-based)', () => {
    it('is null before any progress reports in', () => {
      const { tracker } = setup({ pending: 2 });
      tracker.begin();
      expect(tracker.etaMs()).toBeNull();
      tracker.destroy();
    });

    it('produces an estimate for a single in-progress file once batches report', () => {
      const { tracker, inProgress, pending } = setup({
        inProgress: [{ currentBatch: 1, totalBatches: 10 }],
        pending: 0,
      });
      pending.set(0);
      inProgress.set([{ currentBatch: 1, totalBatches: 10 }]);

      tracker.begin();
      nowValue += 10_000; // 10s elapsed to finish 1/10 of a file
      jasmine.clock().tick(500);

      // fractionDone = 0.1, total = 1, remaining = 10s * (1 - 0.1) / 0.1 = 90s.
      const eta = tracker.etaMs();
      expect(eta).not.toBeNull();
      expect(eta!).toBeCloseTo(90_000, -2);
      tracker.destroy();
    });

    it('returns 0 once all files are done', () => {
      const { tracker, done, inProgress } = setup({
        done: [{ timeMs: 5_000 }],
      });
      inProgress.set([]);
      done.set([{ timeMs: 5_000 }]);
      tracker.begin();
      expect(tracker.etaMs()).toBe(0);
      tracker.destroy();
    });
  });

  describe('begin vs resume', () => {
    it('begin() resets total to 0', () => {
      const { tracker } = setup({});
      tracker.begin();
      nowValue += 5_000;
      jasmine.clock().tick(500);
      tracker.finish();
      expect(tracker.elapsedMs()).toBe(5_000);

      tracker.begin();
      expect(tracker.elapsedMs()).toBe(0);
      tracker.destroy();
    });

    it('resume() continues total from previous finish()', () => {
      const { tracker } = setup({});
      tracker.begin();
      nowValue += 5_000;
      jasmine.clock().tick(500);
      tracker.finish(); // frozen total = 5_000

      nowValue += 1_000; // time passes while idle
      tracker.resume();
      expect(tracker.elapsedMs()).toBe(5_000);

      nowValue += 3_000;
      jasmine.clock().tick(500);
      expect(tracker.elapsedMs()).toBe(8_000);

      tracker.finish();
      expect(tracker.elapsedMs()).toBe(8_000);
      tracker.destroy();
    });
  });
});
