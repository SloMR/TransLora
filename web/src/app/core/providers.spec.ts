import { PROVIDER_KEYS, PROVIDER_PRESETS, priceLabel } from './providers';

describe('provider presets', () => {
  const withModels = PROVIDER_KEYS.filter((k) => PROVIDER_PRESETS[k]!.models.length > 0);

  it('lists every model cheapest first and defaults to the cheapest', () => {
    for (const key of withModels) {
      const { models, defaultModel } = PROVIDER_PRESETS[key]!;
      const costs = models.map((m) => m.input + m.output);
      expect(costs, key).toEqual([...costs].sort((a, b) => a - b));
      expect(defaultModel, key).toBe(models[0]!.id);
    }
  });

  it('never lists a model twice', () => {
    for (const key of withModels) {
      const ids = PROVIDER_PRESETS[key]!.models.map((m) => m.id);
      expect(new Set(ids).size, key).toBe(ids.length);
    }
  });

  it('has no default for the custom provider, whose server picks', () => {
    expect(PROVIDER_PRESETS['custom']!.models).toEqual([]);
    expect(PROVIDER_PRESETS['custom']!.defaultModel).toBe('');
  });

  it('spells a price the way a person reads it', () => {
    expect(priceLabel({ id: 'x', input: 0.2, output: 1.2, note: '' }))
      .toBe('$0.20 in, $1.20 out per 1M tokens');
    // Sub-dime prices keep the digit that tells them apart.
    expect(priceLabel({ id: 'x', input: 0.075, output: 0.3, note: '' }))
      .toBe('$0.075 in, $0.30 out per 1M tokens');
  });
});
