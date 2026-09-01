// Hand-rolled because subsrt-ts drops cue identifiers and settings on parse and
// renumbers cues from the caption array index on build.

import { SubtitleBlock, normalizeCueText } from '../srt-parser';
import { SubtitleDocument, msToSrt } from './types';

interface VttCue {
  textStart: number; // first line of the cue payload
  textEnd: number; // one past the last line of the payload
}

export function parseVtt(content: string): SubtitleDocument {
  const lines = content.split(/\r\n|\r|\n/);
  const cues: VttCue[] = [];
  const blocks: SubtitleBlock[] = [];

  for (let i = 0; i < lines.length; i++) {
    const arrow = lines[i].indexOf('-->');
    if (arrow < 0) continue;
    const start = lines[i].slice(0, arrow).trim();
    const end = lines[i].slice(arrow + 3).trim().split(/\s+/)[0] ?? '';
    let j = i + 1;
    while (j < lines.length && lines[j].trim() !== '') j++;
    cues.push({ textStart: i + 1, textEnd: j });
    blocks.push({
      number: blocks.length + 1,
      timestamp: `${msToSrt(vttTimeToMs(start))} --> ${msToSrt(vttTimeToMs(end))}`,
      text: normalizeCueText(lines.slice(i + 1, j).join('\n')),
    });
    i = j;
  }

  const rebuild = (translated: SubtitleBlock[]): string => {
    const out: string[] = [];
    let cursor = 0;
    cues.forEach((cue, i) => {
      out.push(...lines.slice(cursor, cue.textStart));
      const text = translated[i]
        ? translated[i].text
        : lines.slice(cue.textStart, cue.textEnd).join('\n');
      if (cue.textEnd > cue.textStart || text) out.push(...text.split('\n'));
      cursor = cue.textEnd;
    });
    out.push(...lines.slice(cursor));
    return out.join('\n');
  };

  return { format: 'vtt', blocks, rebuild };
}

// WebVTT times are [HH:]MM:SS.mmm.
function vttTimeToMs(value: string): number {
  const m = /^(?:(\d+):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})$/.exec(value.trim());
  if (!m) return 0;
  const frac = m[4].padEnd(3, '0').slice(0, 3);
  return (
    Number(m[1] ?? 0) * 3_600_000 + Number(m[2]) * 60_000 +
    Number(m[3]) * 1000 + Number(frac)
  );
}
