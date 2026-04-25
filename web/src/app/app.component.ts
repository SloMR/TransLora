import { Component, signal, computed, OnDestroy } from '@angular/core';
import { FormsModule } from '@angular/forms';
import JSZip from 'jszip';
import {
  TranslationService,
  ProviderConfig,
  TranslationCancelledError,
} from './core/translation.service';
import {
  DEFAULT_BATCH_SIZE,
  DEFAULT_CONCURRENCY,
  DEFAULT_CONTEXT_OVERLAP,
  DEFAULT_MAX_RETRIES,
  DEFAULT_PARALLEL_FILES,
  DEFAULT_REFINE_ATTRIBUTION,
  DEFAULT_REVIEW,
  DEFAULT_SCAN_BUDGET,
} from './core/constants';
import { parseSubtitle } from './core/subtitle-formats';
import { LANGUAGES } from './core/languages';
import { PROVIDER_PRESETS, PROVIDER_KEYS } from './core/providers';
import {
  SUBTITLE_EXTS,
  SUBTITLE_ACCEPT,
  UploadedFile,
  FileStatus,
} from './core/file-types';
import { TimeTracker } from './core/time-tracker';

const DEFAULTS = {
  sourceLang: '',
  targetLang: 'Arabic',
  batchSize: DEFAULT_BATCH_SIZE,
  concurrency: DEFAULT_CONCURRENCY,
  parallelFiles: DEFAULT_PARALLEL_FILES,
  maxRetries: DEFAULT_MAX_RETRIES,
  contextOverlap: DEFAULT_CONTEXT_OVERLAP,
  scanBudget: DEFAULT_SCAN_BUDGET,
  refineAttribution: DEFAULT_REFINE_ATTRIBUTION,
  review: DEFAULT_REVIEW,
};

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent implements OnDestroy {
  subtitleAccept = SUBTITLE_ACCEPT;
  supportedFormats = SUBTITLE_EXTS.map((ext) => ext.slice(1).toUpperCase());
  languages = LANGUAGES;
  providerKeys = PROVIDER_KEYS;
  presets = PROVIDER_PRESETS;

  sourceLang = signal(DEFAULTS.sourceLang);
  targetLang = signal(DEFAULTS.targetLang);

  files = signal<UploadedFile[]>([]);
  dragOver = signal(false);

  providerType = signal('custom');
  apiUrl = signal('');
  apiKey = signal('');
  modelName = signal('');
  showAdvanced = signal(false);
  concurrency = signal(DEFAULTS.concurrency);
  batchSize = signal(DEFAULTS.batchSize);
  parallelFiles = signal(DEFAULTS.parallelFiles);
  maxRetries = signal(DEFAULTS.maxRetries);
  contextOverlap = signal(DEFAULTS.contextOverlap);
  scanBudget = signal(DEFAULTS.scanBudget);
  refineAttribution = signal(DEFAULTS.refineAttribution);
  review = signal(DEFAULTS.review);

  theme = signal<'light' | 'dark'>('light');

  isTranslating = signal(false);
  isCancelling = signal(false);
  fileStatuses = signal<FileStatus[]>([]);
  errorMessage = signal('');
  isZipping = signal(false);

  private workQueue: number[] = [];
  private activeWorkers = 0;
  private runController: AbortController | null = null;
  private cancelRequested = false;

  currentPreset = computed(() => PROVIDER_PRESETS[this.providerType()]);

  totalBlocks = computed(() =>
    this.files().reduce((sum, f) => sum + f.blockCount, 0)
  );

  doneFiles = computed(() =>
    this.fileStatuses().filter((f) => f.status === 'done')
  );
  failedFiles = computed(() =>
    this.fileStatuses().filter((f) => f.status === 'failed')
  );
  inProgressFiles = computed(() =>
    this.fileStatuses().filter((f) => f.status === 'translating')
  );
  pendingCount = computed(
    () =>
      this.fileStatuses().filter((f) => f.status === 'pending').length
  );

  overallProgressPercent = computed(() => {
    const all = this.fileStatuses();
    if (all.length === 0) return 0;
    let sum = 0;
    for (const f of all) {
      if (f.status === 'done' || f.status === 'failed') {
        sum += 1;
      } else if (f.status === 'translating' && f.totalBatches) {
        sum += (f.currentBatch ?? 0) / f.totalBatches;
      }
    }
    return Math.round((sum / all.length) * 100);
  });

  tracker = new TimeTracker(
    this.doneFiles,
    this.inProgressFiles,
    this.pendingCount,
  );

  canTranslate = computed(() => {
    if (this.files().length === 0) return false;
    if (this.isTranslating()) return false;
    const preset = this.currentPreset();
    if (preset.needsKey && !this.apiKey()) return false;
    if (!this.apiUrl()) return false;
    return true;
  });

  canRetryFailed = computed(() => this.failedFiles().length > 0 && !this.isCancelling());

  translateButtonLabel = computed(() => {
    const fileCount = this.files().length;
    if (fileCount === 0) return 'Translate subtitles';
    if (fileCount === 1) return `Translate to ${this.targetLang()}`;
    return `Translate ${fileCount} files to ${this.targetLang()}`;
  });

  translateHint = computed(() => {
    if (this.isCancelling()) {
      return 'Stopping translation and cancelling in-flight requests...';
    }

    if (this.isTranslating()) {
      return 'Translation is running. Progress is shown below.';
    }

    if (this.files().length === 0) {
      return 'Add subtitle files, then choose the provider and target language.';
    }

    if (this.currentPreset().needsKey && !this.apiKey()) {
      return `Enter your ${this.currentPreset().label} API key to continue.`;
    }

    if (!this.apiUrl()) {
      return 'Choose a provider or enter an API URL to continue.';
    }

    return `Ready to translate ${this.files().length} file${this.files().length > 1 ? 's' : ''} to ${this.targetLang()}.`;
  });

  apiKeyWarning = computed<string | null>(() => {
    const raw = this.apiKey();
    if (!raw || !this.currentPreset().needsKey) return null;
    const k = raw.trim();
    if (k !== raw) return 'Key has leading or trailing whitespace.';
    if (/\s/.test(k)) return 'Key contains whitespace — it should be a single token.';
    if (
      (k.startsWith('"') && k.endsWith('"')) ||
      (k.startsWith("'") && k.endsWith("'"))
    ) {
      return 'Remove the surrounding quotes.';
    }
    if (/^bearer\s/i.test(k)) return 'Remove the "Bearer " prefix — just paste the key.';
    if (k.length < 20) return `Key looks too short (${k.length} chars).`;
    return null;
  });

  isDone = computed(
    () =>
      !this.isTranslating() &&
      (this.doneFiles().length > 0 || this.failedFiles().length > 0)
  );

  hasResults = computed(
    () =>
      this.isTranslating() ||
      this.doneFiles().length > 0 ||
      this.failedFiles().length > 0
  );

  constructor(private translationService: TranslationService) {
    this.initTheme();
    this.onProviderTypeChange('custom');
  }

  ngOnDestroy() {
    this.cancelRequested = true;
    this.workQueue = [];
    this.runController?.abort();
    this.tracker.destroy();
  }

  private initTheme() {
    if (typeof window === 'undefined') {
      this.setTheme('light');
      return;
    }

    const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
    this.setTheme(prefersDark ? 'dark' : 'light');
  }

  toggleTheme() {
    this.setTheme(this.theme() === 'dark' ? 'light' : 'dark');
  }

  private setTheme(next: 'light' | 'dark') {
    this.theme.set(next);
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', next);
    }
  }

  onDragOver(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.dragOver.set(true);
  }

  onDragLeave(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.dragOver.set(false);
  }

  onDrop(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.dragOver.set(false);
    const fileList = event.dataTransfer?.files;
    if (fileList) this.handleFiles(fileList);
  }

  onFileSelect(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files) {
      this.handleFiles(input.files);
      input.value = '';
    }
  }

  async handleFiles(fileList: FileList) {
    this.errorMessage.set('');
    const incoming: UploadedFile[] = [];
    const existingNames = new Set(this.files().map((f) => f.name));
    const rejected: Array<{ name: string; reason: string }> = [];

    for (const file of Array.from(fileList)) {
      const lower = file.name.toLowerCase();
      if (!SUBTITLE_EXTS.some((ext) => lower.endsWith(ext))) {
        rejected.push({ name: file.name, reason: 'unsupported extension' });
        continue;
      }
      if (existingNames.has(file.name)) continue;

      const content = await this.readFile(file);
      try {
        const doc = parseSubtitle(file.name, content);
        if (doc.blocks.length === 0) {
          rejected.push({ name: file.name, reason: 'no subtitle blocks found' });
          continue;
        }
        incoming.push({
          name: file.name,
          blockCount: doc.blocks.length,
          doc,
        });
      } catch (err: any) {
        rejected.push({
          name: file.name,
          reason: err?.message ?? 'could not be parsed',
        });
      }
    }

    if (incoming.length === 0 && this.files().length === 0) {
      this.errorMessage.set(
        rejected.length > 0
          ? this.formatRejected(rejected)
          : `Please select subtitle files (${SUBTITLE_EXTS.join(', ')}).`,
      );
      return;
    }

    if (rejected.length > 0) {
      this.errorMessage.set(this.formatRejected(rejected));
    }

    this.files.update((current) => [...current, ...incoming]);
    this.clearRunState(false);
  }

  private formatRejected(rejected: Array<{ name: string; reason: string }>): string {
    const details = rejected.map((r) => `${r.name} (${r.reason})`).join('; ');
    return `Skipped: ${details}.`;
  }

  removeFile(index: number) {
    this.files.update((f) => f.filter((_, i) => i !== index));
    this.clearRunState(true);
  }

  private readFile(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(new Error('Failed to read file'));
      reader.readAsText(file);
    });
  }

  onProviderTypeChange(type: string) {
    this.providerType.set(type);
    const preset = PROVIDER_PRESETS[type];
    this.apiUrl.set(preset.apiUrl);
    this.apiKey.set('');
    this.modelName.set(preset.defaultModel);
    this.concurrency.set(preset.defaultConcurrency);
    this.showAdvanced.set(false);
  }

  resetDefaults() {
    this.batchSize.set(DEFAULTS.batchSize);
    this.concurrency.set(this.currentPreset().defaultConcurrency);
    this.parallelFiles.set(DEFAULTS.parallelFiles);
    this.maxRetries.set(DEFAULTS.maxRetries);
    this.contextOverlap.set(DEFAULTS.contextOverlap);
    this.scanBudget.set(DEFAULTS.scanBudget);
    this.refineAttribution.set(DEFAULTS.refineAttribution);
    this.review.set(DEFAULTS.review);
  }

  swapLanguages() {
    const source = this.sourceLang();
    if (!source) return;
    this.sourceLang.set(this.targetLang());
    this.targetLang.set(source);
  }

  startTranslation() {
    if (!this.canTranslate()) return;

    this.errorMessage.set('');
    this.isCancelling.set(false);

    // Initialize statuses (fresh run)
    this.fileStatuses.set(
      this.files().map((f) => ({
        name: f.name,
        outputName: this.makeOutputName(f.name),
        blockCount: f.blockCount,
        status: 'pending' as const,
      }))
    );
    this.enqueue(this.files().map((_, i) => i), false);
  }

  retryFailed() {
    if (!this.canRetryFailed()) return;

    this.errorMessage.set('');
    this.isCancelling.set(false);

    const retryIndices: number[] = [];
    this.fileStatuses.update((statuses) =>
      statuses.map((s, i) => {
        if (s.status !== 'failed') return s;
        retryIndices.push(i);
        return {
          ...s,
          status: 'pending' as const,
          currentBatch: undefined,
          totalBatches: undefined,
          error: undefined,
          timeMs: undefined,
        };
      })
    );

    if (retryIndices.length === 0) return;

    this.enqueue(retryIndices, true);
  }

  // Pushes indices onto the shared queue and ensures enough workers are running.
  // Safe to call mid-run: idle workers pick up new items, or fresh ones spawn
  // up to parallelFiles().
  private enqueue(indices: number[], isRetry: boolean) {
    if (indices.length === 0) return;

    const wasIdle = this.activeWorkers === 0;
    this.workQueue.push(...indices);

    if (wasIdle) {
      this.cancelRequested = false;
      this.runController = new AbortController();
      if (isRetry) {
        this.tracker.resume();
      } else {
        this.tracker.begin();
      }
      this.isTranslating.set(true);
      this.isCancelling.set(false);
    }

    if (!this.runController) return;

    const provider: ProviderConfig = {
      apiUrl: this.apiUrl(),
      apiKey: this.apiKey(),
      model: this.modelName(),
    };

    const desired = Math.min(this.parallelFiles(), this.workQueue.length + this.activeWorkers);
    while (this.activeWorkers < desired) {
      this.spawnWorker(provider, this.runController.signal);
    }
  }

  cancelTranslation() {
    if (!this.isTranslating() || this.isCancelling()) return;

    this.cancelRequested = true;
    this.isCancelling.set(true);
    this.workQueue = [];
    this.runController?.abort();
  }

  private spawnWorker(provider: ProviderConfig, cancelSignal: AbortSignal) {
    this.activeWorkers++;
    void (async () => {
      try {
        while (this.workQueue.length > 0 && !cancelSignal.aborted) {
          const idx = this.workQueue.shift()!;
          await this.translateOne(idx, provider, cancelSignal);
        }
      } finally {
        this.activeWorkers--;
        if (this.activeWorkers === 0 && this.workQueue.length === 0) {
          const cancelled = this.cancelRequested || cancelSignal.aborted;
          this.runController = null;
          this.tracker.finish();

          if (cancelled) {
            this.cancelRequested = false;
            // Mark still-pending/translating entries as failed so they remain
            // retryable from the UI.
            this.fileStatuses.update((arr) =>
              arr.map((s) =>
                s.status === 'pending' || s.status === 'translating'
                  ? {
                      ...s,
                      status: 'failed' as const,
                      error: 'Cancelled',
                      currentBatch: undefined,
                      totalBatches: undefined,
                    }
                  : s,
              ),
            );
          }

          this.isCancelling.set(false);
          this.isTranslating.set(false);
        }
      }
    })();
  }

  private async translateOne(idx: number, provider: ProviderConfig, cancelSignal: AbortSignal) {
    const f = this.files()[idx];
    const fileStart = performance.now();

    if (cancelSignal.aborted || this.cancelRequested) return;
    this.updateFileStatus(idx, { status: 'translating' });

    try {
      const content = await this.translationService.translateDocument(
        f.doc,
        this.sourceLang(),
        this.targetLang(),
        provider,
        this.batchSize(),
        this.concurrency(),
        this.maxRetries(),
        (progress) => {
          if (cancelSignal.aborted || this.cancelRequested) return;
          this.updateFileStatus(idx, {
            currentBatch: progress.currentBatch,
            totalBatches: progress.totalBatches,
          });
        },
        cancelSignal,
        {
          contextOverlap: this.contextOverlap(),
          scanBudget: this.scanBudget(),
          refineAttribution: this.refineAttribution(),
          review: this.review(),
        },
      );

      if (cancelSignal.aborted || this.cancelRequested) return;
      this.updateFileStatus(idx, {
        status: 'done',
        content,
        timeMs: performance.now() - fileStart,
      });
    } catch (err: any) {
      if (
        err instanceof TranslationCancelledError ||
        cancelSignal.aborted ||
        this.cancelRequested
      ) {
        return;
      }

      this.updateFileStatus(idx, {
        status: 'failed',
        error: err?.message ?? 'Translation failed',
        timeMs: performance.now() - fileStart,
      });
    }
  }

  private updateFileStatus(idx: number, update: Partial<FileStatus>) {
    this.fileStatuses.update((arr) => {
      const next = [...arr];
      next[idx] = { ...next[idx], ...update };
      return next;
    });
  }

  downloadFile(f: FileStatus) {
    if (f.content) this.downloadBlob(f.content, f.outputName);
  }

  async downloadAll() {
    const done = this.doneFiles();
    if (done.length === 0 || this.isZipping()) return;

    this.isZipping.set(true);
    try {
      const zip = new JSZip();
      for (const f of done) {
        if (f.content) zip.file(f.outputName, f.content);
      }
      const blob = await zip.generateAsync({
        type: 'blob',
        compression: 'DEFLATE',
        compressionOptions: { level: 6 },
      });
      this.downloadBlobRaw(blob, this.makeZipName());
    } finally {
      this.isZipping.set(false);
    }
  }

  private makeZipName(): string {
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`;
    return `translora-${this.targetLangCode()}-${stamp}.zip`;
  }

  private downloadBlob(content: string, filename: string) {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    this.downloadBlobRaw(blob, filename);
  }

  private downloadBlobRaw(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  reset() {
    this.files.set([]);
    this.clearRunState(true);
  }

  private clearRunState(clearError: boolean) {
    this.workQueue = [];
    this.fileStatuses.set([]);
    this.tracker.reset();
    if (clearError) {
      this.errorMessage.set('');
    }
  }

  makeOutputName(name: string): string {
    const code = this.targetLangCode();
    const m = name.match(/^(.*)(\.[^.]+)$/);
    return m ? `${m[1]}.${code}${m[2]}` : `${name}.${code}`;
  }

  private targetLangCode(): string {
    const name = this.targetLang();
    return this.languages.find((l) => l.name === name)?.code
      ?? name.toLowerCase().slice(0, 2);
  }

  fileProgressPercent(f: FileStatus): number {
    if (!f.totalBatches) return 0;
    return Math.round(((f.currentBatch ?? 0) / f.totalBatches) * 100);
  }
}
