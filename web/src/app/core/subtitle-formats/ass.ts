import { SubtitleBlock } from '../srt-parser';
import {
  SubtitleDocument,
  SubtitleFormat,
  normalizeNewlines,
  pad2,
  stripBom,
} from './types';

/**
 * ASS / SSA parser.
 *
 * We preserve the entire file as an ordered list of records. Most records are
 * kept verbatim (script-info, styles, comments, blank lines). `Dialogue:` lines
 * are split so only the Text field becomes a translatable block; the rest of
 * the line (layer, timings, style, margins) is stored as a prefix and glued
 * back together during rebuild.
 */

type LineRecord =
  | { kind: 'literal'; text: string }
  | { kind: 'dialogue'; prefix: string; blockIndex: number };

export function parseAss(content: string, format: SubtitleFormat = 'ass'): SubtitleDocument {
  const text = normalizeNewlines(stripBom(content));
  const lines = text.split('\n');

  const records: LineRecord[] = [];
  const blocks: SubtitleBlock[] = [];
  const eol = detectEol(content);

  let inEvents = false;
  let textColIdx = -1;
  let n = 1;

  for (const line of lines) {
    const trimmed = line.trim();

    // Section header toggles the "we're in [Events]" flag.
    if (/^\[.+\]\s*$/.test(trimmed)) {
      inEvents = /^\[events\]\s*$/i.test(trimmed);
      records.push({ kind: 'literal', text: line });
      continue;
    }

    if (inEvents && /^format\s*:/i.test(trimmed)) {
      const payload = trimmed.slice(trimmed.indexOf(':') + 1);
      const cols = payload.split(',').map((c) => c.trim().toLowerCase());
      textColIdx = cols.indexOf('text');
      records.push({ kind: 'literal', text: line });
      continue;
    }

    if (inEvents && /^dialogue\s*:/i.test(trimmed) && textColIdx > 0) {
      const split = splitDialogue(line, textColIdx);
      if (split) {
        const timestamp = extractAssTimestamp(split.fields);
        const blockIndex = blocks.length;
        blocks.push({ number: n++, timestamp, text: split.text });
        records.push({ kind: 'dialogue', prefix: split.prefix, blockIndex });
        continue;
      }
    }

    records.push({ kind: 'literal', text: line });
  }

  return {
    format,
    blocks,
    rebuild(translated) {
      return records
        .map((r) => {
          if (r.kind === 'literal') return r.text;
          const t = translated[r.blockIndex];
          return r.prefix + (t ? t.text : '');
        })
        .join(eol);
    },
  };
}

function detectEol(raw: string): string {
  return raw.includes('\r\n') ? '\r\n' : '\n';
}

/**
 * Split a Dialogue line into its fixed prefix (everything up to and including
 * the comma right before the Text column) and the text itself.
 *
 * `textColIdx` is 0-based against the Format line's column list, which maps
 * directly to the number of commas we need to skip over inside the payload.
 */
function splitDialogue(
  line: string,
  textColIdx: number,
): { prefix: string; fields: string[]; text: string } | null {
  const head = line.match(/^(\s*Dialogue\s*:\s*)/i);
  if (!head) return null;

  const headEnd = head[0].length;
  const payload = line.slice(headEnd);

  let commas = 0;
  let i = 0;
  for (; i < payload.length && commas < textColIdx; i++) {
    if (payload[i] === ',') commas++;
  }
  if (commas < textColIdx) return null;

  const prefixPayload = payload.slice(0, i);
  const text = payload.slice(i);
  const fields = prefixPayload.replace(/,\s*$/, '').split(',');

  return {
    prefix: line.slice(0, headEnd) + prefixPayload,
    fields,
    text,
  };
}

/** Derive a synthetic SRT timestamp from the Dialogue's Start/End fields. */
function extractAssTimestamp(fields: string[]): string {
  // Standard ASS Format: Layer, Start, End, Style, Name, MarginL, MarginR,
  // MarginV, Effect, Text. Start=fields[1], End=fields[2].
  const start = assTsToSrt(fields[1]);
  const end = assTsToSrt(fields[2]);
  return `${start} --> ${end}`;
}

function assTsToSrt(ts: string | undefined): string {
  if (!ts) return '00:00:00,000';
  const m = ts.trim().match(/^(\d+):(\d{2}):(\d{2})[.,](\d{1,3})$/);
  if (!m) return '00:00:00,000';
  const [, h, mi, s, frac] = m;
  // ASS uses centiseconds (2 digits). Pad to milliseconds.
  const ms = frac.padEnd(3, '0').slice(0, 3);
  return `${pad2(Number(h))}:${mi}:${s},${ms}`;
}
