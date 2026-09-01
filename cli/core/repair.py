"""Deterministic repairs applied once a batch validates: dropped formatting
tags, speaker dashes, line count, line length, RTL punctuation, the
sentence-final mark, and the reports of cross-cue bleeding and foreign-script
leakage — plus the two file-level checks that need the whole file's baseline:
the vocalisation a model switches on partway through, and the regional variant
it wrote the file in.

Pure functions — no network, no config — so the batch runner and the tests
call them the same way. Models drop `{\\i1}` pairs and re-flow two source
lines into one often enough that retrying is the wrong tool.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from statistics import median

from .constants import VARIANT_DRIFT_FRACTION
from .languages import (
    ARABIC_PUNCTUATION_SCRIPTS,
    DEFAULT_MAX_CHARS_PER_LINE,
    DEFAULT_SCRIPT,
    GENERIC_VARIANT,
    VARIANT_MARKERS,
)
from .srt_parser import SubtitleBlock

# HTML-ish tags and ASS override blocks — the two forms the prompt copies through.
TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>|\{\\[^}]*\}")

# Formatting tags plus the ASS hard line break: markup, never text. Replaced
# by a space so stripping one never welds the words either side together.
NON_TEXT_RE = re.compile(r"</?[a-zA-Z][^>]*>|\{\\[^}]*\}|\\N")

# Written without spaces, so a line break lands between characters, not words.
NO_SPACE_SCRIPTS = frozenset({"han", "japanese", "hiragana", "katakana", "thai"})

# A line starting with one of these is a speaker turn; re-flowing merges speakers.
DIALOGUE_DASHES = ("-", "–")

# Never open a line with a closing mark: a break before one orphans it.
_NO_LINE_START = "、。，．！？；：）〕】》」』〉”｝〗〙〛…,.!?;:)]}»"
# A CJK line break reads most naturally right after one of these.
_BREAK_AFTER = "、。，．！？；：）〕】》」』"

# Coarse Unicode ranges, enough to tell one writing system from another.
# Only letters are classified, so digits, punctuation and combining marks are
# neutral and never count as a script of their own.
SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "latin": ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F),
              (0x1E00, 0x1EFF)),
    "greek": ((0x0370, 0x03FF), (0x1F00, 0x1FFF)),
    "cyrillic": ((0x0400, 0x052F),),
    "hebrew": ((0x0590, 0x05FF),),
    "arabic": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
               (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
    "devanagari": ((0x0900, 0x097F),),
    "thai": ((0x0E00, 0x0E7F),),
    "han": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
    "kana": ((0x3040, 0x30FF), (0x31F0, 0x31FF)),
    "hangul": ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)),
}

# The scripts a target may legitimately be written in. A target script with no
# entry (DEFAULT_SCRIPT) is never checked: we cannot say what belongs in it.
TARGET_SCRIPTS: dict[str, tuple[str, ...]] = {
    "latin": ("latin",),
    "cyrillic": ("cyrillic",),
    "greek": ("greek",),
    "arabic": ("arabic",),
    "hebrew": ("hebrew",),
    "devanagari": ("devanagari",),
    "thai": ("thai",),
    "han": ("han",),
    "japanese": ("kana", "han"),
    "korean": ("hangul", "han"),
}

# Long enough to recognise the leak, short enough to read in a warning.
LEAK_SAMPLE_CHARS = 12

RTL_PUNCTUATION = {"?": "؟", ",": "،", ";": "؛"}
_URL_RE = re.compile(r"(?:[a-zA-Z][\w+.-]*://|www\.)\S+")
_ARABIC_RE = re.compile("[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_LATIN_RE = re.compile(r"[0-9A-Za-z]")
# Punctuation glued to a word, so "line." and "line" count as the same word.
_WORD_STRIP_RE = re.compile(r"[\W_]", re.UNICODE)

# An opening tag immediately followed by its closing form: renders nothing.
EMPTY_PAIR_RE = re.compile(
    r"\{\\(?P<ass>\w+?)1\}\{\\(?P=ass)0\}|<(?P<html>\w+)[^>]*>\s*</(?P=html)>")

# Arabic vocalisation: fatha through sukun, plus the superscript alef.
ARABIC_DIACRITICS_RE = re.compile("[\u064B-\u0652\u0670]")
# A file whose median cue carries more than this is vocalized on purpose.
DIACRITIC_BASELINE_MAX = 1
# Marks in one cue before it counts as a register switch rather than a stray.
DIACRITIC_CUE_MIN = 5

TERMINAL_MARKS = (".", "!", "?")

# Cues shorter than this make any shared-run signal meaningless.
SHIFT_MIN_SOURCE_CHARS = 15
# A run is scored in characters, not words: both real desyncs in the benchmark
# episode share exactly one distinctive word across the boundary, and one of
# them shares a short word as well - only the longer word is evidence.
SHIFT_MIN_RUN_WORDS = 1
SHIFT_MIN_RUN_CHARS = 4
# Share of the shorter output cue the run has to account for, and how far it
# has to outrun the same measurement on the source pair.
SHIFT_MIN_COVERAGE = 0.45
SHIFT_SOURCE_RATIO = 2.0


@dataclass(frozen=True)
class CueShift:
    """One adjacent output pair that looks like a clause slid across their
    boundary, and the run of words that says so."""
    first: int
    second: int
    run: str


def find_tags(text: str) -> list[str]:
    """Formatting tags in reading order."""
    return TAG_RE.findall(text)


def visible_length(text: str) -> int:
    """On-screen width: formatting tags occupy no columns."""
    return len(TAG_RE.sub("", text))


def drop_empty_tag_pairs(text: str) -> str:
    """Remove tag pairs with nothing between them, e.g. an empty italic run."""
    return EMPTY_PAIR_RE.sub("", text)


def repair_tags(source_text: str, output_text: str) -> tuple[str, bool]:
    """Restore tags the model dropped. Returns (text, ok); ok=False means the
    mismatch is not one we can safely undo and the caller should warn."""
    output_text = drop_empty_tag_pairs(output_text)
    source_tags = find_tags(source_text)
    if source_tags == find_tags(output_text):
        return output_text, True
    if TAG_RE.search(output_text) or not source_tags:
        return output_text, False

    # Stripped, so surrounding whitespace still counts as a wrapping pair.
    source = source_text.strip()
    matches = list(TAG_RE.finditer(source))
    if (len(matches) == 2 and matches[0].start() == 0
            and matches[1].end() == len(source)):
        return source_tags[0] + output_text.strip() + source_tags[1], True
    if len(matches) == 1 and matches[0].start() == 0:
        return source_tags[0] + output_text, True
    return output_text, False


def _word_units(text: str) -> list[str]:
    """Whitespace-separated tokens, each formatting tag glued to its word."""
    units: list[str] = []
    current = ""
    pos = 0
    while pos < len(text):
        tag = TAG_RE.match(text, pos)
        if tag:
            current += tag.group(0)
            pos = tag.end()
        elif text[pos].isspace():
            if current:
                units.append(current)
                current = ""
            pos += 1
        else:
            current += text[pos]
            pos += 1
    if current:
        units.append(current)
    return units


def _char_units(text: str) -> list[str]:
    """One unit per character, with tags and trailing spaces glued on so that
    joining with "" is lossless and no break can land inside a tag."""
    units: list[str] = []
    pending = ""
    pos = 0
    while pos < len(text):
        tag = TAG_RE.match(text, pos)
        if tag:
            pending += tag.group(0)
            pos = tag.end()
            continue
        char = text[pos]
        pos += 1
        if char.isspace():
            if units:
                units[-1] += pending + char
                pending = ""
            continue
        units.append(pending + char)
        pending = ""
    if pending:
        if units:
            units[-1] += pending
        else:
            units.append(pending)
    return units


def _balanced_lines(
    units: list[str], target_lines: int, joiner: str, max_chars: int, no_space: bool,
) -> list[str] | None:
    """Split `units` into exactly `target_lines` lines, minimising the longest."""
    total = len(units)
    if total < target_lines:
        return None

    prefix = [0]
    for unit in units:
        prefix.append(prefix[-1] + visible_length(unit))
    gap = len(joiner)

    def width(start: int, end: int) -> int:
        return prefix[end] - prefix[start] + gap * (end - start - 1)

    def may_break_before(index: int) -> bool:
        first = TAG_RE.sub("", units[index]).lstrip()[:1]
        return not (no_space and first and first in _NO_LINE_START)

    def penalty_before(index: int) -> int:
        last = TAG_RE.sub("", units[index - 1]).rstrip()[-1:]
        return 0 if not no_space or (last and last in _BREAK_AFTER) else 1

    def key(state: tuple[int, int]) -> tuple[int, int, int]:
        # Lines that fit are interchangeable, which lets the CJK punctuation
        # preference decide before residual balance does.
        return (max(state[0], max_chars), state[1], state[0])

    unreachable = (10**9, 10**9)
    # best[k][i]: (longest line, break penalty) for units[i:] split into k lines.
    best = [[unreachable] * (total + 1) for _ in range(target_lines + 1)]
    at = [[-1] * (total + 1) for _ in range(target_lines + 1)]
    for start in range(total):
        best[1][start] = (width(start, total), 0)
    for lines_left in range(2, target_lines + 1):
        for start in range(total - lines_left + 1):
            for cut in range(start + 1, total - lines_left + 2):
                tail = best[lines_left - 1][cut]
                if tail == unreachable or not may_break_before(cut):
                    continue
                candidate = (
                    max(width(start, cut), tail[0]),
                    tail[1] + penalty_before(cut),
                )
                if key(candidate) < key(best[lines_left][start]):
                    best[lines_left][start] = candidate
                    at[lines_left][start] = cut
    if best[target_lines][0] == unreachable:
        return None

    lines: list[str] = []
    start = 0
    for lines_left in range(target_lines, 1, -1):
        cut = at[lines_left][start]
        lines.append(joiner.join(units[start:cut]).strip())
        start = cut
    lines.append(joiner.join(units[start:total]).strip())
    return lines


def reflow_to_line_count(
    text: str,
    target_lines: int,
    max_chars: int = DEFAULT_MAX_CHARS_PER_LINE,
    script: str = DEFAULT_SCRIPT,
) -> str:
    """Re-wrap `text` to exactly `target_lines` lines, or return it unchanged
    when that cannot be done safely."""
    lines = text.split("\n")
    if target_lines < 1 or len(lines) == target_lines or not text.strip():
        return text
    if any(TAG_RE.sub("", line).lstrip()[:1] in DIALOGUE_DASHES
           for line in lines):
        return text

    no_space = script in NO_SPACE_SCRIPTS
    joiner = "" if no_space else " "
    flat = joiner.join(line.strip() for line in lines if line.strip())
    units = _char_units(flat) if no_space else _word_units(flat)
    result = _balanced_lines(units, target_lines, joiner, max_chars, no_space)
    if result is None or any(not line for line in result):
        return text
    return "\n".join(result)


def enforce_line_length(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS_PER_LINE,
    script: str = DEFAULT_SCRIPT,
) -> str:
    """One over-long line may become two — the professional maximum. A cue that
    already has two or more lines is left alone."""
    lines = text.split("\n")
    if len(lines) >= 2 or visible_length(lines[0]) <= max_chars:
        return text
    return reflow_to_line_count(text, 2, max_chars, script)


def _opens_dialogue(line: str) -> bool:
    """True when the line's first visible character is a dialogue dash."""
    return TAG_RE.sub("", line).lstrip()[:1] in DIALOGUE_DASHES


def dialogue_dash_lines(text: str) -> int:
    """How many speaker turns the cue encodes: lines opening on a dash."""
    return sum(1 for line in text.split("\n") if _opens_dialogue(line))


def restore_dialogue_dashes(
    source_text: str, output_text: str,
) -> tuple[str, bool]:
    """Put back the dialogue dashes a model dropped. Returns (text, ok);
    ok=False means the turns cannot be recovered safely and the caller should
    warn — a cue that lost both dashes reads as one speaker saying everything.

    Only the line-for-line case is repaired: with the line count intact each
    output line still stands for its own source line, so the dash goes back
    where it came from. Any other shape is guesswork.
    """
    source_count = dialogue_dash_lines(source_text)
    output_count = dialogue_dash_lines(output_text)
    if source_count == output_count:
        return output_text, True
    source_lines = source_text.split("\n")
    output_lines = output_text.split("\n")
    if source_count < output_count or len(source_lines) != len(output_lines):
        return output_text, False

    restored = [
        f"- {line}"
        if _opens_dialogue(src) and not _opens_dialogue(line)
        else line
        for src, line in zip(source_lines, output_lines, strict=True)
    ]
    return "\n".join(restored), True


def _neighbour(
    text: str, index: int, step: int, tag_spans: list[tuple[int, int]],
) -> str:
    """Nearest visible character in one direction; tags and spaces are skipped."""
    i = index + step
    while 0 <= i < len(text):
        span = next((s for s in tag_spans if s[0] <= i < s[1]), None)
        if span is not None:
            i = span[0] - 1 if step < 0 else span[1]
        elif text[i].isspace():
            i += step
        else:
            return text[i]
    return ""


def normalize_rtl_punctuation(text: str, script: str) -> str:
    """Re-point ASCII punctuation that Arabic-script text renders wrong. Left
    alone inside a tag or a URL, and inside a run of Latin text or digits."""
    if script not in ARABIC_PUNCTUATION_SCRIPTS or not text:
        return text
    tag_spans = [m.span() for m in TAG_RE.finditer(text)]
    frozen = tag_spans + [m.span() for m in _URL_RE.finditer(text)]
    chars = list(text)
    for index, char in enumerate(text):
        replacement = RTL_PUNCTUATION.get(char)
        if replacement is None:
            continue
        if any(start <= index < end for start, end in frozen):
            continue
        before = _neighbour(text, index, -1, tag_spans)
        after = _neighbour(text, index, 1, tag_spans)
        if not (_ARABIC_RE.match(before) or _ARABIC_RE.match(after)):
            continue
        if _LATIN_RE.match(before) and _LATIN_RE.match(after):
            continue
        chars[index] = replacement
    return "".join(chars)


def strip_tags(text: str) -> str:
    """The text with formatting tags removed."""
    return NON_TEXT_RE.sub(" ", text)


def script_of(char: str) -> str:
    """The script one character is written in, or "" when it is not a letter
    or belongs to no range we classify."""
    if not char.isalpha():
        return ""
    code = ord(char)
    for name, ranges in SCRIPT_RANGES.items():
        if any(low <= code <= high for low, high in ranges):
            return name
    return ""


@dataclass
class ScriptRun:
    """One run of letters from a single script, and whether the character
    before it was a letter of a different one."""
    script: str
    text: str
    glued: bool


def _script_runs(text: str) -> list[ScriptRun]:
    """Maximal runs of one script, tags read as separators and combining marks
    kept with the letter they sit on."""
    runs: list[ScriptRun] = []
    current: ScriptRun | None = None
    separated = True
    for char in strip_tags(text):
        if current is not None and unicodedata.category(char).startswith("M"):
            current.text += char
            continue
        name = script_of(char)
        if not name:
            current = None
            separated = True
            continue
        if current is not None and current.script == name:
            current.text += char
            continue
        current = ScriptRun(name, char, not separated)
        separated = False
        runs.append(current)
    return runs


@dataclass(frozen=True)
class ScriptLeak:
    """One writing system left in a translation, and what is wrong with it.
    `script` is the alien one, so two cues leaking the same script are one
    problem however differently their messages read."""
    script: str
    message: str


def _glued_runs(runs: list[ScriptRun], own: tuple[str, ...]) -> list[ScriptLeak]:
    """Tokens where a target-script letter touches a letter of another script
    with no space, punctuation or tag between them."""
    problems: list[ScriptLeak] = []
    start = 0
    for i in range(1, len(runs) + 1):
        if i < len(runs) and runs[i].glued:
            continue
        chain = runs[start:i]
        start = i
        if len(chain) < 2:
            continue
        alien = next((r for r in chain if r.script not in own), None)
        mine = next((r for r in chain if r.script in own), None)
        if alien is None or mine is None:
            continue
        welded = "".join(r.text for r in chain)
        problems.append(ScriptLeak(
            alien.script,
            f"'{welded}' welds {mine.script} to {alien.script} "
            f"with no separator"))
    return problems


def find_script_leaks(
    source_text: str, output_text: str, script: str,
) -> list[ScriptLeak]:
    """Writing systems the model left in a translation that has no business
    carrying them: a script neither the target nor the source cue uses, and a
    target-script letter welded to a letter of another script, which breaks
    bidirectional rendering. A report only — never a reason to fail a file."""
    own = TARGET_SCRIPTS.get(script)
    if not own:
        return []
    runs = _script_runs(output_text)
    in_source = {run.script for run in _script_runs(source_text)}
    problems: list[ScriptLeak] = []
    reported: set[str] = set()
    for run in runs:
        if run.script in own or run.script in in_source or run.script in reported:
            continue
        reported.add(run.script)
        problems.append(ScriptLeak(
            run.script,
            f"{run.script} characters appear in the translation "
            f"('{run.text[:LEAK_SAMPLE_CHARS]}')"))
    problems.extend(_glued_runs(runs, own))
    return problems


def script_leaks(source_text: str, output_text: str, script: str) -> list[str]:
    return [leak.message
            for leak in find_script_leaks(source_text, output_text, script)]


def content_words(text: str) -> list[str]:
    """Comparable words: tags gone, punctuation gone, case folded. Scripts
    written without spaces yield one unit per cue, so the run test below simply
    never fires for them."""
    words = (_WORD_STRIP_RE.sub("", token).lower()
             for token in strip_tags(text).split())
    return [word for word in words if word]


def _longest_shared_run(first: list[str], second: list[str]) -> list[str]:
    """The run of words appearing in the same order in both cues that carries
    the most characters. Scored in characters, not words: one long shared word
    is evidence, two shared short ones are coincidence."""
    best: list[str] = []
    best_chars = 0
    previous_words = [0] * (len(second) + 1)
    previous_chars = [0] * (len(second) + 1)
    for i in range(1, len(first) + 1):
        current_words = [0] * (len(second) + 1)
        current_chars = [0] * (len(second) + 1)
        for j in range(1, len(second) + 1):
            if first[i - 1] != second[j - 1]:
                continue
            current_words[j] = previous_words[j - 1] + 1
            current_chars[j] = previous_chars[j - 1] + len(first[i - 1])
            if current_chars[j] > best_chars:
                best_chars = current_chars[j]
                best = first[i - current_words[j]:i]
        previous_words, previous_chars = current_words, current_chars
    return best


def _run_chars(run: list[str]) -> int:
    return len("".join(run))


def _cue_chars(text: str) -> int:
    """The cue's visible text, measured the same way a run is."""
    return _run_chars(content_words(text))


def _is_shared_run(run: list[str]) -> bool:
    return (len(run) >= SHIFT_MIN_RUN_WORDS
            and _run_chars(run) >= SHIFT_MIN_RUN_CHARS)


def find_cross_cue_shifts(
    source: list[SubtitleBlock], output: list[SubtitleBlock],
) -> list[CueShift]:
    """Adjacent output cues that share a run of words their sources do not —
    the shape a clause takes when it slides from one cue into its neighbour.

    A report only; short cues make false positives routine. Length ratios
    cannot see this: the two real desyncs in the benchmark file measured
    0.97/0.79 and 1.11/0.56, well inside normal.
    """
    if len(source) != len(output):
        raise ValueError("source and output must have the same length")
    shifts: list[CueShift] = []
    for i in range(len(source) - 1):
        first_src, second_src = source[i], source[i + 1]
        if (len(first_src.text) < SHIFT_MIN_SOURCE_CHARS
                or len(second_src.text) < SHIFT_MIN_SOURCE_CHARS):
            continue
        run = _longest_shared_run(
            content_words(output[i].text), content_words(output[i + 1].text))
        if not _is_shared_run(run):
            continue
        run_chars = _run_chars(run)
        # Both sides of this ratio drop tags, punctuation and spacing, so a run
        # and the cue it sits in are counted in the same characters.
        shorter = min(_cue_chars(output[i].text), _cue_chars(output[i + 1].text))
        if shorter <= 0 or run_chars < SHIFT_MIN_COVERAGE * shorter:
            continue
        # A run the two SOURCE cues share too is repetition, not a shift.
        source_run = _longest_shared_run(
            content_words(first_src.text), content_words(second_src.text))
        if run_chars < SHIFT_SOURCE_RATIO * _run_chars(source_run):
            continue
        shifts.append(CueShift(first_src.number, second_src.number,
                               " ".join(run)))
    return shifts


def shift_message(shift: CueShift) -> str:
    return (f"Blocks {shift.first}-{shift.second}: '{shift.run}' appears in "
            f"both cues - text may have shifted between them")


def detect_cross_cue_shift(
    source: list[SubtitleBlock], output: list[SubtitleBlock],
) -> list[str]:
    return [shift_message(s) for s in find_cross_cue_shifts(source, output)]


def _last_visible_index(text: str, start: int | None = None) -> int:
    """Index of the last character at or before `start` that is neither a tag
    nor whitespace."""
    spans = [m.span() for m in TAG_RE.finditer(text)]
    i = len(text) - 1 if start is None else min(start, len(text) - 1)
    while i >= 0:
        span = next((s for s in spans if s[0] <= i < s[1]), None)
        if span is not None:
            i = span[0] - 1
        elif text[i].isspace():
            i -= 1
        else:
            return i
    return -1


def _target_mark(mark: str, script: str) -> str:
    if script in ARABIC_PUNCTUATION_SCRIPTS:
        return RTL_PUNCTUATION.get(mark, mark)
    return mark


def _terminal_class(char: str, script: str) -> str:
    """The ASCII terminal mark `char` stands for, or "" if it is not one."""
    for mark in TERMINAL_MARKS:
        if char in (mark, _target_mark(mark, script)):
            return mark
    return ""


def restore_terminal_punctuation(
    source_text: str, output_text: str, script: str,
) -> str:
    """Put back the sentence-final mark the source ended on when the model
    swapped it for another one. A cue that ends in no mark at all is left
    alone — that is a translator's choice, not a substitution.

    Compared by class, not by glyph: an output already ending on the source's
    own mark is left exactly as it is, even when it spells that mark the ASCII
    way. `normalize_rtl_punctuation` runs first and re-points every ASCII mark
    that should be re-pointed; one it deliberately left alone — inside a URL,
    or closing a run of Latin — is not this pass's to overrule.
    """
    source_index = _last_visible_index(source_text)
    if source_index < 0:
        return output_text
    source_mark = source_text[source_index]
    if source_mark not in TERMINAL_MARKS:
        return output_text
    output_index = _last_visible_index(output_text)
    if output_index < 0:
        return output_text
    output_class = _terminal_class(output_text[output_index], script)
    # No mark at all, or already the source's own class: nothing to restore.
    if output_class in ("", source_mark):
        return output_text
    # An ellipsis or a "?!" is a deliberate device, not a swapped-out mark.
    prior_index = _last_visible_index(output_text, output_index - 1)
    if prior_index >= 0 and _terminal_class(output_text[prior_index], script):
        return output_text
    return (output_text[:output_index] + _target_mark(source_mark, script)
            + output_text[output_index + 1:])


def diacritic_count(text: str) -> int:
    return len(ARABIC_DIACRITICS_RE.findall(text))


def normalize_diacritics(
    blocks: list[SubtitleBlock], script: str,
) -> list[SubtitleBlock]:
    """Strip a cue's vocalisation where the file as a whole carries none — the
    model switching orthographic register partway through. File-level on
    purpose: only the whole file says what its baseline is."""
    if script not in ARABIC_PUNCTUATION_SCRIPTS or not blocks:
        return blocks
    counts = [diacritic_count(b.text) for b in blocks]
    if median(counts) > DIACRITIC_BASELINE_MAX:
        return blocks
    return [
        SubtitleBlock(b.number, b.timestamp, ARABIC_DIACRITICS_RE.sub("", b.text))
        if count >= DIACRITIC_CUE_MIN else b
        for b, count in zip(blocks, counts, strict=True)
    ]


@dataclass(frozen=True)
class VariantDrift:
    """A regional variant the finished file reads as, and how much of it does."""
    variant: str
    cues: int
    total: int


# Word characters only, so "مش" is never found inside "مشكلة" — a substring
# test misreads a tenth of a clean Modern Standard Arabic file as colloquial.
_MARKER_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _marker_tokens(text: str) -> set[str]:
    """The cue's words, tags gone and vocalisation stripped: a combining mark
    would otherwise split "مِش" into two tokens that match nothing."""
    return set(_MARKER_TOKEN_RE.findall(
        ARABIC_DIACRITICS_RE.sub("", strip_tags(text))))


def detect_variant_drift(
    blocks: list[SubtitleBlock], script: str, dialect: str = "",
) -> VariantDrift | None:
    """The regional variant a finished file was written in when the user never
    asked for one. File-level: a single colloquial cue is a register choice,
    a sixth of the file is the wrong target.

    Scored as one union across the live buckets, because a file can drift out
    of the standard written form without committing to any single dialect —
    scored bucket by bucket, such a file clears every threshold and reads as
    clean. Labelled with whichever bucket carries most of that union. A report
    only, or None."""
    variants = VARIANT_MARKERS.get(script)
    if not variants or not blocks:
        return None
    asked = dialect.casefold()
    # Asking for a regional variant accepts colloquial writing by definition,
    # so the pan-dialectal bucket is muted alongside the one that was named.
    muted = ({v for v in variants if v.casefold() in asked} | {GENERIC_VARIANT}
             if asked else set())
    live = {variant: frozenset(markers)
            for variant, markers in variants.items() if variant not in muted}
    if not live:
        return None
    total = len(blocks)
    union = 0
    per_variant = dict.fromkeys(live, 0)
    for block in blocks:
        tokens = _marker_tokens(block.text)
        hit = False
        for variant, markers in live.items():
            if markers & tokens:
                per_variant[variant] += 1
                hit = True
        union += hit
    if union <= VARIANT_DRIFT_FRACTION * total:
        return None
    # max() keeps the first maximum, so a tie goes to the earlier bucket.
    return VariantDrift(max(live, key=lambda v: per_variant[v]), union, total)


def variant_drift_message(drift: VariantDrift) -> str:
    return (f"Output looks like {drift.variant} rather than the standard "
            f"written form ({drift.cues} of {drift.total} cues). Pass "
            f"--dialect to ask for it deliberately, or rerun.")
