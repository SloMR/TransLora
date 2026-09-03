import { Signal, WritableSignal, computed, signal } from '@angular/core';
import { ThemePreference } from './run-settings';

/** Resolves the stored theme preference against the OS setting and stamps the
 * result on the document. Owned by the shell; split out so the shell's own
 * file stays about wiring. */
export class ThemeController {
  private systemPrefersDark = signal(false);
  private media: MediaQueryList | null = null;

  readonly theme: Signal<'light' | 'dark'>;

  constructor(private readonly preference: WritableSignal<ThemePreference>) {
    this.theme = computed(() => {
      const pref = this.preference();
      if (pref !== 'system') return pref;
      return this.systemPrefersDark() ? 'dark' : 'light';
    });

    if (typeof window !== 'undefined') {
      this.media = window.matchMedia?.('(prefers-color-scheme: dark)') ?? null;
      if (this.media) {
        this.systemPrefersDark.set(this.media.matches);
        this.media.addEventListener('change', this.onSystemChange);
      }
    }
    this.apply();
  }

  toggleLabel = computed(() =>
    this.theme() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');

  toggle() {
    this.preference.set(this.theme() === 'dark' ? 'light' : 'dark');
  }

  /** Call from an effect so the attribute follows the resolved theme. */
  apply() {
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', this.theme());
    }
  }

  destroy() {
    this.media?.removeEventListener('change', this.onSystemChange);
  }

  private onSystemChange = (event: MediaQueryListEvent) => {
    this.systemPrefersDark.set(event.matches);
  };
}
