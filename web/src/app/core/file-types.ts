import { SubtitleDocument } from './subtitle-formats/types';

export const SUBTITLE_EXTS = ['.srt', '.vtt', '.ass', '.ssa', '.sub', '.sbv'];
export const SUBTITLE_ACCEPT = SUBTITLE_EXTS.join(',');

export interface UploadedFile {
  name: string;
  blockCount: number;
  // Parsed at upload time so the format is known before translation starts.
  doc: SubtitleDocument;
}

export interface FileStatus {
  name: string;
  outputName: string;
  blockCount: number;
  status: 'pending' | 'translating' | 'done' | 'failed';
  currentBatch?: number;
  totalBatches?: number;
  content?: string;
  timeMs?: number;
  error?: string;
}
