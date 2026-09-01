import {
  ChangeDetectionStrategy,
  Component,
  input,
  model,
  output,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LANGUAGES } from '../core/languages';
import { PROVIDER_KEYS, ProviderPreset } from '../core/providers';
import { presetFor } from '../run-settings';

// The language pair and the provider it will be translated by: preset pills,
// endpoint, key and model. The advanced popover is projected into its header.
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
  apiKeyWarning = input.required<string | null>();
  targetNormsLabel = input.required<string>();

  providerSelected = output<string>();
  languagesSwapped = output<void>();

  languages = LANGUAGES;
  providerOptions = PROVIDER_KEYS.map((key) => ({ key, label: presetFor(key).label }));

  onProviderTypeChange(type: string) {
    this.providerSelected.emit(type);
  }

  swapLanguages() {
    this.languagesSwapped.emit();
  }
}
