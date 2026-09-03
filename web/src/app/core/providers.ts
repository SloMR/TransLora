/** One model a preset offers, with what it costs so the picker can say so. */
export interface ModelOption {
  id: string;
  /** List price per million tokens, input then output, in US dollars. */
  input: number;
  output: number;
  /** One or two words on what the model is for; it has to fit a narrow select. */
  note: string;
}

export interface ProviderPreset {
  label: string;
  apiUrl: string;
  /** Cheapest first; the first entry is the default. */
  models: ModelOption[];
  defaultModel: string;
  needsKey: boolean;
  defaultConcurrency: number;
}

// Sorted by what a million tokens costs in and out together, so the picker
// reads from cheapest to dearest and the default is always the first row.
function preset(p: Omit<ProviderPreset, 'defaultModel'>): ProviderPreset {
  const models = [...p.models].sort((a, b) => (a.input + a.output) - (b.input + b.output));
  return { ...p, models, defaultModel: models[0]?.id ?? '' };
}

// Model ids checked against each provider's own catalogue on 2026-09-03; the
// prices are the list prices OpenRouter passes through.
export const PROVIDER_PRESETS: Record<string, ProviderPreset> = {
  custom: preset({
    label: 'Custom / Local',
    apiUrl: '',
    models: [],
    needsKey: false,
    defaultConcurrency: 1,
  }),
  openai: preset({
    label: 'OpenAI',
    apiUrl: 'https://api.openai.com/v1/chat/completions',
    models: [
      { id: 'gpt-5.6-luna', input: 0.2, output: 1.2, note: 'cheapest' },
      { id: 'gpt-5.4-mini', input: 0.75, output: 4.5, note: 'small' },
      { id: 'gpt-5.6-sol', input: 2, output: 10, note: 'all-rounder' },
      { id: 'gpt-5.6-terra', input: 2, output: 12, note: 'strongest' },
      { id: 'gpt-5.5', input: 5, output: 30, note: 'flagship' },
    ],
    needsKey: true,
    defaultConcurrency: 5,
  }),
  groq: preset({
    label: 'Groq',
    apiUrl: 'https://api.groq.com/openai/v1/chat/completions',
    // Groq's own production list; the Llama models moved to enterprise-only.
    models: [
      { id: 'openai/gpt-oss-20b', input: 0.075, output: 0.3, note: 'cheapest' },
      { id: 'openai/gpt-oss-120b', input: 0.15, output: 0.6, note: 'stronger' },
      { id: 'qwen/qwen3.8-27b', input: 0.8, output: 4, note: 'preview' },
    ],
    needsKey: true,
    defaultConcurrency: 3,
  }),
  deepseek: preset({
    label: 'DeepSeek',
    apiUrl: 'https://api.deepseek.com/v1/chat/completions',
    models: [
      { id: 'deepseek-v4-flash', input: 0.08, output: 0.16, note: 'cheapest' },
      { id: 'deepseek-v4-pro', input: 1.03, output: 2.05, note: 'stronger' },
    ],
    needsKey: true,
    defaultConcurrency: 5,
  }),
  openrouter: preset({
    label: 'OpenRouter',
    apiUrl: 'https://openrouter.ai/api/v1/chat/completions',
    models: [
      { id: 'qwen/qwen3.7-flash', input: 0.03, output: 0.13, note: 'cheapest' },
      { id: 'deepseek/deepseek-v4-flash', input: 0.08, output: 0.16, note: 'cheap' },
      { id: 'openai/gpt-5.6-luna', input: 0.2, output: 1.2, note: 'OpenAI' },
      { id: 'google/gemini-3.8-flash', input: 0.75, output: 3.75, note: 'Google' },
      { id: 'anthropic/claude-haiku-4.5', input: 1, output: 5, note: 'Anthropic, small' },
      { id: 'anthropic/claude-sonnet-5', input: 2, output: 10, note: 'Anthropic' },
      { id: 'anthropic/claude-opus-5', input: 5, output: 25, note: 'Anthropic, top' },
    ],
    needsKey: true,
    defaultConcurrency: 5,
  }),
};

export const PROVIDER_KEYS = Object.keys(PROVIDER_PRESETS);

/** "$0.20 in, $1.20 out per 1M tokens", for a picker row. */
export function priceLabel(model: ModelOption): string {
  const money = (n: number) => `$${n < 0.1 ? n.toFixed(3) : n.toFixed(2)}`;
  return `${money(model.input)} in, ${money(model.output)} out per 1M tokens`;
}
