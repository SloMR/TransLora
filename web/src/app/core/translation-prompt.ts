// All LLM-facing prompts and user-message builders, kept in one place so they
// can be iterated on alongside their counterparts.

import { SubtitleBlock, serializeLite } from './srt-parser';

export const SYSTEM_PROMPT = `You are a subtitle translator. You will receive numbered subtitle blocks (no timestamps) and translate them.

Input format for each block:
<N>
<text, may span multiple lines>

RULES (violating any = corrupt file):
- Output the SAME number of blocks as input. No merging, no skipping.
- Copy block numbers exactly.
- Keep one blank line between blocks.
- Preserve line breaks within each block (same line count).
- Translate each block independently — never combine split sentences.
- Translate faithfully: profanity, slurs, slang — match the original register.
- Conversational tone, concise — must fit the original timing.
- If a glossary is provided, use each character's listed gender for pronouns/verb forms, and the listed target-language name consistently.
- "Scene guidance" entries apply PER BLOCK RANGE only. Match the addressee's gender (not just the speaker's). For exactly-two referents addressed together, use the target's dual form if it has one.
- A \`speakers:\` line (e.g. \`120=Alice 121=Alice 122=Bob\`) names the speaker per block. The ADDRESSEE is usually the other named participant — use the addressee's gender (from [brackets]) for second-person forms.
- "Previous context" blocks (if shown) are read-only — infer speaker/addressee from them, do NOT translate or include them.
- Use ONE consistent register and variant of the target language across every block. If the target language has a standard written form (e.g. Modern Standard Arabic), use it unless the source is clearly colloquial.

DO NOT TRANSLATE (copy verbatim):
- HTML tags, music symbols, formatting tags (\\N, {\\an8})
- Proper nouns, brand names, URLs, credit lines

SHORT BLOCKS like "Oh!", "No!", "Hmm." are the #1 cause of missing blocks. Translate every single one.

Output ONLY the translated .srt blocks. No commentary, no markdown fences.`;

export const REVIEW_SYSTEM_PROMPT = `You are a conservative subtitle translation reviewer. You receive a glossary, source blocks, and a first-pass translation in \`<N>\\ntext\` wire format.

DEFAULT: output the first-pass UNCHANGED. Only fix clear violations of the glossary:
- Wrong addressee gender (pronouns, verb conjugation, adjective ending, honorific level) when the glossary unambiguously names the addressee's gender.
- Character name spelled differently from the target form in the glossary.
- Dual/plural/singular agreement when the glossary explicitly flags the count.

If uncertain, keep the block verbatim. Do NOT rephrase, restyle, or "polish". Same number of blocks, same block numbers, same line-count per block.

Output: same wire format, one blank line between blocks. ALL blocks. No commentary, no fences.`;

export const CONTEXT_SYSTEM_PROMPT = `You analyze a subtitle file before it is translated. Return a compact glossary for the translator to use when picking correct pronouns, consistent names, and a single consistent register.

Input blocks are prefixed with their block number as \`[N] text\`.

Reply with all five sections below in this exact order. No commentary, no fences — tags only.

<register>
ONE LINE describing the target-language variant and formality.
</register>
<characters>
NAME => TARGET_NAME | GENDER
</characters>
<terms>
SOURCE => TARGET
</terms>
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
- <scenes>: every ≥3-block stretch of dialogue between named characters. Name the characters explicitly using the names from <characters> so the translator can apply the right gender per range. Ranges may touch but must not overlap.
- Example: \`105-119 => Maria reassures Alex about the interview\` (use the actual names from YOUR <characters> section).
- Include up to 20 characters, 10 terms, 40 scenes, 4 notes.
- Leave a section empty (tags only) if nothing qualifies. Never omit a section.`;

export const ATTRIBUTION_SYSTEM_PROMPT = `You identify the speaker of each subtitle line in a short scene. Given a character list and a block-numbered scene excerpt (\`[N] text\`), reply with exactly one line per input block as \`N=SpeakerName\`. SpeakerName MUST be one of the listed characters or the literal "unknown". No commentary, no fences.`;

export function buildUserMessage(
  sourceLang: string,
  targetLang: string,
  srtContent: string,
  glossary?: string,
  prevTail: SubtitleBlock[] = [],
): string {
  const header = sourceLang
    ? `Translate from ${sourceLang} to ${targetLang}:`
    : `Translate to ${targetLang}:`;
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
  sections.push(`${header}\n\n${srtContent}`);
  return sections.join('\n\n');
}

export function buildReviewUserMessage(
  batch: SubtitleBlock[],
  firstPass: SubtitleBlock[],
  glossary: string,
): string {
  return (
    `Glossary:\n${glossary}\n\n` +
    `Source blocks:\n${serializeLite(batch)}\n\n` +
    `First-pass translation:\n${serializeLite(firstPass)}\n\n` +
    'Output the corrected translation (same wire format):'
  );
}

export function buildScanUserMessage(
  sourceLang: string,
  targetLang: string,
  scanText: string,
): string {
  const sourceLine = sourceLang ? `Source language: ${sourceLang}\n` : '';
  return `${sourceLine}Target language: ${targetLang}\n\n${scanText}`;
}

export function buildAttributionUserMessage(roster: string, sceneLines: string[]): string {
  return `Characters:\n${roster}\n\nScene:\n${sceneLines.join('\n')}`;
}
