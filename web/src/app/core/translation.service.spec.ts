import { provideHttpClient, withXhr } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { ChatClient } from './chat-client';
import { SubtitleBlock } from './srt-parser';
import { SubtitleDocument } from './subtitle-formats/types';
import { RunStats } from './run-stats';
import { TOKEN_PARAM_COMPLETION } from './constants';
import {
  ATTRIBUTION_SYSTEM_PROMPT,
  BACK_TRANSLATION_SYSTEM_PROMPT,
  CONTEXT_SYSTEM_PROMPT,
  FIX_FLAGGED_RULE,
  REVIEW_SYSTEM_PROMPT,
  SYSTEM_PROMPT,
} from './translation-prompt';
import {
  ProviderConfig,
  QualityOptions,
  TranslationCancelledError,
  TranslationProgress,
  TranslationService,
} from './translation.service';

const PROVIDER: ProviderConfig = {
  apiUrl: 'https://api.test/v1/chat/completions',
  apiKey: 'sk-test',
  model: 'test-model',
};

/** A document whose rebuild records exactly what the pipeline assembled. */
function fakeDoc(count: number): { doc: SubtitleDocument; assembled: () => SubtitleBlock[] } {
  const blocks: SubtitleBlock[] = Array.from({ length: count }, (_, i) => ({
    number: i + 1,
    timestamp: `00:00:${String(i).padStart(2, '0')},000 --> 00:00:${String(i + 1).padStart(2, '0')},000`,
    text: `Line ${i + 1}`,
  }));
  let assembled: SubtitleBlock[] = [];
  const doc: SubtitleDocument = {
    format: 'srt',
    blocks,
    rebuild: (translated) => {
      assembled = translated;
      return translated.map((b) => b.text).join('\n');
    },
  };
  return { doc, assembled: () => assembled };
}

function chat(content: string): object {
  return { choices: [{ message: { content } }] };
}

/** Wire-format reply translating the given block numbers to `T<n>`. */
function wireFor(numbers: number[], prefix = 'T'): string {
  return numbers.map((n) => `${n}\n${prefix}${n}`).join('\n\n');
}

function rangeOf(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i);
}

function bodyOf(req: TestRequest): { messages: { content: string }[]; max_tokens: number } {
  return req.request.body as { messages: { content: string }[]; max_tokens: number };
}

/** The whole request body, for the fields bodyOf does not name. */
function rawBodyOf(req: TestRequest): Record<string, unknown> {
  return req.request.body as Record<string, unknown>;
}

function systemPromptOf(req: TestRequest): string {
  return bodyOf(req).messages[0]!.content;
}

function userMessageOf(req: TestRequest): string {
  return bodyOf(req).messages[1]!.content;
}

function modelOf(req: TestRequest): string | undefined {
  return (req.request.body as { model?: string }).model;
}

function pause(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('TranslationService', () => {
  let service: TranslationService;
  let httpMock: HttpTestingController;
  /** Requests pulled off the mock backend but not yet answered by a test. */
  let queue: TestRequest[];
  /** Every request the run has issued so far. */
  let seen: TestRequest[];

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withXhr()), provideHttpClientTesting()],
    });
    service = TestBed.inject(TranslationService);
    httpMock = TestBed.inject(HttpTestingController);
    queue = [];
    seen = [];
  });

  afterEach(() => {
    collect();
    // Cancelled requests are the point of the timeout and abort cases.
    httpMock.verify({ ignoreCancelled: true });
  });

  function collect(): void {
    const found = httpMock.match(() => true);
    queue.push(...found);
    seen.push(...found);
  }

  /** Wait until the service has issued its next request, then return it. */
  async function nextRequest(what = 'a request'): Promise<TestRequest> {
    const deadline = Date.now() + 4000;
    for (;;) {
      collect();
      if (queue.length) return queue.shift()!;
      if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
      await pause(5);
    }
  }

  async function nextRequests(n: number): Promise<TestRequest[]> {
    const out: TestRequest[] = [];
    for (let i = 0; i < n; i++) out.push(await nextRequest(`request ${i + 1} of ${n}`));
    return out;
  }

  /** Let any in-flight work settle, then account for what it issued. */
  async function settle(): Promise<void> {
    await pause(20);
    collect();
  }

  /** Answer the always-first context scan; `glossary` defaults to no sections. */
  async function flushScan(glossary = 'no glossary here'): Promise<TestRequest> {
    const req = await nextRequest('the context scan');
    expect(systemPromptOf(req)).toBe(CONTEXT_SYSTEM_PROMPT);
    req.flush(chat(glossary));
    return req;
  }

  function translate(
    doc: SubtitleDocument,
    opts: {
      batchSize?: number;
      concurrency?: number;
      maxRetries?: number;
      onProgress?: (p: TranslationProgress) => void;
      cancelSignal?: AbortSignal;
      quality?: QualityOptions;
      provider?: ProviderConfig;
      targetLang?: string;
    } = {},
  ): Promise<string> {
    return service.translateDocument(
      doc,
      'English',
      opts.targetLang ?? 'French',
      opts.provider ?? PROVIDER,
      opts.batchSize ?? 2,
      opts.concurrency ?? 1,
      opts.maxRetries ?? 3,
      opts.onProgress,
      opts.cancelSignal,
      opts.quality ?? {},
    );
  }

  it('rejects a document with no blocks without calling the provider', async () => {
    const doc: SubtitleDocument = { format: 'srt', blocks: [], rebuild: () => '' };
    await expect(translate(doc)).rejects.toThrow(/No subtitle blocks/);
  });

  it('translates every batch and reports progress', async () => {
    const { doc, assembled } = fakeDoc(4);
    const progress: TranslationProgress[] = [];
    const run = translate(doc, { batchSize: 2, onProgress: (p) => progress.push(p) });

    await flushScan();
    const first = await nextRequest();
    expect(systemPromptOf(first)).toBe(SYSTEM_PROMPT);
    first.flush(chat(wireFor([1, 2])));
    (await nextRequest()).flush(chat(wireFor([3, 4])));

    expect(await run).toBe('T1\nT2\nT3\nT4');
    expect(assembled().map((b) => b.number)).toEqual([1, 2, 3, 4]);
    // Timestamps are reattached from the source, never from the model.
    expect(assembled()[0]!.timestamp).toBe(doc.blocks[0]!.timestamp);
    expect(progress[0]).toEqual({ currentBatch: 0, totalBatches: 2 });
    expect(progress[progress.length - 1]).toEqual({ currentBatch: 2, totalBatches: 2 });
  });

  it('accepts a fenced reply wrapped in a <think> block', async () => {
    const { doc } = fakeDoc(2);
    const run = translate(doc, { batchSize: 2 });
    await flushScan();
    (await nextRequest()).flush(
      chat(`<think>weighing the register</think>\n\`\`\`srt\n${wireFor([1, 2])}\n\`\`\``),
    );
    expect(await run).toBe('T1\nT2');
  });

  it('starts a worker even when concurrency is zero', async () => {
    // A zero or NaN concurrency used to start no workers at all and "succeed"
    // by rebuilding the untranslated source.
    const { doc } = fakeDoc(4);
    const run = translate(doc, { batchSize: 2, concurrency: 0 });
    await flushScan();
    for (const req of await nextRequests(2)) {
      const nums = userMessageOf(req).includes('1\nLine 1') ? [1, 2] : [3, 4];
      req.flush(chat(wireFor(nums)));
    }
    expect(await run).toBe('T1\nT2\nT3\nT4');
  });

  it('splits the batch after two short replies and translates both halves', async () => {
    const { doc } = fakeDoc(10);
    const run = translate(doc, { batchSize: 10, maxRetries: 5 });
    await flushScan();

    // Nine blocks for a ten-block batch, twice: a deterministic merge.
    for (let i = 0; i < 2; i++) {
      (await nextRequest(`batch attempt ${i + 1}`)).flush(chat(wireFor(rangeOf(1, 9))));
    }

    const left = await nextRequest('the left half');
    expect(userMessageOf(left)).toContain('5\nLine 5');
    expect(userMessageOf(left)).not.toContain('6\nLine 6');
    left.flush(chat(wireFor(rangeOf(1, 5))));

    const right = await nextRequest('the right half');
    expect(userMessageOf(right)).toContain('6\nLine 6');
    right.flush(chat(wireFor(rangeOf(6, 10))));

    expect(await run).toBe(rangeOf(1, 10).map((n) => `T${n}`).join('\n'));
    expect(seen.length).toBe(5);
  }, 20_000);

  it('retries a 429 for the whole retry budget instead of stopping at two', async () => {
    const { doc } = fakeDoc(4);
    const run = translate(doc, { batchSize: 4, maxRetries: 4 });
    await flushScan();

    for (let i = 0; i < 4; i++) {
      const req = await nextRequest(`429 attempt ${i + 1}`);
      req.flush(
        { error: { message: 'slow down' } },
        { status: 429, statusText: 'Too Many Requests', headers: { 'Retry-After': '0' } },
      );
    }

    await expect(run).rejects.toThrow(
      /Batch failed after 4 attempt\(s\) \(block 1\): slow down/,
    );
    // A rate limit must not consume the split budget: no half-batch requests.
    await settle();
    expect(seen.length).toBe(5);
  }, 20_000);

  it('stops sibling workers after a non-retryable 400', async () => {
    const { doc } = fakeDoc(12);
    const run = translate(doc, { batchSize: 2, concurrency: 3 });
    await flushScan();

    const inFlight = await nextRequests(3);
    inFlight[0]!.flush(
      { error: { message: 'unknown model' } },
      { status: 400, statusText: 'Bad Request' },
    );

    await expect(run).rejects.toThrow(/HTTP 400: unknown model \(block 1\)/);
    await settle();
    // Scan + the three in-flight batches; batches 4-6 are never requested.
    expect(seen.length).toBe(4);
    expect(inFlight[1]!.cancelled).toBe(true);
    expect(inFlight[2]!.cancelled).toBe(true);
  }, 20_000);

  it('gives up on a network failure after one retry and explains CORS', async () => {
    const { doc } = fakeDoc(2);
    const run = translate(doc, { batchSize: 2, maxRetries: 5 });
    await flushScan();

    for (let i = 0; i < 2; i++) {
      (await nextRequest(`network attempt ${i + 1}`)).error(new ProgressEvent('error'));
    }

    await expect(run).rejects.toThrow(/Could not reach the API \(network error or CORS\)/);
    await settle();
    expect(seen.length).toBe(3);
  }, 20_000);

  it('abandons a stalled request after timeoutMs and retries it', async () => {
    const { doc } = fakeDoc(2);
    const run = translate(doc, {
      batchSize: 2,
      maxRetries: 2,
      quality: { timeoutMs: 250 },
    });
    await flushScan();

    const stalled = await nextRequest('the stalled batch');
    // Never answered: the request must be abandoned, not awaited forever.
    const retry = await nextRequest('the retry');
    expect(stalled.cancelled).toBe(true);
    retry.flush(chat(wireFor([1, 2])));

    expect(await run).toBe('T1\nT2');
  }, 20_000);

  it('reports the timeout in the failure message', async () => {
    const { doc } = fakeDoc(2);
    const run = translate(doc, {
      batchSize: 2,
      maxRetries: 1,
      quality: { timeoutMs: 600 },
    });
    await flushScan();
    await nextRequest('the stalled batch');

    await expect(run).rejects.toThrow(/Provider did not respond within 1s/);
  }, 20_000);

  it('cancels in-flight requests and rejects when the caller aborts', async () => {
    const { doc } = fakeDoc(4);
    const controller = new AbortController();
    const run = translate(doc, { batchSize: 2, cancelSignal: controller.signal });
    await flushScan();

    const batch = await nextRequest('the first batch');
    controller.abort();

    await expect(run).rejects.toThrow(TranslationCancelledError);
    expect(batch.cancelled).toBe(true);
    await settle();
    expect(seen.length).toBe(2);
  });

  describe('review pass', () => {
    const GLOSSARY = '<characters>\nAlice => Alicia | female\n</characters>';

    it('keeps the first pass when the review reply fails validation', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Alice, wait.';
      const run = translate(doc, { batchSize: 2 });
      await flushScan(GLOSSARY);

      // The first pass carries the pinned name, so nothing here is flagged and
      // the review is the only call after the batch.
      (await nextRequest('the batch')).flush(chat('1\nAlicia\n\n2\nDos'));
      const review = await nextRequest('the review');
      expect(systemPromptOf(review)).toBe(REVIEW_SYSTEM_PROMPT);
      // Renumbered reply: validation rejects it, so the first pass stands.
      review.flush(chat('5\nRevised one\n\n6\nRevised two'));

      expect(await run).toBe('Alicia\nDos');
    });

    it('takes a valid revision', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Alice, wait.';
      const run = translate(doc, { batchSize: 2 });
      await flushScan(GLOSSARY);

      (await nextRequest('the batch')).flush(chat('1\nUno\n\n2\nDos'));
      (await nextRequest('the review')).flush(chat('1\nAlicia, attends.\n\n2\nDos'));

      expect(await run).toBe('Alicia, attends.\nDos');
    });

    it('keeps the first pass and reports a notice when the review call fails', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Alice, wait.';
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        quality: { onNotice: (m) => notices.push(m) },
      });
      await flushScan(GLOSSARY);

      (await nextRequest('the batch')).flush(chat('1\nAlicia\n\n2\nDos'));
      (await nextRequest('the review')).flush(
        { error: { message: 'boom' } },
        { status: 500, statusText: 'Server Error' },
      );

      expect(await run).toBe('Alicia\nDos');
      expect(notices).toEqual(['Review pass failed - keeping the first-pass translation']);
    });

    it('carries the target script\'s grammar checks, and only in the request', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Alice, wait.';
      const run = translate(doc, {
        batchSize: 2, targetLang: 'Arabic', quality: { fixFlagged: false },
      });
      await flushScan(GLOSSARY);

      (await nextRequest('the batch')).flush(chat('1\nواحد\n\n2\nاثنان'));
      const review = await nextRequest('the review');
      expect(userMessageOf(review)).toContain(
        'Target-language checks: After لم use the jussive',
      );
      expect(systemPromptOf(review)).not.toContain('Target-language checks');
      review.flush(chat('1\nواحد\n\n2\nاثنان'));
      await run;
    });

    it('sends no checks line for a target script that has none', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Alice, wait.';
      const run = translate(doc, {
        batchSize: 2, targetLang: 'French', quality: { fixFlagged: false },
      });
      await flushScan(GLOSSARY);

      (await nextRequest('the batch')).flush(chat('1\nUn\n\n2\nDeux'));
      const review = await nextRequest('the review');
      expect(userMessageOf(review)).not.toContain('Target-language checks');
      review.flush(chat('1\nUn\n\n2\nDeux'));
      await run;
    });

    it('sends the review to its own provider, and nothing else there', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Alice, wait.';
      const run = translate(doc, {
        batchSize: 2,
        quality: {
          reviewApiUrl: 'https://review.test/v1/chat/completions',
          reviewApiKey: 'sk-review',
          reviewModel: 'big-model',
        },
      });

      const scan = await flushScan(GLOSSARY);
      expect(scan.request.url).toBe(PROVIDER.apiUrl);
      const batch = await nextRequest('the batch');
      expect(batch.request.url).toBe(PROVIDER.apiUrl);
      expect(modelOf(batch)).toBe('test-model');
      batch.flush(chat('1\nUno\n\n2\nDos'));

      const review = await nextRequest('the review');
      expect(review.request.url).toBe('https://review.test/v1/chat/completions');
      expect(review.request.headers.get('Authorization')).toBe('Bearer sk-review');
      expect(modelOf(review)).toBe('big-model');
      review.flush(chat('1\nAlicia, attends.\n\n2\nDos'));

      expect(await run).toBe('Alicia, attends.\nDos');
    });

    it('takes the main provider for every review field left blank', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Alice, wait.';
      const run = translate(doc, {
        batchSize: 2,
        quality: { reviewModel: 'big-model', reviewApiUrl: '  ', fixFlagged: false },
      });
      await flushScan(GLOSSARY);
      (await nextRequest('the batch')).flush(chat('1\nUno\n\n2\nDos'));

      const review = await nextRequest('the review');
      expect(review.request.url).toBe(PROVIDER.apiUrl);
      expect(review.request.headers.get('Authorization')).toBe('Bearer sk-test');
      expect(modelOf(review)).toBe('big-model');
      review.flush(chat('1\nUno\n\n2\nDos'));
      await run;
    });

    it("carries the batch's own idioms into the review request", async () => {
      // The reviewer is allowed to fix a word-for-word idiom; the glossary it
      // is sent is the same batch slice the translation pass got, idioms and
      // all, so the rule has its target in front of it.
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Alice, break a leg.';
      const run = translate(doc, { batchSize: 2, quality: { fixFlagged: false } });
      await flushScan(
        `${GLOSSARY}\n<idioms>\nbreak a leg => bonne chance\n`
        + 'that ship has sailed => trop tard\n</idioms>',
      );

      (await nextRequest('the batch')).flush(chat('1\nAlicia, bonne chance.\n\n2\nDeux'));
      const review = await nextRequest('the review');
      expect(userMessageOf(review)).toContain(
        'Idioms - render by meaning, never word for word:\n'
        + '- break a leg => bonne chance',
      );
      expect(userMessageOf(review)).not.toContain('that ship has sailed');
      review.flush(chat('1\nAlicia, bonne chance.\n\n2\nDeux'));
      await run;
    });

    it('reviews a batch whose only glossary match is an idiom', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks[0]!.text = 'Break a leg.';
      const run = translate(doc, { batchSize: 2, quality: { fixFlagged: false } });
      await flushScan('<idioms>\nbreak a leg => bonne chance\n</idioms>');

      (await nextRequest('the batch')).flush(chat('1\nBonne chance.\n\n2\nDeux'));
      const review = await nextRequest('the review');
      expect(systemPromptOf(review)).toBe(REVIEW_SYSTEM_PROMPT);
      expect(userMessageOf(review)).toContain(
        'Idioms - render by meaning, never word for word:\n'
        + '- break a leg => bonne chance',
      );
      review.flush(chat('1\nBonne chance.\n\n2\nDeux'));

      expect(await run).toBe('Bonne chance.\nDeux');
    });

    it('skips the review when the batch has nothing correctable', async () => {
      const { doc } = fakeDoc(2);
      const run = translate(doc, { batchSize: 2 });
      // Alice is in the glossary but never named in the batch text.
      await flushScan(GLOSSARY);
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));

      expect(await run).toBe('T1\nT2');
      await settle();
      expect(seen.length).toBe(2);
    });

    it('reports a notice and translates on when the glossary scan fails', async () => {
      const { doc } = fakeDoc(2);
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        quality: { onNotice: (m) => notices.push(m) },
      });
      (await nextRequest('the context scan')).flush(
        { error: { message: 'boom' } },
        { status: 500, statusText: 'Server Error' },
      );
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));

      expect(await run).toBe('T1\nT2');
      expect(notices).toEqual(['Glossary scan failed - continuing without context hints']);
    });
  });

  describe('credential handling', () => {
    async function firstRequestWith(provider: ProviderConfig): Promise<TestRequest> {
      const { doc } = fakeDoc(1);
      const run = translate(doc, { batchSize: 1, provider });
      const scan = await nextRequest('the context scan');
      scan.flush(chat('no glossary here'));
      (await nextRequest('the batch')).flush(chat('1\nT1'));
      await run;
      return scan;
    }

    it('strips credential query params whatever their case', async () => {
      const { doc } = fakeDoc(1);
      const run = translate(doc, {
        batchSize: 1,
        provider: { ...PROVIDER, apiUrl: 'https://api.test/v1/chat?API_KEY=secret&Api_Key=s2&a=1' },
      });
      const scan = await nextRequest('the context scan');
      expect(scan.request.urlWithParams).toBe('https://api.test/v1/chat?a=1');
      expect(scan.request.urlWithParams).not.toContain('secret');
      scan.flush(chat('no glossary here'));
      (await nextRequest('the batch')).flush(chat('1\nT1'));
      await run;
    });

    it('strips URL userinfo, which the browser would turn into a second credential', async () => {
      const { doc } = fakeDoc(1);
      const run = translate(doc, {
        batchSize: 1,
        provider: { ...PROVIDER, apiUrl: 'https://user:pass@api.test/v1/chat' },
      });
      const scan = await nextRequest('the context scan');
      expect(scan.request.url).toBe('https://api.test/v1/chat');
      scan.flush(chat('no glossary here'));
      (await nextRequest('the batch')).flush(chat('1\nT1'));
      await run;
    });

    it('strips quotes and a pasted Bearer prefix from the key', async () => {
      const req = await firstRequestWith({ ...PROVIDER, apiKey: '  "Bearer sk-quoted"  ' });
      expect(req.request.headers.get('Authorization')).toBe('Bearer sk-quoted');
    });

    it('sends no Authorization header for a keyless local server', async () => {
      const req = await firstRequestWith({ ...PROVIDER, apiKey: 'none' });
      expect(req.request.headers.has('Authorization')).toBe(false);
    });
  });

  describe('unusable success bodies', () => {
    async function rejectsWith(body: object, pattern: RegExp): Promise<void> {
      const { doc } = fakeDoc(2);
      const run = translate(doc, { batchSize: 2, maxRetries: 1 });
      await flushScan();
      (await nextRequest('the batch')).flush(body);
      await expect(run).rejects.toThrow(pattern);
    }

    it('treats a truncated reply as retryable rather than a count mismatch', async () => {
      await rejectsWith(
        { choices: [{ finish_reason: 'length', message: { content: '1\nT1' } }] },
        /Response truncated/,
      );
    });

    it('treats an error-shaped 200 body as a provider error', async () => {
      await rejectsWith({ error: { message: 'context length exceeded' } }, /context length exceeded/);
    });

    it('treats a choices-less body as a failure', async () => {
      await rejectsWith({ id: 'x' }, /Response contains no choices/);
    });
  });

  // Measured against the live OpenAI API: gpt-4.1 accepts the defaults, gpt-5
  // rejects both of them, one parameter per 400.
  describe('the provider request dialect', () => {
    const TOKEN_PARAM_400 = {
      error: {
        message: "Unsupported parameter: 'max_tokens' is not supported with "
          + "this model. Use 'max_completion_tokens' instead.",
      },
    };
    const TEMPERATURE_400 = {
      error: {
        message: "Unsupported value: 'temperature' does not support 0.1 with "
          + 'this model. Only the default (1) value is supported.',
      },
    };
    const BAD_REQUEST = { status: 400, statusText: 'Bad Request' };

    it('asks for max_tokens and a temperature until a provider objects', async () => {
      const { doc } = fakeDoc(2);
      const run = translate(doc, { batchSize: 2, quality: { review: false } });
      const scan = await flushScan();
      const batch = await nextRequest('the batch');
      batch.flush(chat(wireFor([1, 2])));
      await run;

      for (const req of [scan, batch]) {
        expect(rawBodyOf(req)['max_tokens']).toBeGreaterThan(0);
        expect(rawBodyOf(req)['temperature']).toBe(0.1);
        expect(rawBodyOf(req)['max_completion_tokens']).toBeUndefined();
      }
      // An endpoint that accepts the defaults pays for no negotiation.
      expect(seen.length).toBe(2);
    });

    it('re-sends the same request with max_completion_tokens after a 400', async () => {
      const { doc } = fakeDoc(2);
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        quality: { review: false, onNotice: (m) => notices.push(m) },
      });

      const refused = await nextRequest('the scan');
      const budget = rawBodyOf(refused)['max_tokens'];
      refused.flush(TOKEN_PARAM_400, BAD_REQUEST);

      const corrected = await nextRequest('the corrected scan');
      expect(rawBodyOf(corrected)['max_completion_tokens']).toBe(budget);
      expect(rawBodyOf(corrected)['max_tokens']).toBeUndefined();
      // The same request, not a fresh one: only the spelling changed.
      expect(userMessageOf(corrected)).toBe(userMessageOf(refused));
      corrected.flush(chat('no glossary here'));

      // Learned for the run: the batch never re-asks the same question.
      const batch = await nextRequest('the batch');
      expect(rawBodyOf(batch)['max_completion_tokens']).toBeGreaterThan(0);
      batch.flush(chat(wireFor([1, 2])));

      expect(await run).toBe('T1\nT2');
      expect(notices).toContain(
        'Provider requires max_completion_tokens instead of max_tokens; '
        + 'adjusted for the rest of the run.',
      );
      expect(seen.length).toBe(3);
    });

    it('takes the effort a model says it supports when it refuses the first ask', async () => {
      // gpt-5.6 refuses 'minimal' and lists none/low/...; older gpt-5 refuses
      // 'none' and lists minimal/low/...: whichever way, the next request asks
      // for the least the model named, and the run says so once.
      const { doc } = fakeDoc(2);
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        quality: { review: false, onNotice: (m) => notices.push(m) },
      });

      (await nextRequest('the scan')).flush(TOKEN_PARAM_400, BAD_REQUEST);
      const asked = await nextRequest('the scan asking for none');
      expect(rawBodyOf(asked)['reasoning_effort']).toBe('none');
      asked.flush({ error: { message: "Unsupported value: 'reasoning_effort' does not "
        + "support 'none' with this model. Supported values are: 'minimal', 'low', "
        + "'medium', and 'high'." } }, BAD_REQUEST);

      const settled = await nextRequest('the scan asking for minimal');
      expect(rawBodyOf(settled)['reasoning_effort']).toBe('minimal');
      settled.flush(chat('no glossary here'));
      const batch = await nextRequest('the batch');
      expect(rawBodyOf(batch)['reasoning_effort']).toBe('minimal');
      batch.flush(chat(wireFor([1, 2])));
      expect(await run).toBe('T1\nT2');
      expect(notices).toContain(
        "Provider requires reasoning_effort 'minimal'; adjusted for the rest of the run.",
      );
    });

    it('asks a reasoning endpoint for no reasoning once it has shown itself', async () => {
      const { doc } = fakeDoc(2);
      const run = translate(doc, { batchSize: 2, quality: { review: false } });

      const refused = await nextRequest('the scan');
      expect(rawBodyOf(refused)['reasoning_effort']).toBeUndefined();
      refused.flush(TOKEN_PARAM_400, BAD_REQUEST);

      // Wanting max_completion_tokens is what a reasoning endpoint does; from
      // here every call asks it not to spend the budget thinking.
      const corrected = await nextRequest('the corrected scan');
      expect(rawBodyOf(corrected)['reasoning_effort']).toBe('none');
      corrected.flush(chat('no glossary here'));

      const batch = await nextRequest('the batch');
      expect(rawBodyOf(batch)['reasoning_effort']).toBe('none');
      batch.flush(chat(wireFor([1, 2])));
      expect(await run).toBe('T1\nT2');
    });

    it('learns minimal reasoning from an output-limit refusal too', async () => {
      const { doc } = fakeDoc(2);
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        quality: { review: false, onNotice: (m) => notices.push(m) },
      });

      const refused = await nextRequest('the scan');
      refused.flush({ error: { message: 'Could not finish the message because '
        + 'max_tokens or model output limit was reached. Please try again with '
        + 'higher max_tokens.' } }, BAD_REQUEST);

      const corrected = await nextRequest('the corrected scan');
      expect(rawBodyOf(corrected)['reasoning_effort']).toBe('none');
      // Nothing else changed: this endpoint never objected to max_tokens.
      expect(rawBodyOf(corrected)['max_tokens']).toBeGreaterThan(0);
      corrected.flush(chat('no glossary here'));
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));
      await run;

      expect(notices).toContain(
        "Provider requires reasoning_effort 'none'; adjusted for the rest of the run.",
      );
    });

    it('drops the temperature after a 400 that names it', async () => {
      const { doc } = fakeDoc(2);
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        quality: { review: false, onNotice: (m) => notices.push(m) },
      });

      (await nextRequest('the scan')).flush(TEMPERATURE_400, BAD_REQUEST);
      const corrected = await nextRequest('the corrected scan');
      expect(rawBodyOf(corrected)['temperature']).toBeUndefined();
      corrected.flush(chat('no glossary here'));
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));

      expect(await run).toBe('T1\nT2');
      expect(notices).toContain(
        'Provider requires the default temperature; adjusted for the rest of the run.',
      );
    });

    it('spends no retry budget on the two corrections a batch needs', async () => {
      const { doc } = fakeDoc(2);
      // One transport attempt only: a correction counted as a failure would
      // fail the file here.
      const run = translate(doc, {
        batchSize: 2,
        maxRetries: 1,
        quality: { review: false },
      });
      await flushScan();

      (await nextRequest('the batch')).flush(TOKEN_PARAM_400, BAD_REQUEST);
      (await nextRequest('the retry')).flush(TEMPERATURE_400, BAD_REQUEST);
      const accepted = await nextRequest('the accepted request');
      expect(rawBodyOf(accepted)['max_completion_tokens']).toBeGreaterThan(0);
      expect(rawBodyOf(accepted)['temperature']).toBeUndefined();
      accepted.flush(chat(wireFor([1, 2])));

      expect(await run).toBe('T1\nT2');
    }, 20_000);

    it('stops correcting when a 400 keeps naming something already fixed', async () => {
      const { doc } = fakeDoc(2);
      const run = translate(doc, {
        batchSize: 2,
        maxRetries: 1,
        quality: { review: false },
      });
      await flushScan();

      (await nextRequest('the batch')).flush(TOKEN_PARAM_400, BAD_REQUEST);
      // Already corrected, so the second one is just a 400 like any other.
      (await nextRequest('the corrected batch')).flush(TOKEN_PARAM_400, BAD_REQUEST);

      await expect(run).rejects.toThrow(/HTTP 400.*max_tokens.*\(block 1\)/);
      await settle();
      expect(seen.length).toBe(3);
    }, 20_000);

    it('re-sends a correction a concurrent call already made', async () => {
      // With several calls in flight the first 400 corrects the dialect and
      // the rest find nothing left to adjust. They must re-send in the shape
      // that was learned, not fail the file over a 400 that no longer applies.
      const { doc } = fakeDoc(2);
      const run = translate(doc, {
        batchSize: 2,
        maxRetries: 1,
        quality: { review: false },
      });

      const refused = await nextRequest('the scan');
      // Stands in for the concurrent call that got there first.
      TestBed.inject(ChatClient).dialectFor(PROVIDER).tokenParam
        = TOKEN_PARAM_COMPLETION;
      refused.flush(TOKEN_PARAM_400, BAD_REQUEST);

      // The same scan again, not the batch the run falls through to when the
      // scan is abandoned.
      const resent = await nextRequest('the re-sent scan');
      expect(systemPromptOf(resent)).toBe(CONTEXT_SYSTEM_PROMPT);
      expect(userMessageOf(resent)).toBe(userMessageOf(refused));
      expect(rawBodyOf(resent)['max_completion_tokens']).toBeGreaterThan(0);
      expect(rawBodyOf(resent)['max_tokens']).toBeUndefined();
      resent.flush(chat('no glossary here'));
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));

      expect(await run).toBe('T1\nT2');
      await settle();
      expect(seen.length).toBe(3);
    }, 20_000);

    it('never negotiates over a 400 that is about something else', async () => {
      const { doc } = fakeDoc(2);
      const run = translate(doc, { batchSize: 2, quality: { review: false } });
      await flushScan();
      (await nextRequest('the batch')).flush(
        { error: { message: 'unknown model' } }, BAD_REQUEST,
      );

      await expect(run).rejects.toThrow(/HTTP 400: unknown model \(block 1\)/);
      await settle();
      expect(seen.length).toBe(2);
    });

    it('sends no temperature at all when the run opted out', async () => {
      const { doc } = fakeDoc(2);
      const run = translate(doc, {
        batchSize: 2,
        quality: { review: false, sendTemperature: false },
      });
      const scan = await flushScan();
      expect(rawBodyOf(scan)['temperature']).toBeUndefined();
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));

      expect(await run).toBe('T1\nT2');
      // No 400 was needed to learn it.
      expect(seen.length).toBe(2);
    });

    it('reports the negotiated dialect with the run', async () => {
      const { doc } = fakeDoc(2);
      let stats: RunStats | undefined;
      const run = translate(doc, {
        batchSize: 2,
        quality: {
          review: false, sendTemperature: false, onStats: (s) => { stats = s; },
        },
      });
      (await nextRequest('the scan')).flush(TOKEN_PARAM_400, BAD_REQUEST);
      (await nextRequest('the corrected scan')).flush(chat('no glossary here'));
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));
      await run;

      expect(stats!.dialect).toEqual({
        tokenParam: 'max_completion_tokens', sendTemperature: false, reasoningEffort: 'none',
      });
    });
  });

  // A reasoning model can spend the whole budget thinking: HTTP 200,
  // finish_reason "length", and not one character of content.
  describe('a reply that was all reasoning', () => {
    const ALL_REASONING = { choices: [{ finish_reason: 'length', message: { content: '' } }] };

    it('retries the same request with twice the budget', async () => {
      const { doc } = fakeDoc(2);
      const run = translate(doc, {
        batchSize: 2, maxRetries: 1, quality: { review: false },
      });
      await flushScan();

      const first = await nextRequest('the batch');
      const budget = bodyOf(first).max_tokens;
      first.flush(ALL_REASONING);

      const retry = await nextRequest('the bigger-budget retry');
      expect(bodyOf(retry).max_tokens).toBe(budget * 2);
      expect(userMessageOf(retry)).toBe(userMessageOf(first));
      retry.flush(chat(wireFor([1, 2])));

      expect(await run).toBe('T1\nT2');
    }, 20_000);

    it('gives up at four times the budget and names the cause', async () => {
      const { doc } = fakeDoc(2);
      const run = translate(doc, {
        batchSize: 2, maxRetries: 5, quality: { review: false },
      });
      await flushScan();

      const first = await nextRequest('the batch');
      const budget = bodyOf(first).max_tokens;
      first.flush(ALL_REASONING);
      (await nextRequest('the 2x retry')).flush(ALL_REASONING);
      const last = await nextRequest('the 4x retry');
      expect(bodyOf(last).max_tokens).toBe(budget * 4);
      last.flush(ALL_REASONING);

      await expect(run).rejects.toThrow(
        /spent all of it reasoning.*\(block 1\)/,
      );
      // Doubling stops at the cap instead of spending the retry budget too.
      await settle();
      expect(seen.length).toBe(4);
    }, 20_000);
  });

  // Ported from the "deterministic repair of an accepted batch" section of
  // cli/tests/test_batch_runner.py. The web tree keeps finalizeBatch private,
  // so each case runs through the pipeline and proves repair never fails a file.
  describe('deterministic repair of an accepted batch', () => {
    // One long Arabic rendering of a two-line English cue, reused below.
    const LONG_AR = 'متى يتجاوز التعليق أو الفعل الخط الأحمر ويصبح شيئا آخر';

    /** Translate one batch of (source text, model output) pairs, and report
     * the repaired texts with everything the run notified about. */
    async function finalize(
      pairs: [string, string][],
      opts: { targetLang?: string; quality?: QualityOptions; glossary?: string } = {},
    ): Promise<{ texts: string[]; notices: string[] }> {
      const { doc, assembled } = fakeDoc(pairs.length);
      pairs.forEach(([source], i) => {
        doc.blocks[i]!.text = source;
      });
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: pairs.length,
        targetLang: opts.targetLang ?? 'Arabic',
        // These cases are about the deterministic repair; the retry the flags
        // would otherwise buy has its own block below.
        quality: { fixFlagged: false, ...opts.quality, onNotice: (m) => notices.push(m) },
      });

      await flushScan(opts.glossary);
      (await nextRequest('the batch')).flush(
        chat(pairs.map(([, output], i) => `${i + 1}\n${output}`).join('\n\n')),
      );
      await run;
      expect(assembled().map((b) => b.number)).toEqual(pairs.map((_, i) => i + 1));
      return { texts: assembled().map((b) => b.text), notices };
    }

    it('restores a dropped italic pair without a warning', async () => {
      const { texts, notices } = await finalize([['{\\i1}Hello there{\\i0}', 'مرحبا بك']]);
      expect(texts).toEqual(['{\\i1}مرحبا بك{\\i0}']);
      expect(notices).toEqual([]);
    });

    it('warns and keeps the translation when a tag change is unrepairable', async () => {
      const { texts, notices } = await finalize([['A <i>b</i> c', 'س ص ع']]);
      expect(texts).toEqual(['س ص ع']);
      expect(notices).toEqual(['Block 1: formatting tags changed (<i>,</i> -> )']);
    });

    it('names both inventories in the tag warning', async () => {
      const { notices } = await finalize([['<i>a</i>', '<b>x</b>']]);
      expect(notices).toEqual(['Block 1: formatting tags changed (<i>,</i> -> <b>,</b>)']);
    });

    it('restores the source line count', async () => {
      const source = 'it is not always easy to notice\nwhen the tide turns in the channel';
      const { texts, notices } = await finalize([[source, LONG_AR]]);
      expect(texts[0]!.split('\n').length).toBe(2);
      expect(notices).toEqual([]);
    });

    it('collapses an extra line back to the source count', async () => {
      // The target is always the source's line count, in both directions.
      const { texts } = await finalize([['one source line', 'مرحبا بك\nفي هذا المكان']]);
      expect(texts).toEqual(['مرحبا بك في هذا المكان']);
    });

    it('restores tags before the reflow so the pair is not split', async () => {
      const source = '{\\i1}it is not always easy\nto notice when the tide turns{\\i0}';
      const { texts } = await finalize([[source, LONG_AR]]);
      const [first, second] = texts[0]!.split('\n');
      expect(first!.startsWith('{\\i1}')).toBe(true);
      expect(second!.endsWith('{\\i0}')).toBe(true);
    });

    it('leaves the line count alone without reflow but still fixes punctuation', async () => {
      const { texts } = await finalize(
        [['one line\nsecond line', 'مرحبا, كيف حالك?']],
        { quality: { reflow: false } },
      );
      expect(texts).toEqual(['مرحبا، كيف حالك؟']);
    });

    it('repoints Arabic punctuation for an Arabic target', async () => {
      const { texts } = await finalize([['Hello, how are you?', 'مرحبا, كيف حالك?']]);
      expect(texts).toEqual(['مرحبا، كيف حالك؟']);
    });

    it('keeps ASCII punctuation for a Latin target', async () => {
      const { texts } = await finalize(
        [['Hello, how are you?', 'Hola, ¿cómo estás?']],
        { targetLang: 'Spanish' },
      );
      expect(texts).toEqual(['Hola, ¿cómo estás?']);
    });

    it('picks the line length from the target language', async () => {
      // Japanese norms cap a line at 16 characters, not the Latin 42.
      const longJa = 'これはとても長い一行の字幕です。もう一度お願いします。';
      const { texts } = await finalize([['a'.repeat(30), longJa]], { targetLang: 'Japanese' });
      expect(Math.max(...texts[0]!.split('\n').map((l) => l.length))).toBeLessThanOrEqual(16);
    });

    it('lets maxLineChars override the script default', async () => {
      const line = Array(6).fill('word').join(' '); // 29 chars: under Arabic's 42, over 20
      expect((await finalize([['short', line]])).texts).toEqual([line]);

      const { texts } = await finalize([['short', line]], { quality: { maxLineChars: 20 } });
      expect(texts).toEqual(['word word word\nword word word']);
    });

    it('gives both speakers back the dash the model dropped', async () => {
      const { texts, notices } = await finalize([
        ['- Where is the manifest?\n- Under the radio.', 'أين البيان؟\nتحت الراديو.'],
      ]);
      expect(texts).toEqual(['- أين البيان؟\n- تحت الراديو.']);
      expect(notices).toEqual([]);
    });

    it('restores a dash before the reflow, so a turn is never split in two', async () => {
      // A cue that opens a speaker turn is left long rather than broken in
      // two; the reflow only knows that if the dash is already back.
      const output = 'كل صندوق على هذا السطح معدود وموقع عليه بالكامل';
      const { texts, notices } = await finalize([
        ['- Every crate on this deck is counted and signed for.', output],
      ]);
      expect(texts).toEqual([`- ${output}`]);
      expect(notices).toEqual([]);
    });

    it('warns when two speakers were merged into one turn', async () => {
      const { texts, notices } = await finalize([
        ['- Did you sign the log?\n- Nadia signed it.', 'هل وقعت السجل؟ نادية وقعته.'],
      ]);
      // Not restorable: nothing says which half of the line lost its dash.
      expect(texts[0]).not.toContain('- ');
      expect(notices).toEqual(['Block 1: speaker dashes changed (2 -> 0)']);
    });

    it('warns when a script neither language uses leaks in', async () => {
      const { notices } = await finalize([['He said hello.', 'قال 你好.']]);
      expect(notices).toEqual([
        "Block 1: han characters appear in the translation ('你好')",
      ]);
    });

    it('warns when a target word is welded to Latin letters', async () => {
      const { texts, notices } = await finalize([
        ['Meet me at the motel.', 'قابلني في المotel.'],
      ]);
      // A warning and a retry hint: the cue itself is shipped as it came.
      expect(texts).toEqual(['قابلني في المotel.']);
      expect(notices).toEqual([
        "Block 1: 'المotel' welds arabic to latin with no separator",
      ]);
    });

    it('reports a suspected cross-cue shift once', async () => {
      const { notices } = await finalize([
        ['when the tide turns in the channel,',
          'متى يتجاوز التعليق أو الفعل الخط الأحمر ويصبح تحرشا صريحا ومؤذيا جدا'],
        ['gates must close and lock.', 'الخط.'],
      ]);
      expect(notices).toEqual([
        "Blocks 1-2: 'الخط' appears in both lines - text may have shifted between them",
      ]);
    });

    it('restores the sentence-final mark the model swapped out', async () => {
      const { texts } = await finalize([["That ship has sailed !", 'هذا ما قالت.']]);
      expect(texts).toEqual(['هذا ما قالت!']);
    });

    it('names a glossary term the batch did not render as pinned', async () => {
      const { notices } = await finalize(
        [['A safety briefing session today', 'ندوة عن المضايقة الجنسية اليوم']],
        {
          quality: { review: false },
          glossary: '<terms>\nsafety briefing => جلسة السلامة\n</terms>',
        },
      );
      expect(notices).toEqual([
        "Block 1: glossary term 'safety briefing' was not rendered as "
        + "'جلسة السلامة'",
      ]);
    });

    it('stays quiet when the pinned rendering is there', async () => {
      const { notices } = await finalize(
        [['A safety briefing session today', 'ندوة جلسة السلامة اليوم']],
        {
          quality: { review: false },
          glossary: '<terms>\nsafety briefing => جلسة السلامة\n</terms>',
        },
      );
      expect(notices).toEqual([]);
    });

    it('strips vocalisation from the cues that switched register', async () => {
      // File-level: cue 1 is the only vocalized one, so it is the outlier.
      const { texts } = await finalize([
        ['Come in and take a seat', 'جِبْهُ وَأَعْطِنِي قَبْلَةً'],
        ['Hello there my friend', 'مرحبا يا صديقي'],
        ['Goodbye for now my friend', 'وداعا يا صديقي'],
      ]);
      expect(texts[0]).toBe('جبه وأعطني قبلة');
      expect(texts[1]).toBe('مرحبا يا صديقي');
    });

    it('leaves a file that is vocalized throughout alone', async () => {
      const { texts } = await finalize([
        ['Come in and take a seat', 'جِبْهُ وَأَعْطِنِي قَبْلَةً'],
        ['Hello there my friend', 'مَرْحَبًا يَا صَدِيقِي'],
        ['Goodbye for now my friend', 'وَدَاعًا يَا صَدِيقِي'],
      ]);
      expect(texts[0]).toBe('جِبْهُ وَأَعْطِنِي قَبْلَةً');
    });

    it('leaves the reporting to the root when the batch splits', async () => {
      // Otherwise a split batch would warn twice about the same block.
      const { doc } = fakeDoc(4);
      for (const b of doc.blocks) b.text = 'A <i>b</i> c is a long enough line';
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 4,
        targetLang: 'Arabic',
        quality: { fixFlagged: false, onNotice: (m) => notices.push(m) },
      });
      await flushScan();

      // Three blocks for a four-block batch, twice: a deterministic merge.
      for (let i = 0; i < 2; i++) {
        (await nextRequest(`batch attempt ${i + 1}`)).flush(chat(wireFor([1, 2, 3])));
      }
      (await nextRequest('the left half')).flush(chat(wireFor([1, 2])));
      (await nextRequest('the right half')).flush(chat(wireFor([3, 4])));
      await run;

      expect(notices.filter((m) => m.includes('formatting tags changed')).length).toBe(4);
    }, 20_000);
  });

  describe('repair of flagged batches', () => {
    // An <i> pair the model swapped for nothing: unrepairable locally, so it
    // is exactly the kind of flag the retry exists for.
    const TAGGED = 'A <i>b</i> c';

    /** One flagged batch, answered first badly then however the test says. */
    async function runWithRetry(
      retryText: string | null,
      quality: QualityOptions = {},
    ): Promise<{ texts: string[]; notices: string[]; repair?: TestRequest }> {
      const { doc, assembled } = fakeDoc(1);
      doc.blocks[0]!.text = TAGGED;
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 1,
        targetLang: 'Arabic',
        quality: { review: false, ...quality, onNotice: (m) => notices.push(m) },
      });
      await flushScan();
      (await nextRequest('the batch')).flush(chat('1\nس ص ع'));
      let repair: TestRequest | undefined;
      if (retryText !== null) {
        repair = await nextRequest('the flagged-batch retry');
        repair.flush(chat(`1\n${retryText}`));
      }
      await run;
      return { texts: assembled().map((b) => b.text), notices, repair };
    }

    it('re-issues the batch naming the exact problem', async () => {
      const { repair } = await runWithRetry('<i>س</i>');
      expect(systemPromptOf(repair!)).toBe(SYSTEM_PROMPT);
      const message = userMessageOf(repair!);
      expect(message).toContain(
        'The previous attempt had these problems - fix ONLY these, keep '
        + 'everything else identical:\n'
        + '- block 1: the formatting tags <i>...</i> were dropped',
      );
      // The retry sees the request the first attempt saw, problems aside.
      expect(message).toContain('Translate from English to Arabic:');
    });

    it('keeps a retry that leaves fewer flags behind', async () => {
      const { texts, notices } = await runWithRetry('<i>س</i>');
      expect(texts).toEqual(['<i>س</i>']);
      expect(notices).toContain('Block 1: re-translated after 1 flag(s); 0 left');
    });

    it('keeps the original when the retry is no better', async () => {
      const { texts, notices } = await runWithRetry('س ص ع');
      expect(texts).toEqual(['س ص ع']);
      expect(notices).not.toContain(expect.stringMatching(/re-translated/));
    });

    it('keeps the original when the retry does not even validate', async () => {
      const { texts } = await runWithRetry('');
      expect(texts).toEqual(['س ص ع']);
    });

    it('gives a cue the batch retry could not fix one more chance on its own', async () => {
      // The real run kept ten of eighteen retried batches unchanged: inside a
      // batch the model let the named correction slide. Alone, it follows it.
      const { doc, assembled } = fakeDoc(2);
      doc.blocks[0]!.text = TAGGED;
      doc.blocks[1]!.text = TAGGED;
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        targetLang: 'Arabic',
        quality: { review: false, contextOverlap: 0, onNotice: (m) => notices.push(m) },
      });
      await flushScan();
      (await nextRequest('the batch')).flush(chat('1\nس ص ع\n\n2\nس ص ع'));
      // The batch retry drops the tags again, so it is not kept.
      (await nextRequest('the batch retry')).flush(chat('1\nس ص ع\n\n2\nس ص ع'));
      for (let i = 0; i < 2; i++) {
        const alone = await nextRequest(`cue ${i + 1} alone`);
        const message = userMessageOf(alone);
        const number = Number(/(?:^|\n)(\d+)\n/.exec(message.split(FIX_FLAGGED_RULE)[1] ?? message)![1]);
        expect(message).toContain(
          `- block ${number}: the formatting tags <i>...</i> were dropped`);
        alone.flush(chat(`${number}\n<i>س</i>`));
      }
      await run;
      expect(assembled().map((b) => b.text)).toEqual(['<i>س</i>', '<i>س</i>']);
      expect(notices).toContain('Repaired 2/2 flagged line(s) on their own');
    });

    it('does not offer a one-cue batch the same retry twice', async () => {
      // With one cue per batch the batch retry already was the cue alone.
      const before = seen.length;
      await runWithRetry('س ص ع');
      // Scan, the batch, its one retry, and nothing more.
      expect(seen.length - before).toBe(3);
    });

    it('makes no extra call at all when the repair is switched off', async () => {
      const before = seen.length;
      const { texts } = await runWithRetry(null, { fixFlagged: false });
      expect(texts).toEqual(['س ص ع']);
      // Scan plus one batch, and nothing else.
      expect(seen.length - before).toBe(2);
    });

    it('runs the retry through the same finaliser the first pass uses', async () => {
      // The reply wraps the cue three times over, so it ships two renderless
      // pairs unless the finaliser sees it — and it only counts as an
      // improvement if the flags are recounted on the finalised text.
      const { texts, notices } = await runWithRetry('<i></i><i>س</i><i></i>');
      expect(texts).toEqual(['<i>س</i>']);
      expect(notices).toContain('Block 1: re-translated after 1 flag(s); 0 left');
    });

    it('feeds a dash the retry still drops back into the flag count', async () => {
      const { doc, assembled } = fakeDoc(1);
      doc.blocks[0]!.text = '- Did you sign the log?\n- Nadia signed it.';
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 1,
        targetLang: 'Arabic',
        quality: { review: false, onNotice: (m) => notices.push(m) },
      });
      await flushScan();
      (await nextRequest('the batch')).flush(chat('1\nهل وقعت السجل؟ نادية وقعته.'));
      const repair = await nextRequest('the flagged-batch retry');
      expect(userMessageOf(repair)).toContain(
        'block 1: the source opens 2 line(s) with a dialogue dash and the '
        + 'translation opens 0',
      );
      repair.flush(chat('1\n- هل وقعت السجل؟\n- نادية وقعته.'));
      await run;

      expect(assembled()[0]!.text).toBe('- هل وقعت السجل؟\n- نادية وقعته.');
      expect(notices).toContain('Block 1: re-translated after 1 flag(s); 0 left');
    });

    it('caps the batches it re-issues and says how many it left alone', async () => {
      const { doc } = fakeDoc(6);
      for (const b of doc.blocks) b.text = TAGGED;
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 1,
        targetLang: 'Arabic',
        quality: { review: false, onNotice: (m) => notices.push(m) },
      });
      await flushScan();
      for (let n = 1; n <= 6; n++) {
        (await nextRequest(`batch ${n}`)).flush(chat(`${n}\nس ص ع`));
      }
      // One cause across six batches is systematic, so the wider ceiling
      // applies — max(2, 25% of 6) = 2 here, the same two retries.
      // One worker, so the second only goes out once the first is answered.
      for (let n = 1; n <= 2; n++) {
        (await nextRequest(`retry ${n}`)).flush(chat(`${n}\n<i>س</i>`));
      }
      // The four the cap left are then offered the cue pass, under its own
      // cap of max(2, 5% of 6) = 2: cues 3 and 4, earliest first.
      for (let n = 3; n <= 4; n++) {
        const alone = await nextRequest(`cue ${n} alone`);
        expect(userMessageOf(alone)).toContain(`\n${n}\nA <i>b</i> c`);
        alone.flush(chat(`${n}\n<i>س</i>`));
      }
      await run;

      expect(notices).toContain(
        '6 flagged batch(es) across 1 cause(s); repairing 2 (cap 2), leaving 4',
      );
      expect(notices).toContain(
        '4 flagged line(s) left after the batch retries; re-translating 2 on '
        + 'their own (cap 2), leaving 2',
      );
      expect(notices).toContain('Repaired 2/2 flagged line(s) on their own');
    }, 20_000);

    it('spends the wider ceiling on a failure that repeats, rarest cause first', async () => {
      // Twelve batches: eleven lost the same tag pair, one lost a speaker
      // dash. 25% of 12 = 3 retries, and the lone dash goes first — the
      // positional cap would have spent both its slots on batches 1 and 2.
      const { doc } = fakeDoc(12);
      for (const b of doc.blocks) b.text = TAGGED;
      doc.blocks[11]!.text = '- Did you sign?\n- Nadia did.';
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 1,
        targetLang: 'Arabic',
        quality: { review: false, onNotice: (m) => notices.push(m) },
      });
      await flushScan();
      for (let n = 1; n <= 11; n++) {
        (await nextRequest(`batch ${n}`)).flush(chat(`${n}\nس ص ع`));
      }
      (await nextRequest('batch 12')).flush(chat('12\nهل وقعت؟ نادية وقعت.'));

      const repaired: string[] = [];
      for (let i = 0; i < 3; i++) {
        const retry = await nextRequest(`retry ${i + 1}`);
        const number = /Translate from English to Arabic:\n\n(\d+)\n/
          .exec(userMessageOf(retry))![1]!;
        repaired.push(number);
        retry.flush(chat(`${number}\nس`));
      }
      // None of those retries helped, so the cue pass takes the earliest two
      // cues whose one-cue batch was not already retried: 3 and 4.
      for (let n = 3; n <= 4; n++) {
        (await nextRequest(`cue ${n} alone`)).flush(chat(`${n}\nس`));
      }
      await run;

      expect(repaired).toEqual(['1', '2', '12']);
      expect(notices).toContain(
        '9 flagged line(s) left after the batch retries; re-translating 2 on '
        + 'their own (cap 2), leaving 7',
      );
      expect(notices).toContain(
        '12 flagged batch(es) across 2 cause(s); repairing 3 (cap 3), leaving 9',
      );
    }, 20_000);
  });

  describe('the adequacy spot check', () => {
    /** Two batches, both sampled; `back` answers each back-translation. */
    // Long enough to be scored (cues under five content words are not) and
    // unlike each other, so the consistency check has no motif to weigh in on.
    const SOURCES = [
      'the harbour master signs the log book at eleven tonight',
      'a stranger left three parcels on the kitchen table',
      'nobody told the driver where the road actually ends',
      'her brother keeps the garden keys under the loose brick',
    ];

    async function runAdequacy(
      back: string | ((n: number) => string), quality: QualityOptions = {},
    ): Promise<{ notices: string[]; requests: TestRequest[] }> {
      const { doc } = fakeDoc(4);
      doc.blocks.forEach((b, i) => { b.text = SOURCES[i]; });
      const backFor = typeof back === 'string' ? () => back : back;
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        quality: {
          review: false, fixFlagged: false, verifyAdequacy: true,
          ...quality,
          onNotice: (m) => notices.push(m),
        },
      });
      await flushScan();
      (await nextRequest('batch 1')).flush(chat(wireFor([1, 2])));
      (await nextRequest('batch 2')).flush(chat(wireFor([3, 4])));
      const requests: TestRequest[] = [];
      for (let i = 0; i < 2; i++) {
        const req = await nextRequest(`back-translation ${i + 1}`);
        requests.push(req);
        const [a, b] = [i * 2 + 1, i * 2 + 2];
        req.flush(chat(`${a}\n${backFor(a)}\n\n${b}\n${backFor(b)}`));
      }
      await run;
      return { notices, requests };
    }

    it('leaves a cue too short to score fairly alone', async () => {
      const { doc } = fakeDoc(2);
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 2,
        quality: { review: false, fixFlagged: false, verifyAdequacy: true,
          onNotice: (m) => notices.push(m) },
      });
      await flushScan();
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));
      (await nextRequest('back-translation')).flush(chat('1\nNothing like it\n\n2\nNothing like it'));
      await run;
      // "Line 1" is two words; one changed word would already read as 50% lost.
      expect(notices).toEqual([]);
    });

    it('asks for the target text back in the source language', async () => {
      const { requests } = await runAdequacy('Line 1');
      expect(systemPromptOf(requests[0]!)).toBe(BACK_TRANSLATION_SYSTEM_PROMPT);
      expect(userMessageOf(requests[0]!)).toContain('Translate back to English:');
      // It is the translation that goes back, not the source.
      expect(userMessageOf(requests[0]!)).toContain('T1');
    });

    it('flags a cue whose meaning did not survive the round trip', async () => {
      const { notices } = await runAdequacy('Nothing like it');
      expect(notices).toContain(
        'Block 1: the back-translation recovered only 0% of the source wording',
      );
    });

    it('stays quiet when the wording comes back', async () => {
      const { notices } = await runAdequacy((n) => SOURCES[n - 1]);
      expect(notices).toEqual([]);
    });

    it('makes no back-translation call unless it is asked for', async () => {
      const { doc } = fakeDoc(4);
      const before = seen.length;
      const run = translate(doc, { batchSize: 2, quality: { review: false } });
      await flushScan();
      (await nextRequest('batch 1')).flush(chat(wireFor([1, 2])));
      (await nextRequest('batch 2')).flush(chat(wireFor([3, 4])));
      await run;
      await settle();
      expect(seen.length - before).toBe(3);
    });

    it('hands what it flagged to the repair pass', async () => {
      const { doc } = fakeDoc(2);
      doc.blocks.forEach((b, i) => { b.text = SOURCES[i]; });
      const run = translate(doc, {
        batchSize: 2,
        quality: { review: false, verifyAdequacy: true },
      });
      await flushScan();
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2])));
      (await nextRequest('the back-translation')).flush(chat('1\nnope\n\n2\nnope'));
      const repair = await nextRequest('the flagged-batch retry');
      expect(userMessageOf(repair)).toContain(
        '- block 1: the translation leaves out part of what the source says',
      );
      repair.flush(chat(wireFor([1, 2], 'R')));
      await run;
    });
  });

  describe('full speaker attribution', () => {
    const CAST = '<characters>\nAlice => Alice | unknown\nBob => Bob | unknown\n'
      + '</characters>\n<scenes>\n1-4 => Alice talks to Bob\n</scenes>';

    /** Four blocks naming the cast, so the scan's scene survives grounding. */
    function castDoc(text: string): SubtitleDocument {
      const { doc } = fakeDoc(4);
      for (const b of doc.blocks) b.text = text;
      return doc;
    }

    it('stays off the wire when the target does not inflect for gender', async () => {
      const run = translate(castDoc('Alice greets Bob warmly'), {
        batchSize: 4, quality: { review: false, fixFlagged: false },
      });
      await flushScan(CAST);
      const next = await nextRequest('the batch');
      expect(systemPromptOf(next)).toBe(SYSTEM_PROMPT);
      next.flush(chat(wireFor([1, 2, 3, 4])));
      await run;
    });

    it('attributes an unknown-gender scene when the target inflects', async () => {
      // The gate used to need two KNOWN genders, so the scenes an Arabic run
      // most needed answering — the ones the scan left ambiguous — were
      // exactly the ones it skipped.
      const run = translate(castDoc('Alice greets Bob warmly'), {
        batchSize: 4,
        targetLang: 'Arabic',
        quality: { review: false, fixFlagged: false },
      });
      await flushScan(CAST);
      const attribution = await nextRequest('the attribution call');
      expect(systemPromptOf(attribution)).toBe(ATTRIBUTION_SYSTEM_PROMPT);
      attribution.flush(chat('1=Alice\n2=Bob\n3=Alice\n4=Bob'));
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2, 3, 4])));
      await run;
    });

    it('attributes a two-participant scene whatever the genders', async () => {
      const run = translate(castDoc('Alice greets Bob warmly'), {
        batchSize: 4,
        quality: { review: false, fixFlagged: false, fullAttribution: true },
      });
      await flushScan(CAST);
      const attribution = await nextRequest('the attribution call');
      expect(systemPromptOf(attribution)).toBe(ATTRIBUTION_SYSTEM_PROMPT);
      attribution.flush(chat('1=Alice\n2=Bob\n3=Alice\n4=Bob'));
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2, 3, 4])));
      await run;
    });

    it('attributes a lone speaker when the target inflects for gender', async () => {
      const solo = '<characters>\nAlice => Alice | unknown\n</characters>\n'
        + '<scenes>\n1-4 => Alice thinks aloud\n</scenes>';
      const run = translate(castDoc('Alice thinks aloud again'), {
        batchSize: 4,
        targetLang: 'Arabic',
        quality: { review: false, fixFlagged: false, fullAttribution: true },
      });
      await flushScan(solo);
      const attribution = await nextRequest('the attribution call');
      expect(systemPromptOf(attribution)).toBe(ATTRIBUTION_SYSTEM_PROMPT);
      attribution.flush(chat('1=Alice\n2=Alice\n3=Alice\n4=Alice'));
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2, 3, 4])));
      await run;
    });
  });

  describe('target-variant drift', () => {
    /** Four cues answered in one batch, `colloquial` of them in Egyptian. */
    async function runVariant(
      colloquial: number, quality: QualityOptions = {},
    ): Promise<string[]> {
      const { doc } = fakeDoc(4);
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 4,
        targetLang: 'Arabic',
        quality: {
          review: false, fixFlagged: false, ...quality,
          onNotice: (m) => notices.push(m),
        },
      });
      await flushScan();
      const reply = [1, 2, 3, 4]
        .map((n) => `${n}\n${n <= colloquial ? 'مش عارف' : 'لا أعرف'}`)
        .join('\n\n');
      (await nextRequest('the batch')).flush(chat(reply));
      await run;
      return notices;
    }

    it('names the variant a finished file drifted into', async () => {
      // Fluent, and unusable against a Modern Standard Arabic target: no
      // per-batch check can see it, because every cue is fine on its own.
      expect(await runVariant(2)).toContain(
        'Output looks like Egyptian rather than the standard written form '
        + '(2 of 4 lines). Pass --dialect to ask for it deliberately, or rerun.',
      );
    });

    it('says nothing about the variant the run asked for', async () => {
      expect(await runVariant(4, { dialect: 'Egyptian Arabic' }))
        .not.toContain(expect.stringMatching(/Output looks like/));
    });

    it('says nothing about a file in the standard written form', async () => {
      expect(await runVariant(0))
        .not.toContain(expect.stringMatching(/Output looks like/));
    });
  });

  describe('file-level phrase consistency', () => {
    const SPLIT_MESSAGE =
      "'night shift' is rendered 4 different ways across 4 lines; "
      + 'no wording is shared by all of them';

    /** One cue per batch, all repeating one phrase, answered in turn. */
    async function runPhrase(
      renderings: string[], quality: QualityOptions = {},
    ): Promise<{ notices: string[]; repairs: TestRequest[] }> {
      const { doc } = fakeDoc(renderings.length);
      doc.blocks.forEach((b, i) => { b.text = `a${i + 1} night shift b${i + 1}`; });
      const notices: string[] = [];
      const run = translate(doc, {
        batchSize: 1,
        targetLang: 'Arabic',
        quality: { review: false, ...quality, onNotice: (m) => notices.push(m) },
      });
      await flushScan();
      for (let n = 1; n <= renderings.length; n++) {
        (await nextRequest(`batch ${n}`)).flush(chat(`${n}\n${renderings[n - 1]}`));
      }
      const repairs: TestRequest[] = [];
      // Four batches under one cause: the systematic ceiling, max(2, 25%) = 2.
      if (quality.fixFlagged !== false) {
        for (let i = 0; i < 2; i++) {
          const repair = await nextRequest(`the repair ${i + 1}`);
          repairs.push(repair);
          const number = /Translate from English to Arabic:\n\n(\d+)\n/
            .exec(userMessageOf(repair))![1]!;
          repair.flush(chat(`${number}\nنوبة الليل`));
        }
        // The two batches the cap left keep their phrase flag, so their cues
        // are re-issued alone, under the cue pass's own cap of two.
        for (let i = 0; i < 2; i++) {
          const alone = await nextRequest(`cue ${i + 3} alone`);
          const number = /Translate from English to Arabic:\n\n(\d+)\n/
            .exec(userMessageOf(alone))![1]!;
          alone.flush(chat(`${number}\nنوبة الليل`));
        }
      }
      await run;
      return { notices, repairs };
    }

    const FOUR_WAYS = ['ألف', 'باء', 'جيم', 'دال'];
    const ONE_WAY = ['نوبة الليل', 'نوبة المساء', 'نوبة أولى', 'نوبة أخيرة'];

    it('names a phrase the finished file rendered four ways', async () => {
      // Every cue is fine on its own: only the whole file shows the split.
      const { notices } = await runPhrase(FOUR_WAYS, { fixFlagged: false });
      expect(notices).toContain(SPLIT_MESSAGE);
    });

    it('says nothing while one wording runs through all of them', async () => {
      const { notices } = await runPhrase(ONE_WAY, { fixFlagged: false });
      expect(notices).not.toContain(expect.stringMatching(/is rendered/));
    });

    it('re-issues the batches it landed in, naming the phrase', async () => {
      const { notices, repairs } = await runPhrase(FOUR_WAYS);
      expect(userMessageOf(repairs[0]!)).toContain(
        "- block 1: the recurring phrase 'night shift' is rendered differently "
        + 'elsewhere in the file; use one wording for it',
      );
      // One phrase is one cause, however many batches it spans — which is what
      // buys it the wider ceiling instead of two of four batches.
      expect(notices).toContain(
        '4 flagged batch(es) across 1 cause(s); repairing 2 (cap 2), leaving 2',
      );
    });

    it('costs nothing when the repair is switched off', async () => {
      const before = seen.length;
      await runPhrase(FOUR_WAYS, { fixFlagged: false });
      // The scan and four batches: the check itself never calls the model.
      expect(seen.length - before).toBe(5);
    });
  });

  describe('what the run reports back', () => {
    it('counts the calls each pass made', async () => {
      const { doc } = fakeDoc(4);
      let stats: RunStats | undefined;
      const run = translate(doc, {
        batchSize: 2,
        quality: { review: false, onStats: (s) => { stats = s; } },
      });
      await flushScan();
      (await nextRequest('batch 1')).flush(chat(wireFor([1, 2])));
      (await nextRequest('batch 2')).flush(chat(wireFor([3, 4])));
      await run;

      expect(stats!.calls.scan).toBe(1);
      expect(stats!.calls.translate).toBe(2);
      expect(stats!.calls.review).toBe(0);
      expect(stats!.blocks).toBe(4);
      expect(stats!.elapsedMs).toBeGreaterThan(0);
    });

    it('counts a retried batch as two translate calls, not one', async () => {
      const { doc } = fakeDoc(2);
      let stats: RunStats | undefined;
      const run = translate(doc, {
        batchSize: 2,
        maxRetries: 3,
        quality: { review: false, onStats: (s) => { stats = s; } },
      });
      await flushScan();
      (await nextRequest('the first attempt')).flush(chat(wireFor([1])));
      (await nextRequest('the retry')).flush(chat(wireFor([1, 2])));
      await run;
      expect(stats!.calls.translate).toBe(2);
    }, 20_000);
  });

  describe('quality options reaching the model', () => {
    /** The scan and first-batch user messages for one run. */
    async function messagesFor(
      targetLang: string, quality: QualityOptions = {},
    ): Promise<{ scan: string; batch: string }> {
      const { doc } = fakeDoc(2);
      const run = translate(doc, { batchSize: 2, targetLang, quality });

      const scanReq = await nextRequest('the context scan');
      const scan = userMessageOf(scanReq);
      scanReq.flush(chat('no glossary here'));
      const batchReq = await nextRequest('the batch');
      const batch = userMessageOf(batchReq);
      batchReq.flush(chat(wireFor([1, 2])));
      await run;
      return { scan, batch };
    }

    it('carries the target script\'s own line limit', async () => {
      expect((await messagesFor('Japanese')).batch).toContain('at or under 16 characters');
      expect((await messagesFor('Arabic')).batch).toContain('at or under 42 characters');
    });

    it('carries an overridden line limit instead of the script default', async () => {
      const { batch } = await messagesFor('Arabic', { maxLineChars: 38 });
      expect(batch).toContain('at or under 38 characters');
    });

    it('adds the register line only when formality is not auto', async () => {
      const explicit = await messagesFor('German', { formality: 'formal' });
      expect(explicit.batch).toContain('Register: use formal address throughout.');
      expect((await messagesFor('German')).batch).not.toContain('Register: use');
    });

    it('seeds the scan with the phrases the file repeats', async () => {
      const { doc } = fakeDoc(4);
      const texts = [
        'The safety briefing session begins',
        'Report safety briefing immediately',
      ];
      doc.blocks.forEach((b, i) => {
        b.text = texts[i % 2]!;
      });
      // The placeholder reply renders the repeated phrase four ways, which the
      // file-level check would rightly re-issue; this test is about the scan.
      const run = translate(doc, {
        batchSize: 4, targetLang: 'Arabic', quality: { fixFlagged: false },
      });

      const scan = await nextRequest('the context scan');
      expect(userMessageOf(scan)).toContain(
        'Recurring phrases - give each one ONE target rendering and use it '
        + 'everywhere:\n- safety briefing',
      );
      scan.flush(chat('no glossary here'));
      (await nextRequest('the batch')).flush(chat(wireFor([1, 2, 3, 4])));
      await run;
    });

    it('says nothing about recurring phrases when the file repeats none', async () => {
      expect((await messagesFor('Arabic')).scan).not.toContain('Recurring phrases');
    });

    it('sends a dialect to both the scan and the batch', async () => {
      const { scan, batch } = await messagesFor('Arabic', { dialect: 'Egyptian Arabic' });
      expect(scan).toContain(
        'Target variant: Egyptian Arabic. Use it as the <register> instead of inferring one.',
      );
      expect(batch).toContain('Target variant: Egyptian Arabic. Use it consistently.');
      expect((await messagesFor('Arabic')).batch).not.toContain('Target variant');
    });
  });
});
