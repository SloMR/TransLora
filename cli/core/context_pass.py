"""Prepass scan: one call extracts cast, terms, scenes, and register."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import httpx

from .config import TranslationConfig
from .srt_parser import SubtitleBlock


CONTEXT_SYSTEM_PROMPT = """\
You analyze a subtitle file before it is translated. Return a compact glossary
for the translator to use when picking correct pronouns, consistent names, and
a single consistent register.

Input blocks are prefixed with their block number as `[N] text`.

Reply with all five sections below in this exact order. No commentary, no
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
- Example: `105-119 => Maria reassures Alex about the interview` (use the actual names from YOUR <characters> section).
- Include up to 20 characters, 10 terms, 40 scenes, 4 notes.
- Leave a section empty (tags only) if nothing qualifies. Never omit a section.\
"""

_ATTRIBUTION_SYSTEM_PROMPT = """\
You identify the speaker of each subtitle line in a short scene. Given a
character list and a block-numbered scene excerpt (`[N] text`), reply with
exactly one line per input block as `N=SpeakerName`. SpeakerName MUST be one
of the listed characters or the literal "unknown". No commentary, no fences.\
"""

_SCAN_MAX_TOKENS = 3000
_MIN_NAME_LEN = 3
_ATTRIB_MIN_BLOCKS = 3

_SECTION_RE = re.compile(
    r"<(?P<tag>register|characters|terms|scenes|notes)>\s*"
    r"(?P<body>.*?)\s*"
    r"(?=</(?P=tag)>|<(?:register|characters|terms|scenes|notes)>|\Z)",
    re.I | re.S,
)
_SCENE_RANGE_RE = re.compile(r"^(\d+)\s*(?:-\s*(\d+))?$")
_ATTRIB_LINE_RE = re.compile(r"^\s*(\d+)\s*=\s*(.+?)\s*$")


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
class SceneHint:
    start: int
    end: int
    description: str
    participants: list[str] = field(default_factory=list)
    # Per-block speaker map (block_number -> character source name), filled
    # by refine_scene_attribution.
    attribution: dict[int, str] = field(default_factory=dict)


@dataclass
class FileContext:
    register: str = ""
    characters: list[CharacterHint] = field(default_factory=list)
    terms: list[TermHint] = field(default_factory=list)
    scenes: list[SceneHint] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.register or self.characters or self.terms
                    or self.scenes or self.notes)

    def render_for_batch(self, batch: list[SubtitleBlock]) -> str:
        """Glossary slice scoped to this batch. Register/notes are file-wide."""
        text = "\n".join(b.text for b in batch)
        scenes = _scenes_overlapping(self.scenes, batch)
        # Include characters named in the batch AND scene participants — the
        # latter covers speakers who address each other as "you" without
        # vocatives, so the translator still learns their gender.
        scene_names = {p for s in scenes for p in s.participants}
        chars = [h for h in self.characters
                 if _find_word(text, h.source) >= 0 or h.source in scene_names]
        terms = [h for h in self.terms if _find_word(text, h.source) >= 0]
        if not (self.register or chars or terms or scenes or self.notes):
            return ""

        gender_by = {h.source.casefold(): h.gender for h in self.characters}
        parts: list[str] = []
        if self.register:
            parts.append(f"Target register: {self.register} (use consistently across every block)")
        if chars:
            parts.append("Characters:\n" + "\n".join(
                f"- {h.source} => {h.target} ({h.gender})" for h in chars))
        if terms:
            parts.append("Terms:\n" + "\n".join(
                f"- {h.source} => {h.target}" for h in terms))
        if scenes:
            parts.append(_render_scenes(scenes, gender_by))
        if self.notes:
            parts.append("Notes:\n" + "\n".join(f"- {n}" for n in self.notes[:4]))
        return "\n\n".join(parts)


def _scenes_overlapping(
    scenes: list[SceneHint], batch: list[SubtitleBlock],
) -> list[SceneHint]:
    if not scenes or not batch:
        return []
    first, last = batch[0].number, batch[-1].number
    return [s for s in scenes if s.end >= first and s.start <= last]


def _gender_mark(g: str | None) -> str:
    return "M" if g == "male" else "F" if g == "female" else ""


def _render_scenes(scenes: list[SceneHint], gender_by: dict[str, str]) -> str:
    lines: list[str] = []
    for s in scenes:
        tagged = ", ".join(
            f"{n} ({mark})" if (mark := _gender_mark(gender_by.get(n.casefold()))) else n
            for n in s.participants
        )
        prefix = f"- Blocks {s.start}-{s.end}:"
        lines.append(
            f"{prefix} [{tagged}] — {s.description}" if tagged
            else f"{prefix} {s.description}")
        if s.attribution:
            speakers = " ".join(f"{n}={s.attribution[n]}" for n in sorted(s.attribution))
            lines.append(f"    speakers: {speakers}")
    return (
        "Scene guidance — each entry applies ONLY to its listed block range. "
        "Participants and genders in [brackets]; a 'speakers:' line names the "
        "speaker per block so you pick the right gender for the ADDRESSEE:\n"
        + "\n".join(lines)
    )


def _find_word(text: str, word: str) -> int:
    """Case-insensitive whole-word search with Unicode-aware boundaries.
    Works for Latin, Arabic, CJK, etc. Returns first match index or -1."""
    if not text or not word:
        return -1
    haystack, needle = text.casefold(), word.casefold()
    nlen = len(needle)
    i = 0
    while i <= len(haystack) - nlen:
        j = haystack.find(needle, i)
        if j < 0:
            return -1
        before = text[j - 1] if j > 0 else ""
        after = text[j + nlen] if j + nlen < len(text) else ""
        # isalnum is Unicode-aware.
        if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
            return j
        i = j + 1
    return -1


def _detect_participants(
    text: str, characters: list[CharacterHint],
) -> list[str]:
    """Source names whose source OR target form appears in `text` as a whole
    word, in order of first appearance. Matches both forms because scan
    descriptions often slip into the target language."""
    aliases: list[tuple[str, str]] = []  # (alias, source_name)
    for h in characters:
        if len(h.source) >= _MIN_NAME_LEN:
            aliases.append((h.source, h.source))
        if h.target != h.source and len(h.target) >= _MIN_NAME_LEN:
            aliases.append((h.target, h.source))
    aliases.sort(key=lambda a: len(a[0]), reverse=True)

    first_at: dict[str, int] = {}
    for alias, name in aliases:
        if name in first_at:
            continue
        idx = _find_word(text, alias)
        if idx >= 0:
            first_at[name] = idx
    return sorted(first_at, key=first_at.__getitem__)


def _format_scan_line(b: SubtitleBlock) -> str:
    return f"[{b.number}] " + b.text.replace("\n", " ")


def serialize_for_scan(
    blocks: list[SubtitleBlock], char_budget: int,
) -> str:
    """Text for the scan pass. Stride-samples large files so characters
    introduced late still land in the glossary."""
    total = sum(len(_format_scan_line(b)) + 1 for b in blocks)
    if total <= char_budget or len(blocks) <= 1:
        return "\n".join(_format_scan_line(b) for b in blocks)
    take_n = max(1, int(len(blocks) * char_budget / total))
    step = len(blocks) / take_n
    sampled = [blocks[int(i * step)] for i in range(take_n)]
    return "\n".join(_format_scan_line(b) for b in sampled)


def _strip_bullet(line: str) -> str:
    return line.strip().lstrip("-*• ").strip()


def parse_context_response(text: str) -> FileContext:
    """Parse the tagged response. Tolerates whitespace and bullet markers."""
    sections = {
        m.group("tag").lower(): m.group("body")
        for m in _SECTION_RE.finditer(text or "")
    }

    register = " ".join(sections.get("register", "").split()).strip().lstrip("-*• ").strip()

    characters: list[CharacterHint] = []
    for line in sections.get("characters", "").splitlines():
        line = _strip_bullet(line)
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
        if src.strip() and tgt:
            characters.append(CharacterHint(src.strip(), tgt, gender))

    terms: list[TermHint] = []
    for line in sections.get("terms", "").splitlines():
        line = _strip_bullet(line)
        if not line or "=>" not in line:
            continue
        src, tgt = line.split("=>", 1)
        if src.strip() and tgt.strip():
            terms.append(TermHint(src.strip(), tgt.strip()))

    scenes: list[SceneHint] = []
    for line in sections.get("scenes", "").splitlines():
        line = _strip_bullet(line)
        if not line or "=>" not in line:
            continue
        rng, desc = line.split("=>", 1)
        m = _SCENE_RANGE_RE.match(rng.strip())
        if not m or not desc.strip():
            continue
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if end < start:
            start, end = end, start
        scenes.append(SceneHint(
            start=start, end=end, description=desc.strip(),
            participants=_detect_participants(desc, characters),
        ))

    notes = [_strip_bullet(l) for l in sections.get("notes", "").splitlines() if _strip_bullet(l)]

    return FileContext(
        register=register,
        characters=characters[:20],
        terms=terms[:10],
        scenes=scenes[:80],
        notes=notes[:4],
    )


async def extract_file_context(
    client: httpx.AsyncClient,
    blocks: list[SubtitleBlock],
    cfg: TranslationConfig,
) -> FileContext:
    """Run one scan call. Returns the parsed+enriched context."""
    from .batch_runner import call_chat_api, strip_markdown_fences

    source_line = f"Source language: {cfg.source_lang}\n" if cfg.source_lang else ""
    user_msg = (
        f"{source_line}Target language: {cfg.target_lang}\n\n"
        f"{serialize_for_scan(blocks, cfg.scan_char_budget)}"
    )
    try:
        raw = await call_chat_api(
            client, CONTEXT_SYSTEM_PROMPT, user_msg, cfg,
            max_tokens=_SCAN_MAX_TOKENS,
        )
    except Exception as e:
        cfg.warn(f"    Context scan failed, proceeding without: {e}")
        return FileContext()

    context = parse_context_response(strip_markdown_fences(raw))
    if context.is_empty():
        snippet = (raw or "").strip().replace("\n", " ")[:240]
        cfg.warn(f"    Context scan returned empty glossary. Raw start: {snippet!r}")
    else:
        enrich_scenes_with_block_text(context, blocks)
    return context


def enrich_scenes_with_block_text(
    context: FileContext, blocks: list[SubtitleBlock],
) -> FileContext:
    """Reconcile scene participants with what's actually in the source blocks.
    Block-text names are primary truth: description-named participants are
    kept only if grounded in the text, and any block-text names missed by the
    description are appended."""
    if not context.scenes or not context.characters:
        return context
    by_num = {b.number: b for b in blocks}
    for s in context.scenes:
        joined = "\n".join(
            by_num[n].text for n in range(s.start, s.end + 1) if n in by_num)
        in_text = _detect_participants(joined, context.characters)
        in_text_set = set(in_text)
        kept = [p for p in s.participants if p in in_text_set]
        seen = set(kept)
        for name in in_text:
            if name not in seen:
                kept.append(name)
                seen.add(name)
        s.participants = kept
    return context


def _needs_attribution(scene: SceneHint, gender_by: dict[str, str]) -> bool:
    return (scene.end - scene.start + 1 >= _ATTRIB_MIN_BLOCKS
            and len(scene.participants) >= 1)


async def _attribute_scene(
    client: httpx.AsyncClient,
    scene: SceneHint,
    by_num: dict[int, SubtitleBlock],
    cfg: TranslationConfig,
    characters: list[CharacterHint],
) -> dict[int, str]:
    from .batch_runner import call_chat_api
    present = set(scene.participants)
    roster = "\n".join(
        f"- {h.source} ({_gender_mark(h.gender) or '?'})"
        for h in characters if h.source in present
    )
    block_lines = [
        f"[{n}] {by_num[n].text.replace(chr(10), ' ')}"
        for n in range(scene.start, scene.end + 1) if n in by_num
    ]
    if not block_lines or not roster:
        return {}
    user_msg = f"Characters:\n{roster}\n\nScene:\n" + "\n".join(block_lines)
    try:
        raw = await call_chat_api(
            client, _ATTRIBUTION_SYSTEM_PROMPT, user_msg, cfg,
            max_tokens=len(block_lines) * 20 + 100,
        )
    except Exception as e:
        cfg.warn(f"    Attribution failed for blocks {scene.start}-{scene.end}: {e}")
        return {}
    out: dict[int, str] = {}
    valid = {h.source for h in characters} | {"unknown"}
    for line in (raw or "").splitlines():
        m = _ATTRIB_LINE_RE.match(line)
        if not m:
            continue
        n = int(m.group(1))
        name = m.group(2).strip().strip('"\'')
        if scene.start <= n <= scene.end and name in valid:
            out[n] = name
    return out


async def refine_scene_attribution(
    client: httpx.AsyncClient,
    context: FileContext,
    blocks: list[SubtitleBlock],
    cfg: TranslationConfig,
) -> None:
    """Fill `SceneHint.attribution` for multi-block scenes with named
    participants. One small LLM call per target scene, bounded by concurrency."""
    if not context.scenes or not context.characters:
        return
    gender_by = {h.source.casefold(): h.gender for h in context.characters}
    targets = [s for s in context.scenes if _needs_attribution(s, gender_by)]
    if not targets:
        return
    by_num = {b.number: b for b in blocks}
    sem = asyncio.Semaphore(max(1, cfg.concurrency))

    async def do(scene: SceneHint) -> None:
        async with sem:
            scene.attribution = await _attribute_scene(
                client, scene, by_num, cfg, context.characters)

    await asyncio.gather(*(do(s) for s in targets))
