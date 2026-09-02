import {
  DEFAULT_BATCH_SIZE,
  DEFAULT_CONTEXT_OVERLAP,
  DEFAULT_DIALECT,
  DEFAULT_FIX_FLAGGED,
  DEFAULT_FORMALITY,
  DEFAULT_FULL_ATTRIBUTION,
  DEFAULT_MAX_RETRIES,
  DEFAULT_PARALLEL_FILES,
  DEFAULT_REFLOW,
  DEFAULT_SCAN_BUDGET,
  DEFAULT_SEND_TEMPERATURE,
  DEFAULT_VERIFY_ADEQUACY,
} from './core/constants';
import { RunSettings, STORAGE_KEY } from './run-settings';

describe('RunSettings', () => {
  let settings: RunSettings;

  beforeEach(() => {
    localStorage.clear();
    settings = new RunSettings();
  });

  afterEach(() => {
    localStorage.clear();
  });

  describe('numeric settings', () => {
    it('falls back to the default when the field is cleared', () => {
      settings.setNumber('batchSize', null);
      expect(settings.batchSize()).toBe(DEFAULT_BATCH_SIZE);

      settings.setNumber('parallelFiles', null);
      expect(settings.parallelFiles()).toBe(DEFAULT_PARALLEL_FILES);

      settings.setNumber('maxRetries', '');
      expect(settings.maxRetries()).toBe(DEFAULT_MAX_RETRIES);

      settings.setNumber('contextOverlap', undefined);
      expect(settings.contextOverlap()).toBe(DEFAULT_CONTEXT_OVERLAP);

      settings.setNumber('scanBudget', 'not a number');
      expect(settings.scanBudget()).toBe(DEFAULT_SCAN_BUDGET);
    });

    it('follows the preset rather than a fixed default for concurrency', () => {
      settings.onProviderTypeChange('openai');
      settings.setNumber('concurrency', null);
      expect(settings.concurrency()).toBe(settings.currentPreset().defaultConcurrency);
    });

    it('clamps zero and negatives up to the lowest value the pipeline survives', () => {
      // batchSize 0 would split into an endless stream of empty batches.
      settings.setNumber('batchSize', 0);
      expect(settings.batchSize()).toBe(3);
      settings.setNumber('batchSize', -20);
      expect(settings.batchSize()).toBe(3);

      // parallelFiles 0 would spawn no worker and leave the UI translating.
      settings.setNumber('parallelFiles', 0);
      expect(settings.parallelFiles()).toBe(1);
      settings.setNumber('parallelFiles', -1);
      expect(settings.parallelFiles()).toBe(1);

      settings.setNumber('concurrency', 0);
      expect(settings.concurrency()).toBe(1);
      settings.setNumber('maxRetries', 0);
      expect(settings.maxRetries()).toBe(1);

      // Zero overlap is legal; only negatives are clamped.
      settings.setNumber('contextOverlap', 0);
      expect(settings.contextOverlap()).toBe(0);
      settings.setNumber('contextOverlap', -3);
      expect(settings.contextOverlap()).toBe(0);
    });

    it('clamps above the ceiling and rounds fractions', () => {
      settings.setNumber('batchSize', 999);
      expect(settings.batchSize()).toBe(50);
      settings.setNumber('parallelFiles', 99);
      expect(settings.parallelFiles()).toBe(10);
      settings.setNumber('scanBudget', 10_000_000);
      expect(settings.scanBudget()).toBe(200_000);
      settings.setNumber('batchSize', 7.6);
      expect(settings.batchSize()).toBe(8);
    });
  });

  // The UI counterpart of the CLI's --formality / --dialect / --max-line-chars
  // / --no-reflow flags, checked in cli/tests/test_translora.py.
  describe('quality settings', () => {
    /** A second instance, built the way a reload would build it. */
    function restoredWith(stored: Record<string, unknown>, check: (settings: RunSettings) => void) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
      check(new RunSettings());
    }

    it('defaults to the shared constants', () => {
      expect(settings.reflow()).toBe(DEFAULT_REFLOW);
      expect(settings.fixFlagged()).toBe(DEFAULT_FIX_FLAGGED);
      expect(settings.verifyAdequacy()).toBe(DEFAULT_VERIFY_ADEQUACY);
      expect(settings.fullAttribution()).toBe(DEFAULT_FULL_ATTRIBUTION);
      expect(settings.formality()).toBe(DEFAULT_FORMALITY);
      expect(settings.dialect()).toBe(DEFAULT_DIALECT);
      // 0 means "whatever the target script's own norm is".
      expect(settings.maxLineChars()).toBe(0);
    });

    it('treats a cleared or negative line length as the script default', () => {
      settings.setNumber('maxLineChars', 38);
      expect(settings.maxLineChars()).toBe(38);

      settings.setNumber('maxLineChars', null);
      expect(settings.maxLineChars()).toBe(0);
      settings.setNumber('maxLineChars', -5);
      expect(settings.maxLineChars()).toBe(0);
      settings.setNumber('maxLineChars', 999);
      expect(settings.maxLineChars()).toBe(90);
    });

    it('keeps a dialect as free text but caps its length', () => {
      settings.setDialect('Egyptian Arabic');
      expect(settings.dialect()).toBe('Egyptian Arabic');

      settings.setDialect('x'.repeat(200));
      expect(settings.dialect().length).toBe(settings.dialectMaxLength);
    });

    it('restores the quality settings a reload finds stored', () => {
      restoredWith(
        { formality: 'informal', dialect: 'Brazilian Portuguese', maxLineChars: 38, reflow: false },
        (restored) => {
          expect(restored.formality()).toBe('informal');
          expect(restored.dialect()).toBe('Brazilian Portuguese');
          expect(restored.maxLineChars()).toBe(38);
          expect(restored.reflow()).toBe(false);
        },
      );
    });

    it('restores the call-cost toggles a reload finds stored', () => {
      restoredWith(
        { fixFlagged: false, verifyAdequacy: true, fullAttribution: true },
        (restored) => {
          expect(restored.fixFlagged()).toBe(false);
          expect(restored.verifyAdequacy()).toBe(true);
          expect(restored.fullAttribution()).toBe(true);
        },
      );
    });

    it('ignores call-cost toggles stored as anything but a boolean', () => {
      restoredWith(
        {
          fixFlagged: 'no', verifyAdequacy: 1, fullAttribution: null,
          sendTemperature: 'off',
        },
        (restored) => {
          expect(restored.fixFlagged()).toBe(DEFAULT_FIX_FLAGGED);
          expect(restored.verifyAdequacy()).toBe(DEFAULT_VERIFY_ADEQUACY);
          expect(restored.fullAttribution()).toBe(DEFAULT_FULL_ATTRIBUTION);
          expect(restored.sendTemperature()).toBe(DEFAULT_SEND_TEMPERATURE);
        },
      );
    });

    // The UI counterpart of the CLI's --no-temperature flag.
    it('keeps the temperature opt-out across a reload, and resets with the rest', () => {
      expect(settings.sendTemperature()).toBe(DEFAULT_SEND_TEMPERATURE);

      settings.sendTemperature.set(false);
      settings.persist();
      expect(new RunSettings().sendTemperature()).toBe(false);

      settings.resetDefaults();
      expect(settings.sendTemperature()).toBe(DEFAULT_SEND_TEMPERATURE);
    });

    it('rejects stored values the pipeline would not accept', () => {
      // Stored settings are as untrusted as typed ones.
      restoredWith(
        { formality: 'chummy', dialect: 'y'.repeat(200), maxLineChars: 999, reflow: 'yes' },
        (restored) => {
          expect(restored.formality()).toBe(DEFAULT_FORMALITY);
          expect(restored.dialect().length).toBe(restored.dialectMaxLength);
          expect(restored.maxLineChars()).toBe(90);
          expect(restored.reflow()).toBe(DEFAULT_REFLOW);
        },
      );
    });

    it('puts all four back on reset', () => {
      settings.reflow.set(false);
      settings.formality.set('formal');
      settings.setDialect('Egyptian Arabic');
      settings.setNumber('maxLineChars', 38);

      settings.resetDefaults();

      expect(settings.reflow()).toBe(DEFAULT_REFLOW);
      expect(settings.formality()).toBe(DEFAULT_FORMALITY);
      expect(settings.dialect()).toBe(DEFAULT_DIALECT);
      expect(settings.maxLineChars()).toBe(0);
    });

    it('puts the call-cost toggles back on reset too', () => {
      settings.fixFlagged.set(false);
      settings.verifyAdequacy.set(true);
      settings.fullAttribution.set(true);

      settings.resetDefaults();

      expect(settings.fixFlagged()).toBe(DEFAULT_FIX_FLAGGED);
      expect(settings.verifyAdequacy()).toBe(DEFAULT_VERIFY_ADEQUACY);
      expect(settings.fullAttribution()).toBe(DEFAULT_FULL_ATTRIBUTION);
    });
  });

  // The UI counterpart of the CLI's --review-api-url / --review-api-key /
  // --review-model flags, and of the --dry-run line naming each pass's provider.
  describe('the review pass provider', () => {
    it('rides on the main provider until a field is overridden', () => {
      expect(settings.reviewApiUrl()).toBe('');
      expect(settings.reviewApiKey()).toBe('');
      expect(settings.reviewModel()).toBe('');
      expect(settings.reviewProviderLabel())
        .toBe('Review pass: the same provider as every other call.');
    });

    it('names the endpoint and model the review pass will use', () => {
      settings.reviewApiUrl.set('https://cloud.test/v1/chat/completions');
      settings.reviewModel.set('big-model');
      expect(settings.reviewProviderLabel())
        .toBe('Review pass: big-model at cloud.test.');
    });

    it('falls back to the main endpoint and model, field by field', () => {
      settings.apiUrl.set('http://localhost:1234/v1/chat/completions');
      settings.modelName.set('local-model');
      settings.reviewApiKey.set('sk-review-key-goes-here');
      expect(settings.reviewProviderLabel())
        .toBe('Review pass: the main endpoint and model, with its own key.');

      settings.reviewApiUrl.set('https://cloud.test/v1/chat/completions');
      expect(settings.reviewProviderLabel())
        .toBe('Review pass: local-model at cloud.test, with its own key.');
    });

    it('never spells the review key out', () => {
      settings.reviewApiKey.set('sk-review-key-goes-here');
      expect(settings.reviewProviderLabel()).not.toContain('sk-review');
    });

    it('persists the endpoint and the model, and never the key', () => {
      settings.reviewApiUrl.set('https://cloud.test/v1/chat/completions');
      settings.reviewModel.set('big-model');
      settings.reviewApiKey.set('sk-review-key-goes-here');
      settings.persist();

      expect(localStorage.getItem(STORAGE_KEY)).not.toContain('sk-review');

      const restored = new RunSettings();
      expect(restored.reviewApiUrl()).toBe('https://cloud.test/v1/chat/completions');
      expect(restored.reviewModel()).toBe('big-model');
      expect(restored.reviewApiKey()).toBe('');
    });

    it('puts all three back on reset', () => {
      settings.reviewApiUrl.set('https://cloud.test/v1/chat/completions');
      settings.reviewApiKey.set('sk-review-key-goes-here');
      settings.reviewModel.set('big-model');

      settings.resetDefaults();

      expect(settings.reviewApiUrl()).toBe('');
      expect(settings.reviewApiKey()).toBe('');
      expect(settings.reviewModel()).toBe('');
    });
  });

  // The UI counterpart of the CLI's --dry-run norms report.
  describe('target norms', () => {
    const labels: [string, string][] = [
      ['Arabic',
        'Arabic script: up to 42 characters on each of 2 lines, ~20 chars/sec, right-to-left.'],
      ['Japanese', 'Japanese script: up to 16 characters on each of 2 lines, ~4 chars/sec.'],
      ['Korean', 'Korean script: up to 20 characters on each of 2 lines, ~12 chars/sec.'],
      ['Klingon', 'Default norms: up to 42 characters on each of 2 lines, ~17 chars/sec.'],
    ];

    for (const [target, label] of labels) {
      it(`reports the norms it will enforce for ${target}`, () => {
        settings.targetLang.set(target);
        expect(settings.targetNormsLabel()).toBe(label);
      });
    }

    it('marks an overridden line length and keeps the script default visible', () => {
      settings.targetLang.set('Arabic');
      settings.setNumber('maxLineChars', 38);

      expect(settings.targetNormsLabel()).toContain('up to 38 characters (your override)');
      expect(settings.scriptDefaultChars()).toBe(42);
      expect(settings.targetNorms().maxCharsPerLine).toBe(38);
    });
  });
});
