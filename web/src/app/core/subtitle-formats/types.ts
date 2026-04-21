import { SubtitleBlock } from '../srt-parser';

export type SubtitleFormat = 'srt' | 'vtt' | 'ass' | 'ssa' | 'sbv' | 'sub';

// Parsed file. `blocks` use normalized SRT shape; `rebuild` serializes back
// to the original format, preserving headers/styles/per-cue metadata.
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
