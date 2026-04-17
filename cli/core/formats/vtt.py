"""WebVTT parser and rebuilder."""

from __future__ import annotations

import re

from ..srt_parser import SubtitleBlock
from .types import SubtitleDocument, normalize_newlines, pad2, strip_bom

_CUE_LINE_RE = re.compile(
    r"^\s*(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}\s*-->\s*"
    r"(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}(?:\s+.*)?$"
)
_TS_EXTRACT_RE = re.compile(
    r"((?:\d{1,2}:)?\d{2}:\d{2}\.\d{3})\s*-->\s*"
    r"((?:\d{1,2}:)?\d{2}:\d{2}\.\d{3})"
)
_VTT_HMS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})")
_VTT_MS_RE = re.compile(r"(\d{2}):(\d{2})\.(\d{3})")


def _vtt_to_srt_ts(ts: str) -> str:
    """Convert a VTT timestamp (HH:MM:SS.mmm or MM:SS.mmm) to SRT form."""
    m = _VTT_HMS_RE.fullmatch(ts)
    if m:
        return f"{pad2(int(m.group(1)))}:{m.group(2)}:{m.group(3)},{m.group(4)}"
    m = _VTT_MS_RE.fullmatch(ts)
    if m:
        return f"00:{m.group(1)}:{m.group(2)},{m.group(3)}"
    return "00:00:00,000"


def parse_vtt(content: str) -> SubtitleDocument:
    text = normalize_newlines(strip_bom(content))
    chunks = re.split(r"\n\n+", text)

    blocks: list[SubtitleBlock] = []
    cue_meta: list[tuple[str | None, str]] = []
    header = "WEBVTT"
    start_idx = 0

    if chunks and chunks[0].lstrip().startswith("WEBVTT"):
        header = chunks[0].rstrip()
        start_idx = 1

    n = 1
    for raw in chunks[start_idx:]:
        chunk = raw.rstrip()
        if not chunk:
            continue
        if re.match(r"^(NOTE|STYLE|REGION)\b", chunk):
            continue

        lines = chunk.split("\n")
        ts_idx = 0
        identifier: str | None = None

        if not _CUE_LINE_RE.match(lines[0]):
            if len(lines) < 2 or not _CUE_LINE_RE.match(lines[1]):
                continue
            identifier = lines[0]
            ts_idx = 1

        original_ts = lines[ts_idx]
        m = _TS_EXTRACT_RE.search(original_ts)
        if not m:
            continue
        srt_ts = f"{_vtt_to_srt_ts(m.group(1))} --> {_vtt_to_srt_ts(m.group(2))}"
        cue_text = "\n".join(lines[ts_idx + 1:])

        blocks.append(SubtitleBlock(number=n, timestamp=srt_ts, text=cue_text))
        cue_meta.append((identifier, original_ts))
        n += 1

    def rebuild(translated: list[SubtitleBlock]) -> str:
        parts: list[str] = [header]
        for i, b in enumerate(translated):
            if i < len(cue_meta):
                ident, ts = cue_meta[i]
            else:
                ident, ts = None, b.timestamp.replace(",", ".")
            prefix = f"{ident}\n" if ident else ""
            parts.append(f"{prefix}{ts}\n{b.text}")
        return "\n\n".join(parts) + "\n"

    return SubtitleDocument(format="vtt", blocks=blocks, rebuild=rebuild)
