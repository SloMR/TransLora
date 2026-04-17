"""YouTube SBV parser and rebuilder."""

from __future__ import annotations

import re

from ..srt_parser import SubtitleBlock
from .types import SubtitleDocument, normalize_newlines, pad2, strip_bom

_SBV_LINE_RE = re.compile(
    r"^(\d+):(\d{2}):(\d{2})\.(\d{3})\s*,\s*"
    r"(\d+):(\d{2}):(\d{2})\.(\d{3})$"
)


def parse_sbv(content: str) -> SubtitleDocument:
    text = normalize_newlines(strip_bom(content)).strip()
    chunks = re.split(r"\n\n+", text)

    blocks: list[SubtitleBlock] = []
    original_ts: list[str] = []
    n = 1

    for chunk in chunks:
        lines = chunk.split("\n")
        m = _SBV_LINE_RE.match(lines[0].strip())
        if not m:
            continue
        g = m.groups()
        start = f"{pad2(int(g[0]))}:{g[1]}:{g[2]},{g[3]}"
        end = f"{pad2(int(g[4]))}:{g[5]}:{g[6]},{g[7]}"
        cue_text = "\n".join(lines[1:])

        blocks.append(
            SubtitleBlock(
                number=n, timestamp=f"{start} --> {end}", text=cue_text
            )
        )
        original_ts.append(lines[0].strip())
        n += 1

    def rebuild(translated: list[SubtitleBlock]) -> str:
        out: list[str] = []
        for i, b in enumerate(translated):
            ts = original_ts[i] if i < len(original_ts) else _srt_to_sbv(b.timestamp)
            out.append(f"{ts}\n{b.text}")
        return "\n\n".join(out) + "\n"

    return SubtitleDocument(format="sbv", blocks=blocks, rebuild=rebuild)


def _srt_to_sbv(srt: str) -> str:
    m = re.match(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})",
        srt,
    )
    if not m:
        return srt
    g = m.groups()
    return (
        f"{int(g[0])}:{g[1]}:{g[2]}.{g[3]},"
        f"{int(g[4])}:{g[5]}:{g[6]}.{g[7]}"
    )
