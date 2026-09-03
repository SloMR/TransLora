"""The back-translation spot check: a sample of finished batches is translated
back to the source language and compared with the source it came from, so a cue
that quietly dropped half its meaning gets flagged for repair.

Opt-in — it costs one call per sampled batch, about a fifth of the file.
"""

from __future__ import annotations

import asyncio

import httpx

from .batch_runner import BatchFlag, BatchResult, output_token_budget
from .chat_client import call_chat_api, strip_markdown_fences
from .config import TranslationConfig
from .constants import ADEQUACY_MIN_OVERLAP, ADEQUACY_MIN_SOURCE_WORDS
from .prompt import (
    BACK_TRANSLATION_SYSTEM_PROMPT,
    build_back_translation_user_message,
)
from .repair import content_words
from .run_stats import adequacy_sample_size, sample_indices
from .srt_parser import SubtitleBlock, parse_lite, serialize_lite


def token_overlap(source_text: str, back_text: str) -> float:
    """Share of the source cue's distinct words the back-translation returned.
    A source cue with no words at all counts as fully covered."""
    wanted = set(content_words(source_text))
    if not wanted:
        return 1.0
    return len(wanted & set(content_words(back_text))) / len(wanted)


def _compare_back_translation(
    batch: list[SubtitleBlock], back: list[SubtitleBlock],
) -> list[BatchFlag]:
    """Matched by block number, so a reply that dropped or reordered a block
    only costs us the cues it actually lost."""
    by_number = {b.number: b.text for b in back}
    flags: list[BatchFlag] = []
    for src in batch:
        returned = by_number.get(src.number)
        if returned is None:
            continue
        if len(content_words(src.text)) < ADEQUACY_MIN_SOURCE_WORDS:
            continue
        overlap = token_overlap(src.text, returned)
        if overlap >= ADEQUACY_MIN_OVERLAP:
            continue
        flags.append(BatchFlag(
            block=src.number,
            message=(f"Block {src.number}: the back-translation recovered only "
                     f"{round(overlap * 100)}% of the source wording"),
            problem=(f"block {src.number}: the translation leaves out part of "
                     f"what the source says"),
            cause="adequacy",
        ))
    return flags


async def verify_adequacy(
    client: httpx.AsyncClient,
    batches: list[list[SubtitleBlock]],
    results: list[BatchResult],
    cfg: TranslationConfig,
) -> dict[int, list[BatchFlag]]:
    """Flags for every cue in the sample whose meaning did not survive the
    round trip, keyed by the batch index they belong to. Best-effort: a failed
    back-translation is a missing opinion, never a failed file."""
    found: dict[int, list[BatchFlag]] = {}
    sample = [
        index
        for index in sample_indices(len(batches),
                                    adequacy_sample_size(len(batches)))
        if index < len(results) and results[index].blocks
    ]
    if not sample:
        return found
    failures = 0
    semaphore = asyncio.Semaphore(max(1, cfg.concurrency))

    async def check(index: int) -> None:
        nonlocal failures
        async with semaphore:
            output = results[index].blocks
            wire = serialize_lite(output)
            try:
                cfg.calls.count("back_translation")
                raw = await call_chat_api(
                    client, BACK_TRANSLATION_SYSTEM_PROMPT,
                    build_back_translation_user_message(cfg.source_lang, wire),
                    cfg, output_token_budget(output, wire),
                )
            except Exception as e:
                failures += 1
                cfg.warn(f"    Back-translation failed for batch "
                         f"{index + 1}: {e}")
                return
            flags = _compare_back_translation(
                batches[index], parse_lite(strip_markdown_fences(raw)))
            if flags:
                found[index] = flags

    await asyncio.gather(*(check(index) for index in sample))
    if failures:
        cfg.warn(f"    Adequacy check failed for {failures} batch(es) - "
                 f"continuing without them")
    return found
