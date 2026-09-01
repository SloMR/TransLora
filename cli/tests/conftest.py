"""Shared fixtures for the CLI suite.

Tests build a real `TranslationConfig` rather than a stub so a renamed or
retyped field breaks here instead of drifting away from the CLI silently.
"""

from __future__ import annotations

import asyncio

import pytest

from core.config import TranslationConfig
from core.srt_parser import SubtitleBlock


def make_block(number: int, text: str) -> SubtitleBlock:
    """One block with a timestamp derived from its number, so a positional
    mix-up in reattachment is visible."""
    return SubtitleBlock(
        number=number,
        timestamp=f"00:00:{number:02d},000 --> 00:00:{number + 1:02d},000",
        text=text,
    )


def make_blocks(count: int, start: int = 1) -> list[SubtitleBlock]:
    return [make_block(n, f"line {n}") for n in range(start, start + count)]


def run_async(coro):
    return asyncio.run(coro)


@pytest.fixture
def cfg() -> TranslationConfig:
    """Config for offline tests: no live terminal, no review pass, no sidecar."""
    return TranslationConfig(
        source_lang="English",
        target_lang="Arabic",
        api_url="http://localhost/v1/chat/completions",
        api_key="none",
        review=False,
        resume=False,
        quiet=True,
    )


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    """Make retry backoff instant, and record the delays it asked for."""
    delays: list[float] = []

    async def fake_sleep(secs: float) -> None:
        delays.append(secs)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays
