"""The glossary the prepass produces: the cast, terms, scenes and register it
holds, and the per-batch slice it renders into a translation prompt."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from .constants import ATTRIB_MIN_BLOCKS, MIN_NAME_LEN
from .repair import content_words, strip_tags
from .srt_parser import SubtitleBlock

# How many glossary terms the scan may return; the file repeats far more
# phrases than the old cap of 10 could hold.
MAX_TERMS = 25
# Idioms are rarer than terms and cost a line of prompt each.
MAX_IDIOMS = 15
# A subtitle equivalent is about as short as the phrase it replaces; a target
# that is both several times longer and long outright is a dictionary
# definition, and pasting one over a punchline makes the cue unreadable.
IDIOM_MAX_EXPANSION = 2.5
IDIOM_MAX_TARGET_CHARS = 40

# === Recurring-phrase seeding ===
# Word n-grams the file repeats, mined before the scan so the glossary is
# seeded from the file itself rather than from whatever the model noticed.
PHRASE_MIN_WORDS = 2
PHRASE_MAX_WORDS = 5
# At least this many words that are not function words: "the match" is one
# word wearing an article, and pinning a word is the glossary's job.
PHRASE_MIN_CONTENT_WORDS = 2
PHRASE_MIN_COUNT = 3
# Shorter than this and a phrase is a fragment, not a rendering decision.
PHRASE_MIN_CHARS = 9
PHRASE_LIMIT = 25

# === File-level phrase consistency ===
# The consistency check mines its own phrases. PHRASE_MIN_CHARS guards the
# scan's term budget, where a fragment costs a slot; this check costs nothing
# and cannot afford to miss an eight-character motif like "the line". Seven
# admits fragments faster than it finds motifs.
CONSISTENCY_MIN_CHARS = 8
# Cues a phrase must recur in before a split reads as a rendering decision
# rather than three paraphrases of three different sentences.
CONSISTENCY_MIN_OCCURRENCES = 4

# A phrase made only of function words pins nothing worth pinning.
# Function words, conversational filler and the commonest verbs: a run made
# only of these is never a term worth pinning, and a phrase like "thank you"
# or "yeah yeah" is expected to be rendered many ways.
PHRASE_STOPWORDS = frozenset({
    "a", "about", "actually", "again", "ah", "ain't", "all", "also", "always",
    "am", "an", "and", "any", "anyone", "anything", "anyway", "are", "aren't",
    "as", "ask", "asked", "at", "away", "back", "bad", "be", "been", "bit",
    "but", "by", "bye", "call", "called", "came", "can", "can't", "cannot",
    "come", "comes", "coming", "could", "couldn't", "did", "didn't", "do",
    "does", "doesn't", "don't", "down", "else", "even", "ever", "everyone",
    "everything", "exactly", "feel", "feels", "felt", "find", "fine", "for",
    "found", "from", "gave", "get", "give", "given", "gives", "go", "goes",
    "going", "gone", "gonna", "good", "got", "gotta", "great", "guess", "guy",
    "guys", "had", "hadn't", "happen", "happened", "has", "hasn't", "have",
    "haven't", "he", "he's", "hello", "help", "her", "here", "here's", "hey",
    "hi", "him", "his", "how", "i", "i'd", "i'll", "i'm", "i've", "if", "in",
    "into", "is", "isn't", "it", "it's", "its", "just", "keep", "kept", "kind",
    "knew", "know", "known", "knows", "leave", "left", "let", "let's", "like",
    "liked", "likes", "little", "look", "looked", "looks", "lot", "lots",
    "love", "made", "make", "makes", "man", "many", "may", "maybe", "me",
    "mean", "means", "meant", "might", "more", "most", "much", "must", "my",
    "need", "needed", "needs", "never", "nice", "no", "nobody", "nope", "not",
    "nothing", "now", "of", "off", "oh", "ok", "okay", "on", "one", "ones",
    "only", "onto", "or", "ought", "our", "out", "over", "people", "please",
    "put", "really", "right", "said", "saw", "say", "says", "see", "seen",
    "sees", "shall", "she", "she's", "should", "shouldn't", "so", "someone",
    "something", "sorry", "sort", "still", "stop", "stuff", "sure", "take",
    "taken", "takes", "talk", "talking", "tell", "tells", "than", "thank",
    "thanks", "that", "that's", "the", "their", "them", "then", "there",
    "there's", "these", "they", "they'd", "they'll", "they're", "they've",
    "thing", "things", "think", "thinks", "this", "thought", "time", "to",
    "told", "too", "took", "tried", "try", "trying", "up", "us", "very",
    "wait", "wanna", "want", "wanted", "wants", "was", "wasn't", "way", "we",
    "we'd", "we'll", "we're", "we've", "well", "went", "were", "weren't",
    "what", "what's", "when", "where", "which", "who", "who's", "will", "with",
    "won't", "work", "works", "would", "wouldn't", "y'all", "yeah", "yep",
    "yes", "you", "you'd", "you'll", "you're", "you've", "your",
})

# Letters and digits, with internal apostrophes kept so "that's" stays one word.
_PHRASE_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


@dataclass
class CharacterHint:
    source: str
    target: str
    gender: str  # "male" | "female" | "unknown"


@dataclass
class TermHint:
    source: str
    target: str


@dataclass(frozen=True)
class TermDrift:
    """A glossary entry the batch used whose pinned target is missing.
    `kind` is the word the report calls it — and, with the source, the stable
    key that says two batches drifted on the same entry."""
    block: int
    source: str
    target: str
    kind: str = "term"  # "term" | "name"

    @property
    def cause(self) -> str:
        return f"{self.kind}:{self.source}"


DRIFT_LABELS = {"term": "glossary term", "name": "character name"}


def drift_phrase(drift: TermDrift) -> str:
    """How a drift reads in a warning: "glossary term 'x' was not rendered as
    'y'". The retry prompt shows the same sentence, uncapitalised."""
    return (f"{DRIFT_LABELS[drift.kind]} '{drift.source}' "
            f"was not rendered as '{drift.target}'")


def glossary_key(source: str) -> str:
    """Case-folded and whitespace-collapsed, so one phrase cannot enter the
    glossary twice under two spellings of the same key."""
    return " ".join(source.split()).casefold()


def is_definition(hint: TermHint) -> bool:
    """An idiom target that explains the idiom instead of translating it. Both
    limits have to be passed: a long equivalent for a long source is fine."""
    source_len = len(hint.source.strip())
    target_len = len(hint.target.strip())
    return (target_len > IDIOM_MAX_EXPANSION * source_len
            and target_len > IDIOM_MAX_TARGET_CHARS)


def usable_idioms(
    terms: list[TermHint], idioms: list[TermHint],
) -> list[TermHint]:
    """The idioms a glossary may keep. An idiom whose source is already a term
    is dropped — terms win, because they carry the substitutable target form and
    an idiom that shadows one pastes its value over every use of the phrase."""
    pinned = {glossary_key(t.source) for t in terms}
    return [h for h in idioms
            if glossary_key(h.source) not in pinned and not is_definition(h)]


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
    idioms: list[TermHint] = field(default_factory=list)
    scenes: list[SceneHint] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


    def is_empty(self) -> bool:
        return not (self.register or self.characters or self.terms
                    or self.idioms or self.scenes or self.notes)

    def _batch_entries(
        self, batch: list[SubtitleBlock],
    ) -> tuple[list[CharacterHint], list[TermHint], list[TermHint],
               list[SceneHint]]:
        text = "\n".join(b.text for b in batch)
        scenes = _scenes_overlapping(self.scenes, batch)
        # Include characters named in the batch AND scene participants — the
        # latter covers speakers who address each other as "you" without
        # vocatives, so the translator still learns their gender.
        scene_names = {p for s in scenes for p in s.participants}
        chars = [h for h in self.characters
                 if _find_word(text, h.source) >= 0 or h.source in scene_names]
        terms = [h for h in self.terms if _find_word(text, h.source) >= 0]
        idioms = [h for h in self.idioms if _find_word(text, h.source) >= 0]
        return chars, terms, idioms, scenes

    def drift_entries(
        self, batch: list[SubtitleBlock], output: list[SubtitleBlock],
    ) -> list[TermDrift]:
        """Terms and character names the batch's source used whose pinned
        target never made it into the output. A report only: a term can
        legitimately be inflected away, so this never fails or retries a batch.

        Characters are matched on the source text alone, never on a scene's
        participant list: a name the cues never say has no target form to miss.
        """
        if not batch:
            return []
        text = "\n".join(b.text for b in batch)
        terms = [(h, at) for h in self.terms
                 if (at := _find_word(text, h.source)) >= 0]
        named = [(h, at) for h in self.characters
                 if (at := _find_word(text, h.source)) >= 0]
        if not terms and not named:
            return []
        rendered = "\n".join(b.text for b in output).lower()
        return [
            TermDrift(_block_holding(batch, at), h.source, h.target, kind)
            for kind, hints in (("term", terms), ("name", named))
            for h, at in hints
            if h.target.strip() and h.target.lower() not in rendered
        ]

    def has_correctable_entries(self, batch: list[SubtitleBlock]) -> bool:
        """True when the slice holds a character, term or idiom the reviewer
        could act on. Idioms count because the reviewer's idiom rule would
        otherwise only ever fire on a batch that also names someone."""
        chars, terms, idioms, _ = self._batch_entries(batch)
        return bool(chars or terms or idioms)

    def render_for_batch(self, batch: list[SubtitleBlock]) -> str:
        """Glossary slice scoped to this batch. Register/notes are file-wide."""
        chars, terms, idioms, scenes = self._batch_entries(batch)
        if not (self.register or chars or terms or idioms or scenes
                or self.notes):
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
        if idioms:
            parts.append("Idioms - render by meaning, never word for word:\n"
                         + "\n".join(f"- {h.source} => {h.target}"
                                     for h in idioms))
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


def gender_mark(g: str | None) -> str:
    return "M" if g == "male" else "F" if g == "female" else ""


def _render_scenes(scenes: list[SceneHint], gender_by: dict[str, str]) -> str:
    lines: list[str] = []
    for s in scenes:
        tagged = ", ".join(
            f"{n} ({mark})" if (mark := gender_mark(gender_by.get(n.casefold()))) else n
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


# Space-separating scripts only: in CJK, Thai or Arabic an adjacent letter is
# normal, so demanding a non-letter neighbour there makes every name unmatchable.
_WORD_CHAR_RE = re.compile(
    r"[0-9_A-Za-z\u00C0-\u024F\u0300-\u036F\u0370-\u03FF"
    r"\u0400-\u052F\u1E00-\u1EFF]"
)


def _is_word_char(ch: str) -> bool:
    return bool(ch) and _WORD_CHAR_RE.match(ch) is not None


@lru_cache(maxsize=512)
def _phrase_pattern(needle: str) -> re.Pattern[str] | None:
    """`needle` as a regex whose internal spaces match any run of whitespace,
    so a phrase broken over a line break still matches."""
    parts = needle.split()
    if not parts:
        return None
    return re.compile(r"\s+".join(re.escape(part) for part in parts))


def _block_holding(batch: list[SubtitleBlock], offset: int) -> int:
    """The number of the cue whose text `offset` into the newline-joined batch
    falls in, so a drift is pinned to the cue that says the term rather than
    to whichever cue happens to open the batch."""
    if offset < 0:
        return batch[0].number
    end = 0
    for block in batch:
        end += len(block.text) + 1  # the joining newline
        if offset < end:
            return block.number
    return batch[-1].number


def _find_word(text: str, word: str) -> int:
    """Case-insensitive whole-word search with Unicode-aware boundaries.
    Works for Latin, Arabic, CJK, etc. A multi-word phrase matches across any
    run of whitespace, so "safety briefing" is still found when the batch
    broke it over two subtitle lines. Returns first match index or -1."""
    if not text or not word:
        return -1
    # Lowercasing can change length (e.g. "İ"), so index haystack, not text.
    haystack = text.lower()
    pattern = _phrase_pattern(word.lower())
    if pattern is None:
        return -1
    at = 0
    while (match := pattern.search(haystack, at)) is not None:
        start, end = match.start(), match.end()
        before = haystack[start - 1] if start > 0 else ""
        after = haystack[end] if end < len(haystack) else ""
        if not _is_word_char(before) and not _is_word_char(after):
            return start
        at = start + 1
    return -1


def detect_participants(
    text: str, characters: list[CharacterHint],
) -> list[str]:
    """Source names whose source OR target form appears in `text` as a whole
    word, in order of first appearance. Matches both forms because scan
    descriptions often slip into the target language."""
    aliases: list[tuple[str, str]] = []  # (alias, source_name)
    for h in characters:
        if len(h.source) >= MIN_NAME_LEN:
            aliases.append((h.source, h.source))
        if h.target != h.source and len(h.target) >= MIN_NAME_LEN:
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


# The phrase tokenizer: what a phrase is mined from, and what it is matched
# against later, so a phrase can never fail to find the cue it came from.
def _phrase_words(text: str) -> list[str]:
    return _PHRASE_WORD_RE.findall(strip_tags(text).lower().replace("’", "'"))


def _mine_phrases(
    blocks: list[SubtitleBlock], min_chars: int,
) -> dict[str, list[int]]:
    """Every candidate phrase, with the index of the block of each occurrence."""
    at: dict[str, list[int]] = {}
    for index, block in enumerate(blocks):
        words = _phrase_words(block.text)
        for size in range(PHRASE_MIN_WORDS, PHRASE_MAX_WORDS + 1):
            for start in range(len(words) - size + 1):
                gram = words[start:start + size]
                content = sum(1 for word in gram if word not in PHRASE_STOPWORDS)
                if content < PHRASE_MIN_CONTENT_WORDS:
                    continue
                phrase = " ".join(gram)
                if len(phrase) < min_chars:
                    continue
                at.setdefault(phrase, []).append(index)
    return at


def recurring_phrases(
    blocks: list[SubtitleBlock], min_chars: int = PHRASE_MIN_CHARS,
) -> list[str]:
    """Source phrases the file repeats, ranked by how much rendering them
    consistently is worth. Deterministic — no model call — and fed to the scan
    so the glossary is seeded from the file instead of the model's attention.
    """
    kept = {phrase: at
            for phrase, at in _mine_phrases(blocks, min_chars).items()
            if len(at) >= PHRASE_MIN_COUNT}
    by_count: dict[int, list[str]] = {}
    for phrase, at in kept.items():
        by_count.setdefault(len(at), []).append(phrase)

    # A short phrase seen only inside a longer one pins nothing extra.
    survivors = [
        phrase for phrase, at in kept.items()
        if not any(other != phrase and f" {phrase} " in f" {other} "
                   for other in by_count[len(at)])
    ]
    # Code-point order, not locale order: the web mirror must rank identically.
    survivors.sort(key=lambda phrase: (-(len(kept[phrase]) * len(phrase)), phrase))
    # Two phrases recurring in exactly the same cues are windows onto one
    # motif — a repeated line longer than PHRASE_MAX_WORDS — and either check
    # would say the same thing about each of them. The best-ranked one speaks.
    seen: set[frozenset[int]] = set()
    motifs: list[str] = []
    for phrase in survivors:
        cues = frozenset(kept[phrase])
        if cues in seen:
            continue
        seen.add(cues)
        motifs.append(phrase)
    return motifs[:PHRASE_LIMIT]


@dataclass(frozen=True)
class PhraseSplit:
    """A recurring source phrase whose cues came back with no wording in
    common. `blocks` are the source blocks it recurs in, so the repair can
    find their batches."""
    phrase: str
    occurrences: int
    distinct_renderings: int
    blocks: tuple[int, ...]

    @property
    def cause(self) -> str:
        return f"phrase:{self.phrase}"


def phrase_split_message(split: PhraseSplit) -> str:
    return (f"'{split.phrase}' is rendered {split.distinct_renderings} "
            f"different ways across {split.occurrences} lines; no wording is "
            f"shared by all of them")


def find_inconsistent_phrases(
    source: list[SubtitleBlock], output: list[SubtitleBlock],
) -> list[PhraseSplit]:
    """Phrases the file repeats whose finished cues share no wording at all —
    the inconsistency the glossary-drift check cannot see, because it only asks
    whether a PINNED target was used. Whole-file by nature: a quarter of the
    file looks consistent from inside any one batch. A report and a repair
    signal, never a rewrite; which rendering is right is not ours to decide."""
    if not source or not output:
        return []
    # By block number, not position: a missing cue must cost only itself.
    rendered = {b.number: b.text for b in output}
    haystacks = [f" {' '.join(_phrase_words(b.text))} " for b in source]
    splits: list[PhraseSplit] = []
    for phrase in recurring_phrases(source, CONSISTENCY_MIN_CHARS):
        needle = f" {phrase} "
        blocks: list[int] = []
        renderings: list[frozenset[str]] = []
        for block, haystack in zip(source, haystacks, strict=True):
            text = rendered.get(block.number)
            if text is None or needle not in haystack:
                continue
            blocks.append(block.number)
            renderings.append(frozenset(content_words(text)))
        if len(blocks) < CONSISTENCY_MIN_OCCURRENCES:
            continue
        # The phrase's own rendering is not alignable inside a cue, but a
        # wording every one of its cues shares is the best evidence of one.
        if frozenset.intersection(*renderings):
            continue
        splits.append(PhraseSplit(phrase, len(blocks), len(set(renderings)),
                                  tuple(blocks)))
    return splits


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


def clamp_scenes_to_blocks(
    context: FileContext, blocks: list[SubtitleBlock],
) -> FileContext:
    """Clip model-invented scene ranges to the file's real block numbers —
    one hallucinated range can otherwise pull the whole file into a single call."""
    if not context.scenes or not blocks:
        return context
    lo = min(b.number for b in blocks)
    hi = max(b.number for b in blocks)
    kept: list[SceneHint] = []
    for s in context.scenes:
        if s.end < lo or s.start > hi:
            continue
        s.start = max(s.start, lo)
        s.end = min(s.end, hi)
        kept.append(s)
    context.scenes = kept
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
        in_text = detect_participants(joined, context.characters)
        in_text_set = set(in_text)
        kept = [p for p in s.participants if p in in_text_set]
        seen = set(kept)
        for name in in_text:
            if name not in seen:
                kept.append(name)
                seen.add(name)
        s.participants = kept
    return context


def _needs_attribution(
    scene: SceneHint,
    gender_by: dict[str, str],
    full: bool = False,
    target_inflects: bool = False,
) -> bool:
    """A scene with two people in it is worth per-block speakers when their
    genders differ — or when `target_inflects` says the target conjugates for
    gender at all, because then an unknown gender is the ambiguity this call
    exists to resolve, not a reason to skip it. `full` trades calls for knowing
    the speaker of every scene with a cast."""
    if scene.end - scene.start + 1 < ATTRIB_MIN_BLOCKS:
        return False
    if full:
        return len(scene.participants) >= 1
    if len(scene.participants) < 2:
        return False
    known = {g for p in scene.participants
             if (g := gender_by.get(p.casefold(), "unknown")) != "unknown"}
    return len(known) >= 2 or target_inflects


def scenes_needing_attribution(
    context: FileContext,
    full: bool = False,
    target_inflects: bool = False,
) -> list[SceneHint]:
    """Scenes worth one attribution call; already-attributed ones are not redone."""
    if not context.scenes or not context.characters:
        return []
    gender_by = {h.source.casefold(): h.gender for h in context.characters}
    return [s for s in context.scenes
            if not s.attribution
            and _needs_attribution(s, gender_by, full, target_inflects)]
