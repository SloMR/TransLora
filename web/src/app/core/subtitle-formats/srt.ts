import { parseSrt, serializeSrt } from '../srt-parser';
import { SubtitleDocument } from './types';

export function parseSrtDocument(content: string): SubtitleDocument {
  return {
    format: 'srt',
    blocks: parseSrt(content),
    rebuild: (translated) => serializeSrt(translated),
  };
}
