import { SubtitleBlock } from '../srt-parser';
import {
  SubtitleDocument,
  normalizeNewlines,
  pad2,
  pad3,
  stripBom,
} from './types';

/**
 * .sub is a family of formats. We support MicroDVD (`{startframe}{endframe}text`)
 * because it's by far the most common variant still in circulation. Other
 * dialects (SubViewer, MPL2, etc.) throw a descriptive error so the user
 * knows why the file was rejected.
 */

const MICRO_DVD_RE = /^\{(\d+)\}\{(\d+)\}(.*)$/;
const DEFAULT_FPS = 23.976;

export function parseSub(content: string): SubtitleDocument {
  const text = normalizeNewlines(stripBom(content));
  const rawLines = text.split('\n');
  const contentLines = rawLines.filter((l) => l.trim().length > 0);
  if (contentLines.length === 0) {
    return emptyDoc();
  }

  // Detect MicroDVD by requiring most of the first few lines to match.
  if (!MICRO_DVD_RE.test(contentLines[0])) {
    throw new Error(
      '.sub format not recognized (only MicroDVD {n}{n}text is supported).',
    );
  }

  const blocks: SubtitleBlock[] = [];
  const frameRanges: Array<[string, string]> = [];
  let n = 1;

  for (const line of contentLines) {
    const m = line.match(MICRO_DVD_RE);
    if (!m) continue;
    const [, f1, f2, rawText] = m;
    // MicroDVD uses `|` between lines within a block.
    const displayText = rawText.replace(/\|/g, '\n');
    const timestamp = `${framesToSrt(Number(f1))} --> ${framesToSrt(Number(f2))}`;

    blocks.push({ number: n++, timestamp, text: displayText });
    frameRanges.push([f1, f2]);
  }

  return {
    format: 'sub',
    blocks,
    rebuild(translated) {
      return translated
        .map((b, i) => {
          const [f1, f2] = frameRanges[i] ?? ['0', '0'];
          const joined = b.text.replace(/\n/g, '|');
          return `{${f1}}{${f2}}${joined}`;
        })
        .join('\n') + '\n';
    },
  };
}

function framesToSrt(frame: number): string {
  const totalMs = Math.round((frame / DEFAULT_FPS) * 1000);
  const ms = totalMs % 1000;
  const totalSec = Math.floor(totalMs / 1000);
  const s = totalSec % 60;
  const totalMin = Math.floor(totalSec / 60);
  const m = totalMin % 60;
  const h = Math.floor(totalMin / 60);
  return `${pad2(h)}:${pad2(m)}:${pad2(s)},${pad3(ms)}`;
}

function emptyDoc(): SubtitleDocument {
  return {
    format: 'sub',
    blocks: [],
    rebuild: () => '',
  };
}
