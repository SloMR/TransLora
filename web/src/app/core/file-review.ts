import { FileReview, ReviewCue } from './file-types';
import { SubtitleDocument } from './subtitle-formats/types';
import { ReviewResult } from './translation.service';

/** Pairs every source cue with what it became, pins each surviving flag's
 * message to the cue it names, and keeps the flags the run raised and then
 * cleared: the run's own account of the file, cue by cue. */
export function buildReview(doc: SubtitleDocument, result: ReviewResult): FileReview {
  const flagsByBlock = new Map<number, string[]>();
  for (const flag of result.flags) {
    const list = flagsByBlock.get(flag.block) ?? [];
    list.push(flag.message);
    flagsByBlock.set(flag.block, list);
  }
  const cues: ReviewCue[] = doc.blocks.map((source, i) => ({
    number: source.number,
    timestamp: source.timestamp,
    source: source.text,
    target: result.blocks[i]?.text ?? '',
    flags: flagsByBlock.get(source.number) ?? [],
  }));
  const surviving = new Set(result.flags.map((f) => `${f.block}\u0000${f.cause}`));
  const repaired = result.raised
    .filter((f) => !surviving.has(`${f.block}\u0000${f.cause}`))
    .map((f) => ({ block: f.block, cause: f.cause, message: f.message }));
  return { cues, repaired };
}
