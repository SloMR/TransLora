"""Shared types for subtitle-format parsers. Separate from the package
`__init__` so `sbv` can import them without an import cycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..srt_parser import SubtitleBlock

SubtitleFormat = Literal["srt", "vtt", "ass", "ssa", "sbv", "sub"]


@dataclass
class SubtitleDocument:
    """Parsed file. `blocks` use normalized SRT shape; `rebuild` serializes back
    to the original format."""

    format: SubtitleFormat
    blocks: list[SubtitleBlock]
    rebuild: Callable[[list[SubtitleBlock]], str]
