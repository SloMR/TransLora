"""Tests for the post-edit review pass in batch_runner."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import core.batch_runner as br
from core.context_pass import CharacterHint, FileContext
from core.srt_parser import SubtitleBlock


def _block(n: int, text: str) -> SubtitleBlock:
    return SubtitleBlock(number=n, timestamp="00:00:00,000 --> 00:00:01,000", text=text)


@dataclass
class _StubCfg:
    source_lang: str = "English"
    target_lang: str = "French"
    model: str | None = None
    api_url: str = ""
    api_key: str = ""
    review: bool = True

    def warn(self, msg: str) -> None:
        pass


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx_with_char() -> FileContext:
    return FileContext(
        characters=[CharacterHint("Alice", "Alice", "female")],
    )


def test_review_keeps_first_pass_when_block_count_mismatches(monkeypatch):
    batch = [_block(1, "Hello Alice."), _block(2, "World.")]
    first_pass = [_block(1, "Bonjour."), _block(2, "Monde.")]

    async def fake_call(*a, **k):
        return "1\nsingle"  # only 1 block; mismatched count

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    out = _run(br._review_pass(None, batch, first_pass, _StubCfg(), _ctx_with_char()))
    assert out is first_pass


def test_review_accepts_valid_revision(monkeypatch):
    batch = [_block(1, "Hello Alice."), _block(2, "World.")]
    first_pass = [_block(1, "Bonjour."), _block(2, "Monde.")]

    async def fake_call(*a, **k):
        return "1\nSalut.\n\n2\nMonde."

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    out = _run(br._review_pass(None, batch, first_pass, _StubCfg(), _ctx_with_char()))
    assert [b.text for b in out] == ["Salut.", "Monde."]
    assert [b.number for b in out] == [1, 2]
    assert [b.timestamp for b in out] == [batch[0].timestamp, batch[1].timestamp]


def test_review_skips_when_no_glossary_and_never_calls_api(monkeypatch):
    # Without scene/character guidance, there's no principled reason to touch
    # the first-pass — the review must not fire at all.
    batch = [_block(1, "Hi.")]
    first_pass = [_block(1, "Salut.")]
    calls = {"n": 0}

    async def fake_call(*a, **k):
        calls["n"] += 1
        return "1\nX."

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    out = _run(br._review_pass(None, batch, first_pass, _StubCfg(), None))
    assert out is first_pass
    assert calls["n"] == 0


def test_review_keeps_first_pass_on_api_error(monkeypatch):
    batch = [_block(1, "Hi Alice.")]
    first_pass = [_block(1, "Salut.")]

    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(br, "call_chat_api", boom)
    out = _run(br._review_pass(None, batch, first_pass, _StubCfg(), _ctx_with_char()))
    assert out is first_pass
