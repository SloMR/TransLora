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

export function stripBom(s: string): string {
  return s.charCodeAt(0) === 0xfeff ? s.slice(1) : s;
}

export function normalizeNewlines(s: string): string {
  return s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

/**
 * Produce a synthetic SRT timestamp for formats where we can't derive real
 * timings cheaply. One-second windows starting at 00:00:00,000 are unique
 * per block and preserve numeric round-trips.
 */
export function synthSrtTimestamp(index: number): string {
  const startSec = index * 2;
  const endSec = startSec + 1;
  return `${secondsToSrt(startSec)} --> ${secondsToSrt(endSec)}`;
}

function secondsToSrt(total: number): string {
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${pad2(h)}:${pad2(m)}:${pad2(s)},000`;
}
