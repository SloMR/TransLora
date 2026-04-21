#!/usr/bin/env python3
"""TransLora CLI — AI-powered subtitle translation."""

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from core.config import DEFAULT_MAX_RETRIES, TranslationConfig, _stderr_warn
from core.batch_runner import FileTranslationError
from core.time_tracker import format_duration
from core.lang_codes import lang_code
from core.translator import translate_file_async
from core.live_status import Colors, LiveLine, Ticker

__version__ = "0.3.1"

SUBTITLE_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub", ".sbv"}

EPILOG = """\
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

Any OpenAI-compatible /v1/chat/completions endpoint works. The port and
path for local servers vary by tool — check your server's documentation.
"""


# Module-level palette — colors auto-disable on non-TTY / NO_COLOR.
C = Colors()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="TransLora — translate subtitle files using LLMs",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version",
                   version=f"TransLora CLI {__version__}")
    p.add_argument("files", nargs="+", type=Path,
                   help="subtitle files or directories (.srt, .vtt, .ass, ...)")
    p.add_argument("--source", "-s", default="",
                   help="Source language (e.g. English, French). "
                        "Omit to auto-detect — useful for mixed-language batches.")
    p.add_argument("--target", "-t", required=True,
                   help="Target language (e.g. Arabic, Spanish, Korean)")
    p.add_argument("--api-url", required=True, help="LLM API endpoint URL")
    p.add_argument("--api-key", default="none",
                   help="API key (default: none — for local servers)")
    p.add_argument("--model", default=None,
                   help="Model name (e.g. gpt-4.1-mini, deepseek-chat)")
    p.add_argument("--batch-size", type=int, default=10,
                   help="Subtitle blocks per batch (default: 10)")
    p.add_argument("--concurrency", "-c", type=int, default=1,
                   help="Parallel batches per file (default: 1, raise for cloud providers)")
    p.add_argument("--parallel-files", "-pf", type=int, default=1,
                   help="Translate this many files at once (default: 1)")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES,
                   help=f"Max retries per batch (default: {DEFAULT_MAX_RETRIES})")
    p.add_argument("--force", action="store_true",
                   help="Re-translate even if output already exists")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show retry/validation warnings (hidden by default)")
    p.add_argument("--output", "-o", type=Path, default=None,
                   help="Output file path (single file only)")
    return p


# ---------------------------------------------------------------------------
# File discovery & output naming
# ---------------------------------------------------------------------------


def _collect_files(paths: list[Path]) -> list[Path]:
    """Expand user-supplied paths into a flat list of subtitle files."""
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            found = sorted(
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in SUBTITLE_EXTS
            )
            if not found:
                print(C.yellow(f"No subtitle files found in {p}"), file=sys.stderr)
            files.extend(found)
        elif p.is_file():
            if p.suffix.lower() in SUBTITLE_EXTS:
                files.append(p)
            else:
                print(C.yellow(f"Skipping non-subtitle file: {p}"), file=sys.stderr)
        else:
            print(C.red(f"Not found: {p}"), file=sys.stderr)
    return files


@dataclass
class Job:
    idx: int
    input_path: Path
    output_path: Path


def _plan_jobs(args, srt_files: list[Path]) -> tuple[list[Job], int]:
    """Decide which files still need translating. Returns (jobs, skipped)."""
    jobs: list[Job] = []
    skipped = 0
    total = len(srt_files)
    code = lang_code(args.target)

    for idx, srt_file in enumerate(srt_files, 1):
        if args.output:
            output_path = args.output
        else:
            output_path = srt_file.with_name(
                f"{srt_file.stem}.{code}{srt_file.suffix}"
            )

        if not args.force and output_path.exists():
            skipped += 1
            print(f"[{idx}/{total}] {C.yellow('SKIP')} "
                  f"{C.dim('(exists)')}: {srt_file.name}")
            continue

        jobs.append(Job(idx, srt_file, output_path))
    return jobs, skipped


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

async def _translate_all(args, jobs: list[Job]) -> tuple[int, list[tuple[Path, str]]]:
    """Run all translation jobs with the configured parallelism."""
    parallel = max(1, args.parallel_files)
    total_jobs = len(jobs)
    # With 2+ jobs in flight, per-file live progress can't share the terminal.
    # Switch translator into quiet mode and drive an overall ticker instead.
    multi_file = total_jobs > 1
    cfg = TranslationConfig(
        source_lang=args.source,
        target_lang=args.target,
        api_url=args.api_url,
        api_key=args.api_key,
        model=args.model,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        quiet=multi_file,
        verbose=args.verbose,
    )
    if args.verbose:
        cfg.warn = _stderr_warn

    start_time = time.time()
    file_times: list[float] = []
    completed = 0
    failed: list[tuple[Path, str]] = []

    # State used by both the ticker thread and the coroutines. Integer/list
    # reads are atomic under the GIL — stale ticker data is just cosmetic.
    live = LiveLine() if multi_file else None
    use_ticker = live is not None and live.enabled

    # Route any batch-level warnings above the ticker line — only when the
    # user asked for verbose output; otherwise warn stays silent.
    if live is not None and cfg.verbose:
        cfg.warn = lambda msg: live.println(C.yellow(msg), file=sys.stderr)

    def render_ticker() -> None:
        if live is None:
            return
        elapsed = time.time() - start_time
        done_total = completed + len(failed)
        remaining = total_jobs - done_total
        eta_str = "—"
        if file_times and remaining > 0:
            avg = sum(file_times) / len(file_times)
            eta_str = format_duration((avg * remaining) / parallel)
        sep = C.dim("│")
        status = (
            f"  {C.cyan(f'{done_total}/{total_jobs}')} {C.dim('files')} "
            f"{sep} {C.dim('elapsed')} {format_duration(elapsed)} "
            f"{sep} {C.dim('ETA')} {C.magenta(eta_str)}"
        )
        if failed:
            status += f" {sep} {C.red(f'✗ {len(failed)}')}"
        live.update(status)

    ticker = Ticker(render_ticker, interval=1.0) if use_ticker else None
    semaphore = asyncio.Semaphore(parallel)

    async def run_job(job: Job) -> None:
        nonlocal completed
        async with semaphore:
            start = time.time()
            try:
                await translate_file_async(job.input_path, job.output_path, cfg)
                elapsed = time.time() - start
                file_times.append(elapsed)
                completed += 1
                # In single-file mode the translator already printed its
                # completion banner — avoid duplicating it.
                if live is not None:
                    done = completed + len(failed)
                    line = (
                        f"  [{C.cyan(f'{done}/{total_jobs}')}] "
                        f"{C.green('✓ DONE')} "
                        f"{C.dim(f'({format_duration(elapsed)})')}: "
                        f"{job.input_path.name}"
                    )
                    live.println(line)
            except FileTranslationError as e:
                file_times.append(time.time() - start)
                failed.append((job.input_path, str(e)))
                done = completed + len(failed)
                line = (
                    f"  [{C.cyan(f'{done}/{total_jobs}')}] "
                    f"{C.red('✗ FAIL')}: "
                    f"{job.input_path.name} {C.dim('—')} {e}"
                )
                if live is not None:
                    live.println(line, file=sys.stderr)
                else:
                    print(line, file=sys.stderr)

    if ticker:
        ticker.start()
    try:
        await asyncio.gather(*(run_job(j) for j in jobs))
    finally:
        if ticker:
            ticker.stop()
        if live is not None:
            live.finalize()

    return completed, failed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _print_header(jobs_count: int, total_files: int, parallel: int,
                  concurrency: int, skipped: int) -> None:
    print(
        f"\n{C.bold('Translating')} "
        f"{C.cyan(f'{jobs_count}/{total_files}')} files "
        f"{C.dim(f'(parallel files: {parallel}, batch concurrency: {concurrency})')}"
    )
    if skipped:
        print(C.dim(f"Skipped {skipped} existing file(s)."))
    print()


def _print_summary(total_elapsed: float, jobs_count: int, completed: int,
                   failed: list[tuple[Path, str]], skipped: int) -> None:
    rule = C.dim("─" * 60)
    print(f"\n{rule}")
    print(f"{C.bold('All done')} in {format_duration(total_elapsed)}")
    print(f"  {C.green('Translated')}: {completed}/{jobs_count}")
    if failed:
        print(f"  {C.red('Failed')}:     {len(failed)}/{jobs_count}")
    if skipped:
        print(f"  {C.yellow('Skipped')}:    {skipped} "
              f"{C.dim('(already existed)')}")
    print(rule)

    if failed:
        print(f"\n{C.red('Failed files:')}")
        for f, err in failed:
            print(f"  {C.red('-')} {f.name}: {C.dim(err)}", file=sys.stderr)


async def _run(args) -> None:
    srt_files = _collect_files(args.files)
    if not srt_files:
        print(C.red("Error: no subtitle files to translate"), file=sys.stderr)
        sys.exit(1)

    if args.output and len(srt_files) > 1:
        print(C.red("Error: --output can only be used with a single file"),
              file=sys.stderr)
        sys.exit(1)

    jobs, skipped = _plan_jobs(args, srt_files)
    if not jobs:
        print(f"\nAll {len(srt_files)} files already translated. "
              f"{C.dim('Use --force to re-translate.')}")
        return

    parallel = max(1, args.parallel_files)
    _print_header(len(jobs), len(srt_files), parallel, args.concurrency, skipped)

    started = time.time()
    completed, failed = await _translate_all(args, jobs)
    total_elapsed = time.time() - started

    _print_summary(total_elapsed, len(jobs), completed, failed, skipped)


def main() -> None:
    args = _build_parser().parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
