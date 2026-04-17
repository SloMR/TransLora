"""Terminal-friendly live status rendering: ANSI colors, in-place line
updates, and a background ticker for multi-file progress.

All output is inert (plain text) when stdout isn't a TTY or when the
user sets `NO_COLOR=1` — this way logs piped to a file stay clean.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

class Colors:
    """ANSI color helpers. Becomes a no-op when colors are disabled."""

    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = (
                sys.stdout.isatty()
                and os.environ.get("NO_COLOR", "") == ""
                and os.environ.get("TERM", "") != "dumb"
            )
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def green(self, t: str) -> str:   return self._wrap("32", t)
    def red(self, t: str) -> str:     return self._wrap("31", t)
    def yellow(self, t: str) -> str:  return self._wrap("33", t)
    def cyan(self, t: str) -> str:    return self._wrap("36", t)
    def magenta(self, t: str) -> str: return self._wrap("35", t)
    def dim(self, t: str) -> str:     return self._wrap("2", t)
    def bold(self, t: str) -> str:    return self._wrap("1", t)


# ---------------------------------------------------------------------------
# In-place single-line updates
# ---------------------------------------------------------------------------

class LiveLine:
    """A single terminal line you can rewrite over and over with `update()`.

    Use `println()` to print something *above* the live line (e.g. an error
    or a completion message) without clobbering it. When you're done, call
    `finalize()` to move the cursor to a fresh line.

    When the output isn't a TTY, `update()` just prints each message as its
    own line so logs stay readable.
    """

    def __init__(self, enabled: bool | None = None, stream=sys.stdout) -> None:
        if enabled is None:
            enabled = stream.isatty()
        self.enabled = enabled
        self.stream = stream
        self._last_len = 0
        self._lock = threading.Lock()

    def update(self, text: str) -> None:
        if not self.enabled:
            with self._lock:
                print(text, file=self.stream, flush=True)
            return
        # Strip ANSI codes when measuring width so padding works correctly.
        visible_len = _visible_len(text)
        with self._lock:
            pad = " " * max(0, self._last_len - visible_len)
            self.stream.write(f"\r{text}{pad}")
            self.stream.flush()
            self._last_len = visible_len

    def println(self, text: str, file=None) -> None:
        """Print `text` on its own line, above the live status."""
        target = file or self.stream
        with self._lock:
            if self.enabled and self._last_len:
                self.stream.write("\r" + " " * self._last_len + "\r")
                self.stream.flush()
            print(text, file=target, flush=True)
            self._last_len = 0

    def finalize(self) -> None:
        """Drop a newline after the live line so normal prints resume below."""
        with self._lock:
            if self.enabled and self._last_len:
                self.stream.write("\n")
                self.stream.flush()
                self._last_len = 0


def _visible_len(text: str) -> int:
    """Length of `text` ignoring ANSI escape sequences."""
    out = 0
    i = 0
    while i < len(text):
        if text[i] == "\033" and i + 1 < len(text) and text[i + 1] == "[":
            # Skip until the final 'm'.
            j = text.find("m", i + 2)
            if j == -1:
                break
            i = j + 1
            continue
        out += 1
        i += 1
    return out


# ---------------------------------------------------------------------------
# Background ticker
# ---------------------------------------------------------------------------

class Ticker:
    """Calls `render_fn` from a background thread every `interval` seconds.

    Swallows exceptions inside the callback so a transient render error can't
    kill the whole translation run.
    """

    def __init__(self, render_fn: Callable[[], None], interval: float = 1.0) -> None:
        self._render = render_fn
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._render()
            except Exception:
                pass
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
