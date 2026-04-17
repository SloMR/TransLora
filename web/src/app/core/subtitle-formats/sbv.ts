import { SubtitleBlock } from '../srt-parser';
import { SubtitleDocument, normalizeNewlines, pad2, stripBom } from './types';

/** YouTube SBV: `H:MM:SS.mmm,H:MM:SS.mmm\ntext\n\n`, no block numbers. */

const SBV_LINE_RE =
  /^(\d+):(\d{2}):(\d{2})\.(\d{3})\s*,\s*(\d+):(\d{2}):(\d{2})\.(\d{3})$/;

export function parseSbv(content: string): SubtitleDocument {
  const text = normalizeNewlines(stripBom(content)).trim();
  const chunks = text.split(/\n\n+/);

  const blocks: SubtitleBlock[] = [];
  const originalTsLines: string[] = [];
  let n = 1;

  for (const chunk of chunks) {
    const lines = chunk.split('\n');
    const m = lines[0].trim().match(SBV_LINE_RE);
    if (!m) continue;

    const start = `${pad2(Number(m[1]))}:${m[2]}:${m[3]},${m[4]}`;
    const end = `${pad2(Number(m[5]))}:${m[6]}:${m[7]},${m[8]}`;
    const cueText = lines.slice(1).join('\n');

    blocks.push({
      number: n++,
      timestamp: `${start} --> ${end}`,
      text: cueText,
    });
    originalTsLines.push(lines[0].trim());
  }

  return {
    format: 'sbv',
    blocks,
    rebuild(translated) {
      return translated
        .map((b, i) => `${originalTsLines[i] ?? srtToSbv(b.timestamp)}\n${b.text}`)
        .join('\n\n') + '\n';
    },
  };
}

function srtToSbv(srt: string): string {
  const m = srt.match(
    /(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})/,
  );
  if (!m) return srt;
  const stripLeadingZeroHour = (h: string) => (h === '00' ? '0' : String(Number(h)));
  return `${stripLeadingZeroHour(m[1])}:${m[2]}:${m[3]}.${m[4]},${stripLeadingZeroHour(m[5])}:${m[6]}:${m[7]}.${m[8]}`;
}
