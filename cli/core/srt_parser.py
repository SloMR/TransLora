"""SRT file parsing, validation, and batch splitting."""

from __future__ import annotations

import re
from dataclasses import dataclass

TIMESTAMP_RE = re.compile(
    r"^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$"
)


@dataclass
class SubtitleBlock:
    """A single .srt subtitle block."""

    number: int
    timestamp: str
    text: str  # may contain multiple lines separated by \n


def parse_srt(content: str) -> list[SubtitleBlock]:
    """Parse raw .srt content into a list of SubtitleBlock objects.

    Handles various line-ending styles and tolerates minor formatting issues.
    """
    # Normalize line endings
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    # Strip BOM if present
    if content.startswith("\ufeff"):
        content = content[1:]

    # Split on double newlines (one or more blank lines between blocks)
    raw_blocks = re.split(r"\n\n+", content.strip())

    blocks: list[SubtitleBlock] = []

    for raw in raw_blocks:
        lines = raw.strip().split("\n")
        if len(lines) < 2:
            continue

        # First line: block number
        try:
            number = int(lines[0].strip())
        except ValueError:
            continue

        # Second line: timestamp
        timestamp = lines[1].strip()
        if not TIMESTAMP_RE.match(timestamp):
            continue

        # Remaining lines: subtitle text
        text = "\n".join(lines[2:]) if len(lines) > 2 else ""

        blocks.append(SubtitleBlock(number=number, timestamp=timestamp, text=text))

    return blocks


def serialize_srt(blocks: list[SubtitleBlock]) -> str:
    """Serialize SubtitleBlock list back into .srt file content."""
    parts: list[str] = []
    for block in blocks:
        parts.append(f"{block.number}\n{block.timestamp}\n{block.text}")
    return "\n\n".join(parts) + "\n"


def split_batches(blocks: list[SubtitleBlock], batch_size: int = 15) -> list[list[SubtitleBlock]]:
    """Split blocks into batches of the given size."""
    return [blocks[i : i + batch_size] for i in range(0, len(blocks), batch_size)]


@dataclass
class ValidationResult:
    """Result of batch validation."""

    ok: bool
    error: str = ""


def validate_batch(
    input_blocks: list[SubtitleBlock],
    output_blocks: list[SubtitleBlock],
) -> ValidationResult:
    """Validate that the translated batch matches the input structure."""
    # 1. Block count
    if len(input_blocks) != len(output_blocks):
        return ValidationResult(
            ok=False,
            error=f"Block count mismatch: expected {len(input_blocks)}, got {len(output_blocks)}",
        )

    # 2. Block number sequence
    for i, (inp, out) in enumerate(zip(input_blocks, output_blocks)):
        if inp.number != out.number:
            return ValidationResult(
                ok=False,
                error=f"Block number mismatch at index {i}: expected {inp.number}, got {out.number}",
            )

    # 3. Timestamps unchanged
    for i, (inp, out) in enumerate(zip(input_blocks, output_blocks)):
        if inp.timestamp != out.timestamp:
            return ValidationResult(
                ok=False,
                error=f"Timestamp modified at block {inp.number}: expected '{inp.timestamp}', got '{out.timestamp}'",
            )

    return ValidationResult(ok=True)
