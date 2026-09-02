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

      for (const sel of ['.nav', '.hero', '.card-files', '.card-settings', '.action-card',
                         '.dropzone', '.lang-grid', '.provider-pills', '.advanced-toggle',
                         '#source-lang', '#target-lang', '.btn-translate']) {
        expect(el.querySelector(sel), sel).not.toBeNull();
      }

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

      app.files.set([makeFile('a.srt'), makeFile('b.srt')]);
      app.settings.parallelFiles.set(2);
      fixture.detectChanges();
      expect(el.querySelectorAll('.file-card').length).toBe(2);

      // Starting a run swaps the action card for the results card.
      app.startTranslation();
      await flush();
      fixture.detectChanges();
      expect(el.querySelector('.results-card')).not.toBeNull();
      expect(el.querySelector('.action-card')).toBeNull();

      service.pending[0]!.onProgress!({ currentBatch: 1, totalBatches: 4 });
      fixture.detectChanges();
      expect(el.querySelector('.progress-row .row-batch')!.textContent).toContain('1 / 4');

      for (const call of service.takeAll()) call.resolve('translated');
      await flush();
      fixture.detectChanges();
      expect(el.querySelectorAll('.result-item').length).toBe(2);
      expect(el.querySelector('.btn-download-all')).not.toBeNull();
      expect(el.querySelector('.btn-again')).not.toBeNull();
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
      expect(line).toContain('9 LLM calls (1 scan, 4 translate, 4 review)');
      expect(line).toContain('blocks/s');
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
        dialect: { tokenParam: 'max_completion_tokens', sendTemperature: false },
      });
      call.resolve('translated');
      await flush();
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('.run-throughput')!.textContent)
        .toContain('max_completion_tokens, no temperature');
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
      expect(app.projectionSummary()).toBe('');
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

      // 1 scan + 10 translate + 10 review + 2 repair.
      expect(app.projectionSummary()).toBe(
        '~23 LLM calls (1 scan, 10 translate, 10 review, 2 repair) '
        + '· about 16s at 5 parallel requests.',
      );
      expect(app.projectionCaveat()).toContain('Estimate only, at 3.5s per call.');
      expect(app.projectionCaveat()).toContain('speaker-attribution calls are not');
    });

    it('grows when an opt-in pass is switched on', () => {
      app.files.set([makeFile('a.srt', 100)]);
      app.settings.setNumber('batchSize', 10);
      const before = app.projection().total;
      app.settings.verifyAdequacy.set(true);
      expect(app.projection().total).toBe(before + 2);
    });
  });
});
