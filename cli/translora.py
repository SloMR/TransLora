#!/usr/bin/env python3
"""TransLora CLI — AI-powered subtitle translation."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.batch_runner import FileTranslationError
from core.cli_args import (
    API_KEY_ENV,
    EXIT_FAILURE,
    EXIT_INTERRUPTED,
    EXIT_OK,
    REVIEW_API_KEY_ENV,
    Job,
    build_parser,
    print_dry_run,
)
from core.config import TranslationConfig, _stderr_warn, dialect_notes
from core.constants import DEFAULT_DIALECT
from core.languages import lang_code
from core.live_status import Colors, LiveLine, Ticker, restore_terminal
from core.run_stats import describe_calls
from core.time_tracker import format_duration
from core.translator import translate_file_async

__version__ = "0.5.0"

SUBTITLE_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub", ".sbv"}

C = Colors()


def _looks_like_own_output(path: Path, code: str) -> bool:
    """movie.ar.srt is this tool's own output for target `ar`, not a source."""
    return bool(code) and path.stem.lower().endswith(f".{code.lower()}")


def _collect_files(paths: list[Path], code: str) -> list[Path]:
    """Expand paths into a flat list of subtitle files."""
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            found = sorted(
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in SUBTITLE_EXTS
            )
            # Otherwise a re-run translates its own output into movie.ar.ar.srt.
            kept = [f for f in found if not _looks_like_own_output(f, code)]
            if len(kept) < len(found):
                print(C.dim(f"Skipping {len(found) - len(kept)} previously "
                            f"translated file(s) in {p}"))
            if not found:
                print(C.yellow(f"No subtitle files found in {p}"), file=sys.stderr)
            files.extend(kept)
        elif p.is_file():
            if p.suffix.lower() in SUBTITLE_EXTS:
                files.append(p)
            else:
                print(C.yellow(f"Skipping non-subtitle file: {p}"), file=sys.stderr)
        else:
            print(C.red(f"Not found: {p}"), file=sys.stderr)
    return files


@dataclass
class RunTotals:
    """Live counts, held by main() so an interrupt can still be summarized."""
    completed: int = 0
    failed: list[tuple[Path, str]] = field(default_factory=list)


def _plan_jobs(args, srt_files: list[Path]) -> tuple[list[Job], int, list[str]]:
    """Return (jobs to run, skipped count, refusals) based on existing outputs."""
    jobs: list[Job] = []
    skipped = 0
    refused: list[str] = []
    total = len(srt_files)
    code = lang_code(args.target)
    inputs = {f.resolve() for f in srt_files}

    for idx, srt_file in enumerate(srt_files, 1):
        output_path = args.output or srt_file.with_name(
            f"{srt_file.stem}.{code}{srt_file.suffix}")

        if output_path.is_symlink():
            # A crafted folder could otherwise aim our write at any writable file.
            refused.append(f"{srt_file.name}: output is a symlink ({output_path})")
            continue
        if output_path.resolve() in inputs:
            refused.append(
                f"{srt_file.name}: output {output_path} is another input file")
            continue

        if not args.force and output_path.exists():
            skipped += 1
            print(f"[{idx}/{total}] {C.yellow('SKIP')} "
                  f"{C.dim('(exists)')}: {srt_file.name}")
            continue

        jobs.append(Job(idx, srt_file, output_path))
    return jobs, skipped, refused


def _build_config(args, multi_file: bool) -> TranslationConfig:
    cfg = TranslationConfig(
        source_lang=args.source,
        target_lang=args.target,
        api_url=args.api_url,
        api_key=args.api_key,
        model=args.model,
        review_api_url=args.review_api_url or "",
        review_api_key=args.review_api_key or "",
        review_model=args.review_model,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        scan_char_budget=args.scan_budget,
        context_overlap=args.context_overlap,
        refine_attribution=args.refine_attribution,
        review=args.review,
        reflow=args.reflow,
        fix_flagged=args.fix_flagged,
        verify_adequacy=args.verify_adequacy,
        full_attribution=args.full_attribution,
        max_line_chars=args.max_line_chars,
        formality=args.formality,
        dialect=args.dialect or DEFAULT_DIALECT,
        encoding=args.encoding,
        request_timeout=args.timeout,
        send_temperature=args.send_temperature,
        glossary_in=args.glossary_in,
        glossary_out=args.glossary_out,
        resume=args.resume,
        quiet=multi_file,
        verbose=args.verbose,
    )
    if args.verbose:
        cfg.warn = _stderr_warn
    return cfg


async def _translate_all(
    args, cfg: TranslationConfig, jobs: list[Job], totals: RunTotals,
) -> None:
    parallel = args.parallel_files
    total_jobs = len(jobs)
    # Multi-file mode: per-file live progress can't share the terminal, so
    # suppress per-file output and drive a run-wide ticker instead.
    live = LiveLine() if total_jobs > 1 else None
    use_ticker = live is not None and live.enabled

    # Shared with the ticker thread — atomic reads under the GIL, stale data
    # is cosmetic.
    start_time = time.time()
    file_times: list[float] = []

    if live is not None and cfg.verbose:
        cfg.warn = lambda msg: live.println(C.yellow(msg), file=sys.stderr)

    def render_ticker() -> None:
        if live is None:
            return
        elapsed = time.time() - start_time
        done_total = totals.completed + len(totals.failed)
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
        if totals.failed:
            status += f" {sep} {C.red(f'✗ {len(totals.failed)}')}"
        live.update(status)

    ticker = Ticker(render_ticker, interval=1.0) if use_ticker else None
    semaphore = asyncio.Semaphore(parallel)

    def report_failure(job: Job, message: str) -> None:
        totals.failed.append((job.input_path, message))
        done = totals.completed + len(totals.failed)
        line = (
            f"  [{C.cyan(f'{done}/{total_jobs}')}] "
            f"{C.red('✗ FAIL')}: "
            f"{job.input_path.name} {C.dim('—')} {message}"
        )
        if live is not None:
            live.println(line, file=sys.stderr)
        else:
            print(line, file=sys.stderr)

    async def run_job(job: Job) -> None:
        async with semaphore:
            start = time.time()
            try:
                await translate_file_async(job.input_path, job.output_path, cfg)
            except FileTranslationError as e:
                report_failure(job, str(e))
                return
            except Exception as e:
                # One unexpected file must not abort the run and lose the summary.
                report_failure(job, f"{type(e).__name__}: {e}")
                return

            # Only successful files feed the ETA; a fast failure would skew it.
            elapsed = time.time() - start
            file_times.append(elapsed)
            totals.completed += 1
            # Single-file mode already prints a completion banner from the
            # translator itself — don't duplicate it here.
            if live is not None:
                done = totals.completed + len(totals.failed)
                live.println(
                    f"  [{C.cyan(f'{done}/{total_jobs}')}] "
                    f"{C.green('✓ DONE')} "
                    f"{C.dim(f'({format_duration(elapsed)})')}: "
                    f"{job.input_path.name}"
                )

    if ticker:
        ticker.start()
    try:
        await asyncio.gather(*(run_job(j) for j in jobs))
    finally:
        if ticker:
            ticker.stop()
        if live is not None:
            live.finalize()


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


def _print_summary(total_elapsed: float, jobs_count: int, totals: RunTotals,
                   skipped: int, cfg: TranslationConfig) -> None:
    calls = cfg.calls
    rule = C.dim("─" * 60)
    print(f"\n{rule}")
    print(f"{C.bold('All done')} in {format_duration(total_elapsed)}")
    print(f"  {C.green('Translated')}: {totals.completed}/{jobs_count}")
    # A single file already printed its own call breakdown, next to blocks/s.
    if jobs_count > 1 and calls.total:
        print(f"  {C.cyan('LLM calls')}:  {calls.total} "
              f"{C.dim(f'({describe_calls(calls)})')}")
    # What the run had to negotiate with the provider; silent when nothing was.
    for note in dialect_notes(cfg):
        print(f"  {C.cyan('Dialect')}:    {C.dim(note)}")
    if totals.failed:
        print(f"  {C.red('Failed')}:     {len(totals.failed)}/{jobs_count}")
    if skipped:
        print(f"  {C.yellow('Skipped')}:    {skipped} "
              f"{C.dim('(already existed)')}")
    print(rule)

    if totals.failed:
        print(f"\n{C.red('Failed files:')}")
        for f, err in totals.failed:
            print(f"  {C.red('-')} {f.name}: {C.dim(err)}", file=sys.stderr)


def _print_cancelled(totals: RunTotals, resume: bool) -> None:
    print(f"\n{C.yellow('Cancelled')} — {totals.completed} file(s) finished, "
          f"{len(totals.failed)} failed.", file=sys.stderr)
    if resume:
        print(C.dim("  Completed batches were saved; re-run the same command "
                    "to continue where it stopped."), file=sys.stderr)


async def _run(args, totals: RunTotals) -> int:
    code = lang_code(args.target)
    srt_files = _collect_files(args.files, code)
    if not srt_files:
        print(C.red("Error: no subtitle files to translate"), file=sys.stderr)
        return EXIT_FAILURE

    if args.output and len(srt_files) > 1:
        print(C.red("Error: --output can only be used with a single file"),
              file=sys.stderr)
        return EXIT_FAILURE
    if args.glossary_out and len(srt_files) > 1:
        # One path, many files: the last writer would win silently.
        print(C.red("Error: --glossary-out can only be used with a single file"),
              file=sys.stderr)
        return EXIT_FAILURE

    jobs, skipped, refused = _plan_jobs(args, srt_files)
    for message in refused:
        print(C.red(f"Refusing to translate {message}"), file=sys.stderr)
    if not jobs:
        if refused:
            return EXIT_FAILURE
        print(f"\nAll {len(srt_files)} files already translated. "
              f"{C.dim('Use --force to re-translate.')}")
        return EXIT_OK

    cfg = _build_config(args, multi_file=len(jobs) > 1)
    if args.dry_run:
        status = print_dry_run(cfg, jobs, args.parallel_files)
        return EXIT_FAILURE if refused else status

    _print_header(len(jobs), len(srt_files), args.parallel_files,
                  args.concurrency, skipped)

    started = time.time()
    await _translate_all(args, cfg, jobs, totals)
    _print_summary(time.time() - started, len(jobs), totals, skipped, cfg)

    return EXIT_FAILURE if (totals.failed or refused) else EXIT_OK


def main() -> None:
    args = build_parser(__version__).parse_args()
    if not args.api_key:
        # Keeps the key out of argv, shell history and `docker inspect`.
        args.api_key = os.environ.get(API_KEY_ENV) or "none"
    if not args.review_api_key:
        # Empty, not "none": empty means "use the main provider's key".
        args.review_api_key = os.environ.get(REVIEW_API_KEY_ENV) or ""

    totals = RunTotals()
    try:
        status = asyncio.run(_run(args, totals))
    except KeyboardInterrupt:
        restore_terminal()
        _print_cancelled(totals, args.resume)
        status = EXIT_INTERRUPTED
    sys.exit(status)


if __name__ == "__main__":
    main()
