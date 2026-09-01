import subsrt from 'subsrt-ts';
import type {
  Caption,
  ContentCaption,
} from 'subsrt-ts/dist/types/handler.js';

import { SubtitleBlock, normalizeCueText, parseSrt, serializeSrt } from '../srt-parser';
import { parseAss } from './ass';
import { parseVtt } from './vtt';
import { SubtitleDocument, SubtitleFormat, msToSrt } from './types';

export * from './types';

// Formats still handled by subsrt-ts; SRT, VTT and ASS/SSA are parsed here
// because subsrt-ts loses cue text or per-cue metadata on round-trip.
const SUBSRT_FORMATS: Record<string, string> = {
  sub: 'sub',
  sbv: 'sbv',
};

// All formats normalize to SRT-shape blocks; rebuild restores the original file.
export function parseSubtitle(fileName: string, content: string): SubtitleDocument {
  const ext = fileExt(fileName);
  if (ext === 'srt') return parseSrtDocument(content);
  if (ext === 'vtt') return parseVtt(content);
  if (ext === 'ass' || ext === 'ssa') return parseAss(content, ext);

  const format = SUBSRT_FORMATS[ext];
  if (!format) {
    throw new Error(`Unsupported subtitle format: .${ext || fileName}`);
  }
  return parseViaSubsrt(content, ext as SubtitleFormat, format);
}

function parseSrtDocument(content: string): SubtitleDocument {
  // Renumbered 1..n: the pipeline addresses blocks by position.
  const parsed = parseSrt(content).map((b, i) => ({ ...b, number: i + 1 }));
  const rebuild = (translated: SubtitleBlock[]): string =>
    serializeSrt(parsed.map((b, i) => (translated[i] ? { ...b, text: translated[i].text } : b)));
  return { format: 'srt', blocks: parsed, rebuild };
}

function parseViaSubsrt(
  content: string,
  ext: SubtitleFormat,
  format: string,
): SubtitleDocument {
  const captions = subsrt.parse(content, { format });

  const contentIndices: number[] = [];
  const blocks: SubtitleBlock[] = [];

  captions.forEach((cap, i) => {
    if (cap.type !== 'caption') return;
    contentIndices.push(i);
    blocks.push({
      number: blocks.length + 1,
      timestamp: `${msToSrt(cap.start)} --> ${msToSrt(cap.end)}`,
      text: normalizeCueText(cap.content ?? cap.text ?? ''),
    });
  });

  const rebuild = (translated: SubtitleBlock[]): string => {
    const updated: Caption[] = captions.map((c) => ({ ...c }) as Caption);
    translated.forEach((block, i) => {
      const ci = contentIndices[i];
      if (ci == null) return;
      const cap = updated[ci] as ContentCaption;
      // subsrt-ts converts only the first newline to MicroDVD's '|'.
      const text = format === 'sub' ? block.text.split(/\r?\n/).join('|') : block.text;
      cap.text = text;
      cap.content = text;
    });
    return subsrt.build(updated, { format });
  };

  return { format: ext, blocks, rebuild };
}

function fileExt(name: string): string {
  const m = name.toLowerCase().match(/\.([a-z0-9]+)$/);
  return m ? m[1] : '';
}
