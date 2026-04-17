"""Multi-format subtitle parsing. All formats normalize to SRT-shape blocks
so the LLM sees a consistent structure regardless of the source file."""

from __future__ import annotations

from pathlib import Path

from .ass import parse_ass
from .sbv import parse_sbv
from .srt import parse_srt_document
from .sub import parse_sub
from .types import SubtitleDocument, SubtitleFormat
from .vtt import parse_vtt


def parse_subtitle(file_name: str, content: str) -> SubtitleDocument:
    """Dispatch to the right parser based on the filename's extension."""
    ext = Path(file_name).suffix.lower().lstrip(".")
    if ext == "srt":
        return parse_srt_document(content)
    if ext == "vtt":
        return parse_vtt(content)
    if ext == "ass":
        return parse_ass(content, "ass")
    if ext == "ssa":
        return parse_ass(content, "ssa")
    if ext == "sbv":
        return parse_sbv(content)
    if ext == "sub":
        return parse_sub(content)
    raise ValueError(f"Unsupported subtitle format: .{ext or file_name}")


__all__ = [
    "SubtitleDocument",
    "SubtitleFormat",
    "parse_subtitle",
]
