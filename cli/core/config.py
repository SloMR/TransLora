"""Configuration dataclass for a single translation run."""

import sys
from dataclasses import dataclass, field
from typing import Callable


DEFAULT_MAX_RETRIES = 5


def _silent_warn(msg: str) -> None:
    pass


def _stderr_warn(msg: str) -> None:
    print(msg, file=sys.stderr)


@dataclass
class TranslationConfig:
    """Everything a translation run needs beyond the file paths.

    Bundled so we aren't threading 8+ arguments through every helper.
    `warn` lets callers intercept retry/validation messages so they can be
    routed around a live progress line instead of clobbering it. Default is
    silent — pass --verbose on the CLI to surface retry/validation chatter.
    """
    source_lang: str  # "" means auto-detect from the text
    target_lang: str
    api_url: str
    api_key: str
    model: str | None = None
    batch_size: int = 15
    concurrency: int = 1
    max_retries: int = DEFAULT_MAX_RETRIES
    quiet: bool = False
    verbose: bool = False
    warn: Callable[[str], None] = field(default=_silent_warn)
