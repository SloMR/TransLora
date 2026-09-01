"""What a run costs: the per-pass call counter, the caps the optional passes
obey, and the projection --dry-run prints before anything is paid for.

The caps and the sample stride are mirrored in web/src/app/core/run-stats.ts;
test_parity.py pins the constants they are built from.
"""

from __future__ import annotations

import pytest

from core.constants import (
    ADEQUACY_MIN_BATCHES,
    ESTIMATED_SECS_PER_CALL,
    FIX_FLAGGED_MIN,
)
from core.run_stats import (
    CALL_KINDS,
    CallCounts,
    adequacy_sample_size,
    describe_calls,
    fix_flagged_cap,
    group_by_cause,
    plan_repairs,
    project_run,
    sample_indices,
    systematic_repair_cap,
)

# === The counter =============================================================


def test_the_counter_starts_empty_and_totals_every_kind() -> None:
    counts = CallCounts()
    assert counts.total == 0
    for kind in CALL_KINDS:
        counts.count(kind)
    assert counts.total == len(CALL_KINDS)


def test_a_snapshot_is_a_copy_the_running_total_cannot_change() -> None:
    counts = CallCounts()
    counts.count("translate", 3)
    before = counts.snapshot()
    counts.count("translate", 2)
    counts.count("review")
    assert before.translate == 3
    # What this file cost, not what the whole run has cost so far.
    made = counts.since(before)
    assert (made.translate, made.review, made.total) == (2, 1, 3)


def test_describe_calls_names_only_the_passes_that_fired() -> None:
    counts = CallCounts(scan=1, translate=38, repair=2)
    assert describe_calls(counts) == "1 scan, 38 translate, 2 repair"
    assert describe_calls(CallCounts()) == ""


def test_the_kinds_are_reported_in_the_order_a_run_makes_them() -> None:
    assert CALL_KINDS == ("scan", "attribution", "translate", "review",
                          "repair", "back_translation")


# === Caps ====================================================================


@pytest.mark.parametrize("batches,cap", [
    (1, 2), (10, 2), (38, 2), (40, 2), (41, 3), (100, 5), (372, 19),
])
def test_the_repair_cap_is_five_percent_of_the_file_but_never_below_two(
    batches: int, cap: int,
) -> None:
    assert fix_flagged_cap(batches) == cap
    assert cap >= FIX_FLAGGED_MIN


@pytest.mark.parametrize("batches,cap", [
    (1, 2), (10, 3), (38, 10), (40, 10), (100, 25), (372, 93),
])
def test_the_raised_cap_is_a_quarter_of_the_file_but_never_below_the_old_one(
    batches: int, cap: int,
) -> None:
    assert systematic_repair_cap(batches) == cap
    assert cap >= fix_flagged_cap(batches)


# === Choosing what to repair =================================================


def test_flagged_batches_are_grouped_by_what_went_wrong_not_where() -> None:
    groups = group_by_cause({3: ["term:policy"], 1: ["term:policy", "tags"]})
    # Block order inside a cause, so a repair budget is spent front to back.
    assert groups == {"term:policy": [1, 3], "tags": [1]}


def test_one_cause_across_the_file_is_one_problem_and_buys_the_raised_cap() -> None:
    # The graded run: 16 of 38 batches flagged, 14 of them left unrepaired
    # because 5% of the file is two batches.
    flagged = {i: ["term:that's what she said"] for i in range(16)}
    plan = plan_repairs(flagged, batch_count=38)
    assert (plan.flagged, plan.causes, plan.cap) == (16, 1, 10)
    assert plan.selected == list(range(10))
    assert plan.skipped == 6


def test_a_scatter_of_one_offs_does_not_buy_the_raised_cap() -> None:
    """Three unrelated causes are three unrelated problems; nothing here says
    the file needs five times the repair budget."""
    flagged = {0: ["tags"], 5: ["dashes"], 9: ["shift"]}
    plan = plan_repairs(flagged, batch_count=38)
    assert (plan.causes, plan.cap) == (3, 2)
    assert len(plan.selected) == 2


def test_the_rarest_cause_is_repaired_first_so_it_is_not_crowded_out() -> None:
    flagged = {i: ["term:policy"] for i in range(6)}
    flagged[9] = ["leak:latin"]
    plan = plan_repairs(flagged, batch_count=20)
    # Cap is 5 (a quarter of 20); the lone leak takes a slot before the six
    # batches sharing one cause spend the rest.
    assert plan.cap == 5
    assert 9 in plan.selected
    assert plan.selected == [0, 1, 2, 3, 9]


def test_a_batch_flagged_twice_is_only_repaired_once() -> None:
    plan = plan_repairs({0: ["tags", "shift"], 1: ["tags"]}, batch_count=40)
    assert plan.selected == [0, 1]
    assert plan.flagged == 2


def test_nothing_flagged_plans_nothing() -> None:
    plan = plan_repairs({}, batch_count=38)
    assert plan.selected == []
    assert (plan.flagged, plan.causes, plan.skipped) == (0, 0, 0)


@pytest.mark.parametrize("batches,size", [
    (0, 0), (1, 1), (5, 2), (10, 2), (38, 8), (100, 20),
])
def test_the_adequacy_sample_is_a_fifth_of_the_file_but_never_below_two(
    batches: int, size: int,
) -> None:
    assert adequacy_sample_size(batches) == size
    # A two-batch minimum cannot ask for batches that do not exist.
    assert size <= batches or batches == 0
    if batches >= ADEQUACY_MIN_BATCHES:
        assert size >= ADEQUACY_MIN_BATCHES


def test_the_sample_spans_the_file_instead_of_its_opening() -> None:
    picked = sample_indices(38, adequacy_sample_size(38))
    assert picked == [0, 4, 9, 14, 19, 23, 28, 33]
    assert len(set(picked)) == len(picked)
    assert max(picked) < 38


@pytest.mark.parametrize("batches,wanted", [(0, 3), (5, 0), (-1, 2)])
def test_an_empty_sample_is_asked_for_nothing(batches: int, wanted: int) -> None:
    assert sample_indices(batches, wanted) == []


def test_the_sample_never_asks_for_more_batches_than_there_are() -> None:
    assert sample_indices(3, 10) == [0, 1, 2]


# === Projection ==============================================================


def test_the_projection_prices_every_pass_that_is_switched_on() -> None:
    projection = project_run(
        [372], batch_size=10, lanes=1,
        review=True, fix_flagged=True, verify_adequacy=True,
    )
    calls = projection.calls
    # 38 batches: one scan, one call each, review each, capped repair, sampled
    # back-translation. Attribution is unknowable before the scan replies.
    assert (calls.scan, calls.translate, calls.review) == (1, 38, 38)
    assert (calls.repair, calls.back_translation) == (2, 8)
    assert calls.attribution == 0
    assert projection.total == 1 + 38 + 38 + 2 + 8


def test_the_optional_passes_cost_nothing_when_they_are_off() -> None:
    projection = project_run(
        [372], batch_size=10, lanes=1,
        review=False, fix_flagged=False, verify_adequacy=False,
    )
    assert projection.total == 39
    assert projection.calls.repair == 0


def test_the_default_run_costs_about_five_percent_more_than_before_repair() -> None:
    """The repair pass is on by default, so it has to stay small. The
    projection prices the ordinary cap: the raised one is bought by a
    systematic failure, which nothing can see before the file is translated."""
    without = project_run([372], 10, 1, review=True, fix_flagged=False,
                          verify_adequacy=False).total
    with_repair = project_run([372], 10, 1, review=True, fix_flagged=True,
                              verify_adequacy=False).total
    assert (with_repair - without) / without <= 0.05


def test_concurrency_divides_the_wall_clock_estimate() -> None:
    serial = project_run([372], 10, 1, True, False, False)
    parallel = project_run([372], 10, 6, True, False, False)
    assert serial.estimated_secs == serial.total * ESTIMATED_SECS_PER_CALL
    assert parallel.estimated_secs == pytest.approx(serial.estimated_secs / 6)
    assert parallel.secs_per_call == ESTIMATED_SECS_PER_CALL


def test_every_file_is_batched_on_its_own() -> None:
    # Two 15-block files are 4 batches, not the 3 that 30 blocks would be.
    projection = project_run([15, 15], 10, 1, False, False, False)
    assert projection.calls.translate == 4
    assert projection.calls.scan == 2


def test_an_empty_file_is_priced_at_nothing() -> None:
    assert project_run([0], 10, 1, True, True, True).total == 0
