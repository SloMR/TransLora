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
    """Per-run config. `warn` is the retry/validation sink — silent by default,
    rebindable by callers so it can route around a live progress line."""
    source_lang: str  # "" means auto-detect
    target_lang: str
    api_url: str
    api_key: str
    model: str | None = None
    batch_size: int = 10
    concurrency: int = 1
    max_retries: int = DEFAULT_MAX_RETRIES
    quiet: bool = False
    verbose: bool = False
    warn: Callable[[str], None] = field(default=_silent_warn)
