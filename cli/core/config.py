"""Configuration for a single translation run: the run's knobs, the one or
two providers its passes call, and the request dialect each of them turned
out to need."""

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .constants import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONCURRENCY,
    DEFAULT_CONTEXT_OVERLAP,
    DEFAULT_DIALECT,
    DEFAULT_ENCODING,
    DEFAULT_FIX_FLAGGED,
    DEFAULT_FORMALITY,
    DEFAULT_FULL_ATTRIBUTION,
    DEFAULT_MAX_RETRIES,
    DEFAULT_REFINE_ATTRIBUTION,
    DEFAULT_REFLOW,
    DEFAULT_REVIEW,
    DEFAULT_SCAN_CHAR_BUDGET,
    DEFAULT_SEND_TEMPERATURE,
    DEFAULT_VERIFY_ADEQUACY,
    REASONING_EFFORT_PREFERENCE,
    REQUEST_TIMEOUT_SECS,
    TOKEN_PARAM_COMPLETION,
    TOKEN_PARAM_DEFAULT,
)
from .run_stats import CallCounts


def _silent_warn(msg: str) -> None:
    pass


def _stderr_warn(msg: str) -> None:
    print(msg, file=sys.stderr)


@dataclass(frozen=True)
class Provider:
    """Where one pass sends its requests. The key is carried, never rendered:
    `describe` is what reports and logs are allowed to show."""
    api_url: str
    api_key: str
    model: str | None = None

    def describe(self) -> str:
        return f"{self.api_url} (model {self.model})" if self.model \
            else self.api_url


# Providers word the two quirks differently, but each one names the parameter
# it is rejecting. Learned from the server so no model list can go stale.
_TOKEN_PARAM_RE = re.compile(
    r"max_tokens.*not supported|use 'max_completion_tokens'", re.I)
_TEMPERATURE_RE = re.compile(
    r"temperature.*(?:does not support|unsupported value)", re.I)

# The wording of the one-off warning, and of the run's stats line.
TOKEN_PARAM_CHANGE = "max_completion_tokens instead of max_tokens"
TEMPERATURE_CHANGE = "the default temperature"
# "Could not finish the message because max_tokens or model output limit was
# reached" — a reasoning model asking for room it will only spend thinking.
_OUTPUT_LIMIT_RE = re.compile(
    r"could not finish the message|model output limit was reached", re.I)
# "Unsupported value: 'reasoning_effort' does not support 'minimal' with this
# model. Supported values are: 'none', 'low', 'medium', 'high', and 'xhigh'."
_REASONING_EFFORT_RE = re.compile(
    r"reasoning_effort.*(?:does not support|unsupported value)", re.I)
_SUPPORTED_VALUES_RE = re.compile(r"supported values are:?\s*([^.]+)", re.I)
REASONING_CHANGE = "reasoning_effort"
REASONING_DROP_CHANGE = "no reasoning_effort"


def dialect_warning(change: str) -> str:
    """The one-off notice a narrowed dialect reports through the run's sink."""
    return f"Provider requires {change}; adjusted for the rest of the run."


@dataclass
class ProviderDialect:
    """The request shape one endpoint accepts, learned from its own 400s.

    Starts at the permissive default and only ever narrows, so a run pays at
    most one corrective round trip per quirk rather than one per call.
    """
    token_param: str = TOKEN_PARAM_DEFAULT
    send_temperature: bool = DEFAULT_SEND_TEMPERATURE
    # Set once an endpoint proves it reasons: the least effort it accepts, or
    # None once it has refused every value we know.
    reasoning_effort: str | None = None

    def adjust_for(self, body: str) -> list[str]:
        """Apply what a 400 body asks for; returns the changes actually made.
        A body that blames something already corrected changes nothing, so the
        caller stops instead of re-sending the same request forever."""
        text = body or ""
        changes: list[str] = []
        if self.token_param == TOKEN_PARAM_DEFAULT and _TOKEN_PARAM_RE.search(text):
            self.token_param = TOKEN_PARAM_COMPLETION
            # An endpoint that wants max_completion_tokens is a reasoning endpoint.
            self.reasoning_effort = REASONING_EFFORT_PREFERENCE[0]
            changes.append(TOKEN_PARAM_CHANGE)
        if self.reasoning_effort is None and _OUTPUT_LIMIT_RE.search(text):
            self.reasoning_effort = REASONING_EFFORT_PREFERENCE[0]
            changes.append(f"{REASONING_CHANGE} '{self.reasoning_effort}'")
        if self.reasoning_effort is not None and _REASONING_EFFORT_RE.search(text):
            # The refusal names what the model takes; ask for the least of it.
            listed = _SUPPORTED_VALUES_RE.search(text)
            offered = re.findall(r"'([^']+)'", listed.group(1)) if listed else []
            chosen = next((v for v in REASONING_EFFORT_PREFERENCE if v in offered), None)
            if chosen == self.reasoning_effort:
                chosen = None  # refused the very value it lists: stop asking
            self.reasoning_effort = chosen
            changes.append(f"{REASONING_CHANGE} '{chosen}'" if chosen else REASONING_DROP_CHANGE)
        if self.send_temperature and _TEMPERATURE_RE.search(text):
            self.send_temperature = False
            changes.append(TEMPERATURE_CHANGE)
        return changes

    def describe(self) -> str:
        """What this endpoint needed that the defaults do not give it; empty
        when nothing was negotiated."""
        parts = []
        if self.token_param != TOKEN_PARAM_DEFAULT:
            parts.append(self.token_param)
        if not self.send_temperature:
            parts.append("no temperature")
        if self.reasoning_effort is not None:
            parts.append(f"reasoning_effort {self.reasoning_effort}")
        return ", ".join(parts)


def dialect_key(provider: Provider) -> tuple[str, str]:
    """Two endpoints share a dialect when URL and model match: which key they
    were reached with cannot change what the server accepts."""
    return (provider.api_url, provider.model or "")


@dataclass
class TranslationConfig:
    """Per-run config. `warn` is the retry/validation sink — silent by default,
    rebindable by callers so it can route around a live progress line."""
    source_lang: str  # "" means auto-detect
    target_lang: str
    api_url: str
    api_key: str
    model: str | None = None
    # A second provider for the review pass only — a stronger model pays off
    # most there. Each field falls back to the main provider's.
    review_api_url: str = ""
    review_api_key: str = ""
    review_model: str | None = None
    batch_size: int = DEFAULT_BATCH_SIZE
    concurrency: int = DEFAULT_CONCURRENCY
    max_retries: int = DEFAULT_MAX_RETRIES
    scan_char_budget: int = DEFAULT_SCAN_CHAR_BUDGET
    context_overlap: int = DEFAULT_CONTEXT_OVERLAP
    # One small LLM call per ambiguous scene; fixes cross-gender addressee slips.
    refine_attribution: bool = DEFAULT_REFINE_ATTRIBUTION
    # One extra call per batch; fixes gender/number/consistency slips. Doubles cost.
    review: bool = DEFAULT_REVIEW
    # Re-wrap a translated cue to the source's line count without a model call.
    reflow: bool = DEFAULT_REFLOW
    # One focused retry per flagged batch, capped at a fraction of the file.
    fix_flagged: bool = DEFAULT_FIX_FLAGGED
    # Back-translate a sample of batches and flag the cues that lost meaning.
    verify_adequacy: bool = DEFAULT_VERIFY_ADEQUACY
    # Attribute speakers in every scene with a cast, not only two-hander ones.
    full_attribution: bool = DEFAULT_FULL_ATTRIBUTION
    # None = the target script's own default.
    max_line_chars: int | None = None
    # Address the viewer formally/informally; "auto" follows the source.
    formality: str = DEFAULT_FORMALITY
    # Free-text target variant, e.g. "Saudi Arabic".
    dialect: str = DEFAULT_DIALECT
    encoding: str = DEFAULT_ENCODING
    request_timeout: float = REQUEST_TIMEOUT_SECS
    # Starting point for every dialect: False skips the first call's 400 on an
    # endpoint the user already knows rejects an explicit temperature.
    send_temperature: bool = DEFAULT_SEND_TEMPERATURE
    # Glossary reuse across a series: load instead of scanning, and/or save.
    glossary_in: Path | None = None
    glossary_out: Path | None = None
    # Keep completed batches in a sidecar so a failure doesn't discard them.
    resume: bool = True
    quiet: bool = False
    verbose: bool = False
    warn: Callable[[str], None] = field(default=_silent_warn)
    # Progress lines for a single-file run; the status line reroutes this so
    # nothing prints through it.
    say: Callable[[str], None] = field(default=print)
    # Running per-run call total, incremented by every pass that calls out.
    calls: CallCounts = field(default_factory=CallCounts)
    # What each endpoint turned out to accept, learned once and reused for the
    # rest of the run.
    dialects: dict[tuple[str, str], ProviderDialect] = field(default_factory=dict)

    def dialect_for(self, provider: Provider) -> ProviderDialect:
        """This endpoint's dialect, created on first use. Mutable and shared:
        the main and review passes learn together when they are one endpoint."""
        key = dialect_key(provider)
        dialect = self.dialects.get(key)
        if dialect is None:
            dialect = ProviderDialect(send_temperature=self.send_temperature)
            self.dialects[key] = dialect
        return dialect

    @property
    def provider(self) -> Provider:
        """The endpoint every pass uses unless it says otherwise."""
        return Provider(self.api_url, self.api_key, self.model)

    @property
    def review_provider(self) -> Provider:
        """The review pass's endpoint: the main one, field by field, until an
        override replaces a field."""
        return Provider(self.review_api_url or self.api_url,
                        self.review_api_key or self.api_key,
                        self.review_model or self.model)


def dialect_notes(cfg: TranslationConfig) -> list[str]:
    """The request shapes this run negotiated, one line per endpoint that
    needed one. Empty for a provider that accepted the defaults; the endpoint
    is named only by a run that has more than one to tell apart."""
    negotiated: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, provider in (("provider", cfg.provider),
                            ("review", cfg.review_provider)):
        key = dialect_key(provider)
        if key in seen:
            continue
        seen.add(key)
        described = cfg.dialect_for(provider).describe()
        if described:
            negotiated.append((label, described))
    if len(seen) == 1:
        return [described for _, described in negotiated]
    return [f"{label}: {described}" for label, described in negotiated]
