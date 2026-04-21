import subsrt from 'subsrt-ts';
import type {
  Caption,
  ContentCaption,
} from 'subsrt-ts/dist/types/handler.js';

import { SubtitleBlock } from '../srt-parser';
import { SubtitleDocument, SubtitleFormat, pad2, pad3 } from './types';

export * from './types';

const EXT_TO_FORMAT: Record<string, string> = {
  srt: 'srt',
  vtt: 'vtt',
  ass: 'ass',
  ssa: 'ssa',
  sub: 'sub',
  sbv: 'sbv',
};

// Parses via subsrt-ts. All formats normalize to SRT-shape blocks; rebuild
// round-trips back through subsrt-ts into the original on-disk format.
export function parseSubtitle(fileName: string, content: string): SubtitleDocument {
  const ext = fileExt(fileName);
  const format = EXT_TO_FORMAT[ext];
  if (!format) {
    throw new Error(`Unsupported subtitle format: .${ext || fileName}`);
  }

  const captions = subsrt.parse(content, { format });

  const contentIndices: number[] = [];
  const blocks: SubtitleBlock[] = [];

  captions.forEach((cap, i) => {
    if (cap.type !== 'caption') return;
    contentIndices.push(i);
    blocks.push({
      number: blocks.length + 1,
      timestamp: `${msToSrt(cap.start)} --> ${msToSrt(cap.end)}`,
      text: cap.content ?? cap.text ?? '',
    });
  });

  const rebuild = (translated: SubtitleBlock[]): string => {
    const updated: Caption[] = captions.map((c) => ({ ...c }) as Caption);
    translated.forEach((block, i) => {
      const ci = contentIndices[i];
      if (ci == null) return;
      const cap = updated[ci] as ContentCaption;
      cap.text = block.text;
      cap.content = block.text;
    });
    return subsrt.build(updated, { format });
  };

  return { format: ext as SubtitleFormat, blocks, rebuild };
}

function msToSrt(ms: number): string {
  const total = Math.max(0, Math.floor(ms));
  const h = Math.floor(total / 3_600_000);
  const m = Math.floor(total / 60_000) % 60;
  const s = Math.floor(total / 1000) % 60;
  const mmm = total % 1000;
  return `${pad2(h)}:${pad2(m)}:${pad2(s)},${pad3(mmm)}`;
}

function fileExt(name: string): string {
  const m = name.toLowerCase().match(/\.([a-z0-9]+)$/);
  return m ? m[1] : '';
}
