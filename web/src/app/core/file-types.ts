import { SubtitleDocument } from './subtitle-formats/types';

export const SUBTITLE_EXTS = ['.srt', '.vtt', '.ass', '.ssa', '.sub', '.sbv'];
export const SUBTITLE_ACCEPT = SUBTITLE_EXTS.join(',');

/** A file the user has picked but not yet translated. */
export interface UploadedFile {
  name: string;
  blockCount: number;
  /** Parsed once at upload so we know the format before translation starts. */
  doc: SubtitleDocument;
}

/** The live state of a single file during and after translation. */
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
