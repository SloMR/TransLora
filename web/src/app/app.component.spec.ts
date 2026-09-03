import { ComponentFixture, TestBed } from '@angular/core/testing';

import { AppComponent } from './app.component';
import { emptyCallCounts } from './core/run-stats';
import { TranslationService } from './core/translation.service';
import { STORAGE_KEY } from './run-settings';
import { StubTranslationService, flush, makeFile } from './spec-helpers';

describe('AppComponent', () => {
  let fixture: ComponentFixture<AppComponent>;
  let app: AppComponent;
  let service: StubTranslationService;

  beforeEach(async () => {
    localStorage.clear();
    service = new StubTranslationService();

    await TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [{ provide: TranslationService, useValue: service }],
    }).compileComponents();

    fixture = TestBed.createComponent(AppComponent);
    app = fixture.componentInstance;
    // 'custom' needs no key, so an endpoint is all canTranslate() is missing.
    app.settings.apiUrl.set('http://localhost:1234/v1/chat/completions');
  });

  afterEach(() => {
    fixture.destroy();
    localStorage.clear();
  });

  // The shell only wires the feature components together, so the check that
  // matters is that they all render and swap over as a run progresses.
  describe('rendering', () => {
    it('renders the shell, the advanced popover and the results view', async () => {
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;

      // Empty: the intake takes the stage, no files in the bin yet.
      for (const sel of ['.bin-brand', '.run-status', '.card-files-stage', '.steps',
                         '.card-settings', '.dropzone', '.lang-grid', '.segmented',
                         '.provider-pills', '.advanced-toggle', '#source-lang', '#target-lang']) {
        expect(el.querySelector(sel), sel).not.toBeNull();
      }
      expect(el.querySelector('.bin-files')).toBeNull();
      expect(el.querySelector('.btn-translate')).toBeNull();
      expect(el.querySelector('.run-status')!.textContent).toContain('Ready');

      // The advanced disclosure opens inside the settings card it is projected into.
      (el.querySelector('.advanced-toggle') as HTMLButtonElement).click();
      fixture.detectChanges();
      expect(el.querySelector('#advanced-panel')).not.toBeNull();
      expect(el.querySelector('#batch-size')).not.toBeNull();
      expect(el.querySelector('#review-api-url')).not.toBeNull();
      expect(el.querySelector('#review-model')).not.toBeNull();
      expect(el.querySelector('.card-settings.card-settings-open')).not.toBeNull();

      (el.querySelector('.advanced-close') as HTMLButtonElement).click();
      fixture.detectChanges();
      expect(el.querySelector('#advanced-panel')).toBeNull();

      // Queued: the files move into the bin and the stage becomes the ready card.
      app.files.set([makeFile('a.srt'), makeFile('b.srt')]);
      app.settings.parallelFiles.set(2);
      fixture.detectChanges();
      expect(el.querySelector('.bin-files .card-files')).not.toBeNull();
      expect(el.querySelectorAll('.file-card').length).toBe(2);
      expect(el.querySelector('.action-card .btn-translate')).not.toBeNull();
      expect(el.querySelector('.receipt')).not.toBeNull();
      expect(el.querySelector('.run-status')!.textContent).toContain('2 files ready');

      // Running: the stage shows the run, each bin row its own progress.
      app.startTranslation();
      await flush();
      fixture.detectChanges();
      expect(el.querySelector('.results-card')).not.toBeNull();
      expect(el.querySelector('.action-card')).toBeNull();
      expect(el.querySelector('.run-status .status-dot-live')).not.toBeNull();
      // Before the first batch the run is reading the file, and says so instead of "0%".
      expect(el.querySelector('.file-card .file-card-meta')!.textContent).toContain('reading names, terms and speakers');
      expect(el.querySelector('.summary-meta')!.textContent).toContain('reading the file first');
      // The prepass has a percent too, from the scan call.
      expect(el.querySelector('.file-card .row-batch')!.textContent).toBe('0%');

      service.pending[0]!.onProgress!({ stage: 'batches', done: 1, total: 4, percent: 30 });
      fixture.detectChanges();
      expect(el.querySelector('.file-card .file-card-meta')!.textContent).toContain('batch 1 of 4');
      expect(el.querySelector('.file-card .row-batch')!.textContent).toBe('30%');
      expect(el.querySelector('.summary-meta')!.textContent).toContain('translating batch 1 of 4');
      expect(el.querySelector('.file-card .file-dot-live')).not.toBeNull();

      // After the last batch the bar keeps moving and the step says what still runs.
      service.pending[0]!.onProgress!({ stage: 'repairing', done: 1, total: 3, percent: 92 });
      fixture.detectChanges();
      expect(el.querySelector('.file-card .file-card-meta')!.textContent).toContain('repairing the flagged lines, 1 of 3');
      expect(el.querySelector('.summary-meta')!.textContent).toContain('repairing the flagged lines, 1 of 3');
      expect(el.querySelector('.file-card .row-batch')!.textContent).toBe('92%');

      for (const call of service.takeAll()) call.resolve('translated');
      await flush();
      fixture.detectChanges();
      expect(el.querySelectorAll('.file-card .file-dot-done').length).toBe(2);
      expect(el.querySelector('.btn-download-all')).not.toBeNull();
      expect(el.querySelector('.btn-again')).not.toBeNull();
      expect(el.querySelector('.run-status')!.textContent).toContain('Done');
    });

    it('offers the three quality presets and lights the one the knobs spell', () => {
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const pills = Array.from(el.querySelectorAll('[aria-labelledby="quality-label"] .segment'))
        .map((b) => b.textContent!.trim());
      expect(pills).toEqual(['Fast', 'Balanced', 'Best']);
      expect(el.querySelector('[aria-labelledby="quality-label"] .segment-active')!.textContent!.trim())
        .toBe('Best');

      const fast = Array.from(el.querySelectorAll<HTMLButtonElement>(
        '[aria-labelledby="quality-label"] .segment')).find((b) => b.textContent!.trim() === 'Fast')!;
      fast.click();
      fixture.detectChanges();
      expect(app.settings.review()).toBe(false);
      expect(app.settings.qualityPreset()).toBe('fast');

      // A knob moved by hand turns the row into a state, not a choice.
      app.settings.verifyAdequacy.set(true);
      fixture.detectChanges();
      expect(el.querySelector('.segment-static')!.textContent!.trim()).toBe('Custom');
    });

    it('says what the run repaired and which cues it could not, by file', async () => {
      const file = makeFile('a.srt', 2);
      file.doc = {
        format: 'srt',
        blocks: [
          { number: 1, timestamp: '00:00:01,000 --> 00:00:02,000', text: 'One' },
          { number: 2, timestamp: '00:00:03,000 --> 00:00:04,000', text: 'Two' },
        ],
        rebuild: (blocks) => blocks.map((b) => b.text).join('\n'),
      };
      app.files.set([file]);
      fixture.detectChanges();
      app.startTranslation();
      await flush();

      const call = service.pending[0]!;
      call.notify('Review pass failed - keeping the first-pass translation');
      call.reportReview({
        blocks: [
          { number: 1, timestamp: file.doc.blocks[0]!.timestamp, text: 'uno' },
          { number: 2, timestamp: file.doc.blocks[1]!.timestamp, text: 'dos' },
        ],
        flags: [{
          block: 2, cause: 'term:halftime', problem: 'p',
          message: "Block 2: glossary term 'halftime' was not rendered as 'الاستراحة'",
        }],
        raised: [
          { block: 1, cause: 'tags', problem: 'p', message: 'Block 1: formatting tags changed' },
          { block: 1, cause: 'dashes', problem: 'p', message: 'Block 1: speaker dashes changed' },
          { block: 2, cause: 'term:halftime', problem: 'p',
            message: "Block 2: glossary term 'halftime' was not rendered as 'الاستراحة'" },
        ],
      });
      call.notify('Block 1: re-translated after 2 flag(s); 0 left');
      call.resolve('uno\ndos');
      await flush();
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      // What was fixed, by kind, and what was not, by line. No log.
      expect(el.querySelector('.repairs-fixed')!.textContent!.replace(/\s+/g, ' ').trim())
        .toBe('2 lines repaired by the run — formatting tags 1, speaker dashes 1');
      expect(el.querySelector('.repairs-left')!.textContent).toContain('1 line still flagged');
      const left = Array.from(el.querySelectorAll('.repairs-list li')).map((li) => [
        li.querySelector('.repairs-where')!.textContent, li.querySelector('.repairs-reason')!.textContent]);
      expect(left).toEqual([['Line 2', "glossary term 'halftime' was not rendered as 'الاستراحة'"]]);
      expect(el.querySelector('.summary-flagged')!.textContent).toContain('1 line still flagged');
      expect(el.querySelector('.file-card .file-card-meta')!.textContent).toContain('1 flagged');
      // Nothing asks the user to go through the file by hand; the one file downloads as itself.
      expect(el.querySelector('.file-open')).toBeNull();
      expect(el.querySelector('app-review-panel')).toBeNull();
      expect(el.querySelector('.btn-download-all')!.textContent!.trim()).toBe('Download file');
    });

    it('calls a run that lost every file a failure, and shows the reason with a retry', async () => {
      app.files.set([makeFile('a.srt', 4)]);
      fixture.detectChanges();
      app.startTranslation();
      await flush();
      service.pending[0]!.notify('Provider requires the default temperature; adjusted for the rest of the run.');
      service.pending[0]!.reject(new Error('HTTP 400: unknown model (block 1)'));
      await flush();
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('.summary-title')!.textContent).toBe('Translation failed');
      expect(el.querySelector('.summary-header-failed')).not.toBeNull();
      expect(el.querySelector('.summary-meta')!.textContent).toContain('0 done, 1 failed');
      // The reason and the way out live on the card, not only in the bin.
      expect(el.querySelector('.failed-msg')!.textContent).toBe('HTTP 400: unknown model (block 1)');
      expect(el.querySelector('.btn-retry')).not.toBeNull();
      expect(el.querySelector('.run-status')!.textContent).toContain('Done, 1 failed');
    });

    it('reports what the finished run cost', async () => {
      app.files.set([makeFile('a.srt', 40)]);
      fixture.detectChanges();
      app.startTranslation();
      await flush();

      const el: HTMLElement = fixture.nativeElement;
      // Nothing to report until a run hands its accounting back.
      expect(el.querySelector('.run-throughput')).toBeNull();

      const call = service.pending[0]!;
      call.reportStats({
        calls: { ...emptyCallCounts(), scan: 1, translate: 4, review: 4 },
        blocks: 40,
        elapsedMs: 8000,
      });
      call.resolve('translated');
      await flush();
      fixture.detectChanges();

      const line = el.querySelector('.run-throughput')!.textContent!;
      expect(line).toContain('9 model calls (1 scan, 4 translate, 4 review)');
      expect(line).toContain('lines a second');
      // Nothing was negotiated, so nothing about the request shape is said.
      expect(line).not.toContain('max_completion_tokens');
    });

    it('names the request shape a provider had to be given', async () => {
      app.files.set([makeFile('a.srt', 40)]);
      fixture.detectChanges();
      app.startTranslation();
      await flush();

      const call = service.pending[0]!;
      call.reportStats({
        calls: { ...emptyCallCounts(), scan: 1, translate: 4 },
        blocks: 40,
        elapsedMs: 8000,
        dialect: { tokenParam: 'max_completion_tokens', sendTemperature: false, reasoningEffort: 'none' },
      });
      call.resolve('translated');
      await flush();
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('.run-throughput')!.textContent)
        .toContain('max_completion_tokens, no temperature, reasoning_effort none');
    });
  });

  describe('output naming', () => {
    it('inserts the target language code before the extension of every supported format', () => {
      app.settings.targetLang.set('Arabic');
      for (const ext of ['srt', 'vtt', 'ass', 'ssa', 'sub', 'sbv']) {
        expect(app.makeOutputName(`movie.${ext}`)).toBe(`movie.ar.${ext}`);
      }
    });

    it('appends the code when there is no extension and keeps inner dots', () => {
      app.settings.targetLang.set('Arabic');
      expect(app.makeOutputName('movie')).toBe('movie.ar');
      expect(app.makeOutputName('show.s01e02.srt')).toBe('show.s01e02.ar.srt');
    });

    it('tracks the selected language and falls back to its first two letters', () => {
      app.settings.targetLang.set('French');
      expect(app.makeOutputName('movie.srt')).toBe('movie.fr.srt');

      app.settings.targetLang.set('Klingon');
      expect(app.makeOutputName('movie.srt')).toBe('movie.kl.srt');
    });
  });

  describe('run orchestration', () => {
    it('removing one file keeps every other file\'s translation', async () => {
      app.files.set([makeFile('a.srt'), makeFile('b.srt'), makeFile('c.srt')]);
      app.settings.parallelFiles.set(3);

      app.startTranslation();
      await flush();
      for (const call of service.takeAll()) call.resolve(`out:${call.doc.format}`);
      await flush();
      expect(app.runner.doneFiles().length).toBe(3);

      app.removeFile(1);

      expect(app.files().map((f) => f.name)).toEqual(['a.srt', 'c.srt']);
      expect(app.runner.fileStatuses().map((s) => s.name)).toEqual(['a.srt', 'c.srt']);
      expect(app.runner.fileStatuses().map((s) => s.outputName)).toEqual([
        'a.ar.srt',
        'c.ar.srt',
      ]);
      expect(app.runner.fileStatuses().every((s) => s.content === 'out:srt')).toBe(true);
    });
  });

  // The UI counterpart of the CLI's --formality / --dialect / --max-line-chars
  // / --no-reflow flags, checked in cli/tests/test_translora.py.
  describe('quality settings', () => {
    it('hands every quality option to the translation service', async () => {
      app.files.set([makeFile('a.srt')]);
      app.settings.reflow.set(false);
      app.settings.formality.set('informal');
      app.settings.setDialect('Egyptian Arabic');
      app.settings.setNumber('maxLineChars', 38);

      app.startTranslation();
      await flush();

      const call = service.pending[0]!;
      expect(call.quality).toEqual(expect.objectContaining({
        reflow: false,
        formality: 'informal',
        dialect: 'Egyptian Arabic',
        maxLineChars: 38,
        contextOverlap: app.settings.contextOverlap(),
        scanBudget: app.settings.scanBudget(),
        review: app.settings.review(),
        refineAttribution: app.settings.refineAttribution(),
        fixFlagged: app.settings.fixFlagged(),
        verifyAdequacy: app.settings.verifyAdequacy(),
        fullAttribution: app.settings.fullAttribution(),
      }));

      call.resolve('translated');
      await flush();
    });

    it('carries the review provider through without ever storing its key', async () => {
      app.files.set([makeFile('a.srt')]);
      app.settings.reviewApiUrl.set('https://cloud.test/v1/chat/completions');
      app.settings.reviewApiKey.set('sk-review-key-goes-here');
      app.settings.reviewModel.set('big-model');
      fixture.detectChanges();

      app.startTranslation();
      await flush();

      const call = service.pending[0]!;
      expect(call.quality).toEqual(expect.objectContaining({
        reviewApiUrl: 'https://cloud.test/v1/chat/completions',
        reviewApiKey: 'sk-review-key-goes-here',
        reviewModel: 'big-model',
      }));
      expect(localStorage.getItem(STORAGE_KEY)).not.toContain('sk-review');

      call.resolve('translated');
      await flush();
    });

    it('carries a switched-on opt-in through to the run', async () => {
      app.files.set([makeFile('a.srt')]);
      app.settings.verifyAdequacy.set(true);
      app.settings.fullAttribution.set(true);
      app.settings.fixFlagged.set(false);

      app.startTranslation();
      await flush();

      const call = service.pending[0]!;
      expect(call.quality).toEqual(expect.objectContaining({
        fixFlagged: false, verifyAdequacy: true, fullAttribution: true,
      }));

      call.resolve('translated');
      await flush();
    });
  });

  // The web counterpart of the CLI's --dry-run call breakdown.
  describe('the pre-run cost estimate', () => {
    it('says nothing with no files queued', () => {
      expect(app.estimate()).toBeNull();
      expect(app.projectionCaveat()).toBe('');
    });

    it('breaks the projected calls down and times them at the chosen lanes', () => {
      app.files.set([makeFile('a.srt', 100)]);
      app.settings.setNumber('batchSize', 10);
      app.settings.setNumber('concurrency', 5);
      app.settings.setNumber('parallelFiles', 1);
      app.settings.review.set(true);
      app.settings.fixFlagged.set(true);
      app.settings.verifyAdequacy.set(false);

      // 1 scan + 10 translate + 10 review + 2 repair, as the receipt shows them.
      expect(app.estimate()).toEqual({
        calls: '23',
        callsDetail: '1 scan, 10 translate, 10 review, 2 repair',
        time: '16s',
        lanes: '5',
      });
      expect(app.projectionCaveat()).toContain('Estimate only, at 3.5s per call.');
      expect(app.projectionCaveat()).toContain('speaker-attribution calls are not');
    });

    it('grows when an opt-in pass is switched on', () => {
      app.files.set([makeFile('a.srt', 100)]);
      app.settings.setNumber('batchSize', 10);
      // Best is the default now, so start from the pass being off.
      app.settings.verifyAdequacy.set(false);
      const before = app.projection().total;
      app.settings.verifyAdequacy.set(true);
      expect(app.projection().total).toBe(before + 2);
    });
  });
});
