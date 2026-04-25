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
    # Prepass scan budget (chars). Sized for full-quality scans on typical
    # TV episodes; lower on tight-context local models (~8k window).
    scan_char_budget: int = 24_000
    # Previous-batch source blocks shown as read-only context; 0 disables.
    context_overlap: int = 2
    # One small LLM call per ambiguous scene; fixes cross-gender addressee slips.
    refine_attribution: bool = True
    # One extra call per batch; fixes gender/number/consistency slips. Doubles cost.
    review: bool = True
    quiet: bool = False
    verbose: bool = False
    warn: Callable[[str], None] = field(default=_silent_warn)
