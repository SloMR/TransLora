import { describe, expect, it } from 'vitest';

import { ProgressMeter, RunProgress } from './run-progress';

describe('ProgressMeter', () => {
  function meter(): { meter: ProgressMeter; seen: RunProgress[] } {
    const seen: RunProgress[] = [];
    return { meter: new ProgressMeter((p) => seen.push(p)), seen };
  }

  it('moves through the prepass instead of sitting at 0%', () => {
    const { meter: m, seen } = meter();
    m.plan('prepass', 1 + 3);
    m.plan('batches', 10);
    m.count('scan');
    // The scan is weighted like several batch calls: 4 of 17 units.
    expect(seen.at(-1)!.percent).toBe(23);
    m.count('attribution');
    expect(seen.at(-1)!.percent).toBe(29);
  });

  it('counts the batch stage in batches and reads 100 only when finished', () => {
    const { meter: m, seen } = meter();
    m.plan('prepass', 1);
    m.plan('batches', 4);
    m.count('scan');
    m.begin('batches');
    m.batches(0, 4);
    expect(seen.at(-1)).toMatchObject({ stage: 'batches', done: 0, total: 4 });
    for (let i = 0; i < 4; i++) { m.count('translate'); m.batches(i + 1, 4); }
    // Every planned call is out, but the file is not written yet.
    expect(seen.at(-1)).toMatchObject({ done: 4, total: 4, percent: 99 });
    m.finish();
    expect(seen.at(-1)!.percent).toBe(100);
  });

  it('keeps moving through the checks and repairs after the last batch', () => {
    const { meter: m, seen } = meter();
    m.plan('prepass', 1);
    m.plan('batches', 2);
    m.plan('checking', 2);
    m.plan('repairing', 2);
    m.count('scan');
    m.begin('batches');
    m.count('translate'); m.count('translate'); m.batches(2, 2);
    const afterBatches = seen.at(-1)!.percent;
    expect(afterBatches).toBeLessThan(99);
    m.begin('checking');
    m.count('backTranslation');
    expect(seen.at(-1)).toMatchObject({ stage: 'checking', done: 1, total: 2 });
    expect(seen.at(-1)!.percent).toBeGreaterThan(afterBatches);
    const afterChecking = seen.at(-1)!.percent;
    m.begin('repairing');
    m.plan('repairing', 1);
    m.count('repair');
    expect(seen.at(-1)).toMatchObject({ stage: 'repairing', done: 1, total: 1 });
    expect(seen.at(-1)!.percent).toBeGreaterThan(afterChecking);
    expect(seen.at(-1)!.percent).toBeLessThan(100);
  });

  it('never goes backwards when a stage turns out bigger than planned', () => {
    const { meter: m, seen } = meter();
    m.plan('prepass', 1);
    m.plan('batches', 2);
    m.count('scan');
    m.begin('batches');
    m.count('translate'); m.count('translate'); m.batches(2, 2);
    const before = seen.at(-1)!.percent;
    // Ten repairs nobody planned for: the total grows, the bar holds.
    m.begin('repairing');
    m.plan('repairing', 10);
    expect(seen.at(-1)!.percent).toBe(before);
    m.count('repair');
    expect(seen.at(-1)!.percent).toBeGreaterThanOrEqual(before);
    // A stage that overruns its plan grows the plan rather than exceeding it.
    for (let i = 0; i < 12; i++) m.count('repair');
    expect(seen.at(-1)).toMatchObject({ done: 13, total: 13 });
  });
});
