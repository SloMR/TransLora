"""The guided session, driven without a terminal: it asks only what a run
needs, lets every question be stepped back from, keeps the answers between
sessions — never the key — and spells each run as the classic command."""

from collections.abc import Sequence

import pytest

from core.live_status import Colors
from core.session import BACK, Back, Choice, Prompts, Settings, run_session

PLAIN = Colors(enabled=False)


class ScriptedPrompts(Prompts):
    """Answers in order; each answer is matched to the kind of question asked."""

    def __init__(self, answers: Sequence[object]) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []
        self.said: list[str] = []
        self.backs: dict[str, bool] = {}
        self.cleared = 0
        # What is on the screen: answered lines and said lines, in order. A
        # back erases from its mark, so this reads like the terminal would.
        self.screen: list[str] = []

    def begin_step(self) -> int:
        return len(self.screen)

    def erase_since(self, mark: int) -> None:
        del self.screen[mark:]

    def _next(self, message: str) -> object:
        self.asked.append(message)
        if not self.answers:
            raise AssertionError(f"no answer left for: {message}")
        answer = self.answers.pop(0)
        if not isinstance(answer, Back):
            self.screen.append(f"? {message} {answer}")
        return answer

    def select(self, message: str, choices: Sequence[Choice], allow_back: bool = False) -> object:
        self.backs[message] = allow_back
        answer = self._next(message)
        if isinstance(answer, Back):
            assert allow_back, message
            return answer
        # Like questionary: a row's value must never be None, or it becomes
        # the row's label. The flow must not lean on None as a sentinel.
        assert all(value is not None for _, value in choices), message
        values = [value for _, value in choices]
        assert answer in values, (message, answer, values)
        return answer

    def autocomplete(self, message: str, choices: Sequence[str], default: str = "",
                     allow_back: bool = False) -> str | Back:
        self.backs[message] = allow_back
        answer = self._next(message)
        assert not isinstance(answer, Back) or allow_back, message
        return answer if isinstance(answer, Back) else str(answer)

    def text(self, message: str, default: str = "", allow_back: bool = False) -> str | Back:
        self.backs[message] = allow_back
        answer = self._next(message)
        assert not isinstance(answer, Back) or allow_back, message
        return answer if isinstance(answer, Back) else str(answer)

    def password(self, message: str, allow_back: bool = False) -> str | Back:
        self.backs[message] = allow_back
        answer = self._next(message)
        assert not isinstance(answer, Back) or allow_back, message
        return answer if isinstance(answer, Back) else str(answer)

    def path(self, message: str, allow_back: bool = False) -> str | Back:
        self.backs[message] = allow_back
        answer = self._next(message)
        assert not isinstance(answer, Back) or allow_back, message
        return answer if isinstance(answer, Back) else str(answer)

    def say(self, text: str) -> None:
        self.said.append(text)
        self.screen.append(text)

    def clear(self) -> None:
        self.cleared += 1
        self.screen.clear()


class FakeStore:
    def __init__(self, saved: dict | None = None) -> None:
        self.saved = saved
        self.writes: list[dict] = []

    def load(self) -> dict | None:
        return self.saved

    def save(self, values: dict):
        self.writes.append(values)
        return "/fake/settings.json"

    def settings_path(self) -> str:
        return "/fake/settings.json"


def _runner():
    runs: list[tuple[list[str], str]] = []

    def run(argv: list[str], api_key: str) -> int:
        runs.append((argv, api_key))
        return 0
    return run, runs


def _folder(tmp_path, count: int = 1):
    folder = tmp_path / "subs"
    folder.mkdir()
    for i in range(count):
        (folder / f"ep{i + 1}.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    return folder


def _session(prompts, files=(), store=None, key_in_env=False):
    run, runs = _runner()
    store = store or FakeStore()
    status = run_session(list(files), prompts, run, key_in_env, "0.5.0", store=store, colors=PLAIN)
    return status, runs, store


FIRST_ANSWERS = ["Arabic", "", "", "openai", "gpt-5.6-luna", "sk-live", "best", "continue"]


def test_the_first_session_asks_in_order_and_spells_the_command(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([*FIRST_ANSWERS, str(folder), "quit"])
    status, runs, _ = _session(prompts)
    assert status == 0
    assert runs == [([str(folder), "-t", "Arabic", "--provider", "openai",
                      "--quality", "best"], "sk-live")]
    # Asked: target, dialect, source, provider, model, key, quality, options, files, next.
    assert [q.split(" ")[0] for q in prompts.asked][:9] == [
        "Translate", "Dialect", "Source", "Which", "Which", "OpenAI", "Quality", "More", "Subtitle"]
    assert prompts.cleared == 1
    assert any("TransLora 0.5.0" in line for line in prompts.said)


def test_the_answers_are_kept_between_sessions_but_never_the_key(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([*FIRST_ANSWERS, str(folder), "quit"])
    _, _, store = _session(prompts)
    assert store.writes == [{
        "target": "Arabic", "source": "", "provider": "openai",
        "api_url": "https://api.openai.com/v1/chat/completions", "model": "gpt-5.6-luna",
        "quality": "best", "dialect": "", "formality": "auto", "concurrency": None,
        "parallel_files": 1,
    }]
    assert not any("sk-live" in str(w) for w in store.writes)
    assert any("never the key" in line for line in prompts.said)


def test_saved_answers_are_offered_not_pre_chosen_and_only_the_key_is_asked_again(tmp_path):
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    prompts = ScriptedPrompts(["use", "sk-2", str(folder), "quit"])
    _, runs, _ = _session(prompts, store=FakeStore(saved))
    assert any("Last time:" in line for line in prompts.said)
    assert prompts.asked[0] == "Settings"
    assert runs[0] == ([str(folder), "-t", "Arabic", "--provider", "openai",
                        "--quality", "fast"], "sk-2")


def test_back_from_the_file_question_returns_to_the_saved_settings_choice(tmp_path):
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    prompts = ScriptedPrompts(["use", "sk-2", BACK, "use", str(folder), "quit"])
    _, _, _ = _session(prompts, store=FakeStore(saved))
    assert prompts.asked.count("Settings") == 2
    # The key was asked once; the second pass kept it.
    assert prompts.asked.count("OpenAI API key (this session only, never saved)") == 1
    assert prompts.backs["OpenAI API key (this session only, never saved)"] is True


def test_back_from_the_key_after_using_saved_settings_returns_to_the_choice(tmp_path):
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    prompts = ScriptedPrompts(["use", BACK, "use", "sk-2", str(folder), "quit"])
    _, runs, _ = _session(prompts, store=FakeStore(saved))
    assert prompts.asked.count("Settings") == 2
    assert runs[0][1] == "sk-2"
    assert runs[0][1] == "sk-2"


def test_back_on_the_which_setting_menu_returns_to_the_settings_choice(tmp_path):
    # The user's own path: saved settings, "Change some of them", then back.
    # It used to drop forward onto the key question.
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    prompts = ScriptedPrompts(["change", BACK, "use", "sk-2", str(folder), "quit"])
    _, runs, _ = _session(prompts, store=FakeStore(saved))
    assert prompts.asked[:3] == ["Settings", "Which setting?", "Settings"]
    assert runs[0][1] == "sk-2"


def test_back_on_the_first_question_after_start_over_returns_to_the_choice(tmp_path):
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    prompts = ScriptedPrompts(["fresh", BACK, "use", "sk-2", str(folder), "quit"])
    _, runs, _ = _session(prompts, store=FakeStore(saved))
    # The remembered settings are offered again, untouched by the abandoned start.
    assert [line for line in prompts.said if "Last time:" in line].__len__() == 2
    assert all("Arabic" in line for line in prompts.said if "Last time:" in line)
    assert "fast" in runs[0][0]


def test_starting_over_ignores_the_saved_answers(tmp_path):
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    prompts = ScriptedPrompts(["fresh", "Spanish", "", "", "groq", "openai/gpt-oss-20b", "k",
                               "balanced", "continue", str(folder), "quit"])
    _, runs, _ = _session(prompts, store=FakeStore(saved))
    assert runs[0][0][1:] == ["-t", "Spanish", "--provider", "groq", "--quality", "balanced"]


def test_changing_some_saved_answers_before_the_run(tmp_path):
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    from core.session import ask_quality
    prompts = ScriptedPrompts(["change", ask_quality, "best", "done", "k", str(folder), "quit"])
    _, runs, _ = _session(prompts, store=FakeStore(saved))
    assert "best" in runs[0][0]


def test_every_question_can_be_stepped_back_from(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([
        "Spanish",            # target
        BACK,                 # dialect: back to target
        "Arabic",             # target again
        "Saudi Arabic",       # dialect
        BACK,                 # source: back to dialect
        "none",               # dialect: none after all (Enter would keep it)
        "",                   # source
        BACK,                 # provider: back to source
        "English",            # source
        "openai", BACK,       # model list: back to the provider list
        "groq", "openai/gpt-oss-20b", "k",
        BACK,                 # quality: back to provider
        "groq", "openai/gpt-oss-20b",  # key kept: same provider
        "best",
        BACK,                 # options: back to quality
        "best", "continue",
        str(folder), "quit",
    ])
    _, runs, _ = _session(prompts)
    argv = runs[0][0]
    assert argv[1:] == ["-t", "Arabic", "-s", "English", "--provider", "groq",
                        "--quality", "best"]
    assert runs[0][1] == "k"
    # Coming back to a question shows the earlier answer instead of pre-typing it.
    assert any(q.endswith("(now: Spanish)") for q in prompts.asked)


def test_stepping_back_takes_the_answered_line_off_the_screen(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([
        "Spanish", BACK, "Arabic",        # target, back from dialect, target again
        "", "", "openai", BACK,           # dialect, source, provider, back from the model list
        "groq", "openai/gpt-oss-20b", "k", "best", "continue", str(folder), "quit",
    ])
    _session(prompts)
    questions = [line for line in prompts.screen if line.startswith("? ")]
    # Each question appears once, with its final answer: a form, not a log.
    assert sum(q.startswith("? Translate into") for q in questions) == 1
    assert any(q.startswith("? Translate into") and q.endswith(" Arabic") for q in questions)
    assert sum(q.startswith("? Which provider?") for q in questions) == 1
    assert any(q == "? Which provider? groq" for q in questions)
    # The abandoned answers are gone from the screen (the re-asked question may
    # still mention the old one as "(now: Spanish)" in its wording).
    assert not any(q.endswith(" Spanish") or q.endswith(" openai") for q in questions)


def test_back_on_the_saved_settings_path_redraws_the_choice_in_place(tmp_path):
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    prompts = ScriptedPrompts(["change", BACK, "use", "sk-2", str(folder), "quit"])
    _session(prompts, store=FakeStore(saved))
    assert sum("Last time:" in line for line in prompts.screen) == 1
    assert sum(line.startswith("? Settings") for line in prompts.screen) == 1
    assert not any(line.startswith("? Which setting?") for line in prompts.screen)


def test_the_first_question_offers_no_back_and_the_rest_do(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([*FIRST_ANSWERS, str(folder), "quit"])
    _session(prompts)
    first = next(q for q in prompts.asked if q.startswith("Translate into"))
    assert prompts.backs[first] is False
    assert prompts.backs["Which provider?"] is True
    assert prompts.backs["Quality"] is True
    assert prompts.backs["More options"] is True
    assert prompts.backs["Subtitle file or folder"] is True


def test_back_from_a_lone_question_returns_to_the_settings_menu(tmp_path):
    from core.session import ask_quality
    folder = _folder(tmp_path)
    saved = {"target": "Arabic", "provider": "openai", "model": "gpt-5.6-luna",
             "api_url": "https://api.openai.com/v1/chat/completions", "quality": "fast"}
    prompts = ScriptedPrompts(["change", ask_quality, BACK, "done", "k", str(folder), "quit"])
    _, runs, _ = _session(prompts, store=FakeStore(saved))
    assert "fast" in runs[0][0]  # unchanged: the back went to the menu, not into "best"
    assert prompts.asked.count("Which setting?") == 2


def test_back_from_the_key_returns_to_the_model_list(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([
        "Arabic", "", "", "openai", "gpt-5.6-luna",
        BACK,                       # key: back to the model list
        "gpt-5.5", "sk-live",       # a different model, then the key
        "best", "continue", str(folder), "quit",
    ])
    _, runs, _ = _session(prompts)
    argv = runs[0][0]
    assert argv[argv.index("--model") + 1] == "gpt-5.5" and runs[0][1] == "sk-live"
    assert prompts.asked.count("Which OpenAI model?") == 2


def test_the_options_menu_offers_parallelism_register_and_overwrite(tmp_path):
    folder = _folder(tmp_path, count=3)
    answers = [*FIRST_ANSWERS[:-1],
               "parallel_files", "3", "concurrency", "8", "formality", "informal",
               "overwrite", "overwrite", "continue",
               str(folder), "quit"]
    prompts = ScriptedPrompts(answers)
    _, runs, store = _session(prompts)
    argv = runs[0][0]
    for flag, value in (("-pf", "3"), ("-c", "8"), ("--formality", "informal")):
        assert argv[argv.index(flag) + 1] == value
    assert "--force" in argv
    assert any("3 subtitle files found" in line for line in prompts.said)
    # Overwriting is a choice for this session, not something remembered.
    assert "overwrite" not in store.writes[0]
    assert store.writes[0]["parallel_files"] == 3
    # The menu leaves one summary line, not a log of every pick.
    options = [line for line in prompts.screen if line.startswith("? More options")]
    assert options == ["? More options 3 files at a time · 8 parallel requests · casual tone "
                       "· overwrite existing translations"]


def test_continue_leaves_the_options_at_their_defaults(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([*FIRST_ANSWERS, str(folder), "quit"])
    _, runs, _ = _session(prompts)
    assert not any(flag in runs[0][0] for flag in ("-pf", "-c", "--formality", "--force"))


def test_files_on_the_command_line_skip_the_path_question(tmp_path):
    folder = _folder(tmp_path, count=2)
    prompts = ScriptedPrompts([*FIRST_ANSWERS, "quit"])
    _, runs, _ = _session(prompts, files=[str(folder)])
    assert runs[0][0][0] == str(folder)
    assert not any("file or folder" in q for q in prompts.asked)


def test_a_path_without_subtitles_is_asked_again(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([*FIRST_ANSWERS, str(tmp_path / "nope"), str(empty),
                               str(folder), "quit"])
    _, runs, _ = _session(prompts)
    assert runs[0][0][0] == str(folder)
    assert "  That path does not exist." in prompts.said
    assert any("No subtitle files there" in line for line in prompts.said)


def test_a_key_in_the_environment_is_used_without_asking(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts(["Arabic", "", "", "deepseek", "deepseek-v4-flash", "balanced",
                               "continue", str(folder), "quit"])
    _, runs, _ = _session(prompts, key_in_env=True)
    assert runs[0][1] == ""
    assert any("TRANSLORA_API_KEY" in line for line in prompts.said)


def test_a_custom_endpoint_takes_a_url_and_an_optional_model(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([
        "Arabic", "", "", "custom",
        "ftp://nope", "http://127.0.0.1:8080/v1/chat/completions",  # the first is refused
        "",                                                          # model: server default
        "best", "continue", str(folder), "quit",
    ])
    _, runs, _ = _session(prompts)
    argv = runs[0][0]
    assert "--api-url" in argv and "http://127.0.0.1:8080/v1/chat/completions" in argv
    assert "--model" not in argv and "--provider" not in argv
    assert any("http://" in line for line in prompts.said)


def test_more_files_reuse_every_answer(tmp_path):
    a = _folder(tmp_path)
    b = tmp_path / "b"
    b.mkdir()
    (b / "x.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    prompts = ScriptedPrompts([*FIRST_ANSWERS, str(a), "more", str(b), "quit"])
    _, runs, _ = _session(prompts)
    assert [r[0][0] for r in runs] == [str(a), str(b)]
    assert runs[0][0][1:] == runs[1][0][1:]


def test_translating_the_same_files_again_overwrites_without_naming_a_flag(tmp_path):
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([*FIRST_ANSWERS, str(folder), "again", "quit"])
    _, runs, _ = _session(prompts)
    assert "--force" not in runs[0][0] and "--force" in runs[1][0]


def test_changing_a_setting_after_a_run_re_translates_with_force(tmp_path):
    from core.session import ask_more, ask_quality
    folder = _folder(tmp_path)
    prompts = ScriptedPrompts([
        *FIRST_ANSWERS, str(folder),
        "change", ask_quality, "fast",
        ask_more, "formality", "informal", "concurrency", "8", "parallel_files", "2", "continue",
        "done", "same",
        "quit",
    ])
    _, runs, _ = _session(prompts)
    assert len(runs) == 2
    argv = runs[1][0]
    assert "--force" in argv and "fast" in argv
    for flag, value in (("--formality", "informal"), ("-c", "8"), ("-pf", "2")):
        assert argv[argv.index(flag) + 1] == value
    assert prompts.cleared == 2  # a fresh screen for the settings, not over the results


def test_ctrl_c_leaves_the_session():
    class Interrupting(ScriptedPrompts):
        def autocomplete(self, message, choices, default="", allow_back=False):
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _session(Interrupting([]))


def test_settings_spell_only_what_differs_from_the_defaults():
    s = Settings(target="Arabic", provider="openai", api_url="x", model="gpt-5.6-luna",
                 files=["a.srt"])
    assert s.to_argv() == ["a.srt", "-t", "Arabic", "--provider", "openai", "--quality", "best"]
    assert "gpt-5.6-luna" in s.describe() and "Best" in s.describe()
    assert not {"api_key", "files", "overwrite"} & set(s.to_saved())
