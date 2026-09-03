"""The command line itself: the flags, their validation, the exit codes they
document, and the --dry-run report."""

from __future__ import annotations

import argparse
import codecs
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from .batch_runner import FileTranslationError
from .chat_client import sanitize_api_url
from .config import Provider, TranslationConfig
from .constants import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONCURRENCY,
    DEFAULT_CONTEXT_OVERLAP,
    DEFAULT_ENCODING,
    DEFAULT_FIX_FLAGGED,
    DEFAULT_FORMALITY,
    DEFAULT_FULL_ATTRIBUTION,
    DEFAULT_MAX_RETRIES,
    DEFAULT_PARALLEL_FILES,
    DEFAULT_REFINE_ATTRIBUTION,
    DEFAULT_REFLOW,
    DEFAULT_REVIEW,
    DEFAULT_SCAN_CHAR_BUDGET,
    DEFAULT_SEND_TEMPERATURE,
    DEFAULT_VERIFY_ADEQUACY,
    ESTIMATED_SECS_PER_CALL,
    FORMALITY_CHOICES,
    REQUEST_TIMEOUT_SECS,
)
from .languages import effective_norms
from .live_status import Colors
from .time_tracker import format_duration
from .translator import FALLBACK_ENCODINGS, FilePlan, plan_file

C = Colors()

API_KEY_ENV = "TRANSLORA_API_KEY"
REVIEW_API_KEY_ENV = "TRANSLORA_REVIEW_API_KEY"

# Documented in EPILOG — scripts and CI depend on these.
EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INTERRUPTED = 130


EPILOG = f"""\
examples:
  # Local OpenAI-compatible server (no key usually needed)
  python translora.py movie.srt -s English -t Arabic \\
    --api-url http://127.0.0.1:8080/v1/chat/completions

  # Cloud provider (any OpenAI-compatible endpoint)
  python translora.py movie.srt -s English -t Arabic \\
    --api-url https://<provider>/v1/chat/completions \\
    --api-key <key> --model <model-name> -c 10

  # Translate a whole folder in parallel
  python translora.py ./subs/ -s English -t Arabic \\
    --api-url ... --api-key ... --model ... -c 5 -pf 3

  # Scan one episode, then reuse its cast for the rest of the season
  python translora.py ep01.srt -t Arabic --api-url ... --glossary-out cast.json
  python translora.py ./season/ -t Arabic --api-url ... --glossary-in cast.json

  # Send only the review pass to a stronger model
  python translora.py movie.srt -s English -t Arabic --api-url ... \\
    --review-api-url https://<provider>/v1/chat/completions \\
    --review-api-key <key> --review-model <stronger-model>

  # Pin the variant and the register, and cap the line length yourself
  python translora.py movie.srt -t Arabic --api-url ... \\
    --dialect "Egyptian Arabic" --formality informal --max-line-chars 38

line norms:
  Line length, line count and reading speed follow the target language's
  script (42 characters for Latin, Cyrillic and Arabic, 20 for Korean,
  16 for Chinese and Japanese). Translated cues are re-wrapped to the
  source's line count locally, without an extra API call. --dry-run prints
  the norms that will be enforced.

environment:
  {API_KEY_ENV}          API key, used when --api-key is not given
  {REVIEW_API_KEY_ENV}   Review-pass API key, used when --review-api-key
                             is not given

exit codes:
  {EXIT_OK}    every file translated or skipped
  {EXIT_FAILURE}    at least one file failed, or the arguments were invalid
  {EXIT_INTERRUPTED}  interrupted (Ctrl-C)

Any OpenAI-compatible /v1/chat/completions endpoint works. The port and
path for local servers vary by tool — check your server's documentation.
"""


class _ArgumentParser(argparse.ArgumentParser):
    """argparse exits 2 on a bad argument; the CLI documents 1."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        sys.exit(f"{self.prog}: error: {message}")


def _int_at_least(minimum: int):
    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"expected an integer, got {raw!r}") from None
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be >= {minimum}, got {value}")
        return value
    return parse


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a number, got {raw!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {value}")
    return value


def _non_empty(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _encoding_name(raw: str) -> str:
    if raw.strip().lower() == "auto":
        return "auto"
    try:
        codecs.lookup(raw)
    except LookupError:
        raise argparse.ArgumentTypeError(f"unknown encoding: {raw!r}") from None
    return raw


@dataclass
class Job:
    """One input file and where its translation will be written."""
    idx: int
    input_path: Path
    output_path: Path


def build_parser(version: str) -> argparse.ArgumentParser:
    """The whole command line. `version` is translora.py's __version__, which
    scripts/check-versions.sh pins to the other version files."""
    p = _ArgumentParser(
        description="TransLora — translate subtitle files using LLMs",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version",
                   version=f"TransLora CLI {version}")
    p.add_argument("files", nargs="+", type=Path,
                   help="subtitle files or directories (.srt, .vtt, .ass, ...)")
    p.add_argument("--source", "-s", default="",
                   help="Source language (e.g. English, French). "
                        "Omit to auto-detect — useful for mixed-language batches.")
    # A blank target would ask for "nothing" and name the output `movie..srt`.
    p.add_argument("--target", "-t", required=True, type=_non_empty,
                   help="Target language (e.g. Arabic, Spanish, Korean)")
    p.add_argument("--api-url", required=True, help="LLM API endpoint URL")
    p.add_argument("--api-key", default=None,
                   help=f"API key (default: ${API_KEY_ENV}, else none — "
                        "for local servers)")
    p.add_argument("--model", default=None,
                   help="Model name (e.g. gpt-4.1-mini, deepseek-chat)")
    p.add_argument("--review-api-url", default=None, metavar="URL",
                   help="Send the review pass to this endpoint instead "
                        "(default: --api-url). Quality is model-bound and the "
                        "review pass is where a stronger model pays off most.")
    p.add_argument("--review-api-key", default=None, metavar="KEY",
                   help=f"API key for --review-api-url "
                        f"(default: ${REVIEW_API_KEY_ENV}, else --api-key)")
    p.add_argument("--review-model", default=None, metavar="MODEL",
                   help="Model for the review pass (default: --model)")
    p.add_argument("--batch-size", type=_int_at_least(1),
                   default=DEFAULT_BATCH_SIZE,
                   help=f"Subtitle blocks per batch (default: {DEFAULT_BATCH_SIZE})")
    p.add_argument("--concurrency", "-c", type=_int_at_least(1),
                   default=DEFAULT_CONCURRENCY,
                   help=f"Parallel batches per file (default: {DEFAULT_CONCURRENCY}, "
                        "raise for cloud providers)")
    p.add_argument("--parallel-files", "-pf", type=_int_at_least(1),
                   default=DEFAULT_PARALLEL_FILES,
                   help=f"Translate this many files at once "
                        f"(default: {DEFAULT_PARALLEL_FILES})")
    p.add_argument("--max-retries", type=_int_at_least(1),
                   default=DEFAULT_MAX_RETRIES,
                   help=f"Max attempts per batch on HTTP/network failures "
                        f"(default: {DEFAULT_MAX_RETRIES})")
    p.add_argument("--timeout", type=_positive_float, default=REQUEST_TIMEOUT_SECS,
                   metavar="SECS",
                   help=f"Per-request timeout in seconds "
                        f"(default: {REQUEST_TIMEOUT_SECS:g}; raise for local "
                        "CPU inference)")
    p.add_argument("--no-temperature", dest="send_temperature",
                   action="store_false", default=DEFAULT_SEND_TEMPERATURE,
                   help="Don't send a temperature at all. Newer reasoning "
                        "models accept only their own default and reject the "
                        "request otherwise; this is learned from the first "
                        "such refusal anyway, so the flag only saves that one "
                        "corrective round trip.")
    p.add_argument("--encoding", type=_encoding_name, default=DEFAULT_ENCODING,
                   help="Source file encoding (default: auto — utf-8 first, "
                        f"then {', '.join(FALLBACK_ENCODINGS)})")
    p.add_argument("--force", action="store_true",
                   help="Re-translate even if output already exists")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show retry/validation warnings (hidden by default)")
    p.add_argument("--output", "-o", type=Path, default=None,
                   help="Output file path (single file only)")
    p.add_argument("--dry-run", action="store_true",
                   help="Report the planned work and LLM call count, then exit "
                        "without calling the API")
    p.add_argument("--glossary-in", type=Path, default=None, metavar="PATH",
                   help="Load the glossary from a JSON file instead of scanning "
                        "(reuses one episode's cast across a season)")
    p.add_argument("--glossary-out", type=Path, default=None, metavar="PATH",
                   help="Write the scanned glossary to a JSON file (single file only)")
    p.add_argument("--no-resume", dest="resume", action="store_false", default=True,
                   help="Don't reuse or write the <output>.translora-progress.json "
                        "sidecar that lets a re-run skip completed batches")
    p.add_argument("--scan-budget", type=_int_at_least(1),
                   default=DEFAULT_SCAN_CHAR_BUDGET, metavar="CHARS",
                   help=f"Character budget for the prepass scan "
                        f"(default: {DEFAULT_SCAN_CHAR_BUDGET}). "
                        "Tuned for best-quality scans on typical TV episodes; "
                        "lower on tight-context local models (~8k window), "
                        "raise on large-context cloud models for full-file scans.")
    p.add_argument("--context-overlap", type=_int_at_least(0),
                   default=DEFAULT_CONTEXT_OVERLAP, metavar="N",
                   help="Source blocks from the previous batch shown as read-only "
                        f"context (default: {DEFAULT_CONTEXT_OVERLAP}). Helps "
                        "maintain speaker continuity across batch boundaries. "
                        "Set to 0 to disable.")
    p.add_argument("--no-refine-attribution", dest="refine_attribution",
                   action="store_false", default=DEFAULT_REFINE_ATTRIBUTION,
                   help="Disable per-block speaker attribution for two-hander "
                        "scenes (saves one small LLM call per ambiguous scene).")
    p.add_argument("--no-review", dest="review",
                   action="store_false", default=DEFAULT_REVIEW,
                   help="Disable the post-edit review pass (one extra call per "
                        "batch that fixes gender/number/consistency slips).")
    p.add_argument("--formality", choices=FORMALITY_CHOICES,
                   default=DEFAULT_FORMALITY,
                   help=f"Address the viewer formally or informally "
                        f"(default: {DEFAULT_FORMALITY} — follow the source's "
                        "own register).")
    # default=None, not "": argparse runs `type` over a string default.
    p.add_argument("--dialect", type=_non_empty, default=None, metavar="TEXT",
                   help="Target variant, e.g. \"Egyptian Arabic\", \"Brazilian "
                        "Portuguese\". Also used as the prepass register instead "
                        "of the scanned guess.")
    p.add_argument("--max-line-chars", type=_int_at_least(1), default=None,
                   metavar="N",
                   help="Override the target script's line length (default: "
                        "42 for Latin/Cyrillic/Arabic, 20 for Korean, 16 for "
                        "Chinese/Japanese — see --dry-run).")
    p.add_argument("--no-fix-flagged", dest="fix_flagged",
                   action="store_false", default=DEFAULT_FIX_FLAGGED,
                   help="Disable the focused retry of batches flagged for "
                        "dropped tags, glossary drift or cross-line bleeding "
                        "(capped at 5%% of the file's batches, so at most a "
                        "few extra calls).")
    p.add_argument("--verify-adequacy", dest="verify_adequacy",
                   action="store_true", default=DEFAULT_VERIFY_ADEQUACY,
                   help="Back-translate a fifth of the batches and flag lines "
                        "that lost meaning, feeding them to the flagged-batch "
                        "retry. Needs --source. Adds ~20%% more calls.")
    p.add_argument("--full-attribution", dest="full_attribution",
                   action="store_true", default=DEFAULT_FULL_ATTRIBUTION,
                   help="Attribute speakers in every scene with a cast, not "
                        "only the two-hander ones. Adds one small call per "
                        "extra scene.")
    p.add_argument("--no-reflow", dest="reflow", action="store_false",
                   default=DEFAULT_REFLOW,
                   help="Don't re-wrap translated lines to the source's line "
                        "count and the script's line length (deterministic, "
                        "no extra API calls).")
    return p


def _plan_line(plan: FilePlan) -> str:
    attribution = "?" if plan.attribution_calls is None else str(plan.attribution_calls)
    review = f"<={plan.review_calls}" if plan.review_calls else "0"
    repair = f"<={plan.repair_calls}" if plan.repair_calls else "0"
    parts = [
        f"scan {plan.scan_calls}",
        f"attribution {attribution}",
        f"translate {plan.translate_calls}",
        f"review {review}",
        f"repair {repair}",
    ]
    if plan.back_translation_calls:
        parts.append(f"back-translation {plan.back_translation_calls}")
    return "    calls: " + f" {C.dim('|')} ".join(parts)


def _endpoint(provider: Provider) -> str:
    """A provider as a report may show it: the URL with any credential query
    param dropped, and the model. The key is never rendered."""
    return Provider(sanitize_api_url(provider.api_url), "",
                    provider.model).describe()


def _provider_lines(cfg: TranslationConfig) -> list[str]:
    """Which endpoint each pass will call."""
    main, review = cfg.provider, cfg.review_provider
    if review == main:
        return [f"  {C.bold('Provider')}: {C.cyan(_endpoint(main))} "
                f"{C.dim('— every pass')}"]
    own_key = "own key" if cfg.review_api_key else "same key as above"
    return [
        f"  {C.bold('Provider')}: {C.cyan(_endpoint(main))} "
        f"{C.dim('— every pass except review')}",
        f"  {C.bold('Review')}:   {C.cyan(_endpoint(review))} "
        f"{C.dim(f'— review pass ({own_key})')}",
    ]


def _norms_line(cfg: TranslationConfig) -> str:
    """What the deterministic reflow/length pass will enforce for this target."""
    norms = effective_norms(cfg.target_lang, cfg.max_line_chars)
    parts = [
        f"script {norms.script}",
        f"{norms.max_chars_per_line} chars/line"
        + (" (overridden)" if cfg.max_line_chars else ""),
        f"max {norms.max_lines} lines",
        f"~{norms.target_cps} chars/sec",
    ]
    if norms.rtl:
        parts.append("RTL")
    parts.append("reflow on" if cfg.reflow else "reflow off")
    return (f"  {C.bold('Target')}: {C.cyan(cfg.target_lang)} "
            f"{C.dim('— ' + ' | '.join(parts))}")


def _estimate_line(
    plans: list[FilePlan], cfg: TranslationConfig, parallel_files: int,
) -> str:
    """Wall clock at the chosen concurrency, from the upper-bound call count.
    An estimate: the per-call figure is one machine's measurement, and a call's
    cost varies with the model and the batch."""
    lanes = max(1, cfg.concurrency * max(1, parallel_files))
    total = sum(p.upper_bound_calls for p in plans)
    seconds = total / lanes * ESTIMATED_SECS_PER_CALL
    lane_text = (f"{cfg.concurrency} concurrent batch(es)"
                 + (f" x {parallel_files} file(s)" if parallel_files > 1 else ""))
    return C.dim(
        f"  Estimated wall clock: {format_duration(seconds)} at {lane_text} "
        f"— assumes {ESTIMATED_SECS_PER_CALL:g}s per call; an estimate, not a "
        f"measurement."
    )


def print_dry_run(
    cfg: TranslationConfig, jobs: list[Job], parallel_files: int = 1,
) -> int:
    """Report the planned work without making a single API call."""
    print(f"\n{C.bold('Dry run')} {C.dim('— no API calls will be made')}\n")
    for line in _provider_lines(cfg):
        print(line)
    print(_norms_line(cfg))
    print()
    plans: list[FilePlan] = []
    errors: list[str] = []
    for job in jobs:
        try:
            plans.append(plan_file(job.input_path, job.output_path, cfg))
        except FileTranslationError as e:
            errors.append(f"{job.input_path.name}: {e}")
        except Exception as e:
            errors.append(f"{job.input_path.name}: {type(e).__name__}: {e}")

    for plan in plans:
        detail = (f"{plan.blocks} block(s), {plan.batches} batch(es), "
                  f"{plan.encoding}")
        if plan.reused:
            detail += f", {plan.reused} batch(es) reusable from a previous run"
        print(f"  {C.cyan(plan.input_path.name)} {C.dim('→')} {plan.output_path}")
        print(C.dim(f"    {detail}"))
        print(_plan_line(plan))

    if plans:
        known = sum(p.known_calls for p in plans)
        review = sum(p.review_calls for p in plans)
        repair = sum(p.repair_calls for p in plans)
        unknown_attribution = any(p.attribution_calls is None for p in plans)
        rule = C.dim("─" * 60)
        print(f"\n{rule}")
        print(f"  {len(plans)} file(s), {sum(p.blocks for p in plans)} block(s), "
              f"{sum(p.batches for p in plans)} batch(es)")
        print(f"  {C.bold('LLM calls')}: {known}"
              + (f" + up to {review} review" if review else "")
              + (f" + up to {repair} repair" if repair else "")
              + (" + one per two-hander scene (known only after the scan)"
                 if unknown_attribution else ""))
        print(C.dim("  Retries and validation splits can add calls."))
        print(_estimate_line(plans, cfg, parallel_files))
        print(rule)

    for err in errors:
        print(C.red(f"  {err}"), file=sys.stderr)
    return EXIT_FAILURE if errors else EXIT_OK
