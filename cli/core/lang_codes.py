"""Map human language names to ISO 639-1 codes for output filename suffixes."""


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
