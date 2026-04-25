"""Per-batch HTTP call, response sanitizing, and retry loop."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

from .config import TranslationConfig
from .constants import (
    ATTEMPTS_BEFORE_SPLIT,
    CRED_QUERY_PARAMS,
    REQUEST_TIMEOUT_SECS,
)
from .context_pass import FileContext
from .prompt import (
    REVIEW_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_review_user_message,
    build_translate_user_message,
)
from .srt_parser import SubtitleBlock, parse_lite, serialize_lite, validate_batch


class FileTranslationError(Exception):
    """A batch exhausted its retries; the whole file is considered failed."""


def sanitize_api_url(url: str) -> str:
    """Drop credential query params so we don't authenticate twice."""
    url = (url or "").strip()
    if not url:
        return url
    try:
        parts = urlsplit(url)
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in CRED_QUERY_PARAMS]
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(kept), parts.fragment))
    except Exception:
        return url


def sanitize_api_key(key: str) -> str:
    k = (key or "").strip()
    if (k.startswith('"') and k.endswith('"')) or \
       (k.startswith("'") and k.endswith("'")):
        k = k[1:-1].strip()
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


def strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def is_retryable_http(code: int) -> bool:
    return code in (408, 429) or code >= 500


async def call_chat_api(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_message: str,
    cfg: TranslationConfig,
    max_tokens: int,
) -> str:
    body: dict = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": max(max_tokens, 1),
        "stream": False,
    }
    if cfg.model:
        body["model"] = cfg.model

    url = sanitize_api_url(cfg.api_url)
    key = sanitize_api_key(cfg.api_key)
    headers = {"Content-Type": "application/json"}
    if key and key != "none":
        headers["Authorization"] = f"Bearer {key}"

    resp = await client.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT_SECS)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _review_pass(
    client: httpx.AsyncClient,
    batch: list[SubtitleBlock],
    first_pass: list[SubtitleBlock],
    cfg: TranslationConfig,
    file_context: FileContext | None,
) -> list[SubtitleBlock]:
    """Re-check first-pass against the glossary; returns first-pass unchanged
    if review output fails validation or there's no glossary to check against."""
    glossary = file_context.render_for_batch(batch) if file_context else ""
    if not glossary:
        return first_pass
    user_msg = build_review_user_message(batch, first_pass, glossary)
    try:
        raw = await call_chat_api(
            client, REVIEW_SYSTEM_PROMPT, user_msg, cfg, max(len(batch), 1) * 120)
    except Exception as e:
        cfg.warn(f"    Review failed, keeping first-pass: {e}")
        return first_pass
    parsed = parse_lite(strip_markdown_fences(raw))
    if len(parsed) != len(batch):
        return first_pass
    revised = [
        SubtitleBlock(number=batch[i].number,
                      timestamp=batch[i].timestamp,
                      text=parsed[i].text)
        for i in range(len(batch))
    ]
    return revised if validate_batch(batch, revised).ok else first_pass


async def translate_batch_with_retry(
    client: httpx.AsyncClient,
    batch_idx: int,
    batch: list[SubtitleBlock],
    cfg: TranslationConfig,
    file_context: FileContext | None = None,
    _split_path: str = "",
    prev_tail: list[SubtitleBlock] | None = None,
) -> list[SubtitleBlock]:
    """Translate one batch; on repeated validation failure, halve and recurse.

    Persistent count mismatches usually mean the model is deterministically
    merging two adjacent similar-looking blocks. Halving keeps terminating
    because at N=1 a count mismatch is impossible.
    """
    batch_wire = serialize_lite(batch)
    glossary = file_context.render_for_batch(batch) if file_context else ""
    user_msg = build_translate_user_message(
        cfg.source_lang, cfg.target_lang, batch_wire, glossary, prev_tail or [],
    )
    label = f"Batch {batch_idx + 1}" + (f".{_split_path}" if _split_path else "")
    first_block = batch[0].number

    can_split = len(batch) > 1
    attempts = ATTEMPTS_BEFORE_SPLIT if can_split else cfg.max_retries
    hit_validation_failure = False

    for attempt in range(1, attempts + 1):
        tag = f"attempt {attempt}/{attempts}"
        try:
            raw = await call_chat_api(
                client, SYSTEM_PROMPT, user_msg, cfg, max(len(batch), 1) * 120,
            )
            output = parse_lite(strip_markdown_fences(raw))
            if len(output) == len(batch):
                output = [
                    SubtitleBlock(number=batch[i].number,
                                  timestamp=batch[i].timestamp,
                                  text=output[i].text)
                    for i in range(len(batch))
                ]
            check = validate_batch(batch, output)
            if check.ok:
                if cfg.review:
                    output = await _review_pass(
                        client, batch, output, cfg, file_context,
                    )
                return output
            hit_validation_failure = True
            cfg.warn(f"    {label} validation failed ({tag}): {check.error}")

        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            snippet = (e.response.text or "")[:200]
            cfg.warn(f"    {label} HTTP {code} ({tag}): {snippet}")

            if not is_retryable_http(code):
                raise FileTranslationError(
                    f"{label} (block {first_block}) HTTP {code}: {snippet}"
                )
            if code == 429 and attempt < attempts:
                delay = 2 ** attempt
                cfg.warn(f"    Rate limited - waiting {delay}s...")
                await asyncio.sleep(delay)
                continue

        except Exception as e:
            cfg.warn(f"    {label} request failed ({tag}): {e}")

        if attempt < attempts:
            await asyncio.sleep(min(attempt, 3))

    if hit_validation_failure and can_split:
        mid = len(batch) // 2
        left, right = batch[:mid], batch[mid:]
        cfg.warn(
            f"    {label} splitting {len(batch)} -> {len(left)} + {len(right)} blocks"
        )
        left_path = (_split_path + "L") if _split_path else "L"
        right_path = (_split_path + "R") if _split_path else "R"
        # Sequential: parallel halves would oversubscribe the outer semaphore.
        left_result = await translate_batch_with_retry(
            client, batch_idx, left, cfg, file_context, left_path,
            prev_tail=prev_tail,
        )
        right_prev = left[-cfg.context_overlap:] if cfg.context_overlap else []
        right_result = await translate_batch_with_retry(
            client, batch_idx, right, cfg, file_context, right_path,
            prev_tail=right_prev,
        )
        return left_result + right_result

    raise FileTranslationError(
        f"{label} (block {first_block}) failed all {attempts} retries"
    )
