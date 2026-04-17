import { SubtitleDocument } from './types';
import { parseSrtDocument } from './srt';
import { parseVtt } from './vtt';
import { parseAss } from './ass';
import { parseSbv } from './sbv';
import { parseSub } from './sub';

export * from './types';

/** Dispatch by filename extension to the right format parser. */
export function parseSubtitle(fileName: string, content: string): SubtitleDocument {
  const ext = fileExt(fileName);
  switch (ext) {
    case 'srt':
      return parseSrtDocument(content);
    case 'vtt':
      return parseVtt(content);
    case 'ass':
      return parseAss(content, 'ass');
    case 'ssa':
      return parseAss(content, 'ssa');
    case 'sbv':
      return parseSbv(content);
    case 'sub':
      return parseSub(content);
    default:
      throw new Error(`Unsupported subtitle format: .${ext || fileName}`);
  }
}

function fileExt(name: string): string {
  const m = name.toLowerCase().match(/\.([a-z0-9]+)$/);
  return m ? m[1] : '';
}
