"""Prepass scan: extract cast, terms, and register from the whole file once
so every batch shares the same glossary. Fails silently to an empty context."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from .config import TranslationConfig
from .srt_parser import SubtitleBlock


CONTEXT_SYSTEM_PROMPT = """\
You analyze a subtitle file before it is translated. Return a compact glossary
for the translator to use when picking correct pronouns, consistent names, and
a single consistent register.

Your reply MUST start with `<register>` and MUST contain all four sections
below, in this exact order, with no other text before, between, or after them.
No commentary. No code fences. No explanations. Tags only.

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
- Leave a section empty (tags only) if nothing qualifies. Never omit a section.\
"""

# Sized so small-context models (4k-8k) still have room for prompt + output.
_SCAN_CHAR_BUDGET = 12_000
_SCAN_MAX_TOKENS = 1500


_SECTION_RE = re.compile(
    r"<(?P<tag>register|characters|terms|notes)>\s*(?P<body>.*?)\s*</(?P=tag)>",
    re.I | re.S,
)


@dataclass
class CharacterHint:
    source: str
    target: str
    gender: str  # "male" | "female" | "unknown"


@dataclass
class TermHint:
    source: str
    target: str


@dataclass
class FileContext:
    register: str = ""
    characters: list[CharacterHint] = field(default_factory=list)
    terms: list[TermHint] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.register or self.characters or self.terms or self.notes)

    def render_for_batch(self, batch: list[SubtitleBlock]) -> str:
        """Return a glossary slice scoped to names/terms present in this batch.
        Register and notes are file-wide and always included if set."""
        text = "\n".join(b.text for b in batch)
        chars = [h for h in self.characters if _contains_word(text, h.source)]
        terms = [h for h in self.terms if _contains_word(text, h.source)]
        if not self.register and not chars and not terms and not self.notes:
            return ""

        parts: list[str] = []
        if self.register:
            parts.append(f"Target register: {self.register} (use consistently across every block)")
        if chars:
            lines = [f"- {h.source} => {h.target} ({h.gender})" for h in chars]
            parts.append("Characters:\n" + "\n".join(lines))
        if terms:
            lines = [f"- {h.source} => {h.target}" for h in terms]
            parts.append("Terms:\n" + "\n".join(lines))
        if self.notes:
            lines = [f"- {n}" for n in self.notes[:4]]
            parts.append("Notes:\n" + "\n".join(lines))
        return "\n\n".join(parts)


def _contains_word(text: str, word: str) -> bool:
    if not word:
        return False
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.I) is not None


def serialize_for_scan(blocks: list[SubtitleBlock]) -> str:
    """Text for the scan pass. Stride-samples large files so characters
    introduced late still have a chance to land in the glossary."""
    total_chars = sum(len(b.text) + 1 for b in blocks)
    if total_chars <= _SCAN_CHAR_BUDGET or len(blocks) <= 1:
        return "\n".join(b.text for b in blocks)

    take_n = max(1, int(len(blocks) * _SCAN_CHAR_BUDGET / total_chars))
    step = len(blocks) / take_n
    sampled = [blocks[int(i * step)] for i in range(take_n)]
    return "\n".join(b.text for b in sampled)


def parse_context_response(text: str) -> FileContext:
    """Parse the tagged response. Tolerates extra whitespace and bullet markers."""
    sections = {
        m.group("tag").lower(): m.group("body")
        for m in _SECTION_RE.finditer(text or "")
    }

    register = " ".join(sections.get("register", "").split()).strip().lstrip("-*• ").strip()

    characters: list[CharacterHint] = []
    for line in sections.get("characters", "").splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line or "=>" not in line:
            continue
        src, rest = line.split("=>", 1)
        if "|" in rest:
            tgt, gender = rest.rsplit("|", 1)
            tgt, gender = tgt.strip(), gender.strip().lower()
        else:
            tgt, gender = rest.strip(), "unknown"
        if gender not in ("male", "female", "unknown"):
            gender = "unknown"
        src = src.strip()
        if src and tgt:
            characters.append(CharacterHint(src, tgt, gender))

    terms: list[TermHint] = []
    for line in sections.get("terms", "").splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line or "=>" not in line:
            continue
        src, tgt = line.split("=>", 1)
        src, tgt = src.strip(), tgt.strip()
        if src and tgt:
            terms.append(TermHint(src, tgt))

    notes: list[str] = []
    for line in sections.get("notes", "").splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if line:
            notes.append(line)

    return FileContext(
        register=register,
        characters=characters[:20],
        terms=terms[:10],
        notes=notes[:4],
    )


async def extract_file_context(
    client: httpx.AsyncClient,
    blocks: list[SubtitleBlock],
    cfg: TranslationConfig,
) -> FileContext:
    """Run one scan call and return a FileContext. Empty on any failure."""
    from .batch_runner import call_chat_api, strip_markdown_fences

    source_line = f"Source language: {cfg.source_lang}\n" if cfg.source_lang else ""
    user_message = (
        f"{source_line}"
        f"Target language: {cfg.target_lang}\n\n"
        f"{serialize_for_scan(blocks)}"
    )
    try:
        raw = await call_chat_api(
            client,
            CONTEXT_SYSTEM_PROMPT,
            user_message,
            cfg,
            max_tokens=_SCAN_MAX_TOKENS,
        )
    except Exception as e:
        cfg.warn(f"    Context scan failed, proceeding without: {e}")
        return FileContext()

    context = parse_context_response(strip_markdown_fences(raw))
    if context.is_empty():
        # Diagnostic snippet: helps tell whether the model ignored tags, truncated, or refused.
        snippet = (raw or "").strip().replace("\n", " ")[:240]
        cfg.warn(f"    Context scan returned empty glossary. Raw start: {snippet!r}")
    return context
