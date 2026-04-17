"""Shared types and helpers for subtitle-format parsers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from ..srt_parser import SubtitleBlock

SubtitleFormat = Literal["srt", "vtt", "ass", "ssa", "sbv", "sub"]


@dataclass
class SubtitleDocument:
    """A parsed subtitle file.

    `blocks` are always normalized into SRT-style shape (sequential numbers
    starting at 1 and ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` timestamps) so the LLM
    always sees the same structure. `rebuild` takes the translated blocks and
    returns the file serialized back into the original format.
    """

    format: SubtitleFormat
    blocks: list[SubtitleBlock]
    rebuild: Callable[[list[SubtitleBlock]], str]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def strip_bom(s: str) -> str:
    return s[1:] if s.startswith("\ufeff") else s


def normalize_newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def detect_eol(raw: str) -> str:
    return "\r\n" if "\r\n" in raw else "\n"


def pad2(n: int) -> str:
    return f"{n:02d}"


def pad3(n: int) -> str:
    return f"{n:03d}"
