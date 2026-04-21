// One-shot prepass: scans the file once for cast/terms/register so every batch
// shares the same glossary. Fails silently to an empty FileContext.

import { SubtitleBlock } from './srt-parser';

export const CONTEXT_SYSTEM_PROMPT = `You analyze a subtitle file before it is translated. Return a compact glossary for the translator to use when picking correct pronouns, consistent names, and a single consistent register.

Your reply MUST start with \`<register>\` and MUST contain all four sections below, in this exact order, with no other text before, between, or after them. No commentary. No code fences. No explanations. Tags only.

<register>
ONE LINE describing the target-language variant and formality the translator should use for the ENTIRE file.
</register>
<characters>
NAME => TARGET_NAME | GENDER
</characters>
<terms>
SOURCE => TARGET
</terms>
<notes>
- NOTE
</notes>

Rules:
- The <register> line names the specific target-language variant and formality (e.g. "Modern Standard Arabic, neutral", "Brazilian Portuguese, casual", "Simplified Mandarin, neutral", "Japanese, polite です/ます form"). Pick ONE and commit to it for the whole file. Base the choice on the source's tone; default to the standard written form of the target language unless the source is clearly colloquial.
- GENDER is "male", "female", or "unknown". Use "unknown" only when the text gives no signal at all.
- TARGET_NAME is how the character's name should appear in the target language (transliterated or localized).
- Include up to 20 named characters, 10 recurring proper terms or jargon, 4 brief notes on setting/tone.
- Leave a section empty (tags only) if nothing qualifies. Never omit a section.`;

// Sized so small-context models (4k-8k) still have room for prompt + output.
export const SCAN_CHAR_BUDGET = 12_000;
export const SCAN_MAX_TOKENS = 1500;

export type Gender = 'male' | 'female' | 'unknown';

export interface CharacterHint {
  source: string;
  target: string;
  gender: Gender;
}

export interface TermHint {
  source: string;
  target: string;
}

export class FileContext {
  constructor(
    public register = '',
    public characters: CharacterHint[] = [],
    public terms: TermHint[] = [],
    public notes: string[] = [],
  ) {}

  isEmpty(): boolean {
    return !(this.register || this.characters.length || this.terms.length || this.notes.length);
  }

  // Glossary slice scoped to names/terms present in this batch. Register and
  // notes are file-wide and always included if set.
  renderForBatch(batch: SubtitleBlock[]): string {
    const text = batch.map((b) => b.text).join('\n');
    const chars = this.characters.filter((h) => containsWord(text, h.source));
    const terms = this.terms.filter((h) => containsWord(text, h.source));
    if (!this.register && !chars.length && !terms.length && !this.notes.length) {
      return '';
    }

    const parts: string[] = [];
    if (this.register) {
      parts.push(`Target register: ${this.register} (use consistently across every block)`);
    }
    if (chars.length) {
      const lines = chars.map((h) => `- ${h.source} => ${h.target} (${h.gender})`);
      parts.push('Characters:\n' + lines.join('\n'));
    }
    if (terms.length) {
      const lines = terms.map((h) => `- ${h.source} => ${h.target}`);
      parts.push('Terms:\n' + lines.join('\n'));
    }
    if (this.notes.length) {
      const lines = this.notes.slice(0, 4).map((n) => `- ${n}`);
      parts.push('Notes:\n' + lines.join('\n'));
    }
    return parts.join('\n\n');
  }
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function containsWord(text: string, word: string): boolean {
  if (!word) return false;
  const re = new RegExp(`(?<!\\w)${escapeRegExp(word)}(?!\\w)`, 'i');
  return re.test(text);
}

// Stride-samples large files so characters introduced late still have a
// chance to land in the glossary.
export function serializeForScan(blocks: SubtitleBlock[]): string {
  const totalChars = blocks.reduce((sum, b) => sum + b.text.length + 1, 0);
  if (totalChars <= SCAN_CHAR_BUDGET || blocks.length <= 1) {
    return blocks.map((b) => b.text).join('\n');
  }

  const takeN = Math.max(1, Math.floor((blocks.length * SCAN_CHAR_BUDGET) / totalChars));
  const step = blocks.length / takeN;
  const sampled: SubtitleBlock[] = [];
  for (let i = 0; i < takeN; i++) {
    sampled.push(blocks[Math.floor(i * step)]);
  }
  return sampled.map((b) => b.text).join('\n');
}

const SECTION_RE = /<(register|characters|terms|notes)>\s*([\s\S]*?)\s*<\/\1>/gi;

function stripBullet(line: string): string {
  return line.trim().replace(/^[-*•]\s*/, '').trim();
}

// Parse tagged response. Tolerates extra whitespace and bullet markers.
export function parseContextResponse(text: string): FileContext {
  const sections: Record<string, string> = {};
  const src = text || '';
  SECTION_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = SECTION_RE.exec(src)) !== null) {
    sections[m[1].toLowerCase()] = m[2];
  }

  const rawRegister = sections['register'] ?? '';
  const register = stripBullet(rawRegister.split(/\s+/).join(' '));

  const characters: CharacterHint[] = [];
  for (const rawLine of (sections['characters'] ?? '').split('\n')) {
    const line = stripBullet(rawLine);
    if (!line || !line.includes('=>')) continue;
    const [srcPart, restPart] = splitOnce(line, '=>');
    let tgt: string, gender: string;
    if (restPart.includes('|')) {
      const idx = restPart.lastIndexOf('|');
      tgt = restPart.slice(0, idx).trim();
      gender = restPart.slice(idx + 1).trim().toLowerCase();
    } else {
      tgt = restPart.trim();
      gender = 'unknown';
    }
    const normalizedGender: Gender =
      gender === 'male' || gender === 'female' ? gender : 'unknown';
    const src2 = srcPart.trim();
    if (src2 && tgt) {
      characters.push({ source: src2, target: tgt, gender: normalizedGender });
    }
  }

  const terms: TermHint[] = [];
  for (const rawLine of (sections['terms'] ?? '').split('\n')) {
    const line = stripBullet(rawLine);
    if (!line || !line.includes('=>')) continue;
    const [srcPart, tgtPart] = splitOnce(line, '=>');
    const src2 = srcPart.trim();
    const tgt = tgtPart.trim();
    if (src2 && tgt) {
      terms.push({ source: src2, target: tgt });
    }
  }

  const notes: string[] = [];
  for (const rawLine of (sections['notes'] ?? '').split('\n')) {
    const line = stripBullet(rawLine);
    if (line) notes.push(line);
  }

  return new FileContext(
    register,
    characters.slice(0, 20),
    terms.slice(0, 10),
    notes.slice(0, 4),
  );
}

function splitOnce(s: string, sep: string): [string, string] {
  const i = s.indexOf(sep);
  if (i < 0) return [s, ''];
  return [s.slice(0, i), s.slice(i + sep.length)];
}
