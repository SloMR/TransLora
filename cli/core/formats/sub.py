"""MicroDVD (.sub) parser and rebuilder.

The .sub extension covers several incompatible formats. We support MicroDVD —
``{startframe}{endframe}Text | Text`` — because it's by far the most common
variant in the wild. Anything else raises a descriptive error so the user
knows exactly why the file was rejected.
"""

from __future__ import annotations

import re

from ..srt_parser import SubtitleBlock
from .types import SubtitleDocument, normalize_newlines, pad2, pad3, strip_bom

_MICRO_DVD_RE = re.compile(r"^\{(\d+)\}\{(\d+)\}(.*)$")
_DEFAULT_FPS = 23.976


def parse_sub(content: str) -> SubtitleDocument:
    text = normalize_newlines(strip_bom(content))
    non_empty = [ln for ln in text.split("\n") if ln.strip()]

    if not non_empty:
        return SubtitleDocument(format="sub", blocks=[], rebuild=lambda _t: "")

    if not _MICRO_DVD_RE.match(non_empty[0]):
        raise ValueError(
            ".sub format not recognized (only MicroDVD {n}{n}text is supported)."
        )

    blocks: list[SubtitleBlock] = []
    frame_ranges: list[tuple[str, str]] = []
    n = 1

    for line in non_empty:
        m = _MICRO_DVD_RE.match(line)
        if not m:
            continue
        f1, f2, raw_text = m.group(1), m.group(2), m.group(3)
        # MicroDVD uses `|` between lines inside a block.
        display_text = raw_text.replace("|", "\n")
        ts = f"{_frames_to_srt(int(f1))} --> {_frames_to_srt(int(f2))}"

        blocks.append(SubtitleBlock(number=n, timestamp=ts, text=display_text))
        frame_ranges.append((f1, f2))
        n += 1

    def rebuild(translated: list[SubtitleBlock]) -> str:
        out: list[str] = []
        for i, b in enumerate(translated):
            f1, f2 = frame_ranges[i] if i < len(frame_ranges) else ("0", "0")
            joined = b.text.replace("\n", "|")
            out.append(f"{{{f1}}}{{{f2}}}{joined}")
        return "\n".join(out) + "\n"

    return SubtitleDocument(format="sub", blocks=blocks, rebuild=rebuild)


def _frames_to_srt(frame: int) -> str:
    total_ms = round((frame / _DEFAULT_FPS) * 1000)
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    s = total_sec % 60
    total_min = total_sec // 60
    mi = total_min % 60
    h = total_min // 60
    return f"{pad2(h)}:{pad2(mi)}:{pad2(s)},{pad3(ms)}"
