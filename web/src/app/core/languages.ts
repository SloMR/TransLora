// Language metadata: the ISO codes the picker offers, and the per-script
// subtitle norms every target language resolves to.
//
// Norms are keyed by the script a language is written in rather than by the
// language itself, so an unlisted language still gets professional norms instead
// of the Latin ones. Values follow the Netflix/EBU house styles.

export interface LanguageOption {
  code: string; // ISO 639-1, used in output filenames (e.g. movie.ar.srt)
  name: string;
}

export const LANGUAGES: LanguageOption[] = [
  { code: 'af', name: 'Afrikaans' },
  { code: 'sq', name: 'Albanian' },
  { code: 'am', name: 'Amharic' },
  { code: 'ar', name: 'Arabic' },
  { code: 'hy', name: 'Armenian' },
  { code: 'az', name: 'Azerbaijani' },
  { code: 'eu', name: 'Basque' },
  { code: 'be', name: 'Belarusian' },
  { code: 'bn', name: 'Bengali' },
  { code: 'bs', name: 'Bosnian' },
  { code: 'bg', name: 'Bulgarian' },
  { code: 'my', name: 'Burmese' },
  { code: 'ca', name: 'Catalan' },
  { code: 'ny', name: 'Chichewa' },
  { code: 'zh', name: 'Chinese' },
  { code: 'co', name: 'Corsican' },
  { code: 'hr', name: 'Croatian' },
  { code: 'cs', name: 'Czech' },
  { code: 'da', name: 'Danish' },
  { code: 'nl', name: 'Dutch' },
  { code: 'en', name: 'English' },
  { code: 'eo', name: 'Esperanto' },
  { code: 'et', name: 'Estonian' },
  { code: 'fi', name: 'Finnish' },
  { code: 'fr', name: 'French' },
  { code: 'fy', name: 'Frisian' },
  { code: 'gl', name: 'Galician' },
  { code: 'ka', name: 'Georgian' },
  { code: 'de', name: 'German' },
  { code: 'el', name: 'Greek' },
  { code: 'gu', name: 'Gujarati' },
  { code: 'ht', name: 'Haitian Creole' },
  { code: 'ha', name: 'Hausa' },
  { code: 'he', name: 'Hebrew' },
  { code: 'hi', name: 'Hindi' },
  { code: 'hu', name: 'Hungarian' },
  { code: 'is', name: 'Icelandic' },
  { code: 'ig', name: 'Igbo' },
  { code: 'id', name: 'Indonesian' },
  { code: 'ga', name: 'Irish' },
  { code: 'it', name: 'Italian' },
  { code: 'ja', name: 'Japanese' },
  { code: 'jv', name: 'Javanese' },
  { code: 'kn', name: 'Kannada' },
  { code: 'kk', name: 'Kazakh' },
  { code: 'km', name: 'Khmer' },
  { code: 'rw', name: 'Kinyarwanda' },
  { code: 'ko', name: 'Korean' },
  { code: 'ku', name: 'Kurdish' },
  { code: 'ky', name: 'Kyrgyz' },
  { code: 'lo', name: 'Lao' },
  { code: 'la', name: 'Latin' },
  { code: 'lv', name: 'Latvian' },
  { code: 'lt', name: 'Lithuanian' },
  { code: 'lb', name: 'Luxembourgish' },
  { code: 'mk', name: 'Macedonian' },
  { code: 'mg', name: 'Malagasy' },
  { code: 'ms', name: 'Malay' },
  { code: 'ml', name: 'Malayalam' },
  { code: 'mt', name: 'Maltese' },
  { code: 'mi', name: 'Maori' },
  { code: 'mr', name: 'Marathi' },
  { code: 'mn', name: 'Mongolian' },
  { code: 'ne', name: 'Nepali' },
  { code: 'no', name: 'Norwegian' },
  { code: 'or', name: 'Odia' },
  { code: 'ps', name: 'Pashto' },
  { code: 'fa', name: 'Persian' },
  { code: 'pl', name: 'Polish' },
  { code: 'pt', name: 'Portuguese' },
  { code: 'pa', name: 'Punjabi' },
  { code: 'ro', name: 'Romanian' },
  { code: 'ru', name: 'Russian' },
  { code: 'sm', name: 'Samoan' },
  { code: 'gd', name: 'Scots Gaelic' },
  { code: 'sr', name: 'Serbian' },
  { code: 'st', name: 'Sesotho' },
  { code: 'sn', name: 'Shona' },
  { code: 'sd', name: 'Sindhi' },
  { code: 'si', name: 'Sinhala' },
  { code: 'sk', name: 'Slovak' },
  { code: 'sl', name: 'Slovenian' },
  { code: 'so', name: 'Somali' },
  { code: 'es', name: 'Spanish' },
  { code: 'su', name: 'Sundanese' },
  { code: 'sw', name: 'Swahili' },
  { code: 'sv', name: 'Swedish' },
  { code: 'tl', name: 'Tagalog' },
  { code: 'tg', name: 'Tajik' },
  { code: 'ta', name: 'Tamil' },
  { code: 'tt', name: 'Tatar' },
  { code: 'te', name: 'Telugu' },
  { code: 'th', name: 'Thai' },
  { code: 'tr', name: 'Turkish' },
  { code: 'tk', name: 'Turkmen' },
  { code: 'uk', name: 'Ukrainian' },
  { code: 'ur', name: 'Urdu' },
  { code: 'ug', name: 'Uyghur' },
  { code: 'uz', name: 'Uzbek' },
  { code: 'vi', name: 'Vietnamese' },
  { code: 'cy', name: 'Welsh' },
  { code: 'xh', name: 'Xhosa' },
  { code: 'yi', name: 'Yiddish' },
  { code: 'yo', name: 'Yoruba' },
  { code: 'zu', name: 'Zulu' },
];

export const DEFAULT_SCRIPT = 'default';

// Right-to-left scripts; only the Arabic one re-points ASCII punctuation.
export const RTL_SCRIPTS = new Set(['arabic', 'hebrew']);
export const ARABIC_PUNCTUATION_SCRIPTS = new Set(['arabic']);
// Scripts that write the sentence-final marks in their own fullwidth forms.
// Korean is not one of them: it takes the ASCII marks as they are.
export const CJK_PUNCTUATION_SCRIPTS = new Set(['han', 'japanese']);

export interface SubtitleNorms {
  script: string;
  maxCharsPerLine: number;
  maxLines: number;
  targetCps: number;
  rtl: boolean;
}

// script -> [max chars per line, max lines, target characters per second]
const SCRIPT_TABLE: Record<string, [number, number, number]> = {
  latin: [42, 2, 17],
  cyrillic: [42, 2, 17],
  greek: [42, 2, 17],
  arabic: [42, 2, 20],
  hebrew: [42, 2, 20],
  devanagari: [42, 2, 17],
  thai: [42, 2, 17],
  han: [16, 2, 9],
  japanese: [16, 2, 4],
  korean: [20, 2, 12],
  [DEFAULT_SCRIPT]: [42, 2, 17],
};

export const SCRIPT_NORMS: Record<string, SubtitleNorms> = Object.fromEntries(
  Object.entries(SCRIPT_TABLE).map(([name, [chars, lines, cps]]) => [
    name,
    {
      script: name,
      maxCharsPerLine: chars,
      maxLines: lines,
      targetCps: cps,
      rtl: RTL_SCRIPTS.has(name),
    },
  ]),
);

// Per-script reminders of the machine-translation errors that script actually
// shows, drawn from the graded run. Rendered into the review request only.
export const GRAMMAR_NOTES: Record<string, string> = {
  arabic: 'After لم use the jussive (لم أقل, not لم أقول). Match gender and number to the referent, including the dual. Do not add diacritics.',
  hebrew: 'Match gender and number to the referent, including the dual.',
  japanese: 'Keep one politeness level throughout. Do not mix です/ます with plain form.',
  korean: 'Keep one speech level throughout.',
  han: 'Use the measure word the noun requires.',
  cyrillic: 'Match case, gender and number across the clause.',
  devanagari: 'Match gender and number, and keep one level of formality.',
  latin: '',
  greek: '',
  thai: '',
  default: '',
};

/** The grammar reminder for a script, or '' when it has none. */
export function grammarNoteFor(script: string): string {
  return GRAMMAR_NOTES[script] ?? '';
}

// script -> variant -> function words that cannot occur in that script's
// standard written form. A file full of them is a different variant than the
// one that was asked for, however fluent it reads. Open by design: a script
// with no entry is never checked.
// `Colloquial` is the bucket for forms that are spoken everywhere and written
// nowhere: a file can drift out of the standard form without ever committing
// to a named dialect, and the named buckets miss it. Every marker was checked
// against seven graded Modern Standard Arabic translations of one file and
// fires on at most two of their 372 cues.
export const VARIANT_MARKERS: Record<string, Record<string, string[]>> = {
  arabic: {
    Egyptian: ['مش', 'ده', 'دي', 'اللي', 'كده', 'عشان', 'أيوه', 'بتاع',
      'دلوقتي', 'إزاي', 'إيه', 'ايه', 'فين', 'كويس', 'بجد', 'تاني'],
    Levantine: ['هيك', 'هلق', 'شو', 'كتير', 'منيح', 'هون', 'هاي'],
    Colloquial: ['مين', 'هذي', 'لسه', 'بس', 'عايز', 'مو', 'ليش', 'وين', 'كمان',
      'شفت', 'بدو', 'حاجة', 'طيب', 'يلا', 'لأ', 'ممكن', 'بعدين', 'إنت', 'إحنا',
      'شوية', 'لازم', 'صح', 'زي', 'دقايق', 'باين', 'جاية', 'تقدر', 'نقدر'],
  },
};

// The pan-dialectal bucket's name, kept out of the drift check's logic so the
// two trees cannot mute different buckets when a dialect was asked for.
export const GENERIC_VARIANT = 'Colloquial';

export const DEFAULT_NORMS = SCRIPT_NORMS[DEFAULT_SCRIPT];
export const DEFAULT_MAX_CHARS_PER_LINE = DEFAULT_NORMS.maxCharsPerLine;
export const DEFAULT_MAX_LINES = DEFAULT_NORMS.maxLines;

const LANGUAGES_BY_SCRIPT: Record<string, string[]> = {
  latin: [
    'afrikaans', 'albanian', 'azerbaijani', 'basque', 'bosnian', 'catalan',
    'chichewa', 'corsican', 'croatian', 'czech', 'danish', 'dutch',
    'english', 'esperanto', 'estonian', 'finnish', 'french', 'frisian',
    'galician', 'german', 'haitian creole', 'hausa', 'hungarian',
    'icelandic', 'igbo', 'indonesian', 'irish', 'italian', 'javanese',
    'kinyarwanda', 'latin', 'latvian', 'lithuanian', 'luxembourgish',
    'malagasy', 'malay', 'maltese', 'maori', 'norwegian', 'polish',
    'portuguese', 'romanian', 'samoan', 'scots gaelic', 'sesotho', 'shona',
    'slovak', 'slovenian', 'somali', 'spanish', 'sundanese', 'swahili',
    'swedish', 'tagalog', 'turkish', 'turkmen', 'uzbek', 'vietnamese',
    'welsh', 'xhosa', 'yoruba', 'zulu',
  ],
  cyrillic: [
    'belarusian', 'bulgarian', 'kazakh', 'kyrgyz', 'macedonian',
    'mongolian', 'russian', 'serbian', 'tajik', 'tatar', 'ukrainian',
  ],
  greek: ['greek'],
  arabic: ['arabic', 'kurdish', 'pashto', 'persian', 'sindhi', 'urdu', 'uyghur'],
  hebrew: ['hebrew', 'yiddish'],
  devanagari: ['hindi', 'marathi', 'nepali'],
  thai: ['thai'],
  han: ['chinese'],
  japanese: ['japanese'],
  korean: ['korean'],
  // Written in a script with no entry of its own; the default norms apply.
  [DEFAULT_SCRIPT]: [
    'amharic', 'armenian', 'bengali', 'burmese', 'georgian', 'gujarati',
    'kannada', 'khmer', 'lao', 'malayalam', 'odia', 'punjabi', 'sinhala',
    'tamil', 'telugu',
  ],
};

export const LANGUAGE_SCRIPTS: Record<string, string> = Object.fromEntries(
  Object.entries(LANGUAGES_BY_SCRIPT).flatMap(([script, languages]) =>
    languages.map((language) => [language, script]),
  ),
);

// Endonyms and common spellings that never reach LANGUAGE_SCRIPTS on their own.
export const LANGUAGE_ALIASES: Record<string, string> = {
  bokmal: 'norwegian',
  cantonese: 'chinese',
  castilian: 'spanish',
  dari: 'persian',
  farsi: 'persian',
  filipino: 'tagalog',
  flemish: 'dutch',
  kirghiz: 'kyrgyz',
  mandarin: 'chinese',
  moldovan: 'romanian',
  myanmar: 'burmese',
  nynorsk: 'norwegian',
  oriya: 'odia',
  panjabi: 'punjabi',
  sinhalese: 'sinhala',
};

// Longest phrase first, so "Brazilian Portuguese" beats a stray one-word hit.
const PHRASES: [string[], string][] = [
  ...Object.entries(LANGUAGE_SCRIPTS),
  ...Object.entries(LANGUAGE_ALIASES).map(
    ([alias, target]) => [alias, LANGUAGE_SCRIPTS[target]] as [string, string],
  ),
]
  .map(([name, script]) => [name.split(' '), script] as [string[], string])
  .sort((a, b) => {
    if (a[0].length !== b[0].length) return b[0].length - a[0].length;
    // Code-unit order, the tie-break the Python side sorts tuples by.
    const [left, right] = [a[0].join('\0'), b[0].join('\0')];
    return left < right ? -1 : left > right ? 1 : 0;
  });

function normalize(name: string): string {
  return (name ?? '')
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/[^a-z]+/g, ' ')
    .trim();
}

/** The script `languageName` is written in, or DEFAULT_SCRIPT. */
export function scriptFor(languageName: string): string {
  const key = normalize(languageName);
  if (!key) return DEFAULT_SCRIPT;
  if (key in LANGUAGE_SCRIPTS) return LANGUAGE_SCRIPTS[key];
  if (key in LANGUAGE_ALIASES) return LANGUAGE_SCRIPTS[LANGUAGE_ALIASES[key]];
  const tokens = key.split(' ');
  for (const [phrase, script] of PHRASES) {
    for (let i = 0; i + phrase.length <= tokens.length; i++) {
      if (phrase.every((word, j) => tokens[i + j] === word)) return script;
    }
  }
  return DEFAULT_SCRIPT;
}

/** Subtitle norms for a target language name, e.g. "Brazilian Portuguese". */
export function normsFor(languageName: string): SubtitleNorms {
  return SCRIPT_NORMS[scriptFor(languageName)];
}

/** `normsFor` with the user's max-line-chars override applied. */
export function effectiveNorms(
  languageName: string, maxLineChars = 0,
): SubtitleNorms {
  const norms = normsFor(languageName);
  return maxLineChars > 0 ? { ...norms, maxCharsPerLine: maxLineChars } : norms;
}
