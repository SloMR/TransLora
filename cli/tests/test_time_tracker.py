import time

from core.time_tracker import EtaEstimator, format_duration


def test_format_duration_seconds() -> None:
    assert format_duration(0) == "0s"
    assert format_duration(1) == "1s"
    assert format_duration(59) == "59s"


def test_format_duration_minutes() -> None:
    assert format_duration(60) == "1m 0s"
    assert format_duration(200) == "3m 20s"
    assert format_duration(3599) == "59m 59s"


def test_format_duration_hours() -> None:
    assert format_duration(3600) == "1h 0m"
    assert format_duration(3900) == "1h 5m"
    assert format_duration(7200) == "2h 0m"


def test_eta_none_before_baseline() -> None:
    start = time.time()
    eta = EtaEstimator(total=10, concurrency=4, start=start)
    # Fewer than `concurrency` completions → no baseline → no estimate.
    for _ in range(3):
        eta.record()
    assert eta.remaining_secs() is None


def test_eta_projects_remaining_after_baseline(monkeypatch) -> None:
    start = 1000.0
    now = [start]
    monkeypatch.setattr(time, "time", lambda: now[0])

    eta = EtaEstimator(total=10, concurrency=2, start=start)
    # First two batches take 4s total → baseline established at done=2.
    now[0] = start + 4.0
    eta.record()
    eta.record()
    # Before any further completions, remaining uses baseline rate as fallback.
    remaining = eta.remaining_secs()
    assert remaining is not None
    # 4s per 2 batches → ~2s per batch → 8 left → ~16s.
    assert 15.0 <= remaining <= 17.0

    # One more batch 2s later → post-baseline rate refines the estimate.
    now[0] = start + 6.0
    eta.record()
    remaining = eta.remaining_secs()
    assert remaining is not None
    # Post-baseline: 1 batch in 2s → 7 remaining → ~14s.
    assert 13.0 <= remaining <= 15.0


def test_eta_none_when_done(monkeypatch) -> None:
    start = 0.0
    monkeypatch.setattr(time, "time", lambda: start)
    eta = EtaEstimator(total=2, concurrency=2, start=start)
    eta.record()
    eta.record()
    assert eta.remaining_secs() is None
