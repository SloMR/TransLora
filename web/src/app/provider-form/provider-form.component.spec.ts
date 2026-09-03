import { ComponentFixture, TestBed } from '@angular/core/testing';

import { PROVIDER_PRESETS } from '../core/providers';
import { ProviderFormComponent } from './provider-form.component';

describe('ProviderFormComponent', () => {
  let fixture: ComponentFixture<ProviderFormComponent>;
  let form: ProviderFormComponent;
  let el: HTMLElement;

  async function mount(providerType: string) {
    await TestBed.configureTestingModule({ imports: [ProviderFormComponent] }).compileComponents();
    fixture = TestBed.createComponent(ProviderFormComponent);
    form = fixture.componentInstance;
    const preset = PROVIDER_PRESETS[providerType]!;
    fixture.componentRef.setInput('sourceLang', '');
    fixture.componentRef.setInput('targetLang', 'Arabic');
    fixture.componentRef.setInput('apiUrl', preset.apiUrl);
    fixture.componentRef.setInput('apiKey', '');
    fixture.componentRef.setInput('modelName', preset.defaultModel);
    fixture.componentRef.setInput('providerType', providerType);
    fixture.componentRef.setInput('currentPreset', preset);
    fixture.componentRef.setInput('qualityPreset', 'best');
    fixture.componentRef.setInput('apiKeyWarning', null);
    fixture.componentRef.setInput('targetNormsLabel', '');
    fixture.detectChanges();
    el = fixture.nativeElement;
  }

  const select = () => el.querySelector('#model-name') as HTMLSelectElement;
  const options = () => Array.from(select().options).map((o) => o.value);

  it('offers the preset models cheapest first, the cheapest chosen, plus a way to type another', async () => {
    await mount('openai');
    const ids = PROVIDER_PRESETS['openai']!.models.map((m) => m.id);
    expect(options()).toEqual([...ids, form.OTHER_MODEL]);
    expect(select().value).toBe('gpt-5.6-luna');
    // A row names the model and its role; the chosen row's price sits underneath.
    expect(select().options[0]!.textContent).toBe('gpt-5.6-luna (cheapest)');
    expect(select().closest('.field')!.querySelector('.field-hint')!.textContent)
      .toContain('$0.20 in, $1.20 out per 1M tokens');
    expect(el.querySelector('#model-name-other')).toBeNull();
  });

  it('sets the model when a row is chosen', async () => {
    await mount('openai');
    select().value = 'gpt-5.6-sol';
    select().dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(form.modelName()).toBe('gpt-5.6-sol');
  });

  it('reveals a text field for another model id and keeps it while typing', async () => {
    await mount('groq');
    select().value = form.OTHER_MODEL;
    select().dispatchEvent(new Event('change'));
    fixture.detectChanges();
    const other = el.querySelector('#model-name-other') as HTMLInputElement;
    expect(other).not.toBeNull();
    expect(form.modelHint()).toBe('Type a model id your account supports.');
    // Choosing "another" keeps whatever was set until something else is typed.
    expect(form.modelName()).toBe('openai/gpt-oss-20b');

    other.value = 'openai/gpt-oss-120b';
    other.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(form.modelName()).toBe('openai/gpt-oss-120b');
    // Even though that id is on the list, the field stays: the person asked to type.
    expect(el.querySelector('#model-name-other')).not.toBeNull();
  });

  it('shows a stored id that is not on the list as "another"', async () => {
    await mount('deepseek');
    fixture.componentRef.setInput('modelName', 'deepseek-v3.2');
    fixture.detectChanges();
    // ngModel writes the select's value on a microtask.
    await fixture.whenStable();
    fixture.detectChanges();
    expect(form.modelChoice()).toBe(form.OTHER_MODEL);
    expect(select().value).toBe(form.OTHER_MODEL);
    expect((el.querySelector('#model-name-other') as HTMLInputElement).value).toBe('deepseek-v3.2');
  });

  it('gives the custom provider a plain endpoint field instead of a model picker', async () => {
    await mount('custom');
    expect(el.querySelector('#model-name')).toBeNull();
    expect(el.querySelector('#api-url-main')).not.toBeNull();
  });
});
