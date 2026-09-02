// Transport for one OpenAI-compatible chat call: credential hygiene, the
// request shape the endpoint turns out to accept, its timeout, and turning an
// unusable reply into an error the retry loop understands.

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Subscription } from 'rxjs';
import {
  CRED_QUERY_PARAMS,
  DEFAULT_SEND_TEMPERATURE,
  MAX_DIALECT_CORRECTIONS,
  MAX_RETRY_DELAY_SECS,
  REASONING_BUDGET_MULTIPLIER,
  REQUEST_TEMPERATURE,
  TOKEN_PARAM_COMPLETION,
  TOKEN_PARAM_DEFAULT,
} from './constants';

export interface ProviderConfig {
  apiUrl: string;
  apiKey: string;
  model: string;
}

export class TranslationCancelledError extends Error {
  constructor(message = 'Translation cancelled') {
    super(message);
    this.name = 'TranslationCancelledError';
  }
}

/** A reasoning model spent the whole token budget before writing a word.
 * Retryable, but only with a bigger budget — the same request would burn the
 * same tokens again. */
export class ReasoningBudgetError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ReasoningBudgetError';
  }
}

/** The request shape one endpoint accepts, learned from its own 400s. Starts
 * at the permissive default and only ever narrows, so a run pays at most one
 * corrective round trip per quirk rather than one per call. */
export interface ProviderDialect {
  tokenParam: typeof TOKEN_PARAM_DEFAULT | typeof TOKEN_PARAM_COMPLETION;
  sendTemperature: boolean;
}

/** The per-run settings one chat call needs. */
export interface ChatOptions {
  timeoutMs: number;
  /** Called once per distinct message; a dialect correction reports here. */
  notify: (message: string) => void;
  /** false = never send `temperature`, for an endpoint known to reject it. */
  sendTemperature?: boolean;
}

@Injectable({ providedIn: 'root' })
export class ChatClient {
  private http = inject(HttpClient);

  // Keyed by URL and model: which key an endpoint was reached with cannot
  // change what it accepts.
  private dialects = new Map<string, ProviderDialect>();

  /** This endpoint's dialect, created permissive on first use. Mutable and
   * shared: the main and review passes learn together when they are one
   * endpoint. */
  dialectFor(provider: ProviderConfig): ProviderDialect {
    const key = `${sanitizeApiUrl(provider.apiUrl)} ${provider.model}`;
    let dialect = this.dialects.get(key);
    if (!dialect) {
      dialect = { tokenParam: TOKEN_PARAM_DEFAULT, sendTemperature: true };
      this.dialects.set(key, dialect);
    }
    return dialect;
  }

  /** One chat completion.
   *
   * Two corrections are made here rather than in a caller's retry loop, so a
   * provider's quirks cost a round trip and not a retry budget: the request
   * dialect a 400 asks for, learned once for the whole run, and the token
   * budget a reasoning model needs before it produces any text, tracked for
   * this request alone. An endpoint that accepts the defaults pays neither. */
  async callChat(
    systemPrompt: string,
    userMessage: string,
    provider: ProviderConfig,
    maxTokens: number,
    run: ChatOptions,
    cancelSignal?: AbortSignal,
  ): Promise<string> {
    const url = sanitizeApiUrl(provider.apiUrl);
    const headers = buildHeaders(sanitizeApiKey(provider.apiKey));
    const learned = this.dialectFor(provider);

    let budget = Math.max(maxTokens, 1);
    const ceiling = budget * REASONING_BUDGET_MULTIPLIER;
    let corrections = 0;

    for (;;) {
      // Copied, not referenced: `learned` is shared, and a sibling narrowing
      // it must not rewrite the record of what this request actually sent.
      const sent = { ...effectiveDialect(learned, run) };
      const body = requestBody(
        systemPrompt, userMessage, provider.model, sent, budget,
      );
      try {
        return extractMessageContent(
          await this.postChat(url, body, headers, run.timeoutMs, cancelSignal),
        );
      } catch (err) {
        if (err instanceof ReasoningBudgetError) {
          if (budget >= ceiling) {
            throw new ReasoningBudgetError(
              `The model returned no text at ${budget} tokens `
              + `(${REASONING_BUDGET_MULTIPLIER}x this request's budget) — it `
              + 'spent all of it reasoning. Use a non-reasoning model, or keep '
              + 'this one for the review pass only.',
            );
          }
          budget = Math.min(budget * 2, ceiling);
          console.warn(`${err.message}; retrying with ${budget} tokens.`);
          continue;
        }
        if (!(err instanceof HttpErrorResponse) || err.status !== 400
            || corrections >= MAX_DIALECT_CORRECTIONS) {
          throw err;
        }
        const changes = adjustDialect(learned, extractServerMessage(err));
        // A concurrent call may have learned this lesson while we were in
        // flight, leaving nothing to adjust: the request is still the one
        // shape that was refused, so re-send it in the shape it learned.
        const now = effectiveDialect(learned, run);
        if (!changes.length && now.tokenParam === sent.tokenParam
            && now.sendTemperature === sent.sendTemperature) {
          throw err;
        }
        corrections++;
        for (const change of changes) {
          run.notify(`Provider requires ${change}; adjusted for the rest of the run.`);
        }
      }
    }
  }

  private postChat(
    url: string,
    body: Record<string, unknown>,
    headers: Record<string, string>,
    timeoutMs: number,
    cancelSignal?: AbortSignal,
  ): Promise<unknown> {
    throwIfCancelled(cancelSignal);

    return new Promise<unknown>((resolve, reject) => {
      let settled = false;
      let requestSub: Subscription | null = null;

      const cleanup = () => {
        clearTimeout(timer);
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

      // HttpClient has no default timeout; a stalled connection parks the worker forever.
      const timer = setTimeout(() => {
        settle(() => reject(new Error(
          `Provider did not respond within ${Math.round(timeoutMs / 1000)}s`,
        )));
      }, timeoutMs);

      requestSub = this.http.post<unknown>(url, body, { headers }).subscribe({
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
}


// Log-safe: the raw HttpErrorResponse carries the URL and any credential params.
export function describeError(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    return `HTTP ${err.status}: ${extractServerMessage(err) || err.statusText}`;
  }
  return (err as Error)?.message || String(err);
}

export function extractServerMessage(err: unknown): string {
  if (!(err instanceof HttpErrorResponse)) return '';
  // A status-0 body is a bare ProgressEvent; it stringifies to {"isTrusted":false}.
  if (err.status === 0) {
    return 'Could not reach the API (network error or CORS). Check the API URL, '
      + 'that the server is running, and that it allows requests from this page.';
  }
  if (!err.error) return '';
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


// Providers word the two quirks differently, but each one names the parameter
// it is rejecting. Learned from the server so no model list can go stale.
const TOKEN_PARAM_RE = /max_tokens.*not supported|use 'max_completion_tokens'/i;
const TEMPERATURE_RE = /temperature.*(?:does not support|unsupported value)/i;

// The wording of the one-off warning, and of the run's stats line.
export const TOKEN_PARAM_CHANGE = 'max_completion_tokens instead of max_tokens';
export const TEMPERATURE_CHANGE = 'the default temperature';

/** The wire body, in the dialect this endpoint has proved it accepts. */
function requestBody(
  systemPrompt: string,
  userMessage: string,
  model: string,
  dialect: ProviderDialect,
  maxTokens: number,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userMessage },
    ],
    [dialect.tokenParam]: Math.max(maxTokens, 1),
    stream: false,
  };
  if (dialect.sendTemperature) body['temperature'] = REQUEST_TEMPERATURE;
  if (model) body['model'] = model;
  return body;
}

/** What a call actually sends: what the endpoint taught us, narrowed further
 * by the run's own opt-out. Kept apart so an opt-out never poses as something
 * the server asked for. */
export function effectiveDialect(
  dialect: ProviderDialect, run: ChatOptions,
): ProviderDialect {
  const sendTemperature = run.sendTemperature ?? DEFAULT_SEND_TEMPERATURE;
  return dialect.sendTemperature && !sendTemperature
    ? { ...dialect, sendTemperature: false }
    : dialect;
}

/** Apply what a 400 body asks for; returns the changes actually made. A body
 * blaming something already corrected changes nothing, so the caller stops
 * instead of re-sending the same request forever. */
function adjustDialect(dialect: ProviderDialect, text: string): string[] {
  const changes: string[] = [];
  if (dialect.tokenParam === TOKEN_PARAM_DEFAULT && TOKEN_PARAM_RE.test(text)) {
    dialect.tokenParam = TOKEN_PARAM_COMPLETION;
    changes.push(TOKEN_PARAM_CHANGE);
  }
  if (dialect.sendTemperature && TEMPERATURE_RE.test(text)) {
    dialect.sendTemperature = false;
    changes.push(TEMPERATURE_CHANGE);
  }
  return changes;
}

/** What this endpoint needed that the defaults do not give it; empty when
 * nothing was negotiated. */
export function describeDialect(dialect: ProviderDialect): string {
  const parts: string[] = [];
  if (dialect.tokenParam !== TOKEN_PARAM_DEFAULT) parts.push(dialect.tokenParam);
  if (!dialect.sendTemperature) parts.push('no temperature');
  return parts.join(', ');
}


// Providers return error-shaped and truncated bodies with HTTP 200.
function extractMessageContent(payload: unknown): string {
  if (!payload || typeof payload !== 'object') {
    throw new Error('Response body is not a JSON object');
  }
  const { error, choices } = payload as { error?: unknown; choices?: unknown };
  if (error) {
    const message = typeof error === 'string'
      ? error
      : (error as { message?: string }).message;
    throw new Error(`Provider returned an error: ${message}`);
  }
  if (!Array.isArray(choices) || !choices.length
      || !choices[0] || typeof choices[0] !== 'object') {
    throw new Error('Response contains no choices');
  }
  const choice = choices[0] as { message?: { content?: unknown }; finish_reason?: string };
  const content = choice.message?.content;
  const blank = typeof content !== 'string' || !content.trim();
  if (choice.finish_reason === 'length') {
    // Nothing at all, with the budget exhausted: the model reasoned instead of
    // answering. A bigger budget is the only thing that changes the outcome.
    if (blank) {
      throw new ReasoningBudgetError(
        'the token budget was spent on reasoning before any text',
      );
    }
    // A truncated reply is missing whole blocks; retry beats a count mismatch.
    throw new Error('Response truncated (finish_reason=length)');
  }
  if (blank) {
    throw new Error('Response contains no message content');
  }
  return content as string;
}

// Capped exponential backoff; a numeric Retry-After header wins.
export function retryDelayMs(failures: number, retryAfter: string | null): number {
  if (retryAfter) {
    const secs = Number(retryAfter.trim());
    // Anything else is the HTTP-date form — not worth parsing.
    if (Number.isFinite(secs)) {
      return Math.max(0, Math.min(secs, MAX_RETRY_DELAY_SECS)) * 1000;
    }
  }
  return Math.min(2 ** failures, MAX_RETRY_DELAY_SECS) * 1000;
}

export function retryAfterHeader(err: unknown): string | null {
  return err instanceof HttpErrorResponse ? err.headers.get('Retry-After') : null;
}


// We authenticate via header, so strip credential query params before sending.
function sanitizeApiUrl(url: string): string {
  const trimmed = (url ?? '').trim();
  if (!trimmed) return trimmed;
  try {
    const u = new URL(trimmed);
    for (const name of [...u.searchParams.keys()]) {
      if ((CRED_QUERY_PARAMS as readonly string[]).includes(name.toLowerCase())) {
        u.searchParams.delete(name);
      }
    }
    // Userinfo becomes a competing Authorization header in the browser.
    u.username = '';
    u.password = '';
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
export function stripMarkdownFences(text: string): string {
  let t = text.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
  if (t.startsWith('```')) {
    t = t.replace(/^```[a-zA-Z]*\n?/, '').replace(/\n?```$/, '').trim();
  }
  return t;
}

export function isRetryableStatus(status: number): boolean {
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

export function throwIfCancelled(signal?: AbortSignal) {
  if (signal?.aborted) {
    throw new TranslationCancelledError();
  }
}
