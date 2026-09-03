// Shared test doubles: the app spec and the runner spec both drive a run.
import { UploadedFile } from './core/file-types';
import { RunStats } from './core/run-stats';
import { SubtitleDocument } from './core/subtitle-formats/types';
import {
  ProviderConfig,
  QualityOptions,
  ReviewResult,
  TranslationProgress,
} from './core/translation.service';

interface PendingCall {
  doc: SubtitleDocument;
  provider: ProviderConfig;
  cancelSignal?: AbortSignal;
  onProgress?: (p: TranslationProgress) => void;
  quality?: QualityOptions;
  /** Hands the run's call breakdown back, the way the real service does. */
  reportStats: (stats: RunStats) => void;
  /** Hands the finished cues and flags back, the way the real service does. */
  reportReview: (review: ReviewResult) => void;
  /** Says something mid-run, the way the real service does. */
  notify: (message: string) => void;
  resolve: (content: string) => void;
  reject: (err: unknown) => void;
}

// Hands back a promise the test settles by hand, so the worker pool can be
// observed mid-run instead of only at its end.
export class StubTranslationService {
  pending: PendingCall[] = [];
  maxConcurrent = 0;
  private active = 0;

  translateDocument(
    doc: SubtitleDocument,
    _sourceLang: string,
    _targetLang: string,
    provider: ProviderConfig,
    _batchSize?: number,
    _concurrency?: number,
    _maxRetries?: number,
    onProgress?: (p: TranslationProgress) => void,
    cancelSignal?: AbortSignal,
    quality?: QualityOptions,
  ): Promise<string> {
    this.active++;
    this.maxConcurrent = Math.max(this.maxConcurrent, this.active);
    return new Promise<string>((resolve, reject) => {
      const settle = () => {
        this.active--;
      };
      this.pending.push({
        doc,
        provider,
        cancelSignal,
        onProgress,
        quality,
        reportStats: (stats) => quality?.onStats?.(stats),
        reportReview: (review) => quality?.onReview?.(review),
        notify: (message) => quality?.onNotice?.(message),
        resolve: (content) => {
          settle();
          resolve(content);
        },
        reject: (err) => {
          settle();
          reject(err);
        },
      });
    });
  }

  takeAll(): PendingCall[] {
    return this.pending.splice(0, this.pending.length);
  }
}

export function makeFile(name: string, blockCount = 4): UploadedFile {
  return {
    name,
    blockCount,
    doc: { format: 'srt', blocks: [], rebuild: () => '' },
  };
}

// Lets every queued microtask and the worker pool's follow-up turns run.
export function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
