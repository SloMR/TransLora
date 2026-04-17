"""SRT format adapter — thin wrapper around the canonical SRT parser."""

from __future__ import annotations

from ..srt_parser import parse_srt, serialize_srt
from .types import SubtitleDocument


def parse_srt_document(content: str) -> SubtitleDocument:
    return SubtitleDocument(
        format="srt",
        blocks=parse_srt(content),
        rebuild=lambda translated: serialize_srt(translated),
    )
