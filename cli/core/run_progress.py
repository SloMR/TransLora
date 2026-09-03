"""One percent for the whole run, every step included: the meter behind the
status line. Mirrors web/src/app/core/run-progress.ts: every model call is one
unit of work, each stage is planned at what the estimate would price it and
revised as the run learns the real number, and the percent never goes
backwards — it waits — reading 100 only when the file is written."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

STAGES = ("prepass", "batches", "checking", "repairing")

STAGE_LABELS: dict[str, str] = {
    "prepass": "reading the file",
    "batches": "translating",
    "checking": "checking meaning",
    "repairing": "repairing flagged lines",
}

_STAGE_OF: dict[str, str] = {
    "scan": "prepass",
    "attribution": "prepass",
    "translate": "batches",
    "review": "batches",
    "back_translation": "checking",
    "repair": "repairing",
}

# The scan is one call that sends the whole file and waits for a glossary
# back: it takes about as long as this many batch calls.
SCAN_UNITS = 4


@dataclass(frozen=True)
class RunProgress:
    """Where the run is: the stage, that stage's own count, and the percent."""
    stage: str
    done: int
    total: int
    percent: int


class ProgressMeter:
    def __init__(self, report: Callable[[RunProgress], None] | None = None) -> None:
        self._report = report
        self.stage = "prepass"
        self._planned = dict.fromkeys(STAGES, 0)
        self._calls = dict.fromkeys(STAGES, 0)
        self._batches_done = 0
        self._batches_total = 0
        self._scan_planned = False
        self._scan_done = False
        self._shown = 0
        self._finished = False

    def plan(self, stage: str, calls: int) -> None:
        """How many calls a stage is expected to make; never less than it already has."""
        if stage == "prepass":
            self._scan_planned = True
        self._planned[stage] = max(calls, self._calls[stage])
        self._emit()

    def begin(self, stage: str) -> None:
        self.stage = stage
        self._emit()

    def count(self, kind: str) -> None:
        """One call of `kind` went out. Counted at the request, not the reply:
        with a handful in flight the bar leads by that handful, never by more."""
        stage = _STAGE_OF[kind]
        if kind == "scan":
            self._scan_done = True
        self._calls[stage] += 1
        if self._calls[stage] > self._planned[stage]:
            self._planned[stage] = self._calls[stage]
        self._emit()

    def batches(self, done: int, total: int) -> None:
        """The batch stage counts finished batches, not calls: that is the
        number the line shows, and a batch's retries and reviews would inflate it."""
        self._batches_done = done
        self._batches_total = total
        self._emit()

    def finish(self) -> None:
        self._finished = True
        self._emit()

    def snapshot(self) -> RunProgress:
        if self.stage == "batches":
            done, total = self._batches_done, self._batches_total
        else:
            done, total = self._calls[self.stage], self._planned[self.stage]
        return RunProgress(self.stage, done, total, self._percent())

    def _percent(self) -> int:
        if self._finished:
            return 100
        extra = SCAN_UNITS - 1
        done = sum(self._calls.values()) + (extra if self._scan_done else 0)
        planned = sum(self._planned.values()) + (extra if self._scan_planned else 0)
        raw = (100 * done) // planned if planned else 0
        self._shown = max(self._shown, min(99, raw))
        return self._shown

    def _emit(self) -> None:
        if self._report is not None:
            self._report(self.snapshot())
