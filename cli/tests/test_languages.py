"""Language metadata: the language -> script -> limits lookup, and the ISO
code used as the output filename suffix.

The norm values are the Netflix/EBU house figures; they are pinned literally
here because a silent change to one of them changes every translated file.
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.languages import (
    ARABIC_PUNCTUATION_SCRIPTS,
    DEFAULT_MAX_CHARS_PER_LINE,
    DEFAULT_MAX_LINES,
    DEFAULT_NORMS,
    DEFAULT_SCRIPT,
    GRAMMAR_NOTES,
    LANG_CODES,
    LANGUAGE_SCRIPTS,
    RTL_SCRIPTS,
    SCRIPT_NORMS,
    effective_norms,
    grammar_note_for,
    lang_code,
    norms_for,
    script_for,
)

LANGUAGES_TS = (Path(__file__).resolve().parents[2]
                / "web" / "src" / "app" / "core" / "languages.ts")


# === The table ===============================================================


@pytest.mark.parametrize("script,chars,lines,cps", [
    ("latin", 42, 2, 17),
    ("cyrillic", 42, 2, 17),
    ("greek", 42, 2, 17),
    ("arabic", 42, 2, 20),
    ("hebrew", 42, 2, 20),
    ("devanagari", 42, 2, 17),
    ("thai", 42, 2, 17),
    ("han", 16, 2, 9),
    ("japanese", 16, 2, 4),
    ("korean", 20, 2, 12),
    ("default", 42, 2, 17),
])
def test_every_script_carries_its_industry_values(
    script: str, chars: int, lines: int, cps: int,
) -> None:
    norms = SCRIPT_NORMS[script]
    assert (norms.script, norms.max_chars_per_line, norms.max_lines,
            norms.target_cps) == (script, chars, lines, cps)


def test_the_table_holds_exactly_the_eleven_documented_scripts() -> None:
    assert set(SCRIPT_NORMS) == {
        "latin", "cyrillic", "greek", "arabic", "hebrew", "devanagari",
        "thai", "han", "japanese", "korean", DEFAULT_SCRIPT,
    }


def test_the_defaults_come_from_the_default_script() -> None:
    assert DEFAULT_NORMS is SCRIPT_NORMS[DEFAULT_SCRIPT]
    assert (DEFAULT_MAX_CHARS_PER_LINE, DEFAULT_MAX_LINES) == (42, 2)


def test_only_arabic_and_hebrew_are_right_to_left() -> None:
    assert {"arabic", "hebrew"} == RTL_SCRIPTS
    assert {s for s, n in SCRIPT_NORMS.items() if n.rtl} == RTL_SCRIPTS


def test_hebrew_is_rtl_but_keeps_ascii_punctuation() -> None:
    assert SCRIPT_NORMS["hebrew"].rtl is True
    assert {"arabic"} == ARABIC_PUNCTUATION_SCRIPTS


# === Language lookup =========================================================


@pytest.mark.parametrize("language,script", [
    ("Arabic", "arabic"),
    ("Japanese", "japanese"),
    ("Chinese", "han"),
    ("Korean", "korean"),
    ("Russian", "cyrillic"),
    ("Hindi", "devanagari"),
    ("Spanish", "latin"),
    ("Greek", "greek"),
    ("Hebrew", "hebrew"),
    ("Thai", "thai"),
    ("Persian", "arabic"),
    ("Urdu", "arabic"),
    ("Yiddish", "hebrew"),
    ("Serbian", "cyrillic"),
    ("Vietnamese", "latin"),
    # Written in a script with no row of its own.
    ("Tamil", DEFAULT_SCRIPT),
    ("Amharic", DEFAULT_SCRIPT),
])
def test_language_names_map_to_their_script(language: str, script: str) -> None:
    assert script_for(language) == script


@pytest.mark.parametrize("language", ["Klingon", "", "   ", "Made Up Tongue"])
def test_an_unrecognised_language_falls_back_instead_of_failing(
    language: str,
) -> None:
    assert script_for(language) == DEFAULT_SCRIPT
    assert norms_for(language) is DEFAULT_NORMS


@pytest.mark.parametrize("language,script", [
    ("Brazilian Portuguese", "latin"),
    ("Egyptian Arabic", "arabic"),
    ("Simplified Chinese", "han"),
    ("Traditional Chinese", "han"),
    ("Latin American Spanish", "latin"),
    ("Iranian Persian", "arabic"),
])
def test_a_regional_variant_resolves_through_its_base_language(
    language: str, script: str,
) -> None:
    assert script_for(language) == script


@pytest.mark.parametrize("language,script", [
    ("Farsi", "arabic"),
    ("Mandarin", "han"),
    ("Cantonese", "han"),
    ("Filipino", "latin"),
    ("Castilian", "latin"),
    ("Flemish", "latin"),
    ("Dari", "arabic"),
    ("Myanmar", DEFAULT_SCRIPT),
])
def test_common_endonyms_and_alternate_spellings_resolve(
    language: str, script: str,
) -> None:
    assert script_for(language) == script


@pytest.mark.parametrize("typed", [
    "arabic", "ARABIC", "  Arabic  ", "Arabic!", "Arabic (Egypt)",
    "Arabic, Modern Standard",
])
def test_the_typed_name_is_normalised_before_lookup(typed: str) -> None:
    assert script_for(typed) == "arabic"


def test_a_diacritic_in_a_typed_name_is_folded_away() -> None:
    assert script_for("Norwegian Bokmål") == "latin"


# === Coverage of the shipped language list ===================================


def _shipped_languages() -> list[str]:
    source = LANGUAGES_TS.read_text(encoding="utf-8")
    return re.findall(r"name: '([^']+)'", source)


@pytest.mark.skipif(not LANGUAGES_TS.exists(), reason="web/ tree not present")
def test_every_language_the_web_ui_offers_is_classified_explicitly() -> None:
    # Falling back to `default` is the safety net for a name we never listed,
    # not an acceptable answer for one of the 105 we ship in the picker.
    shipped = _shipped_languages()
    assert len(shipped) >= 105
    unlisted = [n for n in shipped if n.lower() not in LANGUAGE_SCRIPTS]
    assert unlisted == []


def test_every_language_the_cli_knows_a_code_for_is_classified_explicitly() -> None:
    assert [name for name in LANG_CODES if name not in LANGUAGE_SCRIPTS] == []


# === effective_norms =========================================================


def test_norms_for_returns_the_whole_record() -> None:
    norms = norms_for("Arabic")
    assert (norms.script, norms.max_chars_per_line, norms.max_lines,
            norms.target_cps, norms.rtl) == ("arabic", 42, 2, 20, True)


def test_the_user_override_replaces_only_the_line_length() -> None:
    norms = effective_norms("Japanese", 24)
    assert norms.max_chars_per_line == 24
    assert (norms.script, norms.max_lines, norms.target_cps) == ("japanese", 2, 4)


@pytest.mark.parametrize("override", [None, 0, -5])
def test_an_absent_or_meaningless_override_keeps_the_script_default(
    override: int | None,
) -> None:
    assert effective_norms("Korean", override) is SCRIPT_NORMS["korean"]


def test_norms_are_immutable_so_an_override_cannot_leak_between_files() -> None:
    with pytest.raises(FrozenInstanceError):
        norms_for("Arabic").max_chars_per_line = 10  # type: ignore[misc]
    assert SCRIPT_NORMS["arabic"].max_chars_per_line == 42


# === Output-filename suffix ==================================================
# `movie.srt` + `-t Arabic` -> `movie.ar.srt`.


@pytest.mark.parametrize("language,code", [
    ("Arabic", "ar"),
    ("arabic", "ar"),
    ("  Arabic  ", "ar"),
    ("ARABIC", "ar"),
    ("Japanese", "ja"),
    # Compound names are in the table and must not be truncated to "ha"/"sc".
    ("Haitian Creole", "ht"),
    ("scots gaelic", "gd"),
])
def test_known_languages_map_to_their_iso_code(language: str, code: str) -> None:
    assert lang_code(language) == code


@pytest.mark.parametrize("language,code", [
    ("pt", "pt"),
    ("PT", "pt"),
])
def test_two_letter_input_passes_through(language: str, code: str) -> None:
    assert lang_code(language) == code


@pytest.mark.parametrize("language,code", [
    ("Klingon", "kl"),
    # A regional name the table doesn't carry keeps the first two letters —
    # a filename suffix, not a claim about ISO 639.
    ("Brazilian Portuguese", "br"),
    ("x", "x"),
])
def test_unknown_languages_fall_back_to_the_first_two_letters(
    language: str, code: str,
) -> None:
    assert lang_code(language) == code


def test_blank_language_yields_an_empty_code() -> None:
    # Unreachable from the CLI (--target rejects a blank value), but callers
    # must not assume a non-empty suffix.
    assert lang_code("") == ""
    assert lang_code("   ") == ""


# === Grammar notes ===========================================================


def test_every_script_in_the_norms_table_has_a_grammar_note_entry() -> None:
    """A missing key reads as "no checks for this script", which is a silent
    downgrade rather than a visible failure."""
    assert set(GRAMMAR_NOTES) == set(SCRIPT_NORMS)


@pytest.mark.parametrize("script,fragment", [
    ("arabic", "use the jussive"),
    ("hebrew", "including the dual"),
    ("japanese", "です/ます"),
    ("korean", "one speech level"),
    ("han", "measure word"),
    ("cyrillic", "Match case, gender and number"),
    ("devanagari", "one level of formality"),
])
def test_an_inflecting_script_names_its_classic_error(
    script: str, fragment: str,
) -> None:
    assert fragment in GRAMMAR_NOTES[script]


@pytest.mark.parametrize("script", ["latin", "greek", "thai", DEFAULT_SCRIPT])
def test_a_script_with_nothing_specific_to_check_says_nothing(script: str) -> None:
    # An empty note means the review request keeps its line count and its
    # cache-friendly shape; no filler text.
    assert GRAMMAR_NOTES[script] == ""
    assert grammar_note_for(script) == ""


def test_an_unknown_script_gets_no_note_instead_of_raising() -> None:
    assert grammar_note_for("klingon") == ""


def test_the_note_is_looked_up_by_script_so_every_arabic_script_language_shares_it() -> None:
    for language in ("Arabic", "Persian", "Urdu"):
        assert grammar_note_for(script_for(language)) == GRAMMAR_NOTES["arabic"]
