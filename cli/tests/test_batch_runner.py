"""Retry budgets, the recursive split, the review pass, and the
deterministic repair applied to a batch once it validates.

Every test replaces `call_chat_api`, so nothing here touches the network.
"""

from __future__ import annotations

import httpx
import pytest

import core.batch_runner as br
from core.chat_client import FatalApiError, RetryableApiError
from core.constants import ATTEMPTS_BEFORE_SPLIT
from core.context_pass import FileContext, TermHint
from core.srt_parser import SubtitleBlock, parse_lite, serialize_lite
from tests.conftest import make_blocks, run_async


def _echo(user_message: str, *, drop_last: bool = False, shift: int = 0) -> str:
    """A model reply built from the batch in `user_message`: every block
    translated to `T<n>`, optionally dropping one block or shifting numbers."""
    blocks = parse_lite(user_message)
    if drop_last:
        blocks = blocks[:-1]
    return serialize_lite([
        SubtitleBlock(b.number + shift, "", f"T{b.number}") for b in blocks
    ])


def _install_chat(monkeypatch, handler, providers: list | None = None):
    """Replace call_chat_api with `handler(system_prompt, user_message, call_no)`.
    Returns the (system_prompt, user_message) list it recorded; `providers`, if
    given, collects the endpoint each call was routed to."""
    calls: list[tuple[str, str]] = []

    async def fake_call(client, system_prompt, user_message, cfg, max_tokens,
                        provider=None):
        calls.append((system_prompt, user_message))
        if providers is not None:
            providers.append(provider)
        return handler(system_prompt, user_message, len(calls))

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    return calls


def _http_error(status: int, body: str = "", headers: dict | None = None):
    request = httpx.Request("POST", "http://localhost/v1/chat/completions")
    response = httpx.Response(status, text=body, headers=headers or {},
                              request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request,
                                 response=response)


# === Output token budget =====================================================


def test_output_token_budget_scales_with_payload_size() -> None:
    small = make_blocks(2)
    large = [SubtitleBlock(b.number, b.timestamp, b.text * 200) for b in small]
    assert br.output_token_budget(large) > br.output_token_budget(small)
    assert br.output_token_budget([]) >= 300


# === translate_batch_with_retry ==============================================


def test_clean_reply_is_accepted_with_one_call(monkeypatch, cfg, no_sleep) -> None:
    batch = make_blocks(3)
    calls = _install_chat(monkeypatch, lambda sys_p, user, n: _echo(user))

    out = run_async(br.translate_batch_with_retry(None, 0, batch, cfg))

    assert len(calls) == 1
    assert [b.number for b in out.blocks] == [1, 2, 3]
    assert [b.text for b in out.blocks] == ["T1", "T2", "T3"]
    # Timestamps come from the input, never from the wire.
    assert [b.timestamp for b in out.blocks] == [b.timestamp for b in batch]
    assert out.flags == []
    assert cfg.calls.translate == 1


def test_fenced_reply_still_validates(monkeypatch, cfg, no_sleep) -> None:
    batch = make_blocks(2)
    calls = _install_chat(
        monkeypatch, lambda sys_p, user, n: f"```srt\n{_echo(user)}```")

    out = run_async(br.translate_batch_with_retry(None, 0, batch, cfg))

    assert len(calls) == 1
    assert [b.text for b in out.blocks] == ["T1", "T2"]


def test_nine_for_ten_reply_splits_the_batch(monkeypatch, cfg, no_sleep) -> None:
    batch = make_blocks(10)

    def handler(sys_p, user, n):
        # The whole batch keeps losing a block; each half answers correctly.
        return _echo(user, drop_last=len(parse_lite(user)) == 10)

    calls = _install_chat(monkeypatch, handler)

    out = run_async(br.translate_batch_with_retry(None, 0, batch, cfg))

    # ATTEMPTS_BEFORE_SPLIT failures, then one call per half.
    assert len(calls) == ATTEMPTS_BEFORE_SPLIT + 2
    assert [b.number for b in out.blocks] == list(range(1, 11))
    assert [b.text for b in out.blocks] == [f"T{n}" for n in range(1, 11)]
    # The right half is given the left half's tail as read-only context.
    right_call = calls[-1][1]
    assert "[prev #4]" in right_call and "[prev #5]" in right_call


def test_split_reviews_the_reassembled_batch_once(monkeypatch, cfg, no_sleep) -> None:
    from core.context_pass import CharacterHint, FileContext

    cfg.review = True
    batch = make_blocks(4)
    context = FileContext(characters=[CharacterHint("line", "line", "female")])

    def handler(sys_p, user, n):
        if sys_p is br.REVIEW_SYSTEM_PROMPT:
            return serialize_lite([
                SubtitleBlock(b.number, "", f"R{b.number}") for b in batch])
        return _echo(user, drop_last=len(parse_lite(user)) == 4)

    calls = _install_chat(monkeypatch, handler)

    out = run_async(br.translate_batch_with_retry(None, 0, batch, cfg, context))

    reviews = [c for c in calls if c[0] is br.REVIEW_SYSTEM_PROMPT]
    assert len(reviews) == 1
    assert [b.text for b in out.blocks] == ["R1", "R2", "R3", "R4"]
    assert cfg.calls.review == 1


def test_renumbered_reply_never_passes_validation(monkeypatch, cfg, no_sleep) -> None:
    # Same count, same shape, every number shifted by one. Validation runs on
    # the model's own numbers, so this cannot slip through.
    batch = make_blocks(2)
    _install_chat(monkeypatch, lambda sys_p, user, n: _echo(user, shift=1))

    with pytest.raises(br.FileTranslationError) as excinfo:
        run_async(br.translate_batch_with_retry(None, 0, batch, cfg))
    assert "number mismatch" in str(excinfo.value).lower()


def test_429_storm_uses_the_full_retry_budget(monkeypatch, cfg, no_sleep) -> None:
    # Regression: a splittable batch used to get only ATTEMPTS_BEFORE_SPLIT
    # attempts for transport failures, so two 429s threw away the whole file.
    cfg.max_retries = 4
    batch = make_blocks(10)

    def handler(sys_p, user, n):
        raise _http_error(429, "rate limited", {"retry-after": "3"})

    calls = _install_chat(monkeypatch, handler)

    with pytest.raises(br.FileTranslationError) as excinfo:
        run_async(br.translate_batch_with_retry(None, 0, batch, cfg))
    assert len(calls) == cfg.max_retries
    assert "failed after 4 attempt(s)" in str(excinfo.value)
    assert "HTTP 429" in str(excinfo.value)
    # Retry-After is honoured, and the last attempt doesn't sleep.
    assert no_sleep == [3.0, 3.0, 3.0]


def test_transient_500_then_success(monkeypatch, cfg, no_sleep) -> None:
    batch = make_blocks(2)

    def handler(sys_p, user, n):
        if n == 1:
            raise _http_error(500, "upstream boom")
        return _echo(user)

    calls = _install_chat(monkeypatch, handler)

    out = run_async(br.translate_batch_with_retry(None, 0, batch, cfg))
    assert len(calls) == 2
    assert [b.text for b in out.blocks] == ["T1", "T2"]


def test_truncated_response_is_retried(monkeypatch, cfg, no_sleep) -> None:
    batch = make_blocks(2)

    def handler(sys_p, user, n):
        if n == 1:
            raise RetryableApiError("response truncated (finish_reason=length)")
        return _echo(user)

    calls = _install_chat(monkeypatch, handler)

    out = run_async(br.translate_batch_with_retry(None, 0, batch, cfg))
    assert len(calls) == 2
    assert [b.text for b in out.blocks] == ["T1", "T2"]


def test_a_fatal_provider_error_fails_the_batch_without_retrying(
    monkeypatch, cfg, no_sleep,
) -> None:
    """A model that answered with nothing but reasoning at four times the
    budget will answer the same way on the fifth ask."""
    batch = make_blocks(4)

    def handler(sys_p, user, n):
        raise FatalApiError("the model returned no text at 1200 tokens")

    calls = _install_chat(monkeypatch, handler)

    with pytest.raises(br.FileTranslationError) as excinfo:
        run_async(br.translate_batch_with_retry(None, 0, batch, cfg))
    assert len(calls) == 1
    message = str(excinfo.value)
    assert "block 1" in message and "no text at 1200 tokens" in message
    assert no_sleep == []


def test_non_retryable_400_fails_immediately(monkeypatch, cfg, no_sleep) -> None:
    batch = make_blocks(4)

    def handler(sys_p, user, n):
        raise _http_error(400, "unknown model 'x'")

    calls = _install_chat(monkeypatch, handler)

    with pytest.raises(br.FileTranslationError) as excinfo:
        run_async(br.translate_batch_with_retry(None, 0, batch, cfg))
    assert len(calls) == 1
    message = str(excinfo.value)
    assert "HTTP 400" in message and "unknown model" in message
    assert no_sleep == []


def test_context_overflow_400_names_the_flags_to_lower(monkeypatch, cfg, no_sleep) -> None:
    batch = make_blocks(4)

    def handler(sys_p, user, n):
        raise _http_error(400, "maximum context length is 4096 tokens")

    calls = _install_chat(monkeypatch, handler)

    with pytest.raises(br.FileTranslationError) as excinfo:
        run_async(br.translate_batch_with_retry(None, 0, batch, cfg))
    assert len(calls) == 1
    message = str(excinfo.value)
    assert "--batch-size" in message and "--scan-budget" in message
    assert str(cfg.batch_size) in message


def test_singleton_batch_exhausts_max_retries_on_validation(
    monkeypatch, cfg, no_sleep,
) -> None:
    # A single block cannot be split, so validation failures fall back to the
    # full retry budget rather than the (impossible) split.
    cfg.max_retries = 3
    batch = make_blocks(1)
    calls = _install_chat(monkeypatch, lambda sys_p, user, n: "not a block at all")

    with pytest.raises(br.FileTranslationError):
        run_async(br.translate_batch_with_retry(None, 0, batch, cfg))
    assert len(calls) == cfg.max_retries


# === Deterministic repair of an accepted batch ===============================


# One long Arabic rendering of a two-line English cue, reused below.
LONG_AR = "متى يتجاوز التعليق أو الفعل الخط الأحمر ويصبح شيئا آخر"


def _finalize(cfg, pairs: list[tuple[str, str]], file_context=None):
    """Run the post-validation repair over (source text, model output) pairs.
    Returns the repaired texts and everything the run warned about."""
    warnings: list[str] = []
    cfg.warn = warnings.append
    source = [SubtitleBlock(i + 1, f"ts{i + 1}", src)
              for i, (src, _) in enumerate(pairs)]
    output = [SubtitleBlock(i + 1, "", out)
              for i, (_, out) in enumerate(pairs)]
    result = br._finalize_batch(source, output, cfg, True, file_context)
    assert [b.number for b in result.blocks] == [b.number for b in source]
    assert [b.timestamp for b in result.blocks] == ["" for _ in source]
    # Every warning a batch reports comes from a flag, in the same order.
    assert [f.message for f in result.flags] == warnings
    return [b.text for b in result.blocks], warnings


def test_a_dropped_italic_pair_is_restored_without_a_warning(cfg) -> None:
    texts, warnings = _finalize(cfg, [("{\\i1}Hello there{\\i0}", "مرحبا بك")])
    assert texts == ["{\\i1}مرحبا بك{\\i0}"]
    assert warnings == []


def test_an_unrepairable_tag_change_warns_and_keeps_the_translation(cfg) -> None:
    texts, warnings = _finalize(cfg, [("A <i>b</i> c", "س ص ع")])
    assert texts == ["س ص ع"]
    assert warnings == ["Block 1: formatting tags changed (<i>,</i> -> )"]


def test_a_tag_warning_names_both_inventories(cfg) -> None:
    _, warnings = _finalize(cfg, [("<i>a</i>", "<b>x</b>")])
    assert warnings == ["Block 1: formatting tags changed (<i>,</i> -> <b>,</b>)"]


def test_the_source_line_count_is_restored(cfg) -> None:
    source = "it is not always easy to notice\nwhen the tide turns in the channel"
    texts, warnings = _finalize(cfg, [(source, LONG_AR)])
    assert texts[0].count("\n") == 1
    assert warnings == []


def test_an_extra_line_is_collapsed_back_to_the_source_count(cfg) -> None:
    # The target is always the source's line count, in both directions.
    texts, _ = _finalize(cfg, [("one source line", "مرحبا بك\nفي هذا المكان")])
    assert texts == ["مرحبا بك في هذا المكان"]


def test_tags_are_restored_before_the_reflow_so_the_pair_is_not_split(cfg) -> None:
    source = "{\\i1}it is not always easy\nto notice when the tide turns{\\i0}"
    texts, _ = _finalize(cfg, [(source, LONG_AR)])
    first, second = texts[0].split("\n")
    assert first.startswith("{\\i1}") and second.endswith("{\\i0}")


def test_no_reflow_leaves_the_line_count_alone_but_still_fixes_punctuation(cfg) -> None:
    cfg.reflow = False
    source = "one line\nsecond line"
    texts, _ = _finalize(cfg, [(source, "مرحبا, كيف حالك?")])
    assert texts == ["مرحبا، كيف حالك؟"]


def test_arabic_punctuation_is_repointed_for_an_arabic_target(cfg) -> None:
    texts, _ = _finalize(cfg, [("Hello, how are you?", "مرحبا, كيف حالك?")])
    assert texts == ["مرحبا، كيف حالك؟"]


def test_a_latin_target_keeps_ascii_punctuation(cfg) -> None:
    cfg.target_lang = "Spanish"
    texts, _ = _finalize(cfg, [("Hello, how are you?", "Hola, ¿cómo estás?")])
    assert texts == ["Hola, ¿cómo estás?"]


def test_the_target_language_picks_the_line_length(cfg) -> None:
    # Japanese norms cap a line at 16 characters, not the Latin 42.
    cfg.target_lang = "Japanese"
    long_ja = "これはとても長い一行の字幕です。もう一度お願いします。"
    texts, _ = _finalize(cfg, [("a" * 30, long_ja)])
    assert max(len(line) for line in texts[0].split("\n")) <= 16


def test_max_line_chars_overrides_the_script_default(cfg) -> None:
    line = " ".join(["word"] * 6)  # 29 chars: under Arabic's 42, over 20
    assert _finalize(cfg, [("short", line)])[0] == [line]

    cfg.max_line_chars = 20
    texts, _ = _finalize(cfg, [("short", line)])
    assert texts == ["word word word\nword word word"]


def test_dropped_speaker_dashes_are_restored_before_the_reflow(cfg) -> None:
    # Restored first, so the reflow sees a dialogue cue and leaves it alone.
    texts, warnings = _finalize(
        cfg, [("- Are you awake?\n- Barely.", "هل أنت مستيقظ؟\nبالكاد.")])
    assert texts == ["- هل أنت مستيقظ؟\n- بالكاد."]
    assert warnings == []


def test_a_cue_that_lost_both_speakers_is_warned_about(cfg) -> None:
    texts, warnings = _finalize(
        cfg, [("- Are you awake?\n- Barely.", "هل أنت مستيقظ؟ بالكاد.")])
    # No dash is invented: which words belong to which speaker is no longer
    # knowable, so the reflow only puts the source's line count back.
    assert texts == ["هل أنت مستيقظ؟\nبالكاد."]
    assert warnings == ["Block 1: speaker dashes changed (2 -> 0)"]


def test_the_dash_flag_tells_the_retry_what_to_put_back(cfg) -> None:
    result = br._finalize_batch(
        [SubtitleBlock(1, "ts1", "- A?\n- B.")],
        [SubtitleBlock(1, "", "س؟ ص.")], cfg, False)
    assert [f.problem for f in result.flags] == [
        "block 1: the source cue opens 2 line(s) with a dialogue dash and "
        "the translation opens 0; keep one line per speaker"]


def test_foreign_script_left_in_the_output_is_warned_about(cfg) -> None:
    texts, warnings = _finalize(
        cfg, [("The crate is from Beijing.", "الصندوق من 北京.")])
    # A report and a retry hint: the cue still ships.
    assert texts == ["الصندوق من 北京."]
    assert warnings == [
        "Block 1: han characters appear in the translation ('北京')"]


def test_a_word_welded_to_latin_is_warned_about(cfg) -> None:
    _, warnings = _finalize(
        cfg, [("He is in the motel lobby.", "هو في المotel.")])
    assert warnings == [
        "Block 1: 'المotel' welds arabic to latin with no separator"]


def test_two_leaks_in_one_cue_are_one_flag(cfg) -> None:
    # One cue is one thing to look at; the first problem names it.
    result = br._finalize_batch(
        [SubtitleBlock(1, "ts1", "From Beijing to the depot.")],
        [SubtitleBlock(1, "", "من 北京 إلى المotel.")], cfg, False)
    assert [f.problem for f in result.flags] == [
        "block 1: han characters appear in the translation ('北京')"]


def test_the_leak_check_reads_the_text_the_file_will_ship(cfg) -> None:
    # The wrapping pair is restored first; a tag is a separator, not a weld.
    texts, warnings = _finalize(cfg, [("<i>a Marlin radio</i>", "جهاز Marlin")])
    assert texts == ["<i>جهاز Marlin</i>"]
    assert warnings == []


def test_a_suspected_cross_cue_shift_is_reported_once(cfg) -> None:
    _, warnings = _finalize(cfg, [
        ("when the tide turns in the channel,", "متى يتجاوز التعليق أو الفعل الخط"),
        ("gates must close early.", "الخط."),
    ])
    assert warnings == [
        "Blocks 1-2: 'الخط' appears in both cues - "
        "text may have shifted between them"
    ]


def test_the_sources_sentence_mark_is_restored_after_the_rtl_mapping(cfg) -> None:
    texts, warnings = _finalize(cfg, [("Any re-orders today?", "هل هناك بريد اليوم.")])
    assert texts == ["هل هناك بريد اليوم؟"]
    assert warnings == []


def test_a_glossary_term_the_model_ignored_is_reported(cfg) -> None:
    context = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    _, warnings = _finalize(
        cfg,
        [("We had a safety briefing session.", "حضرنا ندوة عن حصة أخرى.")],
        context,
    )
    assert warnings == [
        "Block 1: glossary term 'safety briefing' was not rendered as "
        "'جلسة السلامة'"
    ]


def test_a_term_the_model_did_render_is_not_reported(cfg) -> None:
    context = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    _, warnings = _finalize(
        cfg,
        [("A safety briefing session.", "ندوة عن جلسة السلامة.")],
        context,
    )
    assert warnings == []


def test_a_term_absent_from_the_batch_is_not_reported(cfg) -> None:
    context = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    _, warnings = _finalize(cfg, [("Good morning.", "صباح الخير.")], context)
    assert warnings == []


def test_a_multi_word_term_split_over_a_line_break_still_counts(cfg) -> None:
    # Whole-word search over the joined batch text: the phrase spans the break.
    context = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    _, warnings = _finalize(
        cfg, [("a safety\nbriefing case", "قضية إغارة\nجنسية")], context)
    assert len(warnings) == 1
    assert "safety briefing" in warnings[0]


def test_a_split_half_leaves_the_glossary_report_to_its_root(cfg) -> None:
    context = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    warnings: list[str] = []
    cfg.warn = warnings.append
    source = [SubtitleBlock(1, "ts1", "A safety briefing session.")]
    output = [SubtitleBlock(1, "", "ندوة عن الإغارة.")]
    br._finalize_batch(source, output, cfg, False, context)
    assert warnings == []


def test_repair_never_fails_the_batch(monkeypatch, cfg, no_sleep) -> None:
    # Tag loss is cosmetic: the file must still ship.
    batch = [SubtitleBlock(1, "ts1", "A <i>b</i> c")]
    warnings: list[str] = []
    cfg.warn = warnings.append
    _install_chat(monkeypatch, lambda sys_p, user, n: "1\nس ص ع")

    out = run_async(br.translate_batch_with_retry(None, 0, batch, cfg))

    assert [b.text for b in out.blocks] == ["س ص ع"]
    assert any("formatting tags changed" in w for w in warnings)
    assert [f.problem for f in out.flags] == [
        "block 1: the formatting tags <i>...</i> were dropped"]


def test_a_split_half_leaves_the_reporting_to_its_root(monkeypatch, cfg, no_sleep) -> None:
    # Otherwise a split batch would warn twice about the same block.
    batch = [SubtitleBlock(n, f"ts{n}", "A <i>b</i> c is a long enough line")
             for n in range(1, 5)]
    warnings: list[str] = []
    cfg.warn = warnings.append

    def handler(sys_p, user, n):
        return _echo(user, drop_last=len(parse_lite(user)) == 4)

    _install_chat(monkeypatch, handler)
    run_async(br.translate_batch_with_retry(None, 0, batch, cfg))

    tag_warnings = [w for w in warnings if "formatting tags changed" in w]
    assert len(tag_warnings) == 4


def test_the_user_message_carries_the_targets_own_line_limit(
    monkeypatch, cfg, no_sleep,
) -> None:
    cfg.target_lang = "Japanese"
    calls = _install_chat(monkeypatch, lambda sys_p, user, n: _echo(user))

    run_async(br.translate_batch_with_retry(None, 0, make_blocks(2), cfg))

    assert "at or under 16 characters" in calls[0][1]
    assert "never exceed 2 lines" in calls[0][1]


# === Flags ===================================================================


def test_a_dropped_tag_is_flagged_in_the_words_the_retry_is_shown(cfg) -> None:
    _, warnings = _finalize(cfg, [("A <i>b</i> c", "س ص ع")])
    result = br._finalize_batch(
        [SubtitleBlock(1, "ts1", "A <i>b</i> c")],
        [SubtitleBlock(1, "", "س ص ع")], cfg, False)
    assert warnings == ["Block 1: formatting tags changed (<i>,</i> -> )"]
    assert [f.problem for f in result.flags] == [
        "block 1: the formatting tags <i>...</i> were dropped"]


def test_a_swapped_tag_is_flagged_as_changed_not_dropped(cfg) -> None:
    result = br._finalize_batch(
        [SubtitleBlock(1, "ts1", "{\\i1}a{\\i0}")],
        [SubtitleBlock(1, "", "{\\b1}x{\\b0}")], cfg, False)
    assert [f.problem for f in result.flags] == [
        "block 1: the formatting tags {\\i1}...{\\i0} were changed to "
        "{\\b1}...{\\b0}"]


def test_bleeding_and_drift_reach_the_retry_as_separate_problems(cfg) -> None:
    context = FileContext(terms=[TermHint("the channel", "الخط الأحمر")])
    source = [SubtitleBlock(1, "ts1", "when the tide turns in the channel,"),
              SubtitleBlock(2, "ts2", "it becomes dangerous.")]
    output = [SubtitleBlock(1, "", "متى يتجاوز التعليق أو الفعل الخط"),
              SubtitleBlock(2, "", "الخط.")]
    result = br._finalize_batch(source, output, cfg, False, context)
    assert [f.problem for f in result.flags] == [
        "block 1: the glossary term 'the channel' was not rendered as "
        "'الخط الأحمر'",
        "block 1: text from the next cue appears here ('الخط')",
    ]
    # Every flag names the block a person would open to check it.
    assert {f.block for f in result.flags} == {1}


def test_a_clean_batch_carries_no_flags(cfg) -> None:
    result = br._finalize_batch(
        [SubtitleBlock(1, "ts1", "Hello there.")],
        [SubtitleBlock(1, "", "مرحبا بك.")], cfg, True)
    assert result.flags == []


# === Causes ==================================================================

# The cause says WHAT went wrong, never where, so the repair pass can count one
# term drifting across sixteen batches as one problem instead of sixteen.


def test_a_dropped_tag_is_caused_by_tags(cfg) -> None:
    result = br._finalize_batch(
        [SubtitleBlock(1, "ts1", "A <i>b</i> c")],
        [SubtitleBlock(1, "", "س ص ع")], cfg, False)
    assert [f.cause for f in result.flags] == ["tags"]


def test_merged_speaker_turns_are_caused_by_dashes(cfg) -> None:
    result = br._finalize_batch(
        [SubtitleBlock(1, "ts1", "- A?\n- B.")],
        [SubtitleBlock(1, "", "س؟ ص.")], cfg, False)
    assert [f.cause for f in result.flags] == ["dashes"]


def test_a_leak_is_caused_by_the_script_that_leaked(cfg) -> None:
    result = br._finalize_batch(
        [SubtitleBlock(1, "ts1", "The crate is from Beijing.")],
        [SubtitleBlock(1, "", "الصندوق من 北京.")], cfg, False)
    assert [f.cause for f in result.flags] == ["leak:han"]


def test_drift_and_bleeding_carry_their_own_causes(cfg) -> None:
    context = FileContext(terms=[TermHint("the channel", "الخط الأحمر")])
    source = [SubtitleBlock(1, "ts1", "when the tide turns in the channel,"),
              SubtitleBlock(2, "ts2", "it becomes dangerous.")]
    output = [SubtitleBlock(1, "", "متى يتجاوز التعليق أو الفعل الخط"),
              SubtitleBlock(2, "", "الخط.")]
    result = br._finalize_batch(source, output, cfg, False, context)
    assert [f.cause for f in result.flags] == ["term:the channel", "shift"]


def test_the_same_term_drifting_in_two_batches_is_one_cause(cfg) -> None:
    context = FileContext(terms=[TermHint("the channel", "الخط الأحمر")])
    causes = {
        br._finalize_batch(
            [SubtitleBlock(n, f"ts{n}", "down the channel now")],
            [SubtitleBlock(n, "", "في القناة الآن")], cfg, False, context,
        ).flags[0].cause
        for n in (1, 40)
    }
    assert causes == {"term:the channel"}


# === The flagged-batch retry =================================================


_TAGGED = [SubtitleBlock(1, "ts1", "A <i>b</i> c"),
           SubtitleBlock(2, "ts2", "Second line here.")]


def _flags_for(cfg, output_texts: list[str]):
    output = [SubtitleBlock(b.number, "", text)
              for b, text in zip(_TAGGED, output_texts, strict=True)]
    return br._finalize_batch(_TAGGED, output, cfg, False)


def test_a_retry_that_fixes_the_flag_is_accepted(monkeypatch, cfg) -> None:
    previous = _flags_for(cfg, ["س ص ع", "سطر ثان هنا."])
    assert len(previous.flags) == 1
    calls = _install_chat(
        monkeypatch, lambda sys_p, user, n: "1\nس <i>ص</i> ع\n\n2\nسطر ثان هنا.")

    out = run_async(br.retry_flagged_batch(
        None, _TAGGED, cfg, None, [], previous, previous.flags))

    assert len(calls) == 1
    assert out is not previous
    assert out.flags == []
    assert [b.text for b in out.blocks] == ["س <i>ص</i> ع", "سطر ثان هنا."]
    assert cfg.calls.repair == 1


def test_the_retry_is_the_batch_own_message_with_the_problems_on_top(
    monkeypatch, cfg,
) -> None:
    previous = _flags_for(cfg, ["س ص ع", "سطر ثان هنا."])
    calls = _install_chat(
        monkeypatch, lambda sys_p, user, n: "1\nس <i>ص</i> ع\n\n2\nسطر ثان هنا.")

    run_async(br.retry_flagged_batch(
        None, _TAGGED, cfg, None, [], previous, previous.flags))

    system_prompt, user_msg = calls[0]
    assert system_prompt is br.SYSTEM_PROMPT
    assert user_msg.startswith(
        "The previous attempt had these problems - fix ONLY these, keep "
        "everything else identical:\n"
        "- block 1: the formatting tags <i>...</i> were dropped\n\n")
    # The original request follows unchanged, wire last.
    assert user_msg.endswith(br.batch_user_message(_TAGGED, cfg, None, []))


def test_a_retry_that_fixes_nothing_keeps_the_first_translation(
    monkeypatch, cfg,
) -> None:
    previous = _flags_for(cfg, ["س ص ع", "سطر ثان هنا."])
    _install_chat(monkeypatch, lambda sys_p, user, n: "1\nس ص ع\n\n2\nسطر ثان.")

    out = run_async(br.retry_flagged_batch(
        None, _TAGGED, cfg, None, [], previous, previous.flags))

    assert out is previous


def test_a_retry_that_fails_validation_keeps_the_first_translation(
    monkeypatch, cfg,
) -> None:
    previous = _flags_for(cfg, ["س ص ع", "سطر ثان هنا."])
    _install_chat(monkeypatch, lambda sys_p, user, n: "1\nonly one block")

    out = run_async(br.retry_flagged_batch(
        None, _TAGGED, cfg, None, [], previous, previous.flags))

    assert out is previous


def test_a_retry_that_errors_keeps_the_first_translation(monkeypatch, cfg) -> None:
    warnings: list[str] = []
    cfg.warn = warnings.append
    previous = _flags_for(cfg, ["س ص ع", "سطر ثان هنا."])

    def boom(sys_p, user, n):
        raise RuntimeError("network down")

    _install_chat(monkeypatch, boom)
    out = run_async(br.retry_flagged_batch(
        None, _TAGGED, cfg, None, [], previous, previous.flags))

    assert out is previous
    assert any("Flagged-batch repair failed" in w for w in warnings)


_DIALOGUE = [SubtitleBlock(1, "ts1", "- Are you awake?\n- Barely."),
             SubtitleBlock(2, "ts2", "<i>Get out!</i>")]


def test_the_retrys_output_is_repaired_like_any_other_batch(
    monkeypatch, cfg,
) -> None:
    """Regression: the re-issued batch used to be accepted as the model wrote
    it, so the batches most likely to need tag repair, dash restoration,
    reflow and punctuation were the only ones that never got them."""
    first = br._finalize_batch(
        _DIALOGUE,
        [SubtitleBlock(1, "", "هل أنت مستيقظ؟ بالكاد."),
         SubtitleBlock(2, "", "<i>اخرج!</i>")],
        cfg, False)
    assert len(first.flags) == 1  # the merged speaker turns
    _install_chat(
        monkeypatch,
        lambda sys_p, user, n: "1\nهل أنت مستيقظ؟\nبالكاد.\n\n2\n<i></i>اخرج.")

    out = run_async(br.retry_flagged_batch(
        None, _DIALOGUE, cfg, None, [], first, first.flags))

    assert out is not first
    assert [b.text for b in out.blocks] == [
        "- هل أنت مستيقظ؟\n- بالكاد.",  # dashes back
        "<i>اخرج!</i>",                  # empty pair dropped, wrap and ! back
    ]
    assert out.flags == []


def test_the_retry_is_judged_on_the_repaired_text_not_the_raw_reply(
    monkeypatch, cfg,
) -> None:
    """The retry fixes the dashes and leaves Chinese behind: one flag traded
    for another is not an improvement, so the first translation stands."""
    first = br._finalize_batch(
        _DIALOGUE,
        [SubtitleBlock(1, "", "هل أنت مستيقظ؟ بالكاد."),
         SubtitleBlock(2, "", "<i>اخرج!</i>")],
        cfg, False)
    _install_chat(
        monkeypatch,
        lambda sys_p, user, n: "1\n- هل أنت مستيقظ؟\n- بالكاد.\n\n2\n<i>出去!</i>")

    out = run_async(br.retry_flagged_batch(
        None, _DIALOGUE, cfg, None, [], first, first.flags))

    assert out is first


def test_a_batch_with_no_flags_is_never_re_issued(monkeypatch, cfg) -> None:
    previous = br.BatchResult([SubtitleBlock(1, "ts1", "fine")])
    calls = _install_chat(monkeypatch, lambda sys_p, user, n: "1\nx")

    out = run_async(br.retry_flagged_batch(
        None, _TAGGED, cfg, None, [], previous, []))

    assert out is previous
    assert calls == []
    assert cfg.calls.repair == 0


# === Per-target review checks ================================================


def test_the_review_message_carries_the_targets_grammar_note(
    monkeypatch, cfg, no_sleep,
) -> None:
    from core.context_pass import CharacterHint

    cfg.review = True
    batch = [SubtitleBlock(1, "ts1", "Hello line.")]
    context = FileContext(characters=[CharacterHint("line", "line", "female")])
    calls = _install_chat(
        monkeypatch,
        lambda sys_p, user, n: ("1\nR" if sys_p is br.REVIEW_SYSTEM_PROMPT
                               else _echo(user)))

    run_async(br.translate_batch_with_retry(None, 0, batch, cfg, context))

    review = next(c for c in calls if c[0] is br.REVIEW_SYSTEM_PROMPT)[1]
    assert ("Target-language checks: After لم use the jussive" in review)


def test_a_latin_target_gets_no_checks_line(monkeypatch, cfg, no_sleep) -> None:
    from core.context_pass import CharacterHint

    cfg.review = True
    cfg.target_lang = "Spanish"
    batch = [SubtitleBlock(1, "ts1", "Hello line.")]
    context = FileContext(characters=[CharacterHint("line", "line", "female")])
    calls = _install_chat(
        monkeypatch,
        lambda sys_p, user, n: ("1\nR" if sys_p is br.REVIEW_SYSTEM_PROMPT
                               else _echo(user)))

    run_async(br.translate_batch_with_retry(None, 0, batch, cfg, context))

    review = next(c for c in calls if c[0] is br.REVIEW_SYSTEM_PROMPT)[1]
    assert "Target-language checks" not in review


# === Where each pass sends its request =======================================


def _reviewed_providers(monkeypatch, cfg) -> list:
    from core.context_pass import CharacterHint

    cfg.review = True
    batch = [SubtitleBlock(1, "ts1", "Hello line.")]
    context = FileContext(characters=[CharacterHint("line", "line", "female")])
    providers: list = []
    _install_chat(
        monkeypatch,
        lambda sys_p, user, n: ("1\nR" if sys_p is br.REVIEW_SYSTEM_PROMPT
                               else _echo(user)),
        providers)
    run_async(br.translate_batch_with_retry(None, 0, batch, cfg, context))
    return providers


def test_the_review_pass_uses_the_review_provider(
    monkeypatch, cfg, no_sleep,
) -> None:
    cfg.review_api_url = "https://review.example/v1/chat/completions"
    cfg.review_api_key = "sk-review"
    cfg.review_model = "stronger"

    translate, review = _reviewed_providers(monkeypatch, cfg)

    # Every other pass takes the run's main provider, which is the default.
    assert translate is None
    assert review == cfg.review_provider
    assert review.api_url == "https://review.example/v1/chat/completions"
    assert review.model == "stronger"


def test_an_unconfigured_review_pass_calls_the_main_provider(
    monkeypatch, cfg, no_sleep,
) -> None:
    cfg.model = "qwen"
    translate, review = _reviewed_providers(monkeypatch, cfg)
    assert translate is None
    assert review == cfg.provider


def test_a_review_model_on_its_own_keeps_the_main_url_and_key(
    monkeypatch, cfg, no_sleep,
) -> None:
    cfg.model = "qwen"
    cfg.review_model = "stronger"
    _, review = _reviewed_providers(monkeypatch, cfg)
    assert (review.api_url, review.api_key) == (cfg.api_url, cfg.api_key)
    assert review.model == "stronger"
