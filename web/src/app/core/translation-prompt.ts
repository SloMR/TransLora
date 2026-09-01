// All LLM-facing prompts and user-message builders, kept in one place so they
// can be iterated on alongside their counterparts.

import { DEFAULT_FORMALITY } from './constants';
import { SubtitleBlock, serializeLite } from './srt-parser';
import { DEFAULT_MAX_CHARS_PER_LINE, DEFAULT_MAX_LINES } from './languages';

export const SYSTEM_PROMPT = `You are a subtitle translator. You will receive numbered subtitle blocks (no timestamps) and translate them.

Format — the block number alone on its own line, then the text lines, one blank line between blocks:

120
First line of the cue
Second line of the cue

121
Oh!

RULES (violating any = corrupt file):
- Output the SAME number of blocks as input. No merging, no skipping.
- Copy block numbers exactly.
- Keep one blank line between blocks.
- Preserve line breaks within each block (same line count).
- Translate each block independently — never combine split sentences.
- Never move words between blocks. If a sentence continues into the next block, translate only the part in THIS block, even if it reads incomplete.
- Translate faithfully: profanity, slurs, slang — match the original register.
- Idioms, jokes and set phrases: translate the MEANING, never word by word. If the target has an equivalent expression, use it; if not, say plainly what the speaker means.
- Conversational tone, concise — must fit the original timing.
- If a glossary is provided, use each character's listed gender for pronouns/verb forms, and the listed target-language name consistently.
- "Scene guidance" entries apply PER BLOCK RANGE only. Match the addressee's gender (not just the speaker's). For exactly-two referents addressed together, use the target's dual form if it has one.
- A \`speakers:\` line (e.g. \`120=Alice 121=Alice 122=Bob\`) names the speaker per block. The ADDRESSEE is usually the other named participant — use the addressee's gender (from [brackets]) for second-person forms.
- "Previous context" blocks (if shown) are read-only — infer speaker/addressee from them, do NOT translate or include them.
- Use ONE consistent register and variant of the target language across every block. If the target language has a standard written form (e.g. Modern Standard Arabic), use it unless the source is clearly colloquial.

DO NOT TRANSLATE (copy verbatim):
- HTML tags, music symbols, formatting tags (\\N, {\\an8}, {\\i1}, {\\i0}, <i>, </i>) - copy every tag through in the same position, opening and closing
- Proper nouns, brand names, URLs, credit lines

SHORT BLOCKS like "Oh!", "No!", "Hmm." are the #1 cause of missing blocks. Translate every single one.

Output ONLY the numbered blocks in exactly the format shown above. Never output a timestamp line — the input has none. No commentary, no markdown fences.`;

export const REVIEW_SYSTEM_PROMPT = `You are a conservative subtitle translation reviewer. You receive a glossary, source blocks, and a first-pass translation in the same numbered wire format (block number alone on the first line, then the text lines).

DEFAULT: output the first-pass UNCHANGED. Only fix clear violations of the glossary:
- Wrong addressee gender (pronouns, verb conjugation, adjective ending, honorific level) when the glossary unambiguously names the addressee's gender.
- Character name spelled differently from the target form in the glossary.
- Dual/plural/singular agreement when the scene guidance names exactly which participants are addressed together (two named participants addressed as a pair take the target's dual form if it has one).
- A literal word-for-word rendering of an idiom or set phrase, where the meaning is lost.

If uncertain, keep the block verbatim. Do NOT rephrase, restyle, or "polish". Same number of blocks, same block numbers, same line-count per block.

Output: same wire format, one blank line between blocks. ALL blocks. No commentary, no fences.`;

export const CONTEXT_SYSTEM_PROMPT = `You analyze a subtitle file before it is translated. Return a compact glossary for the translator to use when picking correct pronouns, consistent names, and a single consistent register.

Input blocks are prefixed with their block number as \`[N] text\`.

Reply with all six sections below in this exact order. No commentary, no fences — tags only.

<register>
ONE LINE describing the target-language variant and formality.
</register>
<characters>
NAME => TARGET_NAME | GENDER
</characters>
<terms>
SOURCE => TARGET
</terms>
<idioms>
SOURCE_IDIOM => TARGET_EQUIVALENT
</idioms>
<scenes>
START-END => description that NAMES the characters involved
</scenes>
<notes>
- NOTE
</notes>

Rules:
- <register>: name the exact target variant (e.g. "Modern Standard Arabic, neutral", "Brazilian Portuguese, casual", "Japanese, polite です/ます form"). Pick one for the whole file.
- GENDER is "male", "female", or "unknown". Use "unknown" only when the text gives no signal.
- TARGET_NAME is how the character's name should appear in the target language.
- <idioms>: source idioms, set phrases and jokes that must NOT be translated word by word. Give the exact words a subtitle would use in the target language — never a definition or an explanation of the idiom. If no equivalent exists, give the shortest plain rendering of what the speaker means. Include up to 15.
- <scenes>: every ≥3-block stretch of dialogue between named characters. Name the characters explicitly using the names from <characters> so the translator can apply the right gender per range. Ranges may touch but must not overlap.
- Example: \`105-119 => Maria reassures Alex about the interview\` (use the actual names from YOUR <characters> section).
- The blocks shown may be a sample of a longer file. Block numbers are the true numbers from the full file, so a scene range may legitimately span numbers you were not shown.
- Include up to 20 characters, 25 terms, 40 scenes, 4 notes.
- Leave a section empty (tags only) if nothing qualifies. Never omit a section.`;

export const BACK_TRANSLATION_SYSTEM_PROMPT = `You translate subtitle blocks back into their original language so an automated check can see how much of the meaning survived. You receive numbered blocks (the block number alone on its own line, then the text lines) and reply in the same format.

Translate literally, block by block. Do not polish, do not summarise, do not add anything. Same number of blocks, same block numbers. No commentary, no markdown fences.`;

export const ATTRIBUTION_SYSTEM_PROMPT = `You identify the speaker of each subtitle line in a short scene. Given a character list and a block-numbered scene excerpt (\`[N] text\`), reply with exactly one line per input block as \`N=SpeakerName\`. SpeakerName MUST be one of the listed characters or the literal "unknown". No commentary, no fences.`;

// Per-request rules: they carry the target's own norms, so they cannot live in
// the cacheable system prompt.
export const LINE_LIMIT_RULE = `- Keep each line at or under {max_chars} characters and never exceed {max_lines} lines per block; prefer tighter phrasing over a longer line.`;
export const FORMALITY_RULE = `Register: use {formality} address throughout.`;
export const DIALECT_RULE = `Target variant: {dialect}. Use it consistently.`;
export const SCAN_DIALECT_RULE = `Target variant: {dialect}. Use it as the <register> instead of inferring one.`;
// The target script's own classic machine-translation errors: per-target, so
// the review system prompt stays static and cacheable.
export const GRAMMAR_NOTE_RULE = `Target-language checks: {note}`;
// Prepended to a re-issued batch, ahead of the user message it already had.
export const FIX_FLAGGED_RULE = `The previous attempt had these problems - fix ONLY these, keep everything else identical:`;
// Seeded deterministically from the file itself, so the scan pins the phrases
// it repeats instead of spending its whole term budget on whatever it noticed.
export const RECURRING_PHRASES_RULE = `Recurring phrases - give each one ONE target rendering and use it everywhere:`;

// The {name} placeholders above are filled the way Python's str.format does.
function fill(rule: string, values: Record<string, string | number>): string {
  return rule.replace(/\{(\w+)\}/g, (_, key: string) => String(values[key] ?? ''));
}

export function buildUserMessage(
  sourceLang: string,
  targetLang: string,
  srtContent: string,
  glossary?: string,
  prevTail: SubtitleBlock[] = [],
  maxChars: number = DEFAULT_MAX_CHARS_PER_LINE,
  maxLines: number = DEFAULT_MAX_LINES,
  formality: string = DEFAULT_FORMALITY,
  dialect = '',
): string {
  const header = sourceLang
    ? `Translate from ${sourceLang} to ${targetLang}:`
    : `Translate to ${targetLang}:`;
  const directives = [fill(LINE_LIMIT_RULE, { max_chars: maxChars, max_lines: maxLines })];
  if (formality === 'formal' || formality === 'informal') {
    directives.push(fill(FORMALITY_RULE, { formality }));
  }
  if (dialect.trim()) directives.push(fill(DIALECT_RULE, { dialect: dialect.trim() }));

  const sections: string[] = [];
  if (glossary && glossary.trim()) {
    sections.push(`Glossary for this scene:\n${glossary}`);
  }
  if (prevTail.length) {
    const lines = prevTail
      .map((b) => `  [prev #${b.number}] ${b.text.replace(/\n/g, ' ')}`)
      .join('\n');
    sections.push('Previous context (read-only, do NOT translate or output):\n' + lines);
  }
  sections.push(directives.join('\n'));
  sections.push(`${header}\n\n${srtContent}`);
  return sections.join('\n\n');
}

export function buildReviewUserMessage(
  batch: SubtitleBlock[],
  firstPass: SubtitleBlock[],
  glossary: string,
  grammarNote = '',
): string {
  const checks = grammarNote.trim()
    ? fill(GRAMMAR_NOTE_RULE, { note: grammarNote.trim() }) + '\n\n'
    : '';
  return (
    `Glossary:\n${glossary}\n\n` +
    checks +
    `Source blocks:\n${serializeLite(batch)}\n\n` +
    `First-pass translation:\n${serializeLite(firstPass)}\n\n` +
    'Output the corrected translation (same wire format):'
  );
}

/** The batch's own user message with the problems to fix prepended, so the
 * retry sees exactly what the first attempt saw plus the corrections. */
export function buildFixFlaggedUserMessage(
  userMessage: string, problems: string[],
): string {
  const listed = problems.map((problem) => `- ${problem}`).join('\n');
  return `${FIX_FLAGGED_RULE}\n${listed}\n\n${userMessage}`;
}

export function buildBackTranslationUserMessage(
  sourceLang: string, wire: string,
): string {
  const header = sourceLang
    ? `Translate back to ${sourceLang}:`
    : 'Translate back to the original language:';
  return `${header}\n\n${wire}`;
}

export function buildScanUserMessage(
  sourceLang: string,
  targetLang: string,
  scanText: string,
  dialect = '',
  phrases: string[] = [],
): string {
  const sourceLine = sourceLang ? `Source language: ${sourceLang}\n` : '';
  const variantLine = dialect.trim()
    ? fill(SCAN_DIALECT_RULE, { dialect: dialect.trim() }) + '\n'
    : '';
  const phraseSection = phrases.length
    ? `\n${RECURRING_PHRASES_RULE}\n${phrases.map((p) => `- ${p}`).join('\n')}\n`
    : '';
  return `${sourceLine}Target language: ${targetLang}\n${variantLine}${phraseSection}\n${scanText}`;
}

export function buildAttributionUserMessage(roster: string, sceneLines: string[]): string {
  return `Characters:\n${roster}\n\nScene:\n${sceneLines.join('\n')}`;
}
