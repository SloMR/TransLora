"""Drift guard for the repo's two implementations.

TransLora ships the same pipeline twice — Python in `cli/`, TypeScript in
`web/` — with no shared code, so the prompts and tuning constants are copies
that can silently diverge. This test enforces
it. The TypeScript is read as plain text (no node, no build step) and compared
against the Python that is actually imported.

Prompts are compared paragraph by paragraph with whitespace inside a paragraph
collapsed: the two trees wrap the same text differently (Python hard-wraps at
~78 columns, TypeScript keeps one long line). Wording, ordering and the
blank-line structure are still compared exactly — only the wrap column is
forgiven, so a merged or dropped paragraph still fails.

Beyond constants.ts, the per-script subtitle norms, the script ranges, the
repair thresholds and the glossary-seeding tables are copies too: a line
length, a code point range, a bleeding threshold or a stopword that drifts
would give the same file two different translations depending on which tree
produced it.
Those tables are looked up by declaration name across web/src/app/core rather
than by filename, so moving one between modules is not drift.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from core import config as py_config
from core import constants as py_constants
from core import context_pass as py_context_pass
from core import languages as py_languages
from core import prompt as py_prompt
from core import providers as py_providers
from core import quality_presets as py_quality
from core import repair as py_repair
from core import srt_parser as py_srt_parser

WEB_CORE = Path(__file__).resolve().parents[2] / "web" / "src" / "app" / "core"
PROMPT_TS = WEB_CORE / "translation-prompt.ts"
CONSTANTS_TS = WEB_CORE / "constants.ts"

if not all(p.exists() for p in (PROMPT_TS, CONSTANTS_TS)):
    pytest.skip("web/ tree not present in this checkout", allow_module_level=True)

PROMPT_NAMES = [
    "SYSTEM_PROMPT",
    "REVIEW_SYSTEM_PROMPT",
    "CONTEXT_SYSTEM_PROMPT",
    "BACK_TRANSLATION_SYSTEM_PROMPT",
    "ATTRIBUTION_SYSTEM_PROMPT",
]

# Python name -> TypeScript name. Every constant that exists in both trees;
# a rename on either side fails test_shared_constants_exist_on_both_sides.
SHARED_CONSTANTS = {
    "DEFAULT_BATCH_SIZE": "DEFAULT_BATCH_SIZE",
    "DEFAULT_CONCURRENCY": "DEFAULT_CONCURRENCY",
    "DEFAULT_PARALLEL_FILES": "DEFAULT_PARALLEL_FILES",
    "DEFAULT_MAX_RETRIES": "DEFAULT_MAX_RETRIES",
    "DEFAULT_SCAN_CHAR_BUDGET": "DEFAULT_SCAN_BUDGET",
    "DEFAULT_CONTEXT_OVERLAP": "DEFAULT_CONTEXT_OVERLAP",
    "DEFAULT_REVIEW": "DEFAULT_REVIEW",
    "DEFAULT_REFINE_ATTRIBUTION": "DEFAULT_REFINE_ATTRIBUTION",
    "DEFAULT_REFLOW": "DEFAULT_REFLOW",
    "DEFAULT_FIX_FLAGGED": "DEFAULT_FIX_FLAGGED",
    "DEFAULT_VERIFY_ADEQUACY": "DEFAULT_VERIFY_ADEQUACY",
    "DEFAULT_FULL_ATTRIBUTION": "DEFAULT_FULL_ATTRIBUTION",
    "DEFAULT_FORMALITY": "DEFAULT_FORMALITY",
    "FORMALITY_CHOICES": "FORMALITY_CHOICES",
    "DEFAULT_DIALECT": "DEFAULT_DIALECT",
    "SCAN_MAX_TOKENS": "SCAN_MAX_TOKENS",
    "MIN_NAME_LEN": "MIN_NAME_LEN",
    "ATTRIB_MIN_BLOCKS": "ATTRIB_MIN_BLOCKS",
    "FIX_FLAGGED_FRACTION": "FIX_FLAGGED_FRACTION",
    "FIX_FLAGGED_MIN": "FIX_FLAGGED_MIN",
    "ADEQUACY_SAMPLE_FRACTION": "ADEQUACY_SAMPLE_FRACTION",
    "ADEQUACY_MIN_BATCHES": "ADEQUACY_MIN_BATCHES",
    "ADEQUACY_MIN_OVERLAP": "ADEQUACY_MIN_OVERLAP",
    "ADEQUACY_MIN_SOURCE_WORDS": "ADEQUACY_MIN_SOURCE_WORDS",
    "ESTIMATED_SECS_PER_CALL": "ESTIMATED_SECS_PER_CALL",
    "ATTEMPTS_BEFORE_SPLIT": "ATTEMPTS_BEFORE_SPLIT",
    "MAX_RETRY_DELAY_SECS": "MAX_RETRY_DELAY_SECS",
    "REQUEST_TIMEOUT_SECS": "REQUEST_TIMEOUT_SECS",
    "CRED_QUERY_PARAMS": "CRED_QUERY_PARAMS",
    "REQUEST_TEMPERATURE": "REQUEST_TEMPERATURE",
    "TOKEN_PARAM_DEFAULT": "TOKEN_PARAM_DEFAULT",
    "TOKEN_PARAM_COMPLETION": "TOKEN_PARAM_COMPLETION",
    "DEFAULT_SEND_TEMPERATURE": "DEFAULT_SEND_TEMPERATURE",
    "MAX_DIALECT_CORRECTIONS": "MAX_DIALECT_CORRECTIONS",
    "REASONING_BUDGET_MULTIPLIER": "REASONING_BUDGET_MULTIPLIER",
}

# Deliberate: the CLI defaults to a local server (1), the web app
# to a cloud provider (5). Anything else diverging is a bug in one of the trees.
KNOWN_DIVERGENT = {"DEFAULT_CONCURRENCY"}

_TS_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "0": "\0"}


def _ts_template_literal(source: str, name: str) -> str:
    """The first backtick literal in `export const <name> = ...`, unescaped.
    Covers both a bare constant and an arrow function returning one."""
    start = source.find(f"export const {name} ")
    assert start >= 0, f"{PROMPT_TS.name} has no `export const {name}`"
    i = source.find("`", start)
    assert i >= 0, f"{name} has no template literal"
    i += 1
    out: list[str] = []
    while i < len(source):
        char = source[i]
        if char == "\\":
            nxt = source[i + 1]
            out.append(_TS_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if char == "`":
            return "".join(out)
        out.append(char)
        i += 1
    raise AssertionError(f"unterminated template literal for {name}")


def _ts_constants(source: str) -> dict[str, object]:
    return {
        m.group(1): _ts_value(m.group(2))
        for m in re.finditer(r"^export const (\w+)\s*=\s*(.+?);\s*$",
                             source, re.M)
    }


def _ts_value(raw: str) -> object:
    raw = raw.strip()
    if raw.endswith("as const"):
        raw = raw[: -len("as const")].strip()
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith("["):
        return frozenset(re.findall(r"['\"]([^'\"]*)['\"]", raw))
    number = raw.replace("_", "")  # TS numeric separators: 24_000
    try:
        return int(number)
    except ValueError:
        pass
    try:
        return float(number)
    except ValueError:
        return raw.strip("'\"")


def _paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip())]


def _same_value(py_value: object, ts_value: object) -> bool:
    if isinstance(py_value, bool) or isinstance(ts_value, bool):
        return py_value is ts_value
    if isinstance(py_value, (int, float)) and isinstance(ts_value, (int, float)):
        return float(py_value) == float(ts_value)
    if isinstance(py_value, (frozenset, set, list, tuple)) and \
            isinstance(ts_value, (frozenset, set, list, tuple)):
        return set(py_value) == set(ts_value)
    return py_value == ts_value


TS_PROMPT_SOURCE = PROMPT_TS.read_text(encoding="utf-8")
TS_CONSTANTS = _ts_constants(CONSTANTS_TS.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_prompts_are_identical_in_both_trees(name: str) -> None:
    py_text = getattr(py_prompt, name)
    ts_text = _ts_template_literal(TS_PROMPT_SOURCE, name)
    py_paras, ts_paras = _paragraphs(py_text), _paragraphs(ts_text)

    # Not strict: a differing paragraph count is reported below, with names.
    for index, (mine, theirs) in enumerate(zip(py_paras, ts_paras, strict=False)):
        assert mine == theirs, (
            f"{name} drifted at paragraph {index + 1}:\n"
            f"  cli/core/prompt.py: {mine!r}\n"
            f"  web/src/app/core/translation-prompt.ts: {theirs!r}"
        )
    assert len(py_paras) == len(ts_paras), (
        f"{name} has {len(py_paras)} paragraph(s) in cli/core/prompt.py but "
        f"{len(ts_paras)} in web/src/app/core/translation-prompt.ts; "
        f"first unmatched: "
        f"{(py_paras + ts_paras)[min(len(py_paras), len(ts_paras))]!r}"
    )


def test_shared_constants_exist_on_both_sides() -> None:
    missing_py = [n for n in SHARED_CONSTANTS if not hasattr(py_constants, n)]
    missing_ts = [ts for ts in SHARED_CONSTANTS.values() if ts not in TS_CONSTANTS]
    assert not missing_py, (
        f"missing from cli/core/constants.py: {missing_py} — renamed without "
        f"updating SHARED_CONSTANTS in this test?")
    assert not missing_ts, (
        f"missing from web/src/app/core/constants.ts: {missing_ts} — renamed "
        f"without updating SHARED_CONSTANTS in this test?")


@pytest.mark.parametrize(
    "py_name",
    sorted(n for n in SHARED_CONSTANTS if n not in KNOWN_DIVERGENT),
)
def test_shared_constants_have_the_same_value(py_name: str) -> None:
    ts_name = SHARED_CONSTANTS[py_name]
    py_value = getattr(py_constants, py_name)
    ts_value = TS_CONSTANTS[ts_name]
    assert _same_value(py_value, ts_value), (
        f"{py_name} drifted: cli/core/constants.py has {py_value!r}, "
        f"web/src/app/core/constants.ts {ts_name} has {ts_value!r}"
    )


def test_only_the_documented_constant_diverges() -> None:
    """Catches both a new divergence and an allowlist entry that went stale."""
    diverged = {
        py_name
        for py_name, ts_name in SHARED_CONSTANTS.items()
        if ts_name in TS_CONSTANTS
        and not _same_value(getattr(py_constants, py_name, None),
                            TS_CONSTANTS[ts_name])
    }
    assert diverged == KNOWN_DIVERGENT, (
        f"undocumented divergence: {sorted(diverged - KNOWN_DIVERGENT)}; "
        f"no longer divergent (drop from KNOWN_DIVERGENT): "
        f"{sorted(KNOWN_DIVERGENT - diverged)}"
    )


def test_documented_concurrency_divergence_still_matches_claude_md() -> None:
    assert py_constants.DEFAULT_CONCURRENCY == 1
    assert TS_CONSTANTS["DEFAULT_CONCURRENCY"] == 5


# === Per-request rules =======================================================

# The line-length, formality and dialect rules live in the user message, not
# the cacheable system prompt, so the paragraph comparison above never sees
# them. Both sides are rendered with the same values before comparing, so the
# wording is compared without pinning how either tree does substitution.
RULE_ARGUMENTS: dict[str, dict[str, object]] = {
    "LINE_LIMIT_RULE": {"max_chars": 16, "max_lines": 2},
    "FORMALITY_RULE": {"formality": "formal"},
    "DIALECT_RULE": {"dialect": "Egyptian Arabic"},
    "SCAN_DIALECT_RULE": {"dialect": "Egyptian Arabic"},
    "GRAMMAR_NOTE_RULE": {"note": "Keep one speech level throughout."},
    "FIX_FLAGGED_RULE": {},
    "RECURRING_PHRASES_RULE": {},
}

# TypeScript-side spellings of the same placeholders, for the `${...}` form.
TS_PLACEHOLDERS = {"maxChars": "max_chars", "maxLines": "max_lines"}


def _render_ts(template: str, values: dict) -> str:
    def fill(match: re.Match) -> str:
        name = match.group(1)
        return str(values[TS_PLACEHOLDERS.get(name, name)])

    return re.sub(r"\{(\w+)\}", fill, re.sub(r"\$\{(\w+)\}", fill, template))


@pytest.mark.parametrize("name", sorted(RULE_ARGUMENTS))
def test_per_request_rules_are_identical_in_both_trees(name: str) -> None:
    values = RULE_ARGUMENTS[name]
    mine = getattr(py_prompt, name).format(**values)
    theirs = _render_ts(_ts_template_literal(TS_PROMPT_SOURCE, name), values)
    assert mine == theirs, (
        f"{name} drifted:\n  cli/core/prompt.py: {mine!r}\n"
        f"  web/src/app/core/translation-prompt.ts: {theirs!r}"
    )


# === Assembled user messages =================================================

# The rules above are compared as text, but the whitespace that frames them is
# assembled inside the builders, and the two trees assemble it differently:
# Python puts the blank line before the phrase section, TypeScript after. The
# outputs match today and nothing pinned that they must, so the recurring-phrase
# section — the newest of them — is pinned here as a whole rendered message.
# The identical golden lives in web/src/app/core/translation-prompt.spec.ts
# ('renders the whole scan message exactly'); change one and change the other.
SCAN_MESSAGE_GOLDEN = (
    "Source language: English\n"
    "Target language: Arabic\n"
    "Target variant: Egyptian Arabic. Use it as the <register> instead of "
    "inferring one.\n"
    "\n"
    "Recurring phrases - give each one ONE target rendering and use it "
    "everywhere:\n"
    "- safety briefing\n"
    "- that ship has sailed\n"
    "\n"
    "[1] Hi"
)


def test_the_assembled_scan_message_matches_the_web_golden() -> None:
    assert py_prompt.build_scan_user_message(
        "English", "Arabic", "[1] Hi", "Egyptian Arabic",
        ["safety briefing", "that ship has sailed"],
    ) == SCAN_MESSAGE_GOLDEN


def test_the_phrase_list_is_separated_from_the_blocks_by_a_blank_line() -> None:
    """The joint the two trees spell differently: a phrase list that ran into
    the blocks would read as one more block to translate."""
    for dialect in ("", "Egyptian Arabic"):
        message = py_prompt.build_scan_user_message(
            "English", "Arabic", "[1] Hi", dialect, ["safety briefing"])
        assert message.endswith("- safety briefing\n\n[1] Hi")


# The review message is assembled around the grammar note the same way in both
# trees; the identical golden lives in translation-prompt.spec.ts.
REVIEW_MESSAGE_GOLDEN = (
    "Glossary:\nCharacters:\n- Alice\n\n"
    "Target-language checks: Keep one speech level throughout.\n\n"
    "Source blocks:\n1\nHi\n\n\n"
    "First-pass translation:\n1\n안녕\n\n\n"
    "Output the corrected translation (same wire format):"
)


def test_the_assembled_review_message_matches_the_web_golden() -> None:
    batch = [py_srt_parser.SubtitleBlock(1, "ts", "Hi")]
    first_pass = [py_srt_parser.SubtitleBlock(1, "ts", "안녕")]
    assert py_prompt.build_review_user_message(
        batch, first_pass, "Characters:\n- Alice",
        "Keep one speech level throughout.",
    ) == REVIEW_MESSAGE_GOLDEN


def test_a_target_with_no_grammar_note_adds_no_checks_line() -> None:
    """Latin targets get no note, and an empty note must not leave a stray
    heading behind in either tree."""
    batch = [py_srt_parser.SubtitleBlock(1, "ts", "Hi")]
    message = py_prompt.build_review_user_message(batch, batch, "g", "")
    assert "Target-language checks" not in message
    assert message.startswith("Glossary:\ng\n\nSource blocks:")


def test_the_flagged_retry_prepends_the_problems_to_the_same_message() -> None:
    message = py_prompt.build_fix_flagged_user_message(
        "ORIGINAL MESSAGE", ["block 1: a", "block 2: b"])
    assert message == (
        py_prompt.FIX_FLAGGED_RULE + "\n- block 1: a\n- block 2: b\n\n"
        "ORIGINAL MESSAGE")


def test_the_back_translation_message_names_the_source_language() -> None:
    assert py_prompt.build_back_translation_user_message("English", "1\nHi\n") == (
        "Translate back to English:\n\n1\nHi\n")
    assert py_prompt.build_back_translation_user_message("", "1\nHi\n") == (
        "Translate back to the original language:\n\n1\nHi\n")


def test_the_system_prompt_carries_no_hardcoded_line_length() -> None:
    """A number baked into either system prompt would contradict the other
    tree's per-target rule for every non-Latin script."""
    for source in (py_prompt.SYSTEM_PROMPT,
                   _ts_template_literal(TS_PROMPT_SOURCE, "SYSTEM_PROMPT")):
        assert "42" not in source
        assert "characters or fewer per line" not in source


# === Shared tables outside constants.ts ======================================


def _strip_comments(source: str) -> str:
    return "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("//"))


def _ts_declaration(source: str, name: str) -> str:
    """The right-hand side of `const <name>[: type] = <value>;`."""
    match = re.search(rf"^(?:export )?const {name}\b[^=]*=", source, re.M)
    assert match, f"no `const {name}` declaration found"
    depth = 0
    for i in range(match.end(), len(source)):
        char = source[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ";" and depth == 0:
            return source[match.end():i]
    raise AssertionError(f"unterminated declaration for {name}")


def _ts_strings(source: str, name: str) -> frozenset[str]:
    """A `['a', 'b']` or `new Set([...])` declaration, as a set. Either quote
    style: a word with an apostrophe in it has to be double-quoted."""
    return frozenset(a or b for a, b in re.findall(
        r"'([^']*)'|\"([^\"]*)\"", _ts_declaration(source, name)))


def _ts_record_blocks(source_body: str) -> dict[str, str]:
    """key -> the bracketed value that follows it, for a `{ k: [...] }` record.
    Bracket-balanced, so a record of arrays of arrays still splits correctly."""
    blocks: dict[str, str] = {}
    for match in re.finditer(r"(?:'([^']*)'|(\w+))\s*:\s*\[", source_body):
        key = match.group(1) or match.group(2)
        depth, i = 1, match.end()
        while i < len(source_body) and depth:
            depth += (source_body[i] == "[") - (source_body[i] == "]")
            i += 1
        blocks[key] = source_body[match.end():i - 1]
    return blocks


def _ts_string_record(source: str, name: str) -> dict[str, str]:
    """A `{ key: 'value' }` record; the key may or may not be quoted."""
    body = _strip_comments(_ts_declaration(source, name))
    pairs = re.findall(r"(?:'([^']*)'|(\w+))\s*:\s*'([^']*)'", body)
    return {quoted or bare: value for quoted, bare, value in pairs}


WEB_SOURCES = {
    path.name: _strip_comments(path.read_text(encoding="utf-8"))
    for path in sorted(WEB_CORE.glob("*.ts"))
    if not path.name.endswith(".spec.ts")
}


# TypeScript wraps a long message by concatenating literals, and where it wraps
# is not drift. Joining them back up lets a sentence be compared as one string.
_TS_CONCAT_RE = re.compile(r"['\"`]\s*\+\s*['\"`]")

WEB_PROSE = {name: _TS_CONCAT_RE.sub("", source)
             for name, source in WEB_SOURCES.items()}


def _ts_source_declaring(name: str) -> str:
    found = [source for source in WEB_SOURCES.values()
             if re.search(rf"^(?:export )?const {name}\b", source, re.M)]
    assert found, f"no module in web/src/app/core declares `const {name}`"
    return found[0]


def test_the_script_norms_table_is_identical_in_both_trees() -> None:
    body = _ts_declaration(_ts_source_declaring("SCRIPT_TABLE"), "SCRIPT_TABLE")
    theirs = {
        (py_languages.DEFAULT_SCRIPT if key == "[DEFAULT_SCRIPT]" else key):
            (int(chars), int(lines), int(cps))
        for key, chars, lines, cps in re.findall(
            r"(\w+|\[DEFAULT_SCRIPT\])\s*:\s*\[(\d+),\s*(\d+),\s*(\d+)\]", body)
    }
    mine = {
        name: (n.max_chars_per_line, n.max_lines, n.target_cps)
        for name, n in py_languages.SCRIPT_NORMS.items()
    }
    assert mine == theirs


def test_the_language_to_script_map_is_identical_in_both_trees() -> None:
    body = _ts_declaration(_ts_source_declaring("LANGUAGES_BY_SCRIPT"),
                            "LANGUAGES_BY_SCRIPT")
    theirs: dict[str, str] = {}
    for key, languages in re.findall(
            r"(\w+|\[DEFAULT_SCRIPT\])\s*:\s*\[([^\]]*)\]", body):
        script = py_languages.DEFAULT_SCRIPT if key == "[DEFAULT_SCRIPT]" else key
        for language in re.findall(r"'([^']*)'", languages):
            theirs[language] = script
    assert theirs == py_languages.LANGUAGE_SCRIPTS


def test_the_language_aliases_are_identical_in_both_trees() -> None:
    assert _ts_string_record(
        _ts_source_declaring("LANGUAGE_ALIASES"),
        "LANGUAGE_ALIASES") == py_languages.LANGUAGE_ALIASES


def test_the_grammar_notes_are_identical_in_both_trees() -> None:
    """A note that drifts changes what the reviewer is told to check for one
    whole script — invisible to every other test in either tree."""
    theirs = _ts_string_record(
        _ts_source_declaring("GRAMMAR_NOTES"), "GRAMMAR_NOTES")
    assert theirs == py_languages.GRAMMAR_NOTES


def test_every_script_has_a_grammar_note_entry() -> None:
    """A missing key would silently mean "no checks" for that script."""
    assert set(py_languages.GRAMMAR_NOTES) == set(py_languages.SCRIPT_NORMS)


# Both trees are searched by declaration name, not filename, so moving a table
# between modules is not drift.
PY_TABLE_MODULES = (py_languages, py_srt_parser, py_repair, py_context_pass,
                    py_constants)


def _both_sides(name: str) -> tuple[Any, str]:
    """The Python value and the TypeScript source that declares the same name."""
    for module in PY_TABLE_MODULES:
        if hasattr(module, name):
            return getattr(module, name), _ts_source_declaring(name)
    names = ", ".join(m.__name__ for m in PY_TABLE_MODULES)
    raise AssertionError(f"{name} is in none of: {names}")


def test_the_script_ranges_are_identical_in_both_trees() -> None:
    """The ranges decide which letters count as foreign in a translation: one
    that drifts makes a cue a leak in one tree and clean in the other."""
    mine, ts_source = _both_sides("SCRIPT_RANGES")
    body = _strip_comments(_ts_declaration(ts_source, "SCRIPT_RANGES"))
    number = r"0[xX][0-9a-fA-F]+|\d+"
    theirs = {
        key: [(int(low, 0), int(high, 0)) for low, high in
              re.findall(rf"\[\s*({number})\s*,\s*({number})\s*\]", block)]
        for key, block in _ts_record_blocks(body).items()
    }
    assert {name: list(ranges) for name, ranges in mine.items()} == theirs


def test_the_target_script_map_is_identical_in_both_trees() -> None:
    mine, ts_source = _both_sides("TARGET_SCRIPTS")
    body = _strip_comments(_ts_declaration(ts_source, "TARGET_SCRIPTS"))
    theirs = {key: tuple(re.findall(r"'([^']*)'", block))
              for key, block in _ts_record_blocks(body).items()}
    assert mine == theirs


def test_every_classified_script_can_be_a_target() -> None:
    """A range set with no target entry could never be checked against."""
    assert set(py_repair.TARGET_SCRIPTS) - set(py_repair.SCRIPT_RANGES) == \
        {"japanese", "korean"}
    assert set(py_repair.SCRIPT_RANGES) - set(py_repair.TARGET_SCRIPTS) == \
        {"kana", "hangul"}


def test_the_rtl_punctuation_map_is_identical_in_both_trees() -> None:
    mine, ts_source = _both_sides("RTL_PUNCTUATION")
    assert _ts_string_record(ts_source, "RTL_PUNCTUATION") == mine


def test_the_cjk_punctuation_map_is_identical_in_both_trees() -> None:
    mine, ts_source = _both_sides("CJK_PUNCTUATION")
    assert _ts_string_record(ts_source, "CJK_PUNCTUATION") == mine


@pytest.mark.parametrize("name", [
    "RTL_SCRIPTS", "ARABIC_PUNCTUATION_SCRIPTS", "CJK_PUNCTUATION_SCRIPTS",
    "NO_SPACE_SCRIPTS",
    "DIALOGUE_DASHES", "TERMINAL_MARKS",
    # A word only one tree treats as filler changes which phrases get pinned,
    # and so which renderings the glossary fixes.
    "PHRASE_STOPWORDS",
])
def test_shared_string_sets_are_identical_in_both_trees(name: str) -> None:
    mine, ts_source = _both_sides(name)
    assert frozenset(mine) == _ts_strings(ts_source, name)


@pytest.mark.parametrize("name,value", [
    # Cross-cue bleeding: how short a cue is beneath notice, and how much of a
    # run two output cues must share before it counts as content that moved.
    ("SHIFT_MIN_SOURCE_CHARS", 15),
    ("SHIFT_MIN_RUN_WORDS", 1),
    ("SHIFT_MIN_RUN_CHARS", 4),
    # How much of the shorter cue the run must be, and how far it must exceed
    # the source pair's own shared run.
    ("SHIFT_MIN_COVERAGE", 0.45),
    ("SHIFT_SOURCE_RATIO", 2.0),
    # How much of a foreign-script run a leak warning quotes.
    ("LEAK_SAMPLE_CHARS", 12),
    # Orthographic register: the file's baseline, and the per-cue trigger.
    ("DIACRITIC_BASELINE_MAX", 1),
    ("DIACRITIC_CUE_MIN", 5),
    # When a repeated cause stops being a scatter of one-offs, and how much of
    # the file answering it may then cost.
    ("SYSTEMATIC_CAUSE_MIN", 4),
    ("SYSTEMATIC_REPAIR_FRACTION", 0.25),
    # What separates an idiom's target from a definition of the idiom.
    ("IDIOM_MAX_EXPANSION", 2.5),
    ("IDIOM_MAX_TARGET_CHARS", 40),
    # Share of a file's lines that must carry a variant's own function words
    # before the run says the target variant was ignored.
    ("VARIANT_DRIFT_FRACTION", 0.15),
    # The file-level consistency check's own phrase floor — deliberately
    # below the scan's, which is rationing term slots — and how often a phrase
    # must recur before its renderings are evidence of a split.
    ("CONSISTENCY_MIN_CHARS", 8),
    ("CONSISTENCY_MIN_OCCURRENCES", 4),
    # Glossary size and the recurring-phrase seeding that fills it.
    ("MAX_TERMS", 25),
    ("MAX_IDIOMS", 15),
    ("PHRASE_MIN_WORDS", 2),
    ("PHRASE_MAX_WORDS", 5),
    ("PHRASE_MIN_CONTENT_WORDS", 2),
    ("PHRASE_MIN_COUNT", 3),
    ("PHRASE_MIN_CHARS", 9),
    ("PHRASE_LIMIT", 25),
])
def test_the_repair_thresholds_are_identical_in_both_trees(
    name: str, value: float,
) -> None:
    mine, ts_source = _both_sides(name)
    assert mine == value == float(_ts_declaration(ts_source, name).strip())


def test_the_variant_markers_are_identical_in_both_trees() -> None:
    """The function words that say a file came back in the wrong variant. A
    marker only one tree knows makes the same file clean there and colloquial
    here."""
    mine, ts_source = _both_sides("VARIANT_MARKERS")
    body = _strip_comments(_ts_declaration(ts_source, "VARIANT_MARKERS"))
    # The variant names hold the arrays; the script keys hold those objects.
    theirs = {key: tuple(re.findall(r"'([^']*)'", block))
              for key, block in _ts_record_blocks(body).items()}
    assert {variant: tuple(markers)
            for variants in mine.values()
            for variant, markers in variants.items()} == theirs
    for script in mine:
        assert re.search(rf"(?:'{script}'|\b{script})\s*:\s*\{{", body), (
            f"web/src/app/core VARIANT_MARKERS has no {script!r} script")


def test_the_drift_labels_are_identical_in_both_trees() -> None:
    """What a drifting glossary entry is called in the warning and in the retry
    prompt — the second is what the model is actually shown."""
    mine, ts_source = _both_sides("DRIFT_LABELS")
    assert _ts_string_record(ts_source, "DRIFT_LABELS") == mine


def test_every_variant_marker_script_is_a_script_we_classify() -> None:
    """Markers keyed by a script no target ever resolves to could never fire."""
    assert set(py_languages.VARIANT_MARKERS) <= set(py_languages.SCRIPT_NORMS)


def test_the_generic_variant_bucket_is_named_identically_in_both_trees() -> None:
    """The bucket a requested dialect mutes alongside the one it names. A name
    that drifts leaves one tree flagging every correctly-dialectal file."""
    mine, ts_source = _both_sides("GENERIC_VARIANT")
    assert _ts_value(_ts_declaration(ts_source, "GENERIC_VARIANT")) == mine


def test_the_generic_variant_names_a_bucket_that_exists() -> None:
    """A name that matches no key mutes nothing, silently."""
    assert any(py_languages.GENERIC_VARIANT in variants
               for variants in py_languages.VARIANT_MARKERS.values())


# The key the repair budget groups on. One that drifts makes the same
# systematic failure look like several batches in the other tree, and the cap
# it buys different. Checked as a literal because the two trees build the
# prefixed ones by interpolation, which they are free to spell differently.
FLAG_CAUSES = ("tags", "dashes", "shift", "adequacy", "term:", "name:",
               "leak:", "phrase:")


@pytest.mark.parametrize("cause", FLAG_CAUSES)
def test_the_flag_causes_are_named_identically_in_both_trees(cause: str) -> None:
    assert any(cause in source for source in WEB_SOURCES.values()), (
        f"no module in web/src/app/core names the flag cause {cause!r}"
    )


# The variant-drift warning, compared as the fragments either side of its
# interpolations: the two trees name the placeholders differently, but every
# word around them has to match.
VARIANT_DRIFT_FRAGMENTS = (
    "Output looks like ",
    " rather than the standard written form (",
    " lines). Pass --dialect to ask for it deliberately, or rerun.",
)


@pytest.mark.parametrize("fragment", VARIANT_DRIFT_FRAGMENTS)
def test_the_variant_drift_warning_is_worded_identically_in_both_trees(
    fragment: str,
) -> None:
    mine = py_repair.variant_drift_message(
        py_repair.VariantDrift("Egyptian", 121, 372))
    assert fragment in mine
    assert any(fragment in source for source in WEB_PROSE.values()), (
        f"no module in web/src/app/core emits {fragment!r}"
    )


# The phrase-consistency warning, compared the same way: the trees interpolate
# the phrase and the two counts differently, every word around them must match.
PHRASE_SPLIT_FRAGMENTS = (
    " is rendered ",
    " different ways across ",
    " lines; no wording is shared by all of them",
)


@pytest.mark.parametrize("fragment", PHRASE_SPLIT_FRAGMENTS)
def test_the_phrase_split_warning_is_worded_identically_in_both_trees(
    fragment: str,
) -> None:
    mine = py_context_pass.phrase_split_message(
        py_context_pass.PhraseSplit("the line", 7, 4, (202, 237, 239, 268)))
    assert fragment in mine
    assert any(fragment in source for source in WEB_PROSE.values()), (
        f"no module in web/src/app/core emits {fragment!r}"
    )


def test_the_default_script_name_is_identical_in_both_trees() -> None:
    assert _ts_declaration(_ts_source_declaring("DEFAULT_SCRIPT"),
                           "DEFAULT_SCRIPT").strip().strip("'") \
        == py_languages.DEFAULT_SCRIPT


# === Provider request dialect ================================================

# Both trees learn an endpoint's request shape from the same two error bodies
# and report it in the same words. A regex that drifts leaves one tree failing
# a file where the other recovers from it.


@pytest.mark.parametrize("py_name,ts_name", [
    ("_TOKEN_PARAM_RE", "TOKEN_PARAM_RE"),
    ("_TEMPERATURE_RE", "TEMPERATURE_RE"),
    ("_OUTPUT_LIMIT_RE", "OUTPUT_LIMIT_RE"),
    ("_REASONING_EFFORT_RE", "REASONING_EFFORT_RE"),
    ("_SUPPORTED_VALUES_RE", "SUPPORTED_VALUES_RE"),
])
def test_the_dialect_detection_regexes_are_identical_in_both_trees(
    py_name: str, ts_name: str,
) -> None:
    mine = getattr(py_config, py_name)
    assert mine.flags & re.I, f"{py_name} must be case-insensitive"
    theirs = _ts_declaration(_ts_source_declaring(ts_name), ts_name).strip()
    assert theirs == f"/{mine.pattern}/i", (
        f"{py_name} drifted: cli/core/config.py has {mine.pattern!r}, "
        f"web has {theirs!r}"
    )


@pytest.mark.parametrize("name", [
    "TOKEN_PARAM_CHANGE", "TEMPERATURE_CHANGE", "REASONING_CHANGE", "REASONING_DROP_CHANGE",
])
def test_the_dialect_change_names_are_identical_in_both_trees(name: str) -> None:
    theirs = _ts_declaration(_ts_source_declaring(name), name).strip().strip("'")
    assert getattr(py_config, name) == theirs


def test_the_reasoning_effort_preference_is_identical_and_in_the_same_order() -> None:
    """Ordered, not a set: 'none' is asked for first because it is cheapest."""
    mine, ts_source = _both_sides("REASONING_EFFORT_PREFERENCE")
    theirs = re.findall(r"'([^']+)'", _ts_declaration(ts_source, "REASONING_EFFORT_PREFERENCE"))
    assert theirs == list(mine)


def test_the_dialect_warning_is_worded_identically_in_both_trees() -> None:
    """Rendered with the TypeScript's own placeholder, so what is compared is
    the sentence and not how either tree interpolates it."""
    mine = py_config.dialect_warning("${change}")
    assert any(mine in source for source in WEB_PROSE.values()), (
        f"no module in web/src/app/core emits {mine!r}"
    )


# === Provider presets =========================================================
# The web's picker and the CLI's --provider / interactive start offer the same
# endpoints and the same models at the same prices, cheapest first.


def _web_presets() -> dict[str, dict]:
    source = (WEB_CORE / "providers.ts").read_text(encoding="utf-8")
    presets: dict[str, dict] = {}
    for m in re.finditer(r"\n  (\w+): preset\(\{(.*?)\n  \}\),", source, re.S):
        key, body = m.group(1), m.group(2)
        url = re.search(r"apiUrl: '([^']*)'", body)
        lanes = re.search(r"defaultConcurrency: (\d+)", body)
        assert url is not None and lanes is not None, key
        presets[key] = {
            "api_url": url.group(1),
            "models": re.findall(
                r"\{ id: '([^']+)', input: ([\d.]+), output: ([\d.]+), note: '([^']*)' \}",
                body),
            "needs_key": "needsKey: true" in body,
            "concurrency": int(lanes.group(1)),
        }
    return presets


def test_the_provider_presets_are_identical_in_both_trees() -> None:
    web = _web_presets()
    assert set(web) == set(py_providers.PROVIDER_PRESETS)
    for key, preset in py_providers.PROVIDER_PRESETS.items():
        theirs = web[key]
        assert preset.api_url == theirs["api_url"], key
        assert preset.needs_key is theirs["needs_key"], key
        assert preset.default_concurrency == theirs["concurrency"], key
        mine = [(m.id, m.input, m.output, m.note) for m in preset.models]
        assert mine == [(i, float(a), float(b), n) for i, a, b, n in theirs["models"]], key


# === Quality presets ==========================================================
# The web's three pills and the CLI's --quality / guided session bundle the
# same knobs to the same values, under the same words.

_WEB_KNOB = {"review": "review", "refineAttribution": "refine_attribution",
             "fixFlagged": "fix_flagged", "verifyAdequacy": "verify_adequacy",
             "fullAttribution": "full_attribution", "contextOverlap": "context_overlap"}


def _web_quality_presets() -> dict[str, dict]:
    source = (WEB_CORE.parent / "run-presets.ts").read_text(encoding="utf-8")
    consts = (WEB_CORE / "constants.ts").read_text(encoding="utf-8")

    def value(raw: str):
        raw = raw.strip()
        if raw in ("true", "false"):
            return raw == "true"
        if raw.isdigit():
            return int(raw)
        found = re.search(r"export const " + re.escape(raw) + r" = ([^;]+);", consts)
        assert found is not None, raw
        return value(found.group(1))

    presets: dict[str, dict] = {}
    preset_re = re.compile(
        r"\n  (\w+): \{\n    label: '([^']+)',\n    summary: '([^']+)',\n(.*?)\n  \},", re.S)
    for m in preset_re.finditer(source):
        key, label, summary, body = m.groups()
        knobs = {_WEB_KNOB[k]: value(v) for k, v in re.findall(r"(\w+): ([^,\n]+),", body)}
        presets[key] = {"label": label, "summary": summary, "knobs": knobs}
    return presets


def test_the_quality_presets_are_identical_in_both_trees() -> None:
    web = _web_quality_presets()
    assert list(web) == list(py_quality.QUALITY_PRESETS) == ["fast", "balanced", "best"]
    for key, preset in py_quality.QUALITY_PRESETS.items():
        assert preset.label == web[key]["label"], key
        assert preset.summary == web[key]["summary"], key
        mine = {name: getattr(preset.knobs, name) for name in py_quality.KNOB_NAMES}
        assert mine == web[key]["knobs"], key
