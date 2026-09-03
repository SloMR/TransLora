import {
  DEFAULT_CONTEXT_OVERLAP,
  DEFAULT_FIX_FLAGGED,
  DEFAULT_FULL_ATTRIBUTION,
  DEFAULT_REFINE_ATTRIBUTION,
  DEFAULT_REVIEW,
  DEFAULT_VERIFY_ADEQUACY,
} from './core/constants';

export type QualityPresetKey = 'fast' | 'balanced' | 'best';

/** The knobs a preset decides. Batch size, retries and the provider are not
 * quality choices and are left exactly as the user set them. */
export interface QualityKnobs {
  review: boolean;
  refineAttribution: boolean;
  fixFlagged: boolean;
  verifyAdequacy: boolean;
  fullAttribution: boolean;
  contextOverlap: number;
}

export interface QualityPreset extends QualityKnobs {
  label: string;
  /** One line under the pills: what this buys and what it costs. */
  summary: string;
}

// Balanced is the pipeline's own constants, the CLI's defaults. The web opens
// on Best and its Advanced "reset" lands there too: the pills make the cheaper
// bundles one click away, so the default can be the thorough one.
export const QUALITY_PRESETS: Record<QualityPresetKey, QualityPreset> = {
  fast: {
    label: 'Fast',
    summary: 'One pass per batch. No review, no repairs — about a third of the calls.',
    review: false,
    refineAttribution: false,
    fixFlagged: false,
    verifyAdequacy: false,
    fullAttribution: false,
    contextOverlap: 1,
  },
  balanced: {
    label: 'Balanced',
    summary: 'Review where the glossary can help, and one repair pass for what the detectors flag.',
    review: DEFAULT_REVIEW,
    refineAttribution: DEFAULT_REFINE_ATTRIBUTION,
    fixFlagged: DEFAULT_FIX_FLAGGED,
    verifyAdequacy: DEFAULT_VERIFY_ADEQUACY,
    fullAttribution: DEFAULT_FULL_ATTRIBUTION,
    contextOverlap: DEFAULT_CONTEXT_OVERLAP,
  },
  best: {
    label: 'Best',
    summary: 'Everything on: every scene attributed, and a back-translation check on a sample.',
    review: true,
    refineAttribution: true,
    fixFlagged: true,
    verifyAdequacy: true,
    fullAttribution: true,
    contextOverlap: 3,
  },
};

export const QUALITY_PRESET_KEYS: QualityPresetKey[] = ['fast', 'balanced', 'best'];

const KNOB_KEYS: (keyof QualityKnobs)[] = [
  'review', 'refineAttribution', 'fixFlagged', 'verifyAdequacy',
  'fullAttribution', 'contextOverlap',
];

/** The preset these knobs spell, or null when they match none — the Advanced
 * panel can always produce a mix no pill names. */
export function matchQualityPreset(knobs: QualityKnobs): QualityPresetKey | null {
  for (const key of QUALITY_PRESET_KEYS) {
    const preset = QUALITY_PRESETS[key];
    if (KNOB_KEYS.every((knob) => preset[knob] === knobs[knob])) return key;
  }
  return null;
}
