"""The SRT wire format: parsing, serializing, batch splitting, and the
validation of a model's reply against the batch it was sent."""

from __future__ import annotations

import re
from dataclasses import dataclass

TIMESTAMP_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$"
)
# Unanchored: catches a timestamp the model echoed anywhere inside a block.
TIMESTAMP_IN_TEXT_RE = re.compile(
    r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}"
)


@dataclass
class SubtitleBlock:
    number: int
    timestamp: str
    text: str


def normalize_text(text: str) -> str:
    """One newline convention, no BOM, no trailing blank lines. Every parser
    runs its cue text through this: a stray CR reads as a second line, so it
    doubles the cue's line count and pushes a closing tag off the end."""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def parse_srt(content: str) -> list[SubtitleBlock]:
    raw_blocks = re.split(r"\n\n+", normalize_text(content).strip())

    blocks: list[SubtitleBlock] = []
    for raw in raw_blocks:
        lines = raw.strip().split("\n")
        if len(lines) < 2:
            continue
        try:
            number = int(lines[0].strip())
        except ValueError:
            continue
        timestamp = lines[1].strip()
        if not TIMESTAMP_RE.match(timestamp):
            continue
        text = "\n".join(lines[2:]) if len(lines) > 2 else ""
        blocks.append(SubtitleBlock(number=number, timestamp=timestamp, text=text))

    return blocks


def serialize_srt(blocks: list[SubtitleBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        parts.append(f"{block.number}\n{block.timestamp}\n{block.text}")
    return "\n\n".join(parts) + "\n"


def _collapse_blank_lines(text: str) -> str:
    """A blank line inside a block would forge a block boundary in the wire format."""
    return "\n".join(line for line in text.split("\n") if line.strip())


# Wire format: number + text only. Timestamps are stripped before sending
# because small models sometimes corrupt them; callers reattach positionally.
def serialize_lite(blocks: list[SubtitleBlock]) -> str:
    return "\n\n".join(
        f"{b.number}\n{_collapse_blank_lines(b.text)}" for b in blocks
    ) + "\n"


def parse_lite(content: str) -> list[SubtitleBlock]:
    raw_blocks = re.split(r"\n\n+", normalize_text(content).strip())

    blocks: list[SubtitleBlock] = []
    for raw in raw_blocks:
        lines = raw.strip().split("\n")
        if not lines:
            continue
        try:
            number = int(lines[0].strip())
        except ValueError:
            continue
        text = "\n".join(lines[1:]) if len(lines) > 1 else ""
        blocks.append(SubtitleBlock(number=number, timestamp="", text=text))
    return blocks


def split_batches(blocks: list[SubtitleBlock], batch_size: int = 15) -> list[list[SubtitleBlock]]:
    return [blocks[i : i + batch_size] for i in range(0, len(blocks), batch_size)]


@dataclass
class ValidationResult:
    ok: bool
    error: str = ""


def validate_batch(
    input_blocks: list[SubtitleBlock],
    output_blocks: list[SubtitleBlock],
) -> ValidationResult:
    if len(input_blocks) != len(output_blocks):
        return ValidationResult(
            ok=False,
            error=f"Block count mismatch: expected {len(input_blocks)}, got {len(output_blocks)}",
        )

    for i, (inp, out) in enumerate(zip(input_blocks, output_blocks, strict=True)):
        if inp.number != out.number:
            return ValidationResult(
                ok=False,
                error=(f"Block number mismatch at index {i}: "
                       f"expected {inp.number}, got {out.number}"),
            )

    # Wire-parsed blocks carry no timestamp; callers reattach after validation passes.
    for inp, out in zip(input_blocks, output_blocks, strict=True):
        if out.timestamp and inp.timestamp != out.timestamp:
            return ValidationResult(
                ok=False,
                error=(f"Timestamp modified at block {inp.number}: "
                       f"expected '{inp.timestamp}', got '{out.timestamp}'"),
            )

    # Catches silent data-loss where the model shifts blocks and leaves a
    # tail block blank while preserving count/numbers/timestamps.
    for inp, out in zip(input_blocks, output_blocks, strict=True):
        if inp.text.strip() and not out.text.strip():
            return ValidationResult(
                ok=False,
                error=f"Empty output at block {inp.number} (input was non-empty)",
            )

    # The wire format has no timestamp lines, so one in the text is model leakage.
    for inp, out in zip(input_blocks, output_blocks, strict=True):
        if TIMESTAMP_IN_TEXT_RE.search(out.text):
            return ValidationResult(
                ok=False,
                error=f"Timestamp line leaked into text at block {inp.number}",
            )

    return ValidationResult(ok=True)
