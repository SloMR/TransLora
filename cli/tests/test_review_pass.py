"""Tests for the post-edit review pass in batch_runner."""
from __future__ import annotations

import core.batch_runner as br
from core.chat_client import FatalApiError
from core.context_pass import CharacterHint, FileContext, TermHint
from core.srt_parser import SubtitleBlock, serialize_lite
from tests.conftest import make_block, run_async


def _ctx_with_char() -> FileContext:
    return FileContext(
        characters=[CharacterHint("Alice", "Alice", "female")],
    )


def test_review_keeps_first_pass_when_block_count_mismatches(monkeypatch, cfg):
    batch = [make_block(1, "Hello Alice."), make_block(2, "World.")]
    first_pass = [make_block(1, "Bonjour."), make_block(2, "Monde.")]

    async def fake_call(*a, **k):
        return "1\nsingle"  # only 1 block; mismatched count

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    out = run_async(br._review_pass(None, batch, first_pass, cfg, _ctx_with_char()))
    assert out is first_pass


def test_review_keeps_first_pass_when_the_reviewer_renumbers(monkeypatch, cfg):
    batch = [make_block(1, "Hello Alice."), make_block(2, "World.")]
    first_pass = [make_block(1, "Bonjour."), make_block(2, "Monde.")]

    async def fake_call(*a, **k):
        return "2\nSalut.\n\n3\nMonde."

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    out = run_async(br._review_pass(None, batch, first_pass, cfg, _ctx_with_char()))
    assert out is first_pass


def test_review_accepts_valid_revision(monkeypatch, cfg):
    batch = [make_block(1, "Hello Alice."), make_block(2, "World.")]
    first_pass = [make_block(1, "Bonjour."), make_block(2, "Monde.")]

    async def fake_call(*a, **k):
        return "1\nSalut.\n\n2\nMonde."

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    out = run_async(br._review_pass(None, batch, first_pass, cfg, _ctx_with_char()))
    assert [b.text for b in out] == ["Salut.", "Monde."]
    assert [b.number for b in out] == [1, 2]
    assert [b.timestamp for b in out] == [batch[0].timestamp, batch[1].timestamp]


def test_review_skips_when_no_glossary_and_never_calls_api(monkeypatch, cfg):
    # Without scene/character guidance, there's no principled reason to touch
    # the first-pass — the review must not fire at all.
    batch = [make_block(1, "Hi.")]
    first_pass = [make_block(1, "Salut.")]
    calls = {"n": 0}

    async def fake_call(*a, **k):
        calls["n"] += 1
        return "1\nX."

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    out = run_async(br._review_pass(None, batch, first_pass, cfg, None))
    assert out is first_pass
    assert calls["n"] == 0


def test_review_skips_a_batch_whose_glossary_slice_has_only_a_register(
    monkeypatch, cfg,
):
    # Register alone is not something the reviewer is allowed to change, so
    # paying for a call on this batch buys nothing.
    batch = [make_block(1, "Hi.")]
    first_pass = [make_block(1, "Salut.")]
    calls = {"n": 0}

    async def fake_call(*a, **k):
        calls["n"] += 1
        return "1\nX."

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    context = FileContext(register="Modern Standard Arabic, neutral")
    out = run_async(br._review_pass(None, batch, first_pass, cfg, context))
    assert out is first_pass
    assert calls["n"] == 0


def test_review_keeps_first_pass_when_the_review_model_is_fatal(monkeypatch, cfg):
    """The review pass is best-effort: an unusable review endpoint costs the
    opinion, never the file."""
    batch = [make_block(1, "Hi Alice.")]
    first_pass = [make_block(1, "Salut.")]

    async def fatal(*a, **k):
        raise FatalApiError("the model returned no text at 1200 tokens")

    monkeypatch.setattr(br, "call_chat_api", fatal)
    out = run_async(br._review_pass(None, batch, first_pass, cfg, _ctx_with_char()))
    assert out is first_pass


def test_review_keeps_first_pass_on_api_error(monkeypatch, cfg):
    batch = [make_block(1, "Hi Alice.")]
    first_pass = [make_block(1, "Salut.")]

    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(br, "call_chat_api", boom)
    out = run_async(br._review_pass(None, batch, first_pass, cfg, _ctx_with_char()))
    assert out is first_pass


# === What the reviewer is actually shown =====================================

# The review's fourth permitted correction is "a literal word-for-word
# rendering of an idiom", which is worth nothing unless the batch's <idioms>
# are in front of it. They are: `_review_pass` embeds `render_for_batch`, which
# renders idioms under their own heading. These pin that they still do.


def _capture_review(monkeypatch, cfg, batch, context) -> str:
    sent: list[str] = []

    async def fake_call(client, system_prompt, user_message, *a, **k):
        sent.append(user_message)
        return serialize_lite([SubtitleBlock(b.number, "", "x") for b in batch])

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    first_pass = [make_block(b.number, "first") for b in batch]
    run_async(br._review_pass(None, batch, first_pass, cfg, context))
    return sent[0] if sent else ""


def test_the_reviewer_is_shown_the_idioms_its_fourth_rule_is_about(
    monkeypatch, cfg,
):
    batch = [make_block(1, "Alice says that ship has sailed.")]
    context = FileContext(
        characters=[CharacterHint("Alice", "أليس", "female")],
        idioms=[TermHint("that ship has sailed", "فات الأوان")],
    )
    sent = _capture_review(monkeypatch, cfg, batch, context)

    assert "Idioms - render by meaning, never word for word:" in sent
    assert "- that ship has sailed => فات الأوان" in sent


def test_an_idiom_the_batch_never_uses_is_not_shown_to_the_reviewer(
    monkeypatch, cfg,
):
    batch = [make_block(1, "Alice is late.")]
    context = FileContext(
        characters=[CharacterHint("Alice", "أليس", "female")],
        idioms=[TermHint("that ship has sailed", "فات الأوان")],
    )
    assert "فات الأوان" not in _capture_review(monkeypatch, cfg, batch, context)


def test_a_batch_whose_only_glossary_entry_is_an_idiom_is_reviewed(
    monkeypatch, cfg,
):
    """The gate counts idioms, so the fourth rule no longer needs a character
    or a term to ride in on — it was a direct cap on the weakest aspect."""
    batch = [make_block(1, "That ship has sailed.")]
    context = FileContext(idioms=[TermHint("that ship has sailed", "فات الأوان")])
    assert context.has_correctable_entries(batch)
    sent = _capture_review(monkeypatch, cfg, batch, context)
    assert "- that ship has sailed => فات الأوان" in sent


def test_a_batch_that_names_nothing_at_all_still_buys_no_review(
    monkeypatch, cfg,
):
    batch = [make_block(1, "Good morning.")]
    context = FileContext(idioms=[TermHint("that ship has sailed", "فات الأوان")])
    assert _capture_review(monkeypatch, cfg, batch, context) == ""
