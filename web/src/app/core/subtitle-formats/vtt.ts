import { SubtitleBlock } from '../srt-parser';
import { SubtitleDocument, normalizeNewlines, pad2, stripBom } from './types';

// VTT cue timestamps: HH:MM:SS.mmm or MM:SS.mmm, both sides of "-->".
const CUE_LINE_RE =
  /^\s*(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}(?:\s+.*)?$/;

function vttTsToSrt(ts: string): string {
  // Convert every HH:MM:SS.mmm (or MM:SS.mmm) token to HH:MM:SS,mmm.
  return ts.replace(/(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})|(\d{2}):(\d{2})\.(\d{3})/g,
    (_match, h, m, s, ms, mm, ss, mss) => {
      if (h !== undefined) {
        return `${pad2(Number(h))}:${m}:${s},${ms}`;
      }
      return `00:${mm}:${ss},${mss}`;
    });
}

export function parseVtt(content: string): SubtitleDocument {
  const text = normalizeNewlines(stripBom(content));
  const chunks = text.split(/\n\n+/);

  const blocks: SubtitleBlock[] = [];
  const cueMeta: Array<{ identifier: string | null; originalTs: string }> = [];
  let header = 'WEBVTT';
  let firstChunkIdx = 0;

  if (chunks.length > 0 && /^WEBVTT\b/.test(chunks[0])) {
    header = chunks[0].replace(/\s+$/, '');
    firstChunkIdx = 1;
  }

  let n = 1;
  for (let i = firstChunkIdx; i < chunks.length; i++) {
    const chunk = chunks[i].replace(/\s+$/, '');
    if (!chunk) continue;
    if (/^(NOTE|STYLE|REGION)\b/.test(chunk)) continue;

    const lines = chunk.split('\n');
    let tsLineIdx = 0;
    let identifier: string | null = null;

    if (!CUE_LINE_RE.test(lines[0])) {
      if (lines.length < 2 || !CUE_LINE_RE.test(lines[1])) continue;
      identifier = lines[0];
      tsLineIdx = 1;
    }

    const originalTs = lines[tsLineIdx];
    const srtTs = extractCueTimestamp(originalTs);
    if (!srtTs) continue;
    const cueText = lines.slice(tsLineIdx + 1).join('\n');

    blocks.push({ number: n, timestamp: srtTs, text: cueText });
    cueMeta.push({ identifier, originalTs });
    n++;
  }

  return {
    format: 'vtt',
    blocks,
    rebuild(translated) {
      const parts: string[] = [header];
      translated.forEach((b, i) => {
        const meta = cueMeta[i];
        const ts = meta?.originalTs ?? b.timestamp.replace(/,/g, '.');
        const id = meta?.identifier ? meta.identifier + '\n' : '';
        parts.push(`${id}${ts}\n${b.text}`);
      });
      return parts.join('\n\n') + '\n';
    },
  };
}

function extractCueTimestamp(line: string): string | null {
  // Strip any trailing cue settings like "line:80% align:middle".
  const m = line.match(
    /((?:\d{1,2}:)?\d{2}:\d{2}\.\d{3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}\.\d{3})/,
  );
  if (!m) return null;
  return `${vttTsToSrt(m[1])} --> ${vttTsToSrt(m[2])}`;
}
