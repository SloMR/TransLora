"""Per-file orchestration: parse, prepass scan, batched translate, stitch."""

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
from .context_pass import FileContext, extract_file_context, refine_scene_attribution
from .time_tracker import EtaEstimator, format_duration
from .live_status import Colors, LiveLine, Ticker


# Re-exported so translora.py doesn't need to import from submodules directly.
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
    """Translate one subtitle file end-to-end.

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
        src_label = cfg.source_lang or "auto"
        print(
            f"{colors.bold('Translating')} {colors.cyan(str(len(doc.blocks)))} blocks "
            f"in {colors.cyan(str(total))} batches "
            f"{colors.dim(f'({src_label} → {cfg.target_lang}, {doc.format})')}"
        )
        if cfg.concurrency > 1:
            print(colors.dim(f"Concurrency: {cfg.concurrency}"))

    started_at = time.time()
    async with httpx.AsyncClient() as scan_client:
        if not cfg.quiet:
            print(colors.dim("  Scanning for cast and context..."))
        file_context = await extract_file_context(
            scan_client, doc.blocks, cfg,
        )
        if cfg.refine_attribution and not file_context.is_empty():
            if not cfg.quiet:
                print(colors.dim("  Attributing speakers in mixed-gender scenes..."))
            await refine_scene_attribution(
                scan_client, file_context, doc.blocks, cfg,
            )
    if not cfg.quiet:
        if file_context.is_empty():
            print(colors.dim("  Glossary: empty (proceeding without context hints)"))
        else:
            chars = len(file_context.characters)
            terms = len(file_context.terms)
            scenes = len(file_context.scenes)
            attrib = sum(1 for s in file_context.scenes if s.attribution)
            notes = len(file_context.notes)
            print(colors.dim(
                f"  Glossary: {chars} character(s), {terms} term(s), "
                f"{scenes} scene(s) ({attrib} attributed), {notes} note(s)"
            ))
            if file_context.register:
                print(colors.dim(f"  Register: {file_context.register}"))

    translated_batches = await _run_batches(
        batches, cfg, colors, started_at, file_context,
    )

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


def _prev_tail(
    batches: list[list[SubtitleBlock]], idx: int, overlap: int,
) -> list[SubtitleBlock]:
    if idx <= 0 or overlap <= 0:
        return []
    return batches[idx - 1][-overlap:]


async def _run_batches(
    batches: list[list[SubtitleBlock]],
    cfg: TranslationConfig,
    colors: Colors,
    started_at: float,
    file_context: FileContext | None = None,
) -> list[list[SubtitleBlock]]:
    """Translate every batch with up to cfg.concurrency requests in flight.

    Results are returned in original batch order. A fatal batch failure cancels
    remaining work.
    """
    total = len(batches)
    results: list[list[SubtitleBlock] | None] = [None] * total
    eta = EtaEstimator(total, cfg.concurrency, started_at)
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
                    prev_tail = _prev_tail(batches, idx, cfg.context_overlap)
                    try:
                        results[idx] = await translate_batch_with_retry(
                            client, idx, batches[idx], cfg, file_context,
                            prev_tail=prev_tail,
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

    return [r for r in results if r is not None]


def _render_status(
    live: LiveLine,
    colors: Colors,
    total: int,
    batch_elapsed: float,
    eta: EtaEstimator,
) -> None:
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
