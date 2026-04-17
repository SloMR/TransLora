"""Translate an .srt file end-to-end by sending batches to an LLM chat API.

Pipeline:
    read file -> parse blocks -> split into batches -> send each batch in
    parallel -> validate response -> stitch translated batches back together.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

from .srt_parser import SubtitleBlock, split_batches
from .formats import parse_subtitle
from .config import DEFAULT_MAX_RETRIES, TranslationConfig
from .batch_runner import FileTranslationError, translate_batch_with_retry
from .time_tracker import EtaEstimator, format_duration
from .live_status import Colors, LiveLine, Ticker


# Re-exports — callers (translora.py) import these names from translator
# so they don't need to know the internal module layout.
__all__ = [
    "DEFAULT_MAX_RETRIES",
    "FileTranslationError",
    "TranslationConfig",
    "format_duration",
    "translate_file_async",
]


async def translate_file_async(
    input_path: Path,
    output_path: Path,
    cfg: TranslationConfig,
) -> None:
    """Translate one .srt file end-to-end.

    Raises FileTranslationError on any batch that exhausts retries.
    """
    content = input_path.read_text(encoding="utf-8-sig")
    try:
        doc = parse_subtitle(input_path.name, content)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return

    if not doc.blocks:
        print("Error: no subtitle blocks found in file", file=sys.stderr)
        return

    batches = split_batches(doc.blocks, cfg.batch_size)
    total = len(batches)
    colors = Colors()

    if not cfg.quiet:
        print(
            f"{colors.bold('Translating')} {colors.cyan(str(len(doc.blocks)))} blocks "
            f"in {colors.cyan(str(total))} batches "
            f"{colors.dim(f'({cfg.source_lang} → {cfg.target_lang}, {doc.format})')}"
        )
        if cfg.concurrency > 1:
            print(colors.dim(f"Concurrency: {cfg.concurrency}"))

    started_at = time.time()
    translated_batches = await _run_batches(batches, cfg, colors, started_at)

    # Stitch in order (they completed out-of-order but `_run_batches` returns
    # them indexed by their original position).
    translated: list[SubtitleBlock] = []
    for r in translated_batches:
        translated.extend(r)

    output_path.write_text(doc.rebuild(translated), encoding="utf-8")
    if not cfg.quiet:
        elapsed = time.time() - started_at
        throughput = len(doc.blocks) / elapsed if elapsed > 0 else 0
        print(
            f"{colors.green('✓ Completed')} in {format_duration(elapsed)} "
            f"{colors.dim(f'({throughput:.1f} blocks/s)')}"
        )
        print(colors.dim(f"  Output: {output_path}"))


async def _run_batches(
    batches: list[list[SubtitleBlock]],
    cfg: TranslationConfig,
    colors: Colors,
    started_at: float,
) -> list[list[SubtitleBlock]]:
    """Translate every batch with up to `cfg.concurrency` requests in flight.

    Returns results in original batch order. Raises FileTranslationError
    and cancels remaining work the moment any batch fails fatally.
    """
    total = len(batches)
    results: list[list[SubtitleBlock] | None] = [None] * total
    eta = EtaEstimator(total, cfg.concurrency, started_at)
    semaphore = asyncio.Semaphore(cfg.concurrency)

    # Shared cancellation flag — as soon as any batch fails fatally we stop
    # scheduling new work rather than wasting retries on doomed batches.
    failure: FileTranslationError | None = None

    live = LiveLine() if not cfg.quiet else None

    # Route batch-level retry/error messages above the live line so they don't
    # get clobbered by the progress refresh.
    original_warn = cfg.warn
    if live is not None:
        cfg.warn = lambda msg: live.println(colors.yellow(msg), file=sys.stderr)

    # Shared with the ticker so the "batch" column keeps showing the last
    # completed batch's time between completions.
    last_batch_elapsed = 0.0

    def render() -> None:
        if live is not None:
            _render_status(live, colors, total, last_batch_elapsed, eta)

    ticker = Ticker(render, interval=1.0) if live is not None else None
    if ticker is not None:
        ticker.start()

    try:
        async with httpx.AsyncClient() as client:
            async def run_one(idx: int) -> None:
                nonlocal failure, last_batch_elapsed
                if failure:
                    return
                async with semaphore:
                    if failure:
                        return
                    batch_start = time.time()
                    try:
                        results[idx] = await translate_batch_with_retry(
                            client, idx, batches[idx], cfg
                        )
                    except FileTranslationError as e:
                        failure = e
                        return

                    last_batch_elapsed = time.time() - batch_start
                    eta.record()
                    render()

            await asyncio.gather(*(run_one(i) for i in range(total)))
    finally:
        if ticker is not None:
            ticker.stop()
        if live is not None:
            live.finalize()
        cfg.warn = original_warn

    if failure:
        raise failure

    # All slots must be filled now — `failure` would have been raised otherwise.
    return [r for r in results if r is not None]


def _render_status(
    live: LiveLine,
    colors: Colors,
    total: int,
    batch_elapsed: float,
    eta: EtaEstimator,
) -> None:
    """Draw the single in-place progress line for one batch completion."""
    done = eta.done
    pct = int(100 * done / total) if total else 0
    elapsed = time.time() - eta.start
    remaining = eta.remaining_secs()
    eta_str = format_duration(remaining) if remaining is not None else "—"
    avg = elapsed / done if done else batch_elapsed
    sep = colors.dim("│")
    line = (
        f"  [{colors.cyan(f'{done}/{total}')} {colors.dim(f'{pct}%')}] "
        f"{sep} {colors.dim('batch')} {format_duration(batch_elapsed)} "
        f"{sep} {colors.dim('elapsed')} {format_duration(elapsed)} "
        f"{sep} {colors.dim('ETA')} {colors.magenta(eta_str)} "
        f"{sep} {colors.dim('avg')} {format_duration(avg)}"
    )
    live.update(line)
