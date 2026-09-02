"""The deterministic repairs measured against a whole file.

`data/synthetic_aligned.json` holds one synthetic "run" per target language,
each row tagged with the `lang` it translates into and numbered from 1 within
that slice. Every slice plants the same eleven defect shapes in the same
eleven roles — the constants below name them — because the repairs branch on
the target's script, and a defect planted only in Arabic only ever proves
Arabic works.

Arabic is the longest slice because three passes are its alone: the RTL
punctuation map, the vocalisation strip and the variant check. Each is
file-level, so it needs a file to be level against, and the cues past role 12
are what that file is made of.

One pass cannot see a defect every slice plants, and the blind spot is pinned
by a test rather than left for someone to find as a silent gap: `content_words`
splits on spaces, so the cross-cue run test never fires for Japanese or
Chinese.

Unit tests elsewhere pin each rule on small hand-built cases; these pin what
the rules do to a whole file, which is the only place a detector's
false-positive rate is visible — or, for the review gate, the only place its
call count is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import repair
from core.constants import DEFAULT_BATCH_SIZE
from core.context_pass import (
    CharacterHint,
    FileContext,
    TermHint,
    recurring_phrases,
)
from core.languages import norms_for, script_for
from core.repair import (
    RTL_PUNCTUATION,
    detect_cross_cue_shift,
    detect_variant_drift,
    diacritic_count,
    dialogue_dash_lines,
    enforce_line_length,
    find_tags,
    normalize_diacritics,
    normalize_rtl_punctuation,
    reflow_to_line_count,
    repair_tags,
    restore_dialogue_dashes,
    restore_terminal_punctuation,
    script_leaks,
    visible_length,
)
from core.srt_parser import SubtitleBlock, normalize_text

DATA = Path(__file__).parent / "data" / "synthetic_aligned.json"

ARABIC = "Arabic"
JAPANESE, CHINESE, RUSSIAN, SPANISH = "Japanese", "Chinese", "Russian", "Spanish"
LANGUAGES = (ARABIC, JAPANESE, CHINESE, RUSSIAN, SPANISH)
ADDED_LANGUAGES = (JAPANESE, CHINESE, RUSSIAN, SPANISH)

ARABIC_CUE_COUNT = 32
ADDED_CUE_COUNT = 12

# === The roles every slice plants, by cue number within that slice ===========

CUE_OVER_BUDGET = 1
CUE_TWO_LINE = 2
CUE_DIALOGUE = 3
CUE_TAGGED = 4
# The first cue's translation swallowed a clause of the second, which kept
# only the fragment.
BLED_PAIR = (5, 6)
CUE_DASH_DROPPED = 7   # both dashes gone, both lines survived: restorable
CUE_DASH_MERGED = 8    # dashes gone AND the turns merged: not restorable
CUE_DROPPED_WRAP = 9
CUE_COLLAPSED = 10
CUE_FLATTENED_MARK = 11
# Cue 12 is whatever that slice alone can show: the welded Latin in Japanese,
# the Han leak in Russian, the forty-character line in Chinese.
CUE_LEAK = CUE_FORTY_CHARS = 12

# The scripts whose bleeding the run test can see. A script written without
# spaces yields one token per cue, so no run is ever shared.
BLEED_VISIBLE = (ARABIC, RUSSIAN, SPANISH)
BLEED_BLIND = (JAPANESE, CHINESE)
# Every script's flattened terminal mark is restored, each in its own glyph:
# `_target_mark` re-points the ASCII three through RTL_PUNCTUATION for Arabic
# and CJK_PUNCTUATION for Han and Japanese.
RESTORED_MARK = {ARABIC: "!", JAPANESE: "！", CHINESE: "！",
                 RUSSIAN: "!", SPANISH: "!"}

# === What the Arabic slice alone carries, past role 12 ======================

# 13 and 14 both say "coffee", and so do their translations: repetition, never
# a shift.
SHARED_WORD_PAIR = (13, 14)
CUE_CRLF = 15
# Both cues open on the same Arabic particle and the second is little else.
FALSE_POSITIVE = (16, 17)
SPLIT_PHRASE_CUES = (18, 19, 20, 21)
CONSISTENT_PHRASE_CUES = (22, 23, 24)
CUE_SPACED_LATIN = 25   # a brand the source itself uses: not a leak
CUE_HAN_LEAK = 26
CUE_WELDED_LEAK = 27
VOCALIZED_CUES = (28, 29, 30, 31, 32)

# A detector that flags everything is useless. The graded run's ceiling was 15
# flagged pairs in 371; this slice's 31 pairs allow 2.
MAX_FLAGGED_PAIRS = 2


def _rows(lang: str = ARABIC) -> list[dict]:
    rows: list[dict] = json.loads(DATA.read_text(encoding="utf-8"))
    return [r for r in rows if r["lang"] == lang]


def _load(lang: str = ARABIC) -> tuple[list[SubtitleBlock], list[SubtitleBlock]]:
    """Source and output as the parser hands them over — normalised, because
    that is the only shape the pipeline ever sees."""
    rows = _rows(lang)
    source = [SubtitleBlock(r["n"], r["ts"], normalize_text(r["en"])) for r in rows]
    output = [SubtitleBlock(r["n"], r["ts"], normalize_text(r["target"]))
              for r in rows]
    return source, output


@pytest.fixture(scope="module")
def run() -> tuple[list[SubtitleBlock], list[SubtitleBlock]]:
    return _load()


@pytest.fixture(scope="module")
def slices() -> dict[str, tuple[list[SubtitleBlock], list[SubtitleBlock]]]:
    return {lang: _load(lang) for lang in LANGUAGES}


def _by_number(blocks: list[SubtitleBlock]) -> dict[int, SubtitleBlock]:
    return {b.number: b for b in blocks}


def _flagged_pairs(messages: list[str]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for message in messages:
        first, second = message.split(":")[0].removeprefix("Blocks ").split("-")
        pairs.add((int(first), int(second)))
    return pairs


def test_every_slice_is_a_whole_file_of_its_own(slices) -> None:
    for lang in LANGUAGES:
        source, output = slices[lang]
        expected = ARABIC_CUE_COUNT if lang == ARABIC else ADDED_CUE_COUNT
        assert len(source) == len(output) == expected, lang
        assert [b.number for b in source] == list(range(1, expected + 1)), lang


def test_every_slice_plants_the_same_eleven_shapes(slices) -> None:
    """The roles are the fixture's contract. A slice missing one would make
    every loop below quietly measure four scripts instead of five."""
    for lang in LANGUAGES:
        source, output = slices[lang]
        src, out = _by_number(source), _by_number(output)
        norms = norms_for(lang)
        assert visible_length(out[CUE_OVER_BUDGET].text) > norms.max_chars_per_line
        assert len(out[CUE_TWO_LINE].text.split("\n")) == 2
        assert dialogue_dash_lines(out[CUE_DIALOGUE].text) == 2
        assert len(find_tags(out[CUE_TAGGED].text)) == 2
        assert dialogue_dash_lines(out[CUE_DASH_DROPPED].text) == 0
        assert len(out[CUE_DASH_MERGED].text.split("\n")) == 1
        assert find_tags(out[CUE_DROPPED_WRAP].text) == []
        assert len(src[CUE_COLLAPSED].text.split("\n")) == 2
        assert len(out[CUE_COLLAPSED].text.split("\n")) == 1
        assert src[CUE_FLATTENED_MARK].text.endswith("!")


# === Wire hygiene ============================================================


def test_the_crlf_residue_is_normalised_away(run) -> None:
    """A stray CR reads as a second line, doubling the cue's line count and
    pushing a closing tag off the end."""
    raw = next(r for r in _rows() if "\r" in r["en"])
    assert raw["n"] == CUE_CRLF and "\r" in raw["target"]
    source, output = run
    for text in (_by_number(source)[CUE_CRLF].text,
                 _by_number(output)[CUE_CRLF].text):
        assert "\r" not in text
        assert len(text.split("\n")) == 2


# === Cross-cue bleeding ======================================================


def test_the_planted_bleed_is_flagged_in_every_script_that_can_see_it(
    slices,
) -> None:
    for lang in BLEED_VISIBLE:
        assert _flagged_pairs(detect_cross_cue_shift(*slices[lang])) == {
            BLED_PAIR}, lang


def test_a_space_less_script_hides_the_same_bleed(slices) -> None:
    """Not a gap to close by loosening the rule. `content_words` splits on
    spaces, so a script that does not use them hands the run test one token per
    cue and no run can ever be shared. The bleed is planted in these slices
    anyway, so the day the rule learns to see it, this test says so."""
    for lang in BLEED_BLIND:
        source, output = slices[lang]
        assert detect_cross_cue_shift(source, output) == [], lang
        first = _by_number(output)[BLED_PAIR[0]]
        assert len(repair.content_words(first.text)) == 1, lang


def test_the_detector_stays_quiet_on_most_of_the_file(run) -> None:
    flagged = _flagged_pairs(detect_cross_cue_shift(*run))
    assert len(flagged) <= MAX_FLAGGED_PAIRS
    assert FALSE_POSITIVE not in flagged


def test_a_word_both_source_cues_share_is_never_a_shift(run) -> None:
    """13 and 14 both say "coffee", and so do their translations. Repetition
    is not movement, and the source-ratio rule is what knows the difference."""
    assert SHARED_WORD_PAIR not in _flagged_pairs(detect_cross_cue_shift(*run))


def test_the_coverage_rule_is_what_silences_the_word_reuse_pairs(run) -> None:
    """The desync puts most of the shorter cue's characters in the shared run;
    a pair that merely reuses a word does not come close."""
    _, output = run
    by_number = _by_number(output)

    def coverage(first: int, second: int) -> tuple[int, float]:
        run_words = repair._longest_shared_run(
            repair.content_words(by_number[first].text),
            repair.content_words(by_number[second].text))
        shorter = min(repair._cue_chars(by_number[first].text),
                      repair._cue_chars(by_number[second].text))
        return repair._run_chars(run_words), repair.SHIFT_MIN_COVERAGE * shorter

    chars, needed = coverage(*BLED_PAIR)
    assert chars >= needed
    chars, needed = coverage(*SHARED_WORD_PAIR)
    assert chars < needed


def test_the_flag_names_the_shared_run(run) -> None:
    assert (
        "Blocks 5-6: 'بوابة المرفأ' appears in both cues - "
        "text may have shifted between them"
    ) in detect_cross_cue_shift(*run)


def test_the_old_length_ratio_rule_would_have_missed_it(run) -> None:
    """Why the rule was replaced: the desync barely moved the cue lengths."""
    source, output = run
    by_number, out_by_number = _by_number(source), _by_number(output)
    ratios = [
        len(out_by_number[n].text) / len(by_number[n].text) for n in BLED_PAIR
    ]
    assert not (ratios[0] > 2.0 and ratios[1] < 0.5)
    assert not (ratios[0] < 0.5 and ratios[1] > 2.0)


# === Foreign script ==========================================================


def test_only_the_two_leaking_arabic_cues_are_reported(run) -> None:
    source, output = run
    leaks = {
        src.number: script_leaks(src.text, out.text, "arabic")
        for src, out in zip(source, output, strict=True)
    }
    assert sorted(n for n, found in leaks.items() if found) == [
        CUE_HAN_LEAK, CUE_WELDED_LEAK]
    # A Latin brand the source itself uses, spaced off from the Arabic, is not
    # a leak — otherwise every credit line would be flagged.
    assert leaks[CUE_SPACED_LATIN] == []


def test_the_leak_reports_name_the_offending_text(run) -> None:
    source, output = run
    by_source, by_output = _by_number(source), _by_number(output)

    def leaks(n: int) -> list[str]:
        return script_leaks(by_source[n].text, by_output[n].text, "arabic")

    assert leaks(CUE_HAN_LEAK) == [
        "han characters appear in the translation ('北京')"]
    assert leaks(CUE_WELDED_LEAK) == [
        "'الmotel' welds arabic to latin with no separator"]


def test_latin_welded_into_a_japanese_cue_is_reported(slices) -> None:
    source, output = slices[JAPANESE]
    src, out = _by_number(source)[CUE_LEAK], _by_number(output)[CUE_LEAK]
    assert script_leaks(src.text, out.text, "japanese")


def test_han_left_in_a_russian_cue_is_reported(slices) -> None:
    source, output = slices[RUSSIAN]
    src, out = _by_number(source)[CUE_LEAK], _by_number(output)[CUE_LEAK]
    assert script_leaks(src.text, out.text, "cyrillic")


def test_no_other_cue_in_any_added_slice_leaks(slices) -> None:
    for lang in ADDED_LANGUAGES:
        source, output = slices[lang]
        script = script_for(lang)
        flagged = [
            src.number
            for src, out in zip(source, output, strict=True)
            if script_leaks(src.text, out.text, script)
        ]
        assert flagged in ([], [CUE_LEAK]), lang


# === Speaker dashes ==========================================================


def test_the_dashes_go_back_where_the_line_count_survived(slices) -> None:
    for lang in LANGUAGES:
        source, output = slices[lang]
        src = _by_number(source)[CUE_DASH_DROPPED]
        out = _by_number(output)[CUE_DASH_DROPPED]
        text, ok = restore_dialogue_dashes(src.text, out.text)
        assert ok, lang
        assert text == "\n".join(f"- {line}" for line in out.text.split("\n"))


def test_a_cue_that_lost_a_speaker_turn_is_reported_not_guessed(slices) -> None:
    for lang in LANGUAGES:
        source, output = slices[lang]
        src = _by_number(source)[CUE_DASH_MERGED]
        out = _by_number(output)[CUE_DASH_MERGED]
        text, ok = restore_dialogue_dashes(src.text, out.text)
        assert (text, ok) == (out.text, False), lang


def test_every_other_cue_keeps_its_dashes_and_its_text(run) -> None:
    source, output = run
    for src, out in zip(source, output, strict=True):
        if src.number in (CUE_DASH_DROPPED, CUE_DASH_MERGED):
            continue
        assert restore_dialogue_dashes(src.text, out.text) == (out.text, True)


# === Line count and line length ==============================================


def test_the_collapsed_cue_is_rewrapped_in_every_script(slices) -> None:
    for lang in LANGUAGES:
        _, output = slices[lang]
        norms = norms_for(lang)
        out = _by_number(output)[CUE_COLLAPSED]
        rewrapped = reflow_to_line_count(
            out.text, 2, norms.max_chars_per_line, norms.script)
        assert len(rewrapped.split("\n")) == 2, lang


def test_the_over_long_cue_is_broken_in_every_script(slices) -> None:
    for lang in LANGUAGES:
        _, output = slices[lang]
        norms = norms_for(lang)
        out = _by_number(output)[CUE_OVER_BUDGET]
        lines = enforce_line_length(
            out.text, norms.max_chars_per_line, norms.script).split("\n")
        # Two lines is the professional maximum, so this is what the repair
        # guarantees: exactly two, both inside the target script's budget.
        assert len(lines) == 2, lang
        for line in lines:
            assert visible_length(line) <= norms.max_chars_per_line, lang


def test_the_dialogue_cue_is_left_alone_whatever_the_script(slices) -> None:
    for lang in LANGUAGES:
        _, output = slices[lang]
        norms = norms_for(lang)
        out = _by_number(output)[CUE_DIALOGUE]
        assert reflow_to_line_count(
            out.text, 1, norms.max_chars_per_line, norms.script) == out.text, lang


def test_a_space_less_script_is_broken_between_characters(slices) -> None:
    for lang in (JAPANESE, CHINESE):
        _, output = slices[lang]
        norms = norms_for(lang)
        out = _by_number(output)[CUE_OVER_BUDGET]
        lines = reflow_to_line_count(
            out.text, 2, norms.max_chars_per_line, norms.script).split("\n")
        assert len(lines) == 2, lang
        # Joined with nothing at all: the break landed inside the writing, and
        # no separator was invented to hold the halves apart.
        assert "".join(lines) == out.text, lang
        # The word reflow has one unit to work with and cannot split at all.
        assert reflow_to_line_count(
            out.text, 2, norms.max_chars_per_line, "latin") == out.text, lang


def test_a_spaced_script_is_broken_between_words(slices) -> None:
    for lang in (ARABIC, RUSSIAN, SPANISH):
        _, output = slices[lang]
        norms = norms_for(lang)
        out = _by_number(output)[CUE_OVER_BUDGET]
        lines = reflow_to_line_count(
            out.text, 2, norms.max_chars_per_line, norms.script).split("\n")
        assert len(lines) == 2, lang
        # Rejoining on the space proves no word was cut in half.
        assert " ".join(lines) == out.text, lang


def test_the_line_budget_is_per_script_not_global(slices) -> None:
    assert [norms_for(lang).max_chars_per_line for lang in LANGUAGES] == [
        42, 16, 16, 42, 42]
    _, output = slices[CHINESE]
    out = _by_number(output)[CUE_FORTY_CHARS]
    assert visible_length(out.text) == 40
    latin, han = norms_for(SPANISH), norms_for(CHINESE)
    assert enforce_line_length(
        out.text, latin.max_chars_per_line, latin.script) == out.text
    assert len(enforce_line_length(
        out.text, han.max_chars_per_line, han.script).split("\n")) == 2


def test_a_two_line_cue_inside_its_budget_is_left_alone(slices) -> None:
    for lang in LANGUAGES:
        _, output = slices[lang]
        norms = norms_for(lang)
        out = _by_number(output)[CUE_TWO_LINE]
        assert enforce_line_length(
            out.text, norms.max_chars_per_line, norms.script) == out.text, lang


def test_a_tag_costs_no_columns_in_any_script(slices) -> None:
    for lang in LANGUAGES:
        _, output = slices[lang]
        out = _by_number(output)[CUE_TAGGED]
        assert visible_length(out.text) < len(out.text), lang


# === Formatting tags =========================================================


def test_a_dropped_wrapping_tag_is_restored_whatever_the_script(slices) -> None:
    for lang in LANGUAGES:
        source, output = slices[lang]
        src = _by_number(source)[CUE_DROPPED_WRAP]
        out = _by_number(output)[CUE_DROPPED_WRAP]
        text, ok = repair_tags(src.text, out.text)
        assert ok, lang
        assert text == f"<i>{out.text}</i>", lang


def test_the_empty_pair_is_dropped_and_the_wrapping_pair_restored(run) -> None:
    """The shape a real run shipped: the wrap duplicated, leaving renderless
    pairs either side of the text."""
    source, output = run
    src, out = _by_number(source)[12], _by_number(output)[12]
    text, ok = repair_tags(src.text, out.text)
    assert ok
    assert text == "{\\i1}كان المرفأ هادئا.{\\i0}"


# === Diacritics ==============================================================


def test_only_the_vocalized_tail_is_stripped(run) -> None:
    _, output = run
    stripped = normalize_diacritics(output, "arabic")
    moved = [b.number for b, s in zip(output, stripped, strict=True)
             if b.text != s.text]
    assert moved == list(VOCALIZED_CUES)


def test_stripping_leaves_the_letters_alone(run) -> None:
    _, output = run
    stripped = _by_number(normalize_diacritics(output, "arabic"))
    assert stripped[28].text == "خذ الفانوس وانزل."
    # A shadda sits on a letter; stripping it must not take the letter.
    assert stripped[30].text == "الماء شديد السواد الليلة."
    for n in VOCALIZED_CUES:
        assert diacritic_count(stripped[n].text) == 0


def test_a_file_vocalized_throughout_is_left_alone(run) -> None:
    """The strip is file-level: only the whole file says what its baseline is."""
    _, output = run
    vocalized = [b for b in output if b.number in VOCALIZED_CUES]
    assert normalize_diacritics(vocalized, "arabic") == vocalized


def test_vocalisation_stripping_leaves_a_non_arabic_file_untouched(
    slices,
) -> None:
    for lang in ADDED_LANGUAGES:
        _, output = slices[lang]
        assert normalize_diacritics(output, script_for(lang)) == output, lang


def test_vocalisation_stripping_is_licensed_by_the_target_too(run) -> None:
    """The same Arabic cues, declared Latin, come back untouched: the gate is
    the target's script, not what the characters happen to be."""
    _, output = run
    assert normalize_diacritics(output, "latin") == output


# === Terminal punctuation ====================================================


def test_the_flattened_mark_is_restored_in_every_script(slices) -> None:
    for lang in LANGUAGES:
        source, output = slices[lang]
        script = script_for(lang)
        restored = [
            src.number
            for src, out in zip(source, output, strict=True)
            if restore_terminal_punctuation(src.text, out.text, script) != out.text
        ]
        assert restored == [CUE_FLATTENED_MARK], lang


def test_the_restored_mark_is_spelled_the_way_the_script_spells_it(
    slices,
) -> None:
    """A CJK cue ends its sentence on a fullwidth mark, so an ASCII "!" welded
    onto kana is the same defect wearing a different hat."""
    for lang in LANGUAGES:
        source, output = slices[lang]
        src = _by_number(source)[CUE_FLATTENED_MARK]
        out = _by_number(output)[CUE_FLATTENED_MARK]
        assert src.text.endswith("!"), lang
        assert restore_terminal_punctuation(
            src.text, out.text, script_for(lang),
        ) == out.text[:-1] + RESTORED_MARK[lang], lang


# === Arabic-only passes, measured against the scripts they must not touch ====


def test_the_rtl_map_repoints_an_arabic_cue(run) -> None:
    _, output = run
    question = _by_number(output)[CUE_DIALOGUE].text
    assert "؟" in question
    assert normalize_rtl_punctuation(
        question.replace("؟", "?"), "arabic") == question
    assert set(RTL_PUNCTUATION) == {"?", ",", ";"}


def test_the_rtl_map_leaves_every_other_script_byte_identical(slices) -> None:
    for lang in ADDED_LANGUAGES:
        _, output = slices[lang]
        for out in output:
            assert normalize_rtl_punctuation(
                out.text, script_for(lang)) == out.text, lang
            # Not just the script gate: Arabic's own map finds nothing to
            # re-point in a cue with no Arabic letter beside the mark.
            assert normalize_rtl_punctuation(out.text, "arabic") == out.text, lang


def test_the_rtl_map_is_licensed_by_the_target_not_by_the_text(run) -> None:
    _, output = run
    for out in output:
        assert normalize_rtl_punctuation(out.text, "latin") == out.text


def test_variant_drift_finds_nothing_in_a_non_arabic_file(slices) -> None:
    for lang in ADDED_LANGUAGES:
        _, output = slices[lang]
        assert detect_variant_drift(
            [b.text for b in output], lang) is None, lang


# === Recurring-phrase seeding ================================================


def test_the_phrases_the_run_kept_mistranslating_are_seeded(run) -> None:
    source, _ = run
    phrases = recurring_phrases(source)
    for phrase in ("night shift", "harbour master"):
        assert phrase in phrases
    assert len(phrases) <= 25


def test_the_split_phrase_is_rendered_three_different_ways(run) -> None:
    """Every cue reads correctly on its own; only a whole-file pass can see
    that the file never settled on one wording."""
    _, output = run
    by_number = _by_number(output)
    renderings = {by_number[n].text for n in SPLIT_PHRASE_CUES}
    assert len(renderings) == len(SPLIT_PHRASE_CUES)
    assert sum("نوبة الليل" in t for t in renderings) == 2
    # The phrase the file did settle on, for contrast.
    assert all("رئيس المرفأ" in by_number[n].text
               for n in CONSISTENT_PHRASE_CUES)


# === What the review gate costs ==============================================

# A review call is bought per batch by has_correctable_entries, so widening the
# gate to count idioms is a call-count change and is measured here rather than
# argued. The glossary below is the shape a scan returns for this file: its
# cast, the phrases recurring_phrases already seeds as terms, and the set
# phrases its dialogue actually uses.

BENCHMARK_GLOSSARY = FileContext(
    register="Modern Standard Arabic, neutral",
    characters=[
        CharacterHint("Omar", "عمر", "male"),
        CharacterHint("Nadia", "نادية", "female"),
    ],
    terms=[
        TermHint("harbour master", "رئيس المرفأ"),
        TermHint("night shift", "نوبة الليل"),
        TermHint("log book", "دفتر السجل"),
    ],
    idioms=[
        TermHint("all right", "حسنا"),
        TermHint("hurry up", "أسرع"),
        TermHint("one by one", "واحدا تلو الآخر"),
    ],
)


def _batches(
    source: list[SubtitleBlock], size: int,
) -> list[list[SubtitleBlock]]:
    return [source[i:i + size] for i in range(0, len(source), size)]


def _idiom_only(batch: list[SubtitleBlock]) -> bool:
    """A slice the old chars-or-terms gate turned away: it names an idiom and
    nothing else the reviewer is permitted to act on."""
    rendered = BENCHMARK_GLOSSARY.render_for_batch(batch)
    return ("Idioms - render by meaning" in rendered
            and "Characters:" not in rendered and "Terms:" not in rendered)


def test_counting_idioms_buys_exactly_one_review_call(run) -> None:
    """4 batches and all 4 reviewed. The closing scene is the one that names
    an idiom and nothing else the reviewer may act on, so it is the single
    call the widened gate bought; every other batch already named a character
    or a term."""
    source, _ = run
    batches = _batches(source, DEFAULT_BATCH_SIZE)
    reviewed = [b for b in batches
                if BENCHMARK_GLOSSARY.has_correctable_entries(b)]
    gained = [b for b in batches if _idiom_only(b)]
    assert (len(batches), len(reviewed)) == (4, 4)
    assert [(b[0].number, b[-1].number) for b in gained] == [(31, 32)]
    assert gained[0] in reviewed


def test_halving_the_batch_leaves_one_slice_the_gate_turns_away(run) -> None:
    """7 batches, 6 reviewed: smaller batches mean glossary slices that name
    nothing correctable at all, and those still cost nothing."""
    source, _ = run
    batches = _batches(source, DEFAULT_BATCH_SIZE // 2)
    reviewed = [b for b in batches
                if BENCHMARK_GLOSSARY.has_correctable_entries(b)]
    assert (len(batches), len(reviewed)) == (7, 6)
