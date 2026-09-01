import { WritableSignal, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { StubTranslationService, flush, makeFile } from '../spec-helpers';
import { UploadedFile } from './file-types';
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
    expect(runner.isTranslating()).toBeFalse();
  });

  it('marks in-flight files failed on cancel and settles isTranslating', async () => {
    files.set([makeFile('a.srt'), makeFile('b.srt')]);
    parallelFiles = 2;

    runner.start();
    await flush();
    const signals = service.pending.map((c) => c.cancelSignal!);
    expect(signals.length).toBe(2);
    expect(signals.every((s) => !s.aborted)).toBeTrue();

    runner.cancel();
    expect(signals.every((s) => s.aborted)).toBeTrue();

    // The service rejects once the run's signal aborts.
    for (const call of service.takeAll()) call.reject(new TranslationCancelledError());
    await flush();

    expect(runner.isTranslating()).toBeFalse();
    expect(runner.isCancelling()).toBeFalse();
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
    expect(runner.canRetryFailed()).toBeTrue();

    runner.retryFailed();
    await flush();

    const retrySignals = service.pending.map((c) => c.cancelSignal!);
    expect(retrySignals.length).toBe(2);
    // A reused controller would already be aborted, so nothing would run.
    expect(firstSignals.every((s) => s.aborted)).toBeTrue();
    expect(retrySignals.every((s) => !s.aborted)).toBeTrue();
    // includes() compares by identity, unlike Jasmine's toContain.
    expect(retrySignals.some((s) => firstSignals.includes(s))).toBeFalse();
    expect(runner.isTranslating()).toBeTrue();

    for (const call of service.takeAll()) call.resolve('translated');
    await flush();

    expect(runner.isTranslating()).toBeFalse();
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
});
