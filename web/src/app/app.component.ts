import {
  Component,
  OnDestroy,
  computed,
  effect,
  inject,
  signal,
  ChangeDetectionStrategy
} from '@angular/core';
import { AdvancedPanelComponent } from './advanced-panel/advanced-panel.component';
import { SUBTITLE_EXTS, UploadedFile } from './core/file-types';
import { LANGUAGES } from './core/languages';
import { describeCalls, projectRun } from './core/run-stats';
import { TranslationRunnerService } from './core/translation-runner.service';
import { FileIntakeComponent } from './file-intake/file-intake.component';
import { ProviderFormComponent } from './provider-form/provider-form.component';
import { RunSettings } from './run-settings';
import { RunResultsComponent } from './run-results/run-results.component';

// The page shell: theme, layout, and the wiring between the intake form, the
// settings forms and the run queue.
@Component({
  selector: 'app-root',
  imports: [
    AdvancedPanelComponent,
    FileIntakeComponent,
    ProviderFormComponent,
    RunResultsComponent,
  ],
  templateUrl: './app.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styleUrl: './app.component.scss',
})
export class AppComponent implements OnDestroy {
  supportedFormats = SUBTITLE_EXTS.map((ext) => ext.slice(1).toUpperCase());
  languages = LANGUAGES;

  settings = new RunSettings();
  readonly runner = inject(TranslationRunnerService);

  files = signal<UploadedFile[]>([]);
  errorMessage = signal('');
  showAdvanced = signal(false);

  // Output names already saved; drives the unsaved-work warning.
  private downloaded = signal<ReadonlySet<string>>(new Set<string>());

  private systemPrefersDark = signal(false);
  private themeMedia: MediaQueryList | null = null;

  theme = computed<'light' | 'dark'>(() => {
    const pref = this.settings.themePreference();
    if (pref !== 'system') return pref;
    return this.systemPrefersDark() ? 'dark' : 'light';
  });

  themeToggleLabel = computed(() =>
    this.theme() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode',
  );

  fileCountLabel = computed(() => {
    const n = this.files().length;
    return `${n} file${n === 1 ? '' : 's'}`;
  });

  totalBlocks = computed(() =>
    this.files().reduce((sum, f) => sum + f.blockCount, 0)
  );

  // What the queued files would cost before anyone pays for it.
  projection = computed(() => projectRun({
    blockCounts: this.files().map((f) => f.blockCount),
    batchSize: this.settings.batchSize(),
    lanes: this.settings.concurrency() * this.settings.parallelFiles(),
    review: this.settings.review(),
    fixFlagged: this.settings.fixFlagged(),
    verifyAdequacy: this.settings.verifyAdequacy(),
  }));

  projectionSummary = computed(() => {
    const projected = this.projection();
    if (projected.total === 0) return '';
    const lanes = this.settings.concurrency() * this.settings.parallelFiles();
    return `~${projected.total} LLM calls (${describeCalls(projected.calls)}) `
      + `· about ${this.runner.tracker.formatMs(projected.estimateMs)} at `
      + `${lanes} parallel request${lanes === 1 ? '' : 's'}.`;
  });

  projectionCaveat = computed(() => {
    const projected = this.projection();
    if (projected.total === 0) return '';
    return `Estimate only, at ${projected.secsPerCall}s per call. Review and `
      + 'repair are upper bounds, and speaker-attribution calls are not '
      + 'counted — how many scenes need one is known only after the prepass.';
  });

  canTranslate = computed(() => {
    if (this.files().length === 0) return false;
    if (this.runner.isTranslating()) return false;
    const preset = this.settings.currentPreset();
    if (preset.needsKey && !this.settings.apiKey()) return false;
    if (!this.settings.apiUrl()) return false;
    return true;
  });

  translateButtonLabel = computed(() => {
    const fileCount = this.files().length;
    if (fileCount === 0) return 'Translate subtitles';
    if (fileCount === 1) return `Translate to ${this.settings.targetLang()}`;
    return `Translate ${fileCount} files to ${this.settings.targetLang()}`;
  });

  translateHint = computed(() => {
    if (this.runner.isCancelling()) {
      return 'Stopping translation and cancelling in-flight requests...';
    }

    if (this.runner.isTranslating()) {
      return 'Translation is running. Progress is shown below.';
    }

    if (this.files().length === 0) {
      return 'Add subtitle files, then choose the provider and target language.';
    }

    if (this.settings.currentPreset().needsKey && !this.settings.apiKey()) {
      return `Enter your ${this.settings.currentPreset().label} API key to continue.`;
    }

    if (!this.settings.apiUrl()) {
      return 'Choose a provider or enter an API URL to continue.';
    }

    return `Ready to translate ${this.files().length} file${this.files().length > 1 ? 's' : ''} to ${this.settings.targetLang()}.`;
  });

  // Translations live only in memory, so leaving the page throws them away.
  hasUnsavedWork = computed(
    () =>
      this.runner.isTranslating() ||
      this.runner.doneFiles().some((f) => !!f.content && !this.downloaded().has(f.outputName)),
  );

  private onSystemThemeChange = (event: MediaQueryListEvent) => {
    this.systemPrefersDark.set(event.matches);
  };

  private onBeforeUnload = (event: BeforeUnloadEvent) => {
    if (!this.hasUnsavedWork()) return;
    event.preventDefault();
    // Older browsers only show the prompt when returnValue is set.
    event.returnValue = '';
  };

  constructor() {
    this.initTheme();

    if (typeof window !== 'undefined') {
      window.addEventListener('beforeunload', this.onBeforeUnload);
    }

    this.runner.connect({
      files: this.files,
      outputName: (name) => this.makeOutputName(name),
      request: () => ({
        provider: {
          apiUrl: this.settings.apiUrl(),
          apiKey: this.settings.apiKey(),
          model: this.settings.modelName(),
        },
        sourceLang: this.settings.sourceLang(),
        targetLang: this.settings.targetLang(),
        batchSize: this.settings.batchSize(),
        concurrency: this.settings.concurrency(),
        maxRetries: this.settings.maxRetries(),
        parallelFiles: this.settings.parallelFiles(),
        quality: {
          contextOverlap: this.settings.contextOverlap(),
          scanBudget: this.settings.scanBudget(),
          refineAttribution: this.settings.refineAttribution(),
          review: this.settings.review(),
          reflow: this.settings.reflow(),
          fixFlagged: this.settings.fixFlagged(),
          verifyAdequacy: this.settings.verifyAdequacy(),
          fullAttribution: this.settings.fullAttribution(),
          sendTemperature: this.settings.sendTemperature(),
          formality: this.settings.formality(),
          dialect: this.settings.dialect(),
          maxLineChars: this.settings.maxLineChars(),
          reviewApiUrl: this.settings.reviewApiUrl(),
          reviewApiKey: this.settings.reviewApiKey(),
          reviewModel: this.settings.reviewModel(),
        },
      }),
    });

    effect(() => this.applyTheme());
    effect(() => this.settings.persist());
  }

  ngOnDestroy() {
    this.runner.destroy();
    if (typeof window !== 'undefined') {
      window.removeEventListener('beforeunload', this.onBeforeUnload);
    }
    this.themeMedia?.removeEventListener('change', this.onSystemThemeChange);
  }

  private initTheme() {
    if (typeof window !== 'undefined') {
      this.themeMedia = window.matchMedia?.('(prefers-color-scheme: dark)') ?? null;
      if (this.themeMedia) {
        this.systemPrefersDark.set(this.themeMedia.matches);
        this.themeMedia.addEventListener('change', this.onSystemThemeChange);
      }
    }

    this.applyTheme();
  }

  toggleTheme() {
    this.settings.themePreference.set(this.theme() === 'dark' ? 'light' : 'dark');
  }

  private applyTheme() {
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', this.theme());
    }
  }

  onProviderTypeChange(type: string) {
    this.settings.onProviderTypeChange(type);
    this.showAdvanced.set(false);
  }

  onFilesAdded(incoming: UploadedFile[]) {
    this.files.update((current) => [...current, ...incoming]);
    this.clearRunState(false);
  }

  removeFile(index: number) {
    this.files.update((f) => f.filter((_, i) => i !== index));
    this.runner.removeAt(index);
    this.errorMessage.set('');
  }

  startTranslation() {
    if (!this.canTranslate()) return;

    this.errorMessage.set('');
    this.downloaded.set(new Set<string>());
    this.runner.start();
  }

  retryFailed() {
    if (!this.runner.canRetryFailed()) return;

    this.errorMessage.set('');
    this.runner.retryFailed();
  }

  markDownloaded(names: string[]) {
    this.downloaded.update((seen) => new Set([...seen, ...names]));
  }

  reset() {
    this.files.set([]);
    this.clearRunState(true);
  }

  private clearRunState(clearError: boolean) {
    this.runner.clear();
    this.downloaded.set(new Set<string>());
    if (clearError) {
      this.errorMessage.set('');
    }
  }

  makeOutputName(name: string): string {
    const code = this.targetLangCode();
    const m = name.match(/^(.*)(\.[^.]+)$/);
    return m ? `${m[1]}.${code}${m[2]}` : `${name}.${code}`;
  }

  targetLangCode(): string {
    const name = this.settings.targetLang();
    return this.languages.find((l) => l.name === name)?.code
      ?? name.toLowerCase().slice(0, 2);
  }
}
