import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  input,
  model,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LANGUAGES } from '../core/languages';
import { ModelOption, PROVIDER_KEYS, ProviderPreset, priceLabel } from '../core/providers';
import { QUALITY_PRESETS, QUALITY_PRESET_KEYS, QualityPresetKey } from '../run-presets';
import { presetFor } from '../run-settings';

// The language pair, how hard the run should try, and the provider it will be
// translated by: preset pills, endpoint, key and model. The advanced popover
// is projected into its header.
@Component({
  selector: 'app-provider-form',
  imports: [FormsModule],
  templateUrl: './provider-form.component.html',
  styleUrl: './provider-form.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProviderFormComponent {
  sourceLang = model.required<string>();
  targetLang = model.required<string>();
  apiUrl = model.required<string>();
  apiKey = model.required<string>();
  modelName = model.required<string>();

  providerType = input.required<string>();
  currentPreset = input.required<ProviderPreset>();
  /** Which quality bundle the knobs currently spell; 'custom' when none. */
  qualityPreset = input.required<QualityPresetKey | 'custom'>();
  apiKeyWarning = input.required<string | null>();
  targetNormsLabel = input.required<string>();

  providerSelected = output<string>();
  qualitySelected = output<QualityPresetKey>();
  languagesSwapped = output<void>();

  languages = LANGUAGES;
  providerOptions = PROVIDER_KEYS.map((key) => ({ key, label: presetFor(key).label }));
  qualityOptions = QUALITY_PRESET_KEYS.map((key) => ({ key, ...QUALITY_PRESETS[key] }));

  /** The picker's value for "type a model id of your own". */
  readonly OTHER_MODEL = '__other__';
  // Set when the person chose "Other", so the text field stays even while it is
  // empty or holds an id that happens to match a listed one.
  private typingOther = signal(false);

  modelChoice = computed(() => {
    if (this.typingOther()) return this.OTHER_MODEL;
    const id = this.modelName();
    return this.currentPreset().models.some((m) => m.id === id) ? id : this.OTHER_MODEL;
  });

  showOtherModel = computed(() => this.modelChoice() === this.OTHER_MODEL);

  constructor() {
    // A new provider brings its own list; start from it, not from "Other".
    effect(() => {
      this.providerType();
      this.typingOther.set(false);
    });
  }

  /** Short enough to survive a narrow select: the id and what it is for. */
  optionLabel(m: ModelOption): string {
    return `${m.id} (${m.note})`;
  }

  /** The chosen row's price, or how to fill the free-text field. */
  modelHint = computed(() => {
    const chosen = this.currentPreset().models.find((m) => m.id === this.modelChoice());
    return chosen
      ? `${priceLabel(chosen)}. Listed cheapest first.`
      : 'Type a model id your account supports.';
  });

  onModelChoice(value: string) {
    if (value === this.OTHER_MODEL) {
      this.typingOther.set(true);
      return;
    }
    this.typingOther.set(false);
    this.modelName.set(value);
  }

  qualitySummary(): string {
    const key = this.qualityPreset();
    return key === 'custom'
      ? 'A mix of your own — set in Advanced. Pick a preset to return to a named bundle.'
      : QUALITY_PRESETS[key].summary;
  }

  onProviderTypeChange(type: string) {
    this.providerSelected.emit(type);
  }

  swapLanguages() {
    this.languagesSwapped.emit();
  }
}
