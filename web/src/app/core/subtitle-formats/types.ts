import { SubtitleBlock } from '../srt-parser';

export type SubtitleFormat = 'srt' | 'vtt' | 'ass' | 'ssa' | 'sbv' | 'sub';

// Parsed file. `blocks` use normalized SRT shape; `rebuild` serializes back
// to the original format, preserving headers/styles/per-cue metadata.
export interface SubtitleDocument {
  format: SubtitleFormat;
  blocks: SubtitleBlock[];
  rebuild(translated: SubtitleBlock[]): string;
}

export function msToSrt(ms: number): string {
  const total = Math.max(0, Math.floor(ms));
  const pad = (value: number, width: number) => String(value).padStart(width, '0');
  return (
    `${pad(Math.floor(total / 3_600_000), 2)}:` +
    `${pad(Math.floor(total / 60_000) % 60, 2)}:` +
    `${pad(Math.floor(total / 1000) % 60, 2)},${pad(total % 1000, 3)}`
  );
}
