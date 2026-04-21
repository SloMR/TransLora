import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Subscription } from 'rxjs';
import {
  SubtitleBlock,
  parseLite,
  serializeLite,
  splitBatches,
  validateBatch,
} from './srt-parser';
import { SubtitleDocument } from './subtitle-formats/types';
import { SYSTEM_PROMPT, buildUserMessage } from './translation-prompt';
import {
  CONTEXT_SYSTEM_PROMPT,
  FileContext,
  SCAN_MAX_TOKENS,
  parseContextResponse,
  serializeForScan,
} from './context-pass';

export interface ProviderConfig {
  apiUrl: string;
  apiKey: string;
  model: string;
}

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
export const DEFAULT_BATCH_SIZE = 10;
export const DEFAULT_CONCURRENCY = 5;
export const DEFAULT_PARALLEL_FILES = 1;

const ATTEMPTS_BEFORE_SPLIT = 2;

type ChatResponse = { choices: { message: { content: string } }[] };

@Injectable({ providedIn: 'root' })
export class TranslationService {
  constructor(private http: HttpClient) {}

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

    const fileContext = await this.extractFileContext(
      doc.blocks, sourceLang, targetLang, provider, cancelSignal,
    );

    const batches = splitBatches(doc.blocks, batchSize);
    const results: SubtitleBlock[][] = new Array(batches.length);

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
          batches[i], sourceLang, targetLang, provider, maxRetries, fileContext, cancelSignal,
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

  private async extractFileContext(
    blocks: SubtitleBlock[],
    sourceLang: string,
    targetLang: string,
    provider: ProviderConfig,
    cancelSignal?: AbortSignal,
  ): Promise<FileContext> {
    const sourceLine = sourceLang ? `Source language: ${sourceLang}\n` : '';
    const userMessage =
      sourceLine +
      `Target language: ${targetLang}\n\n` +
      serializeForScan(blocks);

    try {
      const raw = await this.callChat(
        CONTEXT_SYSTEM_PROMPT, userMessage, provider, SCAN_MAX_TOKENS, cancelSignal,
      );
      return parseContextResponse(stripMarkdownFences(raw));
    } catch (err) {
      if (err instanceof TranslationCancelledError) throw err;
      console.warn('Context scan failed, proceeding without:', err);
      return new FileContext();
    }
  }

  private async translateBatch(
    inputBlocks: SubtitleBlock[],
    sourceLang: string,
    targetLang: string,
    provider: ProviderConfig,
    maxRetries: number,
    fileContext: FileContext,
    cancelSignal?: AbortSignal,
  ): Promise<SubtitleBlock[]> {
    throwIfCancelled(cancelSignal);

    const canSplit = inputBlocks.length > 1;
    // Splittable batches give up early — halving resolves persistent count
    // mismatches faster than more retries on the same payload.
    const attempts = canSplit ? ATTEMPTS_BEFORE_SPLIT : maxRetries;
    const firstBlockNum = inputBlocks[0].number;

    const batchWire = serializeLite(inputBlocks);
    const glossary = fileContext.renderForBatch(inputBlocks);
    const userMessage = buildUserMessage(sourceLang, targetLang, batchWire, glossary);
    let hitValidationFailure = false;
    let lastError = '';

    for (let attempt = 1; attempt <= attempts; attempt++) {
      throwIfCancelled(cancelSignal);
      try {
        const raw = await this.callChat(
          SYSTEM_PROMPT, userMessage, provider,
          Math.max(inputBlocks.length, 1) * 120, cancelSignal,
        );
        let output = parseLite(stripMarkdownFences(raw));
        // Wire format strips timestamps; reattach positionally.
        if (output.length === inputBlocks.length) {
          output = output.map((b, i) => ({
            number: inputBlocks[i].number,
            timestamp: inputBlocks[i].timestamp,
            text: b.text,
          }));
        }
        const check = validateBatch(inputBlocks, output);
        if (check.ok) return output;

        hitValidationFailure = true;
        lastError = `validation: ${check.error}`;
        console.warn(`Batch validation failed (${attempt}/${attempts}):`, check.error);

      } catch (err: unknown) {
        if (err instanceof TranslationCancelledError) {
          throw err;
        }

        const status = err instanceof HttpErrorResponse ? err.status : 0;
        lastError = this.extractServerMessage(err) || (err as Error)?.message || String(err);

        console.warn(
          `Batch request failed (${attempt}/${attempts}) [HTTP ${status}]:`,
          lastError,
        );

        if (!isRetryableStatus(status)) {
          throw new Error(`HTTP ${status}: ${lastError} (block ${firstBlockNum})`);
        }

        if (status === 429 && attempt < attempts) {
          const delay = 2 ** attempt * 1000;
          console.warn(`Rate limited — waiting ${delay / 1000}s...`);
          await sleep(delay, cancelSignal);
          continue;
        }
      }

      if (attempt < attempts) {
        await sleep(Math.min(attempt, 3) * 1000, cancelSignal);
      }
    }

    // Recursive split: halve on persistent validation failure. Terminates at
    // N=1 where count mismatch is impossible.
    if (hitValidationFailure && canSplit) {
      const mid = Math.floor(inputBlocks.length / 2);
      const left = inputBlocks.slice(0, mid);
      const right = inputBlocks.slice(mid);
      console.warn(
        `Batch splitting ${inputBlocks.length} -> ${left.length} + ${right.length} blocks`,
      );
      // Sequential: parallel halves would oversubscribe the worker pool slot.
      const leftResult = await this.translateBatch(
        left, sourceLang, targetLang, provider, maxRetries, fileContext, cancelSignal,
      );
      const rightResult = await this.translateBatch(
        right, sourceLang, targetLang, provider, maxRetries, fileContext, cancelSignal,
      );
      return [...leftResult, ...rightResult];
    }

    throw new Error(
      `Batch failed all ${attempts} retries (block ${firstBlockNum}): ${lastError}`,
    );
  }

  private async callChat(
    systemPrompt: string,
    userMessage: string,
    provider: ProviderConfig,
    maxTokens: number,
    cancelSignal?: AbortSignal,
  ): Promise<string> {
    const body: Record<string, unknown> = {
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userMessage },
      ],
      temperature: 0.1,
      max_tokens: Math.max(maxTokens, 1),
      stream: false,
    };
    if (provider.model) body['model'] = provider.model;

    const url = sanitizeApiUrl(provider.apiUrl);
    const headers = buildHeaders(sanitizeApiKey(provider.apiKey));
    const resp = await this.postChat(url, body, headers, cancelSignal);
    return resp.choices[0].message.content;
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


const CRED_QUERY_PARAMS = ['key', 'api_key', 'apikey', 'access_token'];

// We authenticate via header, so strip credential query params before sending.
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

// LLMs sometimes wrap output in ```...``` even when told not to.
function stripMarkdownFences(text: string): string {
  let t = text.trim();
  if (t.startsWith('```')) {
    t = t.replace(/^```[a-zA-Z]*\n?/, '').replace(/\n?```$/, '').trim();
  }
  return t;
}

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
