import { RunProgress } from './run-progress';
import { RunStats } from './run-stats';
import { SubtitleDocument } from './subtitle-formats/types';

export const SUBTITLE_EXTS = ['.srt', '.vtt', '.ass', '.ssa', '.sub', '.sbv'];
export const SUBTITLE_ACCEPT = SUBTITLE_EXTS.join(',');

export interface UploadedFile {
  name: string;
  blockCount: number;
  // Parsed at upload time so the format is known before translation starts.
  doc: SubtitleDocument;
}

/** One cue of a finished file: what went in, what came out, and what the
 * run's detectors still say about it. */
export interface ReviewCue {
  number: number;
  timestamp: string;
  source: string;
  target: string;
  /** Empty for a cue nothing was wrong with. */
  flags: string[];
}

/** A flag the run raised and then cleared: what the repair fixed. */
export interface RepairedFlag {
  block: number;
  cause: string;
  message: string;
}

/** A finished file, cue by cue, and what the run fixed on the way. */
export interface FileReview {
  cues: ReviewCue[];
  repaired: RepairedFlag[];
}

export interface FileStatus {
  name: string;
  outputName: string;
  blockCount: number;
  status: 'pending' | 'translating' | 'done' | 'failed';
  /** While translating: which step the file is on, that step's own count, and
   * one percent across every step. */
  progress?: RunProgress;
  content?: string;
  timeMs?: number;
  /** What the finished run cost: LLM calls by pass, blocks, elapsed time. */
  stats?: RunStats;
  /** The translation alongside its source, with what stayed flagged. */
  review?: FileReview;
  error?: string;
}
