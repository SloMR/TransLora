"""One-shot prepass: scan the whole file for cast, recurring terms, and tone
notes before batched translation begins.

The goal is to fix gendered-pronoun errors in languages like Arabic, where
the model must pick masculine/feminine forms but English gives no signal.
We send the full source text once, ask for a compact glossary, and inject
the relevant slice into each batch's prompt.

If the scan fails for any reason, callers get an empty FileContext and
translation proceeds exactly as it did before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from .config import TranslationConfig
from .srt_parser import SubtitleBlock


CONTEXT_SYSTEM_PROMPT = """\
You analyze a subtitle file before it is translated. Output a compact glossary
the translator will use to pick correct pronouns and consistent names.

Output ONLY this exact tagged format — no commentary, no code fences:
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
- GENDER is "male", "female", or "unknown". Use "unknown" only when the text gives no signal at all.
- TARGET_NAME is how the character's name should appear in the target language (transliterated or localized).
- Include up to 20 named characters, 10 recurring proper terms or jargon, 4 brief notes on setting/register/tone.
- Leave a section with just its tags if nothing qualifies.\
"""


_SECTION_RE = re.compile(
    r"<(?P<tag>characters|terms|notes)>\s*(?P<body>.*?)\s*</(?P=tag)>",
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
    characters: list[CharacterHint] = field(default_factory=list)
    terms: list[TermHint] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.characters or self.terms or self.notes)

    def render_for_batch(self, batch: list[SubtitleBlock]) -> str:
        """Return only the glossary slice relevant to this batch.

        Characters and terms are included only if their source form appears
        in the batch text. Notes are always included (short, file-wide).
        Returns an empty string when there is nothing worth injecting.
        """
        text = "\n".join(b.text for b in batch)
        chars = [h for h in self.characters if _contains_word(text, h.source)]
        terms = [h for h in self.terms if _contains_word(text, h.source)]
        if not chars and not terms and not self.notes:
            return ""

        parts: list[str] = []
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
    """Serialize subtitle text for the scan pass — no numbers, no timestamps."""
    return "\n".join(b.text for b in blocks)


def parse_context_response(text: str) -> FileContext:
    """Parse the tagged response. Tolerates extra whitespace and bullet markers."""
    sections = {
        m.group("tag").lower(): m.group("body")
        for m in _SECTION_RE.finditer(text or "")
    }

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

    user_message = (
        f"Source language: {cfg.source_lang}\n"
        f"Target language: {cfg.target_lang}\n\n"
        f"{serialize_for_scan(blocks)}"
    )
    try:
        raw = await call_chat_api(
            client,
            CONTEXT_SYSTEM_PROMPT,
            user_message,
            cfg,
            max_tokens=800,
        )
        return parse_context_response(strip_markdown_fences(raw))
    except Exception as e:
        cfg.warn(f"    Context scan failed, proceeding without: {e}")
        return FileContext()
