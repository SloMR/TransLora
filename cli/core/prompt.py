"""All LLM-facing prompts and user-message builders, kept in one place so they
can be iterated on and reviewed alongside their counterparts."""

from __future__ import annotations

from collections.abc import Sequence

from .constants import DEFAULT_FORMALITY
from .languages import DEFAULT_MAX_CHARS_PER_LINE, DEFAULT_MAX_LINES
from .srt_parser import SubtitleBlock, serialize_lite

SYSTEM_PROMPT = """\
You are a subtitle translator. You will receive numbered subtitle blocks (no timestamps) and translate them.

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
- A `speakers:` line (e.g. `120=Alice 121=Alice 122=Bob`) names the speaker per block. The ADDRESSEE is usually the other named participant — use the addressee's gender (from [brackets]) for second-person forms.
- "Previous context" blocks (if shown) are read-only — infer speaker/addressee from them, do NOT translate or include them.
- Use ONE consistent register and variant of the target language across every block. If the target language has a standard written form (e.g. Modern Standard Arabic), use it unless the source is clearly colloquial.

DO NOT TRANSLATE (copy verbatim):
- HTML tags, music symbols, formatting tags (\\N, {\\an8}, {\\i1}, {\\i0}, <i>, </i>) - copy every tag through in the same position, opening and closing
- Proper nouns, brand names, URLs, credit lines

SHORT BLOCKS like "Oh!", "No!", "Hmm." are the #1 cause of missing blocks. Translate every single one.

Output ONLY the numbered blocks in exactly the format shown above. Never output a timestamp line — the input has none. No commentary, no markdown fences.\
"""


REVIEW_SYSTEM_PROMPT = """\
You are a conservative subtitle translation reviewer. You receive a glossary, source blocks, and a first-pass translation in the same numbered wire format (block number alone on the first line, then the text lines).

DEFAULT: output the first-pass UNCHANGED. Only fix clear violations of the glossary:
- Wrong addressee gender (pronouns, verb conjugation, adjective ending, honorific level) when the glossary unambiguously names the addressee's gender.
- Character name spelled differently from the target form in the glossary.
- Dual/plural/singular agreement when the scene guidance names exactly which participants are addressed together (two named participants addressed as a pair take the target's dual form if it has one).
- A literal word-for-word rendering of an idiom or set phrase, where the meaning is lost.

If uncertain, keep the block verbatim. Do NOT rephrase, restyle, or "polish". Same number of blocks, same block numbers, same line-count per block.

Output: same wire format, one blank line between blocks. ALL blocks. No commentary, no fences.\
"""


CONTEXT_SYSTEM_PROMPT = """\
You analyze a subtitle file before it is translated. Return a compact glossary
for the translator to use when picking correct pronouns, consistent names, and
a single consistent register.

Input blocks are prefixed with their block number as `[N] text`.

Reply with all six sections below in this exact order. No commentary, no
fences — tags only.

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
- Example: `105-119 => Maria reassures Alex about the interview` (use the actual names from YOUR <characters> section).
- The blocks shown may be a sample of a longer file. Block numbers are the true numbers from the full file, so a scene range may legitimately span numbers you were not shown.
- Include up to 20 characters, 25 terms, 40 scenes, 4 notes.
- Leave a section empty (tags only) if nothing qualifies. Never omit a section.\
"""


BACK_TRANSLATION_SYSTEM_PROMPT = """\
You translate subtitle blocks back into their original language so an
automated check can see how much of the meaning survived. You receive numbered
blocks (the block number alone on its own line, then the text lines) and reply
in the same format.

Translate literally, block by block. Do not polish, do not summarise, do not
add anything. Same number of blocks, same block numbers. No commentary, no
markdown fences.\
"""


ATTRIBUTION_SYSTEM_PROMPT = """\
You identify the speaker of each subtitle line in a short scene. Given a
character list and a block-numbered scene excerpt (`[N] text`), reply with
exactly one line per input block as `N=SpeakerName`. SpeakerName MUST be one
of the listed characters or the literal "unknown". No commentary, no fences.\
"""


# Per-request rules: they carry the target's own norms, so they cannot live in
# the cacheable system prompt.
LINE_LIMIT_RULE = "- Keep each line at or under {max_chars} characters and never exceed {max_lines} lines per block; prefer tighter phrasing over a longer line."
FORMALITY_RULE = "Register: use {formality} address throughout."
DIALECT_RULE = "Target variant: {dialect}. Use it consistently."
SCAN_DIALECT_RULE = "Target variant: {dialect}. Use it as the <register> instead of inferring one."
# The target script's own classic machine-translation errors: per-target, so
# the review system prompt stays static and cacheable.
GRAMMAR_NOTE_RULE = "Target-language checks: {note}"
# Prepended to a re-issued batch, ahead of the user message it already had.
FIX_FLAGGED_RULE = "The previous attempt had these problems - fix ONLY these, keep everything else identical:"
RECURRING_PHRASES_RULE = "Recurring phrases - give each one ONE target rendering and use it everywhere:"


def build_translate_user_message(
    source_lang: str,
    target_lang: str,
    batch_wire: str,
    glossary: str,
    prev_tail: list[SubtitleBlock],
    max_chars: int = DEFAULT_MAX_CHARS_PER_LINE,
    max_lines: int = DEFAULT_MAX_LINES,
    formality: str = DEFAULT_FORMALITY,
    dialect: str = "",
) -> str:
    header = (
        f"Translate from {source_lang} to {target_lang}:"
        if source_lang else f"Translate to {target_lang}:"
    )
    directives = [LINE_LIMIT_RULE.format(max_chars=max_chars, max_lines=max_lines)]
    if formality in ("formal", "informal"):
        directives.append(FORMALITY_RULE.format(formality=formality))
    if dialect.strip():
        directives.append(DIALECT_RULE.format(dialect=dialect.strip()))

    sections: list[str] = []
    if glossary:
        sections.append(f"Glossary for this scene:\n{glossary}")
    if prev_tail:
        # Non-numbered so the parser can't confuse these with real input blocks.
        prev_lines = "\n".join(
            f"  [prev #{b.number}] {b.text.replace(chr(10), ' ')}" for b in prev_tail
        )
        sections.append(
            "Previous context (read-only, do NOT translate or output):\n" + prev_lines
        )
    sections.append("\n".join(directives))
    sections.append(f"{header}\n\n{batch_wire}")
    return "\n\n".join(sections)


def build_review_user_message(
    batch: list[SubtitleBlock],
    first_pass: list[SubtitleBlock],
    glossary: str,
    grammar_note: str = "",
) -> str:
    checks = (
        GRAMMAR_NOTE_RULE.format(note=grammar_note.strip()) + "\n\n"
        if grammar_note.strip() else ""
    )
    return (
        f"Glossary:\n{glossary}\n\n"
        f"{checks}"
        f"Source blocks:\n{serialize_lite(batch)}\n\n"
        f"First-pass translation:\n{serialize_lite(first_pass)}\n\n"
        "Output the corrected translation (same wire format):"
    )


def build_fix_flagged_user_message(
    user_message: str, problems: Sequence[str],
) -> str:
    """The batch's own user message with the problems to fix prepended, so the
    retry sees exactly what the first attempt saw plus the corrections."""
    listed = "\n".join(f"- {problem}" for problem in problems)
    return f"{FIX_FLAGGED_RULE}\n{listed}\n\n{user_message}"


def build_back_translation_user_message(source_lang: str, wire: str) -> str:
    header = (
        f"Translate back to {source_lang}:" if source_lang
        else "Translate back to the original language:"
    )
    return f"{header}\n\n{wire}"


def build_scan_user_message(
    source_lang: str,
    target_lang: str,
    scan_text: str,
    dialect: str = "",
    phrases: Sequence[str] = (),
) -> str:
    source_line = f"Source language: {source_lang}\n" if source_lang else ""
    variant_line = (
        SCAN_DIALECT_RULE.format(dialect=dialect.strip()) + "\n"
        if dialect.strip() else ""
    )
    # Seeded deterministically from the file, so the scan cannot overlook a
    # phrase that repeats: whichever ones it renders come back as <terms>.
    phrase_section = (
        RECURRING_PHRASES_RULE + "\n"
        + "\n".join(f"- {p}" for p in phrases) + "\n\n"
        if phrases else ""
    )
    return (f"{source_line}Target language: {target_lang}\n"
            f"{variant_line}\n{phrase_section}{scan_text}")


def build_attribution_user_message(roster: str, block_lines: list[str]) -> str:
    return f"Characters:\n{roster}\n\nScene:\n" + "\n".join(block_lines)
