"""The status line a single-file run keeps at the bottom of the terminal, from
the first scan call to the written file: which step, how far through it, the
percent for the whole run and an ETA from it. Notes and warnings print above
it. A pipe or a CI log gets one plain line per change instead of a refresh."""

from __future__ import annotations

import sys
import time

from .live_status import Colors, LiveLine, Ticker
from .run_progress import STAGE_LABELS, ProgressMeter, RunProgress
from .time_tracker import format_duration

# Below this the ETA is a guess from almost nothing; say so instead.
ETA_MIN_PERCENT = 3


def render_status(
    progress: RunProgress, elapsed: float, batch_elapsed: float | None,
    colors: Colors,
) -> str:
    """One line: `[translating 63/88 · 71%] │ batch 4s │ elapsed 1m 2s │ ETA 40s`."""
    label = STAGE_LABELS.get(progress.stage, progress.stage)
    count = (f" {progress.done}/{progress.total}"
             if progress.stage != "prepass" and progress.total else "")
    sep = colors.dim("│")
    parts = [f"  [{colors.cyan(label + count)} {colors.dim(f'{progress.percent}%')}]"]
    if progress.stage == "batches" and batch_elapsed is not None:
        parts.append(f"{colors.dim('batch')} {format_duration(batch_elapsed)}")
    parts.append(f"{colors.dim('elapsed')} {format_duration(elapsed)}")
    parts.append(f"{colors.dim('ETA')} {colors.magenta(eta_label(progress.percent, elapsed))}")
    return f" {sep} ".join(parts)


def eta_label(percent: int, elapsed: float) -> str:
    if percent >= 100:
        return "0s"
    if percent < ETA_MIN_PERCENT or elapsed <= 0:
        return "—"
    return format_duration(elapsed * (100 - percent) / percent)


class RunDisplay:
    """Owns the live line, the meter that feeds it and the once-a-second refresh."""

    def __init__(self, colors: Colors, started_at: float, stream=sys.stdout) -> None:
        self.colors = colors
        self.started_at = started_at
        self.live = LiveLine(stream=stream)
        self.meter = ProgressMeter(self._on_progress)
        self.progress: RunProgress | None = None
        self.batch_elapsed: float | None = None
        self._ticker = Ticker(self.render, interval=1.0) if self.live.enabled else None

    def start(self) -> None:
        if self._ticker is not None:
            self._ticker.start()

    def stop(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()
        self.live.finalize()

    def say(self, text: str) -> None:
        """A line above the status, never through it."""
        self.live.println(text)

    def warn(self, text: str) -> None:
        self.live.println(self.colors.yellow(text), file=sys.stderr)

    def render(self) -> None:
        if self.progress is None:
            return
        self.live.update(render_status(
            self.progress, time.time() - self.started_at, self.batch_elapsed,
            self.colors))

    def _on_progress(self, progress: RunProgress) -> None:
        # Stored, not drawn: the ticker draws on a TTY, and a pipe gets a line
        # per stage or batch from the callers, not one per call.
        stage_changed = self.progress is None or self.progress.stage != progress.stage
        self.progress = progress
        if stage_changed and not self.live.enabled:
            self.render()
