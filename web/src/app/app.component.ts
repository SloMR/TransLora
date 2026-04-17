import { Component, signal, computed, OnDestroy } from '@angular/core';
import { FormsModule } from '@angular/forms';
import JSZip from 'jszip';
import {
  TranslationService,
  ProviderConfig,
  DEFAULT_MAX_RETRIES,
  DEFAULT_BATCH_SIZE,
  DEFAULT_CONCURRENCY,
  DEFAULT_PARALLEL_FILES,
} from './core/translation.service';
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

// Defaults — kept in one place so "Reset defaults" is trivial
const DEFAULTS = {
  sourceLang: 'English',
  targetLang: 'Arabic',
  batchSize: DEFAULT_BATCH_SIZE,
  concurrency: DEFAULT_CONCURRENCY,
  parallelFiles: DEFAULT_PARALLEL_FILES,
  maxRetries: DEFAULT_MAX_RETRIES,
};

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent implements OnDestroy {
  subtitleAccept = SUBTITLE_ACCEPT;
  languages = LANGUAGES;
  providerKeys = PROVIDER_KEYS;
  presets = PROVIDER_PRESETS;

  // Languages
  sourceLang = signal(DEFAULTS.sourceLang);
  targetLang = signal(DEFAULTS.targetLang);

  // Files
  files = signal<UploadedFile[]>([]);
  dragOver = signal(false);

  // Provider
  providerType = signal('custom');
  apiUrl = signal('');
  apiKey = signal('');
  modelName = signal('');
  showAdvanced = signal(false);
  concurrency = signal(DEFAULTS.concurrency);
  batchSize = signal(DEFAULTS.batchSize);
  parallelFiles = signal(DEFAULTS.parallelFiles);
  maxRetries = signal(DEFAULTS.maxRetries);

  // Theme
  theme = signal<'light' | 'dark'>('light');

  // Translation state
  isTranslating = signal(false);
  fileStatuses = signal<FileStatus[]>([]);
  errorMessage = signal('');
  isZipping = signal(false);

  private workQueue: number[] = [];
  private activeWorkers = 0;

  // Computed
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

  // Elapsed / avg / ETA timing lives in a dedicated helper.
  tracker = new TimeTracker(
    this.doneFiles,
    this.inProgressFiles,
    this.pendingCount,
    this.parallelFiles,
  );

  canTranslate = computed(() => {
    if (this.files().length === 0) return false;
    if (this.isTranslating()) return false;
    const preset = this.currentPreset();
    if (preset.needsKey && !this.apiKey()) return false;
    if (!this.apiUrl()) return false;
    return true;
  });

  canRetryFailed = computed(() => this.failedFiles().length > 0);

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
    this.tracker.destroy();
  }

  // --- Theme ---

  private initTheme() {
    const prefersDark =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-color-scheme: dark)').matches;
    this.setTheme(prefersDark ? 'dark' : 'light');
  }

  toggleTheme() {
    this.setTheme(this.theme() === 'dark' ? 'light' : 'dark');
  }

  private setTheme(next: 'light' | 'dark') {
    this.theme.set(next);
    document.documentElement.setAttribute('data-theme', next);
  }

  // --- Files ---

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
    this.fileStatuses.set([]);
    this.tracker.reset();
  }

  private formatRejected(rejected: Array<{ name: string; reason: string }>): string {
    const details = rejected.map((r) => `${r.name} (${r.reason})`).join('; ');
    return `Skipped: ${details}.`;
  }

  removeFile(index: number) {
    this.files.update((f) => f.filter((_, i) => i !== index));
  }

  private readFile(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(new Error('Failed to read file'));
      reader.readAsText(file);
    });
  }

  // --- Provider ---

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
  }

  // --- Translation ---

  startTranslation() {
    if (!this.canTranslate()) return;

    this.errorMessage.set('');

    // Initialize statuses (fresh run)
    this.fileStatuses.set(
      this.files().map((f) => ({
        name: f.name,
        outputName: this.makeOutputName(f.name),
        blockCount: f.blockCount,
        status: 'pending' as const,
      }))
    );

    this.enqueue(this.files().map((_, i) => i));
  }

  retryFailed() {
    if (!this.canRetryFailed()) return;

    this.errorMessage.set('');

    // Reset failed files to pending and collect their indices.
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

    this.enqueue(retryIndices);
  }

  /**
   * Push indices onto the shared work queue and make sure enough workers are
   * running. Safe to call while a previous run is still in flight — new items
   * are picked up by idle workers, or fresh ones are spawned up to
   * `parallelFiles()`.
   */
  private enqueue(indices: number[]) {
    if (indices.length === 0) return;

    const wasIdle = this.activeWorkers === 0;
    this.workQueue.push(...indices);

    if (wasIdle) {
      this.tracker.begin();
      this.isTranslating.set(true);
    }

    const provider: ProviderConfig = {
      apiUrl: this.apiUrl(),
      apiKey: this.apiKey(),
      model: this.modelName(),
    };

    const desired = Math.min(this.parallelFiles(), this.workQueue.length + this.activeWorkers);
    while (this.activeWorkers < desired) {
      this.spawnWorker(provider);
    }
  }

  private spawnWorker(provider: ProviderConfig) {
    this.activeWorkers++;
    void (async () => {
      try {
        while (this.workQueue.length > 0) {
          const idx = this.workQueue.shift()!;
          await this.translateOne(idx, provider);
        }
      } finally {
        this.activeWorkers--;
        if (this.activeWorkers === 0 && this.workQueue.length === 0) {
          this.tracker.finish();
          this.isTranslating.set(false);
        }
      }
    })();
  }

  private async translateOne(idx: number, provider: ProviderConfig) {
    const f = this.files()[idx];
    const fileStart = performance.now();

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
          this.updateFileStatus(idx, {
            currentBatch: progress.currentBatch,
            totalBatches: progress.totalBatches,
          });
        }
      );

      this.updateFileStatus(idx, {
        status: 'done',
        content,
        timeMs: performance.now() - fileStart,
      });
    } catch (err: any) {
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

  // --- Downloads ---

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

  // --- Reset ---

  reset() {
    this.files.set([]);
    this.fileStatuses.set([]);
    this.errorMessage.set('');
    this.tracker.reset();
  }

  // --- Helpers ---

  makeOutputName(name: string): string {
    const code = this.targetLangCode();
    const m = name.match(/^(.*)(\.[^.]+)$/);
    return m ? `${m[1]}.${code}${m[2]}` : `${name}.${code}`;
  }

  /** Look up the 2-letter code for the current target language. */
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
