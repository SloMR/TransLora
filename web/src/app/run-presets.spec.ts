import {
  DEFAULT_CONTEXT_OVERLAP,
  DEFAULT_FIX_FLAGGED,
  DEFAULT_REVIEW,
} from './core/constants';
import {
  QUALITY_PRESETS,
  QUALITY_PRESET_KEYS,
  QualityKnobs,
  matchQualityPreset,
} from './run-presets';
import { RunSettings } from './run-settings';

function knobsOf(settings: RunSettings): QualityKnobs {
  return {
    review: settings.review(),
    refineAttribution: settings.refineAttribution(),
    fixFlagged: settings.fixFlagged(),
    verifyAdequacy: settings.verifyAdequacy(),
    fullAttribution: settings.fullAttribution(),
    contextOverlap: settings.contextOverlap(),
  };
}

describe('quality presets', () => {
  it('names each preset back from its own knobs', () => {
    for (const key of QUALITY_PRESET_KEYS) {
      expect(matchQualityPreset(QUALITY_PRESETS[key])).toBe(key);
    }
  });

  it('keeps Balanced equal to the pipeline constants, and opens the web on Best', () => {
    expect(QUALITY_PRESETS.balanced.review).toBe(DEFAULT_REVIEW);
    expect(QUALITY_PRESETS.balanced.fixFlagged).toBe(DEFAULT_FIX_FLAGGED);
    expect(QUALITY_PRESETS.balanced.contextOverlap).toBe(DEFAULT_CONTEXT_OVERLAP);
    localStorage.clear();
    const fresh = new RunSettings();
    expect(fresh.qualityPreset()).toBe('best');
    expect(fresh.verifyAdequacy()).toBe(true);
    expect(fresh.fullAttribution()).toBe(true);
  });

  it('orders the presets by how much they spend', () => {
    const calls = (k: keyof typeof QUALITY_PRESETS) => {
      const p = QUALITY_PRESETS[k];
      return Number(p.review) + Number(p.fixFlagged) + Number(p.verifyAdequacy)
        + Number(p.refineAttribution) + Number(p.fullAttribution);
    };
    expect(calls('fast')).toBeLessThan(calls('balanced'));
    expect(calls('balanced')).toBeLessThan(calls('best'));
  });

  describe('on RunSettings', () => {
    let settings: RunSettings;

    beforeEach(() => {
      localStorage.clear();
      settings = new RunSettings();
    });

    afterEach(() => localStorage.clear());

    it('applies a preset knob for knob and reads it back', () => {
      settings.applyQualityPreset('fast');
      expect(knobsOf(settings)).toEqual({
        review: false,
        refineAttribution: false,
        fixFlagged: false,
        verifyAdequacy: false,
        fullAttribution: false,
        contextOverlap: 1,
      });
      expect(settings.qualityPreset()).toBe('fast');

      settings.applyQualityPreset('best');
      expect(settings.verifyAdequacy()).toBe(true);
      expect(settings.fullAttribution()).toBe(true);
      expect(settings.qualityPreset()).toBe('best');
    });

    it('leaves the knobs a preset does not own alone', () => {
      settings.setNumber('batchSize', 25);
      settings.setNumber('maxRetries', 9);
      settings.applyQualityPreset('fast');
      expect(settings.batchSize()).toBe(25);
      expect(settings.maxRetries()).toBe(9);
    });

    it('reads as Custom the moment one knob leaves the bundle, and returns when it comes back', () => {
      settings.applyQualityPreset('balanced');
      settings.verifyAdequacy.set(true);
      expect(settings.qualityPreset()).toBe('custom');
      settings.verifyAdequacy.set(false);
      expect(settings.qualityPreset()).toBe('balanced');
    });

    it('survives a reload through the knobs, not a stored name', () => {
      settings.applyQualityPreset('best');
      settings.persist();
      const reloaded = new RunSettings();
      expect(reloaded.qualityPreset()).toBe('best');
      expect(JSON.parse(localStorage.getItem('translora.settings')!)).not.toHaveProperty('qualityPreset');
    });
  });
});
