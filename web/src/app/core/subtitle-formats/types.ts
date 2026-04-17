import { SubtitleBlock } from '../srt-parser';

export type SubtitleFormat = 'srt' | 'vtt' | 'ass' | 'ssa' | 'sbv' | 'sub';

/**
 * A file that has been parsed from one of the supported subtitle formats.
 *
 * `blocks` are always normalized into SRT-style shape — sequential numbers
 * starting at 1 and `HH:MM:SS,mmm --> HH:MM:SS,mmm` timestamps — so the LLM
 * always sees the same structure regardless of the source format. The
 * original on-disk structure (headers, styles, per-cue metadata) is kept
 * inside `rebuild`, which serializes the translated blocks back into the
 * original format.
 */
export interface SubtitleDocument {
  format: SubtitleFormat;
  blocks: SubtitleBlock[];
  rebuild(translated: SubtitleBlock[]): string;
}

export type { SubtitleBlock };

export function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

export function pad3(n: number): string {
  return String(n).padStart(3, '0');
}
