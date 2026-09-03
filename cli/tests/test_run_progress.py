"""The meter behind the status line, mirrored from the web's run-progress.spec:
every step moves the percent, it never goes backwards, and 100 means written."""

from core.run_progress import ProgressMeter, RunProgress


def _meter() -> tuple[ProgressMeter, list[RunProgress]]:
    seen: list[RunProgress] = []
    return ProgressMeter(seen.append), seen


def test_the_prepass_moves_the_percent_instead_of_sitting_at_zero():
    m, seen = _meter()
    m.plan("prepass", 1 + 3)
    m.plan("batches", 10)
    m.count("scan")
    # The scan is weighted like several batch calls: 4 of 17 units.
    assert seen[-1].percent == 23
    m.count("attribution")
    assert seen[-1].percent == 29


def test_the_batch_stage_counts_batches_and_reads_100_only_when_finished():
    m, seen = _meter()
    m.plan("prepass", 1)
    m.plan("batches", 4)
    m.count("scan")
    m.begin("batches")
    m.batches(0, 4)
    assert (seen[-1].stage, seen[-1].done, seen[-1].total) == ("batches", 0, 4)
    for i in range(4):
        m.count("translate")
        m.batches(i + 1, 4)
    # Every planned call is out, but the file is not written yet.
    assert (seen[-1].done, seen[-1].total, seen[-1].percent) == (4, 4, 99)
    m.finish()
    assert seen[-1].percent == 100


def test_the_checks_and_repairs_keep_it_moving_after_the_last_batch():
    m, seen = _meter()
    for stage, calls in (("prepass", 1), ("batches", 2), ("checking", 2), ("repairing", 2)):
        m.plan(stage, calls)
    m.count("scan")
    m.begin("batches")
    m.count("translate")
    m.count("translate")
    m.batches(2, 2)
    after_batches = seen[-1].percent
    assert after_batches < 99
    m.begin("checking")
    m.count("back_translation")
    assert (seen[-1].stage, seen[-1].done, seen[-1].total) == ("checking", 1, 2)
    assert seen[-1].percent > after_batches
    after_checking = seen[-1].percent
    m.begin("repairing")
    m.plan("repairing", 1)
    m.count("repair")
    assert (seen[-1].stage, seen[-1].done, seen[-1].total) == ("repairing", 1, 1)
    assert after_checking < seen[-1].percent < 100


def test_it_never_goes_backwards_when_a_stage_turns_out_bigger_than_planned():
    m, seen = _meter()
    m.plan("prepass", 1)
    m.plan("batches", 2)
    m.count("scan")
    m.begin("batches")
    m.count("translate")
    m.count("translate")
    m.batches(2, 2)
    before = seen[-1].percent
    # Ten repairs nobody planned for: the total grows, the line holds.
    m.begin("repairing")
    m.plan("repairing", 10)
    assert seen[-1].percent == before
    m.count("repair")
    assert seen[-1].percent >= before
    # A stage that overruns its plan grows the plan rather than exceeding it.
    for _ in range(12):
        m.count("repair")
    assert (seen[-1].done, seen[-1].total) == (13, 13)
