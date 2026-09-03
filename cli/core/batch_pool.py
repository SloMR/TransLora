"""Running one file's batches: bounded concurrency, reuse of a resume
sidecar, the live progress line, and cancellation on the first fatal batch."""

from __future__ import annotations

import asyncio
import time

import httpx

from .batch_runner import (
    BatchResult,
    FileTranslationError,
    translate_batch_with_retry,
)
from .config import TranslationConfig
from .context_pass import FileContext
from .live_status import Colors
from .resume import BatchProgress
from .run_display import RunDisplay
from .srt_parser import SubtitleBlock


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
    display: RunDisplay | None = None,
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
    semaphore = asyncio.Semaphore(cfg.concurrency)

    failure: FileTranslationError | None = None

    # The file's status line when the caller has one; a bare call (the tests,
    # a script) gets its own for the duration of the batches.
    own_display = display is None and not cfg.quiet
    if own_display:
        display = RunDisplay(colors, started_at)
        display.meter.plan("batches", total)
        display.start()
    original_warn = cfg.warn
    if own_display and display is not None and cfg.verbose:
        cfg.warn = display.warn

    done = reused
    if display is not None:
        # The count before the stage: the first line of the stage names it.
        display.meter.batches(done, total)
        display.meter.begin("batches")

    try:
        async def run_one(idx: int) -> None:
            nonlocal failure, done
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

                done += 1
                if display is not None:
                    display.batch_elapsed = time.time() - batch_start
                    display.meter.batches(done, total)
                    display.render()

        await asyncio.gather(*(run_one(i) for i in pending))
    finally:
        if own_display and display is not None:
            display.stop()
        cfg.warn = original_warn

    if failure:
        raise failure

    return [r for r in results if r is not None]
