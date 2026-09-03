import { WritableSignal, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { StubTranslationService, flush, makeFile } from '../spec-helpers';
import { UploadedFile } from './file-types';
import { SubtitleBlock } from './srt-parser';
import { TranslationRunnerService } from './translation-runner.service';
import { TranslationCancelledError, TranslationService } from './translation.service';

describe('TranslationRunnerService', () => {
  let runner: TranslationRunnerService;
  let service: StubTranslationService;
  let files: WritableSignal<UploadedFile[]>;
  let parallelFiles: number;

  beforeEach(() => {
    service = new StubTranslationService();
    files = signal<UploadedFile[]>([]);
    parallelFiles = 1;

    TestBed.configureTestingModule({
      providers: [{ provide: TranslationService, useValue: service }],
    });

    runner = TestBed.inject(TranslationRunnerService);
    runner.connect({
      files,
      outputName: (name) => name.replace(/^(.*)(\.[^.]+)$/, '$1.ar$2'),
      request: () => ({
        provider: { apiUrl: 'http://localhost:1234/v1/chat/completions', apiKey: '', model: '' },
        sourceLang: '',
        targetLang: 'Arabic',
        batchSize: 10,
        concurrency: 5,
        maxRetries: 5,
        parallelFiles,
        quality: {},
      }),
    });
  });

  afterEach(() => {
    runner.destroy();
  });

  it('never runs more files at once than parallelFiles', async () => {
    files.set(['a', 'b', 'c', 'd', 'e'].map((n) => makeFile(`${n}.srt`)));
    parallelFiles = 2;

    runner.start();
    await flush();
    expect(service.pending.length).toBe(2);

    // Drain one at a time; a freed worker must pick the next index up.
    while (service.pending.length > 0) {
      service.pending.shift()!.resolve('translated');
      await flush();
    }

    expect(service.maxConcurrent).toBe(2);
    expect(runner.doneFiles().length).toBe(5);
    expect(runner.isTranslating()).toBe(false);
  });

  it('marks in-flight files failed on cancel and settles isTranslating', async () => {
    files.set([makeFile('a.srt'), makeFile('b.srt')]);
    parallelFiles = 2;

    runner.start();
    await flush();
    const signals = service.pending.map((c) => c.cancelSignal!);
    expect(signals.length).toBe(2);
    expect(signals.every((s) => !s.aborted)).toBe(true);

    runner.cancel();
    expect(signals.every((s) => s.aborted)).toBe(true);

    // The service rejects once the run's signal aborts.
    for (const call of service.takeAll()) call.reject(new TranslationCancelledError());
    await flush();

    expect(runner.isTranslating()).toBe(false);
    expect(runner.isCancelling()).toBe(false);
    expect(runner.failedFiles().map((f) => f.error)).toEqual(['Cancelled', 'Cancelled']);
  });

  it('retrying after a cancel uses a fresh AbortController, not the aborted one', async () => {
    files.set([makeFile('a.srt'), makeFile('b.srt')]);
    parallelFiles = 2;

    runner.start();
    await flush();
    const firstSignals = service.pending.map((c) => c.cancelSignal!);

    runner.cancel();
    for (const call of service.takeAll()) call.reject(new TranslationCancelledError());
    await flush();
    expect(runner.canRetryFailed()).toBe(true);

    runner.retryFailed();
    await flush();

    const retrySignals = service.pending.map((c) => c.cancelSignal!);
    expect(retrySignals.length).toBe(2);
    // A reused controller would already be aborted, so nothing would run.
    expect(firstSignals.every((s) => s.aborted)).toBe(true);
    expect(retrySignals.every((s) => !s.aborted)).toBe(true);
    // includes() compares by identity, unlike Jasmine's toContain.
    expect(retrySignals.some((s) => firstSignals.includes(s))).toBe(false);
    expect(runner.isTranslating()).toBe(true);

    for (const call of service.takeAll()) call.resolve('translated');
    await flush();

    expect(runner.isTranslating()).toBe(false);
    expect(runner.failedFiles().length).toBe(0);
    expect(runner.doneFiles().length).toBe(2);
  });

  it('re-queues only the failed files and clears their error', async () => {
    files.set([makeFile('a.srt'), makeFile('b.srt')]);
    parallelFiles = 2;

    runner.start();
    await flush();
    const [first, second] = service.takeAll();
    first!.resolve('translated');
    second!.reject(new Error('502 Bad Gateway'));
    await flush();

    expect(runner.doneFiles().map((f) => f.name)).toEqual(['a.srt']);
    expect(runner.failedFiles().map((f) => f.error)).toEqual(['502 Bad Gateway']);

    runner.retryFailed();
    await flush();

    expect(service.pending.length).toBe(1);
    expect(runner.fileStatuses()[1]!.error).toBeUndefined();
    expect(runner.fileStatuses()[0]!.content).toBe('translated');
  });

  describe('what a finished file carries for the reviewer', () => {
    const source: SubtitleBlock[] = [
      { number: 1, timestamp: '00:00:01,000 --> 00:00:02,000', text: 'One' },
      { number: 2, timestamp: '00:00:03,000 --> 00:00:04,000', text: 'Two' },
    ];

    function reviewable(name: string): UploadedFile {
      const file = makeFile(name, source.length);
      file.doc = {
        format: 'srt',
        blocks: source,
        rebuild: (translated) => translated.map((b) => `${b.number}|${b.text}`).join('\n'),
      };
      return file;
    }

    it('carries the progress of every step from the prepass to the repairs', async () => {
      files.set([reviewable('a.srt')]);
      runner.start();
      await flush();
      expect(runner.fileStatuses()[0]!.progress).toEqual({ stage: 'prepass', done: 0, total: 0, percent: 0 });
      service.pending[0]!.onProgress!({ stage: 'batches', done: 0, total: 3, percent: 20 });
      expect(runner.fileStatuses()[0]!.progress).toMatchObject({ stage: 'batches', total: 3 });
      expect(runner.overallProgressPercent()).toBe(20);
      // After the last batch the stage says what still runs, and the bar keeps moving.
      service.pending[0]!.onProgress!({ stage: 'checking', done: 1, total: 2, percent: 85 });
      expect(runner.fileStatuses()[0]!.progress!.stage).toBe('checking');
      service.pending[0]!.onProgress!({ stage: 'repairing', done: 0, total: 4, percent: 90 });
      expect(runner.fileStatuses()[0]!.progress!.stage).toBe('repairing');
      expect(runner.overallProgressPercent()).toBe(90);
      service.pending[0]!.resolve('1|uno\n2|dos');
      await flush();
    });

    it('pairs every source cue with its translation and pins each flag to its cue', async () => {
      files.set([reviewable('a.srt')]);
      runner.start();
      await flush();

      const call = service.pending[0]!;
      call.reportReview({
        blocks: [
          { number: 1, timestamp: source[0]!.timestamp, text: 'uno' },
          { number: 2, timestamp: source[1]!.timestamp, text: 'dos' },
        ],
        flags: [{
          block: 2,
          message: 'Block 2: formatting tags changed (<i>,</i> -> )',
          problem: 'block 2: keep the tags',
          cause: 'tags',
        }],
        raised: [
          { block: 1, message: 'Block 1: speaker dashes changed (2 -> 0)',
            problem: 'block 1: keep the dashes', cause: 'dashes' },
          { block: 2, message: 'Block 2: formatting tags changed (<i>,</i> -> )',
            problem: 'block 2: keep the tags', cause: 'tags' },
        ],
      });
      call.resolve('1|uno\n2|dos');
      await flush();

      const review = runner.doneFiles()[0]!.review!;
      expect(review.cues).toEqual([
        { number: 1, timestamp: source[0]!.timestamp, source: 'One', target: 'uno', flags: [] },
        { number: 2, timestamp: source[1]!.timestamp, source: 'Two', target: 'dos',
          flags: ['Block 2: formatting tags changed (<i>,</i> -> )'] },
      ]);
      // Raised and then cleared: the dashes on cue 1 were put back by a retry.
      expect(review.repaired).toEqual([
        { block: 1, cause: 'dashes', message: 'Block 1: speaker dashes changed (2 -> 0)' },
      ]);
    });

    it('starts a retried file with no review from the failed run', async () => {
      files.set([reviewable('a.srt')]);
      runner.start();
      await flush();
      const call = service.pending[0]!;
      call.notify('something was skipped');
      call.reject(new Error('502 Bad Gateway'));
      await flush();

      runner.retryFailed();
      await flush();
      expect(runner.fileStatuses()[0]!.review).toBeUndefined();
    });
  });
});

