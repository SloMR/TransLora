import { computed, signal } from '@angular/core';
import { type Formality } from './core/translation.service';
import {
  DEFAULT_BATCH_SIZE,
  DEFAULT_CONTEXT_OVERLAP,
  DEFAULT_DIALECT,
  DEFAULT_FIX_FLAGGED,
  DEFAULT_FORMALITY,
  DEFAULT_FULL_ATTRIBUTION,
  DEFAULT_MAX_RETRIES,
  DEFAULT_PARALLEL_FILES,
  DEFAULT_REFINE_ATTRIBUTION,
  DEFAULT_REFLOW,
  DEFAULT_REVIEW,
  DEFAULT_SCAN_BUDGET,
  DEFAULT_SEND_TEMPERATURE,
  DEFAULT_VERIFY_ADEQUACY,
  FORMALITY_CHOICES,
} from './core/constants';
import { DEFAULT_SCRIPT, effectiveNorms, normsFor } from './core/languages';
import { PROVIDER_PRESETS, ProviderPreset } from './core/providers';

const DEFAULTS = {
  sourceLang: '',
  targetLang: 'Arabic',
  batchSize: DEFAULT_BATCH_SIZE,
  parallelFiles: DEFAULT_PARALLEL_FILES,
  maxRetries: DEFAULT_MAX_RETRIES,
  contextOverlap: DEFAULT_CONTEXT_OVERLAP,
  scanBudget: DEFAULT_SCAN_BUDGET,
  refineAttribution: DEFAULT_REFINE_ATTRIBUTION,
  review: DEFAULT_REVIEW,
  reflow: DEFAULT_REFLOW,
  fixFlagged: DEFAULT_FIX_FLAGGED,
  verifyAdequacy: DEFAULT_VERIFY_ADEQUACY,
  fullAttribution: DEFAULT_FULL_ATTRIBUTION,
  sendTemperature: DEFAULT_SEND_TEMPERATURE,
  formality: DEFAULT_FORMALITY as Formality,
  dialect: DEFAULT_DIALECT,
  maxLineChars: 0,
  // Blank = the review pass rides on the main provider, field by field.
  reviewApiUrl: '',
  reviewModel: '',
};

// A free-text variant goes into every request; keep it a phrase, not an essay.
export const DIALECT_MAX_LEN = 60;

// Concurrency has no standalone default: it always follows the selected preset.
const DEFAULT_PROVIDER = 'custom';
const DEFAULT_PRESET = PROVIDER_PRESETS[DEFAULT_PROVIDER]!;

// Keyed by a plain string, so an unknown provider (a stale stored payload) reads back undefined.
export function presetFor(type: string): ProviderPreset {
  return PROVIDER_PRESETS[type] ?? DEFAULT_PRESET;
}

// The inputs' min/max is advisory only; these are the bounds the pipeline
// requires (batchSize 0 loops forever in splitBatches, concurrency 0 starts
// no worker and "succeeds" untranslated).
const LIMITS = {
  batchSize: { lo: 3, hi: 50 },
  concurrency: { lo: 1, hi: 20 },
  parallelFiles: { lo: 1, hi: 10 },
  maxRetries: { lo: 1, hi: 20 },
  contextOverlap: { lo: 0, hi: 20 },
  scanBudget: { lo: 2000, hi: 200_000 },
  // 0 keeps whatever the target script's own norm is.
  maxLineChars: { lo: 0, hi: 90 },
} as const;

export type NumericSetting = keyof typeof LIMITS;

export type ThemePreference = 'light' | 'dark' | 'system';

export const STORAGE_KEY = 'translora.settings';

// Neither API key is here: a key is never persisted.
interface StoredSettings {
  providerType?: string;
  apiUrl?: string;
  customApiUrl?: string;
  modelName?: string;
  sourceLang?: string;
  targetLang?: string;
  batchSize?: number;
  concurrency?: number;
  parallelFiles?: number;
  maxRetries?: number;
  contextOverlap?: number;
  scanBudget?: number;
  refineAttribution?: boolean;
  review?: boolean;
  reflow?: boolean;
  fixFlagged?: boolean;
  verifyAdequacy?: boolean;
  fullAttribution?: boolean;
  sendTemperature?: boolean;
  formality?: string;
  dialect?: string;
  maxLineChars?: number;
  reviewApiUrl?: string;
  reviewModel?: string;
  theme?: ThemePreference;
}

// Just the host, so a warning line never carries a path or a query string.
function hostOf(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

// Angular's number value accessor emits null for an empty field.
function clampNumber(value: unknown, lo: number, hi: number, fallback: number): number {
  if (value === null || value === undefined || value === '') return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(hi, Math.max(lo, Math.round(n)));
}

// Every value a run reads: the signals the forms bind to, the bounds they are
// clamped to, and the localStorage round-trip. Owned by AppComponent, which
// hands the individual signals to the forms.
export class RunSettings {
  sourceLang = signal(DEFAULTS.sourceLang);
  targetLang = signal(DEFAULTS.targetLang);

  providerType = signal(DEFAULT_PROVIDER);
  apiUrl = signal(DEFAULT_PRESET.apiUrl);
  apiKey = signal('');
  modelName = signal(DEFAULT_PRESET.defaultModel);
  concurrency = signal(DEFAULT_PRESET.defaultConcurrency);
  batchSize = signal(DEFAULTS.batchSize);
  parallelFiles = signal(DEFAULTS.parallelFiles);
  maxRetries = signal(DEFAULTS.maxRetries);
  contextOverlap = signal(DEFAULTS.contextOverlap);
  scanBudget = signal(DEFAULTS.scanBudget);
  refineAttribution = signal(DEFAULTS.refineAttribution);
  review = signal(DEFAULTS.review);
  reflow = signal(DEFAULTS.reflow);
  fixFlagged = signal(DEFAULTS.fixFlagged);
  verifyAdequacy = signal(DEFAULTS.verifyAdequacy);
  fullAttribution = signal(DEFAULTS.fullAttribution);
  // Off = never send `temperature`; a provider that rejects it teaches the run
  // the same thing, one corrective round trip later.
  sendTemperature = signal(DEFAULTS.sendTemperature);
  formality = signal<Formality>(DEFAULTS.formality);
  dialect = signal(DEFAULTS.dialect);
  maxLineChars = signal(DEFAULTS.maxLineChars);
  // The review pass's own provider; each blank field falls back to the main one.
  reviewApiUrl = signal(DEFAULTS.reviewApiUrl);
  reviewApiKey = signal('');
  reviewModel = signal(DEFAULTS.reviewModel);
  dialectMaxLength = DIALECT_MAX_LEN;

  // Persisted alongside the rest; the shell resolves it against the OS setting.
  themePreference = signal<ThemePreference>('system');

  // What the user typed for the custom provider, kept across preset switches.
  private customApiUrl = '';
  private customApiKey = '';

  currentPreset = computed(() => presetFor(this.providerType()));

  // What the reflow pass will enforce for the chosen target, override included.
  targetNorms = computed(() => effectiveNorms(this.targetLang(), this.maxLineChars()));

  scriptDefaultChars = computed(() => normsFor(this.targetLang()).maxCharsPerLine);

  targetNormsLabel = computed(() => {
    const norms = this.targetNorms();
    const script = norms.script === DEFAULT_SCRIPT
      ? 'Default norms'
      : `${norms.script[0]!.toUpperCase()}${norms.script.slice(1)} script`;
    const override = this.maxLineChars() > 0 ? ' (your override)' : '';
    const direction = norms.rtl ? ', right-to-left' : '';
    return `${script}: up to ${norms.maxCharsPerLine} characters${override} on each of `
      + `${norms.maxLines} lines, ~${norms.targetCps} chars/sec${direction}.`;
  });

  // The web counterpart of the CLI's --dry-run line naming each pass's
  // provider. Never names the key, only whether one of its own is set.
  reviewProviderLabel = computed(() => {
    const url = this.reviewApiUrl().trim();
    const model = this.reviewModel().trim();
    const ownKey = this.reviewApiKey().trim() !== '';
    if (!url && !model) {
      return ownKey
        ? 'Review pass: the main endpoint and model, with its own key.'
        : 'Review pass: the same provider as every other call.';
    }
    const where = hostOf(url || this.apiUrl());
    const which = model || this.modelName() || "the endpoint's default model";
    return `Review pass: ${which} at ${where}${ownKey ? ', with its own key' : ''}.`;
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

  constructor() {
    const stored = this.readStoredSettings();
    if (stored) {
      if (stored.theme === 'light' || stored.theme === 'dark') {
        this.themePreference.set(stored.theme);
      }
      this.applyStoredSettings(stored);
    }
  }

  private readStoredSettings(): StoredSettings | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed: unknown = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? (parsed as StoredSettings) : null;
    } catch {
      return null;
    }
  }

  private applyStoredSettings(stored: StoredSettings) {
    if (typeof stored.providerType === 'string' && PROVIDER_PRESETS[stored.providerType]) {
      this.providerType.set(stored.providerType);
    }
    const preset = this.currentPreset();
    this.apiUrl.set(typeof stored.apiUrl === 'string' ? stored.apiUrl : preset.apiUrl);
    this.modelName.set(
      typeof stored.modelName === 'string' ? stored.modelName : preset.defaultModel,
    );
    if (typeof stored.customApiUrl === 'string') this.customApiUrl = stored.customApiUrl;
    if (typeof stored.sourceLang === 'string') this.sourceLang.set(stored.sourceLang);
    if (typeof stored.targetLang === 'string') this.targetLang.set(stored.targetLang);
    if (typeof stored.refineAttribution === 'boolean') {
      this.refineAttribution.set(stored.refineAttribution);
    }
    if (typeof stored.review === 'boolean') this.review.set(stored.review);
    if (typeof stored.reflow === 'boolean') this.reflow.set(stored.reflow);
    if (typeof stored.fixFlagged === 'boolean') this.fixFlagged.set(stored.fixFlagged);
    if (typeof stored.verifyAdequacy === 'boolean') {
      this.verifyAdequacy.set(stored.verifyAdequacy);
    }
    if (typeof stored.fullAttribution === 'boolean') {
      this.fullAttribution.set(stored.fullAttribution);
    }
    if (typeof stored.sendTemperature === 'boolean') {
      this.sendTemperature.set(stored.sendTemperature);
    }
    if ((FORMALITY_CHOICES as readonly string[]).includes(stored.formality ?? '')) {
      this.formality.set(stored.formality as Formality);
    }
    if (typeof stored.dialect === 'string') {
      this.dialect.set(stored.dialect.slice(0, DIALECT_MAX_LEN));
    }
    if (typeof stored.reviewApiUrl === 'string') this.reviewApiUrl.set(stored.reviewApiUrl);
    if (typeof stored.reviewModel === 'string') this.reviewModel.set(stored.reviewModel);

    // Clamped on the way in: stored values are as untrusted as typed ones.
    this.setNumber('batchSize', stored.batchSize);
    this.setNumber('concurrency', stored.concurrency);
    this.setNumber('parallelFiles', stored.parallelFiles);
    this.setNumber('maxRetries', stored.maxRetries);
    this.setNumber('contextOverlap', stored.contextOverlap);
    this.setNumber('scanBudget', stored.scanBudget);
    this.setNumber('maxLineChars', stored.maxLineChars);
  }

  persist() {
    const payload: StoredSettings = {
      providerType: this.providerType(),
      apiUrl: this.apiUrl(),
      customApiUrl: this.customApiUrl,
      modelName: this.modelName(),
      sourceLang: this.sourceLang(),
      targetLang: this.targetLang(),
      batchSize: this.batchSize(),
      concurrency: this.concurrency(),
      parallelFiles: this.parallelFiles(),
      maxRetries: this.maxRetries(),
      contextOverlap: this.contextOverlap(),
      scanBudget: this.scanBudget(),
      refineAttribution: this.refineAttribution(),
      review: this.review(),
      reflow: this.reflow(),
      fixFlagged: this.fixFlagged(),
      verifyAdequacy: this.verifyAdequacy(),
      fullAttribution: this.fullAttribution(),
      sendTemperature: this.sendTemperature(),
      formality: this.formality(),
      dialect: this.dialect(),
      maxLineChars: this.maxLineChars(),
      reviewApiUrl: this.reviewApiUrl(),
      reviewModel: this.reviewModel(),
      theme: this.themePreference(),
    };

    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // Private mode or full quota: settings just do not persist.
    }
  }

  onProviderTypeChange(type: string) {
    if (this.providerType() === 'custom') {
      // The custom preset has no endpoint of its own, so keep what was typed.
      this.customApiUrl = this.apiUrl();
      this.customApiKey = this.apiKey();
    }

    this.providerType.set(type);
    const preset = presetFor(type);
    if (type === 'custom') {
      this.apiUrl.set(this.customApiUrl);
      this.apiKey.set(this.customApiKey);
    } else {
      this.apiUrl.set(preset.apiUrl);
      this.apiKey.set('');
    }
    this.modelName.set(preset.defaultModel);
    this.concurrency.set(preset.defaultConcurrency);
  }

  setNumber(key: NumericSetting, value: unknown) {
    const { lo, hi } = LIMITS[key];
    const fallback =
      key === 'concurrency' ? this.currentPreset().defaultConcurrency : DEFAULTS[key];
    this[key].set(clampNumber(value, lo, hi, fallback));
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
    this.reflow.set(DEFAULTS.reflow);
    this.fixFlagged.set(DEFAULTS.fixFlagged);
    this.verifyAdequacy.set(DEFAULTS.verifyAdequacy);
    this.fullAttribution.set(DEFAULTS.fullAttribution);
    this.sendTemperature.set(DEFAULTS.sendTemperature);
    this.formality.set(DEFAULTS.formality);
    this.dialect.set(DEFAULTS.dialect);
    this.maxLineChars.set(DEFAULTS.maxLineChars);
    this.reviewApiUrl.set(DEFAULTS.reviewApiUrl);
    this.reviewApiKey.set('');
    this.reviewModel.set(DEFAULTS.reviewModel);
  }

  setDialect(value: string) {
    this.dialect.set((value ?? '').slice(0, DIALECT_MAX_LEN));
  }

  swapLanguages() {
    const source = this.sourceLang();
    if (!source) return;
    this.sourceLang.set(this.targetLang());
    this.targetLang.set(source);
  }
}
