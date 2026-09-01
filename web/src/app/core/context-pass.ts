// The glossary the prepass produces: the cast, terms, scenes and register it
// holds, and the per-batch slice it renders into a translation prompt.

import {
  ATTRIB_MIN_BLOCKS,
  IDIOM_MAX_EXPANSION,
  IDIOM_MAX_TARGET_CHARS,
  MIN_NAME_LEN,
} from './constants';
import { contentWords, stripTags } from './repair';
import { SubtitleBlock } from './srt-parser';

// How many glossary terms the scan may return; the file repeats far more
// phrases than the old cap of 10 could hold.
export const MAX_TERMS = 25;
// Idioms are the scan's most expensive line to get right; past this the model
// starts listing ordinary phrases.
export const MAX_IDIOMS = 15;

// === Recurring-phrase seeding ===
// Word n-grams the file repeats, mined before the scan so the glossary is
// seeded from the file itself rather than from whatever the model noticed.
export const PHRASE_MIN_WORDS = 2;
export const PHRASE_MAX_WORDS = 5;
export const PHRASE_MIN_COUNT = 3;
// Shorter than this and a phrase is a fragment, not a rendering decision.
export const PHRASE_MIN_CHARS = 9;
export const PHRASE_LIMIT = 25;

// === File-level phrase consistency ===
// The consistency check mines its own phrases. PHRASE_MIN_CHARS guards the
// scan's term budget, where a fragment costs a slot; this check costs nothing
// and cannot afford to miss an eight-character motif like "the line". Seven
// admits fragments faster than it finds motifs.
export const CONSISTENCY_MIN_CHARS = 8;
// Cues a phrase must recur in before a split reads as a rendering decision
// rather than three paraphrases of three different sentences.
export const CONSISTENCY_MIN_OCCURRENCES = 4;

// A phrase made only of function words pins nothing worth pinning.
export const PHRASE_STOPWORDS = new Set([
  'a', 'about', 'all', 'am', 'an', 'and', 'any', 'are', 'as', 'at', 'be',
  'been', 'but', 'by', 'can', 'could', 'did', 'do', 'does', 'for', 'from',
  'get', 'got', 'had', 'has', 'have', 'he', 'her', 'here', 'him', 'his',
  'how', 'i', 'if', 'in', 'is', 'it', 'its', 'just', 'me', 'my', 'no', 'not',
  'of', 'on', 'or', 'our', 'out', 'she', 'so', 'than', 'that', 'the', 'their',
  'them', 'then', 'there', 'these', 'they', 'this', 'to', 'up', 'us', 'was',
  'we', 'were', 'what', 'when', 'where', 'which', 'who', 'will', 'with',
  'would', 'you', 'your',
]);

// Letters and digits, with internal apostrophes kept so "that's" stays one word.
const PHRASE_WORD_RE = /[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu;

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

/** Which table a drifting entry came from: the wording and the repair cause
 * differ, but the check is the same one. */
export type DriftKind = 'term' | 'name';

/** A pinned term or character name the batch's output never used. */
export interface TermDrift {
  block: number;
  kind: DriftKind;
  source: string;
  target: string;
}

const DRIFT_LABELS: Record<DriftKind, string> = {
  term: 'glossary term',
  name: 'character name',
};

/** How a drift reads in a warning: "glossary term 'x' was not rendered as
 * 'y'". The retry prompt shows the same sentence, uncapitalised. */
export function driftPhrase(drift: TermDrift): string {
  return `${DRIFT_LABELS[drift.kind]} '${drift.source}' `
    + `was not rendered as '${drift.target}'`;
}

/** The repair cause a drift is grouped by: what drifted, never where. */
export function driftCause(drift: TermDrift): string {
  return `${drift.kind}:${drift.source}`;
}

/** Case-folded and whitespace-collapsed, so one phrase cannot enter the
 * glossary twice under two spellings of the same key. */
function glossaryKey(source: string): string {
  return source.trim().split(/\s+/).join(' ').toLowerCase();
}

/** An idiom target that explains the idiom instead of translating it. Both
 * limits have to be passed: a long equivalent for a long source is fine. */
function isDefinition(hint: TermHint): boolean {
  const source = [...hint.source.trim()].length;
  const target = [...hint.target.trim()].length;
  return target > source * IDIOM_MAX_EXPANSION && target > IDIOM_MAX_TARGET_CHARS;
}

/** The idioms a glossary may keep. An idiom whose source is already a term is
 * dropped — terms win, because they carry the substitutable target form and an
 * idiom that shadows one pastes its value over every use of the phrase. */
export function usableIdioms(terms: TermHint[], idioms: TermHint[]): TermHint[] {
  const pinned = new Set(terms.map((t) => glossaryKey(t.source)));
  return idioms.filter((h) => !pinned.has(glossaryKey(h.source)) && !isDefinition(h));
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
    public idioms: TermHint[] = [],
    public scenes: SceneHint[] = [],
    public notes: string[] = [],
  ) {}

  isEmpty(): boolean {
    return !(this.register || this.characters.length || this.terms.length
      || this.idioms.length || this.scenes.length || this.notes.length);
  }

  private entriesForBatch(batch: SubtitleBlock[]): {
    chars: CharacterHint[];
    terms: TermHint[];
    idioms: TermHint[];
    scenes: SceneHint[];
  } {
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
    const idioms = this.idioms.filter((h) => findWord(text, h.source) >= 0);
    return { chars, terms, idioms, scenes };
  }

  // The reviewer may only fix names, genders, glossary terms and idioms —
  // leaving idioms out caps the idiom fix to batches that also name a
  // character or a term.
  hasCorrections(batch: SubtitleBlock[]): boolean {
    const { chars, terms, idioms } = this.entriesForBatch(batch);
    return chars.length > 0 || terms.length > 0 || idioms.length > 0;
  }

  /** Terms and character names the batch's source used whose pinned target
   * never made it into the output. A report only: a term can legitimately be
   * inflected away, so this never fails or retries a batch. */
  driftEntries(batch: SubtitleBlock[], output: SubtitleBlock[]): TermDrift[] {
    if (!batch.length) return [];
    const text = batch.map((b) => b.text).join('\n');
    // Named in the batch's own source, not merely a scene participant: a
    // character who never comes up owes the output no target form.
    const named = this.characters.filter((h) => findWord(text, h.source) >= 0);
    const { terms } = this.entriesForBatch(batch);
    if (!terms.length && !named.length) return [];
    const rendered = output.map((b) => b.text).join('\n').toLowerCase();
    const missing = (h: TermHint): boolean =>
      h.target.trim() !== '' && !rendered.includes(h.target.toLowerCase());
    const first = batch[0]!.number;
    return [
      ...terms.filter(missing).map(
        (h) => ({ block: first, kind: 'term' as const, source: h.source, target: h.target })),
      ...named.filter(missing).map(
        (h) => ({ block: first, kind: 'name' as const, source: h.source, target: h.target })),
    ];
  }

  driftWarnings(batch: SubtitleBlock[], output: SubtitleBlock[]): string[] {
    return this.driftEntries(batch, output).map(
      (d) => `Block ${d.block}: ${driftPhrase(d)}`);
  }

  // Glossary slice scoped to this batch. Register/notes are file-wide.
  renderForBatch(batch: SubtitleBlock[]): string {
    const { chars, terms, idioms, scenes } = this.entriesForBatch(batch);
    if (!this.register && !chars.length && !terms.length && !idioms.length
        && !scenes.length && !this.notes.length) {
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
    if (idioms.length) {
      parts.push('Idioms - render by meaning, never word for word:\n'
        + idioms.map((h) => `- ${h.source} => ${h.target}`).join('\n'));
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

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Case-insensitive whole-word search with Unicode-aware boundaries.
// Works for Latin, Arabic, CJK, etc. A multi-word phrase matches across any
// run of whitespace, so "safety briefing" is still found when the batch
// broke it over two subtitle lines. Returns first match index or -1.
function findWord(text: string, word: string): number {
  if (!text || !word) return -1;
  // Lowercasing can change length (e.g. "İ"), so index haystack, not text.
  const haystack = text.toLowerCase();
  const parts = word.toLowerCase().split(/\s+/).filter((p) => p !== '');
  if (!parts.length) return -1;
  const pattern = new RegExp(parts.map(escapeRegExp).join('\\s+'), 'gu');
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(haystack)) !== null) {
    const start = match.index;
    const end = start + match[0].length;
    const before = start > 0 ? haystack[start - 1] : '';
    const after = end < haystack.length ? haystack[end] : '';
    if (!extendsWord(before) && !extendsWord(after)) return start;
    pattern.lastIndex = start + 1;
  }
  return -1;
}

// Uncased scripts (CJK, Arabic, Thai) run words together, so an adjacent
// letter there is no evidence the name is part of a longer word.
function extendsWord(ch: string): boolean {
  if (!ch) return false;
  if (ch === '_') return true;
  if (/\p{N}/u.test(ch)) return true;
  return /\p{L}/u.test(ch) && ch.toLowerCase() !== ch.toUpperCase();
}

export function detectParticipants(text: string, characters: CharacterHint[]): string[] {
  // Match source AND target forms so descriptions in the target language
  // still resolve to the canonical source name.
  const aliases: { alias: string; name: string }[] = [];
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

/** Source phrases the file repeats, ranked by how much rendering them
 * consistently is worth. Deterministic — no model call — and fed to the scan
 * so the glossary is seeded from the file instead of the model's attention. */
export function recurringPhrases(
  blocks: SubtitleBlock[], minChars: number = PHRASE_MIN_CHARS,
): string[] {
  const counts = new Map<string, number>();
  for (const block of blocks) {
    const words = phraseWords(block.text);
    for (let n = PHRASE_MIN_WORDS; n <= PHRASE_MAX_WORDS; n++) {
      for (let i = 0; i + n <= words.length; i++) {
        const gram = words.slice(i, i + n);
        if (gram.every((w) => PHRASE_STOPWORDS.has(w))) continue;
        const phrase = gram.join(' ');
        if (phrase.length < minChars) continue;
        counts.set(phrase, (counts.get(phrase) ?? 0) + 1);
      }
    }
  }

  const kept = [...counts].filter(([, count]) => count >= PHRASE_MIN_COUNT);
  // A short phrase seen only inside a longer one pins nothing extra.
  const survivors = kept.filter(([phrase, count]) => !kept.some(
    ([other, otherCount]) => other !== phrase && otherCount === count
      && ` ${other} `.includes(` ${phrase} `),
  ));
  // Code-point order, not locale order: the CLI mirror must rank identically.
  survivors.sort((a, b) => (b[1] * b[0].length) - (a[1] * a[0].length)
    || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return survivors.slice(0, PHRASE_LIMIT).map(([phrase]) => phrase);
}

// The phrase tokenizer: what a phrase is mined from, and what it is matched
// against later, so a phrase can never fail to find the cue it came from.
function phraseWords(text: string): string[] {
  return stripTags(text).toLowerCase().match(PHRASE_WORD_RE) ?? [];
}

/** A recurring source phrase whose cues came back with no wording in common. */
export interface PhraseSplit {
  phrase: string;
  occurrences: number;
  distinctRenderings: number;
  /** The source blocks it recurs in, so the repair can find their batches. */
  blocks: number[];
}

export function phraseSplitMessage(split: PhraseSplit): string {
  return `'${split.phrase}' is rendered ${split.distinctRenderings} different `
    + `ways across ${split.occurrences} cues; no wording is shared by all of them`;
}

/** Phrases the file repeats whose finished cues share no wording at all — the
 * inconsistency the glossary-drift check cannot see, because it only asks
 * whether a PINNED target was used. Whole-file by nature: a quarter of the
 * file looks consistent from inside any one batch. A report and a repair
 * signal, never a rewrite; which rendering is right is not ours to decide. */
export function findInconsistentPhrases(
  source: SubtitleBlock[], output: SubtitleBlock[],
): PhraseSplit[] {
  if (!source.length || !output.length) return [];
  // By block number, not position: a missing cue must cost only itself.
  const rendered = new Map(output.map((b) => [b.number, b.text]));
  const haystacks = source.map((b) => ` ${phraseWords(b.text).join(' ')} `);
  const splits: PhraseSplit[] = [];
  for (const phrase of recurringPhrases(source, CONSISTENCY_MIN_CHARS)) {
    const needle = ` ${phrase} `;
    const blocks: number[] = [];
    const renderings: Set<string>[] = [];
    for (let i = 0; i < source.length; i++) {
      const text = rendered.get(source[i]!.number);
      if (text === undefined || !haystacks[i]!.includes(needle)) continue;
      blocks.push(source[i]!.number);
      renderings.push(new Set(contentWords(text)));
    }
    if (blocks.length < CONSISTENCY_MIN_OCCURRENCES) continue;
    // The phrase's own rendering is not alignable inside a cue, but a wording
    // every one of its cues shares is the best evidence there is of one.
    const shared = [...renderings[0]!].some(
      (word) => renderings.every((words) => words.has(word)));
    if (shared) continue;
    const distinct = new Set(
      renderings.map((words) => [...words].sort().join(' '))).size;
    splits.push({
      phrase, occurrences: blocks.length, distinctRenderings: distinct, blocks,
    });
  }
  return splits;
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

// Reconcile scene participants with what's in the source blocks. Block-text
// names are primary truth: description-named participants are kept only if
// grounded in the text; missed block-text names are appended.
export function enrichScenesWithBlockText(
  context: FileContext,
  blocks: SubtitleBlock[],
): FileContext {
  if (!context.scenes.length || !blocks.length) return context;
  const clamped = clampScenes(context.scenes, blocks);
  if (!context.characters.length) {
    return new FileContext(
      context.register, context.characters, context.terms, context.idioms,
      clamped, context.notes,
    );
  }
  const byNum = new Map(blocks.map((b) => [b.number, b]));
  const enriched = clamped.map((scene) => {
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
    context.register, context.characters, context.terms, context.idioms,
    enriched, context.notes,
  );
}

// The model may name ranges beyond the file; clamp so range loops stay bounded.
function clampScenes(scenes: SceneHint[], blocks: SubtitleBlock[]): SceneHint[] {
  const first = blocks[0].number;
  const last = blocks[blocks.length - 1].number;
  const out: SceneHint[] = [];
  for (const s of scenes) {
    if (s.end < first || s.start > last) continue;
    out.push({ ...s, start: Math.max(s.start, first), end: Math.min(s.end, last) });
  }
  return out;
}

/** Per-block speakers change the translation when the scene mixes genders, or
 * when the target inflects for gender at all — an "unknown" gender is the
 * ambiguity the call exists to resolve, so it must not be what blocks it.
 * `full` trades calls for knowing the speaker of every scene with a cast. */
export function needsAttribution(
  scene: SceneHint,
  genderBy: Map<string, Gender>,
  full = false,
  targetInflects = false,
): boolean {
  if (scene.end - scene.start + 1 < ATTRIB_MIN_BLOCKS) return false;
  if (full) return scene.participants.length >= 1;
  if (scene.participants.length < 2) return false;
  const known = new Set(
    scene.participants
      .map((p) => genderBy.get(p.toLowerCase()))
      .filter((g): g is Gender => g === 'male' || g === 'female'),
  );
  return known.size >= 2 || targetInflects;
}
