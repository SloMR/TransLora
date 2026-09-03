"""The session's answers between runs — everything except the key. Kept in
the platform's own place for a tool's settings (Application Support on macOS,
%APPDATA% on Windows, ~/.config on Linux) as one small JSON file. The key is
never written: it is asked for each session or read from the environment."""

from __future__ import annotations

import json
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "translora"
FILE_NAME = "settings.json"

# What is remembered. Not the key, not the files: the files are the question
# a new session is actually there to answer.
REMEMBERED = ("target", "source", "provider", "api_url", "model", "quality",
              "dialect", "formality", "concurrency", "parallel_files")


def settings_path() -> Path:
    return Path(user_config_dir(APP_NAME, appauthor=False)) / FILE_NAME


def load() -> dict | None:
    """The saved answers, or None when there are none or they are unreadable."""
    path = settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {key: raw[key] for key in REMEMBERED if key in raw}


def save(values: dict) -> Path | None:
    """Write the remembered fields; a failure is not the run's problem."""
    path = settings_path()
    kept = {key: values[key] for key in REMEMBERED if key in values}
    kept.pop("api_key", None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        return None
    return path
