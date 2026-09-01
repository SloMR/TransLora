// Hand-rolled because subsrt-ts's SSA handler drops the first character of every
// dialogue line and rebuilds [Script Info] and the style table from scratch.

import { SubtitleBlock, normalizeCueText } from '../srt-parser';
import { SubtitleDocument, SubtitleFormat, msToSrt } from './types';

// v4.00 (SSA) and v4.00+ (ASS) both end on Text; used when Format: is missing.
const DEFAULT_FIELDS = [
  'Layer', 'Start', 'End', 'Style', 'Name',
  'MarginL', 'MarginR', 'MarginV', 'Effect', 'Text',
];

interface DialogueLine {
  lineIndex: number;
  prefix: string; // everything up to and including the comma before Text
}

export function parseAss(content: string, ext: SubtitleFormat): SubtitleDocument {
  const lines = content.split(/\r\n|\r|\n/);

  let inEvents = false;
  let fields = DEFAULT_FIELDS;
  const dialogues: DialogueLine[] = [];
  const blocks: SubtitleBlock[] = [];

  lines.forEach((line, i) => {
    const trimmed = line.trim();
    if (trimmed.startsWith('[')) {
      inEvents = /^\[events\]$/i.test(trimmed);
      return;
    }
    if (!inEvents) return;
    const colon = line.indexOf(':');
    if (colon < 0) return;
    const key = line.slice(0, colon).trim().toLowerCase();
    if (key === 'format') {
      fields = line.slice(colon + 1).split(',').map((f) => f.trim());
      return;
    }
    // Comment events are preserved verbatim but never translated.
    if (key !== 'dialogue') return;

    const [prefix, raw] = splitEventLine(line, colon, fields.length);
    dialogues.push({ lineIndex: i, prefix });
    const values = prefix.slice(colon + 1).split(',');
    blocks.push({
      number: blocks.length + 1,
      timestamp: `${msToSrt(fieldTime(fields, values, 'Start'))} --> ${msToSrt(fieldTime(fields, values, 'End'))}`,
      text: normalizeCueText(raw.split('\\N').join('\n')),
    });
  });

  const rebuild = (translated: SubtitleBlock[]): string => {
    const out = [...lines];
    dialogues.forEach((d, i) => {
      const block = translated[i];
      if (!block) return;
      out[d.lineIndex] = d.prefix + block.text.split('\n').join('\\N');
    });
    return out.join('\n');
  };

  return { format: ext, blocks, rebuild };
}

// Text is the last field and may contain commas: everything after fieldCount-1 of them.
function splitEventLine(
  line: string, colon: number, fieldCount: number,
): [string, string] {
  let cut = colon + 1;
  for (let n = 0; n < fieldCount - 1; n++) {
    const next = line.indexOf(',', cut);
    if (next < 0) return [line, ''];
    cut = next + 1;
  }
  return [line.slice(0, cut), line.slice(cut)];
}

function fieldTime(fields: string[], values: string[], name: string): number {
  const idx = fields.findIndex((f) => f.toLowerCase() === name.toLowerCase());
  return idx >= 0 ? assTimeToMs(values[idx] ?? '') : 0;
}

// ASS times are H:MM:SS.cc (centiseconds).
function assTimeToMs(value: string): number {
  const m = /^(\d+):(\d{1,2}):(\d{1,2})[.,](\d{1,3})$/.exec(value.trim());
  if (!m) return 0;
  const frac = m[4].padEnd(3, '0').slice(0, 3);
  return (
    Number(m[1]) * 3_600_000 + Number(m[2]) * 60_000 + Number(m[3]) * 1000 +
    Number(frac)
  );
}
