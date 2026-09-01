"""Parsing what the prepass calls send back: the tagged scan sections and the
`N=Speaker` attribution lines."""

from __future__ import annotations

import re

from .context_pass import (
    MAX_IDIOMS,
    MAX_TERMS,
    CharacterHint,
    FileContext,
    SceneHint,
    TermHint,
    detect_participants,
    usable_idioms,
)

_TAGS = "register|characters|terms|idioms|scenes|notes"
_SECTION_RE = re.compile(
    rf"<(?P<tag>{_TAGS})>\s*"
    r"(?P<body>.*?)\s*"
    rf"(?=</(?P=tag)>|<(?:{_TAGS})>|\Z)",
    re.I | re.S,
)
_SCENE_RANGE_RE = re.compile(r"^(\d+)\s*(?:-\s*(\d+))?$")
_ATTRIB_LINE_RE = re.compile(r"^\s*(\d+)\s*=\s*(.+?)\s*$")


def _strip_bullet(line: str) -> str:
    return line.strip().lstrip("-*• ").strip()


def _pairs(body: str) -> list[TermHint]:
    """`SOURCE => TARGET` lines, in order, blanks and bullets tolerated."""
    hints: list[TermHint] = []
    for raw in body.splitlines():
        line = _strip_bullet(raw)
        if not line or "=>" not in line:
            continue
        src, tgt = line.split("=>", 1)
        if src.strip() and tgt.strip():
            hints.append(TermHint(src.strip(), tgt.strip()))
    return hints


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

    terms = _pairs(sections.get("terms", ""))[:MAX_TERMS]
    # Dropped before the budget is applied, so a poisoned entry cannot spend
    # one of the fifteen slots a usable idiom could have had.
    idioms = usable_idioms(terms, _pairs(sections.get("idioms", "")))

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
            participants=detect_participants(desc, characters),
        ))

    notes = [line for raw in sections.get("notes", "").splitlines()
             if (line := _strip_bullet(raw))]

    return FileContext(
        register=register,
        characters=characters[:20],
        terms=terms,
        idioms=idioms[:MAX_IDIOMS],
        scenes=scenes[:40],
        notes=notes[:4],
    )


def parse_attribution_response(
    raw: str, scene: SceneHint, characters: list[CharacterHint],
) -> dict[int, str]:
    """Parse `N=SpeakerName` lines, keeping only in-scene blocks and roster names."""
    valid = {h.source for h in characters} | {"unknown"}
    out: dict[int, str] = {}
    for line in (raw or "").splitlines():
        m = _ATTRIB_LINE_RE.match(line)
        if not m:
            continue
        n = int(m.group(1))
        name = m.group(2).strip().strip('"\'')
        if scene.start <= n <= scene.end and name in valid:
            out[n] = name
    return out
