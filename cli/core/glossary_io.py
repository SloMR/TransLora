"""The --glossary-in/--glossary-out JSON file: one episode's scan, reused
across a season."""

from __future__ import annotations

import json
from pathlib import Path

from .context_pass import (
    CharacterHint,
    FileContext,
    SceneHint,
    TermHint,
    usable_idioms,
)

GLOSSARY_VERSION = 1


def _hints(raw: object) -> list[TermHint]:
    """`[{"source":..., "target":...}]` entries; anything else is skipped."""
    if not isinstance(raw, list):
        return []
    return [
        TermHint(str(t["source"]), str(t["target"]))
        for t in raw
        if isinstance(t, dict) and t.get("source") and t.get("target")
    ]


def glossary_to_dict(context: FileContext, source_file: str) -> dict:
    """JSON form of a FileContext; `source_file` scopes the scene block numbers."""
    return {
        "translora_glossary": GLOSSARY_VERSION,
        "source_file": source_file,
        "register": context.register,
        "characters": [
            {"source": c.source, "target": c.target, "gender": c.gender}
            for c in context.characters
        ],
        "terms": [{"source": t.source, "target": t.target} for t in context.terms],
        # Applied on the way out as well as in, so a season's shared glossary
        # cannot carry one key in both tables.
        "idioms": [
            {"source": t.source, "target": t.target}
            for t in usable_idioms(context.terms, context.idioms)
        ],
        "scenes": [
            {
                "start": s.start,
                "end": s.end,
                "description": s.description,
                "participants": list(s.participants),
                "attribution": {str(k): v for k, v in s.attribution.items()},
            }
            for s in context.scenes
        ],
        "notes": list(context.notes),
    }


def glossary_from_dict(data: object) -> tuple[FileContext, str]:
    """Inverse of glossary_to_dict, returning (context, source_file). Raises
    ValueError if it is not a glossary; malformed entries are skipped."""
    if not isinstance(data, dict) or "translora_glossary" not in data:
        raise ValueError("not a TransLora glossary file")
    version = data.get("translora_glossary")
    if version != GLOSSARY_VERSION:
        raise ValueError(f"unsupported glossary version: {version!r}")

    characters = [
        CharacterHint(str(c["source"]), str(c["target"]),
                      str(c.get("gender", "unknown")))
        for c in data.get("characters") or []
        if isinstance(c, dict) and c.get("source") and c.get("target")
    ]
    terms = _hints(data.get("terms"))
    # Absent from a glossary written before idioms existed; that is not an error.
    # An already-poisoned file heals as it is read.
    idioms = usable_idioms(terms, _hints(data.get("idioms")))
    scenes: list[SceneHint] = []
    for s in data.get("scenes") or []:
        if not isinstance(s, dict):
            continue
        try:
            start, end = int(s["start"]), int(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        attribution: dict[int, str] = {}
        for num, name in (s.get("attribution") or {}).items():
            try:
                attribution[int(num)] = str(name)
            except (TypeError, ValueError):
                continue
        scenes.append(SceneHint(
            start=min(start, end), end=max(start, end),
            description=str(s.get("description", "")),
            participants=[str(p) for p in s.get("participants") or []],
            attribution=attribution,
        ))

    context = FileContext(
        register=str(data.get("register", "")),
        characters=characters,
        terms=terms,
        idioms=idioms,
        scenes=scenes,
        notes=[str(n) for n in data.get("notes") or []],
    )
    return context, str(data.get("source_file", ""))


def save_glossary(path: Path, context: FileContext, source_file: str) -> None:
    path.write_text(
        json.dumps(glossary_to_dict(context, source_file),
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_glossary(path: Path) -> tuple[FileContext, str]:
    """Read a glossary written by save_glossary. Raises ValueError if unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"could not read glossary {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"glossary {path} is not valid JSON: {e}") from e
    return glossary_from_dict(data)
