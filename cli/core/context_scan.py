"""The prepass calls: one scan for the file's glossary, then one small
attribution call per scene whose speakers the translation depends on."""

from __future__ import annotations

import asyncio

import httpx

from .chat_client import call_chat_api, strip_markdown_fences
from .config import TranslationConfig
from .constants import SCAN_MAX_TOKENS
from .context_parse import parse_attribution_response, parse_context_response
from .context_pass import (
    CharacterHint,
    FileContext,
    SceneHint,
    clamp_scenes_to_blocks,
    enrich_scenes_with_block_text,
    gender_mark,
    recurring_phrases,
    scenes_needing_attribution,
    serialize_for_scan,
)
from .languages import effective_norms, grammar_note_for
from .prompt import (
    ATTRIBUTION_SYSTEM_PROMPT,
    CONTEXT_SYSTEM_PROMPT,
    build_attribution_user_message,
    build_scan_user_message,
)
from .srt_parser import SubtitleBlock


def attribution_targets(
    context: FileContext, cfg: TranslationConfig,
) -> list[SceneHint]:
    """Scenes worth one attribution call under this run's settings. A target
    whose script inflects for gender qualifies every two-hander scene, known
    genders or not; --full-attribution buys every scene with a cast."""
    norms = effective_norms(cfg.target_lang, cfg.max_line_chars)
    return scenes_needing_attribution(
        context, cfg.full_attribution, bool(grammar_note_for(norms.script)))


async def extract_file_context(
    client: httpx.AsyncClient,
    blocks: list[SubtitleBlock],
    cfg: TranslationConfig,
) -> FileContext:
    """Run one scan call. Returns the parsed+enriched context."""
    user_msg = build_scan_user_message(
        cfg.source_lang, cfg.target_lang,
        serialize_for_scan(blocks, cfg.scan_char_budget),
        cfg.dialect,
        recurring_phrases(blocks),
    )
    try:
        cfg.calls.count("scan")
        raw = await call_chat_api(
            client, CONTEXT_SYSTEM_PROMPT, user_msg, cfg,
            max_tokens=SCAN_MAX_TOKENS,
        )
    except Exception as e:
        cfg.warn(f"    Context scan failed, proceeding without: {e}")
        return FileContext()

    context = parse_context_response(strip_markdown_fences(raw))
    if context.is_empty():
        snippet = (raw or "").strip().replace("\n", " ")[:240]
        cfg.warn(f"    Context scan returned empty glossary. Raw start: {snippet!r}")
    else:
        clamp_scenes_to_blocks(context, blocks)
        enrich_scenes_with_block_text(context, blocks)
    return context


async def _attribute_scene(
    client: httpx.AsyncClient,
    scene: SceneHint,
    by_num: dict[int, SubtitleBlock],
    cfg: TranslationConfig,
    characters: list[CharacterHint],
) -> dict[int, str]:
    present = set(scene.participants)
    roster = "\n".join(
        f"- {h.source} ({gender_mark(h.gender) or '?'})"
        for h in characters if h.source in present
    )
    block_lines = [
        f"[{n}] {by_num[n].text.replace(chr(10), ' ')}"
        for n in range(scene.start, scene.end + 1) if n in by_num
    ]
    if not block_lines or not roster:
        return {}
    user_msg = build_attribution_user_message(roster, block_lines)
    try:
        cfg.calls.count("attribution")
        raw = await call_chat_api(
            client, ATTRIBUTION_SYSTEM_PROMPT, user_msg, cfg,
            max_tokens=len(block_lines) * 20 + 100,
        )
    except Exception as e:
        cfg.warn(f"    Attribution failed for blocks {scene.start}-{scene.end}: {e}")
        return {}
    return parse_attribution_response(raw, scene, characters)


async def refine_scene_attribution(
    client: httpx.AsyncClient,
    context: FileContext,
    blocks: list[SubtitleBlock],
    cfg: TranslationConfig,
) -> None:
    """Fill `SceneHint.attribution` for multi-block scenes with named
    participants. One small LLM call per target scene, bounded by concurrency."""
    targets = attribution_targets(context, cfg)
    if not targets:
        return
    by_num = {b.number: b for b in blocks}
    sem = asyncio.Semaphore(max(1, cfg.concurrency))

    async def do(scene: SceneHint) -> None:
        async with sem:
            scene.attribution = await _attribute_scene(
                client, scene, by_num, cfg, context.characters)

    await asyncio.gather(*(do(s) for s in targets))
