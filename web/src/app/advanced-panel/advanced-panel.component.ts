import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  inject,
  input,
  model,
  output,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { FORMALITY_CHOICES } from '../core/constants';
import { ProviderPreset } from '../core/providers';
import { type Formality } from '../core/translation.service';
import { DIALECT_MAX_LEN, NumericSetting } from '../run-settings';

// In plain words: "register" and "formal" meant nothing to a first user.
const FORMALITY_LABELS: Record<string, string> = {
  auto: 'Same tone as the original',
  formal: 'Polite throughout',
  informal: 'Casual throughout',
};
const FORMALITY_OPTIONS = FORMALITY_CHOICES.map((value) => ({
  value,
  label: FORMALITY_LABELS[value] ?? value,
}));

// The advanced disclosure: endpoint overrides, request limits and the quality
// knobs, plus the dismiss behaviour of the popover they live in.
@Component({
  selector: 'app-advanced-panel',
  imports: [FormsModule],
  templateUrl: './advanced-panel.component.html',
  styleUrl: './advanced-panel.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdvancedPanelComponent implements OnDestroy {
  showAdvanced = model.required<boolean>();

  apiUrl = model.required<string>();
  apiKey = model.required<string>();
  modelName = model.required<string>();
  formality = model.required<Formality>();
  review = model.required<boolean>();
  refineAttribution = model.required<boolean>();
  reflow = model.required<boolean>();
  fixFlagged = model.required<boolean>();
  verifyAdequacy = model.required<boolean>();
  fullAttribution = model.required<boolean>();
  sendTemperature = model.required<boolean>();
  reviewApiUrl = model.required<string>();
  reviewApiKey = model.required<string>();
  reviewModel = model.required<string>();

  providerType = input.required<string>();
  currentPreset = input.required<ProviderPreset>();
  apiKeyWarning = input.required<string | null>();
  targetLang = input.required<string>();
  scriptDefaultChars = input.required<number>();
  batchSize = input.required<number>();
  concurrency = input.required<number>();
  parallelFiles = input.required<number>();
  maxRetries = input.required<number>();
  scanBudget = input.required<number>();
  contextOverlap = input.required<number>();
  maxLineChars = input.required<number>();
  dialect = input.required<string>();
  reviewProviderLabel = input.required<string>();

  numberChanged = output<{ key: NumericSetting; value: unknown }>();
  dialectChanged = output<string>();
  defaultsReset = output<void>();

  formalityOptions = FORMALITY_OPTIONS;
  dialectMaxLength = DIALECT_MAX_LEN;

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private advancedToggle = viewChild<ElementRef<HTMLButtonElement>>('advancedToggle');

  private onDocumentPointerDown = (event: Event) => {
    if (!this.showAdvanced()) return;
    const menu = this.host.nativeElement;
    // Focus already followed the click, so do not yank it back to the toggle.
    if (menu && !menu.contains(event.target as Node)) this.closeAdvanced(false);
  };

  private onDocumentKeydown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') this.closeAdvanced();
  };

  constructor() {
    if (typeof document !== 'undefined') {
      document.addEventListener('pointerdown', this.onDocumentPointerDown);
      document.addEventListener('keydown', this.onDocumentKeydown);
    }
  }

  ngOnDestroy() {
    if (typeof document !== 'undefined') {
      document.removeEventListener('pointerdown', this.onDocumentPointerDown);
      document.removeEventListener('keydown', this.onDocumentKeydown);
    }
  }

  toggleAdvanced() {
    if (this.showAdvanced()) {
      this.closeAdvanced();
    } else {
      this.showAdvanced.set(true);
    }
  }

  // A disclosure, not a modal: no focus trap, but dismissal returns focus to the toggle.
  closeAdvanced(restoreFocus = true) {
    if (!this.showAdvanced()) return;
    this.showAdvanced.set(false);
    if (restoreFocus) this.advancedToggle()?.nativeElement.focus();
  }

  setNumber(key: NumericSetting, value: unknown) {
    this.numberChanged.emit({ key, value });
  }

  setDialect(value: string) {
    this.dialectChanged.emit(value);
  }

  resetDefaults() {
    this.defaultsReset.emit();
  }
}
