"""ASS/SSA parser and rebuilder.

The script keeps the entire file as an ordered list of "line records". Every
non-event line (script info, styles, comments, blank lines) is preserved
verbatim. Only ``Dialogue:`` lines become translatable blocks; the rest of the
line — layer, timings, style, margins — is stored as a prefix so we can glue
the translated text back in place without touching anything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..srt_parser import SubtitleBlock
from .types import SubtitleDocument, detect_eol, normalize_newlines, pad2, strip_bom

_SECTION_RE = re.compile(r"^\[.+\]\s*$")
_EVENTS_HEADER_RE = re.compile(r"^\[events\]\s*$", re.IGNORECASE)
_FORMAT_RE = re.compile(r"^format\s*:", re.IGNORECASE)
_DIALOGUE_RE = re.compile(r"^dialogue\s*:", re.IGNORECASE)
_DIALOGUE_HEAD_RE = re.compile(r"^(\s*Dialogue\s*:\s*)", re.IGNORECASE)
_ASS_TS_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})[.,](\d{1,3})$")


@dataclass
class _Literal:
    kind: Literal["literal"]
    text: str


@dataclass
class _Dialogue:
    kind: Literal["dialogue"]
    prefix: str
    block_index: int


def parse_ass(
    content: str, fmt: Literal["ass", "ssa"] = "ass"
) -> SubtitleDocument:
    text = normalize_newlines(strip_bom(content))
    eol = detect_eol(content)
    lines = text.split("\n")

    records: list[_Literal | _Dialogue] = []
    blocks: list[SubtitleBlock] = []

    in_events = False
    text_col_idx = -1
    n = 1

    for line in lines:
        trimmed = line.strip()

        if _SECTION_RE.match(trimmed):
            in_events = bool(_EVENTS_HEADER_RE.match(trimmed))
            records.append(_Literal("literal", line))
            continue

        if in_events and _FORMAT_RE.match(trimmed):
            payload = trimmed.split(":", 1)[1]
            cols = [c.strip().lower() for c in payload.split(",")]
            if "text" in cols:
                text_col_idx = cols.index("text")
            records.append(_Literal("literal", line))
            continue

        if in_events and _DIALOGUE_RE.match(trimmed) and text_col_idx > 0:
            split = _split_dialogue(line, text_col_idx)
            if split is not None:
                prefix, fields, cue_text = split
                timestamp = _extract_timestamp(fields)
                blocks.append(
                    SubtitleBlock(number=n, timestamp=timestamp, text=cue_text)
                )
                records.append(_Dialogue("dialogue", prefix, len(blocks) - 1))
                n += 1
                continue

        records.append(_Literal("literal", line))

    def rebuild(translated: list[SubtitleBlock]) -> str:
        out: list[str] = []
        for r in records:
            if isinstance(r, _Literal):
                out.append(r.text)
            else:
                if r.block_index < len(translated):
                    out.append(r.prefix + translated[r.block_index].text)
                else:
                    out.append(r.prefix)
        return eol.join(out)

    return SubtitleDocument(format=fmt, blocks=blocks, rebuild=rebuild)


def _split_dialogue(
    line: str, text_col_idx: int
) -> tuple[str, list[str], str] | None:
    head = _DIALOGUE_HEAD_RE.match(line)
    if not head:
        return None
    head_end = head.end()
    payload = line[head_end:]

    commas = 0
    i = 0
    while i < len(payload) and commas < text_col_idx:
        if payload[i] == ",":
            commas += 1
        i += 1
    if commas < text_col_idx:
        return None

    prefix_payload = payload[:i]
    cue_text = payload[i:]
    fields = prefix_payload.rstrip(",").split(",")

    return line[:head_end] + prefix_payload, fields, cue_text


def _extract_timestamp(fields: list[str]) -> str:
    # Format: Layer, Start, End, Style, Name, ..., Text
    start = _ass_ts_to_srt(fields[1] if len(fields) > 1 else "")
    end = _ass_ts_to_srt(fields[2] if len(fields) > 2 else "")
    return f"{start} --> {end}"


def _ass_ts_to_srt(ts: str) -> str:
    m = _ASS_TS_RE.match(ts.strip())
    if not m:
        return "00:00:00,000"
    h, mi, s, frac = m.group(1), m.group(2), m.group(3), m.group(4)
    ms = (frac + "000")[:3]
    return f"{pad2(int(h))}:{mi}:{s},{ms}"
