"""Transport for one OpenAI-compatible chat call: credential hygiene, the
request shape the endpoint turns out to accept, and turning an unusable reply
into a retryable error."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .config import (
    Provider,
    ProviderDialect,
    TranslationConfig,
    dialect_warning,
)
from .constants import (
    CRED_QUERY_PARAMS,
    MAX_DIALECT_CORRECTIONS,
    MAX_RETRY_DELAY_SECS,
    REASONING_BUDGET_MULTIPLIER,
    REASONING_EFFORT_MINIMAL,
    REQUEST_TEMPERATURE,
)


class RetryableApiError(Exception):
    """The provider answered, but not usably — worth another attempt."""


class FatalApiError(Exception):
    """The provider will answer the same way however often we ask."""


class ReasoningBudgetError(RetryableApiError):
    """A reasoning model spent the whole token budget before writing a word.
    Retryable, but only with a bigger budget — the same request would burn the
    same tokens again."""


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


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_markdown_fences(text: str) -> str:
    text = _THINK_BLOCK_RE.sub("", text).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def is_retryable_http(code: int) -> bool:
    return code in (408, 429) or code >= 500


# Providers phrase context overflow differently, but all of them say "context".
_CONTEXT_OVERFLOW_RE = re.compile(
    r"context[ _-]?(?:length|window|size)|maximum context|too many tokens",
    re.I,
)


def is_context_overflow(body: str) -> bool:
    """True when a 400 body blames the request's size, not its shape."""
    return bool(_CONTEXT_OVERFLOW_RE.search(body or ""))


def retry_delay(failures: int, retry_after: str | None = None) -> float:
    """Capped exponential backoff; a numeric Retry-After header wins."""
    if retry_after:
        try:
            return max(0.0, min(float(retry_after.strip()), MAX_RETRY_DELAY_SECS))
        except ValueError:
            pass  # HTTP-date form — not worth parsing, fall back to backoff.
    return min(2.0 ** failures, MAX_RETRY_DELAY_SECS)


def extract_message_content(payload: object) -> str:
    """Assistant text from an OpenAI-shaped response. Providers return error-shaped
    and truncated bodies with HTTP 200, so any unusable shape becomes retryable."""
    if not isinstance(payload, dict):
        raise RetryableApiError("response body is not a JSON object")
    err = payload.get("error")
    if err:
        msg = err.get("message") if isinstance(err, dict) else err
        raise RetryableApiError(f"provider returned an error: {msg}")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RetryableApiError("response contains no choices")
    choice = choices[0]
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    finish_reason = choice.get("finish_reason")
    if not isinstance(content, str) or not content.strip():
        # Nothing at all with the budget exhausted: the model reasoned instead
        # of answering, and only a bigger budget can change that.
        if finish_reason == "length":
            raise ReasoningBudgetError(
                "the token budget was spent on reasoning before any text")
        raise RetryableApiError("response contains no message content")
    if finish_reason == "length":
        raise RetryableApiError("response truncated (finish_reason=length)")
    return content


def request_body(
    system_prompt: str,
    user_message: str,
    endpoint: Provider,
    dialect: ProviderDialect,
    max_tokens: int,
) -> dict:
    """The wire body, in the dialect this endpoint has proved it accepts."""
    body: dict = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        dialect.token_param: max(max_tokens, 1),
        "stream": False,
    }
    if dialect.send_temperature:
        body["temperature"] = REQUEST_TEMPERATURE
    if dialect.minimal_reasoning:
        body["reasoning_effort"] = REASONING_EFFORT_MINIMAL
    if endpoint.model:
        body["model"] = endpoint.model
    return body


async def call_chat_api(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_message: str,
    cfg: TranslationConfig,
    max_tokens: int,
    provider: Provider | None = None,
) -> str:
    """One chat completion. `provider` routes the call somewhere other than
    the run's main endpoint; the timeout is the run's either way.

    Two corrections are made here rather than in a caller's retry loop, so a
    provider's quirks cost a round trip and not a retry budget: the request
    dialect a 400 asks for, learned once for the whole run, and the token
    budget a reasoning model needs before it produces any text, tracked for
    this request alone. An endpoint that accepts the defaults pays neither.
    """
    endpoint = provider or cfg.provider
    dialect = cfg.dialect_for(endpoint)

    url = sanitize_api_url(endpoint.api_url)
    key = sanitize_api_key(endpoint.api_key)
    headers = {"Content-Type": "application/json"}
    if key and key != "none":
        headers["Authorization"] = f"Bearer {key}"

    budget = max(max_tokens, 1)
    ceiling = budget * REASONING_BUDGET_MULTIPLIER
    corrections = 0

    while True:
        sent = (dialect.token_param, dialect.send_temperature)
        resp = await client.post(
            url,
            json=request_body(system_prompt, user_message, endpoint,
                              dialect, budget),
            headers=headers,
            timeout=cfg.request_timeout,
        )
        if resp.status_code == 400 and corrections < MAX_DIALECT_CORRECTIONS:
            changes = dialect.adjust_for(resp.text)
            # A concurrent call may have learned this lesson while we were in
            # flight, leaving nothing to adjust: the request is still the one
            # shape that was refused, so re-send it in the shape it learned.
            if changes or (dialect.token_param, dialect.send_temperature) != sent:
                corrections += 1
                for change in changes:
                    cfg.warn(dialect_warning(change))
                continue
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as e:
            raise RetryableApiError(f"response was not JSON: {e}") from e
        try:
            return extract_message_content(payload)
        except ReasoningBudgetError as e:
            if budget >= ceiling:
                raise FatalApiError(
                    f"the model returned no text at {budget} tokens "
                    f"({REASONING_BUDGET_MULTIPLIER}x this request's budget) "
                    f"— it spent all of it reasoning. Use a non-reasoning "
                    f"model, or keep this one for the review pass only with "
                    f"--review-model."
                ) from e
            budget = min(budget * 2, ceiling)
            cfg.warn(f"    {e}; retrying with {budget} tokens.")
