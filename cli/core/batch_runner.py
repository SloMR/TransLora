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

from .srt_parser import SubtitleBlock, parse_srt, serialize_srt, validate_batch
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
    batch_srt: str,
    cfg: TranslationConfig,
) -> str:
    """POST one batch to the OpenAI-compatible chat endpoint, return raw text."""
    body: dict = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"Translate from {cfg.source_lang} to {cfg.target_lang}:\n\n{batch_srt}"},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
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


async def translate_batch_with_retry(
    client: httpx.AsyncClient,
    batch_idx: int,
    batch: list[SubtitleBlock],
    cfg: TranslationConfig,
) -> list[SubtitleBlock]:
    """Translate one batch; retry on transient errors; raise on exhaustion."""
    batch_srt = serialize_srt(batch)
    label = f"Batch {batch_idx + 1}"
    first_block = batch[0].number

    for attempt in range(1, cfg.max_retries + 1):
        tag = f"attempt {attempt}/{cfg.max_retries}"
        try:
            raw = await call_chat_api(client, batch_srt, cfg)
            output = parse_srt(strip_markdown_fences(raw))
            check = validate_batch(batch, output)
            if check.ok:
                return output
            cfg.warn(f"    {label} validation failed ({tag}): {check.error}")

        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            snippet = (e.response.text or "")[:200]
            cfg.warn(f"    {label} HTTP {code} ({tag}): {snippet}")

            if not is_retryable_http(code):
                raise FileTranslationError(
                    f"{label} (block {first_block}) HTTP {code}: {snippet}"
                )
            if code == 429 and attempt < cfg.max_retries:
                delay = 2 ** attempt
                cfg.warn(f"    Rate limited — waiting {delay}s...")
                await asyncio.sleep(delay)
                continue

        except Exception as e:  # network error, JSON decode error, etc.
            cfg.warn(f"    {label} request failed ({tag}): {e}")

        # Small back-off before the next attempt (1s, 2s, 3s cap).
        if attempt < cfg.max_retries:
            await asyncio.sleep(min(attempt, 3))

    raise FileTranslationError(
        f"{label} (block {first_block}) failed all {cfg.max_retries} retries"
    )
