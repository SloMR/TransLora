"""Per-file orchestration: parse, prepass scan, batched translate, stitch."""

from __future__ import annotations

import asyncio
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .adequacy import verify_adequacy
from .batch_pool import prev_tail_for, run_batches
from .batch_runner import (
    BatchFlag,
    BatchResult,
    FileTranslationError,
    retry_flagged_batch,
)
from .config import TranslationConfig
from .constants import DEFAULT_MAX_RETRIES
from .context_pass import (
    FileContext,
    clamp_scenes_to_blocks,
    find_inconsistent_phrases,
    phrase_split_message,
)
from .context_scan import (
    attribution_targets,
    extract_file_context,
    refine_scene_attribution,
)
from .formats import parse_subtitle
from .glossary_io import load_glossary, save_glossary
from .languages import effective_norms
from .live_status import Colors
from .repair import (
    detect_variant_drift,
    normalize_diacritics,
    variant_drift_message,
)
from .resume import BatchProgress, progress_path, run_key
from .run_display import RunDisplay
from .run_progress import ProgressMeter
from .run_stats import (
    adequacy_sample_size,
    describe_calls,
    fix_flagged_cap,
    plan_repairs,
)
from .srt_parser import SubtitleBlock, split_batches
from .time_tracker import format_duration

# Re-exported so translora.py and cli_args.py don't need to import from
# submodules directly.
__all__ = [
    "DEFAULT_MAX_RETRIES",
    "FilePlan",
    "FileTranslationError",
    "TranslationConfig",
    "format_duration",
    "plan_file",
    "read_subtitle_text",
    "translate_file_async",
]

# Last-resort chain, tried in order after utf-8. These codepages accept almost
# any byte string, so the first one wins and the rest are near-unreachable:
# reordering them silently changes which mojibake a file decodes to.
FALLBACK_ENCODINGS = ("cp1252", "cp1256", "cp1251", "shift_jis")


def read_subtitle_text(path: Path, encoding: str = "auto") -> tuple[str, str]:
    """Decode a subtitle file, returning (text, encoding used)."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise FileTranslationError(f"could not read {path}: {e}") from e

    candidates = ["utf-8-sig"]
    if encoding and encoding != "auto":
        candidates.append(encoding)
    candidates.extend(FALLBACK_ENCODINGS)

    for candidate in candidates:
        try:
            return raw.decode(candidate), candidate
        except (UnicodeDecodeError, LookupError):
            continue
    raise FileTranslationError(
        f"could not decode {path.name} as any of "
        f"{', '.join(candidates)} — pass --encoding with the file's codepage"
    )


def _parse_or_fail(input_path: Path, content: str):
    """Parse, or raise. A file that yields nothing is a failure, not a success."""
    try:
        doc = parse_subtitle(input_path.name, content)
    except ValueError as err:
        raise FileTranslationError(str(err)) from err
    if not doc.blocks:
        raise FileTranslationError("no subtitle blocks found in file")
    return doc


def _check_output_writable(output_path: Path) -> None:
    """Fail before the file is translated and paid for, not after."""
    if output_path.is_symlink():
        # A crafted subtitle folder must not redirect our write elsewhere.
        raise FileTranslationError(
            f"refusing to write through a symlink: {output_path}")
    parent = output_path.parent
    if not parent.is_dir():
        raise FileTranslationError(f"output directory does not exist: {parent}")
    if output_path.exists():
        if not output_path.is_file():
            raise FileTranslationError(
                f"output path is not a regular file: {output_path}")
        if not os.access(output_path, os.W_OK):
            raise FileTranslationError(f"output file is not writable: {output_path}")
    elif not os.access(parent, os.W_OK):
        raise FileTranslationError(f"output directory is not writable: {parent}")


def _note(cfg: TranslationConfig, message: str) -> None:
    """Progress detail: said in single-file mode, routed to warn otherwise."""
    if cfg.quiet:
        cfg.warn(message)
    else:
        cfg.say(message)


def _load_glossary_for(
    input_path: Path, blocks: list[SubtitleBlock], cfg: TranslationConfig,
) -> FileContext:
    """Read --glossary-in. An unusable one is fatal: scanning instead would
    spend the money the user asked to save."""
    assert cfg.glossary_in is not None
    try:
        context, source_file = load_glossary(cfg.glossary_in)
    except ValueError as e:
        raise FileTranslationError(str(e)) from e

    if source_file != input_path.name and context.scenes:
        # Scene ranges are block numbers — meaningful only in their own file.
        dropped = len(context.scenes)
        context.scenes = []
        _note(cfg, f"  Glossary is from {source_file or 'another file'}: "
                   f"dropped {dropped} scene range(s), kept cast and terms")
    else:
        clamp_scenes_to_blocks(context, blocks)
    return context


async def _build_file_context(
    client: httpx.AsyncClient,
    input_path: Path,
    blocks: list[SubtitleBlock],
    cfg: TranslationConfig,
    colors: Colors,
) -> FileContext:
    if cfg.glossary_in:
        if not cfg.quiet:
            cfg.say(colors.dim(f"  Loading glossary from {cfg.glossary_in} "
                             f"(scan skipped)"))
        file_context = _load_glossary_for(input_path, blocks, cfg)
    else:
        if not cfg.quiet:
            cfg.say(colors.cyan("  Scanning for cast and context..."))
        file_context = await extract_file_context(client, blocks, cfg)

    if cfg.refine_attribution and attribution_targets(file_context, cfg):
        if not cfg.quiet:
            cfg.say(colors.cyan("  Attributing speakers in two-hander scenes..."))
        await refine_scene_attribution(client, file_context, blocks, cfg)

    if not cfg.quiet:
        if file_context.is_empty():
            cfg.say(colors.dim("  Glossary: empty (proceeding without context hints)"))
        else:
            chars = len(file_context.characters)
            terms = len(file_context.terms)
            idioms = len(file_context.idioms)
            scenes = len(file_context.scenes)
            attrib = sum(1 for s in file_context.scenes if s.attribution)
            notes = len(file_context.notes)
            cfg.say(colors.dim(
                f"  Glossary: {chars} character(s), {terms} term(s), "
                f"{idioms} idiom(s), {scenes} scene(s) ({attrib} attributed), "
                f"{notes} note(s)"
            ))
            if file_context.register:
                cfg.say(colors.dim(f"  Register: {file_context.register}"))

    if cfg.glossary_out:
        try:
            save_glossary(cfg.glossary_out, file_context, input_path.name)
        except OSError as e:
            cfg.warn(f"    Could not write glossary {cfg.glossary_out}: {e}")
        else:
            if not cfg.quiet:
                cfg.say(colors.dim(f"  Glossary written: {cfg.glossary_out}"))
    return file_context


def _open_progress(
    input_path: Path,
    output_path: Path,
    cfg: TranslationConfig,
    total_blocks: int,
) -> BatchProgress | None:
    if not cfg.resume:
        return None
    progress = BatchProgress(
        progress_path(output_path), run_key(input_path, cfg, total_blocks))
    progress.load()
    return progress


def _normalize_file_orthography(
    blocks: list[SubtitleBlock], cfg: TranslationConfig,
) -> list[SubtitleBlock]:
    """File-level checks that need the whole file's baseline, so they cannot
    run per batch: a tail of cues that came back vocalized, and a file written
    in a regional variant nobody asked for."""
    norms = effective_norms(cfg.target_lang, cfg.max_line_chars)
    normalized = normalize_diacritics(blocks, norms.script)
    changed = sum(1 for before, after in zip(normalized, blocks, strict=True)
                  if before.text != after.text)
    if changed:
        cfg.warn(f"    Removed diacritics from {changed} line(s): the rest of "
                 f"the file is unvocalized")
    # Reported, never rewritten: a variant is a translation decision, and the
    # markers are evidence of one, not proof.
    drift = detect_variant_drift(normalized, norms.script, cfg.dialect)
    if drift is not None:
        cfg.warn(f"    {variant_drift_message(drift)}")
    return normalized


def _flagged_batches(
    results: list[BatchResult], *extra: dict[int, list[BatchFlag]],
) -> dict[int, list[BatchFlag]]:
    """Every batch with something to fix, its own flags first."""
    flagged = {i: list(r.flags) for i, r in enumerate(results) if r.flags}
    for found in extra:
        for index, flags in found.items():
            flagged.setdefault(index, []).extend(flags)
    return flagged


def _phrase_flags(
    batches: list[list[SubtitleBlock]],
    results: list[BatchResult],
    cfg: TranslationConfig,
) -> dict[int, list[BatchFlag]]:
    """Recurring phrases the finished file rendered several ways: warned once
    each at file level, then one flag per batch a split landed in, all under
    the one cause — a phrase the file rendered three ways is a single
    systematic problem, and grouping it as one is what buys it the wider
    repair ceiling.

    It runs here rather than beside the other file-level checks because the
    repair it feeds happens before they do. Costs no call of its own.
    """
    source = [block for batch in batches for block in batch]
    output = [block for result in results for block in result.blocks]
    batch_of = {block.number: index
                for index, batch in enumerate(batches) for block in batch}
    found: dict[int, list[BatchFlag]] = {}
    for split in find_inconsistent_phrases(source, output):
        message = phrase_split_message(split)
        cfg.warn(f"    {message}")
        seen: set[int] = set()
        for block in split.blocks:
            index = batch_of.get(block)
            if index is None or index in seen:
                continue
            seen.add(index)
            found.setdefault(index, []).append(BatchFlag(
                block=block,
                message=message,
                problem=(f"block {block}: the recurring phrase "
                         f"'{split.phrase}' is rendered differently elsewhere "
                         f"in the file; use one wording for it"),
                cause=split.cause,
            ))
    return found


async def _adequacy_flags(
    client: httpx.AsyncClient,
    batches: list[list[SubtitleBlock]],
    results: list[BatchResult],
    cfg: TranslationConfig,
    colors: Colors,
) -> dict[int, list[BatchFlag]]:
    """The opt-in back-translation check, or nothing when it cannot run."""
    if not cfg.verify_adequacy:
        return {}
    if not cfg.source_lang:
        # Nothing to translate back into; guessing the language would make the
        # overlap score meaningless.
        cfg.warn("    Adequacy check skipped: it needs --source to translate "
                 "back into")
        return {}
    if not cfg.quiet:
        cfg.say(colors.cyan("  Checking adequacy by back-translation..."))
    return await verify_adequacy(client, batches, results, cfg)


async def _repair_flagged(
    client: httpx.AsyncClient,
    batches: list[list[SubtitleBlock]],
    results: list[BatchResult],
    flagged: dict[int, list[BatchFlag]],
    cfg: TranslationConfig,
    file_context: FileContext | None,
    meter: ProgressMeter | None = None,
) -> tuple[set[int], set[int]]:
    """Re-issue the flagged batches, rarest cause first so a failure spanning
    the file cannot crowd out the one-offs. Each retry is kept only if it comes
    back with strictly fewer flags, so this can never make the file worse.
    Returns the batches it tried and the ones whose retry it kept."""
    plan = plan_repairs(
        {index: [flag.cause for flag in flags]
         for index, flags in flagged.items() if flags},
        len(batches),
    )
    selected = plan.selected
    colors = Colors()
    if meter is not None:
        meter.plan("repairing", len(selected))
    if plan.skipped:
        _note(cfg, colors.yellow(
            f"  {plan.flagged} flagged batch(es) across {plan.causes} "
            f"cause(s); repairing {len(selected)} (cap {plan.cap}), "
            f"leaving {plan.skipped}"))
    else:
        _note(cfg, colors.cyan(f"  Repairing {len(selected)} flagged batch(es) across "
                               f"{plan.causes} cause(s)..."))

    semaphore = asyncio.Semaphore(max(1, cfg.concurrency))
    accepted: set[int] = set()

    async def repair_one(index: int) -> None:
        async with semaphore:
            repaired = await retry_flagged_batch(
                client, batches[index], cfg, file_context,
                prev_tail_for(batches, index, cfg.context_overlap),
                results[index], flagged[index],
            )
            if repaired is not results[index]:
                results[index] = repaired
                accepted.add(index)

    await asyncio.gather(*(repair_one(i) for i in selected))
    _note(cfg, colors.green(f"  Repaired {len(accepted)}/{len(selected)} flagged batch(es)"))
    return set(selected), accepted


def _surviving_by_cue(
    results: list[BatchResult],
    extras: dict[int, list[BatchFlag]],
    accepted: set[int],
) -> dict[int, list[BatchFlag]]:
    """What is still wrong after the batch retries, by cue. A batch's detector
    flags are re-read from whatever text it ends up shipping; the adequacy and
    phrase flags were never re-measured, so they stand unless the batch they
    belong to was re-translated and the retry kept."""
    by_cue: dict[int, list[BatchFlag]] = {}
    for index, result in enumerate(results):
        flags = list(result.flags)
        if index not in accepted:
            flags.extend(extras.get(index, []))
        for flag in flags:
            by_cue.setdefault(flag.block, []).append(flag)
    return by_cue


async def _repair_cues(
    client: httpx.AsyncClient,
    batches: list[list[SubtitleBlock]],
    results: list[BatchResult],
    extras: dict[int, list[BatchFlag]],
    attempted: set[int],
    accepted: set[int],
    cfg: TranslationConfig,
    file_context: FileContext | None,
    meter: ProgressMeter | None = None,
) -> None:
    """The narrower second pass: a cue still flagged after the batch retries is
    re-issued on its own, its problems named. A correction the model let slide
    inside a ten-cue batch is usually followed when the cue is all there is to
    do. Same acceptance rule as the batch retry, so it cannot make the file
    worse, and capped at the same share of the file's cues. A one-cue batch
    the batch pass already retried is not offered the same retry twice."""
    where = {block.number: (index, position)
             for index, batch in enumerate(batches)
             for position, block in enumerate(batch)}
    leftover = {
        number: flags
        for number, flags in _surviving_by_cue(results, extras, accepted).items()
        if number in where
        and not (len(batches[where[number][0]]) == 1
                 and where[number][0] in attempted)
    }
    if not leftover:
        return
    source = [block for batch in batches for block in batch]
    numbers = sorted(leftover)
    cap = fix_flagged_cap(len(source))
    chosen = numbers[:cap]
    if meter is not None:
        meter.plan("repairing", len(attempted) + len(chosen))
    colors = Colors()
    if len(chosen) < len(numbers):
        _note(cfg, colors.yellow(
            f"  {len(numbers)} flagged line(s) left after the batch "
            f"retries; re-translating {len(chosen)} on their own "
            f"(cap {cap}), leaving {len(numbers) - len(chosen)}"))
    else:
        _note(cfg, colors.cyan(f"  Re-translating {len(chosen)} flagged line(s) on their "
                               f"own..."))
    at = {block.number: i for i, block in enumerate(source)}
    semaphore = asyncio.Semaphore(max(1, cfg.concurrency))
    kept = 0

    async def repair_one(number: int) -> None:
        nonlocal kept
        index, position = where[number]
        flags = leftover[number]
        previous = BatchResult([results[index].blocks[position]], flags)
        start = at[number]
        # Its neighbours before it, as the batch would have shown them.
        prev_tail = (source[max(0, start - cfg.context_overlap):start]
                     if cfg.context_overlap > 0 else [])
        async with semaphore:
            repaired = await retry_flagged_batch(
                client, [source[start]], cfg, file_context, prev_tail,
                previous, flags,
            )
        if repaired is previous:
            return
        result = results[index]
        blocks = list(result.blocks)
        blocks[position] = repaired.blocks[0]
        results[index] = BatchResult(
            blocks,
            [f for f in result.flags if f.block != number] + repaired.flags,
        )
        extras[index] = [f for f in extras.get(index, []) if f.block != number]
        kept += 1

    await asyncio.gather(*(repair_one(n) for n in chosen))
    _note(cfg, colors.green(f"  Repaired {kept}/{len(chosen)} flagged line(s) on their own"))


def _plan_run(meter: ProgressMeter, batch_count: int, cfg: TranslationConfig) -> None:
    """The status line's pace: each step priced as the estimate would price it,
    to be revised as the run learns. Nothing here is a promise."""
    scan = 0 if cfg.glossary_in else 1
    attribution = math.ceil(batch_count / 4) if cfg.refine_attribution else 0
    meter.plan("prepass", scan + attribution)
    meter.plan("batches", batch_count * (2 if cfg.review else 1))
    if cfg.verify_adequacy and cfg.source_lang:
        meter.plan("checking", adequacy_sample_size(batch_count))
    if cfg.fix_flagged:
        meter.plan("repairing", fix_flagged_cap(batch_count))


FLAG_LINES_SHOWN = 25


def _flag_lines(left: dict[int, list[BatchFlag]], limit: int = FLAG_LINES_SHOWN) -> list[str]:
    """The lines still flagged, each with its reasons: the part worth acting on."""
    lines = []
    for number in sorted(left)[:limit]:
        reasons = sorted({re.sub(r"^Block \d+: ", "", flag.message) for flag in left[number]})
        lines.append(f"    line {number}: {'; '.join(reasons)}")
    if len(left) > limit:
        lines.append(f"    ... and {len(left) - limit} more")
    return lines


def _flag_summary(
    raised: set[tuple[int, str]], surviving: set[tuple[int, str]],
    left: dict[int, list[BatchFlag]],
) -> str:
    """The one line worth reading about the flags: how many the run raised,
    how many it put right itself, and which lines it could not."""
    line = (f"  Flags: {len(raised)} raised, {len(raised - surviving)} fixed "
            f"by the run, {len(surviving)} still flagged")
    lines = sorted(left)
    if 0 < len(lines) <= 12:
        line += f" (line{'s' if len(lines) > 1 else ''} {', '.join(map(str, lines))})"
    return line


@dataclass(frozen=True)
class FileReport:
    """What one finished file came to, for the queue's per-file line: the
    lines the run could not put right. The counts are printed by the run."""
    still_flagged: tuple[int, ...]


async def translate_file_async(
    input_path: Path,
    output_path: Path,
    cfg: TranslationConfig,
) -> FileReport:
    """Translate one subtitle file end-to-end.

    Raises FileTranslationError on anything that makes the file unusable: an
    undecodable or unparseable source, an unwritable output, an exhausted batch.
    """
    content, encoding = read_subtitle_text(input_path, cfg.encoding)
    doc = _parse_or_fail(input_path, content)
    _check_output_writable(output_path)

    batches = split_batches(doc.blocks, cfg.batch_size)
    colors = Colors()

    if encoding != "utf-8-sig":
        _note(cfg, colors.yellow(f"  Decoded {input_path.name} as {encoding}"))
    if not cfg.quiet:
        src_label = cfg.source_lang or "auto"
        print(
            f"{colors.bold('Translating')} {colors.cyan(str(len(doc.blocks)))} blocks "
            f"in {colors.cyan(str(len(batches)))} batches "
            f"{colors.dim(f'({src_label} → {cfg.target_lang}, {doc.format})')}"
        )
        if cfg.concurrency > 1:
            print(colors.dim(f"Concurrency: {cfg.concurrency}"))

    progress = _open_progress(input_path, output_path, cfg, len(doc.blocks))
    started_at = time.time()
    calls_before = cfg.calls.snapshot()
    # One status line for the whole file, from the first scan call to the
    # written output, so no step sits there unexplained. Multi-file runs are
    # quiet and report per file instead.
    display = RunDisplay(colors, started_at) if not cfg.quiet else None
    meter = display.meter if display is not None else None
    previous_say, previous_warn, previous_listener = cfg.say, cfg.warn, cfg.calls.listener
    if display is not None:
        cfg.say = display.say
        if cfg.verbose:
            cfg.warn = display.warn
        cfg.calls.listener = display.meter.count
        _plan_run(display.meter, len(batches), cfg)
        display.start()
    try:
        # One client for the whole file: prepass and batches share its connection pool.
        async with httpx.AsyncClient() as client:
            file_context = await _build_file_context(
                client, input_path, doc.blocks, cfg, colors,
            )
            if meter is not None and cfg.review:
                # Now the glossary is known, so is which batches earn a review call.
                reviewed = sum(1 for b in batches if file_context.has_correctable_entries(b))
                meter.plan("batches", len(batches) + reviewed)
            results = await run_batches(
                client, batches, cfg, colors, time.time(), file_context, progress,
                display,
            )
            if meter is not None and cfg.verify_adequacy and cfg.source_lang:
                meter.begin("checking")
            # The file-level flags, kept apart from the detectors': no retry can
            # re-measure them, so only an accepted retry of their batch clears them.
            extras = _flagged_batches(
                [], await _adequacy_flags(client, batches, results, cfg, colors),
                _phrase_flags(batches, results, cfg))
            flagged = _flagged_batches(results, extras)
            # Snapshot before any repair: the repairs below rewrite what survives.
            raised = {(flag.block, flag.cause)
                      for flags in flagged.values() for flag in flags}
            accepted: set[int] = set()
            if cfg.fix_flagged and flagged:
                if meter is not None:
                    meter.begin("repairing")
                attempted, accepted = await _repair_flagged(
                    client, batches, results, flagged, cfg, file_context, meter)
                await _repair_cues(
                    client, batches, results, extras, attempted, accepted, cfg,
                    file_context, meter)
            elif meter is not None:
                # Nothing to repair: the line should not wait for calls that never come.
                meter.plan("repairing", 0)
            left = _surviving_by_cue(results, extras, accepted)
            surviving = {(flag.block, flag.cause)
                         for flags in left.values() for flag in flags}
            if raised:
                summary = _flag_summary(raised, surviving, left)
                _note(cfg, colors.yellow(summary) if left else colors.green(summary))
                for line in _flag_lines(left):
                    _note(cfg, colors.yellow(line))
    except BaseException:
        if display is not None:
            display.stop()
        raise
    finally:
        cfg.say, cfg.warn, cfg.calls.listener = previous_say, previous_warn, previous_listener

    translated: list[SubtitleBlock] = []
    for r in results:
        translated.extend(r.blocks)
    translated = _normalize_file_orthography(translated, cfg)

    output_path.write_text(doc.rebuild(translated), encoding="utf-8")
    if display is not None:
        display.meter.finish()
        display.render()
        display.stop()
    if progress is not None:
        progress.discard()
    if not cfg.quiet:
        elapsed = time.time() - started_at
        throughput = len(doc.blocks) / elapsed if elapsed > 0 else 0
        made = cfg.calls.since(calls_before)
        print(
            f"{colors.green('✓ Completed')} in {format_duration(elapsed)} "
            f"{colors.dim(f'({throughput:.1f} blocks/s)')}"
        )
        print(colors.dim(f"  LLM calls: {made.total} ({describe_calls(made)})"))
        print(colors.dim(f"  Output: {output_path}"))
    return FileReport(tuple(sorted(left)))


@dataclass
class FilePlan:
    """Planned work for one file. `attribution_calls` is None when only the scan
    can decide the count; `review_calls` and `repair_calls` are upper bounds."""
    input_path: Path
    output_path: Path
    encoding: str
    blocks: int
    batches: int
    reused: int
    scan_calls: int
    attribution_calls: int | None
    translate_calls: int
    review_calls: int
    repair_calls: int
    back_translation_calls: int

    @property
    def known_calls(self) -> int:
        return (self.scan_calls + (self.attribution_calls or 0)
                + self.translate_calls + self.back_translation_calls)

    @property
    def upper_bound_calls(self) -> int:
        """Everything the file could cost, review and repair included."""
        return self.known_calls + self.review_calls + self.repair_calls


def plan_file(
    input_path: Path, output_path: Path, cfg: TranslationConfig,
) -> FilePlan:
    """Parse and batch one file to report the planned work. No API calls."""
    content, encoding = read_subtitle_text(input_path, cfg.encoding)
    doc = _parse_or_fail(input_path, content)
    _check_output_writable(output_path)
    batches = split_batches(doc.blocks, cfg.batch_size)

    reused = 0
    progress = _open_progress(input_path, output_path, cfg, len(doc.blocks))
    if progress is not None:
        reused = sum(1 for i, b in enumerate(batches)
                     if progress.get(i, b) is not None)
    remaining = len(batches) - reused

    attribution_calls: int | None = None
    if cfg.glossary_in:
        context = _load_glossary_for(input_path, doc.blocks, cfg)
        attribution_calls = (
            len(attribution_targets(context, cfg)) if cfg.refine_attribution else 0
        )
    elif not cfg.refine_attribution:
        attribution_calls = 0

    return FilePlan(
        input_path=input_path,
        output_path=output_path,
        encoding=encoding,
        blocks=len(doc.blocks),
        batches=len(batches),
        reused=reused,
        scan_calls=0 if cfg.glossary_in else 1,
        attribution_calls=attribution_calls,
        translate_calls=remaining,
        review_calls=remaining if cfg.review else 0,
        # Capped on the whole file: a resumed batch can still be flagged.
        repair_calls=fix_flagged_cap(len(batches)) if cfg.fix_flagged else 0,
        back_translation_calls=(
            adequacy_sample_size(len(batches))
            if cfg.verify_adequacy and cfg.source_lang else 0
        ),
    )
