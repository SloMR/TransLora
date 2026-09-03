"""What a run costs: the LLM calls it made, counted by the pass that made
them, and the projection --dry-run shows before a run starts, while the price
can still be changed."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from .constants import (
    ADEQUACY_MIN_BATCHES,
    ADEQUACY_SAMPLE_FRACTION,
    FIX_FLAGGED_FRACTION,
    FIX_FLAGGED_MIN,
    SYSTEMATIC_CAUSE_MIN,
    SYSTEMATIC_REPAIR_FRACTION,
)

# Ordered as a run makes them; reports follow this order.
CALL_KINDS = ("scan", "attribution", "translate", "review", "repair",
              "back_translation")

CALL_KIND_LABELS = {
    "scan": "scan",
    "attribution": "attribution",
    "translate": "translate",
    "review": "review",
    "repair": "repair",
    "back_translation": "back-translation",
}


@dataclass
class CallCounts:
    """LLM calls made so far, by pass. Mutated in place by the passes
    themselves, so one run's config carries one running total."""
    scan: int = 0
    attribution: int = 0
    translate: int = 0
    review: int = 0
    repair: int = 0
    back_translation: int = 0
    # Told about every call as it goes out; the status line's meter listens.
    listener: Callable[[str], None] | None = field(default=None, compare=False, repr=False)

    def count(self, kind: str, times: int = 1) -> None:
        setattr(self, kind, getattr(self, kind) + times)
        if self.listener is not None:
            for _ in range(times):
                self.listener(kind)

    @property
    def total(self) -> int:
        return sum(getattr(self, k) for k in CALL_KINDS)

    def snapshot(self) -> CallCounts:
        return CallCounts(**{kind: getattr(self, kind) for kind in CALL_KINDS})

    def since(self, before: CallCounts) -> CallCounts:
        """What has been counted since `before` was taken."""
        return CallCounts(**{
            kind: getattr(self, kind) - getattr(before, kind) for kind in CALL_KINDS
        })


def describe_calls(counts: CallCounts) -> str:
    """"1 scan, 38 translate, 2 repair" — passes that made no call are left out."""
    return ", ".join(
        f"{getattr(counts, kind)} {CALL_KIND_LABELS[kind]}"
        for kind in CALL_KINDS if getattr(counts, kind)
    )


# Rounding up, never to nearest: the two trees must agree, and Python rounds
# halves to even where JavaScript rounds them up.
def fix_flagged_cap(batch_count: int) -> int:
    """How many batches the flagged-batch repair may re-issue when every cause
    is a one-off."""
    return max(FIX_FLAGGED_MIN, math.ceil(batch_count * FIX_FLAGGED_FRACTION))


def systematic_repair_cap(batch_count: int) -> int:
    """The wider ceiling for a file where one cause keeps recurring: the
    ordinary cap answers a handful of one-offs, and leaves a systematic failure
    almost entirely unrepaired."""
    return max(FIX_FLAGGED_MIN,
               math.ceil(batch_count * SYSTEMATIC_REPAIR_FRACTION))


def group_by_cause(
    flagged: Mapping[int, Sequence[str]],
) -> dict[str, list[int]]:
    """Cause -> the flagged batches carrying it, each in block order."""
    groups: dict[str, list[int]] = {}
    for index in sorted(flagged):
        for cause in dict.fromkeys(flagged[index]):
            groups.setdefault(cause, []).append(index)
    return groups


@dataclass(frozen=True)
class RepairPlan:
    """Which flagged batches to re-issue, and the arithmetic that chose them."""
    selected: list[int]
    cap: int
    causes: int
    flagged: int

    @property
    def skipped(self) -> int:
        return self.flagged - len(self.selected)


def plan_repairs(
    flagged: Mapping[int, Sequence[str]], batch_count: int,
) -> RepairPlan:
    """Pick the batches to re-issue by cause rather than by position.

    Rarest cause first, so one term drifting across the file cannot spend the
    whole budget and crowd out the batches nothing else will look at; within a
    cause, earliest batch first. The ceiling rises only when some cause has
    gone systematic — a run of unrelated one-offs is not evidence of a problem
    worth five times the calls.
    """
    groups = group_by_cause(flagged)
    systematic = any(len(batches) >= SYSTEMATIC_CAUSE_MIN
                     for batches in groups.values())
    cap = (systematic_repair_cap(batch_count) if systematic
           else fix_flagged_cap(batch_count))

    selected: list[int] = []
    seen: set[int] = set()
    # A stable sort, so causes of equal size keep the order they were first
    # flagged in rather than an alphabetical one.
    for cause in sorted(groups, key=lambda c: len(groups[c])):
        for index in groups[cause]:
            if len(selected) >= cap:
                break
            if index not in seen:
                seen.add(index)
                selected.append(index)
    return RepairPlan(sorted(selected), cap, len(groups), len(flagged))


def adequacy_sample_size(batch_count: int) -> int:
    """How many batches the adequacy check back-translates."""
    if batch_count <= 0:
        return 0
    return min(batch_count,
               max(ADEQUACY_MIN_BATCHES,
                   math.ceil(batch_count * ADEQUACY_SAMPLE_FRACTION)))


def sample_indices(batch_count: int, wanted: int) -> list[int]:
    """Every Nth batch, so the sample spans the file rather than its opening."""
    if batch_count <= 0 or wanted <= 0:
        return []
    take = min(batch_count, wanted)
    step = batch_count / take
    return [int(i * step) for i in range(take)]
