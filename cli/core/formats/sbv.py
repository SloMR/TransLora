"""YouTube SBV parser and rebuilder."""

from __future__ import annotations

import re

from ..srt_parser import SubtitleBlock, normalize_text
from .types import SubtitleDocument

_SBV_LINE_RE = re.compile(
    r"^(\d+):(\d{2}):(\d{2})\.(\d{3})\s*,\s*"
    r"(\d+):(\d{2}):(\d{2})\.(\d{3})$"
)


def parse_sbv(content: str) -> SubtitleDocument:
    text = normalize_text(content).strip()
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
        start = f"{int(g[0]):02d}:{g[1]}:{g[2]},{g[3]}"
        end = f"{int(g[4]):02d}:{g[5]}:{g[6]},{g[7]}"
        cue_text = normalize_text("\n".join(lines[1:]))

        blocks.append(
            SubtitleBlock(
                number=n, timestamp=f"{start} --> {end}", text=cue_text
            )
        )
        original_ts.append(lines[0].strip())
        n += 1

    def rebuild(translated: list[SubtitleBlock]) -> str:
        # Keyed by block number, not list position: a short or reordered
        # translation must not shift every cue onto the wrong timestamp.
        text_by_number = {b.number: b.text for b in translated}
        out = [
            f"{ts}\n{text_by_number.get(src.number, src.text)}"
            for ts, src in zip(original_ts, blocks, strict=True)
        ]
        return "\n\n".join(out) + "\n"

    return SubtitleDocument(format="sbv", blocks=blocks, rebuild=rebuild)
