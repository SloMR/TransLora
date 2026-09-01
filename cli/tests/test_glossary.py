"""Glossary persistence: --glossary-out writes it, --glossary-in reuses it
across a season instead of paying for a scan per episode."""

from __future__ import annotations

import json

import pytest

from core.batch_runner import FileTranslationError
from core.context_pass import CharacterHint, FileContext, SceneHint, TermHint
from core.glossary_io import (
    glossary_from_dict,
    glossary_to_dict,
    load_glossary,
    save_glossary,
)
from core.translator import _load_glossary_for
from tests.conftest import make_blocks


def _context() -> FileContext:
    return FileContext(
        register="Modern Standard Arabic, neutral",
        characters=[CharacterHint("Alice", "أليس", "female"),
                    CharacterHint("Bob", "بوب", "male")],
        terms=[TermHint("headquarters", "المقر")],
        idioms=[TermHint("that ship has sailed", "تعبير مجازي")],
        scenes=[SceneHint(start=10, end=14, description="Alice warns Bob",
                          participants=["Alice", "Bob"],
                          attribution={10: "Alice", 11: "Bob"})],
        notes=["Workplace drama"],
    )


def test_glossary_survives_a_json_roundtrip(tmp_path) -> None:
    path = tmp_path / "cast.json"
    save_glossary(path, _context(), "ep01.srt")

    loaded, source_file = load_glossary(path)

    assert source_file == "ep01.srt"
    assert loaded == _context()
    # Attribution keys are ints again, not the JSON strings.
    assert loaded.scenes[0].attribution == {10: "Alice", 11: "Bob"}


# The shape the graded run's glossary was in: one phrase pinned as a term and
# defined as an idiom, with the definition winning at render time.
_POISONED = {
    "translora_glossary": 1,
    "source_file": "ep01.srt",
    "terms": [{"source": "that's what she said",
               "target": "هذا ما قالته هي"}],
    "idioms": [
        {"source": "That's What She Said",
         "target": "هذا تعبير ساخر يُستخدم للرد على جملة تحتمل معنى مزدوجا"},
        {"source": "break a leg", "target": "بالتوفيق"},
    ],
}


def test_a_poisoned_glossary_heals_when_it_is_read() -> None:
    context, _ = glossary_from_dict(_POISONED)
    assert [t.target for t in context.terms] == ["هذا ما قالته هي"]
    assert [i.source for i in context.idioms] == ["break a leg"]


def test_a_collision_cannot_be_written_back_out(tmp_path) -> None:
    """Healing on read alone would let --glossary-out re-emit the collision
    from a context built some other way."""
    context = FileContext(
        terms=[TermHint("that's what she said", "هذا ما قالته هي")],
        idioms=[TermHint("that's what she said", "شيء آخر")],
    )
    path = tmp_path / "cast.json"
    save_glossary(path, context, "ep01.srt")
    assert json.loads(path.read_text(encoding="utf-8"))["idioms"] == []


def test_glossary_file_is_readable_json(tmp_path) -> None:
    path = tmp_path / "cast.json"
    save_glossary(path, _context(), "ep01.srt")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["translora_glossary"] == 1
    assert data["source_file"] == "ep01.srt"
    # Non-ASCII target names are stored readably, not as \\u escapes.
    assert "أليس" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("payload,fragment", [
    ({"characters": []}, "not a TransLora glossary"),
    ({"translora_glossary": 99}, "unsupported glossary version"),
])
def test_glossary_rejects_foreign_files(payload, fragment) -> None:
    with pytest.raises(ValueError, match=fragment):
        glossary_from_dict(payload)


def test_glossary_skips_malformed_entries_rather_than_failing() -> None:
    data = glossary_to_dict(_context(), "ep01.srt")
    data["characters"].append({"source": "NoTarget"})
    data["terms"].append("not a dict")
    data["scenes"].append({"start": "x", "end": 3, "description": "bad"})

    loaded, _ = glossary_from_dict(data)

    assert [c.source for c in loaded.characters] == ["Alice", "Bob"]
    assert len(loaded.terms) == 1
    assert len(loaded.scenes) == 1


def test_missing_glossary_file_raises_value_error(tmp_path) -> None:
    with pytest.raises(ValueError, match="could not read glossary"):
        load_glossary(tmp_path / "nope.json")


def test_invalid_json_raises_value_error(tmp_path) -> None:
    path = tmp_path / "cast.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_glossary(path)


# === Loading into a run ======================================================


def test_scene_ranges_from_another_episode_are_dropped(tmp_path, cfg, capsys) -> None:
    # Block numbers only mean something in the file they were derived from;
    # cast and terms carry across the season, scene ranges do not.
    path = tmp_path / "cast.json"
    save_glossary(path, _context(), "ep01.srt")
    cfg.glossary_in = path
    cfg.quiet = False

    loaded = _load_glossary_for(tmp_path / "ep02.srt", make_blocks(20), cfg)

    assert loaded.scenes == []
    assert [c.source for c in loaded.characters] == ["Alice", "Bob"]
    assert "dropped 1 scene range(s)" in capsys.readouterr().out


def test_scene_ranges_are_clamped_for_the_file_they_came_from(tmp_path, cfg) -> None:
    path = tmp_path / "cast.json"
    save_glossary(path, _context(), "ep01.srt")
    cfg.glossary_in = path

    loaded = _load_glossary_for(tmp_path / "ep01.srt", make_blocks(12), cfg)

    assert (loaded.scenes[0].start, loaded.scenes[0].end) == (10, 12)


def test_an_unusable_glossary_fails_the_file(tmp_path, cfg) -> None:
    # The user asked for this glossary explicitly; scanning instead would
    # silently spend the money they were trying to save.
    path = tmp_path / "cast.json"
    path.write_text('{"nope": true}', encoding="utf-8")
    cfg.glossary_in = path

    with pytest.raises(FileTranslationError, match="not a TransLora glossary"):
        _load_glossary_for(tmp_path / "ep01.srt", make_blocks(3), cfg)


def test_idioms_survive_the_roundtrip(tmp_path) -> None:
    path = tmp_path / "cast.json"
    save_glossary(path, _context(), "ep01.srt")
    loaded, _ = load_glossary(path)
    assert [(i.source, i.target) for i in loaded.idioms] == [
        ("that ship has sailed", "تعبير مجازي")]


def test_a_glossary_written_before_idioms_existed_still_loads() -> None:
    # No "idioms" key at all: an older season file must keep working.
    data = glossary_to_dict(_context(), "ep01.srt")
    del data["idioms"]
    context, _ = glossary_from_dict(data)
    assert context.idioms == []
    assert len(context.terms) == 1


def test_a_malformed_idiom_entry_is_skipped_not_fatal() -> None:
    data = glossary_to_dict(_context(), "ep01.srt")
    data["idioms"] = [{"source": "ok", "target": "fine"}, {"source": "half"}, 7]
    context, _ = glossary_from_dict(data)
    assert [(i.source, i.target) for i in context.idioms] == [("ok", "fine")]
