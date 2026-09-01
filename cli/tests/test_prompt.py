"""Prompt text and user-message builders: the wiring that puts the glossary,
the read-only previous context and the per-target directives in front of the
model, and the rules the static system prompt must keep carrying."""

from __future__ import annotations

import pytest

from core.prompt import (
    BACK_TRANSLATION_SYSTEM_PROMPT,
    CONTEXT_SYSTEM_PROMPT,
    RECURRING_PHRASES_RULE,
    REVIEW_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_attribution_user_message,
    build_back_translation_user_message,
    build_review_user_message,
    build_scan_user_message,
    build_translate_user_message,
)
from core.srt_parser import serialize_lite
from tests.conftest import make_blocks

WIRE = "1\nHello\n\n2\nWorld\n"


def test_translate_message_names_both_languages() -> None:
    msg = build_translate_user_message("English", "Arabic", WIRE, "", [])
    assert "Translate from English to Arabic:" in msg
    assert msg.endswith(WIRE)


def test_translate_message_omits_the_source_when_auto_detecting() -> None:
    msg = build_translate_user_message("", "Arabic", WIRE, "", [])
    assert "Translate to Arabic:" in msg
    assert "Translate from" not in msg


def test_translate_message_orders_glossary_then_context_then_blocks() -> None:
    prev = make_blocks(2, start=8)
    msg = build_translate_user_message(
        "English", "Arabic", WIRE, "Characters:\n- Alice => أليس (female)", prev)
    assert (msg.index("Glossary for this scene:")
            < msg.index("Previous context")
            < msg.index("Translate from English to Arabic:"))


def test_translate_message_omits_empty_sections() -> None:
    msg = build_translate_user_message("English", "Arabic", WIRE, "", [])
    assert "Glossary for this scene:" not in msg
    assert "Previous context" not in msg


def test_previous_context_blocks_are_flattened_and_marked() -> None:
    prev = make_blocks(1, start=9)
    prev[0].text = "two\nlines"
    msg = build_translate_user_message("English", "Arabic", WIRE, "", prev)
    # Newlines inside a previous block would forge a wire block boundary.
    assert "  [prev #9] two lines" in msg
    assert "9\ntwo" not in msg


def test_review_message_carries_source_and_first_pass() -> None:
    batch = make_blocks(2)
    first_pass = make_blocks(2)
    for b in first_pass:
        b.text = f"T{b.number}"
    msg = build_review_user_message(batch, first_pass, "Characters:\n- Alice")

    assert msg.index("Glossary:") < msg.index("Source blocks:") \
        < msg.index("First-pass translation:")
    assert serialize_lite(batch) in msg
    assert serialize_lite(first_pass) in msg


def test_scan_message_omits_the_source_line_when_auto_detecting() -> None:
    with_source = build_scan_user_message("English", "Arabic", "[1] Hi")
    assert with_source.startswith("Source language: English\n")
    without = build_scan_user_message("", "Arabic", "[1] Hi")
    assert without.startswith("Target language: Arabic")
    assert "Source language" not in without


def test_attribution_message_lists_the_roster_then_the_scene() -> None:
    msg = build_attribution_user_message("- Alice (F)\n- Bob (M)",
                                         ["[10] Hi", "[11] Bye"])
    assert msg.index("Characters:") < msg.index("Scene:")
    assert msg.endswith("[10] Hi\n[11] Bye")


# === Per-request directives ==================================================


def test_the_line_limit_rule_carries_the_targets_own_norms() -> None:
    msg = build_translate_user_message("English", "Japanese", WIRE, "", [],
                                       max_chars=16, max_lines=2)
    assert ("- Keep each line at or under 16 characters and never exceed 2 "
            "lines per block; prefer tighter phrasing over a longer line."
            ) in msg


def test_the_line_limit_rule_defaults_to_the_latin_norms() -> None:
    msg = build_translate_user_message("English", "Arabic", WIRE, "", [])
    assert "at or under 42 characters and never exceed 2 lines" in msg


def test_the_static_system_prompt_no_longer_names_a_line_length() -> None:
    # The limit is per-target, so it rides in the user message and the system
    # prompt stays byte-identical across requests (and cacheable).
    assert "42" not in SYSTEM_PROMPT
    assert "characters or fewer per line" not in SYSTEM_PROMPT


def test_the_system_prompt_forbids_moving_words_between_blocks() -> None:
    assert ("- Never move words between blocks. If a sentence continues into "
            "the next block, translate only the part in THIS block, even if it "
            "reads incomplete.") in SYSTEM_PROMPT
    # Immediately after the independence rule it reinforces.
    assert SYSTEM_PROMPT.index("Translate each block independently") \
        < SYSTEM_PROMPT.index("Never move words between blocks")


def test_the_system_prompt_names_the_tags_that_were_dropped() -> None:
    assert ("- HTML tags, music symbols, formatting tags (\\N, {\\an8}, "
            "{\\i1}, {\\i0}, <i>, </i>) - copy every tag through in the same "
            "position, opening and closing") in SYSTEM_PROMPT


@pytest.mark.parametrize("formality", ["formal", "informal"])
def test_an_explicit_formality_adds_one_register_line(formality: str) -> None:
    msg = build_translate_user_message("English", "German", WIRE, "", [],
                                       formality=formality)
    assert f"Register: use {formality} address throughout." in msg


def test_auto_formality_says_nothing_and_leaves_it_to_the_source() -> None:
    msg = build_translate_user_message("English", "German", WIRE, "", [],
                                       formality="auto")
    assert "Register: use" not in msg


def test_a_dialect_names_the_variant_to_use() -> None:
    msg = build_translate_user_message("English", "Arabic", WIRE, "", [],
                                       dialect="Egyptian Arabic")
    assert "Target variant: Egyptian Arabic. Use it consistently." in msg


@pytest.mark.parametrize("dialect", ["", "   "])
def test_a_blank_dialect_adds_nothing(dialect: str) -> None:
    msg = build_translate_user_message("English", "Arabic", WIRE, "", [],
                                       dialect=dialect)
    assert "Target variant" not in msg


def test_a_dialect_is_stripped_before_it_reaches_the_model() -> None:
    msg = build_translate_user_message("English", "Arabic", WIRE, "", [],
                                       dialect="  Brazilian Portuguese  ")
    assert "Target variant: Brazilian Portuguese. Use it consistently." in msg


def test_directives_sit_between_the_context_and_the_blocks() -> None:
    prev = make_blocks(1, start=9)
    msg = build_translate_user_message(
        "English", "Arabic", WIRE, "Characters:\n- Alice", prev,
        formality="formal", dialect="Egyptian Arabic")
    assert (msg.index("Previous context")
            < msg.index("- Keep each line at or under")
            < msg.index("Register: use formal")
            < msg.index("Target variant: Egyptian Arabic")
            < msg.index("Translate from English to Arabic:"))
    # The wire stays last so nothing can be mistaken for an input block.
    assert msg.endswith(WIRE)


def test_the_scan_uses_a_given_dialect_instead_of_guessing_the_register() -> None:
    msg = build_scan_user_message("English", "Arabic", "[1] Hi",
                                  dialect="Egyptian Arabic")
    assert ("Target variant: Egyptian Arabic. Use it as the <register> "
            "instead of inferring one.") in msg
    assert msg.endswith("[1] Hi")


def test_the_scan_infers_the_register_when_no_dialect_is_given() -> None:
    assert "Target variant" not in build_scan_user_message(
        "English", "Arabic", "[1] Hi")


# === Recurring phrases and idioms ============================================


def test_the_scan_message_lists_the_recurring_phrases_before_the_blocks() -> None:
    msg = build_scan_user_message(
        "English", "Arabic", "[1] Hi",
        phrases=["safety briefing", "that ship has sailed"])
    assert (RECURRING_PHRASES_RULE + "\n- safety briefing\n"
            "- that ship has sailed") in msg
    assert msg.index(RECURRING_PHRASES_RULE) < msg.index("[1] Hi")
    assert msg.endswith("[1] Hi")


def test_the_scan_message_says_nothing_when_no_phrase_recurs() -> None:
    assert "Recurring phrases" not in build_scan_user_message(
        "English", "Arabic", "[1] Hi", phrases=[])


def test_the_recurring_phrase_rule_asks_for_one_rendering_each() -> None:
    assert RECURRING_PHRASES_RULE == (
        "Recurring phrases - give each one ONE target rendering and use it "
        "everywhere:")


def test_a_dialect_and_the_phrases_both_reach_the_scan() -> None:
    msg = build_scan_user_message("English", "Arabic", "[1] Hi",
                                  dialect="Egyptian Arabic",
                                  phrases=["safety briefing"])
    assert msg.index("Target variant:") < msg.index(RECURRING_PHRASES_RULE)


def test_the_system_prompt_tells_the_model_to_translate_idioms_by_meaning() -> None:
    assert ("- Idioms, jokes and set phrases: translate the MEANING, never "
            "word by word. If the target has an equivalent expression, use it; "
            "if not, say plainly what the speaker means.") in SYSTEM_PROMPT
    # It follows the register rule, which it qualifies: faithful, not literal.
    assert SYSTEM_PROMPT.index("Translate faithfully") \
        < SYSTEM_PROMPT.index("Idioms, jokes and set phrases")


def test_the_reviewer_may_fix_a_calqued_idiom() -> None:
    assert ("- A literal word-for-word rendering of an idiom or set phrase, "
            "where the meaning is lost.") in REVIEW_SYSTEM_PROMPT


def test_the_scan_is_asked_for_a_glossary_worth_a_whole_episode() -> None:
    assert "20 characters, 25 terms, 40 scenes, 4 notes" in CONTEXT_SYSTEM_PROMPT


def test_the_scan_asks_for_idioms_in_their_own_section() -> None:
    assert "<idioms>\nSOURCE_IDIOM => TARGET_EQUIVALENT\n</idioms>" \
        in CONTEXT_SYSTEM_PROMPT
    # After <terms>, before <scenes>: the order the parser and the rules follow.
    assert (CONTEXT_SYSTEM_PROMPT.index("<terms>")
            < CONTEXT_SYSTEM_PROMPT.index("<idioms>")
            < CONTEXT_SYSTEM_PROMPT.index("<scenes>"))
    assert "Reply with all six sections" in CONTEXT_SYSTEM_PROMPT


def test_the_idiom_rule_says_what_an_idiom_is_and_caps_the_list() -> None:
    assert ("- <idioms>: source idioms, set phrases and jokes that must NOT be "
            "translated word by word. Give the exact words a subtitle would "
            "use in the target language — never a definition or an explanation "
            "of the idiom. If no equivalent exists, give the shortest plain "
            "rendering of what the speaker means. Include up to 15."
            ) in CONTEXT_SYSTEM_PROMPT


def test_the_idiom_rule_forbids_a_definition_outright() -> None:
    """A definition pasted over a punchline was the worst defect the graded
    run found: 93 characters at 56 cps, physically unreadable."""
    rule = next(line for line in CONTEXT_SYSTEM_PROMPT.splitlines()
                if line.startswith("- <idioms>:"))
    assert "never a definition or an explanation" in rule


# === Register: the source's own coarseness ===================================

# The graded run's weakest aspect was idiom at 6.2, and the recurring cause was
# the pipeline softening the source: "bone your mum" came back as "dance with


def test_the_reviewer_is_still_conservative() -> None:
    """A fifth licence to change text is only safe while the reviewer's default
    is still to output the first pass untouched."""
    assert "DEFAULT: output the first-pass UNCHANGED." in REVIEW_SYSTEM_PROMPT
    assert "If uncertain, keep the block verbatim. Do NOT rephrase, restyle, " \
        'or "polish".' in REVIEW_SYSTEM_PROMPT


def test_the_scan_register_names_the_variant_and_nothing_else() -> None:
    """Asking this line for the variant AND how coarse the dialogue is cost a
    measured regression: the scan started reporting informality and stopped
    naming the variant, and because the register line is injected into every
    batch, a whole 372-cue file drifted out of Modern Standard Arabic into
    colloquial. How strong the language may be is SYSTEM_PROMPT's job; this
    line steers the variant, and only the variant."""
    assert ('- <register>: name the exact target variant (e.g. "Modern '
            'Standard Arabic, neutral", "Brazilian Portuguese, casual", '
            '"Japanese, polite です/ます form"). Pick one for the whole file.'
            ) in CONTEXT_SYSTEM_PROMPT
    rule = next(line for line in CONTEXT_SYSTEM_PROMPT.splitlines()
                if line.startswith("- <register>:"))
    assert "coarse" not in rule and "profane" not in rule


def test_the_review_message_carries_the_targets_grammar_checks() -> None:
    batch = make_blocks(1)
    msg = build_review_user_message(batch, batch, "Characters:\n- Alice",
                                    "Keep one speech level throughout.")
    assert "Target-language checks: Keep one speech level throughout." in msg
    # Between the glossary and the blocks, where the reviewer reads it before
    # it sees anything to change.
    assert (msg.index("Glossary:") < msg.index("Target-language checks:")
            < msg.index("Source blocks:"))


@pytest.mark.parametrize("note", ["", "   "])
def test_a_script_with_no_grammar_note_adds_no_line(note: str) -> None:
    batch = make_blocks(1)
    assert "Target-language checks" not in build_review_user_message(
        batch, batch, "g", note)


def test_the_back_translation_prompt_asks_for_a_literal_round_trip() -> None:
    assert "Translate literally, block by block." in BACK_TRANSLATION_SYSTEM_PROMPT
    assert "Same number of blocks, same block numbers." \
        in BACK_TRANSLATION_SYSTEM_PROMPT
    # It is a measurement, not a deliverable: nothing about style or timing.
    assert "polish" in BACK_TRANSLATION_SYSTEM_PROMPT


def test_the_back_translation_message_ends_with_the_wire() -> None:
    msg = build_back_translation_user_message("English", WIRE)
    assert msg.startswith("Translate back to English:")
    assert msg.endswith(WIRE)


def test_the_register_rule_is_the_original_one_line_form() -> None:
    """A stronger "never soften" rule was measured on a graded 372-cue benchmark
    and made things worse: softening rose 13 -> 18 and fluency fell 6.4 -> 5.9,
    with zero restorations across the ten crude cues it targeted."""
    assert ("- Translate faithfully: profanity, slurs, slang — match the "
            "original register.") in SYSTEM_PROMPT
    assert "full strength" not in SYSTEM_PROMPT
    assert "Register that was softened" not in REVIEW_SYSTEM_PROMPT
