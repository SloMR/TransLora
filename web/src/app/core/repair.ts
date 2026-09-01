// The deterministic repairs applied to a batch once it validates: formatting
// tags the model dropped, speaker dashes, line count, line length, RTL
// punctuation, the sentence-final mark, foreign-script leakage and cross-cue
// shift detection — plus the one pass that needs the whole file, stripping
// vocalisation the model added to a few cues.
//
// Pure functions — no network, no config — so the translation service and the
// tests call them the same way. Models drop `{\i1}` pairs and re-flow two
// source lines into one often enough that retrying is the wrong tool: repair
// it locally.

import { VARIANT_DRIFT_FRACTION } from './constants';
import {
  ARABIC_PUNCTUATION_SCRIPTS,
  DEFAULT_MAX_CHARS_PER_LINE,
  DEFAULT_SCRIPT,
  GENERIC_VARIANT,
  VARIANT_MARKERS,
} from './languages';
import { SubtitleBlock } from './srt-parser';

// HTML-ish tags and ASS override blocks — the two forms the prompt copies through.
const TAG_RE = /<\/?[a-zA-Z][^>]*>|\{\\[^}]*\}/g;
// Same pattern, anchored, for scanning a string one unit at a time.
const TAG_AT_RE = /<\/?[a-zA-Z][^>]*>|\{\\[^}]*\}/y;

// Written without spaces, so a line break lands between characters, not words.
export const NO_SPACE_SCRIPTS = new Set([
  'han', 'japanese', 'hiragana', 'katakana', 'thai',
]);

// A line starting with one of these is a speaker turn; re-flowing merges speakers.
export const DIALOGUE_DASHES = ['-', '–'];

// Never open a line with a closing mark: a break before one orphans it.
const NO_LINE_START = '、。，．！？；：）〕】》」』〉”｝〗〙〛…,.!?;:)]}»';
// A CJK line break reads most naturally right after one of these.
const BREAK_AFTER = '、。，．！？；：）〕】》」』';

export const RTL_PUNCTUATION: Record<string, string> = { '?': '؟', ',': '،', ';': '؛' };
const URL_RE = /(?:[a-zA-Z][\w+.-]*:\/\/|www\.)\S+/g;
const ARABIC_RE = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/;
const LATIN_RE = /[0-9A-Za-z]/;

// Formatting tags plus the ASS hard line break: markup, never text. Replaced
// by a space so stripping one never welds the words either side together.
const NON_TEXT_RE = /<\/?[a-zA-Z][^>]*>|\{\\[^}]*\}|\\N/g;

// Arabic vocalisation: fatha through sukun, plus the superscript alef.
const ARABIC_DIACRITICS_RE = /[\u064B-\u0652\u0670]/g;
// A file whose median cue carries more than this is vocalized on purpose.
export const DIACRITIC_BASELINE_MAX = 1;
// Marks in one cue before it counts as a register switch rather than a stray.
export const DIACRITIC_CUE_MIN = 5;

export const TERMINAL_MARKS = ['.', '!', '?'];

// Cues shorter than this make any shared-run signal meaningless.
export const SHIFT_MIN_SOURCE_CHARS = 15;
// A run is scored in characters, not words: both real desyncs in the benchmark
// episode share exactly one distinctive word across the boundary, and one of
// them shares a short word as well - only the longer word is evidence.
export const SHIFT_MIN_RUN_WORDS = 1;
export const SHIFT_MIN_RUN_CHARS = 4;
// Share of the shorter output cue the run has to account for, and how far it
// has to outrun the same measurement on the source pair.
export const SHIFT_MIN_COVERAGE = 0.45;
export const SHIFT_SOURCE_RATIO = 2.0;

export interface TagRepair {
  text: string;
  ok: boolean;
}

/** Formatting tags in reading order. */
export function findTags(text: string): string[] {
  return text.match(TAG_RE) ?? [];
}

/** On-screen width: formatting tags occupy no columns. */
export function visibleLength(text: string): number {
  return codePoints(text.replace(TAG_RE, '')).length;
}

/** Restore tags the model dropped. `ok` false means the mismatch is not one we
 * can safely undo and the caller should warn. */
// An opening tag immediately followed by its closing form: renders nothing.
const EMPTY_PAIR_RE = /\{\\(?<ass>\w+?)1\}\{\\\k<ass>0\}|<(?<html>\w+)[^>]*>\s*<\/\k<html>>/g;

export function dropEmptyTagPairs(text: string): string {
  return text.replace(EMPTY_PAIR_RE, '');
}

export function repairTags(sourceText: string, outputText: string): TagRepair {
  outputText = dropEmptyTagPairs(outputText);
  const sourceTags = findTags(sourceText);
  const outputTags = findTags(outputText);
  if (sourceTags.length === outputTags.length
      && sourceTags.every((tag, i) => tag === outputTags[i])) {
    return { text: outputText, ok: true };
  }
  if (outputTags.length || !sourceTags.length) return { text: outputText, ok: false };

  // Stripped: a cue arriving with stray whitespace still reads as TAG body TAG.
  const source = sourceText.trim();
  const matches = [...source.matchAll(TAG_RE)];
  if (matches.length === 2) {
    const [opening, closing] = matches;
    if (opening!.index === 0
        && closing!.index + closing![0].length === source.length) {
      return { text: sourceTags[0]! + outputText.trim() + sourceTags[1]!, ok: true };
    }
  }
  if (matches.length === 1 && matches[0]!.index === 0) {
    return { text: sourceTags[0]! + outputText, ok: true };
  }
  return { text: outputText, ok: false };
}

export interface DashRepair {
  text: string;
  ok: boolean;
}

/** Lines that open a speaker turn: lines whose first non-tag, non-space
 * character is a dialogue dash. */
export function dialogueDashLines(text: string): number {
  return text.split('\n')
    .filter((line) => DIALOGUE_DASHES.includes(firstVisible(line)))
    .length;
}

/** Put back the speaker dashes the model dropped. Losing both dashes merges
 * two speakers into one turn, and nothing downstream notices. `ok` false means
 * the cue no longer lines up well enough to say which lines lost one, and the
 * caller should warn. */
export function restoreDialogueDashes(
  sourceText: string, outputText: string,
): DashRepair {
  const sourceCount = dialogueDashLines(sourceText);
  const outputCount = dialogueDashLines(outputText);
  if (sourceCount === outputCount) return { text: outputText, ok: true };
  const sourceLines = sourceText.split('\n');
  const outputLines = outputText.split('\n');
  // Only a line-for-line cue says which output line the missing dash belongs to.
  if (sourceCount < outputCount || sourceLines.length !== outputLines.length) {
    return { text: outputText, ok: false };
  }
  const restored = outputLines.map((line, i) => (
    DIALOGUE_DASHES.includes(firstVisible(sourceLines[i]!))
      && !DIALOGUE_DASHES.includes(firstVisible(line))
      ? `- ${line}`
      : line
  ));
  return { text: restored.join('\n'), ok: true };
}

/** Re-wrap `text` to exactly `targetLines` lines, or return it unchanged when
 * that cannot be done safely. */
export function reflowToLineCount(
  text: string,
  targetLines: number,
  maxChars: number = DEFAULT_MAX_CHARS_PER_LINE,
  script: string = DEFAULT_SCRIPT,
): string {
  const lines = text.split('\n');
  if (targetLines < 1 || lines.length === targetLines || !text.trim()) return text;
  if (hasDialogueDash(text)) return text;

  const noSpace = NO_SPACE_SCRIPTS.has(script);
  const joiner = noSpace ? '' : ' ';
  const flat = lines
    .map((line) => line.trim())
    .filter((line) => line)
    .join(joiner);
  const units = noSpace ? charUnits(flat) : wordUnits(flat);
  const result = balancedLines(units, targetLines, joiner, maxChars, noSpace);
  if (result === null || result.some((line) => !line)) return text;
  return result.join('\n');
}

/** One over-long line may become two — the professional maximum. A cue that
 * already has two or more lines is left alone. */
export function enforceLineLength(
  text: string,
  maxChars: number = DEFAULT_MAX_CHARS_PER_LINE,
  script: string = DEFAULT_SCRIPT,
): string {
  const lines = text.split('\n');
  if (lines.length >= 2 || visibleLength(lines[0]!) <= maxChars) return text;
  return reflowToLineCount(text, 2, maxChars, script);
}

/** Re-point ASCII punctuation that Arabic-script text renders wrong. Left alone
 * inside a tag or a URL, and inside a run of Latin text or digits. */
export function normalizeRtlPunctuation(text: string, script: string): string {
  if (!ARABIC_PUNCTUATION_SCRIPTS.has(script) || !text) return text;
  const tagSpans = spansOf(text, TAG_RE);
  const frozen = [...tagSpans, ...spansOf(text, URL_RE)];
  // UTF-16 units, so the indices line up with the spans the regexes reported.
  const chars = text.split('');
  for (let index = 0; index < chars.length; index++) {
    const replacement = RTL_PUNCTUATION[chars[index]!];
    if (replacement === undefined) continue;
    if (frozen.some(([start, end]) => start <= index && index < end)) continue;
    const before = neighbour(text, index, -1, tagSpans);
    const after = neighbour(text, index, 1, tagSpans);
    if (!(ARABIC_RE.test(before) || ARABIC_RE.test(after))) continue;
    if (LATIN_RE.test(before) && LATIN_RE.test(after)) continue;
    chars[index] = replacement;
  }
  return chars.join('');
}

// Coarse Unicode ranges, enough to tell one writing system from another.
// Only letters are classified, so digits, punctuation and combining marks are
// neutral and never count as a script of their own.
export const SCRIPT_RANGES: Record<string, [number, number][]> = {
  latin: [[0x0041, 0x005a], [0x0061, 0x007a], [0x00c0, 0x024f], [0x1e00, 0x1eff]],
  greek: [[0x0370, 0x03ff], [0x1f00, 0x1fff]],
  cyrillic: [[0x0400, 0x052f]],
  hebrew: [[0x0590, 0x05ff]],
  arabic: [[0x0600, 0x06ff], [0x0750, 0x077f], [0x08a0, 0x08ff],
    [0xfb50, 0xfdff], [0xfe70, 0xfeff]],
  devanagari: [[0x0900, 0x097f]],
  thai: [[0x0e00, 0x0e7f]],
  han: [[0x3400, 0x4dbf], [0x4e00, 0x9fff], [0xf900, 0xfaff]],
  kana: [[0x3040, 0x30ff], [0x31f0, 0x31ff]],
  hangul: [[0x1100, 0x11ff], [0x3130, 0x318f], [0xac00, 0xd7af]],
};

// The scripts a target may legitimately be written in. A target script with no
// entry (DEFAULT_SCRIPT) is never checked: we cannot say what belongs in it.
export const TARGET_SCRIPTS: Record<string, string[]> = {
  latin: ['latin'],
  cyrillic: ['cyrillic'],
  greek: ['greek'],
  arabic: ['arabic'],
  hebrew: ['hebrew'],
  devanagari: ['devanagari'],
  thai: ['thai'],
  han: ['han'],
  japanese: ['kana', 'han'],
  korean: ['hangul', 'han'],
};

// Non-global, so `test` never carries a lastIndex between calls.
const LETTER_RE = /\p{L}/u;
const MARK_RE = /\p{M}/u;
// Long enough to recognise the leak, short enough to read in a warning.
const LEAK_SAMPLE_CHARS = 12;

/** One run of letters from a single script, and whether the character before
 * it was a letter of a different one. */
interface ScriptRun {
  script: string;
  text: string;
  glued: boolean;
}

/** One writing system left in a translation, and what is wrong with it.
 * `script` is the alien one, so two cues leaking the same script are one
 * problem however differently their messages read. */
export interface ScriptLeak {
  script: string;
  message: string;
}

/** Writing systems the model left in a translation that has no business
 * carrying them: a script neither the target nor the source cue uses, and a
 * target-script letter welded to a letter of another script, which breaks
 * bidirectional rendering. A report only — never a reason to fail a file. */
export function scriptLeaks(
  sourceText: string, outputText: string, script: string,
): ScriptLeak[] {
  const own = TARGET_SCRIPTS[script];
  if (!own) return [];
  const runs = scriptRuns(outputText);
  const inSource = new Set(scriptRuns(sourceText).map((run) => run.script));
  const leaks: ScriptLeak[] = [];
  const reported = new Set<string>();
  for (const run of runs) {
    if (own.includes(run.script) || inSource.has(run.script)) continue;
    if (reported.has(run.script)) continue;
    reported.add(run.script);
    leaks.push({
      script: run.script,
      message: `${run.script} characters appear in the translation `
        + `('${codePoints(run.text).slice(0, LEAK_SAMPLE_CHARS).join('')}')`,
    });
  }
  leaks.push(...gluedRuns(runs, own));
  return leaks;
}

/** Tokens where a target-script letter touches a letter of another script with
 * no space, punctuation or tag between them. */
function gluedRuns(runs: ScriptRun[], own: string[]): ScriptLeak[] {
  const leaks: ScriptLeak[] = [];
  let start = 0;
  for (let i = 1; i <= runs.length; i++) {
    if (i < runs.length && runs[i]!.glued) continue;
    const chain = runs.slice(start, i);
    start = i;
    if (chain.length < 2) continue;
    const alien = chain.find((run) => !own.includes(run.script));
    const mine = chain.find((run) => own.includes(run.script));
    if (!alien || !mine) continue;
    leaks.push({
      script: alien.script,
      message: `'${chain.map((run) => run.text).join('')}' welds `
        + `${mine.script} to ${alien.script} with no separator`,
    });
  }
  return leaks;
}

/** Maximal runs of one script, tags read as separators and combining marks
 * kept with the letter they sit on. */
function scriptRuns(text: string): ScriptRun[] {
  const runs: ScriptRun[] = [];
  let current: ScriptRun | null = null;
  let separated = true;
  for (const char of codePoints(stripTags(text))) {
    if (current && MARK_RE.test(char)) {
      current.text += char;
      continue;
    }
    const name = scriptOf(char);
    if (name === null) {
      current = null;
      separated = true;
      continue;
    }
    if (current && current.script === name) {
      current.text += char;
      continue;
    }
    current = { script: name, text: char, glued: !separated };
    separated = false;
    runs.push(current);
  }
  return runs;
}

/** The script one character is written in, or null when it is not a letter or
 * belongs to no range we classify. */
function scriptOf(char: string): string | null {
  if (!LETTER_RE.test(char)) return null;
  const code = char.codePointAt(0)!;
  for (const [name, ranges] of Object.entries(SCRIPT_RANGES)) {
    if (ranges.some(([low, high]) => code >= low && code <= high)) return name;
  }
  return null;
}

/** One adjacent output pair that looks like a clause slid across their
 * boundary, and the run of words that says so. */
export interface CueShift {
  first: number;
  second: number;
  run: string;
}

/** Adjacent output cues that share a run of words their sources do not — the
 * shape a clause takes when it slides from one cue into its neighbour. A
 * report only; short cues make false positives routine. */
export function findCrossCueShifts(
  source: SubtitleBlock[], output: SubtitleBlock[],
): CueShift[] {
  const shifts: CueShift[] = [];
  for (let i = 0; i + 1 < source.length; i++) {
    const firstSrc = source[i]!;
    const secondSrc = source[i + 1]!;
    if (textLength(firstSrc.text) < SHIFT_MIN_SOURCE_CHARS
        || textLength(secondSrc.text) < SHIFT_MIN_SOURCE_CHARS) continue;
    const run = longestSharedRun(
      contentWords(output[i]!.text), contentWords(output[i + 1]!.text));
    if (run.words.length < SHIFT_MIN_RUN_WORDS
        || run.chars < SHIFT_MIN_RUN_CHARS) continue;
    // Both sides of this ratio drop tags, punctuation and spacing, so a run
    // and the cue it sits in are counted in the same characters.
    const shorter = Math.min(
      contentLength(output[i]!.text), contentLength(output[i + 1]!.text));
    if (shorter <= 0 || run.chars < shorter * SHIFT_MIN_COVERAGE) continue;
    // A run the two SOURCE cues share too is repetition, not a shift.
    const sourceRun = longestSharedRun(
      contentWords(firstSrc.text), contentWords(secondSrc.text));
    if (run.chars < sourceRun.chars * SHIFT_SOURCE_RATIO) continue;
    shifts.push({
      first: firstSrc.number,
      second: secondSrc.number,
      run: run.words.join(' '),
    });
  }
  return shifts;
}

export function shiftMessage(shift: CueShift): string {
  return `Blocks ${shift.first}-${shift.second}: '${shift.run}' appears in `
    + 'both cues - text may have shifted between them';
}

export function detectCrossCueShift(
  source: SubtitleBlock[], output: SubtitleBlock[],
): string[] {
  return findCrossCueShifts(source, output).map(shiftMessage);
}

/** Strip a cue's vocalisation where the file as a whole carries none — the
 * model switching orthographic register partway through. File-level on
 * purpose: only the whole file says what its baseline is. */
export function normalizeDiacritics(
  blocks: SubtitleBlock[], script: string,
): SubtitleBlock[] {
  if (!ARABIC_PUNCTUATION_SCRIPTS.has(script) || !blocks.length) return blocks;
  const counts = blocks.map((b) => b.text.match(ARABIC_DIACRITICS_RE)?.length ?? 0);
  if (median(counts) > DIACRITIC_BASELINE_MAX) return blocks;
  return blocks.map((b, i) => (counts[i]! >= DIACRITIC_CUE_MIN
    ? { ...b, text: b.text.replace(ARABIC_DIACRITICS_RE, '') }
    : b));
}

/** A variant the finished file reads as, and how many of its cues say so. */
export interface VariantDrift {
  variant: string;
  cues: number;
  total: number;
}

/** The target variant a finished file drifted into when nobody asked for one:
 * a fluent, unusable file that no per-batch check can see, because every cue
 * is fine on its own. File-level like the diacritic pass, and a report only —
 * it never rewrites a cue.
 *
 * Scored as one union: a file can leave the standard written form without ever
 * committing to a named dialect, spreading its markers thinly enough that every
 * bucket clears the threshold on its own. What crosses the line is colloquial
 * writing as a whole; the loudest bucket only supplies the label. */
export function detectVariantDrift(
  blocks: SubtitleBlock[], script: string, dialect = '',
): VariantDrift | null {
  const variants = VARIANT_MARKERS[script];
  if (!variants || !blocks.length) return null;
  const asked = dialect.toLowerCase();
  const scored = Object.entries(variants).filter(([variant]) => !(
    asked.includes(variant.toLowerCase())
    // Asking for a regional variant accepts colloquial writing along with it.
    || (asked !== '' && variant === GENERIC_VARIANT)));
  const cueTokens = blocks.map((b) => variantTokens(b.text));
  const counts = scored.map(([, markers]) => cueTokens.filter(
    (tokens) => markers.some((marker) => tokens.has(marker))).length);
  // A cue several buckets claim is still one cue of drift.
  const union = cueTokens.filter((tokens) => scored.some(
    ([, markers]) => markers.some((marker) => tokens.has(marker)))).length;
  if (union <= blocks.length * VARIANT_DRIFT_FRACTION) return null;
  // Ties keep the table's own order, so the label never depends on the file.
  let leader = '';
  let loudest = -1;
  scored.forEach(([variant], i) => {
    if (counts[i] > loudest) [leader, loudest] = [variant, counts[i]];
  });
  return { variant: leader, cues: union, total: blocks.length };
}

export function variantDriftMessage(drift: VariantDrift): string {
  return `Output looks like ${drift.variant} rather than the standard written `
    + `form (${drift.cues} of ${drift.total} cues). Pass --dialect to ask for `
    + 'it deliberately, or rerun.';
}

/** Whole words, vocalisation stripped: a marker only counts as a word of its
 * own, so 'مش' inside 'مشكلة' is not evidence of anything. */
function variantTokens(text: string): Set<string> {
  const words = stripTags(text)
    .replace(ARABIC_DIACRITICS_RE, '')
    .match(/[\p{L}\p{N}]+/gu);
  return new Set(words ?? []);
}

/** Put back the sentence-final mark the source ended on when the model
 * swapped it for another one. A cue that ends in no mark at all is left
 * alone — that is a translator's choice, not a substitution. */
export function restoreTerminalPunctuation(
  sourceText: string, outputText: string, script: string,
): string {
  const sourceIndex = lastVisibleIndex(sourceText);
  if (sourceIndex < 0) return outputText;
  const sourceMark = sourceText.charAt(sourceIndex);
  if (!TERMINAL_MARKS.includes(sourceMark)) return outputText;
  const outputIndex = lastVisibleIndex(outputText);
  if (outputIndex < 0) return outputText;
  const outputClass = terminalClass(outputText.charAt(outputIndex), script);
  // No mark at all, or already the source's own class: nothing to restore.
  if (outputClass === '' || outputClass === sourceMark) return outputText;
  // An ellipsis or a "?!" is a deliberate device, not a swapped-out mark.
  const priorIndex = lastVisibleIndex(outputText, outputIndex - 1);
  if (priorIndex >= 0
      && terminalClass(outputText.charAt(priorIndex), script) !== '') return outputText;
  return outputText.slice(0, outputIndex) + targetMark(sourceMark, script)
    + outputText.slice(outputIndex + 1);
}

/** The ASCII terminal mark this character stands for, or '' if it is not one. */
function terminalClass(char: string, script: string): string {
  return TERMINAL_MARKS.find(
    (mark) => mark === char || targetMark(mark, script) === char) ?? '';
}

/** The text with formatting tags removed. */
export function stripTags(text: string): string {
  return text.replace(NON_TEXT_RE, ' ');
}

function targetMark(mark: string, script: string): string {
  return ARABIC_PUNCTUATION_SCRIPTS.has(script)
    ? (RTL_PUNCTUATION[mark] ?? mark)
    : mark;
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 === 1
    ? sorted[mid]!
    : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

/** Index of the last character at or before `from` that is neither a tag nor
 * whitespace. */
function lastVisibleIndex(text: string, from = text.length - 1): number {
  const spans = spansOf(text, TAG_RE);
  for (let i = Math.min(from, text.length - 1); i >= 0; i--) {
    const span = spans.find(([start, end]) => start <= i && i < end);
    if (span) {
      i = span[0];
      continue;
    }
    if (!/\s/.test(text.charAt(i))) return i;
  }
  return -1;
}

/** Comparable words: tags gone, punctuation gone, case folded. */
export function contentWords(text: string): string[] {
  return stripTags(text)
    .split(/\s+/)
    .map((word) => word.replace(/[^\p{L}\p{N}]/gu, '').toLowerCase())
    .filter((word) => word !== '');
}

interface SharedRun {
  words: string[];
  chars: number;
}

/** The run of words appearing in the same order in both cues that carries the
 * most characters — one long shared word is stronger evidence of a slide than
 * a longer run of short ones. */
function longestSharedRun(first: string[], second: string[]): SharedRun {
  let best: SharedRun = { words: [], chars: 0 };
  let previousWords = new Array<number>(second.length + 1).fill(0);
  let previousChars = new Array<number>(second.length + 1).fill(0);
  for (let i = 1; i <= first.length; i++) {
    const currentWords = new Array<number>(second.length + 1).fill(0);
    const currentChars = new Array<number>(second.length + 1).fill(0);
    for (let j = 1; j <= second.length; j++) {
      if (first[i - 1] !== second[j - 1]) continue;
      currentWords[j] = previousWords[j - 1]! + 1;
      currentChars[j] = previousChars[j - 1]! + textLength(first[i - 1]!);
      if (currentChars[j]! > best.chars) {
        best = {
          words: first.slice(i - currentWords[j]!, i),
          chars: currentChars[j]!,
        };
      }
    }
    previousWords = currentWords;
    previousChars = currentChars;
  }
  return best;
}

/** Characters of a cue that a shared run could be made of. */
function contentLength(text: string): number {
  return textLength(contentWords(text).join(''));
}

// Python counts code points; matching that keeps the two trees' warnings equal.
function codePoints(text: string): string[] {
  return Array.from(text);
}

function textLength(text: string): number {
  return codePoints(text).length;
}

function tagAt(text: string, index: number): string | null {
  TAG_AT_RE.lastIndex = index;
  const match = TAG_AT_RE.exec(text);
  return match ? match[0] : null;
}

function spansOf(text: string, pattern: RegExp): [number, number][] {
  return [...text.matchAll(pattern)].map((m) => [m.index, m.index + m[0].length]);
}

/** Nearest visible character in one direction; tags and spaces are skipped. */
function neighbour(
  text: string, index: number, step: number, tagSpans: [number, number][],
): string {
  let i = index + step;
  while (i >= 0 && i < text.length) {
    const span = tagSpans.find(([start, end]) => start <= i && i < end);
    if (span) {
      i = step < 0 ? span[0] - 1 : span[1];
    } else if (/\s/.test(text.charAt(i))) {
      i += step;
    } else {
      return text.charAt(i);
    }
  }
  return '';
}

function firstVisible(unit: string): string {
  return codePoints(unit.replace(TAG_RE, '').replace(/^\s+/, ''))[0] ?? '';
}

function hasDialogueDash(text: string): boolean {
  return dialogueDashLines(text) > 0;
}

function lastVisible(unit: string): string {
  const stripped = codePoints(unit.replace(TAG_RE, '').replace(/\s+$/, ''));
  return stripped[stripped.length - 1] ?? '';
}

/** Whitespace-separated tokens, each formatting tag glued to its word. */
function wordUnits(text: string): string[] {
  const units: string[] = [];
  let current = '';
  let pos = 0;
  while (pos < text.length) {
    const tag = tagAt(text, pos);
    if (tag) {
      current += tag;
      pos += tag.length;
    } else if (/\s/.test(text.charAt(pos))) {
      if (current) units.push(current);
      current = '';
      pos += 1;
    } else {
      current += text.charAt(pos);
      pos += 1;
    }
  }
  if (current) units.push(current);
  return units;
}

/** One unit per character, with tags and trailing spaces glued on so that
 * joining with "" is lossless and no break can land inside a tag. */
function charUnits(text: string): string[] {
  const units: string[] = [];
  let pending = '';
  let pos = 0;
  while (pos < text.length) {
    const tag = tagAt(text, pos);
    if (tag) {
      pending += tag;
      pos += tag.length;
      continue;
    }
    const char = String.fromCodePoint(text.codePointAt(pos)!);
    pos += char.length;
    if (/\s/.test(char)) {
      if (units.length) {
        units[units.length - 1] += pending + char;
        pending = '';
      }
      continue;
    }
    units.push(pending + char);
    pending = '';
  }
  if (pending) {
    if (units.length) units[units.length - 1] += pending;
    else units.push(pending);
  }
  return units;
}

/** Split `units` into exactly `targetLines` lines, minimising the longest. */
function balancedLines(
  units: string[],
  targetLines: number,
  joiner: string,
  maxChars: number,
  noSpace: boolean,
): string[] | null {
  const total = units.length;
  if (total < targetLines) return null;

  const prefix = [0];
  for (const unit of units) prefix.push(prefix[prefix.length - 1]! + visibleLength(unit));
  const gap = joiner.length;

  const width = (start: number, end: number) =>
    prefix[end]! - prefix[start]! + gap * (end - start - 1);

  const mayBreakBefore = (index: number): boolean => {
    const first = firstVisible(units[index]!);
    return !(noSpace && first !== '' && NO_LINE_START.includes(first));
  };

  const penaltyBefore = (index: number): number => {
    const last = lastVisible(units[index - 1]!);
    return !noSpace || (last !== '' && BREAK_AFTER.includes(last)) ? 0 : 1;
  };

  // Lines that fit are interchangeable, which lets the CJK punctuation
  // preference decide before residual balance does.
  const better = (a: [number, number], b: [number, number]): boolean => {
    const left: number[] = [Math.max(a[0], maxChars), a[1], a[0]];
    const right: number[] = [Math.max(b[0], maxChars), b[1], b[0]];
    for (let i = 0; i < left.length; i++) {
      if (left[i] !== right[i]) return left[i]! < right[i]!;
    }
    return false;
  };

  const UNREACHABLE: [number, number] = [1e9, 1e9];
  // best[k][i]: (longest line, break penalty) for units[i:] split into k lines.
  const best: [number, number][][] = Array.from(
    { length: targetLines + 1 },
    () => Array.from({ length: total + 1 }, () => UNREACHABLE),
  );
  const at: number[][] = Array.from(
    { length: targetLines + 1 },
    () => new Array<number>(total + 1).fill(-1),
  );
  for (let start = 0; start < total; start++) best[1]![start] = [width(start, total), 0];

  for (let linesLeft = 2; linesLeft <= targetLines; linesLeft++) {
    for (let start = 0; start <= total - linesLeft; start++) {
      for (let cut = start + 1; cut <= total - linesLeft + 1; cut++) {
        const tail = best[linesLeft - 1]![cut]!;
        if (tail === UNREACHABLE || !mayBreakBefore(cut)) continue;
        const candidate: [number, number] = [
          Math.max(width(start, cut), tail[0]),
          tail[1] + penaltyBefore(cut),
        ];
        if (better(candidate, best[linesLeft]![start]!)) {
          best[linesLeft]![start] = candidate;
          at[linesLeft]![start] = cut;
        }
      }
    }
  }
  if (best[targetLines]![0] === UNREACHABLE) return null;

  const lines: string[] = [];
  let start = 0;
  for (let linesLeft = targetLines; linesLeft > 1; linesLeft--) {
    const cut = at[linesLeft]![start]!;
    lines.push(units.slice(start, cut).join(joiner).trim());
    start = cut;
  }
  lines.push(units.slice(start, total).join(joiner).trim());
  return lines;
}
