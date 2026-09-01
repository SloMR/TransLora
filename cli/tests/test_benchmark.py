"""The deterministic repairs measured against a whole file.

`data/synthetic_aligned.json` is a 50-cue synthetic English->Arabic "run",
written for this suite: made-up dialogue paired with a made-up translation
that reproduces, cue for cue, the defect shapes two graded benchmark runs
exposed — CRLF residue, a clause bleeding across a cue boundary, a tail that
switched to vocalized orthography, dropped tags and dropped speaker dashes,
collapsed and over-long lines, foreign script welded onto Arabic, a flattened
sentence mark, and a phrase the file repeats.

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
from core.repair import (
    detect_cross_cue_shift,
    diacritic_count,
    enforce_line_length,
    normalize_diacritics,
    reflow_to_line_count,
    repair_tags,
    restore_dialogue_dashes,
    restore_terminal_punctuation,
    script_leaks,
)
from core.srt_parser import SubtitleBlock, normalize_text

DATA = Path(__file__).parent / "data" / "synthetic_aligned.json"

CUE_COUNT = 50

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


def _rows() -> list[dict]:
    rows: list[dict] = json.loads(DATA.read_text(encoding="utf-8"))
    return rows


def _load() -> tuple[list[SubtitleBlock], list[SubtitleBlock]]:
    """Source and output as the parser hands them over — normalised, because
    that is the only shape the pipeline ever sees."""
    rows = _rows()
    source = [SubtitleBlock(r["n"], r["ts"], normalize_text(r["en"])) for r in rows]
    output = [SubtitleBlock(r["n"], r["ts"], normalize_text(r["ar"])) for r in rows]
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
    assert raw["n"] == 15 and "\r" in raw["ar"]
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
