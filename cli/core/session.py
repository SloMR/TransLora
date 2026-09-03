"""The guided session: `translora` with nothing else. It clears the terminal,
asks for what a run needs with menus, runs, and offers more. Every question
can be stepped back from. The answers are kept between sessions — all but the
key — and offered, never pre-chosen, at the next start.

The prompts sit behind `Prompts` so the flow can be driven by a test without a
terminal; `QuestionaryPrompts` is the real one, arrow keys and all."""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import settings_store
from .cli_args import SUBTITLE_EXTS
from .languages import LANGUAGE_ALIASES, LANGUAGE_SCRIPTS
from .live_status import Colors
from .providers import CUSTOM, PROVIDER_PRESETS, ProviderPreset, price_label
from .quality_presets import QUALITY_PRESETS

API_KEY_ENV = "TRANSLORA_API_KEY"
AUTO_DETECT = "Auto-detect"

Choice = tuple[str, object]


def can_ask() -> bool:
    """Only a person at a terminal is asked; a pipe or a CI job gets an error."""
    return sys.stdin.isatty() and sys.stdout.isatty()


class Back:
    """Returned by a prompt when the person steps back a question."""


BACK = Back()


@dataclass(frozen=True)
class Ask:
    """What a question needs to know about its surroundings."""
    key_in_env: bool
    # Whether there is a question before this one: the first one offers no
    # back, and a lone question re-asked from the settings menu goes back to
    # the menu.
    back: bool = False


class Prompts:
    """What the session asks with. Nothing is pre-chosen: a select starts on
    its first row with nothing marked. Ctrl-C raises KeyboardInterrupt."""

    def select(self, message: str, choices: Sequence[Choice], allow_back: bool = False) -> object:
        raise NotImplementedError

    def autocomplete(self, message: str, choices: Sequence[str], default: str = "",
                     allow_back: bool = False) -> str | Back:
        raise NotImplementedError

    def text(self, message: str, default: str = "", allow_back: bool = False) -> str | Back:
        raise NotImplementedError

    def password(self, message: str, allow_back: bool = False) -> str | Back:
        raise NotImplementedError

    def path(self, message: str, allow_back: bool = False) -> str | Back:
        raise NotImplementedError

    def say(self, text: str) -> None:
        print(text)

    def clear(self) -> None:
        """A fresh screen; nothing on a pipe."""

    def begin_step(self) -> int:
        """A mark on the screen; `erase_since` takes back everything printed
        after it, so a step that is asked again appears where it was."""
        return 0

    def erase_since(self, mark: int) -> None:
        """Nothing on a pipe."""


@dataclass
class Settings:
    """One session's answers. `to_argv` spells them as the classic command, so
    the run itself is the same code path a script would take."""
    target: str = ""
    source: str = ""            # "" is auto-detect
    provider: str | None = None  # a preset key, or None for a custom endpoint
    api_url: str = ""
    model: str = ""
    api_key: str = ""           # this session only; never saved, never on argv
    quality: str = "best"
    dialect: str = ""
    formality: str = "auto"
    concurrency: int | None = None
    parallel_files: int = 1
    # Re-translate files whose output exists; a per-session choice, not saved.
    overwrite: bool = False
    files: list[str] = field(default_factory=list)

    @property
    def preset(self) -> ProviderPreset | None:
        return PROVIDER_PRESETS[self.provider] if self.provider else None

    @classmethod
    def from_saved(cls, saved: dict) -> Settings:
        s = cls()
        for key, value in saved.items():
            if hasattr(s, key) and key not in ("api_key", "files"):
                setattr(s, key, value)
        return s

    def to_saved(self) -> dict:
        values = asdict(self)
        for key in ("api_key", "files", "overwrite"):
            values.pop(key)
        return values

    def to_argv(self, force: bool = False) -> list[str]:
        argv: list[str] = [*self.files, "-t", self.target]
        if self.source:
            argv += ["-s", self.source]
        if self.provider:
            argv += ["--provider", self.provider]
        else:
            argv += ["--api-url", self.api_url]
        if self.model and (self.preset is None or self.model != self.preset.default_model):
            argv += ["--model", self.model]
        argv += ["--quality", self.quality]
        if self.dialect:
            argv += ["--dialect", self.dialect]
        if self.formality != "auto":
            argv += ["--formality", self.formality]
        if self.concurrency is not None:
            argv += ["-c", str(self.concurrency)]
        if self.parallel_files != 1:
            argv += ["-pf", str(self.parallel_files)]
        if force or self.overwrite:
            argv.append("--force")
        return argv

    def describe(self) -> str:
        where = (f"{self.preset.label} · {self.model}" if self.preset
                 else f"{self.api_url} · {self.model or 'server default'}")
        source = self.source or "auto-detected source"
        return (f"{source} → {self.target}"
                + (f" ({self.dialect})" if self.dialect else "")
                + f" · {where} · {QUALITY_PRESETS[self.quality].label}"
                + (f" · {self.parallel_files} files at once" if self.parallel_files > 1 else ""))


def language_choices() -> list[str]:
    names = set(LANGUAGE_SCRIPTS) | set(LANGUAGE_ALIASES)
    return sorted(name.title() for name in names)


def count_subtitle_files(path: str) -> int:
    p = Path(path).expanduser()
    if p.is_file():
        return 1 if p.suffix.lower() in SUBTITLE_EXTS else 0
    return sum(1 for f in p.rglob("*") if f.is_file() and f.suffix.lower() in SUBTITLE_EXTS)


# ---- the questions: each returns BACK to step back, None to go on ------------

def _now(current: str) -> str:
    return f" (now: {current})" if current else ""


def ask_target(p: Prompts, s: Settings, ask: Ask) -> Back | None:
    while True:
        answer = p.autocomplete(f"Translate into which language? (type to search){_now(s.target)}",
                                language_choices(), allow_back=ask.back)
        if isinstance(answer, Back):
            return BACK
        if answer.strip():
            s.target = answer.strip()
            return None
        p.say("  A language is needed.")


CLEAR_WORDS = frozenset({"none", "-"})


def ask_dialect(p: Prompts, s: Settings, ask: Ask) -> Back | None:
    hint = f"(Enter keeps {s.dialect}, 'none' clears it)" if s.dialect else "(Enter for none)"
    answer = p.text(f'Dialect or variant, e.g. "Saudi Arabic" {hint}', allow_back=ask.back)
    if isinstance(answer, Back):
        return BACK
    typed = answer.strip()
    if typed.lower() in CLEAR_WORDS:
        s.dialect = ""
    elif typed or not s.dialect:
        s.dialect = typed
    return None


def ask_source(p: Prompts, s: Settings, ask: Ask) -> Back | None:
    answer = p.autocomplete(f"Source language (Enter for auto-detect){_now(s.source)}",
                            [AUTO_DETECT, *language_choices()], allow_back=ask.back)
    if isinstance(answer, Back):
        return BACK
    s.source = "" if answer.strip() in ("", AUTO_DETECT) else answer.strip()
    return None


def ask_provider(p: Prompts, s: Settings, ask: Ask) -> Back | None:
    presets = [pr for pr in PROVIDER_PRESETS.values() if pr.key != CUSTOM]
    choices: list[Choice] = [
        (f"{pr.label} · {pr.default_model} · {price_label(pr.models[0])}", pr.key)
        for pr in presets
    ]
    choices.append(("Custom or local server (an OpenAI-style chat-completions URL)", CUSTOM))
    while True:
        mark = p.begin_step()
        picked = p.select("Which provider?", choices, allow_back=ask.back)
        if isinstance(picked, Back):
            return BACK
        if picked == CUSTOM:
            if _ask_custom(p, s) is None:
                return None
            p.erase_since(mark)
            continue  # stepped back to the provider list
        preset = PROVIDER_PRESETS[str(picked)]
        while True:
            model_mark = p.begin_step()
            if _ask_model(p, s, preset) is not None:
                p.erase_since(mark)
                break  # back to the provider list
            s.concurrency = None  # the preset's own, unless changed under options
            # Back from the key returns to the model list, not the provider list.
            if isinstance(ask_key(p, s, ask, allow_back=True), Back):
                p.erase_since(model_mark)
                continue
            return None


def _ask_custom(p: Prompts, s: Settings) -> Back | None:
    current_url = s.api_url if s.provider is None else ""
    mark = p.begin_step()
    while True:
        url = p.text(f"Endpoint URL{_now(current_url)}", allow_back=True)
        if isinstance(url, Back):
            return BACK
        url = url.strip() or current_url
        if url.startswith(("http://", "https://")):
            break
        p.say("  The URL has to start with http:// or https://.")
    # Remembered before the model question, so a back from it keeps the URL.
    current_model = s.model if s.provider is None else ""
    s.provider, s.api_url, s.api_key = None, url, ""
    hint = (f" (Enter keeps {current_model})" if current_model
            else " (Enter if the server has only one)")
    model = p.text(f"Model name{hint}", allow_back=True)
    if isinstance(model, Back):
        p.erase_since(mark)
        return _ask_custom(p, s)
    s.model = model.strip() or current_model
    s.concurrency = None
    return None


def _ask_model(p: Prompts, s: Settings, preset: ProviderPreset) -> Back | None:
    choices: list[Choice] = [
        (f"{m.id} · {m.note} · {price_label(m)}", m.id) for m in preset.models
    ]
    choices.append(("Another model (type its name)", "__other__"))
    mark = p.begin_step()
    model = p.select(f"Which {preset.label} model?", choices, allow_back=True)
    if isinstance(model, Back):
        return BACK
    if model == "__other__":
        typed = p.text("Model name", allow_back=True)
        if isinstance(typed, Back):
            p.erase_since(mark)
            return _ask_model(p, s, preset)
        model = typed.strip() or preset.default_model
    if s.provider != preset.key:
        s.api_key = ""  # a key belongs to one provider
    s.provider, s.api_url, s.model = preset.key, preset.api_url, str(model)
    return None


def ask_key(p: Prompts, s: Settings, ask: Ask, allow_back: bool = False) -> Back | None:
    """The key is never remembered, so a session asks for it once per provider."""
    preset = s.preset
    if preset is None or not preset.needs_key or s.api_key:
        return None
    if ask.key_in_env:
        p.say(f"  Using the key in {API_KEY_ENV}.")
        return None
    answer = p.password(f"{preset.label} API key (this session only, never saved)",
                        allow_back=allow_back)
    if isinstance(answer, Back):
        return BACK
    s.api_key = answer
    return None


def ask_quality(p: Prompts, s: Settings, ask: Ask) -> Back | None:
    choices: list[Choice] = [(f"{q.label} · {q.summary}", q.key) for q in QUALITY_PRESETS.values()]
    picked = p.select("Quality", choices, allow_back=ask.back)
    if isinstance(picked, Back):
        return BACK
    s.quality = str(picked)
    return None


def ask_files(p: Prompts, s: Settings, ask: Ask) -> Back | None:
    while True:
        answer = p.path("Subtitle file or folder", allow_back=ask.back)
        if isinstance(answer, Back):
            return BACK
        chosen = Path(answer.strip()).expanduser()
        if not answer.strip() or not chosen.exists():
            p.say("  That path does not exist.")
            continue
        found = count_subtitle_files(str(chosen))
        if found == 0:
            p.say("  No subtitle files there (.srt, .vtt, .ass, .ssa, .sub, .sbv).")
            continue
        s.files = [str(chosen)]
        if found > 1:
            p.say(f"  {found} subtitle files found.")
        return None


CONTINUE = "continue"


FORMALITY_LABELS = {
    "auto": "same tone as the original",
    "formal": "polite tone",
    "informal": "casual tone",
}


def _options_summary(s: Settings) -> str:
    lanes = (f"{s.concurrency} parallel requests" if s.concurrency
             else "the provider's parallel requests")
    files = f"{s.parallel_files} file{'s' if s.parallel_files > 1 else ''} at a time"
    existing = "overwrite existing translations" if s.overwrite else "skip existing translations"
    return f"{files} · {lanes} · {FORMALITY_LABELS[s.formality]} · {existing}"


def ask_more(p: Prompts, s: Settings, ask: Ask) -> Back | None:
    """The knobs that are not quality, offered as a menu so they are seen but
    not forced: how hard to hit the provider, the register, and whether to
    overwrite. Each row asks its value and returns here, drawn in place,
    until Continue leaves one summary line behind."""
    mark = p.begin_step()
    while True:
        lanes = str(s.concurrency) if s.concurrency else "the provider's default"
        choices: list[Choice] = [
            (f"How many files to translate at a time (now: {s.parallel_files})",
             "parallel_files"),
            (f"How many requests to send at once per file (now: {lanes})", "concurrency"),
            (f"Tone of the translation (now: {FORMALITY_LABELS[s.formality]})", "formality"),
            ("What to do with files already translated "
             f"(now: {'overwrite' if s.overwrite else 'skip'})", "overwrite"),
            ("Continue", CONTINUE),
        ]
        p.erase_since(mark)
        picked = p.select("More options", choices, allow_back=ask.back)
        if isinstance(picked, Back):
            return BACK
        if picked == CONTINUE:
            p.erase_since(mark)
            p.say(f"? More options {_options_summary(s)}")
            return None
        if picked == "parallel_files":
            answer = p.text("How many files to translate at a time? More is faster, "
                            f"and costs the same (now: {s.parallel_files})")
            if not isinstance(answer, Back) and answer.strip().isdigit() and int(answer) > 0:
                s.parallel_files = int(answer)
        elif picked == "concurrency":
            answer = p.text("How many requests to send at once per file? Faster, "
                            "until the provider starts refusing "
                            f"(now: {lanes}; 0 for the default)")
            if not isinstance(answer, Back) and answer.strip().isdigit():
                s.concurrency = int(answer) or None
        elif picked == "formality":
            formality = p.select("Tone of the translation", [
                ("Same as the original — polite where it is polite, casual where it is casual",
                 "auto"),
                ('Polite throughout — as if speaking to a stranger: "Would you like some tea?"',
                 "formal"),
                ('Casual throughout — as if speaking to a friend: "Want some tea?"', "informal"),
            ])
            s.formality = str(formality)
        elif picked == "overwrite":
            s.overwrite = p.select("A file was translated before. Translate it again?", [
                ("Skip it — keep the existing translation", "skip"),
                ("Translate it again and overwrite", "overwrite"),
            ]) == "overwrite"


Step = Callable[[Prompts, Settings, Ask], "Back | None"]

FIRST_RUN: list[Step] = [ask_target, ask_dialect, ask_source, ask_provider, ask_quality,
                         ask_more, ask_files]

SETTINGS_MENU: list[tuple[str, Step]] = [
    ("Target language", ask_target),
    ("Dialect or variant", ask_dialect),
    ("Source language", ask_source),
    ("Provider, model or key", ask_provider),
    ("Quality", ask_quality),
    ("More options: speed, tone, existing translations", ask_more),
]


def walk(steps: Sequence[Step], p: Prompts, s: Settings, key_in_env: bool,
         first_back: bool = False) -> bool:
    """Run the steps in order; a step that returns BACK hands control to the
    one before it. The first step is offered a back only when the caller has
    somewhere to take it (`first_back`), and then this returns True."""
    marks: list[int] = []
    i = 0
    while i < len(steps):
        if i == len(marks):
            marks.append(p.begin_step())
        else:
            marks[i] = p.begin_step()
        result = steps[i](p, s, Ask(key_in_env, back=i > 0 or first_back))
        if isinstance(result, Back):
            if i == 0:
                p.erase_since(marks[0])
                return True
            # Take back the answered line, and ask that question where it was.
            p.erase_since(marks[i - 1])
            i -= 1
        else:
            i += 1
    return False


# ---- the session -----------------------------------------------------------

Runner = Callable[[list[str], str], int]


def run_session(
    files: Sequence[str], p: Prompts, run: Runner, key_in_env: bool, version: str,
    store=settings_store, colors: Colors | None = None,
) -> int:
    """Ask, run, offer more. Returns the last run's exit status."""
    c = colors or Colors()
    saved = store.load()
    s = Settings.from_saved(saved) if saved else Settings()
    s.files = list(files)
    _screen(p, c, version)

    if saved:
        remembered = s
        while True:
            s = remembered
            mark = p.begin_step()
            p.say(f"  Last time: {c.cyan(s.describe())}")
            p.say("")
            picked = p.select("Settings", [
                ("Use them", "use"),
                ("Change some of them", "change"),
                ("Start over", "fresh"),
            ])
            # Every back on this path lands here, on the choice, never forward.
            if picked == "fresh":
                s = Settings(files=list(files))
                if walk(_without_files(FIRST_RUN, s), p, s, key_in_env, first_back=True):
                    p.erase_since(mark)
                    continue
                break
            if picked == "change" and _change_some(p, s, key_in_env):
                p.erase_since(mark)
                continue
            if isinstance(ask_key(p, s, Ask(key_in_env), allow_back=True), Back):
                p.erase_since(mark)
                continue
            if not s.files and walk([ask_files], p, s, key_in_env, first_back=True):
                p.erase_since(mark)
                continue
            break
    else:
        walk(_without_files(FIRST_RUN, s), p, s, key_in_env)

    status = 0
    force = False
    while True:
        p.say("")
        p.say(f"  {c.cyan(s.describe())}")
        status = run(s.to_argv(force=force), s.api_key)
        if status == 0:
            store.save(s.to_saved())
        force = False
        while True:
            answer = p.select("Next", [
                ("Translate more files with these settings", "more"),
                ("Translate the same files again, overwriting the output", "again"),
                ("Change a setting", "change"),
                ("Quit", "quit"),
            ])
            if answer == "again":
                force = True
                break
            if answer == "quit":
                p.say("")
                where = store.settings_path()
                p.say(c.dim(f"Settings kept in {where} — never the key."))
                p.say(c.dim("To skip the questions in a script: translora "
                            + " ".join(_quote(a) for a in s.to_argv())))
                return status
            if answer == "more":
                walk([ask_files], p, s, key_in_env)
                break
            _screen(p, c, version)
            p.say(f"  Now: {c.cyan(s.describe())}")
            p.say("")
            _change_some(p, s, key_in_env)
            again = p.select("Then", [
                ("Re-translate the same files with the new settings", "same"),
                ("Translate other files", "other"),
            ])
            force = True
            if again == "other":
                walk([ask_files], p, s, key_in_env)
                force = False
            break


def _without_files(steps: Sequence[Step], s: Settings) -> list[Step]:
    """Files given on the command line are not asked for."""
    return [step for step in steps if step is not ask_files or not s.files]


DONE = "done"


def _change_some(p: Prompts, s: Settings, key_in_env: bool) -> bool:
    """One setting at a time, until the person says they are done. Returns
    True when they stepped back out of the menu instead."""
    mark = p.begin_step()
    while True:
        p.erase_since(mark)
        choices: list[Choice] = [(label, step) for label, step in SETTINGS_MENU]
        choices.append(("Done", DONE))
        which = p.select("Which setting?", choices, allow_back=True)
        if isinstance(which, Back):
            p.erase_since(mark)
            return True
        if which == DONE:
            p.erase_since(mark)
            return False
        step: Step = which  # type: ignore[assignment]
        # Back from a lone question is back to this menu, drawn where it was.
        if isinstance(step(p, s, Ask(key_in_env, back=True)), Back):
            continue
        if step is ask_provider:
            ask_key(p, s, Ask(key_in_env))


def _screen(p: Prompts, c: Colors, version: str) -> None:
    p.clear()
    width = shutil.get_terminal_size((80, 24)).columns
    p.say(f"{c.bold(c.cyan(f'TransLora {version}'))}  {c.dim('Ctrl-C quits · ⌫ or ← steps back')}")
    p.say(c.dim("─" * min(width, 100)))
    p.say("")


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


class QuestionaryPrompts(Prompts):
    """Arrow-key menus in the terminal, on every platform questionary runs on.
    Back is the ⌫ key or the left arrow: at once in a menu; in a typed field
    they do their usual job first and step back once the cursor is at the
    start with nothing left to delete. Stepping back takes the answered line
    off the screen and asks the question where it was, so the screen always
    reads as one form. Colours are the terminal's own sixteen, so they read on
    a light theme and a dark one alike."""

    def __init__(self) -> None:
        import questionary
        self.q = questionary
        # Terminal rows of everything printed since the last clear, in order,
        # so a mark can be taken back exactly.
        self._rows: list[int] = []
        self.style = questionary.Style([
            ("qmark", "fg:ansicyan bold"),
            ("question", "bold"),
            ("answer", "fg:ansicyan bold"),
            ("pointer", "fg:ansicyan bold"),
            ("highlighted", "fg:ansicyan bold"),
            ("selected", "fg:ansicyan"),
            ("instruction", "fg:ansibrightblack"),
            ("separator", "fg:ansibrightblack"),
            ("text", ""),
            ("search_success", "noinherit fg:ansigreen bold"),
            ("search_none", "noinherit fg:ansired bold"),
            # The drop-down while typing: a grey box, the current row inverted,
            # so it stays legible whatever the terminal's background is.
            ("completion-menu", "bg:ansibrightblack fg:ansiwhite"),
            ("completion-menu.completion", "bg:ansibrightblack fg:ansiwhite"),
            ("completion-menu.completion.current", "reverse bold"),
            ("completion-menu.meta.completion", "bg:ansibrightblack fg:ansiwhite"),
            ("scrollbar.background", "bg:ansibrightblack"),
            ("scrollbar.button", "bg:ansiwhite"),
        ])

    def begin_step(self) -> int:
        return len(self._rows)

    def erase_since(self, mark: int) -> None:
        rows = sum(self._rows[mark:])
        del self._rows[mark:]
        if rows and sys.stdout.isatty():
            sys.stdout.write(f"\x1b[{rows}A\x1b[J")
            sys.stdout.flush()

    def say(self, text: str) -> None:
        print(text)
        self._rows.append(_screen_rows(text))

    def clear(self) -> None:
        if sys.stdout.isatty():
            os.system("cls" if os.name == "nt" else "clear")
        self._rows.clear()

    def _ask(self, question, message: str, allow_back: bool, typed: bool,
             shown: Callable[[object], str]) -> object:
        """Run a question; when back is allowed, ⌫ or the left arrow end it
        with BACK — in a typed field only once the cursor is at the start. An
        answer leaves questionary's one summary line on screen; count it."""
        if allow_back:
            from prompt_toolkit.filters import Condition
            from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
            app = question.application
            at_start = Condition(lambda: app.current_buffer.cursor_position == 0)
            bindings = KeyBindings()

            @bindings.add("backspace", filter=at_start if typed else True, eager=True)
            @bindings.add("left", filter=at_start if typed else True, eager=True)
            def _back(event) -> None:
                # Leave nothing behind: an answered question keeps its summary
                # line, a question stepped back from keeps nothing.
                event.app.erase_when_done = True
                event.app.exit(result=BACK)

            existing = app.key_bindings
            app.key_bindings = merge_key_bindings([existing, bindings]) if existing else bindings
        answer = question.unsafe_ask()
        if not isinstance(answer, Back):
            self._rows.append(_screen_rows(f"? {message} {shown(answer)}"))
        return answer

    def select(self, message: str, choices: Sequence[Choice], allow_back: bool = False) -> object:
        items = [self.q.Choice(title=label, value=value) for label, value in choices]
        titles = {value: label for label, value in choices}
        hint = "(↑↓ Enter · ⌫ back)" if allow_back else "(↑↓ Enter)"
        question = self.q.select(message, choices=items, style=self.style,
                                 use_indicator=False, use_shortcuts=False, instruction=hint)
        return self._ask(question, message, allow_back, typed=False,
                         shown=lambda value: titles.get(value, str(value)))

    def autocomplete(self, message: str, choices: Sequence[str], default: str = "",
                     allow_back: bool = False) -> str | Back:
        message = _hint(message, allow_back)
        question = self.q.autocomplete(message, choices=list(choices), default=default,
                                       ignore_case=True, match_middle=True, style=self.style)
        return _text_or_back(self._ask(question, message, allow_back, typed=True, shown=str))

    def text(self, message: str, default: str = "", allow_back: bool = False) -> str | Back:
        message = _hint(message, allow_back)
        question = self.q.text(message, default=default, style=self.style)
        return _text_or_back(self._ask(question, message, allow_back, typed=True, shown=str))

    def password(self, message: str, allow_back: bool = False) -> str | Back:
        message = _hint(message, allow_back)
        question = self.q.password(message, style=self.style)
        return _text_or_back(self._ask(question, message, allow_back, typed=True,
                                       shown=lambda value: "*" * len(str(value))))

    def path(self, message: str, allow_back: bool = False) -> str | Back:
        message = _hint(message, allow_back)
        question = self.q.path(message, style=self.style)
        return _text_or_back(self._ask(question, message, allow_back, typed=True, shown=str))


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _screen_rows(text: str) -> int:
    """How many terminal rows a printed line takes, wrapping included."""
    from prompt_toolkit.utils import get_cwidth
    columns = max(1, shutil.get_terminal_size((80, 24)).columns)
    width = get_cwidth(_ANSI_RE.sub("", text))
    return max(1, -(-width // columns))


def _text_or_back(answer: object) -> str | Back:
    return BACK if isinstance(answer, Back) else str(answer)


def _hint(message: str, allow_back: bool) -> str:
    return f"{message} [⌫ back]" if allow_back else message
