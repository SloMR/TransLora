export interface ProviderPreset {
  label: string;
  apiUrl: string;
  models: string[];
  defaultModel: string;
  needsKey: boolean;
  defaultConcurrency: number;
}

export const PROVIDER_PRESETS: Record<string, ProviderPreset> = {
  custom: {
    label: 'Custom / Local',
    apiUrl: '',
    models: [],
    defaultModel: '',
    needsKey: false,
    defaultConcurrency: 1,
  },
  openai: {
    label: 'OpenAI',
    apiUrl: 'https://api.openai.com/v1/chat/completions',
    models: [
      'gpt-4.1',
      'gpt-4.1-mini',
      'gpt-4.1-nano',
      'gpt-4o',
      'gpt-4o-mini',
    ],
    defaultModel: 'gpt-4.1-mini',
    needsKey: true,
    defaultConcurrency: 5,
  },
  groq: {
    label: 'Groq',
    apiUrl: 'https://api.groq.com/openai/v1/chat/completions',
    models: [
      'llama-3.3-70b-versatile',
      'llama-3.1-8b-instant',
      'gemma2-9b-it',
    ],
    defaultModel: 'llama-3.3-70b-versatile',
    needsKey: true,
    defaultConcurrency: 3,
  },
  deepseek: {
    label: 'DeepSeek',
    apiUrl: 'https://api.deepseek.com/v1/chat/completions',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    defaultModel: 'deepseek-chat',
    needsKey: true,
    defaultConcurrency: 5,
  },
  openrouter: {
    label: 'OpenRouter',
    apiUrl: 'https://openrouter.ai/api/v1/chat/completions',
    models: [
      'anthropic/claude-sonnet-4',
      'anthropic/claude-haiku-4',
      'meta-llama/llama-3.3-70b-instruct',
    ],
    defaultModel: 'anthropic/claude-sonnet-4',
    needsKey: true,
    defaultConcurrency: 5,
  },
};

export const PROVIDER_KEYS = Object.keys(PROVIDER_PRESETS);
