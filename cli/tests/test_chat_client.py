"""Credential hygiene, fence stripping, retry classification, the response
shapes the transport refuses, and the request dialect it learns from a
provider's own 400s.

Nothing here touches the network: the pure functions are called directly and
`call_chat_api` is driven through an httpx MockTransport.
"""

from __future__ import annotations

import json

import httpx
import pytest

import core.chat_client as cc
from core.config import (
    REASONING_CHANGE,
    TEMPERATURE_CHANGE,
    TOKEN_PARAM_CHANGE,
    Provider,
    ProviderDialect,
    dialect_notes,
)
from core.constants import (
    MAX_RETRY_DELAY_SECS,
    REASONING_BUDGET_MULTIPLIER,
    REQUEST_TEMPERATURE,
    TOKEN_PARAM_COMPLETION,
    TOKEN_PARAM_DEFAULT,
)
from tests.conftest import run_async

# === Sanitizers ==============================================================


@pytest.mark.parametrize("raw,expected", [
    ("```\n1\nHi\n```", "1\nHi"),
    ("```srt\n1\nHi\n```", "1\nHi"),
    ("  ```json\n1\nHi\n```  ", "1\nHi"),
    ("```srt\n1\nHi", "1\nHi"),          # fence opened, never closed
    ("1\nHi", "1\nHi"),                   # unfenced text is untouched
    ("1\nuse ``` inline", "1\nuse ``` inline"),
    ("<think>merge these?</think>\n\n1\nHi", "1\nHi"),
    ("<THINK>\nreasoning\n</THINK>```\n1\nHi\n```", "1\nHi"),
    ("", ""),
])
def test_strip_markdown_fences(raw: str, expected: str) -> None:
    assert cc.strip_markdown_fences(raw) == expected


def test_sanitize_api_url_drops_credential_query_params() -> None:
    url = cc.sanitize_api_url(
        "https://host/v1/chat?key=secret&api_key=s2&model=x&access_token=s3")
    assert "secret" not in url and "s2" not in url and "s3" not in url
    assert url == "https://host/v1/chat?model=x"


@pytest.mark.parametrize("raw,expected", [
    ('"sk-abc"', "sk-abc"),
    ("'sk-abc'", "sk-abc"),
    ("Bearer sk-abc", "sk-abc"),
    ("  bearer sk-abc  ", "sk-abc"),
    ("", ""),
])
def test_sanitize_api_key(raw: str, expected: str) -> None:
    assert cc.sanitize_api_key(raw) == expected


@pytest.mark.parametrize("code,retryable", [
    (408, True), (429, True), (500, True), (503, True),
    (400, False), (401, False), (404, False),
])
def test_is_retryable_http(code: int, retryable: bool) -> None:
    assert cc.is_retryable_http(code) is retryable


@pytest.mark.parametrize("body,overflow", [
    ("This model's maximum context length is 8192 tokens", True),
    ("context_length_exceeded", True),
    ("too many tokens in request", True),
    ("invalid model name", False),
    ("", False),
])
def test_is_context_overflow(body: str, overflow: bool) -> None:
    assert cc.is_context_overflow(body) is overflow


def test_retry_delay_prefers_numeric_retry_after_and_caps() -> None:
    assert cc.retry_delay(1, "7") == 7.0
    assert cc.retry_delay(1, str(MAX_RETRY_DELAY_SECS + 100)) == MAX_RETRY_DELAY_SECS
    # HTTP-date form is not parsed; fall back to exponential backoff.
    assert cc.retry_delay(2, "Wed, 21 Oct 2015 07:28:00 GMT") == 4.0
    assert cc.retry_delay(20) == MAX_RETRY_DELAY_SECS


# === Response shape ==========================================================


def test_extract_message_content_returns_text() -> None:
    payload = {"choices": [{"message": {"content": "1\nHola"}}]}
    assert cc.extract_message_content(payload) == "1\nHola"


@pytest.mark.parametrize("payload,fragment", [
    ("not a dict", "not a JSON object"),
    ({"error": {"message": "rate limited"}}, "rate limited"),
    ({"choices": []}, "no choices"),
    ({"choices": [{"message": {"content": None}}]}, "no message content"),
    ({"choices": [{"message": {"content": "   "}}]}, "no message content"),
    ({"choices": [{"message": {"content": "1\nHi"},
                   "finish_reason": "length"}]}, "truncated"),
])
def test_extract_message_content_rejects_unusable_shapes(payload, fragment) -> None:
    with pytest.raises(cc.RetryableApiError, match=fragment):
        cc.extract_message_content(payload)


def test_an_empty_reply_that_ran_out_of_budget_blames_reasoning() -> None:
    """Distinct from a plain truncation: only a bigger budget can fix it."""
    payload = {"choices": [{"message": {"content": ""},
                            "finish_reason": "length"}]}
    with pytest.raises(cc.ReasoningBudgetError, match="reasoning"):
        cc.extract_message_content(payload)
    assert issubclass(cc.ReasoningBudgetError, cc.RetryableApiError)


def test_a_null_content_with_an_exhausted_budget_blames_reasoning() -> None:
    payload = {"choices": [{"message": {"content": None, "reasoning": "..."},
                            "finish_reason": "length"}]}
    with pytest.raises(cc.ReasoningBudgetError):
        cc.extract_message_content(payload)


def test_an_empty_reply_that_finished_normally_is_not_a_reasoning_failure() -> None:
    payload = {"choices": [{"message": {"content": ""},
                            "finish_reason": "stop"}]}
    with pytest.raises(cc.RetryableApiError, match="no message content"):
        cc.extract_message_content(payload)


# === Provider dialect ========================================================

# The two bodies api.openai.com actually returns for gpt-5, verbatim.
MAX_TOKENS_400 = (
    '{"error": {"message": "Unsupported parameter: \'max_tokens\' is not '
    'supported with this model. Use \'max_completion_tokens\' instead.", '
    '"type": "invalid_request_error", "param": "max_tokens", '
    '"code": "unsupported_parameter"}}'
)
TEMPERATURE_400 = (
    '{"error": {"message": "Unsupported value: \'temperature\' does not '
    'support 0.1 with this model. Only the default (1) value is supported.", '
    '"type": "invalid_request_error", "param": "temperature", '
    '"code": "unsupported_value"}}'
)
UNRELATED_400 = '{"error": {"message": "The model `x` does not exist."}}'


def test_a_dialect_starts_at_the_permissive_default() -> None:
    dialect = ProviderDialect()
    assert dialect.token_param == TOKEN_PARAM_DEFAULT
    assert dialect.send_temperature is True
    assert dialect.describe() == ""


def test_the_token_param_400_switches_only_the_token_parameter() -> None:
    dialect = ProviderDialect()
    assert dialect.adjust_for(MAX_TOKENS_400) == [TOKEN_PARAM_CHANGE]
    assert dialect.token_param == TOKEN_PARAM_COMPLETION
    assert dialect.send_temperature is True
    assert dialect.describe() == "max_completion_tokens, minimal reasoning"


def test_the_temperature_400_only_stops_sending_temperature() -> None:
    dialect = ProviderDialect()
    assert dialect.adjust_for(TEMPERATURE_400) == [TEMPERATURE_CHANGE]
    assert dialect.send_temperature is False
    assert dialect.token_param == TOKEN_PARAM_DEFAULT
    assert dialect.describe() == "no temperature"


def test_an_unrelated_400_adjusts_nothing() -> None:
    dialect = ProviderDialect()
    assert dialect.adjust_for(UNRELATED_400) == []
    assert dialect.adjust_for("") == []
    assert dialect.describe() == ""


def test_a_correction_is_never_applied_twice() -> None:
    """The caller stops when nothing changed, so a provider repeating itself
    cannot spin the corrective retry forever."""
    dialect = ProviderDialect()
    assert dialect.adjust_for(MAX_TOKENS_400) == [TOKEN_PARAM_CHANGE]
    assert dialect.adjust_for(MAX_TOKENS_400) == []


def test_one_body_naming_both_quirks_corrects_both() -> None:
    dialect = ProviderDialect()
    changes = dialect.adjust_for(MAX_TOKENS_400 + TEMPERATURE_400)
    assert changes == [TOKEN_PARAM_CHANGE, TEMPERATURE_CHANGE]
    assert dialect.describe() == "max_completion_tokens, no temperature, minimal reasoning"


# === The request the transport actually sends ================================


def _handler(*responses: httpx.Response):
    """A transport replying with `responses` in order — the last one repeats —
    and the JSON body of every request it was given."""
    bodies: list[dict] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return handle, bodies


def _ok(text: str = "1\nHola") -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}]})


def _all_reasoning() -> httpx.Response:
    """200 with the whole budget spent thinking — what gpt-5-mini returns."""
    return httpx.Response(200, json={
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}]})


def _call(cfg, handle, max_tokens: int = 300, provider=None) -> str:
    async def go() -> str:
        async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle)) as client:
            return await cc.call_chat_api(
                client, "sys", "user", cfg, max_tokens, provider)

    reply: str = run_async(go())
    return reply


def test_an_endpoint_that_accepts_the_defaults_costs_exactly_one_call(cfg) -> None:
    handle, bodies = _handler(_ok())
    assert _call(cfg, handle) == "1\nHola"
    assert len(bodies) == 1
    assert bodies[0]["max_tokens"] == 300
    assert bodies[0]["temperature"] == REQUEST_TEMPERATURE
    assert "max_completion_tokens" not in bodies[0]
    assert dialect_notes(cfg) == []


def test_the_two_openai_400s_are_corrected_within_the_one_call(cfg) -> None:
    warnings: list[str] = []
    cfg.warn = warnings.append
    handle, bodies = _handler(
        httpx.Response(400, text=MAX_TOKENS_400),
        httpx.Response(400, text=TEMPERATURE_400),
        _ok(),
    )

    assert _call(cfg, handle) == "1\nHola"
    assert len(bodies) == 3
    assert "max_tokens" not in bodies[1] and bodies[1]["max_completion_tokens"] == 300
    assert "temperature" in bodies[1]
    assert "temperature" not in bodies[2]
    assert warnings == [
        "Provider requires max_completion_tokens instead of max_tokens; "
        "adjusted for the rest of the run.",
        "Provider requires the default temperature; adjusted for the rest "
        "of the run.",
    ]


def test_a_learned_dialect_is_reused_and_warned_about_once(cfg) -> None:
    warnings: list[str] = []
    cfg.warn = warnings.append
    first, _ = _handler(httpx.Response(400, text=MAX_TOKENS_400), _ok())
    _call(cfg, first)

    second, bodies = _handler(_ok())
    assert _call(cfg, second) == "1\nHola"
    assert len(bodies) == 1
    assert bodies[0]["max_completion_tokens"] == 300
    assert len(warnings) == 1
    assert dialect_notes(cfg) == ["max_completion_tokens, minimal reasoning"]


def test_a_correction_another_batch_already_made_is_still_re_sent(cfg) -> None:
    """With several batches in flight the first 400 corrects the dialect and
    the rest find nothing left to adjust. They must re-send in the shape that
    was learned, not fail the file over a 400 that no longer applies."""
    dialect = cfg.dialect_for(cfg.provider)
    bodies: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            # Stands in for the concurrent batch that got there first.
            dialect.adjust_for(MAX_TOKENS_400)
            return httpx.Response(400, text=MAX_TOKENS_400)
        return _ok()

    assert _call(cfg, handle) == "1\nHola"
    assert len(bodies) == 2
    assert bodies[1]["max_completion_tokens"] == 300


def test_a_400_that_is_not_a_dialect_quirk_still_fails(cfg) -> None:
    handle, bodies = _handler(httpx.Response(400, text=UNRELATED_400))
    with pytest.raises(httpx.HTTPStatusError):
        _call(cfg, handle)
    assert len(bodies) == 1


def test_a_provider_repeating_a_corrected_400_gives_up(cfg) -> None:
    """One corrective round trip, then the 400 is what it always was: fatal."""
    handle, bodies = _handler(httpx.Response(400, text=MAX_TOKENS_400))
    with pytest.raises(httpx.HTTPStatusError):
        _call(cfg, handle)
    assert len(bodies) == 2


def test_no_temperature_skips_the_corrective_round_trip(cfg) -> None:
    cfg.send_temperature = False
    handle, bodies = _handler(_ok())
    assert _call(cfg, handle) == "1\nHola"
    assert "temperature" not in bodies[0]
    assert dialect_notes(cfg) == ["no temperature"]


# === Reasoning models that spend the whole budget thinking ===================


def test_a_reply_that_was_all_reasoning_is_retried_with_double_the_budget(
    cfg,
) -> None:
    warnings: list[str] = []
    cfg.warn = warnings.append
    handle, bodies = _handler(_all_reasoning(), _ok())

    assert _call(cfg, handle) == "1\nHola"
    assert [b["max_tokens"] for b in bodies] == [300, 600]
    assert "reasoning" in warnings[0]


def test_the_budget_stops_doubling_at_the_multiplier(cfg) -> None:
    handle, bodies = _handler(_all_reasoning())
    with pytest.raises(cc.FatalApiError) as excinfo:
        _call(cfg, handle)

    ceiling = 300 * REASONING_BUDGET_MULTIPLIER
    assert [b["max_tokens"] for b in bodies] == [300, 600, ceiling]
    message = str(excinfo.value)
    assert "reasoning" in message and "--review-model" in message


def test_the_grown_budget_belongs_to_the_request_not_the_provider(cfg) -> None:
    """The next call starts from its own payload's size again."""
    first, _ = _handler(_all_reasoning(), _ok())
    _call(cfg, first)

    second, bodies = _handler(_ok())
    _call(cfg, second, max_tokens=500)
    assert bodies[0]["max_tokens"] == 500


# === One dialect per endpoint ================================================


def test_one_endpoint_serving_both_passes_learns_once(cfg) -> None:
    assert cfg.dialect_for(cfg.provider) is cfg.dialect_for(cfg.review_provider)


def test_a_separate_review_endpoint_learns_on_its_own(cfg) -> None:
    cfg.model = "local-model"
    cfg.review_model = "gpt-5"
    handle, _ = _handler(httpx.Response(400, text=MAX_TOKENS_400), _ok())
    _call(cfg, handle, provider=cfg.review_provider)

    assert cfg.dialect_for(cfg.review_provider).token_param == \
        TOKEN_PARAM_COMPLETION
    assert cfg.dialect_for(cfg.provider).token_param == TOKEN_PARAM_DEFAULT
    assert dialect_notes(cfg) == ["review: max_completion_tokens, minimal reasoning"]


def test_a_reasoning_endpoint_is_asked_to_stop_reasoning() -> None:
    """An endpoint demanding max_completion_tokens is reasoning-era: on gpt-5-mini
    the default spent 448 of 476 tokens thinking, minimal spent none."""
    dialect = ProviderDialect()
    dialect.adjust_for(MAX_TOKENS_400)
    assert dialect.minimal_reasoning is True

    body = cc.request_body("sys", "user", Provider(api_url="u", api_key="k"), dialect, 300)
    assert body["reasoning_effort"] == cc.REASONING_EFFORT_MINIMAL
    assert "max_completion_tokens" in body


def test_an_output_limit_400_also_asks_for_minimal_reasoning() -> None:
    """"Could not finish the message because max_tokens ... was reached" is a
    reasoning model asking for room it will only spend thinking."""
    dialect = ProviderDialect(token_param=TOKEN_PARAM_COMPLETION)
    changes = dialect.adjust_for(
        '{"error":{"message":"Could not finish the message because max_tokens '
        'or model output limit was reached. Please try again with higher max_tokens."}}')
    assert changes == [REASONING_CHANGE]
    assert dialect.minimal_reasoning is True


def test_a_plain_endpoint_is_never_asked_for_reasoning_effort() -> None:
    body = cc.request_body("sys", "user", Provider(api_url="u", api_key="k"),
                        ProviderDialect(), 300)
    assert "reasoning_effort" not in body
    assert "max_tokens" in body
