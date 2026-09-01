"""Multi-format subtitle parsing via pysubs2 (with a small SBV fallback).
Every format is normalized to SRT-shape blocks so the LLM sees one structure;
rebuild delegates back to pysubs2 to preserve headers, styles, and per-cue metadata.

Known limitation: pysubs2 routes every format through ASS-shaped events, so a
round-trip drops what ASS has no slot for — VTT cue ids and settings, NOTE and
STYLE blocks, SRT positioning tags. Fixing it needs format-native parsers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pysubs2

from ..srt_parser import SubtitleBlock, normalize_text
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
# MicroDVD files declare their framerate as a `{1}{1}<fps>` pseudo-cue.
_MICRODVD_FPS_RE = re.compile(r"^\s*\{\s*1\s*\}\s*\{\s*1\s*\}\s*(\d+(?:\.\d+)?)\s*$")


def declared_microdvd_fps(content: str) -> str | None:
    """The file's own `{1}{1}<fps>` header, verbatim, or None. Only the first
    non-empty line counts — elsewhere `{1}{1}` is an ordinary cue."""
    for line in content.splitlines():
        if not line.strip():
            continue
        m = _MICRODVD_FPS_RE.match(line)
        if not m:
            return None
        return m.group(1) if float(m.group(1)) > 0 else None
    return None


def _parse_pysubs2(content: str, source_ext: str, fmt: str) -> SubtitleDocument:
    read_kwargs: dict = {}
    write_kwargs: dict = {}
    declared_fps: str | None = None
    if fmt == "microdvd":
        # pysubs2 honours the file's own fps header only when no fps is passed;
        # forcing one turns that header into a translatable cue.
        declared_fps = declared_microdvd_fps(content)
        if declared_fps is None:
            read_kwargs["fps"] = _MICRODVD_DEFAULT_FPS
            write_kwargs["fps"] = _MICRODVD_DEFAULT_FPS
        # pysubs2 writes `{0}{0}<fps>`, which nothing reads back as a framerate;
        # we re-emit the original header below instead.
        write_kwargs["write_fps_declaration"] = False
    subs = pysubs2.SSAFile.from_string(content, format_=fmt, **read_kwargs)

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
                text=normalize_text(event.text.replace("\\N", "\n")),
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
        out = subs.to_string(format_=fmt, **write_kwargs)
        if declared_fps is not None:
            out = "{1}{1}" + declared_fps + "\n" + out
        return out

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
