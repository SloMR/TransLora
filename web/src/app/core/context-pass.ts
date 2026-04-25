// One-shot prepass: scans the file once for cast/terms/scenes/register so every
// batch shares the same glossary. Fails silently to an empty FileContext.

import { ATTRIB_MIN_BLOCKS, MIN_NAME_LEN } from './constants';
import { SubtitleBlock } from './srt-parser';

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

export interface SceneHint {
  start: number;
  end: number;
  description: string;
  participants: string[];
  attribution: Record<number, string>; // Per-block speaker map (block_number -> character source name).
}

export class FileContext {
  constructor(
    public register = '',
    public characters: CharacterHint[] = [],
    public terms: TermHint[] = [],
    public scenes: SceneHint[] = [],
    public notes: string[] = [],
  ) {}

  isEmpty(): boolean {
    return !(this.register || this.characters.length || this.terms.length
      || this.scenes.length || this.notes.length);
  }

  // Glossary slice scoped to this batch. Register/notes are file-wide.
  renderForBatch(batch: SubtitleBlock[]): string {
    const text = batch.map((b) => b.text).join('\n');
    const scenes = scenesOverlapping(this.scenes, batch);
    // Include characters named in the batch AND scene participants — the
    // latter covers speakers who address each other as "you" without
    // vocatives, so the translator still learns their gender.
    const sceneNames = new Set<string>(scenes.flatMap((s) => s.participants));
    const chars = this.characters.filter(
      (h) => findWord(text, h.source) >= 0 || sceneNames.has(h.source),
    );
    const terms = this.terms.filter((h) => findWord(text, h.source) >= 0);
    if (!this.register && !chars.length && !terms.length && !scenes.length && !this.notes.length) {
      return '';
    }

    const genderBy = new Map(this.characters.map((h) => [h.source.toLowerCase(), h.gender]));
    const parts: string[] = [];
    if (this.register) {
      parts.push(`Target register: ${this.register} (use consistently across every block)`);
    }
    if (chars.length) {
      parts.push('Characters:\n' + chars.map((h) => `- ${h.source} => ${h.target} (${h.gender})`).join('\n'));
    }
    if (terms.length) {
      parts.push('Terms:\n' + terms.map((h) => `- ${h.source} => ${h.target}`).join('\n'));
    }
    if (scenes.length) {
      parts.push(renderScenes(scenes, genderBy));
    }
    if (this.notes.length) {
      parts.push('Notes:\n' + this.notes.slice(0, 4).map((n) => `- ${n}`).join('\n'));
    }
    return parts.join('\n\n');
  }
}

function scenesOverlapping(scenes: SceneHint[], batch: SubtitleBlock[]): SceneHint[] {
  if (!scenes.length || !batch.length) return [];
  const first = batch[0].number;
  const last = batch[batch.length - 1].number;
  return scenes.filter((s) => s.end >= first && s.start <= last);
}

export function genderMark(g: Gender | undefined): string {
  return g === 'male' ? 'M' : g === 'female' ? 'F' : '';
}

function renderScenes(scenes: SceneHint[], genderBy: Map<string, Gender>): string {
  const lines: string[] = [];
  for (const s of scenes) {
    const tagged = s.participants.map((n) => {
      const mark = genderMark(genderBy.get(n.toLowerCase()));
      return mark ? `${n} (${mark})` : n;
    }).join(', ');
    const prefix = `- Blocks ${s.start}-${s.end}:`;
    lines.push(tagged ? `${prefix} [${tagged}] — ${s.description}` : `${prefix} ${s.description}`);
    const nums = Object.keys(s.attribution).map(Number).sort((a, b) => a - b);
    if (nums.length) {
      lines.push('    speakers: ' + nums.map((n) => `${n}=${s.attribution[n]}`).join(' '));
    }
  }
  return (
    "Scene guidance — each entry applies ONLY to its listed block range. " +
    "Participants and genders in [brackets]; a 'speakers:' line names the " +
    "speaker per block so you pick the right gender for the ADDRESSEE:\n" +
    lines.join('\n')
  );
}

// Case-insensitive whole-word search with Unicode-aware boundaries.
// Works for Latin, Arabic, CJK, etc. Returns first match index or -1.
function findWord(text: string, word: string): number {
  if (!text || !word) return -1;
  const haystack = text.toLowerCase();
  const needle = word.toLowerCase();
  const nlen = needle.length;
  let i = 0;
  while (i <= haystack.length - nlen) {
    const j = haystack.indexOf(needle, i);
    if (j < 0) return -1;
    const before = j > 0 ? text[j - 1] : '';
    const after = j + nlen < text.length ? text[j + nlen] : '';
    if (!extendsWord(before) && !extendsWord(after)) return j;
    i = j + 1;
  }
  return -1;
}

function extendsWord(ch: string): boolean {
  if (!ch) return false;
  if (ch === '_') return true;
  return /\p{L}|\p{N}/u.test(ch);
}

function detectParticipants(text: string, characters: CharacterHint[]): string[] {
  // Match source AND target forms so descriptions in the target language
  // still resolve to the canonical source name.
  const aliases: Array<{ alias: string; name: string }> = [];
  for (const h of characters) {
    if (h.source.length >= MIN_NAME_LEN) aliases.push({ alias: h.source, name: h.source });
    if (h.target !== h.source && h.target.length >= MIN_NAME_LEN) {
      aliases.push({ alias: h.target, name: h.source });
    }
  }
  aliases.sort((a, b) => b.alias.length - a.alias.length);
  const firstAt = new Map<string, number>();
  for (const { alias, name } of aliases) {
    if (firstAt.has(name)) continue;
    const idx = findWord(text, alias);
    if (idx >= 0) firstAt.set(name, idx);
  }
  return [...firstAt.entries()].sort((a, b) => a[1] - b[1]).map(([n]) => n);
}

function formatScanLine(b: SubtitleBlock): string {
  return `[${b.number}] ${b.text.replace(/\n/g, ' ')}`;
}

// Stride-samples large files so characters introduced late still land in
// the glossary.
export function serializeForScan(
  blocks: SubtitleBlock[],
  charBudget: number,
): string {
  const total = blocks.reduce((sum, b) => sum + formatScanLine(b).length + 1, 0);
  if (total <= charBudget || blocks.length <= 1) {
    return blocks.map(formatScanLine).join('\n');
  }
  const takeN = Math.max(1, Math.floor((blocks.length * charBudget) / total));
  const step = blocks.length / takeN;
  const sampled: SubtitleBlock[] = [];
  for (let i = 0; i < takeN; i++) sampled.push(blocks[Math.floor(i * step)]);
  return sampled.map(formatScanLine).join('\n');
}

// Closing tag optional so a truncated reply still parses.
const SECTION_RE =
  /<(register|characters|terms|scenes|notes)>\s*([\s\S]*?)\s*(?=<\/\1>|<(?:register|characters|terms|scenes|notes)>|$)/gi;
const SCENE_RANGE_RE = /^(\d+)\s*(?:-\s*(\d+))?$/;
const ATTRIB_LINE_RE = /^\s*(\d+)\s*=\s*(.+?)\s*$/;

function stripBullet(line: string): string {
  return line.trim().replace(/^[-*•]\s*/, '').trim();
}

function splitOnce(s: string, sep: string): [string, string] {
  const i = s.indexOf(sep);
  return i < 0 ? [s, ''] : [s.slice(0, i), s.slice(i + sep.length)];
}

// Parse the tagged response. Tolerates whitespace and bullet markers.
export function parseContextResponse(text: string): FileContext {
  const sections: Record<string, string> = {};
  SECTION_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = SECTION_RE.exec(text || '')) !== null) {
    sections[m[1].toLowerCase()] = m[2];
  }

  const register = stripBullet((sections['register'] ?? '').split(/\s+/).join(' '));

  const characters: CharacterHint[] = [];
  for (const raw of (sections['characters'] ?? '').split('\n')) {
    const line = stripBullet(raw);
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
    const g: Gender = gender === 'male' || gender === 'female' ? gender : 'unknown';
    const src = srcPart.trim();
    if (src && tgt) characters.push({ source: src, target: tgt, gender: g });
  }

  const terms: TermHint[] = [];
  for (const raw of (sections['terms'] ?? '').split('\n')) {
    const line = stripBullet(raw);
    if (!line || !line.includes('=>')) continue;
    const [srcPart, tgtPart] = splitOnce(line, '=>');
    const src = srcPart.trim();
    const tgt = tgtPart.trim();
    if (src && tgt) terms.push({ source: src, target: tgt });
  }

  const scenes: SceneHint[] = [];
  for (const raw of (sections['scenes'] ?? '').split('\n')) {
    const line = stripBullet(raw);
    if (!line || !line.includes('=>')) continue;
    const [rangePart, descPart] = splitOnce(line, '=>');
    const desc = descPart.trim();
    const rm = SCENE_RANGE_RE.exec(rangePart.trim());
    if (!desc || !rm) continue;
    let start = parseInt(rm[1], 10);
    let end = rm[2] ? parseInt(rm[2], 10) : start;
    if (end < start) [start, end] = [end, start];
    scenes.push({
      start, end, description: desc,
      participants: detectParticipants(desc, characters),
      attribution: {},
    });
  }

  const notes: string[] = [];
  for (const raw of (sections['notes'] ?? '').split('\n')) {
    const line = stripBullet(raw);
    if (line) notes.push(line);
  }

  return new FileContext(
    register,
    characters.slice(0, 20),
    terms.slice(0, 10),
    scenes.slice(0, 80),
    notes.slice(0, 4),
  );
}

// Reconcile scene participants with what's in the source blocks. Block-text
// names are primary truth: description-named participants are kept only if
// grounded in the text; missed block-text names are appended.
export function enrichScenesWithBlockText(
  context: FileContext,
  blocks: SubtitleBlock[],
): FileContext {
  if (!context.scenes.length || !context.characters.length) return context;
  const byNum = new Map(blocks.map((b) => [b.number, b]));
  const enriched = context.scenes.map((scene) => {
    const parts: string[] = [];
    for (let n = scene.start; n <= scene.end; n++) {
      const b = byNum.get(n);
      if (b) parts.push(b.text);
    }
    const inText = detectParticipants(parts.join('\n'), context.characters);
    const inTextSet = new Set(inText);
    const kept = scene.participants.filter((p) => inTextSet.has(p));
    const seen = new Set(kept);
    for (const name of inText) {
      if (!seen.has(name)) {
        kept.push(name);
        seen.add(name);
      }
    }
    return { ...scene, participants: kept };
  });
  return new FileContext(
    context.register, context.characters, context.terms, enriched, context.notes,
  );
}

export function needsAttribution(scene: SceneHint): boolean {
  return (scene.end - scene.start + 1) >= ATTRIB_MIN_BLOCKS && scene.participants.length >= 1;
}

export function parseAttributionResponse(
  raw: string, scene: SceneHint, characters: CharacterHint[],
): Record<number, string> {
  const valid = new Set<string>(characters.map((h) => h.source));
  valid.add('unknown');
  const out: Record<number, string> = {};
  for (const line of (raw || '').split('\n')) {
    const m = ATTRIB_LINE_RE.exec(line);
    if (!m) continue;
    const n = parseInt(m[1], 10);
    const name = m[2].trim().replace(/^["']|["']$/g, '');
    if (n >= scene.start && n <= scene.end && valid.has(name)) out[n] = name;
  }
  return out;
}
