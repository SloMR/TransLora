"""Per-batch HTTP call, response sanitizing, and retry loop.

This is the "send one batch, get it back validated" layer. It knows how
to talk to an OpenAI-compatible chat endpoint and how to recover from
transient failures. Everything above this layer (translator.py) just
asks for batches and stitches them together.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

from .context_pass import FileContext
from .srt_parser import SubtitleBlock, parse_lite, serialize_lite, validate_batch
from .config import TranslationConfig
from .prompt import SYSTEM_PROMPT


REQUEST_TIMEOUT_SECS = 120.0

_CRED_QUERY_PARAMS = {"key", "api_key", "apikey", "access_token"}


class FileTranslationError(Exception):
    """A batch used up all its retries — the whole file is considered failed."""


# ---------------------------------------------------------------------------
# Input sanitization — users paste URLs/keys in all kinds of shapes.
# ---------------------------------------------------------------------------

def sanitize_api_url(url: str) -> str:
    """Drop credential query params like `?key=...` so we don't authenticate
    twice when the user pastes a pre-keyed URL."""
    url = (url or "").strip()
    if not url:
        return url
    try:
        parts = urlsplit(url)
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in _CRED_QUERY_PARAMS]
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(kept), parts.fragment))
    except Exception:
        return url


def sanitize_api_key(key: str) -> str:
    """Strip whitespace, surrounding quotes, and any `Bearer ` prefix."""
    k = (key or "").strip()
    if (k.startswith('"') and k.endswith('"')) or \
       (k.startswith("'") and k.endswith("'")):
        k = k[1:-1].strip()
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


def strip_markdown_fences(text: str) -> str:
    """LLMs sometimes wrap output in ```...``` despite being told not to."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def is_retryable_http(code: int) -> bool:
    """Retry on timeout / rate-limit / server errors. Everything else is fatal."""
    return code in (408, 429) or code >= 500


# ---------------------------------------------------------------------------
# HTTP call + retry
# ---------------------------------------------------------------------------

async def call_chat_api(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_message: str,
    cfg: TranslationConfig,
    max_tokens: int,
) -> str:
    """POST one chat request to the OpenAI-compatible endpoint, return raw text."""
    body: dict = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": max(max_tokens, 1),
        "stream": False,
        "cache_prompt": True,
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


def _build_user_message(
    cfg: TranslationConfig,
    batch_wire: str,
    file_context: FileContext | None,
    batch: list[SubtitleBlock],
) -> str:
    """Assemble the user message, prepending any relevant glossary slice."""
    if cfg.source_lang:
        header = f"Translate from {cfg.source_lang} to {cfg.target_lang}:"
    else:
        header = f"Translate to {cfg.target_lang}:"
    if file_context is not None:
        ctx = file_context.render_for_batch(batch)
        if ctx:
            return f"Glossary for this scene:\n{ctx}\n\n{header}\n\n{batch_wire}"
    return f"{header}\n\n{batch_wire}"


_ATTEMPTS_BEFORE_SPLIT = 2


async def translate_batch_with_retry(
    client: httpx.AsyncClient,
    batch_idx: int,
    batch: list[SubtitleBlock],
    cfg: TranslationConfig,
    file_context: FileContext | None = None,
    _split_path: str = "",
) -> list[SubtitleBlock]:
    """Translate one batch; on repeated validation failure split it in half.

    Persistent count mismatches usually mean the model is deterministically
    merging two adjacent similar-looking blocks (e.g., repeated reactions
    like "Oh." / "Oh!"). Splitting gives the model fewer similar blocks to
    confuse and almost always resolves the merge. We keep halving until we
    reach single-block batches, which can't have count mismatches.
    """
    batch_wire = serialize_lite(batch)
    user_msg = _build_user_message(cfg, batch_wire, file_context, batch)
    label = f"Batch {batch_idx + 1}" + (f".{_split_path}" if _split_path else "")
    first_block = batch[0].number

    can_split = len(batch) > 1
    attempts = _ATTEMPTS_BEFORE_SPLIT if can_split else cfg.max_retries
    hit_validation_failure = False

    for attempt in range(1, attempts + 1):
        tag = f"attempt {attempt}/{attempts}"
        try:
            raw = await call_chat_api(
                client, SYSTEM_PROMPT, user_msg, cfg, max(len(batch), 1) * 120,
            )
            output = parse_lite(strip_markdown_fences(raw))
            # Reattach timestamps from the original input positionally.
            if len(output) == len(batch):
                output = [
                    SubtitleBlock(number=batch[i].number,
                                  timestamp=batch[i].timestamp,
                                  text=output[i].text)
                    for i in range(len(batch))
                ]
            check = validate_batch(batch, output)
            if check.ok:
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

        except Exception as e:  # network error, JSON decode error, etc.
            cfg.warn(f"    {label} request failed ({tag}): {e}")

        # Small back-off before the next attempt (1s, 2s, 3s cap).
        if attempt < attempts:
            await asyncio.sleep(min(attempt, 3))

    # All attempts exhausted. If we hit validation errors and can still split,
    # cut the batch in half and retry each half independently. Otherwise fail.
    if hit_validation_failure and can_split:
        mid = len(batch) // 2
        left, right = batch[:mid], batch[mid:]
        cfg.warn(
            f"    {label} splitting {len(batch)} -> {len(left)} + {len(right)} blocks"
        )
        left_path = (_split_path + "L") if _split_path else "L"
        right_path = (_split_path + "R") if _split_path else "R"
        # Sequential: parallel halves would oversubscribe the outer semaphore's
        # per-batch slot and starve other batches.
        left_result = await translate_batch_with_retry(
            client, batch_idx, left, cfg, file_context, left_path,
        )
        right_result = await translate_batch_with_retry(
            client, batch_idx, right, cfg, file_context, right_path,
        )
        return left_result + right_result

    raise FileTranslationError(
        f"{label} (block {first_block}) failed all {attempts} retries"
    )
