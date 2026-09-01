"""In-place terminal status: ANSI-aware clearing and the background ticker."""

from __future__ import annotations

import io
import threading

from core.live_status import Colors, LiveLine, Ticker, _visible_len, restore_terminal


def test_visible_len_ignores_ansi_sequences() -> None:
    assert _visible_len("plain") == 5
    assert _visible_len("\033[31mred\033[0m") == 3
    # An escape with no terminator ends the count — the rest is unprintable.
    assert _visible_len("abc\033[31") == 3


def test_colors_are_plain_when_disabled() -> None:
    plain, fancy = Colors(enabled=False), Colors(enabled=True)
    assert plain.red("x") == "x"
    assert fancy.red("x") == "\033[31mx\033[0m"


def test_live_line_pads_over_a_longer_previous_line() -> None:
    stream = io.StringIO()
    live = LiveLine(enabled=True, stream=stream)
    live.update("a longer status line")
    live.update("short")
    # The tail of the previous line is overwritten with spaces, not left behind.
    assert stream.getvalue() == "\ra longer status line\rshort" + " " * 15


def test_live_line_measures_width_without_ansi() -> None:
    stream = io.StringIO()
    live = LiveLine(enabled=True, stream=stream)
    live.update(Colors(enabled=True).red("12345"))
    live.update("1")
    assert stream.getvalue().endswith("\r1" + " " * 4)


def test_live_line_println_clears_the_live_line_first() -> None:
    stream = io.StringIO()
    live = LiveLine(enabled=True, stream=stream)
    live.update("status")
    live.println("a warning")
    out = stream.getvalue()
    assert "\r      \r" in out          # 6 spaces over "status"
    assert out.endswith("a warning\n")
    # After a println the live line is gone, so nothing is padded over it.
    live.update("x")
    assert stream.getvalue().endswith("\rx")


def test_live_line_falls_back_to_plain_lines_when_disabled() -> None:
    stream = io.StringIO()
    live = LiveLine(enabled=False, stream=stream)
    live.update("one")
    live.update("two")
    live.finalize()
    assert stream.getvalue() == "one\ntwo\n"
    assert "\r" not in stream.getvalue()


def test_live_line_finalize_closes_the_line_once() -> None:
    stream = io.StringIO()
    live = LiveLine(enabled=True, stream=stream)
    live.update("status")
    live.finalize()
    live.finalize()
    assert stream.getvalue().count("\n") == 1


def test_restore_terminal_is_a_no_op_off_a_tty() -> None:
    stream = io.StringIO()
    restore_terminal(stream)
    assert stream.getvalue() == ""


def test_ticker_renders_until_stopped_and_survives_a_raising_callback() -> None:
    ticked = threading.Event()
    calls: list[int] = []

    def render() -> None:
        calls.append(1)
        ticked.set()
        raise RuntimeError("render blew up")

    ticker = Ticker(render, interval=0.01)
    ticker.start()
    assert ticked.wait(timeout=2.0)
    ticker.stop()
    before = len(calls)
    assert before >= 1
    # stop() joins the thread: no further renders after it returns.
    ticked.clear()
    assert not ticked.wait(timeout=0.1)
    assert len(calls) == before
