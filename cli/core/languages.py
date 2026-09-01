"""Language metadata for a target language name: the script it is written in,
the subtitle norms that script demands, and the ISO 639-1 code used as the
output filename suffix.

Norms are keyed by script rather than by language, so an unlisted language
still gets professional values instead of the Latin ones. The figures follow
the Netflix/EBU house styles.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

DEFAULT_SCRIPT = "default"

# Right-to-left scripts; only the Arabic one re-points ASCII punctuation.
RTL_SCRIPTS = frozenset({"arabic", "hebrew"})
ARABIC_PUNCTUATION_SCRIPTS = frozenset({"arabic"})


@dataclass(frozen=True)
class SubtitleNorms:
    script: str
    max_chars_per_line: int
    max_lines: int
    target_cps: int
    rtl: bool


# script -> (max chars per line, max lines, target characters per second)
_SCRIPT_TABLE: dict[str, tuple[int, int, int]] = {
    "latin": (42, 2, 17),
    "cyrillic": (42, 2, 17),
    "greek": (42, 2, 17),
    "arabic": (42, 2, 20),
    "hebrew": (42, 2, 20),
    "devanagari": (42, 2, 17),
    "thai": (42, 2, 17),
    "han": (16, 2, 9),
    "japanese": (16, 2, 4),
    "korean": (20, 2, 12),
    DEFAULT_SCRIPT: (42, 2, 17),
}

SCRIPT_NORMS: dict[str, SubtitleNorms] = {
    name: SubtitleNorms(name, chars, lines, cps, name in RTL_SCRIPTS)
    for name, (chars, lines, cps) in _SCRIPT_TABLE.items()
}

# Per-script reminders of the machine-translation errors that script actually
# shows, drawn from the graded run. Rendered into the review request only.
GRAMMAR_NOTES: dict[str, str] = {
    "arabic": "After لم use the jussive (لم أقل, not لم أقول). Match gender "
              "and number to the referent, including the dual. Do not add "
              "diacritics.",
    "hebrew": "Match gender and number to the referent, including the dual.",
    "japanese": "Keep one politeness level throughout. Do not mix です/ます "
                "with plain form.",
    "korean": "Keep one speech level throughout.",
    "han": "Use the measure word the noun requires.",
    "cyrillic": "Match case, gender and number across the clause.",
    "devanagari": "Match gender and number, and keep one level of formality.",
    "latin": "",
    "greek": "",
    "thai": "",
    DEFAULT_SCRIPT: "",
}


def grammar_note_for(script: str) -> str:
    """The grammar reminder for a script, or "" when it has none."""
    return GRAMMAR_NOTES.get(script, "")


# script -> variant -> the function words that cannot occur in that script's
# standard written form. Matched as whole words, so a marker is evidence of the
# variant and not of a longer word that happens to contain it.
#
# The pan-dialectal bucket: forms that are spoken everywhere and written
# nowhere, so they belong to no named dialect. A file can drift out of the
# standard written form without ever committing to one, and the named buckets
# alone read that file as clean.
GENERIC_VARIANT = "Colloquial"

# Every marker here was checked against seven graded Modern Standard Arabic
# translations of the same file and fires on at most two of their 372 cues;
# candidates that read as standard prose — تمام, يعني, فعلا, لو — were dropped
# for firing on those clean files.
VARIANT_MARKERS: dict[str, dict[str, tuple[str, ...]]] = {
    "arabic": {
        "Egyptian": ("مش", "ده", "دي", "اللي", "كده", "عشان", "أيوه", "بتاع",
                     "دلوقتي", "إزاي", "إيه", "ايه", "فين", "كويس", "بجد",
                     "تاني"),
        "Levantine": ("هيك", "هلق", "شو", "كتير", "منيح", "هون", "هاي"),
        "Colloquial": ("مين", "هذي", "لسه", "بس", "عايز", "مو", "ليش", "وين",
                       "كمان", "شفت", "بدو", "حاجة", "طيب", "يلا", "لأ",
                       "ممكن", "بعدين", "إنت", "إحنا", "شوية", "لازم", "صح",
                       "زي", "دقايق", "باين", "جاية", "تقدر", "نقدر"),
    },
}


DEFAULT_NORMS = SCRIPT_NORMS[DEFAULT_SCRIPT]
DEFAULT_MAX_CHARS_PER_LINE = DEFAULT_NORMS.max_chars_per_line
DEFAULT_MAX_LINES = DEFAULT_NORMS.max_lines

_LANGUAGES_BY_SCRIPT: dict[str, tuple[str, ...]] = {
    "latin": (
        "afrikaans", "albanian", "azerbaijani", "basque", "bosnian", "catalan",
        "chichewa", "corsican", "croatian", "czech", "danish", "dutch",
        "english", "esperanto", "estonian", "finnish", "french", "frisian",
        "galician", "german", "haitian creole", "hausa", "hungarian",
        "icelandic", "igbo", "indonesian", "irish", "italian", "javanese",
        "kinyarwanda", "latin", "latvian", "lithuanian", "luxembourgish",
        "malagasy", "malay", "maltese", "maori", "norwegian", "polish",
        "portuguese", "romanian", "samoan", "scots gaelic", "sesotho", "shona",
        "slovak", "slovenian", "somali", "spanish", "sundanese", "swahili",
        "swedish", "tagalog", "turkish", "turkmen", "uzbek", "vietnamese",
        "welsh", "xhosa", "yoruba", "zulu",
    ),
    "cyrillic": (
        "belarusian", "bulgarian", "kazakh", "kyrgyz", "macedonian",
        "mongolian", "russian", "serbian", "tajik", "tatar", "ukrainian",
    ),
    "greek": ("greek",),
    "arabic": ("arabic", "kurdish", "pashto", "persian", "sindhi", "urdu", "uyghur"),
    "hebrew": ("hebrew", "yiddish"),
    "devanagari": ("hindi", "marathi", "nepali"),
    "thai": ("thai",),
    "han": ("chinese",),
    "japanese": ("japanese",),
    "korean": ("korean",),
    # Written in a script with no entry of its own; the default norms apply.
    DEFAULT_SCRIPT: (
        "amharic", "armenian", "bengali", "burmese", "georgian", "gujarati",
        "kannada", "khmer", "lao", "malayalam", "odia", "punjabi", "sinhala",
        "tamil", "telugu",
    ),
}

LANGUAGE_SCRIPTS: dict[str, str] = {
    language: script
    for script, languages in _LANGUAGES_BY_SCRIPT.items()
    for language in languages
}

# Endonyms and common spellings that never reach LANGUAGE_SCRIPTS on their own.
LANGUAGE_ALIASES: dict[str, str] = {
    "bokmal": "norwegian",
    "cantonese": "chinese",
    "castilian": "spanish",
    "dari": "persian",
    "farsi": "persian",
    "filipino": "tagalog",
    "flemish": "dutch",
    "kirghiz": "kyrgyz",
    "mandarin": "chinese",
    "moldovan": "romanian",
    "myanmar": "burmese",
    "nynorsk": "norwegian",
    "oriya": "odia",
    "panjabi": "punjabi",
    "sinhalese": "sinhala",
}

# Longest phrase first, so "Brazilian Portuguese" beats a stray one-word hit.
_PHRASES: list[tuple[tuple[str, ...], str]] = sorted(
    (
        (tuple(name.split()), script)
        for name, script in (
            list(LANGUAGE_SCRIPTS.items())
            + [(alias, LANGUAGE_SCRIPTS[target])
               for alias, target in LANGUAGE_ALIASES.items()]
        )
    ),
    key=lambda item: (-len(item[0]), item[0]),
)


def _normalize(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z]+", " ", plain.lower()).strip()


def script_for(language_name: str) -> str:
    """The script `language_name` is written in, or DEFAULT_SCRIPT."""
    key = _normalize(language_name)
    if not key:
        return DEFAULT_SCRIPT
    if key in LANGUAGE_SCRIPTS:
        return LANGUAGE_SCRIPTS[key]
    if key in LANGUAGE_ALIASES:
        return LANGUAGE_SCRIPTS[LANGUAGE_ALIASES[key]]
    tokens = key.split()
    for phrase, script in _PHRASES:
        span = len(phrase)
        if any(tuple(tokens[i:i + span]) == phrase
               for i in range(len(tokens) - span + 1)):
            return script
    return DEFAULT_SCRIPT


def norms_for(language_name: str) -> SubtitleNorms:
    """Subtitle norms for a target language name, e.g. "Brazilian Portuguese"."""
    return SCRIPT_NORMS[script_for(language_name)]


def effective_norms(
    language_name: str, max_line_chars: int | None = None,
) -> SubtitleNorms:
    """`norms_for` with the user's --max-line-chars override applied."""
    norms = norms_for(language_name)
    if max_line_chars and max_line_chars > 0:
        return replace(norms, max_chars_per_line=max_line_chars)
    return norms


LANG_CODES = {
    "afrikaans": "af", "albanian": "sq", "amharic": "am", "arabic": "ar",
    "armenian": "hy", "azerbaijani": "az", "basque": "eu", "belarusian": "be",
    "bengali": "bn", "bosnian": "bs", "bulgarian": "bg", "burmese": "my",
    "catalan": "ca", "chichewa": "ny", "chinese": "zh", "corsican": "co",
    "croatian": "hr", "czech": "cs", "danish": "da", "dutch": "nl",
    "english": "en", "esperanto": "eo", "estonian": "et", "finnish": "fi",
    "french": "fr", "frisian": "fy", "galician": "gl", "georgian": "ka",
    "german": "de", "greek": "el", "gujarati": "gu", "haitian creole": "ht",
    "hausa": "ha", "hebrew": "he", "hindi": "hi", "hungarian": "hu",
    "icelandic": "is", "igbo": "ig", "indonesian": "id", "irish": "ga",
    "italian": "it", "japanese": "ja", "javanese": "jv", "kannada": "kn",
    "kazakh": "kk", "khmer": "km", "kinyarwanda": "rw", "korean": "ko",
    "kurdish": "ku", "kyrgyz": "ky", "lao": "lo", "latin": "la",
    "latvian": "lv", "lithuanian": "lt", "luxembourgish": "lb",
    "macedonian": "mk", "malagasy": "mg", "malay": "ms", "malayalam": "ml",
    "maltese": "mt", "maori": "mi", "marathi": "mr", "mongolian": "mn",
    "nepali": "ne", "norwegian": "no", "odia": "or", "pashto": "ps",
    "persian": "fa", "polish": "pl", "portuguese": "pt", "punjabi": "pa",
    "romanian": "ro", "russian": "ru", "samoan": "sm", "scots gaelic": "gd",
    "serbian": "sr", "sesotho": "st", "shona": "sn", "sindhi": "sd",
    "sinhala": "si", "slovak": "sk", "slovenian": "sl", "somali": "so",
    "spanish": "es", "sundanese": "su", "swahili": "sw", "swedish": "sv",
    "tagalog": "tl", "tajik": "tg", "tamil": "ta", "tatar": "tt",
    "telugu": "te", "thai": "th", "turkish": "tr", "turkmen": "tk",
    "ukrainian": "uk", "urdu": "ur", "uyghur": "ug", "uzbek": "uz",
    "vietnamese": "vi", "welsh": "cy", "xhosa": "xh", "yiddish": "yi",
    "yoruba": "yo", "zulu": "zu",
}


def lang_code(language: str) -> str:
    """Best-effort 2-letter code for `language`. Falls back to the first
    two letters if the name isn't in the table (e.g. user typed a regional name)."""
    key = language.strip().lower()
    if key in LANG_CODES:
        return LANG_CODES[key]
    if len(key) == 2 and key.isalpha():
        return key
    return key[:2]
