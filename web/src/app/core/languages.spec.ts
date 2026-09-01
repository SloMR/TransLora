// Ported from cli/tests/test_subtitle_norms.py. The values are the Netflix/EBU
// house figures, pinned literally in both suites because a silent change to one
// of them changes every translated file.

import {
  ARABIC_PUNCTUATION_SCRIPTS,
  DEFAULT_MAX_CHARS_PER_LINE,
  DEFAULT_MAX_LINES,
  DEFAULT_NORMS,
  DEFAULT_SCRIPT,
  GENERIC_VARIANT,
  GRAMMAR_NOTES,
  LANGUAGES,
  LANGUAGE_SCRIPTS,
  RTL_SCRIPTS,
  SCRIPT_NORMS,
  VARIANT_MARKERS,
  effectiveNorms,
  grammarNoteFor,
  normsFor,
  scriptFor,
} from './languages';

describe('the script table', () => {
  const table: [string, number, number, number][] = [
    ['latin', 42, 2, 17],
    ['cyrillic', 42, 2, 17],
    ['greek', 42, 2, 17],
    ['arabic', 42, 2, 20],
    ['hebrew', 42, 2, 20],
    ['devanagari', 42, 2, 17],
    ['thai', 42, 2, 17],
    ['han', 16, 2, 9],
    ['japanese', 16, 2, 4],
    ['korean', 20, 2, 12],
    ['default', 42, 2, 17],
  ];

  for (const [script, chars, lines, cps] of table) {
    it(`carries the industry values for ${script}`, () => {
      const norms = SCRIPT_NORMS[script]!;
      expect([norms.script, norms.maxCharsPerLine, norms.maxLines, norms.targetCps])
        .toEqual([script, chars, lines, cps]);
    });
  }

  it('holds exactly the eleven documented scripts', () => {
    expect(Object.keys(SCRIPT_NORMS).sort()).toEqual([
      'arabic', 'cyrillic', DEFAULT_SCRIPT, 'devanagari', 'greek', 'han',
      'hebrew', 'japanese', 'korean', 'latin', 'thai',
    ].sort());
  });

  it('takes its defaults from the default script', () => {
    expect(DEFAULT_NORMS).toBe(SCRIPT_NORMS[DEFAULT_SCRIPT]!);
    expect([DEFAULT_MAX_CHARS_PER_LINE, DEFAULT_MAX_LINES]).toEqual([42, 2]);
  });

  it('marks only Arabic and Hebrew right-to-left', () => {
    expect([...RTL_SCRIPTS].sort()).toEqual(['arabic', 'hebrew']);
    const flagged = Object.values(SCRIPT_NORMS).filter((n) => n.rtl).map((n) => n.script);
    expect(flagged.sort()).toEqual(['arabic', 'hebrew']);
  });

  it('keeps Hebrew RTL but on ASCII punctuation', () => {
    expect(SCRIPT_NORMS['hebrew']!.rtl).toBeTrue();
    expect([...ARABIC_PUNCTUATION_SCRIPTS]).toEqual(['arabic']);
  });
});

describe('scriptFor', () => {
  const languages: [string, string][] = [
    ['Arabic', 'arabic'],
    ['Japanese', 'japanese'],
    ['Chinese', 'han'],
    ['Korean', 'korean'],
    ['Russian', 'cyrillic'],
    ['Hindi', 'devanagari'],
    ['Spanish', 'latin'],
    ['Greek', 'greek'],
    ['Hebrew', 'hebrew'],
    ['Thai', 'thai'],
    ['Persian', 'arabic'],
    ['Urdu', 'arabic'],
    ['Yiddish', 'hebrew'],
    ['Serbian', 'cyrillic'],
    ['Vietnamese', 'latin'],
    // Written in a script with no row of its own.
    ['Tamil', DEFAULT_SCRIPT],
    ['Amharic', DEFAULT_SCRIPT],
  ];
  for (const [language, script] of languages) {
    it(`maps ${language} to its script`, () => {
      expect(scriptFor(language)).toBe(script);
    });
  }

  for (const language of ['Klingon', '', '   ', 'Made Up Tongue']) {
    it(`falls back instead of failing for ${JSON.stringify(language)}`, () => {
      expect(scriptFor(language)).toBe(DEFAULT_SCRIPT);
      expect(normsFor(language)).toBe(DEFAULT_NORMS);
    });
  }

  const variants: [string, string][] = [
    ['Brazilian Portuguese', 'latin'],
    ['Egyptian Arabic', 'arabic'],
    ['Simplified Chinese', 'han'],
    ['Traditional Chinese', 'han'],
    ['Latin American Spanish', 'latin'],
    ['Iranian Persian', 'arabic'],
  ];
  for (const [language, script] of variants) {
    it(`resolves ${language} through its base language`, () => {
      expect(scriptFor(language)).toBe(script);
    });
  }

  const endonyms: [string, string][] = [
    ['Farsi', 'arabic'],
    ['Mandarin', 'han'],
    ['Cantonese', 'han'],
    ['Filipino', 'latin'],
    ['Castilian', 'latin'],
    ['Flemish', 'latin'],
    ['Dari', 'arabic'],
    ['Myanmar', DEFAULT_SCRIPT],
  ];
  for (const [language, script] of endonyms) {
    it(`resolves the endonym ${language}`, () => {
      expect(scriptFor(language)).toBe(script);
    });
  }

  const typed = [
    'arabic', 'ARABIC', '  Arabic  ', 'Arabic!', 'Arabic (Egypt)',
    'Arabic, Modern Standard',
  ];
  for (const name of typed) {
    it(`normalises the typed name ${JSON.stringify(name)} before lookup`, () => {
      expect(scriptFor(name)).toBe('arabic');
    });
  }

  it('folds away a diacritic in a typed name', () => {
    expect(scriptFor('Norwegian Bokmål')).toBe('latin');
  });
});

describe('coverage of the shipped language list', () => {
  it('classifies every language the picker offers explicitly', () => {
    // Falling back to `default` is the safety net for a name we never listed,
    // not an acceptable answer for one of the 105 we ship in the picker.
    expect(LANGUAGES.length).toBeGreaterThanOrEqual(105);
    const unlisted = LANGUAGES
      .map((l) => l.name)
      .filter((name) => !(name.toLowerCase() in LANGUAGE_SCRIPTS));
    expect(unlisted).toEqual([]);
  });
});

describe('normsFor and effectiveNorms', () => {
  it('returns the whole record', () => {
    const norms = normsFor('Arabic');
    expect([
      norms.script, norms.maxCharsPerLine, norms.maxLines, norms.targetCps, norms.rtl,
    ]).toEqual(['arabic', 42, 2, 20, true]);
  });

  it('lets the user override replace only the line length', () => {
    const norms = effectiveNorms('Japanese', 24);
    expect(norms.maxCharsPerLine).toBe(24);
    expect([norms.script, norms.maxLines, norms.targetCps]).toEqual(['japanese', 2, 4]);
  });

  for (const override of [undefined, 0, -5]) {
    it(`keeps the script default for an override of ${override}`, () => {
      expect(effectiveNorms('Korean', override)).toBe(SCRIPT_NORMS['korean']!);
    });
  }

  it('hands back a copy so an override cannot leak between files', () => {
    const overridden = effectiveNorms('Arabic', 10);
    expect(overridden).not.toBe(SCRIPT_NORMS['arabic']!);
    expect(SCRIPT_NORMS['arabic']!.maxCharsPerLine).toBe(42);
    expect(normsFor('Arabic').maxCharsPerLine).toBe(42);
  });
});


// Drawn from the errors the graded English -> Arabic run actually made, so
// these strings are the contract, not decoration.
describe('per-script grammar notes', () => {
  const expected: [string, string][] = [
    ['arabic', 'After لم use the jussive (لم أقل, not لم أقول). Match gender and '
      + 'number to the referent, including the dual. Do not add diacritics.'],
    ['hebrew', 'Match gender and number to the referent, including the dual.'],
    ['japanese', 'Keep one politeness level throughout. Do not mix です/ます with '
      + 'plain form.'],
    ['korean', 'Keep one speech level throughout.'],
    ['han', 'Use the measure word the noun requires.'],
    ['cyrillic', 'Match case, gender and number across the clause.'],
    ['devanagari', 'Match gender and number, and keep one level of formality.'],
    ['latin', ''],
    ['greek', ''],
    ['thai', ''],
    [DEFAULT_SCRIPT, ''],
  ];

  for (const [script, note] of expected) {
    it(`carries the ${script} note verbatim`, () => {
      expect(GRAMMAR_NOTES[script]).toBe(note);
    });
  }

  it('covers every script the norms table knows', () => {
    expect(Object.keys(GRAMMAR_NOTES).sort())
      .toEqual(Object.keys(SCRIPT_NORMS).sort());
  });

  it('reads a note through the target language name', () => {
    expect(grammarNoteFor(scriptFor('Egyptian Arabic'))).toContain('jussive');
    expect(grammarNoteFor(scriptFor('French'))).toBe('');
  });

  it('answers with no note for a script it has never heard of', () => {
    expect(grammarNoteFor('klingon')).toBe('');
  });
});

describe('the variant marker table', () => {
  const arabic = VARIANT_MARKERS['arabic']!;

  it('carries the pan-dialectal bucket verbatim', () => {
    // Forms spoken across the Arabic-speaking world and written in none of its
    // standard prose. A run that drifts out of Modern Standard Arabic without
    // settling on Egyptian or Levantine reaches for exactly these, so the
    // named buckets alone declared such a file clean.
    expect(arabic['Colloquial']).toEqual([
      'مين', 'هذي', 'لسه', 'بس', 'عايز', 'مو', 'ليش', 'وين', 'كمان', 'شفت',
      'بدو', 'حاجة', 'طيب', 'يلا', 'لأ', 'ممكن', 'بعدين', 'إنت', 'إحنا',
      'شوية', 'لازم', 'صح', 'زي', 'دقايق', 'باين', 'جاية', 'تقدر', 'نقدر',
    ]);
  });

  it('keeps every marker to one bucket', () => {
    // detectVariantDrift picks its label from the per-bucket counts, so a
    // marker listed twice inflates two of them off one cue.
    const all = Object.values(arabic).flat();
    expect(all.length).toBe(new Set(all).size);
  });

  it('names a generic bucket that every script carrying markers has', () => {
    // Asking for a dialect mutes GENERIC_VARIANT by name; a name matching no
    // bucket would mute nothing and flag a correctly-dialectal file.
    for (const variants of Object.values(VARIANT_MARKERS)) {
      expect(Object.keys(variants)).toContain(GENERIC_VARIANT);
    }
  });

  it('lists markers no script-norm lookup would ever miss', () => {
    // A bucket under a script no target resolves to could never fire.
    for (const script of Object.keys(VARIANT_MARKERS)) {
      expect(Object.keys(SCRIPT_NORMS)).toContain(script);
    }
  });
});
