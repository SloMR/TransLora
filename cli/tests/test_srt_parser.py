"""The SRT wire format, batch validation, and the deterministic repairs.

Every repair case below reproduces a defect a graded English->Arabic run
actually produced: formatting tags dropped undetected, a quarter of the cues
collapsing two source lines into one, a clause migrating into the next cue,
both dialogue dashes vanishing so two speakers merged into one line, and
Chinese characters and unseparated Latin left inside Arabic text.
"""

from __future__ import annotations

import re

import pytest

from core.repair import (
    TARGET_SCRIPTS,
    detect_variant_drift,
    diacritic_count,
    dialogue_dash_lines,
    drop_empty_tag_pairs,
    enforce_line_length,
    find_cross_cue_shifts,
    find_script_leaks,
    find_tags,
    normalize_diacritics,
    normalize_rtl_punctuation,
    reflow_to_line_count,
    repair_tags,
    restore_dialogue_dashes,
    restore_terminal_punctuation,
    script_of,
    shift_message,
    variant_drift_message,
    visible_length,
)
from core.srt_parser import (
    SubtitleBlock,
    normalize_text,
    parse_lite,
    serialize_lite,
    split_batches,
    validate_batch,
)

TIMESTAMP_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$"
)


# The hand-written SRT text parser and writer live here now: the CLI reads
# every format through pysubs2 and only these tests want the bare wire shape.
def parse_srt(content: str) -> list[SubtitleBlock]:
    raw_blocks = re.split(r"\n\n+", normalize_text(content).strip())

    blocks: list[SubtitleBlock] = []
    for raw in raw_blocks:
        lines = raw.strip().split("\n")
        if len(lines) < 2:
            continue
        try:
            number = int(lines[0].strip())
        except ValueError:
            continue
        timestamp = lines[1].strip()
        if not TIMESTAMP_RE.match(timestamp):
            continue
        text = "\n".join(lines[2:]) if len(lines) > 2 else ""
        blocks.append(SubtitleBlock(number=number, timestamp=timestamp, text=text))

    return blocks


def serialize_srt(blocks: list[SubtitleBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        parts.append(f"{block.number}\n{block.timestamp}\n{block.text}")
    return "\n\n".join(parts) + "\n"


def detect_cross_cue_shift(
    source: list[SubtitleBlock], output: list[SubtitleBlock],
) -> list[str]:
    return [shift_message(s) for s in find_cross_cue_shifts(source, output)]


def script_leaks(source_text: str, output_text: str, script: str) -> list[str]:
    return [leak.message
            for leak in find_script_leaks(source_text, output_text, script)]


ITALIC_OPEN, ITALIC_CLOSE = "{\\i1}", "{\\i0}"

SAMPLE = (
    "1\n"
    "00:00:01,000 --> 00:00:02,500\n"
    "Hello world\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,500\n"
    "Two\n"
    "lines\n"
)


def test_parse_basic() -> None:
    blocks = parse_srt(SAMPLE)
    assert [b.number for b in blocks] == [1, 2]
    assert blocks[0].timestamp == "00:00:01,000 --> 00:00:02,500"
    assert blocks[1].text == "Two\nlines"


def test_parse_strips_bom_and_crlf() -> None:
    raw = "\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\nHi\r\n"
    blocks = parse_srt(raw)
    assert len(blocks) == 1
    assert blocks[0].text == "Hi"


def test_parse_skips_malformed_blocks() -> None:
    raw = (
        "not-a-number\n"
        "00:00:01,000 --> 00:00:02,500\n"
        "text\n"
        "\n"
        "2\n"
        "00:00:03,000 --> 00:00:04,500\n"
        "good\n"
    )
    blocks = parse_srt(raw)
    assert [b.number for b in blocks] == [2]


def test_serialize_roundtrip() -> None:
    blocks = parse_srt(SAMPLE)
    out = serialize_srt(blocks)
    assert parse_srt(out) == blocks


def test_split_batches_exact_and_remainder() -> None:
    blocks = [SubtitleBlock(i, "00:00:00,000 --> 00:00:01,000", "x") for i in range(1, 8)]
    assert [len(b) for b in split_batches(blocks, 3)] == [3, 3, 1]
    assert [len(b) for b in split_batches(blocks, 7)] == [7]
    assert split_batches([], 5) == []


def test_validate_batch_pass() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    b = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hola")]
    assert validate_batch(a, b).ok


def test_validate_batch_count_mismatch() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    result = validate_batch(a, [])
    assert not result.ok
    assert "count" in result.error.lower()


def test_validate_batch_number_mismatch() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    b = [SubtitleBlock(2, "00:00:01,000 --> 00:00:02,000", "hola")]
    assert not validate_batch(a, b).ok


def test_validate_batch_timestamp_modified() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    b = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,500", "hola")]
    result = validate_batch(a, b)
    assert not result.ok
    assert "timestamp" in result.error.lower()


def test_validate_batch_rejects_empty_output_for_nonempty_input() -> None:
    a = [
        SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi"),
        SubtitleBlock(2, "00:00:03,000 --> 00:00:04,000", "there"),
    ]
    b = [
        SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hola"),
        SubtitleBlock(2, "00:00:03,000 --> 00:00:04,000", ""),
    ]
    result = validate_batch(a, b)
    assert not result.ok
    assert "empty" in result.error.lower()


def test_validate_batch_allows_empty_output_for_empty_input() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "")]
    b = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "")]
    assert validate_batch(a, b).ok


# === Lite wire format ========================================================
# Number + text, no timestamps. This is what the model actually sees and
# answers in, so every drift in block count starts here.


def test_serialize_lite_omits_timestamps() -> None:
    blocks = parse_srt(SAMPLE)
    assert serialize_lite(blocks) == "1\nHello world\n\n2\nTwo\nlines\n"


def test_lite_roundtrip_preserves_numbers_and_line_breaks() -> None:
    blocks = parse_srt(SAMPLE)
    out = parse_lite(serialize_lite(blocks))
    assert [b.number for b in out] == [1, 2]
    assert [b.text for b in out] == ["Hello world", "Two\nlines"]
    # Timestamps are not on the wire; callers reattach the input's.
    assert [b.timestamp for b in out] == ["", ""]


def test_serialize_lite_collapses_an_internal_blank_line() -> None:
    # A blank line inside a caption would forge a block boundary: the block
    # would come back as two, and half the caption would be dropped silently.
    blocks = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "one\n\ntwo")]
    wire = serialize_lite(blocks)
    assert wire == "1\none\ntwo\n"
    assert len(parse_lite(wire)) == 1


def test_parse_lite_strips_bom_and_crlf() -> None:
    out = parse_lite("﻿1\r\nHola\r\n\r\n2\r\nAdios\r\n")
    assert [(b.number, b.text) for b in out] == [(1, "Hola"), (2, "Adios")]


def test_parse_lite_skips_commentary_and_keeps_blocks() -> None:
    out = parse_lite("Here is the translation:\n\n1\nHola\n\n2\nAdios\n")
    assert [b.number for b in out] == [1, 2]


def test_parse_lite_drops_n_paren_numbering() -> None:
    # "1)" is not an int, so the block is dropped rather than mis-numbered —
    # validate_batch then rejects the reply on count.
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    out = parse_lite("1)\nHola\n")
    assert out == []
    assert not validate_batch(a, out).ok


def test_validate_batch_rejects_a_renumbered_response() -> None:
    # The model shifted every number by one: same count, same text shape.
    # Caught only because validation runs before timestamps are reattached.
    a = [
        SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi"),
        SubtitleBlock(2, "00:00:03,000 --> 00:00:04,000", "there"),
    ]
    b = parse_lite("2\nHola\n\n3\nAlli\n")
    result = validate_batch(a, b)
    assert not result.ok
    assert "number mismatch" in result.error.lower()
    assert "expected 1" in result.error


def test_validate_batch_rejects_a_leaked_timestamp_line() -> None:
    a = [SubtitleBlock(7, "00:00:01,000 --> 00:00:02,000", "hi")]
    b = parse_lite("7\n00:00:01,000 --> 00:00:02,000\nHola\n")
    result = validate_batch(a, b)
    assert not result.ok
    assert "leaked" in result.error.lower()
    assert "block 7" in result.error


def test_validate_batch_accepts_a_clean_wire_reply() -> None:
    a = [
        SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi"),
        SubtitleBlock(2, "00:00:03,000 --> 00:00:04,000", "two\nlines"),
    ]
    b = parse_lite(serialize_lite(a))
    assert validate_batch(a, b).ok


# === Tag inventory ===========================================================


@pytest.mark.parametrize("text,tags", [
    ("{\\i1}Hello{\\i0}", ["{\\i1}", "{\\i0}"]),
    ("<i>Hello</i>", ["<i>", "</i>"]),
    ('<font color="#fff">Hi</font>', ['<font color="#fff">', "</font>"]),
    ("{\\an8}Top of screen", ["{\\an8}"]),
    ("Plain text", []),
    # 3 < 4 is not a tag: a tag opens with a letter.
    ("3 < 4 and 5 > 2", []),
])
def test_find_tags_lists_both_forms_in_reading_order(text: str, tags: list) -> None:
    assert find_tags(text) == tags


def test_visible_length_ignores_tags() -> None:
    assert visible_length("{\\i1}Hello{\\i0}") == len("Hello")
    assert visible_length("<i>Hi</i> there") == len("Hi there")


# === repair_tags =============================================================


def test_matching_tags_pass_through_untouched() -> None:
    text, ok = repair_tags("<i>Hello</i>", "<i>مرحبا</i>")
    assert (text, ok) == ("<i>مرحبا</i>", True)


def test_untagged_source_and_output_is_a_no_op() -> None:
    assert repair_tags("Hello", "مرحبا") == ("مرحبا", True)


@pytest.mark.parametrize("source,expected", [
    ("{\\i1}Hello there{\\i0}", "{\\i1}مرحبا{\\i0}"),
    ("<i>Hello there</i>", "<i>مرحبا</i>"),
])
def test_dropped_wrapping_pair_is_restored(source: str, expected: str) -> None:
    # The observed defect: blocks 235-239, 242, 292 and 363 lost their italics.
    text, ok = repair_tags(source, "مرحبا")
    assert (text, ok) == (expected, True)


def test_dropped_leading_tag_is_restored() -> None:
    text, ok = repair_tags("{\\an8}Hello", "مرحبا")
    assert (text, ok) == ("{\\an8}مرحبا", True)


@pytest.mark.parametrize("source,output", [
    # Interior tags: where they belong in the translation is a guess.
    ("A <i>b</i> c", "س ص ع"),
    ("Hello{\\i0} there", "مرحبا"),
    # Trailing-only tag: prefixing it would move it.
    ("Hello{\\i0}", "مرحبا"),
])
def test_unrepairable_loss_is_reported_not_guessed(source: str, output: str) -> None:
    text, ok = repair_tags(source, output)
    assert (text, ok) == (output, False)


@pytest.mark.parametrize("source,output", [
    ("<i>Hello</i>", "<b>مرحبا</b>"),        # wrong tag
    ("<i>Hello</i>", "<i>مرحبا"),            # closing tag dropped
    ("Hello", "<i>مرحبا</i>"),               # tag invented
    ("<i>a</i> <i>b</i>", "<i>س ص</i>"),     # pair merged
])
def test_changed_tags_are_never_rewritten(source: str, output: str) -> None:
    text, ok = repair_tags(source, output)
    assert (text, ok) == (output, False)


# === reflow_to_line_count ====================================================


def test_two_source_lines_collapsed_into_one_are_restored() -> None:
    # 85 of 372 cues did this; max line length went from 47 to 98 chars.
    one_line = "متى يتجاوز التعليق أو الفعل الخط الأحمر ويصبح شيئا آخر تماما"
    out = reflow_to_line_count(one_line, 2, 42, "arabic")
    assert out.count("\n") == 1
    assert out.replace("\n", " ") == one_line
    assert max(len(line) for line in out.split("\n")) <= 42


def test_the_split_minimises_the_longest_line() -> None:
    out = reflow_to_line_count("aaa bbb ccc ddd eee fff", 2, 42, "latin")
    # A greedy fill would give "aaa bbb ccc ddd eee" / "fff".
    assert out == "aaa bbb ccc\nddd eee fff"


def test_three_lines_are_produced_when_the_source_had_three() -> None:
    out = reflow_to_line_count("aaa bbb ccc ddd eee fff", 3, 42, "latin")
    assert out == "aaa bbb\nccc ddd\neee fff"


def test_a_cue_that_already_matches_is_returned_unchanged() -> None:
    text = "first line\nsecond line"
    assert reflow_to_line_count(text, 2, 42, "latin") is text


@pytest.mark.parametrize("text,target", [
    ("- Yes, I did.\n- No, you did not do that at all", 1),
    ("– Yes, I did.\n– No, you did not do that at all", 1),
    ("- Yes, I did. - No, you did not do that at all", 2),
    ("{\\i1}- Yes.{\\i0}\n- No, not at all, I promise you that", 1),
])
def test_dialogue_dash_cues_are_never_reflowed(text: str, target: int) -> None:
    # Those dashes encode speaker turns; re-flowing them merges two speakers.
    assert reflow_to_line_count(text, target, 42, "latin") is text


@pytest.mark.parametrize("text,script,expected", [
    ("你好世界，这是测试。", "han", "你好世界，\n这是测试。"),
    ("これはテストです。もう一度お願いします。", "japanese",
     "これはテストです。\nもう一度お願いします。"),
])
def test_scripts_without_spaces_break_on_characters(
    text: str, script: str, expected: str,
) -> None:
    assert reflow_to_line_count(text, 2, 16, script) == expected


def test_a_cjk_break_prefers_the_punctuation_over_the_even_split() -> None:
    # A 6/6 split would open the second line with "，"; 7/5 reads correctly.
    out = reflow_to_line_count("一二三四五六，七八九十。", 2, 6, "han")
    assert out == "一二三四五六，\n七八九十。"
    assert all(line[0] not in "、。，．！？；：）」" for line in out.split("\n"))


def test_a_formatting_tag_is_never_split_across_lines() -> None:
    text = "{\\i1}it is not always easy to notice when the tide turns{\\i0}"
    out = reflow_to_line_count(text, 2, 42, "latin")
    assert out.split("\n")[0].startswith(ITALIC_OPEN)
    assert out.split("\n")[1].endswith(ITALIC_CLOSE)
    assert find_tags(out) == find_tags(text)


def test_a_tag_glued_to_a_word_moves_with_it() -> None:
    out = reflow_to_line_count(
        "abcdefgh {\\i1}ijklmnop{\\i0} qrstuvwx", 2, 12, "latin")
    assert out == "abcdefgh\n{\\i1}ijklmnop{\\i0} qrstuvwx"


@pytest.mark.parametrize("text,target", [
    ("Hi", 3),        # fewer words than lines
    ("", 2),
    ("   ", 2),
    ("Hello there", 0),
    ("Hello there", -1),
])
def test_an_impossible_reflow_returns_the_text_unchanged(
    text: str, target: int,
) -> None:
    assert reflow_to_line_count(text, target, 42, "latin") is text


def test_reflow_never_loses_or_reorders_words() -> None:
    text = "the quick brown fox jumps over the lazy dog again"
    out = reflow_to_line_count(text, 2, 42, "latin")
    assert out.split() == text.split()


# === enforce_line_length =====================================================


def test_a_single_over_long_line_becomes_two() -> None:
    text = "a" * 30 + " " + "b" * 30
    out = enforce_line_length(text, 42, "latin")
    assert out == "a" * 30 + "\n" + "b" * 30


def test_a_line_within_the_limit_is_left_alone() -> None:
    assert enforce_line_length("short enough", 42, "latin") == "short enough"


def test_a_two_line_cue_is_never_split_into_three() -> None:
    # Two lines is the professional maximum, over-long or not.
    text = "a" * 60 + "\n" + "b" * 60
    assert enforce_line_length(text, 42, "latin") == text


def test_the_limit_is_measured_without_tags() -> None:
    text = "{\\i1}" + "a" * 40 + "{\\i0}"
    assert enforce_line_length(text, 42, "latin") == text


def test_an_unsplittable_long_word_is_left_alone() -> None:
    text = "a" * 60
    assert enforce_line_length(text, 42, "latin") == text


# === normalize_rtl_punctuation ===============================================


@pytest.mark.parametrize("text,expected", [
    ("مرحبا, كيف حالك?", "مرحبا، كيف حالك؟"),
    ("نعم; لا", "نعم؛ لا"),
    ("ماذا?", "ماذا؟"),
])
def test_arabic_punctuation_is_repointed(text: str, expected: str) -> None:
    assert normalize_rtl_punctuation(text, "arabic") == expected


def test_conversion_reaches_across_a_formatting_tag() -> None:
    assert normalize_rtl_punctuation(
        "{\\i1}مرحبا{\\i0}, بك", "arabic") == "{\\i1}مرحبا{\\i0}، بك"


@pytest.mark.parametrize("script", ["hebrew", "latin", "han", "default"])
def test_only_arabic_script_targets_are_repointed(script: str) -> None:
    # Hebrew is RTL but keeps ASCII punctuation.
    assert normalize_rtl_punctuation("שלום, מה?", script) == "שלום, מה?"


@pytest.mark.parametrize("text", [
    "Hello, world?",                     # no Arabic anywhere
    "2x02 : الحلقة",                     # episode code, Latin/digit run
    "1,000 دولار",                       # digit group separator
    "زر http://x.com/a,b?q=1 الآن",      # URL query string
    "زر www.example.com/a?b الآن",       # bare host form
    "{\\pos(10,20)}مرحبا",               # comma inside an ASS override
    '<font face="A,B">مرحبا</font>',     # comma inside an HTML attribute
])
def test_protected_spans_keep_their_ascii_punctuation(text: str) -> None:
    assert normalize_rtl_punctuation(text, "arabic") == text


def test_a_comma_directly_after_a_url_stays_ascii() -> None:
    # The URL pattern eats the trailing comma; leaving it alone is the safe
    # side of the trade, since a comma can be part of the address.
    assert normalize_rtl_punctuation(
        "www.example.com, مرحبا", "arabic") == "www.example.com, مرحبا"
    # A comma before a URL is outside it, so it converts as usual.
    assert normalize_rtl_punctuation(
        "مرحبا, www.example.com", "arabic") == "مرحبا، www.example.com"


def test_empty_text_is_handled() -> None:
    assert normalize_rtl_punctuation("", "arabic") == ""


# === detect_cross_cue_shift ==================================================

# Real block 236/237: the model let 236 finish 237's sentence, so both cues
# ended up carrying "الخط" while the English pair shares no word at all.
BLED_FIRST = "متى يتجاوز التعليق أو الفعل الخط"
BLED_SECOND = "الخط."


def _pair(a_src: str, b_src: str, a_out: str, b_out: str):
    source = [SubtitleBlock(236, "", a_src), SubtitleBlock(237, "", b_src)]
    output = [SubtitleBlock(236, "", a_out), SubtitleBlock(237, "", b_out)]
    return source, output


def test_content_shared_by_two_output_cues_but_not_their_sources_is_reported() -> None:
    source, output = _pair(
        "when the tide turns in the channel,", "gates must close early.",
        BLED_FIRST, BLED_SECOND,
    )
    assert detect_cross_cue_shift(source, output) == [
        "Blocks 236-237: 'الخط' appears in both cues - "
        "text may have shifted between them"
    ]


def test_a_repetition_the_source_makes_too_is_left_alone() -> None:
    # Both English cues say "the gate", so both Arabic cues saying "الخط" is
    # the translation being faithful, not text moving.
    source, output = _pair(
        "the gate was closed,", "the gate is right here.",
        BLED_FIRST, BLED_SECOND,
    )
    assert detect_cross_cue_shift(source, output) == []


def test_a_shared_word_too_small_to_matter_is_ignored() -> None:
    # One short word in common is ordinary vocabulary, not content moving.
    source, output = _pair(
        "Where did he go tonight?", "He went home already.",
        "إلى أين ذهب؟", "ذهب إلى المنزل.",
    )
    assert detect_cross_cue_shift(source, output) == []


def test_cues_too_short_to_judge_are_skipped() -> None:
    # "Oh!" against its neighbour shares words by chance all the time.
    source, output = _pair("Oh!", "Oh no.", "الخط", "الخط")
    assert detect_cross_cue_shift(source, output) == []


def test_the_old_length_ratio_alone_no_longer_reports_anything() -> None:
    """A cue that simply came out long or short is not evidence of a shift."""
    source, output = _pair(
        "when the tide turns in the channel,", "gates must close and lock.",
        "متى يتجاوز التعليق أو الفعل الأحمر ويصبح تحرشا صريحا ومؤذيا جدا",
        "نعم.",
    )
    assert detect_cross_cue_shift(source, output) == []


def test_formatting_tags_and_punctuation_do_not_hide_the_run() -> None:
    source, output = _pair(
        "when the tide turns in the channel,", "gates must close early.",
        "{\\i1}" + BLED_FIRST + "{\\i0}", "{\\i1}" + BLED_SECOND + r"\N",
    )
    assert len(detect_cross_cue_shift(source, output)) == 1


def test_an_empty_batch_reports_nothing() -> None:
    assert detect_cross_cue_shift([], []) == []


def test_a_length_mismatch_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        detect_cross_cue_shift([SubtitleBlock(1, "", "a")], [])


# === restore_terminal_punctuation ============================================


def test_a_question_that_came_back_as_a_statement_is_restored() -> None:
    assert restore_terminal_punctuation(
        "Any re-orders today?", "هل هناك بريد اليوم.", "arabic",
    ) == "هل هناك بريد اليوم؟"


def test_an_exclamation_that_came_back_as_a_statement_is_restored() -> None:
    # Real block 284: "That ship has sailed!" lost its punchline mark.
    assert restore_terminal_punctuation(
        "That ship has sailed!", "هذا ما قالت.", "arabic",
    ) == "هذا ما قالت!"


def test_a_latin_target_keeps_the_ascii_question_mark() -> None:
    assert restore_terminal_punctuation(
        "Are you sure?", "¿Estás seguro.", "latin",
    ) == "¿Estás seguro?"


def test_a_matching_mark_is_left_exactly_as_it_is() -> None:
    text = "هل هناك بريد اليوم؟"
    assert restore_terminal_punctuation("Any re-orders today?", text, "arabic") is text


def test_the_source_mark_spelled_the_ascii_way_is_left_alone() -> None:
    """`normalize_rtl_punctuation` runs first; an ASCII mark it deliberately
    left alone is not this pass's to overrule. Mirrors the web tree."""
    text = "هل هناك بريد اليوم?"
    assert restore_terminal_punctuation("Any re-orders today?", text, "arabic") is text


def test_a_mark_frozen_inside_a_url_is_not_re_pointed() -> None:
    # The RTL pass freezes punctuation inside a URL; re-pointing it here would
    # put an Arabic question mark inside the link.
    output = "انظر www.example.com?"
    assert normalize_rtl_punctuation(output, "arabic") == output
    assert restore_terminal_punctuation(
        "See www.example.com?", output, "arabic") == output


def test_a_mark_closing_a_run_of_latin_is_not_re_pointed() -> None:
    output = 'الفيلم "Titanic"?'
    assert normalize_rtl_punctuation(output, "arabic") == output
    assert restore_terminal_punctuation(
        'The movie "Titanic"?', output, "arabic") == output


def test_a_wrong_class_mark_is_still_re_pointed_from_ascii() -> None:
    """The class guard must not swallow a genuine substitution."""
    assert restore_terminal_punctuation(
        "Any re-orders today?", "هل هناك بريد اليوم.", "arabic",
    ) == "هل هناك بريد اليوم؟"


def test_an_output_with_no_terminal_mark_is_left_alone() -> None:
    assert restore_terminal_punctuation(
        "Well?", "حسنا", "arabic") == "حسنا"


def test_a_source_with_no_terminal_mark_restores_nothing() -> None:
    assert restore_terminal_punctuation(
        "and then he", "ثم هو.", "arabic") == "ثم هو."


@pytest.mark.parametrize("output", ["حسنا...", "حسنا…", "حسنا؟!"])
def test_a_multi_mark_ending_is_deliberate_and_kept(output: str) -> None:
    assert restore_terminal_punctuation("Well.", output, "arabic") == output


def test_an_ellipsis_in_the_source_still_names_the_full_stop_class() -> None:
    # The source trails off; a question mark is the wrong class either way.
    assert restore_terminal_punctuation(
        "I doubt that very much...", "لا أعتقد ذلك؟", "arabic") == "لا أعتقد ذلك."


def test_the_mark_is_replaced_inside_a_closing_tag_pair() -> None:
    assert restore_terminal_punctuation(
        "{\\i1}must the gate close?{\\i0}", "{\\i1}يتجاوز الخط.{\\i0}", "arabic",
    ) == "{\\i1}يتجاوز الخط؟{\\i0}"


# === normalize_diacritics ====================================================

# Six marks: over the per-cue threshold that says a cue changed register.
VOCALIZED = "مَرْحَبًا بِكَ"
PLAIN = "مرحبا بك"


def _blocks(*texts: str) -> list[SubtitleBlock]:
    return [SubtitleBlock(i + 1, f"ts{i + 1}", t) for i, t in enumerate(texts)]


def test_the_vocalized_tail_of_an_unvocalized_file_is_stripped() -> None:
    blocks = _blocks(PLAIN, PLAIN, PLAIN, VOCALIZED)
    assert [b.text for b in normalize_diacritics(blocks, "arabic")] == [
        PLAIN, PLAIN, PLAIN, PLAIN]


def test_a_file_vocalized_throughout_is_never_touched() -> None:
    blocks = _blocks(VOCALIZED, VOCALIZED, VOCALIZED)
    assert normalize_diacritics(blocks, "arabic") == blocks


def test_a_cue_with_only_a_mark_or_two_is_below_the_threshold() -> None:
    light = "مرحبًا بك"
    blocks = _blocks(PLAIN, PLAIN, light)
    assert [b.text for b in normalize_diacritics(blocks, "arabic")] == [
        PLAIN, PLAIN, light]


def test_timestamps_and_numbers_survive_the_strip() -> None:
    blocks = _blocks(PLAIN, PLAIN, VOCALIZED)
    stripped = normalize_diacritics(blocks, "arabic")[2]
    assert (stripped.number, stripped.timestamp) == (3, "ts3")


def test_a_non_arabic_script_is_never_touched() -> None:
    blocks = _blocks(PLAIN, PLAIN, VOCALIZED)
    assert normalize_diacritics(blocks, "latin") == blocks
    assert normalize_diacritics(blocks, "hebrew") == blocks


def test_an_empty_file_is_handled() -> None:
    assert normalize_diacritics([], "arabic") == []


def test_diacritic_count_counts_only_the_marks() -> None:
    assert diacritic_count(PLAIN) == 0
    assert diacritic_count(VOCALIZED) == 6


def test_drop_empty_tag_pairs_removes_renderless_runs() -> None:
    """The model sometimes emits {\\i1}{\\i0} around real text; it renders nothing."""
    assert drop_empty_tag_pairs(r"{\i1}{\i0}{\i1}x{\i0}{\i1}{\i0}") == r"{\i1}x{\i0}"
    assert drop_empty_tag_pairs("<i></i><i>x</i>") == "<i>x</i>"
    assert drop_empty_tag_pairs(r"{\i1}x{\i0}") == r"{\i1}x{\i0}"
    assert drop_empty_tag_pairs(r"{\an8}x") == r"{\an8}x"


def test_repair_tags_normalises_a_duplicated_wrap() -> None:
    """Observed on a real run: block 166 came back wrapped three times over."""
    text, ok = repair_tags(r"{\i1}It's over.{\i0}", r"{\i1}{\i0}{\i1}\u062a{\i0}{\i1}{\i0}")
    assert text == r"{\i1}\u062a{\i0}"
    assert ok is True


# === dialogue dashes =========================================================


def test_dialogue_dash_lines_counts_speaker_turns() -> None:
    assert dialogue_dash_lines("- Hello.\n- Hi.") == 2
    assert dialogue_dash_lines("Hello.\nHi.") == 0
    # A hyphen inside a line is not a turn, and neither is an empty line.
    assert dialogue_dash_lines("A well-known face.\n") == 0
    # The tag comes first on the line, the dash still opens it.
    assert dialogue_dash_lines("<i>- Hello.</i>\n- Hi.") == 2
    # The en dash is the other convention.
    assert dialogue_dash_lines("– Hello.\n– Hi.") == 2


def test_equal_dash_counts_are_left_exactly_as_they_are() -> None:
    assert restore_dialogue_dashes("- A.\n- B.", "- س.\n- ص.") == ("- س.\n- ص.", True)
    assert restore_dialogue_dashes("A.\nB.", "س.\nص.") == ("س.\nص.", True)


def test_dropped_dashes_are_restored_line_for_line() -> None:
    """With the line count intact each output line still stands for its own
    source line, so the dash goes back where it came from."""
    assert restore_dialogue_dashes("- A.\n- B.", "س.\nص.") == ("- س.\n- ص.", True)


def test_only_the_lines_that_had_a_dash_get_one_back() -> None:
    text, ok = restore_dialogue_dashes("- A.\nB.", "س.\nص.")
    assert (text, ok) == ("- س.\nص.", True)


def test_a_line_that_kept_its_dash_is_not_given_a_second_one() -> None:
    text, ok = restore_dialogue_dashes("- A.\n- B.", "- س.\nص.")
    assert (text, ok) == ("- س.\n- ص.", True)


def test_a_merged_cue_is_reported_rather_than_guessed_at() -> None:
    # Two speakers on one line: which words belong to whom is unknowable.
    assert restore_dialogue_dashes("- A.\n- B.", "س. ص.") == ("س. ص.", False)


def test_dashes_the_model_invented_are_reported_not_removed() -> None:
    assert restore_dialogue_dashes("A.\nB.", "- س.\n- ص.") == ("- س.\n- ص.", False)


def test_the_restored_dash_goes_outside_a_leading_tag() -> None:
    text, ok = restore_dialogue_dashes("- A.\n- B.", "<i>س.</i>\nص.")
    assert (text, ok) == ("- <i>س.</i>\n- ص.", True)


# === script_leaks ============================================================


def test_script_of_names_letters_and_ignores_everything_else() -> None:
    assert script_of("a") == "latin"
    assert script_of("م") == "arabic"
    assert script_of("京") == "han"
    assert script_of("ア") == "kana"
    assert script_of("한") == "hangul"
    # Digits, punctuation, marks and spaces are neutral.
    assert [script_of(c) for c in "5 .\u064e"] == ["", "", "", ""]


def test_a_target_script_owns_the_writing_systems_it_uses() -> None:
    assert TARGET_SCRIPTS["arabic"] == ("arabic",)
    assert TARGET_SCRIPTS["japanese"] == ("kana", "han")
    assert TARGET_SCRIPTS["korean"] == ("hangul", "han")
    # Nothing can be called alien for a script we cannot classify.
    assert "default" not in TARGET_SCRIPTS


def test_a_script_neither_side_uses_is_reported() -> None:
    assert script_leaks("The crate is from Beijing.", "الصندوق من 北京.",
                        "arabic") == [
        "han characters appear in the translation ('北京')"]


def test_a_script_the_source_itself_used_is_not_a_leak() -> None:
    # Credits, brand names and URLs survive a translation on purpose.
    assert script_leaks("The radio is a Marlin 400.", "الجهاز هو Marlin 400.",
                        "arabic") == []


def test_target_letters_welded_to_another_script_are_reported() -> None:
    assert script_leaks("in the motel lobby", "في بهو المotel.", "arabic") == [
        "'المotel' welds arabic to latin with no separator"]


def test_a_vocalized_word_is_still_seen_as_one_run() -> None:
    """The combining marks stay with the letter they sit on, so a diacritic
    just before the weld cannot hide it."""
    assert script_leaks("in the motel", "في المَotel.", "arabic") == [
        "'المَotel' welds arabic to latin with no separator"]


def test_digits_and_punctuation_never_weld_two_scripts() -> None:
    assert script_leaks("50 signs, Gate 4.", "50 علامة، البوابة 4.",
                        "arabic") == []


def test_a_tag_between_two_scripts_counts_as_a_separator() -> None:
    assert script_leaks("<i>Marlin</i> radio", "<i>Marlin</i>الجهاز",
                        "arabic") == []


def test_a_target_written_in_two_scripts_may_mix_them() -> None:
    # Japanese is kanji and kana; welding them is normal writing.
    assert script_leaks("Hello there.", "こんにちは世界。", "japanese") == []
    assert script_leaks("Hello there.", "こんにちはМир。", "japanese") == [
        "cyrillic characters appear in the translation ('Мир')",
        "'こんにちはМир' welds kana to cyrillic with no separator",
    ]


def test_a_target_whose_script_we_cannot_classify_reports_nothing() -> None:
    assert script_leaks("Hello there.", "ሰላም 北京", "default") == []


def test_a_leak_is_named_once_however_often_it_recurs() -> None:
    leaks = script_leaks("From Beijing to Beijing.", "من 北京 إلى 北京.",
                         "arabic")
    assert leaks == ["han characters appear in the translation ('北京')"]


def test_an_ordinary_translation_reports_nothing() -> None:
    assert script_leaks("Nadia signs the log.", "نادية توقع السجل.",
                        "arabic") == []


def test_a_leak_is_keyed_by_the_script_that_leaked() -> None:
    """The cause groups two cues leaking the same script into one problem,
    however differently their messages read."""
    leaks = find_script_leaks("From Beijing.", "من 北京.", "arabic")
    assert [leak.script for leak in leaks] == ["han"]

    welded = find_script_leaks("in the motel lobby", "في بهو المotel.", "arabic")
    assert [leak.script for leak in welded] == ["latin"]


# === detect_variant_drift ====================================================

# Egyptian function words that cannot occur in Modern Standard Arabic, and the
# MSA rendering of the same lines.
EGYPTIAN = ("مش عارف إزاي", "ده اللي حصل", "عشان كده", "أيوه بتاع الشركة")
STANDARD = ("لا أعرف كيف", "هذا ما حدث", "لهذا السبب", "نعم، ملك الشركة")


def test_a_file_written_in_a_variant_nobody_asked_for_is_reported() -> None:
    drift = detect_variant_drift(_blocks(*EGYPTIAN), "arabic")
    assert drift is not None
    assert (drift.variant, drift.cues, drift.total) == ("Egyptian", 4, 4)


def test_the_warning_names_the_variant_the_share_and_the_way_out() -> None:
    drift = detect_variant_drift(_blocks(*EGYPTIAN), "arabic")
    assert variant_drift_message(drift) == (
        "Output looks like Egyptian rather than the standard written form "
        "(4 of 4 cues). Pass --dialect to ask for it deliberately, or rerun.")


def test_a_standard_file_is_never_reported() -> None:
    assert detect_variant_drift(_blocks(*STANDARD), "arabic") is None


def test_a_marker_inside_a_longer_word_is_not_a_marker() -> None:
    """"مش" sits inside "مشكلة"; a substring test reads a tenth of a clean
    file as colloquial."""
    assert detect_variant_drift(
        _blocks("مشكلة كبيرة", "مشكلة أخرى", "الشمس مشرقة"), "arabic") is None


def test_a_vocalized_marker_still_counts() -> None:
    """Diacritics split a word into single letters that match nothing, so the
    check reads the same text the diacritic pass would leave behind."""
    drift = detect_variant_drift(_blocks("مِش عارف", "مش كده"), "arabic")
    assert drift is not None and drift.cues == 2


def test_a_marker_inside_a_formatting_tag_is_not_counted() -> None:
    assert detect_variant_drift(
        _blocks("<font face='ده'>مرحبا</font>", "أهلا"), "arabic") is None


def test_a_few_colloquial_cues_are_a_register_choice_not_a_drift() -> None:
    # One cue in ten is under the 15% share; six in ten is over it.
    light = _blocks(*(STANDARD * 2 + ("مش كده",)))
    assert detect_variant_drift(light, "arabic") is None
    heavy = _blocks(*(STANDARD + EGYPTIAN + EGYPTIAN))
    assert detect_variant_drift(heavy, "arabic") is not None


def test_a_variant_the_user_asked_for_is_not_a_drift() -> None:
    blocks = _blocks(*EGYPTIAN)
    assert detect_variant_drift(blocks, "arabic", "Egyptian Arabic") is None
    assert detect_variant_drift(blocks, "arabic", "egyptian") is None
    # Asking for one variant does not excuse arriving in another.
    other = detect_variant_drift(blocks, "arabic", "Levantine")
    assert other is not None and other.variant == "Egyptian"


def test_the_strongest_variant_labels_the_union_it_does_not_score_it() -> None:
    blocks = _blocks("مش كده", "ده اللي حصل", "عشان كده", "شو هيك")
    drift = detect_variant_drift(blocks, "arabic")
    assert drift is not None
    # Egyptian carries three of the four, but four cues drifted, and four is
    # the number the warning has to say.
    assert (drift.variant, drift.cues, drift.total) == ("Egyptian", 4, 4)


def test_a_tie_for_the_label_goes_to_the_first_bucket_declared() -> None:
    """Two buckets carrying the same share must not make the warning's wording
    depend on dict ordering."""
    drift = detect_variant_drift(
        _blocks("مش كده", "ده اللي", "شو هيك", "هلق كتير"), "arabic")
    assert drift is not None and drift.variant == "Egyptian"


def test_a_script_with_no_variant_table_is_never_reported() -> None:
    assert detect_variant_drift(_blocks("Hello", "there"), "latin") is None
    assert detect_variant_drift([], "arabic") is None


# Colloquial across the Arabic-speaking world and written in no standard text,
# so they belong to no named dialect: a file can arrive in all of them without
# any one named bucket crossing its share.
PAN_DIALECTAL = ("مين عنده الملف", "هذي مو فكرتي", "لسه بدري", "بس عايز أساعدك",
                 "وين رحت", "ليش كمان")


def test_a_file_that_drifts_without_picking_a_dialect_is_still_reported() -> None:
    """The gap the named buckets left: forms everyone says and nobody writes.
    Spread across Egyptian and Levantine they clear neither threshold, and the
    file reads clean while visibly not being the standard written form."""
    drift = detect_variant_drift(_blocks(*PAN_DIALECTAL), "arabic")
    assert drift is not None
    assert (drift.variant, drift.cues, drift.total) == ("Colloquial", 6, 6)


def test_the_standard_words_the_colloquial_markers_live_inside_are_not_markers() -> None:
    """Every one of these is ordinary Modern Standard Arabic that contains a
    marker as a substring — "بحاجة" (in need of), "تبدو" (it seems), "بسبب"
    (because of), "المحامين" (the lawyers). A substring test reads the graded
    8.0 translation as colloquial, which is worse than no detector at all."""
    assert detect_variant_drift(_blocks(
        "نحن بحاجة إلى المزيد من الوقت",
        "تبدو متعبًا اليوم",
        "تأخرنا بسبب الازدحام",
        "تحدثت إلى المحامين أمس",
        "لم يعد بدون إجابة",
    ), "arabic") is None


def test_asking_for_any_dialect_mutes_the_pan_dialectal_bucket_too() -> None:
    """A file that came back correctly Egyptian is colloquial by definition,
    and these markers are ordinary Egyptian. Scored as a union, leaving the
    generic bucket live would flag the file the user asked for."""
    blocks = _blocks(*PAN_DIALECTAL)
    assert detect_variant_drift(blocks, "arabic") is not None
    assert detect_variant_drift(blocks, "arabic", "Egyptian Arabic") is None
    # A dialect with no bucket of its own is still a dialect being asked for.
    assert detect_variant_drift(blocks, "arabic", "Gulf Arabic") is None


# One file leaving the standard form through three buckets at once. Sized so
# that no bucket is a sixth of the file on its own but the three together are
# more than a quarter of it.
EGYPTIAN_FEW = ("مش عارف إزاي", "ده اللي حصل", "عشان كده")
LEVANTINE_FEW = ("شو هيك",)
COLLOQUIAL_FEW = ("وين رحت", "ليش كمان")
MIXED_TOTAL = 22


def _in_a_file_of(cues: tuple[str, ...]) -> list[SubtitleBlock]:
    """`cues` padded with standard ones to MIXED_TOTAL, so every count below
    is measured against the same 15% bar."""
    return _blocks(*(cues + (STANDARD * 6)[:MIXED_TOTAL - len(cues)]))


def test_the_variant_buckets_are_scored_as_one_union() -> None:
    """The regression: a file drifts through Egyptian, Levantine and the
    generic bucket at once, clears all three thresholds separately, and reads
    as clean while being visibly not the standard written form."""
    for own in (EGYPTIAN_FEW, LEVANTINE_FEW, COLLOQUIAL_FEW):
        assert detect_variant_drift(_in_a_file_of(own), "arabic") is None

    drift = detect_variant_drift(
        _in_a_file_of(EGYPTIAN_FEW + LEVANTINE_FEW + COLLOQUIAL_FEW), "arabic")
    assert drift is not None
    assert (drift.variant, drift.cues, drift.total) == (
        "Egyptian", 6, MIXED_TOTAL)


def test_a_cue_carrying_two_buckets_is_counted_once() -> None:
    """The union is cues, not marker hits: each of these is both Egyptian and
    pan-dialectal, and three such cues are three cues of drift, not six."""
    assert detect_variant_drift(
        _in_a_file_of(("مش وين", "ده ليش", "كده كمان")), "arabic") is None
