import json

from core import settings_store


def test_the_saved_answers_round_trip_without_the_key(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "settings_path", lambda: tmp_path / "t" / "settings.json")
    path = settings_store.save({"target": "Arabic", "provider": "openai", "api_key": "sk-live",
                                "files": ["a.srt"], "quality": "best"})
    assert path == tmp_path / "t" / "settings.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == {"target": "Arabic", "provider": "openai", "quality": "best"}
    assert settings_store.load() == {"target": "Arabic", "provider": "openai", "quality": "best"}


def test_nothing_saved_or_garbage_reads_as_none(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "settings_path", lambda: target)
    assert settings_store.load() is None
    target.write_text("not json", encoding="utf-8")
    assert settings_store.load() is None
    target.write_text("[1, 2]", encoding="utf-8")
    assert settings_store.load() is None


def test_the_path_is_the_platforms_own_settings_place():
    path = settings_store.settings_path()
    assert path.name == "settings.json" and "translora" in str(path)
