
from core.time_tracker import format_duration


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
