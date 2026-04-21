"""Multi-format subtitle parsing via pysubs2 (with a small SBV fallback).
Every format is normalized to SRT-shape blocks so the LLM sees one structure;
rebuild delegates back to pysubs2 to preserve headers, styles, and per-cue metadata."""

from __future__ import annotations

from pathlib import Path

import pysubs2

from ..srt_parser import SubtitleBlock
from .sbv import parse_sbv
from .types import SubtitleDocument, SubtitleFormat

_EXT_TO_PYSUBS2: dict[str, str] = {
    "srt": "srt",
    "vtt": "vtt",
    "ass": "ass",
    "ssa": "ssa",
    "sub": "microdvd",
}


def parse_subtitle(file_name: str, content: str) -> SubtitleDocument:
    ext = Path(file_name).suffix.lower().lstrip(".")
    if ext == "sbv":
        return parse_sbv(content)
    fmt = _EXT_TO_PYSUBS2.get(ext)
    if fmt is None:
        raise ValueError(f"Unsupported subtitle format: .{ext or file_name}")
    return _parse_pysubs2(content, ext, fmt)


_MICRODVD_DEFAULT_FPS = 23.976


def _parse_pysubs2(content: str, source_ext: str, fmt: str) -> SubtitleDocument:
    kwargs: dict = {}
    if fmt == "microdvd":
        kwargs["fps"] = _MICRODVD_DEFAULT_FPS
    subs = pysubs2.SSAFile.from_string(content, format_=fmt, **kwargs)

    event_indices: list[int] = []
    blocks: list[SubtitleBlock] = []
    for idx, event in enumerate(subs.events):
        if event.is_comment:
            continue
        event_indices.append(idx)
        blocks.append(
            SubtitleBlock(
                number=len(blocks) + 1,
                timestamp=f"{_ms_to_srt(event.start)} --> {_ms_to_srt(event.end)}",
                text=event.text.replace("\\N", "\n"),
            )
        )

    source_format: SubtitleFormat = source_ext  # type: ignore[assignment]

    def rebuild(translated: list[SubtitleBlock]) -> str:
        for i, translated_block in enumerate(translated):
            if i >= len(event_indices):
                break
            subs.events[event_indices[i]].text = translated_block.text.replace(
                "\n", "\\N"
            )
        return subs.to_string(format_=fmt, **kwargs)

    return SubtitleDocument(format=source_format, blocks=blocks, rebuild=rebuild)


def _ms_to_srt(ms: int) -> str:
    total = max(0, int(ms))
    hours = total // 3_600_000
    minutes = (total // 60_000) % 60
    seconds = (total // 1000) % 60
    millis = total % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


__all__ = [
    "SubtitleDocument",
    "SubtitleFormat",
    "parse_subtitle",
]
