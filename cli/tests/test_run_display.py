from core.live_status import Colors
from core.run_display import eta_label, render_status
from core.run_progress import RunProgress

PLAIN = Colors(enabled=False)


def test_the_line_names_the_step_its_count_and_the_percent():
    line = render_status(RunProgress("batches", 63, 88, 71), 62.0, 4.0, PLAIN)
    assert line == "  [translating 63/88 71%] │ batch 4s │ elapsed 1m 2s │ ETA 25s"


def test_the_prepass_has_no_count_worth_showing_and_no_batch_time():
    line = render_status(RunProgress("prepass", 1, 4, 12), 5.0, None, PLAIN)
    assert line == "  [reading the file 12%] │ elapsed 5s │ ETA 37s"


def test_the_later_steps_show_their_own_counts():
    assert render_status(RunProgress("repairing", 5, 11, 93), 100.0, 4.0, PLAIN) \
        == "  [repairing flagged lines 5/11 93%] │ elapsed 1m 40s │ ETA 8s"


def test_the_eta_is_a_dash_until_there_is_something_to_go_on():
    assert eta_label(0, 10) == "—"
    assert eta_label(2, 10) == "—"
    assert eta_label(50, 30) == "30s"
    assert eta_label(100, 30) == "0s"
