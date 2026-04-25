"""Configuration dataclass for a single translation run."""

import sys
from dataclasses import dataclass, field
from typing import Callable

from .constants import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONCURRENCY,
    DEFAULT_CONTEXT_OVERLAP,
    DEFAULT_MAX_RETRIES,
    DEFAULT_SCAN_CHAR_BUDGET,
)


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
    batch_size: int = DEFAULT_BATCH_SIZE
    concurrency: int = DEFAULT_CONCURRENCY
    max_retries: int = DEFAULT_MAX_RETRIES
    scan_char_budget: int = DEFAULT_SCAN_CHAR_BUDGET
    context_overlap: int = DEFAULT_CONTEXT_OVERLAP
    # One small LLM call per ambiguous scene; fixes cross-gender addressee slips.
    refine_attribution: bool = True
    # One extra call per batch; fixes gender/number/consistency slips. Doubles cost.
    review: bool = True
    quiet: bool = False
    verbose: bool = False
    warn: Callable[[str], None] = field(default=_silent_warn)
