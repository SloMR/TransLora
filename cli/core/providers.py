"""The provider presets: an endpoint, the models it offers with their list
prices, and the defaults a run takes from it. Mirrors web/src/app/core/
providers.ts entry for entry; tests/test_parity.py holds the two together."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelOption:
    """One model a preset offers, with what it costs so a picker can say so."""
    id: str
    # List price per million tokens, input then output, in US dollars.
    input: float
    output: float
    # One or two words on what the model is for.
    note: str


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    api_url: str
    # Cheapest first; the first entry is the default.
    models: tuple[ModelOption, ...]
    needs_key: bool
    default_concurrency: int

    @property
    def default_model(self) -> str:
        return self.models[0].id if self.models else ""


def _preset(key: str, label: str, api_url: str, models: list[ModelOption],
            needs_key: bool, default_concurrency: int) -> ProviderPreset:
    cheapest_first = tuple(sorted(models, key=lambda m: m.input + m.output))
    return ProviderPreset(key, label, api_url, cheapest_first, needs_key,
                          default_concurrency)


CUSTOM = "custom"

PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    p.key: p for p in (
        _preset(CUSTOM, "Custom / Local", "", [], needs_key=False,
                default_concurrency=1),
        _preset("openai", "OpenAI", "https://api.openai.com/v1/chat/completions", [
            ModelOption("gpt-5.6-luna", 0.2, 1.2, "cheapest"),
            ModelOption("gpt-5.4-mini", 0.75, 4.5, "small"),
            ModelOption("gpt-5.6-sol", 2, 10, "all-rounder"),
            ModelOption("gpt-5.6-terra", 2, 12, "strongest"),
            ModelOption("gpt-5.5", 5, 30, "flagship"),
        ], needs_key=True, default_concurrency=5),
        _preset("groq", "Groq", "https://api.groq.com/openai/v1/chat/completions", [
            ModelOption("openai/gpt-oss-20b", 0.075, 0.3, "cheapest"),
            ModelOption("openai/gpt-oss-120b", 0.15, 0.6, "stronger"),
            ModelOption("qwen/qwen3.8-27b", 0.8, 4, "preview"),
        ], needs_key=True, default_concurrency=3),
        _preset("deepseek", "DeepSeek", "https://api.deepseek.com/v1/chat/completions", [
            ModelOption("deepseek-v4-flash", 0.08, 0.16, "cheapest"),
            ModelOption("deepseek-v4-pro", 1.03, 2.05, "stronger"),
        ], needs_key=True, default_concurrency=5),
        _preset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1/chat/completions", [
            ModelOption("qwen/qwen3.7-flash", 0.03, 0.13, "cheapest"),
            ModelOption("deepseek/deepseek-v4-flash", 0.08, 0.16, "cheap"),
            ModelOption("openai/gpt-5.6-luna", 0.2, 1.2, "OpenAI"),
            ModelOption("google/gemini-3.8-flash", 0.75, 3.75, "Google"),
            ModelOption("anthropic/claude-haiku-4.5", 1, 5, "Anthropic, small"),
            ModelOption("anthropic/claude-sonnet-5", 2, 10, "Anthropic"),
            ModelOption("anthropic/claude-opus-5", 5, 25, "Anthropic, top"),
        ], needs_key=True, default_concurrency=5),
    )
}

# The ones a flag can name: a custom endpoint is spelled out with --api-url.
PROVIDER_CHOICES: tuple[str, ...] = tuple(k for k in PROVIDER_PRESETS if k != CUSTOM)


def price_label(model: ModelOption) -> str:
    """"$0.20 in, $1.20 out per 1M tokens", for a picker row."""
    def money(n: float) -> str:
        return f"${n:.3f}" if n < 0.1 else f"${n:.2f}"
    return f"{money(model.input)} in, {money(model.output)} out per 1M tokens"
