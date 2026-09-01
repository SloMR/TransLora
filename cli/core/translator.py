"""Per-file orchestration: parse, prepass scan, batched translate, stitch."""

from __future__ import annotations

import asyncio
import os
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
    """Progress detail: printed in single-file mode, routed to warn otherwise."""
    if cfg.quiet:
        cfg.warn(message)
    else:
        print(message)


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
            print(colors.dim(f"  Loading glossary from {cfg.glossary_in} "
                             f"(scan skipped)"))
        file_context = _load_glossary_for(input_path, blocks, cfg)
    else:
        if not cfg.quiet:
            print(colors.dim("  Scanning for cast and context..."))
        file_context = await extract_file_context(client, blocks, cfg)

    if cfg.refine_attribution and attribution_targets(file_context, cfg):
        if not cfg.quiet:
            print(colors.dim("  Attributing speakers in two-hander scenes..."))
        await refine_scene_attribution(client, file_context, blocks, cfg)

    if not cfg.quiet:
        if file_context.is_empty():
            print(colors.dim("  Glossary: empty (proceeding without context hints)"))
        else:
            chars = len(file_context.characters)
            terms = len(file_context.terms)
            idioms = len(file_context.idioms)
            scenes = len(file_context.scenes)
            attrib = sum(1 for s in file_context.scenes if s.attribution)
            notes = len(file_context.notes)
            print(colors.dim(
                f"  Glossary: {chars} character(s), {terms} term(s), "
                f"{idioms} idiom(s), {scenes} scene(s) ({attrib} attributed), "
                f"{notes} note(s)"
            ))
            if file_context.register:
                print(colors.dim(f"  Register: {file_context.register}"))

    if cfg.glossary_out:
        try:
            save_glossary(cfg.glossary_out, file_context, input_path.name)
        except OSError as e:
            cfg.warn(f"    Could not write glossary {cfg.glossary_out}: {e}")
        else:
            if not cfg.quiet:
                print(colors.dim(f"  Glossary written: {cfg.glossary_out}"))
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
        cfg.warn(f"    Removed diacritics from {changed} cue(s): the rest of "
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
        print(colors.dim("  Checking adequacy by back-translation..."))
    return await verify_adequacy(client, batches, results, cfg)


async def _repair_flagged(
    client: httpx.AsyncClient,
    batches: list[list[SubtitleBlock]],
    results: list[BatchResult],
    flagged: dict[int, list[BatchFlag]],
    cfg: TranslationConfig,
    file_context: FileContext | None,
) -> None:
    """Re-issue the flagged batches, rarest cause first so a failure spanning
    the file cannot crowd out the one-offs. Each retry is kept only if it comes
    back with strictly fewer flags, so this can never make the file worse."""
    plan = plan_repairs(
        {index: [flag.cause for flag in flags]
         for index, flags in flagged.items() if flags},
        len(batches),
    )
    selected = plan.selected
    if plan.skipped:
        _note(cfg, f"  {plan.flagged} flagged batch(es) across {plan.causes} "
                   f"cause(s); repairing {len(selected)} (cap {plan.cap}), "
                   f"leaving {plan.skipped}")
    else:
        _note(cfg, f"  Repairing {len(selected)} flagged batch(es) across "
                   f"{plan.causes} cause(s)...")

    semaphore = asyncio.Semaphore(max(1, cfg.concurrency))
    accepted = 0

    async def repair_one(index: int) -> None:
        nonlocal accepted
        async with semaphore:
            repaired = await retry_flagged_batch(
                client, batches[index], cfg, file_context,
                prev_tail_for(batches, index, cfg.context_overlap),
                results[index], flagged[index],
            )
            if repaired is not results[index]:
                results[index] = repaired
                accepted += 1

    await asyncio.gather(*(repair_one(i) for i in selected))
    _note(cfg, f"  Repaired {accepted}/{len(selected)} flagged batch(es)")


async def translate_file_async(
    input_path: Path,
    output_path: Path,
    cfg: TranslationConfig,
) -> None:
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
    # One client for the whole file: prepass and batches share its connection pool.
    async with httpx.AsyncClient() as client:
        file_context = await _build_file_context(
            client, input_path, doc.blocks, cfg, colors,
        )
        # The batch clock excludes the prepass, which would otherwise skew the ETA.
        results = await run_batches(
            client, batches, cfg, colors, time.time(), file_context, progress,
        )
        extra = await _adequacy_flags(client, batches, results, cfg, colors)
        flagged = _flagged_batches(
            results, extra, _phrase_flags(batches, results, cfg))
        if cfg.fix_flagged and flagged:
            await _repair_flagged(
                client, batches, results, flagged, cfg, file_context)

    translated: list[SubtitleBlock] = []
    for r in results:
        translated.extend(r.blocks)
    translated = _normalize_file_orthography(translated, cfg)

    output_path.write_text(doc.rebuild(translated), encoding="utf-8")
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
