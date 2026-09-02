// The call accounting behind the completion summary and the pre-run estimate:
// how many calls each optional pass adds, and how the sample that feeds the
// adequacy check is spread across a file.

import {
  ADEQUACY_MIN_BATCHES,
  ESTIMATED_SECS_PER_CALL,
  FIX_FLAGGED_MIN,
} from './constants';
import {
  CALL_KINDS,
  addCallCounts,
  adequacySampleSize,
  blocksPerSecond,
  describeCalls,
  emptyCallCounts,
  fixFlaggedCap,
  projectRun,
  sampleIndices,
  systematicRepairCap,
  totalCalls,
} from './run-stats';

describe('call counts', () => {
  it('starts at zero for every pass', () => {
    const counts = emptyCallCounts();
    expect(CALL_KINDS.every((kind) => counts[kind] === 0)).toBe(true);
    expect(totalCalls(counts)).toBe(0);
  });

  it('adds two runs together without mutating either', () => {
    const a = { ...emptyCallCounts(), scan: 1, translate: 4 };
    const b = { ...emptyCallCounts(), translate: 2, repair: 1 };
    expect(addCallCounts(a, b)).toEqual({
      ...emptyCallCounts(), scan: 1, translate: 6, repair: 1,
    });
    expect(a.translate).toBe(4);
  });

  it('names only the passes that made a call', () => {
    expect(describeCalls({
      ...emptyCallCounts(), scan: 1, translate: 38, repair: 2,
    })).toBe('1 scan, 38 translate, 2 repair');
  });

  it('spells the back-translation pass the way a person would read it', () => {
    expect(describeCalls({ ...emptyCallCounts(), backTranslation: 8 }))
      .toBe('8 back-translation');
  });

  it('reports no throughput before the clock has run', () => {
    expect(blocksPerSecond(100, 0)).toBe(0);
    expect(blocksPerSecond(100, 5000)).toBe(20);
  });
});

describe('the flagged-batch repair cap', () => {
  it('never re-issues more than a twentieth of a file', () => {
    expect(fixFlaggedCap(100)).toBe(5);
    expect(fixFlaggedCap(200)).toBe(10);
  });

  it('keeps a floor so a short file still gets a repair', () => {
    expect(fixFlaggedCap(1)).toBe(FIX_FLAGGED_MIN);
    expect(fixFlaggedCap(0)).toBe(FIX_FLAGGED_MIN);
  });

  it('widens to a quarter of the file for a failure that repeats', () => {
    // The graded run flagged 16 of 44 batches for one drifting term and
    // repaired 2 of them; a quarter of the file answers the whole problem.
    expect(systematicRepairCap(44)).toBe(11);
    expect(systematicRepairCap(100)).toBe(25);
    // Never below the ordinary cap, whatever the file size.
    expect(systematicRepairCap(1)).toBe(FIX_FLAGGED_MIN);
    expect(systematicRepairCap(6)).toBe(fixFlaggedCap(6));
  });
});

describe('the adequacy sample', () => {
  it('takes a fifth of the batches', () => {
    expect(adequacySampleSize(40)).toBe(8);
    expect(adequacySampleSize(100)).toBe(20);
  });

  it('keeps a floor, but never asks for more batches than exist', () => {
    expect(adequacySampleSize(3)).toBe(ADEQUACY_MIN_BATCHES);
    expect(adequacySampleSize(1)).toBe(1);
    expect(adequacySampleSize(0)).toBe(0);
  });

  it('spans the file instead of sampling its opening', () => {
    expect(sampleIndices(40, 8)).toEqual([0, 5, 10, 15, 20, 25, 30, 35]);
    expect(sampleIndices(10, 3)).toEqual([0, 3, 6]);
  });

  it('asks for nothing when there is nothing to sample', () => {
    expect(sampleIndices(0, 4)).toEqual([]);
    expect(sampleIndices(4, 0)).toEqual([]);
  });
});

describe('projectRun', () => {
  const base = {
    blockCounts: [100],
    batchSize: 10,
    lanes: 5,
    review: false,
    fixFlagged: false,
    verifyAdequacy: false,
  };

  it('counts one scan per file and one call per batch', () => {
    const projected = projectRun({ ...base, blockCounts: [100, 45] });
    expect(projected.calls.scan).toBe(2);
    expect(projected.calls.translate).toBe(15);
    expect(projected.total).toBe(17);
  });

  it('adds a review call per batch and a capped repair budget', () => {
    const projected = projectRun({ ...base, review: true, fixFlagged: true });
    expect(projected.calls.review).toBe(10);
    expect(projected.calls.repair).toBe(FIX_FLAGGED_MIN);
  });

  it('adds the back-translation sample only when it is asked for', () => {
    expect(projectRun(base).calls.backTranslation).toBe(0);
    expect(projectRun({ ...base, verifyAdequacy: true }).calls.backTranslation).toBe(2);
  });

  it('divides the wall-clock estimate by the lanes actually in flight', () => {
    const serial = projectRun({ ...base, lanes: 1 });
    expect(serial.estimateMs).toBe(11 * ESTIMATED_SECS_PER_CALL * 1000);
    expect(projectRun({ ...base, lanes: 5 }).estimateMs)
      .toBeCloseTo(serial.estimateMs / 5, 6);
    // A nonsense lane count must not divide by zero.
    expect(projectRun({ ...base, lanes: 0 }).estimateMs).toBe(serial.estimateMs);
  });

  it('projects nothing for an empty queue', () => {
    const projected = projectRun({ ...base, blockCounts: [] });
    expect(projected.total).toBe(0);
    expect(projected.estimateMs).toBe(0);
  });

  it('leaves attribution out: only the prepass knows how many scenes qualify', () => {
    expect(projectRun({ ...base, review: true }).calls.attribution).toBe(0);
  });
});
