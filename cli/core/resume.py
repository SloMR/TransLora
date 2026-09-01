"""Sidecar progress file so a failure or Ctrl-C doesn't discard paid batches.

Keyed on the run's identity, and every reused batch is re-checked against the
batch it stands in for, so a stale or mismatched sidecar is ignored, never used.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from .config import TranslationConfig
from .srt_parser import SubtitleBlock

PROGRESS_SUFFIX = ".translora-progress.json"
PROGRESS_VERSION = 1


def progress_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.name + PROGRESS_SUFFIX)


def run_key(input_path: Path, cfg: TranslationConfig, total_blocks: int) -> dict:
    """Everything that would invalidate an earlier run's batches."""
    return {
        "input": str(input_path.resolve()),
        "target": cfg.target_lang,
        "model": cfg.model or "",
        "batch_size": cfg.batch_size,
        "blocks": total_blocks,
    }


class BatchProgress:
    """Completed batches for one file, persisted after each batch."""

    def __init__(self, path: Path, key: dict) -> None:
        self.path = path
        self.key = key
        self._done: dict[int, list[SubtitleBlock]] = {}

    def load(self) -> int:
        """Read the sidecar if it belongs to this exact run; returns how many
        batches are reusable."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        if not isinstance(data, dict) or data.get("version") != PROGRESS_VERSION:
            return 0
        if data.get("key") != self.key:
            return 0
        done: dict[int, list[SubtitleBlock]] = {}
        for idx, blocks in (data.get("batches") or {}).items():
            try:
                done[int(idx)] = [
                    SubtitleBlock(number=int(b["n"]), timestamp=str(b["ts"]),
                                  text=str(b["text"]))
                    for b in blocks
                ]
            except (TypeError, ValueError, KeyError):
                return 0
        self._done = done
        return len(done)

    def get(self, idx: int, batch: list[SubtitleBlock]) -> list[SubtitleBlock] | None:
        """The stored translation for batch `idx`, if it lines up with that batch."""
        stored = self._done.get(idx)
        if stored is None or len(stored) != len(batch):
            return None
        if any(s.number != b.number
               for s, b in zip(stored, batch, strict=True)):
            return None
        return stored

    def record(self, idx: int, blocks: list[SubtitleBlock]) -> None:
        self._done[idx] = blocks
        self._write()

    def discard(self) -> None:
        """Drop the sidecar — the output file is complete."""
        self._done.clear()
        with contextlib.suppress(OSError):
            self.path.unlink()

    def _write(self) -> None:
        payload = {
            "version": PROGRESS_VERSION,
            "key": self.key,
            "batches": {
                str(idx): [
                    {"n": b.number, "ts": b.timestamp, "text": b.text}
                    for b in blocks
                ]
                for idx, blocks in self._done.items()
            },
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            # Write-then-replace: an interrupted write must not corrupt what we have.
            tmp.write_text(json.dumps(payload, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            # Progress is an optimization; losing it must never fail the run.
            with contextlib.suppress(OSError):
                tmp.unlink()
