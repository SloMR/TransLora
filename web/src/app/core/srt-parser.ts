// The wire format the model is sent and the validation of what comes back:
// parsing and serializing SRT, the timestamp-free "lite" batch format, batch
// splitting, and the checks a reply must pass before it is accepted.

export interface SubtitleBlock {
  number: number;
  timestamp: string;
  text: string;
}

export interface ValidationResult {
  ok: boolean;
  error: string;
}

const TIMESTAMP_RE = /^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$/;
// Unanchored: catches a timestamp anywhere inside a returned block's text.
const TIMESTAMP_IN_TEXT_RE = /\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}/;
const BLOCK_NUMBER_RE = /^[+-]?\d+$/;

/** Cue text as the rest of the pipeline expects it: LF breaks, no trailing
 * blank lines. Interior blanks stay — serializeLite collapses those. Every
 * format parser runs its cue text through this. */
export function normalizeCueText(text: string): string {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  while (lines.length && !lines[lines.length - 1]!.trim()) lines.pop();
  return lines.join('\n');
}

export function parseSrt(content: string): SubtitleBlock[] {
  content = normalizeCueText(content);
  if (content.charCodeAt(0) === 0xfeff) {
    content = content.slice(1);
  }

  const rawBlocks = content.trim().split(/\n\n+/);
  const blocks: SubtitleBlock[] = [];

  for (const raw of rawBlocks) {
    const lines = raw.trim().split('\n');
    if (lines.length < 2) continue;

    const number = parseInt(lines[0].trim(), 10);
    if (isNaN(number)) continue;

    const timestamp = lines[1].trim();
    if (!TIMESTAMP_RE.test(timestamp)) continue;

    const text = lines.slice(2).join('\n');

    blocks.push({ number, timestamp, text });
  }

  return blocks;
}

export function serializeSrt(blocks: SubtitleBlock[]): string {
  return (
    blocks.map((b) => `${b.number}\n${b.timestamp}\n${b.text}`).join('\n\n') +
    '\n'
  );
}

// Wire format: number + text only. Timestamps are stripped before sending
// because small models sometimes corrupt them; callers reattach positionally.
// A blank line inside a cue is dropped: it would forge a block boundary.
export function serializeLite(blocks: SubtitleBlock[]): string {
  return blocks
    .map((b) => {
      const text = b.text.split('\n').filter((line) => line.trim()).join('\n');
      return `${b.number}\n${text}`;
    })
    .join('\n\n') + '\n';
}

export function parseLite(content: string): SubtitleBlock[] {
  content = normalizeCueText(content);
  if (content.charCodeAt(0) === 0xfeff) content = content.slice(1);

  const rawBlocks = content.trim().split(/\n\n+/);
  const blocks: SubtitleBlock[] = [];

  for (const raw of rawBlocks) {
    const lines = raw.trim().split('\n');
    if (lines.length < 1) continue;

    // Strict: "1) text" would otherwise parse as an empty block that validates.
    const numberLine = lines[0].trim();
    if (!BLOCK_NUMBER_RE.test(numberLine)) continue;
    const number = parseInt(numberLine, 10);

    const text = lines.slice(1).join('\n');
    blocks.push({ number, timestamp: '', text });
  }
  return blocks;
}

export function splitBatches(
  blocks: SubtitleBlock[],
  batchSize = 15
): SubtitleBlock[][] {
  const batches: SubtitleBlock[][] = [];
  for (let i = 0; i < blocks.length; i += batchSize) {
    batches.push(blocks.slice(i, i + batchSize));
  }
  return batches;
}

export function validateBatch(
  inputBlocks: SubtitleBlock[],
  outputBlocks: SubtitleBlock[]
): ValidationResult {
  if (inputBlocks.length !== outputBlocks.length) {
    return {
      ok: false,
      error: `Block count mismatch: expected ${inputBlocks.length}, got ${outputBlocks.length}`,
    };
  }

  for (let i = 0; i < inputBlocks.length; i++) {
    if (inputBlocks[i].number !== outputBlocks[i].number) {
      return {
        ok: false,
        error: `Block number mismatch at index ${i}: expected ${inputBlocks[i].number}, got ${outputBlocks[i].number}`,
      };
    }
  }

  // Wire-parsed blocks have no timestamp yet; compare only once one is set.
  for (let i = 0; i < inputBlocks.length; i++) {
    if (outputBlocks[i].timestamp && inputBlocks[i].timestamp !== outputBlocks[i].timestamp) {
      return {
        ok: false,
        error:
          `Timestamp modified at block ${inputBlocks[i].number}: ` +
          `expected '${inputBlocks[i].timestamp}', got '${outputBlocks[i].timestamp}'`,
      };
    }
  }

  // The model can shift blocks and leave a tail blank with count/numbers intact.
  for (let i = 0; i < inputBlocks.length; i++) {
    if (inputBlocks[i].text.trim() && !outputBlocks[i].text.trim()) {
      return {
        ok: false,
        error: `Empty output at block ${inputBlocks[i].number} (input was non-empty)`,
      };
    }
  }

  // Wire format has no timestamps, so one in the text is an invented line.
  for (let i = 0; i < inputBlocks.length; i++) {
    if (TIMESTAMP_IN_TEXT_RE.test(outputBlocks[i].text)) {
      return {
        ok: false,
        error: `Timestamp line leaked into text at block ${inputBlocks[i].number}`,
      };
    }
  }

  return { ok: true, error: '' };
}
