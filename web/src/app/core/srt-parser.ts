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

/**
 * Parse raw .srt content into SubtitleBlock array.
 */
export function parseSrt(content: string): SubtitleBlock[] {
  // Normalize line endings and strip BOM
  content = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
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

/**
 * Serialize SubtitleBlock array back to .srt file content.
 */
export function serializeSrt(blocks: SubtitleBlock[]): string {
  return (
    blocks.map((b) => `${b.number}\n${b.timestamp}\n${b.text}`).join('\n\n') +
    '\n'
  );
}

// Wire format sent to the LLM: number + text only. Timestamps are pure noise
// for the model — it echoes them back, and small models sometimes corrupt a
// digit. We strip them before sending and reattach from the original input.
export function serializeLite(blocks: SubtitleBlock[]): string {
  return blocks.map((b) => `${b.number}\n${b.text}`).join('\n\n') + '\n';
}

/**
 * Parse the wire-format response. Timestamps are left empty — callers reattach
 * them positionally from the original batch.
 */
export function parseLite(content: string): SubtitleBlock[] {
  content = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  if (content.charCodeAt(0) === 0xfeff) content = content.slice(1);

  const rawBlocks = content.trim().split(/\n\n+/);
  const blocks: SubtitleBlock[] = [];

  for (const raw of rawBlocks) {
    const lines = raw.trim().split('\n');
    if (lines.length < 1) continue;

    const number = parseInt(lines[0].trim(), 10);
    if (isNaN(number)) continue;

    const text = lines.slice(1).join('\n');
    blocks.push({ number, timestamp: '', text });
  }
  return blocks;
}

/**
 * Split blocks into batches of the given size.
 */
export function splitBatches(
  blocks: SubtitleBlock[],
  batchSize: number = 15
): SubtitleBlock[][] {
  const batches: SubtitleBlock[][] = [];
  for (let i = 0; i < blocks.length; i += batchSize) {
    batches.push(blocks.slice(i, i + batchSize));
  }
  return batches;
}

/**
 * Validate that translated output matches input structure.
 */
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

  for (let i = 0; i < inputBlocks.length; i++) {
    if (inputBlocks[i].timestamp !== outputBlocks[i].timestamp) {
      return {
        ok: false,
        error: `Timestamp modified at block ${inputBlocks[i].number}`,
      };
    }
  }

  return { ok: true, error: '' };
}
