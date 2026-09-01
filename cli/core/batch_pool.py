"""Running one file's batches: bounded concurrency, reuse of a resume
sidecar, the live progress line, and cancellation on the first fatal batch."""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

from .batch_runner import (
    BatchResult,
    FileTranslationError,
    translate_batch_with_retry,
)
from .config import TranslationConfig
from .context_pass import FileContext
from .live_status import Colors, LiveLine, Ticker
from .resume import BatchProgress
from .srt_parser import SubtitleBlock
from .time_tracker import EtaEstimator, format_duration


def prev_tail_for(
    batches: list[list[SubtitleBlock]], idx: int, overlap: int,
) -> list[SubtitleBlock]:
    """Source blocks from the previous batch, shown to this one as read-only
    context. The flagged retry rebuilds the same message, so it asks here too."""
    if idx <= 0 or overlap <= 0:
        return []
    return batches[idx - 1][-overlap:]


async def run_batches(
    client: httpx.AsyncClient,
    batches: list[list[SubtitleBlock]],
    cfg: TranslationConfig,
    colors: Colors,
    started_at: float,
    file_context: FileContext | None = None,
    progress: BatchProgress | None = None,
) -> list[BatchResult]:
    """Translate every batch with up to cfg.concurrency requests in flight.

    Results are returned in original batch order. A fatal batch failure cancels
    remaining work; batches already in `progress` are reused, not paid for again.
    """
    total = len(batches)
    results: list[BatchResult | None] = [None] * total

    reused = 0
    if progress is not None:
        for idx, batch in enumerate(batches):
            cached = progress.get(idx, batch)
            if cached is not None:
                # A reused batch was already reported on the run that paid for it.
                results[idx] = BatchResult(cached)
                reused += 1
    if reused and not cfg.quiet:
        print(colors.dim(f"  Resuming: {reused}/{total} batches already translated"))

    pending = [i for i, r in enumerate(results) if r is None]
    eta = EtaEstimator(len(pending), cfg.concurrency, started_at)
    semaphore = asyncio.Semaphore(cfg.concurrency)

    failure: FileTranslationError | None = None

    live = LiveLine() if not cfg.quiet else None

    # Route verbose warnings above the live line so the progress refresh
    # doesn't clobber them. In non-verbose mode warn is a no-op, so leave it.
    original_warn = cfg.warn
    if live is not None and cfg.verbose:
        cfg.warn = lambda msg: live.println(colors.yellow(msg), file=sys.stderr)

    # Held between completions so the ticker keeps showing the last batch time.
    last_batch_elapsed = 0.0

    def render() -> None:
        if live is not None:
            _render_status(live, colors, reused, total, last_batch_elapsed, eta)

    # A non-TTY LiveLine prints a whole line per update — a 1s tick would bury CI logs.
    ticker = Ticker(render, interval=1.0) if live is not None and live.enabled else None
    if ticker is not None:
        ticker.start()

    try:
        async def run_one(idx: int) -> None:
            nonlocal failure, last_batch_elapsed
            if failure:
                return
            async with semaphore:
                if failure:
                    return
                batch_start = time.time()
                prev_tail = prev_tail_for(batches, idx, cfg.context_overlap)
                try:
                    translated = await translate_batch_with_retry(
                        client, idx, batches[idx], cfg, file_context,
                        prev_tail=prev_tail,
                    )
                except FileTranslationError as e:
                    failure = e
                    return
                results[idx] = translated
                if progress is not None:
                    progress.record(idx, translated.blocks)

                last_batch_elapsed = time.time() - batch_start
                eta.record()
                render()

        await asyncio.gather(*(run_one(i) for i in pending))
    finally:
        if ticker is not None:
            ticker.stop()
        if live is not None:
            live.finalize()
        cfg.warn = original_warn

    if failure:
        raise failure

    return [r for r in results if r is not None]


def _render_status(
    live: LiveLine,
    colors: Colors,
    done_offset: int,
    total: int,
    batch_elapsed: float,
    eta: EtaEstimator,
) -> None:
    done = done_offset + eta.done
    pct = int(100 * done / total) if total else 0
    elapsed = time.time() - eta.start
    remaining = eta.remaining_secs()
    eta_str = format_duration(remaining) if remaining is not None else "—"
    avg = elapsed / eta.done if eta.done else batch_elapsed
    sep = colors.dim("│")
    line = (
        f"  [{colors.cyan(f'{done}/{total}')} {colors.dim(f'{pct}%')}] "
        f"{sep} {colors.dim('batch')} {format_duration(batch_elapsed)} "
        f"{sep} {colors.dim('elapsed')} {format_duration(elapsed)} "
        f"{sep} {colors.dim('ETA')} {colors.magenta(eta_str)} "
        f"{sep} {colors.dim('avg')} {format_duration(avg)}"
    )
    live.update(line)
