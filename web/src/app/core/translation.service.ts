import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Subscription } from 'rxjs';
import {
  SubtitleBlock,
  parseSrt,
  serializeSrt,
  splitBatches,
  validateBatch,
} from './srt-parser';
import { SubtitleDocument } from './subtitle-formats/types';
import { SYSTEM_PROMPT, buildUserMessage } from './translation-prompt';

export interface ProviderConfig {
  apiUrl: string;
  apiKey: string;
  model: string;
}

/** Sent to the caller every time a batch starts or finishes. */
export interface TranslationProgress {
  currentBatch: number;
  totalBatches: number;
}

export class TranslationCancelledError extends Error {
  constructor(message = 'Translation cancelled') {
    super(message);
    this.name = 'TranslationCancelledError';
  }
}

export const DEFAULT_MAX_RETRIES = 5;
export const DEFAULT_BATCH_SIZE = 5;
export const DEFAULT_CONCURRENCY = 5;
export const DEFAULT_PARALLEL_FILES = 1;

type ChatResponse = { choices: { message: { content: string } }[] };

@Injectable({ providedIn: 'root' })
export class TranslationService {
  constructor(private http: HttpClient) {}

  /**
   * Translate a parsed subtitle document. The document's blocks are translated
   * in batches and then stitched back together using the document's own
   * `rebuild`, which preserves the source file's original format.
   */
  async translateDocument(
    doc: SubtitleDocument,
    sourceLang: string,
    targetLang: string,
    provider: ProviderConfig,
    batchSize = DEFAULT_BATCH_SIZE,
    concurrency = DEFAULT_CONCURRENCY,
    maxRetries = DEFAULT_MAX_RETRIES,
    onProgress?: (p: TranslationProgress) => void,
    cancelSignal?: AbortSignal,
  ): Promise<string> {
    if (doc.blocks.length === 0) {
      throw new Error('No subtitle blocks found in file');
    }
    throwIfCancelled(cancelSignal);

    const batches = splitBatches(doc.blocks, batchSize);
    const results: SubtitleBlock[][] = new Array(batches.length);

    // Simple worker pool: each worker pulls the next index until none left.
    let nextIdx = 0;
    let completed = 0;
    const emit = () => onProgress?.({
      currentBatch: completed,
      totalBatches: batches.length,
    });

    const worker = async () => {
      while (true) {
        throwIfCancelled(cancelSignal);
        const i = nextIdx++;
        if (i >= batches.length) return;
        results[i] = await this.translateBatch(
          batches[i], sourceLang, targetLang, provider, maxRetries, cancelSignal,
        );
        completed++;
        emit();
      }
    };

    emit();
    const workerCount = Math.min(concurrency, batches.length);
    await Promise.all(Array.from({ length: workerCount }, worker));

    const translated: SubtitleBlock[] = [];
    for (const r of results) if (r) translated.push(...r);
    return doc.rebuild(translated);
  }

  // ---------------------------------------------------------------------
  // Per-batch translation with retry
  // ---------------------------------------------------------------------

  private async translateBatch(
    inputBlocks: SubtitleBlock[],
    sourceLang: string,
    targetLang: string,
    provider: ProviderConfig,
    maxRetries: number,
    cancelSignal?: AbortSignal,
  ): Promise<SubtitleBlock[]> {
    throwIfCancelled(cancelSignal);
    const batchSrt = serializeSrt(inputBlocks);
    const body = this.buildRequestBody(
      sourceLang, targetLang, batchSrt, provider.model, inputBlocks.length,
    );
    const url = sanitizeApiUrl(provider.apiUrl);
    const headers = buildHeaders(sanitizeApiKey(provider.apiKey));
    const firstBlockNum = inputBlocks[0].number;
    let lastError = '';

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      throwIfCancelled(cancelSignal);
      try {
        const resp = await this.postChat(url, body, headers, cancelSignal);
        const output = parseSrt(stripMarkdownFences(resp.choices[0].message.content));
        const check = validateBatch(inputBlocks, output);
        if (check.ok) return output;

        lastError = `validation: ${check.error}`;
        console.warn(`Batch validation failed (${attempt}/${maxRetries}):`, check.error);

      } catch (err: unknown) {
        if (err instanceof TranslationCancelledError) {
          throw err;
        }

        const status = err instanceof HttpErrorResponse ? err.status : 0;
        lastError = this.extractServerMessage(err) || (err as Error)?.message || String(err);

        console.warn(
          `Batch request failed (${attempt}/${maxRetries}) [HTTP ${status}]:`,
          lastError,
        );

        // Fail fast on non-retryable errors (bad key, bad request, etc.)
        if (!isRetryableStatus(status)) {
          throw new Error(`HTTP ${status}: ${lastError} (block ${firstBlockNum})`);
        }

        // Rate-limited: exponential backoff before retrying.
        if (status === 429 && attempt < maxRetries) {
          const delay = 2 ** attempt * 1000;
          console.warn(`Rate limited — waiting ${delay / 1000}s...`);
          await sleep(delay, cancelSignal);
          continue;
        }
      }

      // Small linear backoff between other retries (1s, 2s, 3s cap).
      if (attempt < maxRetries) {
        await sleep(Math.min(attempt, 3) * 1000, cancelSignal);
      }
    }

    throw new Error(
      `Batch failed all ${maxRetries} retries (block ${firstBlockNum}): ${lastError}`,
    );
  }

  private postChat(
    url: string,
    body: Record<string, unknown>,
    headers: Record<string, string>,
    cancelSignal?: AbortSignal,
  ): Promise<ChatResponse> {
    throwIfCancelled(cancelSignal);

    return new Promise<ChatResponse>((resolve, reject) => {
      let settled = false;
      let requestSub: Subscription | null = null;

      const cleanup = () => {
        requestSub?.unsubscribe();
        cancelSignal?.removeEventListener('abort', onAbort);
      };

      const settle = (fn: () => void) => {
        if (settled) return;
        settled = true;
        cleanup();
        fn();
      };

      const onAbort = () => {
        settle(() => reject(new TranslationCancelledError()));
      };

      requestSub = this.http.post<ChatResponse>(url, body, { headers }).subscribe({
        next: (resp) => {
          settle(() => resolve(resp));
        },
        error: (err) => {
          settle(() => reject(err));
        },
        complete: () => {
          settle(() => reject(new Error('Empty response from provider')));
        },
      });

      cancelSignal?.addEventListener('abort', onAbort, { once: true });
    });
  }

  private buildRequestBody(
    sourceLang: string,
    targetLang: string,
    batchSrt: string,
    model: string,
    blockCount: number,
  ): Record<string, unknown> {
    const body: Record<string, unknown> = {
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: buildUserMessage(sourceLang, targetLang, batchSrt) },
      ],
      temperature: 0.1,
      max_tokens: Math.max(blockCount, 1) * 120,
      stream: false,
      cache_prompt: true,
    };
    if (model) body['model'] = model;
    return body;
  }

  /** Pull a human-readable message out of whatever shape the provider returned. */
  private extractServerMessage(err: unknown): string {
    if (!(err instanceof HttpErrorResponse) || !err.error) return '';
    const body = Array.isArray(err.error) ? err.error[0] : err.error;
    if (!body) return '';
    if (typeof body === 'string') {
      return body.length > 300 ? body.slice(0, 300) + '…' : body;
    }
    if (body.error?.message) return body.error.message;
    if (typeof body.error === 'string') return body.error;
    if (body.message) return body.message;
    try {
      return JSON.stringify(body).slice(0, 300);
    } catch {
      return '';
    }
  }
}


// ---------------------------------------------------------------------------
// HELPERS
// ---------------------------------------------------------------------------

const CRED_QUERY_PARAMS = ['key', 'api_key', 'apikey', 'access_token'];

/** Drop credential query params like `?key=...` — we authenticate via header. */
function sanitizeApiUrl(url: string): string {
  const trimmed = (url ?? '').trim();
  if (!trimmed) return trimmed;
  try {
    const u = new URL(trimmed);
    for (const p of CRED_QUERY_PARAMS) u.searchParams.delete(p);
    return u.toString();
  } catch {
    return trimmed;
  }
}

/** Strip whitespace, surrounding quotes, and any accidental `Bearer ` prefix. */
function sanitizeApiKey(key: string): string {
  let k = (key ?? '').trim();
  if ((k.startsWith('"') && k.endsWith('"')) || (k.startsWith("'") && k.endsWith("'"))) {
    k = k.slice(1, -1).trim();
  }
  if (/^bearer\s+/i.test(k)) k = k.replace(/^bearer\s+/i, '').trim();
  return k;
}

function buildHeaders(apiKey: string): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (apiKey && apiKey !== 'none') headers['Authorization'] = `Bearer ${apiKey}`;
  return headers;
}

/** LLMs sometimes wrap output in ```...``` despite being told not to. */
function stripMarkdownFences(text: string): string {
  let t = text.trim();
  if (t.startsWith('```')) {
    t = t.replace(/^```[a-zA-Z]*\n?/, '').replace(/\n?```$/, '').trim();
  }
  return t;
}

/** Retry on timeout, rate-limit, 5xx, or network errors. Everything else is fatal. */
function isRetryableStatus(status: number): boolean {
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

function throwIfCancelled(signal?: AbortSignal) {
  if (signal?.aborted) {
    throw new TranslationCancelledError();
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  throwIfCancelled(signal);

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, ms);

    const onAbort = () => {
      cleanup();
      reject(new TranslationCancelledError());
    };

    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
    };

    signal?.addEventListener('abort', onAbort, { once: true });
  });
}
