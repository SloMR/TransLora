"""The deterministic repairs measured against a whole file.

`data/synthetic_aligned.json` holds one synthetic "run" per target language,
each row tagged with the `lang` it translates into and numbered from 1 within
that slice. The Arabic slice is 50 cues of made-up dialogue paired with a
made-up translation that reproduces, cue for cue, the defect shapes two graded
benchmark runs exposed — CRLF residue, a clause bleeding across a cue
boundary, a tail that switched to vocalized orthography, dropped tags and
dropped speaker dashes, collapsed and over-long lines, foreign script welded
onto Arabic, a flattened sentence mark, and a phrase the file repeats.

The Japanese, Chinese, Russian and Spanish slices are short and clean. They
exist because the repairs branch on the target's script and Arabic alone
exercises one branch of each: reflow breaks on characters for the space-less
scripts and on words for the rest, the line budget is 16 for han and japanese
against 42 for latin and cyrillic, and the RTL and vocalisation passes must
leave every other script byte-identical.

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
# The added slices, each covering a branch the Arabic one cannot reach.
JAPANESE, CHINESE, RUSSIAN, SPANISH = "Japanese", "Chinese", "Russian", "Spanish"
ADDED_LANGUAGES = (JAPANESE, CHINESE, RUSSIAN, SPANISH)

CUE_COUNT = 50
ADDED_CUE_COUNT = 6

# The added slices share one shape, so a test can name a cue by its role.
CUE_OVER_BUDGET = 1
CUE_TWO_LINE = 2
CUE_DIALOGUE = 3
CUE_TAGGED = 4
# Cue 5 is whatever that slice alone can show: the script leak in Japanese and
# Russian, the forty-character line in Spanish.
CUE_LEAK = CUE_FORTY_CHARS = 5

# The one cue pair that really desynced: 30's translation swallowed 31's clause.
REAL_DESYNCS = {(30, 31)}
# A detector that flags everything is useless. The graded run's ceiling was 15
# flagged pairs in 371, so this file's 49 pairs allow 2; 3 leaves some slack.
MAX_FLAGGED_PAIRS = 3
# What the rule costs a reviewer here: two pairs to read, of which one is real.
FLAGGED_PAIRS = 2
# Both cues open on the same Arabic particle and the second is little else.
FALSE_POSITIVE = (24, 25)
# 16/17 share "coffee" because both source cues do: never a shift.
SHARED_WORD_PAIR = (16, 17)


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


def _by_number(blocks: list[SubtitleBlock]) -> dict[int, SubtitleBlock]:
    return {b.number: b for b in blocks}


def test_the_fixture_is_one_whole_file(run) -> None:
    source, output = run
    assert len(source) == len(output) == CUE_COUNT
    assert [b.number for b in source] == list(range(1, CUE_COUNT + 1))


# === Wire hygiene ============================================================


def test_the_crlf_residue_is_normalised_away(run) -> None:
    """A stray CR reads as a second line, doubling the cue's line count and
    pushing a closing tag off the end."""
    raw = next(r for r in _rows() if "\r" in r["en"])
    assert raw["n"] == 15 and "\r" in raw["target"]
    source, output = run
    for text in (_by_number(source)[15].text, _by_number(output)[15].text):
        assert "\r" not in text
        assert len(text.split("\n")) == 2


# === Cross-cue bleeding ======================================================


def _flagged_pairs(messages: list[str]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for message in messages:
        first, second = message.split(":")[0].removeprefix("Blocks ").split("-")
        pairs.add((int(first), int(second)))
    return pairs


def test_both_real_desyncs_are_flagged(run) -> None:
    flagged = _flagged_pairs(detect_cross_cue_shift(*run))
    assert flagged >= REAL_DESYNCS


def test_the_detector_stays_quiet_on_most_of_the_file(run) -> None:
    source, _ = run
    messages = detect_cross_cue_shift(*run)
    assert len(source) - 1 == CUE_COUNT - 1
    assert len(messages) == FLAGGED_PAIRS
    assert len(messages) <= MAX_FLAGGED_PAIRS


def test_only_one_flagged_pair_is_a_false_positive(run) -> None:
    flagged = _flagged_pairs(detect_cross_cue_shift(*run))
    assert flagged - REAL_DESYNCS == {FALSE_POSITIVE}


def test_a_word_both_source_cues_share_is_never_a_shift(run) -> None:
    """16 and 17 both say "coffee", and so do their translations. Repetition
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

    for first, second in sorted(REAL_DESYNCS):
        chars, needed = coverage(first, second)
        assert chars >= needed
    chars, needed = coverage(*SHARED_WORD_PAIR)
    assert chars < needed


def test_the_flag_names_the_shared_run(run) -> None:
    assert (
        "Blocks 30-31: 'منتصف الليل' appears in both cues - "
        "text may have shifted between them"
    ) in detect_cross_cue_shift(*run)


def test_the_old_length_ratio_rule_would_have_missed_it(run) -> None:
    """Why the rule was replaced: the desync barely moved the cue lengths."""
    source, output = run
    by_number = _by_number(source)
    out_by_number = _by_number(output)
    for first, second in sorted(REAL_DESYNCS):
        ratios = [
            len(out_by_number[n].text) / len(by_number[n].text)
            for n in (first, second)
        ]
        assert not (ratios[0] > 2.0 and ratios[1] < 0.5)
        assert not (ratios[0] < 0.5 and ratios[1] > 2.0)


# === Foreign script ==========================================================


def test_only_the_three_leaking_cues_are_reported(run) -> None:
    source, output = run
    leaks = {
        src.number: script_leaks(src.text, out.text, "arabic")
        for src, out in zip(source, output, strict=True)
    }
    assert sorted(n for n, found in leaks.items() if found) == [11, 12, 13]
    # A Latin brand the source itself uses, spaced off from the Arabic, is not
    # a leak — otherwise every credit line would be flagged.
    assert leaks[14] == []


def test_the_leak_reports_name_the_offending_text(run) -> None:
    source, output = run
    by_source, by_output = _by_number(source), _by_number(output)

    def leaks(n: int) -> list[str]:
        return script_leaks(by_source[n].text, by_output[n].text, "arabic")

    assert leaks(11) == ["han characters appear in the translation ('北京')"]
    assert leaks(12) == ["'المotel' welds arabic to latin with no separator"]
    assert leaks(13) == ["'وjoey' welds arabic to latin with no separator"]


# === Speaker dashes ==========================================================


def _restored(run) -> dict[int, tuple[str, bool]]:
    source, output = run
    return {
        src.number: restore_dialogue_dashes(src.text, out.text)
        for src, out in zip(source, output, strict=True)
    }


def test_the_dashes_go_back_where_the_line_count_survived(run) -> None:
    text, ok = _restored(run)[4]
    assert ok
    assert text == "- أين عمر الليلة؟\n- هو عند البوابة."


def test_a_cue_that_lost_a_speaker_turn_is_reported_not_guessed(run) -> None:
    """Both source lines were merged into one, so which words belong to which
    speaker is no longer knowable: report it, change nothing."""
    _, output = run
    text, ok = _restored(run)[3]
    assert not ok
    assert text == _by_number(output)[3].text


def test_every_other_cue_keeps_its_dashes_and_its_text(run) -> None:
    _, output = run
    by_number = _by_number(output)
    for number, (text, ok) in _restored(run).items():
        if number in (3, 4):
            continue
        assert ok and text == by_number[number].text


# === Line count and line length ==============================================


def _reflowed(run, max_chars: int = 42, script: str = "arabic") -> dict[int, str]:
    source, output = run
    result = {}
    for src, out in zip(source, output, strict=True):
        text, _ = restore_dialogue_dashes(src.text, out.text)
        text = reflow_to_line_count(
            text, len(src.text.split("\n")), max_chars, script)
        result[src.number] = enforce_line_length(text, max_chars, script)
    return result


def test_only_the_collapsed_and_over_long_cues_are_rewrapped(run) -> None:
    reflowed = _reflowed(run)
    restored = _restored(run)
    changed = [n for n, text in reflowed.items() if text != restored[n][0]]
    # 7 lost a line break, 8 and 30 are single lines past the Arabic limit.
    assert changed == [7, 8, 30]
    assert reflowed[7].count("\n") == 1
    assert all(len(line) <= 42 for line in reflowed[8].split("\n"))


def test_the_dialogue_cue_is_left_alone_even_though_it_lost_a_line(run) -> None:
    """Cue 3 is one line where the source had two, but re-wrapping a cue that
    still carries a dash would put a speaker turn mid-line."""
    _, output = run
    assert _reflowed(run)[3] == _by_number(output)[3].text


def test_a_line_inside_the_arabic_limit_is_still_over_the_cjk_one(run) -> None:
    _, output = run
    line = _by_number(output)[9].text
    assert 16 < len(line) <= 42
    assert enforce_line_length(line, 42, "arabic") == line
    assert enforce_line_length(line, 16, "han").count("\n") == 1


# === Formatting tags =========================================================


def test_the_empty_pair_is_dropped_and_the_wrapping_pair_restored(run) -> None:
    source, output = run
    by_source, by_output = _by_number(source), _by_number(output)
    assert "<i></i>" in by_output[5].text
    assert repair_tags(by_source[5].text, by_output[5].text) == (
        "انطفأت الأضواء عند الفجر.", True)
    assert repair_tags(by_source[6].text, by_output[6].text) == (
        "<i>لم يجب أحد على اللاسلكي.</i>", True)


# === Diacritics ==============================================================


def test_only_the_vocalized_tail_is_stripped(run) -> None:
    _, output = run
    normalized = normalize_diacritics(output, "arabic")
    changed = [
        after.number for before, after in zip(output, normalized, strict=True)
        if before.text != after.text
    ]
    # The model switched orthographic register for the last ten cues, plus 22.
    assert changed == [22, *range(41, 51)]


def test_stripping_leaves_the_letters_alone(run) -> None:
    _, output = run
    normalized = normalize_diacritics(output, "arabic")
    vocalized = next(b for b in normalized if b.number == 41)
    assert diacritic_count(vocalized.text) == 0
    assert "عمر" in vocalized.text


def test_a_file_vocalized_throughout_is_left_alone(run) -> None:
    """Full vocalisation is a deliberate style, not the drift we are fixing."""
    _, output = run
    heavy = [b for b in output if b.number >= 41]
    assert normalize_diacritics(heavy, "arabic") == heavy


# === Terminal punctuation ====================================================


def test_the_swapped_sentence_marks_are_restored(run) -> None:
    source, output = run
    by_number = _by_number(output)
    fixed = {
        src.number: restore_terminal_punctuation(src.text, out.text, "arabic")
        for src, out in zip(source, output, strict=True)
    }
    changed = {n: text for n, text in fixed.items()
               if text != by_number[n].text}
    # An exclamation and a question both came back as statements.
    assert sorted(changed) == [10, 20]
    assert changed[10].endswith("!")
    assert changed[20].endswith("؟")


# === The other scripts =======================================================

# Everything above measures Arabic. The repairs branch on the target's script
# in four places — where a line may be broken, how wide it may be, whether
# ASCII punctuation is re-pointed and whether vocalisation is stripped — and
# Arabic reaches exactly one side of each branch. These pin the other side.


@pytest.fixture(scope="module")
def slices() -> dict[str, tuple[list[SubtitleBlock], list[SubtitleBlock]]]:
    return {lang: _load(lang) for lang in ADDED_LANGUAGES}


def _cue(slices, lang: str, number: int) -> str:
    return _by_number(slices[lang][1])[number].text


def test_every_added_slice_is_a_whole_file_of_its_own(slices) -> None:
    for lang in ADDED_LANGUAGES:
        source, output = slices[lang]
        assert len(source) == len(output) == ADDED_CUE_COUNT
        assert [b.number for b in source] == list(range(1, ADDED_CUE_COUNT + 1))


def test_every_added_slice_carries_the_shapes_the_repairs_need(slices) -> None:
    """A slice with nothing over its budget, no second line, no speaker turn
    and no tag leaves the rules below with nothing to act on."""
    for lang in ADDED_LANGUAGES:
        norms = norms_for(lang)
        over_budget = _cue(slices, lang, CUE_OVER_BUDGET)
        assert visible_length(over_budget) > norms.max_chars_per_line
        assert "\n" not in over_budget
        assert _cue(slices, lang, CUE_TWO_LINE).count("\n") == 1
        assert dialogue_dash_lines(_cue(slices, lang, CUE_DIALOGUE)) == 2
        assert find_tags(_cue(slices, lang, CUE_TAGGED))


# === Where a line may be broken ==============================================


def test_a_space_less_script_is_broken_between_characters(slices) -> None:
    """Japanese and Chinese cues carry no spaces at all, so the word-unit
    branch sees a single unit and gives up: only the character-unit branch can
    wrap them, and rejoining with no space reproduces the cue."""
    for lang in (JAPANESE, CHINESE):
        text = _cue(slices, lang, CUE_OVER_BUDGET)
        norms = norms_for(lang)
        assert " " not in text
        lines = reflow_to_line_count(
            text, 2, norms.max_chars_per_line, norms.script).split("\n")
        assert len(lines) == 2
        assert "".join(lines) == text
        assert reflow_to_line_count(text, 2, 42, "latin") == text


def test_a_spaced_script_is_broken_between_words(slices) -> None:
    """Rejoining with the space the break consumed reproduces the cue, so no
    break landed inside a word."""
    for lang in (RUSSIAN, SPANISH):
        text = _cue(slices, lang, CUE_OVER_BUDGET)
        norms = norms_for(lang)
        lines = reflow_to_line_count(
            text, 2, norms.max_chars_per_line, norms.script).split("\n")
        assert len(lines) == 2
        assert " ".join(lines) == text


def test_a_dialogue_cue_is_never_reflowed_whatever_the_script(slices) -> None:
    """Merging two dashed lines would put a speaker turn mid-line; the guard
    runs before the script is ever consulted and has to stay that way."""
    for lang in ADDED_LANGUAGES:
        text = _cue(slices, lang, CUE_DIALOGUE)
        norms = norms_for(lang)
        assert reflow_to_line_count(
            text, 1, norms.max_chars_per_line, norms.script) == text


def test_dropped_speaker_dashes_go_back_whatever_the_script(slices) -> None:
    for lang in ADDED_LANGUAGES:
        source, output = slices[lang]
        text = _by_number(output)[CUE_DIALOGUE].text
        flattened = "\n".join(
            line.removeprefix("- ") for line in text.split("\n"))
        assert restore_dialogue_dashes(
            _by_number(source)[CUE_DIALOGUE].text, flattened) == (text, True)


# === How wide a line may be ==================================================


def test_the_line_budget_is_per_script_not_global(slices) -> None:
    """One forty-character line, two targets: latin keeps it, han has to wrap
    it. The text never changed — only the norms the target brings."""
    line = _cue(slices, SPANISH, CUE_FORTY_CHARS)
    latin, han = norms_for(SPANISH), norms_for(CHINESE)
    assert (latin.max_chars_per_line, han.max_chars_per_line) == (42, 16)
    assert visible_length(line) == 40
    assert enforce_line_length(line, latin.max_chars_per_line, latin.script) == line
    assert enforce_line_length(
        line, han.max_chars_per_line, han.script).count("\n") == 1


def test_a_two_line_cue_inside_its_budget_is_left_alone(slices) -> None:
    for lang in ADDED_LANGUAGES:
        text = _cue(slices, lang, CUE_TWO_LINE)
        norms = norms_for(lang)
        assert all(visible_length(line) <= norms.max_chars_per_line
                   for line in text.split("\n"))
        assert enforce_line_length(
            text, norms.max_chars_per_line, norms.script) == text


def test_a_tag_costs_no_columns_in_any_script(slices) -> None:
    """Every tagged cue is past its budget as raw text and inside it once the
    tags are discounted, so a rule measuring len() would rewrap all four."""
    for lang in ADDED_LANGUAGES:
        text = _cue(slices, lang, CUE_TAGGED)
        norms = norms_for(lang)
        assert len(text) > norms.max_chars_per_line
        assert visible_length(text) <= norms.max_chars_per_line
        assert enforce_line_length(
            text, norms.max_chars_per_line, norms.script) == text


def test_a_dropped_wrapping_tag_is_restored_whatever_the_script(slices) -> None:
    for lang in ADDED_LANGUAGES:
        source, output = slices[lang]
        text = _by_number(output)[CUE_TAGGED].text
        bare = text.removeprefix("<i>").removesuffix("</i>")
        assert repair_tags(
            _by_number(source)[CUE_TAGGED].text, bare) == (text, True)


# === Arabic-only passes, measured against the scripts they must not touch ====


RTL_CUE = 2


def test_the_rtl_map_repoints_an_arabic_cue(run, slices) -> None:
    """Arabic cue 2 is the same English line as cue 3 of every added slice, so
    the five translations below are one input answered five ways. Only the
    Arabic one carries a mark ASCII gets wrong: its ؟ is what the map has to
    put back when a model returns the ASCII form."""
    source, arabic = run
    assert (_by_number(source)[RTL_CUE].text
            == _by_number(slices[SPANISH][0])[CUE_DIALOGUE].text)
    written = _by_number(arabic)[RTL_CUE].text
    as_returned = written.replace("؟", "?")
    assert as_returned != written
    assert normalize_rtl_punctuation(as_returned, "arabic") == written


def test_the_rtl_map_leaves_every_other_script_byte_identical(slices) -> None:
    for lang in ADDED_LANGUAGES:
        output = slices[lang][1]
        # Vacuous unless the slice holds a mark the map could have re-pointed.
        assert any(char in RTL_PUNCTUATION for b in output for char in b.text)
        script = script_for(lang)
        assert ([normalize_rtl_punctuation(b.text, script) for b in output]
                == [b.text for b in output])


def test_the_rtl_map_is_licensed_by_the_target_not_by_the_text(run) -> None:
    """The same Arabic cue, byte for byte: re-pointed for an Arabic target and
    left alone for every other one. Two rules keep the map off a Russian or
    Japanese cue — the target's script and the absence of an Arabic
    neighbour — and this is the one that pins the first."""
    _, arabic = run
    as_returned = _by_number(arabic)[RTL_CUE].text.replace("؟", "?")
    for lang in ADDED_LANGUAGES:
        assert normalize_rtl_punctuation(
            as_returned, script_for(lang)) == as_returned


def test_vocalisation_stripping_leaves_a_non_arabic_file_untouched(
    slices,
) -> None:
    """Twice over: the script guard turns the pass away, and there is nothing
    in these scripts for it to strip even when it is asked to run."""
    for lang in ADDED_LANGUAGES:
        output = slices[lang][1]
        assert normalize_diacritics(output, script_for(lang)) == output
        assert normalize_diacritics(output, "arabic") == output


def test_vocalisation_stripping_is_licensed_by_the_target_too(run) -> None:
    """The Arabic file's vocalized tail offered to a non-Arabic target: the
    marks stay, though an Arabic target strips eleven cues of them."""
    _, arabic = run
    for lang in ADDED_LANGUAGES:
        assert normalize_diacritics(arabic, script_for(lang)) == arabic


def test_variant_drift_finds_nothing_in_a_non_arabic_file(slices) -> None:
    """The markers are Arabic function words, so a Russian or Japanese file is
    clean whether or not the script guard is what stops the check."""
    for lang in (RUSSIAN, JAPANESE):
        output = slices[lang][1]
        assert detect_variant_drift(output, script_for(lang)) is None
        assert detect_variant_drift(output, "arabic") is None


# === Foreign script, in the scripts Arabic cannot show =======================


def test_latin_welded_into_a_japanese_cue_is_reported(slices) -> None:
    source, output = slices[JAPANESE]
    assert script_leaks(
        _by_number(source)[CUE_LEAK].text,
        _by_number(output)[CUE_LEAK].text,
        "japanese",
    ) == ["'客はmotelにいる' welds han to latin with no separator"]


def test_han_left_in_a_russian_cue_is_reported(slices) -> None:
    source, output = slices[RUSSIAN]
    assert script_leaks(
        _by_number(source)[CUE_LEAK].text,
        _by_number(output)[CUE_LEAK].text,
        "cyrillic",
    ) == ["han characters appear in the translation ('北京')"]


def test_no_other_cue_in_any_added_slice_leaks(slices) -> None:
    """The Spanish and Chinese slices are the control: a detector that fires
    on clean text in a script it was never tuned on is worse than none."""
    for lang in ADDED_LANGUAGES:
        source, output = slices[lang]
        script = script_for(lang)
        flagged = [out.number
                   for src, out in zip(source, output, strict=True)
                   if script_leaks(src.text, out.text, script)]
        assert flagged == ([CUE_LEAK] if lang in (JAPANESE, RUSSIAN) else [])


# === Recurring-phrase seeding ================================================


def test_the_phrases_the_run_kept_mistranslating_are_seeded(run) -> None:
    source, _ = run
    phrases = recurring_phrases(source)
    assert phrases[0] == "harbour master"  # 5 cues, the file's own subject
    for phrase in ("night shift", "the west gate", "the harbour master"):
        assert phrase in phrases
    assert len(phrases) <= 25


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
        CharacterHint("Joey", "جوي", "male"),
        CharacterHint("Reyes", "رييس", "unknown"),
    ],
    terms=[
        TermHint("harbour master", "رئيس الميناء"),
        TermHint("night shift", "نوبة الليل"),
        TermHint("west gate", "البوابة الغربية"),
        TermHint("log book", "دفتر السجل"),
    ],
    idioms=[
        TermHint("all right", "حسنا"),
        TermHint("hurry up", "أسرع"),
        TermHint("out loud", "بصوت عال"),
        TermHint("one by one", "واحدا تلو الآخر"),
        TermHint("at last", "أخيرا"),
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


def test_counting_idioms_costs_nothing_at_the_default_batch_size(run) -> None:
    """5 batches, 5 reviewed before and 5 after: ten cues is long enough that
    every batch of this file already names a character or a term."""
    source, _ = run
    batches = _batches(source, DEFAULT_BATCH_SIZE)
    reviewed = [b for b in batches
                if BENCHMARK_GLOSSARY.has_correctable_entries(b)]
    assert (len(batches), len(reviewed)) == (5, 5)
    assert [b for b in batches if _idiom_only(b)] == []


def test_halving_the_batch_is_where_counting_idioms_buys_a_call(run) -> None:
    """10 batches, 8 reviewed before and 9 after — one extra call, and the
    closing scene is the batch that gains it."""
    source, _ = run
    batches = _batches(source, DEFAULT_BATCH_SIZE // 2)
    reviewed = [b for b in batches
                if BENCHMARK_GLOSSARY.has_correctable_entries(b)]
    gained = [b for b in batches if _idiom_only(b)]
    assert (len(batches), len(reviewed)) == (10, 9)
    assert [(b[0].number, b[-1].number) for b in gained] == [(46, 50)]
