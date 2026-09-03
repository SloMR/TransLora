import pytest

from core.context_parse import parse_attribution_response, parse_context_response
from core.context_pass import (
    CONSISTENCY_MIN_CHARS,
    IDIOM_MAX_TARGET_CHARS,
    MAX_IDIOMS,
    MAX_TERMS,
    PHRASE_LIMIT,
    CharacterHint,
    FileContext,
    PhraseSplit,
    SceneHint,
    TermHint,
    _needs_attribution,
    enrich_scenes_with_block_text,
    find_inconsistent_phrases,
    glossary_key,
    is_definition,
    phrase_split_message,
    recurring_phrases,
    scenes_needing_attribution,
    serialize_for_scan,
    usable_idioms,
)
from core.srt_parser import SubtitleBlock

_TEST_BUDGET = 24_000


def _block(n: int, text: str) -> SubtitleBlock:
    return SubtitleBlock(number=n, timestamp="00:00:00,000 --> 00:00:01,000", text=text)


def test_parse_well_formed_response():
    raw = """
<register>
Target language, neutral register
</register>
<characters>
Alice => TargetAlice | female
Bob => TargetBob | male
Stranger => TargetStranger | unknown
</characters>
<terms>
headquarters => TargetHQ
</terms>
<notes>
- Workplace drama
- Casual register
</notes>
"""
    ctx = parse_context_response(raw)
    assert ctx.register == "Target language, neutral register"
    assert ctx.characters == [
        CharacterHint("Alice", "TargetAlice", "female"),
        CharacterHint("Bob", "TargetBob", "male"),
        CharacterHint("Stranger", "TargetStranger", "unknown"),
    ]
    assert ctx.terms == [TermHint("headquarters", "TargetHQ")]
    assert ctx.notes == ["Workplace drama", "Casual register"]


def test_parse_register_collapses_whitespace_and_bullet():
    raw = """
<register>
  - Target language,
    casual
</register>
<characters>
</characters>
<terms>
</terms>
<notes>
</notes>
"""
    ctx = parse_context_response(raw)
    assert ctx.register == "Target language, casual"


def test_render_includes_register_line_even_when_no_matches():
    ctx = FileContext(
        register="Target language, neutral",
        characters=[CharacterHint("Alice", "TargetAlice", "female")],
        terms=[],
        notes=[],
    )
    batch = [_block(1, "Nobody named here.")]
    rendered = ctx.render_for_batch(batch)
    assert "Target register: Target language, neutral" in rendered
    assert "Alice" not in rendered


def test_the_coarseness_the_scan_recorded_reaches_every_batch():
    """What makes the <register> rule worth changing: the line is file-wide, so
    a scan that records the crudeness carries it into every batch's prompt at
    no extra call."""
    ctx = FileContext(register="Modern Standard Arabic, crude sexual humour")
    for batch in ([_block(1, "Nothing named here.")], [_block(2, "Oh!")]):
        assert ctx.render_for_batch(batch) == (
            "Target register: Modern Standard Arabic, crude sexual humour "
            "(use consistently across every block)")


def test_is_empty_considers_register():
    assert FileContext().is_empty()
    assert not FileContext(register="Target language").is_empty()


def test_parse_tolerates_missing_closing_tag():
    # Real scan models sometimes drop the closing </scenes> tag before the
    # next section. The body should still parse up to the next opening tag.
    raw = """
<register>
Target variant
</register>
<characters>
Alice => آليس | female
</characters>
<terms>
</terms>
<scenes>
1-5 => Alice speaks
6-10 => Alice continues
<notes>
- tone note
</notes>
"""
    ctx = parse_context_response(raw)
    assert ctx.register == "Target variant"
    assert len(ctx.scenes) == 2
    assert ctx.notes == ["tone note"]


def test_parse_tolerates_missing_sections_and_bullets():
    raw = """
<characters>
- Alice => TargetAlice | female
* Bob => TargetBob | MALE
</characters>
"""
    ctx = parse_context_response(raw)
    assert [h.source for h in ctx.characters] == ["Alice", "Bob"]
    assert ctx.characters[1].gender == "male"
    assert ctx.terms == []
    assert ctx.notes == []


def test_parse_garbage_returns_empty():
    assert parse_context_response("").is_empty()
    assert parse_context_response("sorry I cannot help").is_empty()


def test_render_for_batch_only_includes_matching_characters():
    ctx = FileContext(
        characters=[
            CharacterHint("Alice", "TargetAlice", "female"),
            CharacterHint("Bob", "TargetBob", "male"),
        ],
        terms=[TermHint("headquarters", "TargetHQ")],
        notes=["Workplace drama"],
    )
    batch = [_block(1, "Alice, come here."), _block(2, "I'm tired.")]
    rendered = ctx.render_for_batch(batch)
    assert "Alice" in rendered
    assert "Bob" not in rendered
    assert "headquarters" not in rendered
    assert "Workplace drama" in rendered


def test_render_for_batch_empty_when_nothing_matches_and_no_notes():
    ctx = FileContext(
        characters=[CharacterHint("Alice", "TargetAlice", "female")],
        terms=[],
        notes=[],
    )
    batch = [_block(1, "I'm tired.")]
    assert ctx.render_for_batch(batch) == ""


def test_render_word_boundary_does_not_match_substrings():
    ctx = FileContext(
        characters=[CharacterHint("Alice", "TargetAlice", "female")],
        terms=[],
        notes=[],
    )
    # "Alice" as a substring of a longer word must not trigger a match.
    batch = [_block(1, "Alicebot is online.")]
    assert "Alice" not in ctx.render_for_batch(batch)


def test_parse_scenes():
    raw = """
<register>
</register>
<characters>
</characters>
<terms>
</terms>
<scenes>
97-117 => Alice and Carol discuss a concern
279-284 => Dave talks about his daughters
42 => Bob monologues
</scenes>
<notes>
</notes>
"""
    ctx = parse_context_response(raw)
    assert [(s.start, s.end, s.description) for s in ctx.scenes] == [
        (97, 117, "Alice and Carol discuss a concern"),
        (279, 284, "Dave talks about his daughters"),
        (42, 42, "Bob monologues"),
    ]
    # No characters section, so no participants should be detected.
    for s in ctx.scenes:
        assert s.participants == []


def test_parse_scenes_detects_participants_from_characters():
    raw = """
<register>
</register>
<characters>
Alice => Alice | female
Carol => Carol | female
Dave => Dave | male
</characters>
<terms>
</terms>
<scenes>
97-117 => Alice tells Carol her worries
279-284 => Dave complains about his daughters
</scenes>
<notes>
</notes>
"""
    ctx = parse_context_response(raw)
    assert ctx.scenes[0].participants == ["Alice", "Carol"]
    assert ctx.scenes[1].participants == ["Dave"]


def test_parse_scenes_detects_participants_via_target_name():
    # Scan model wrote the scene description using the target-language form
    # of the character's name (common when prompt output slips into the
    # target language). We should still resolve it back to the source name.
    raw = """
<characters>
Alice => آليس | female
Carol => كارول | female
</characters>
<scenes>
97-117 => آليس تخبر كارول بمخاوفها
</scenes>
"""
    ctx = parse_context_response(raw)
    assert ctx.scenes[0].participants == ["Alice", "Carol"]


def test_needs_attribution_only_on_multi_block_mixed_gender_scenes():
    # For a target that does not inflect for gender, per-block speakers only
    # change the translation when the scene mixes known genders.
    g = {"alice": "female", "bob": "male", "ghost": "unknown"}
    mixed = SceneHint(start=1, end=5, description="x", participants=["Alice", "Bob"])
    one_gender = SceneHint(start=1, end=5, description="x", participants=["Alice"])
    unknown_only = SceneHint(start=1, end=5, description="x",
                             participants=["Alice", "Ghost"])
    two_block = SceneHint(start=1, end=2, description="x",
                          participants=["Alice", "Bob"])
    no_one = SceneHint(start=1, end=5, description="x", participants=[])
    assert _needs_attribution(mixed, g) is True
    assert _needs_attribution(one_gender, g) is False
    assert _needs_attribution(unknown_only, g) is False
    assert _needs_attribution(two_block, g) is False
    assert _needs_attribution(no_one, g) is False


def test_an_unknown_gender_no_longer_blocks_an_inflecting_target():
    """Resolving the ambiguity is what the call is for: the graded run's
    gender errors clustered in exactly the scenes the old gate skipped."""
    g = {"alice": "female", "ghost": "unknown"}
    unknown_only = SceneHint(start=1, end=5, description="x",
                             participants=["Alice", "Ghost"])
    both_unknown = SceneHint(start=1, end=5, description="x",
                             participants=["Ghost", "Wraith"])
    assert _needs_attribution(unknown_only, g, target_inflects=True) is True
    assert _needs_attribution(both_unknown, g, target_inflects=True) is True
    # A target with no gendered grammar buys nothing by knowing the speaker.
    assert _needs_attribution(unknown_only, g) is False


def test_an_inflecting_target_still_needs_two_people_in_the_scene():
    """The addressee is what a per-block speaker map resolves; with one
    participant there is nobody to be addressed."""
    g = {"alice": "female"}
    alone = SceneHint(start=1, end=5, description="x", participants=["Alice"])
    short = SceneHint(start=1, end=2, description="x",
                      participants=["Alice", "Ghost"])
    assert _needs_attribution(alone, g, target_inflects=True) is False
    assert _needs_attribution(short, g, target_inflects=True) is False


def test_render_for_batch_includes_speakers_line_when_attribution_present():
    ctx = FileContext(
        characters=[
            CharacterHint("Alice", "Alice", "female"),
            CharacterHint("Bob", "Bob", "male"),
        ],
        scenes=[SceneHint(
            start=10, end=12, description="Alice advises Bob",
            participants=["Alice", "Bob"],
            attribution={10: "Alice", 11: "Alice", 12: "Bob"},
        )],
    )
    batch = [_block(10, "x"), _block(11, "y"), _block(12, "z")]
    out = ctx.render_for_batch(batch)
    assert "speakers: 10=Alice 11=Alice 12=Bob" in out
    assert "[Alice (F), Bob (M)]" in out


def test_enrich_scenes_pulls_names_from_block_text_when_description_omits_them():
    # Description says nothing about who's speaking, but the block text
    # contains a vocative — the classic "the summary is abstract but the
    # dialogue names names" case.
    ctx = FileContext(
        characters=[
            CharacterHint("Alice", "Alice", "female"),
            CharacterHint("Dave", "Dave", "male"),
        ],
        scenes=[SceneHint(start=1, end=3, description="A tense conversation")],
    )
    blocks = [
        _block(1, "Alice, I need a word with you."),
        _block(2, "About what?"),
        _block(3, "Dave said he's leaving."),
    ]
    enriched = enrich_scenes_with_block_text(ctx, blocks)
    assert enriched.scenes[0].participants == ["Alice", "Dave"]


def test_enrich_scenes_preserves_description_order_and_dedups():
    ctx = FileContext(
        characters=[
            CharacterHint("Alice", "Alice", "female"),
            CharacterHint("Dave", "Dave", "male"),
        ],
        scenes=[SceneHint(
            start=1, end=2,
            description="Dave talks to someone",
            participants=["Dave"],
        )],
    )
    blocks = [
        _block(1, "Alice, look at this."),
        _block(2, "Dave, calm down."),
    ]
    enriched = enrich_scenes_with_block_text(ctx, blocks)
    # "Dave" kept (grounded in block 2), "Alice" appended (found in block 1).
    assert enriched.scenes[0].participants == ["Dave", "Alice"]


def test_enrich_drops_description_name_not_in_blocks():
    # Scan hallucinated "Alice" into the description but she never actually
    # speaks in these blocks — drop her, keep only Dave who's really there.
    ctx = FileContext(
        characters=[
            CharacterHint("Alice", "Alice", "female"),
            CharacterHint("Dave", "Dave", "male"),
        ],
        scenes=[SceneHint(
            start=1, end=2,
            description="Alice and Dave talk",
            participants=["Alice", "Dave"],
        )],
    )
    blocks = [
        _block(1, "Dave, are you okay?"),
        _block(2, "I'm fine."),
    ]
    enriched = enrich_scenes_with_block_text(ctx, blocks)
    assert enriched.scenes[0].participants == ["Dave"]


def test_parse_scenes_rejects_substring_match_inside_other_words():
    # A 2-char Arabic transliteration like "لو" would substring-match inside
    # many Arabic words (e.g. "الوقوف" contains "لو"). We require whole-word
    # matching AND a minimum alias length of 3 to avoid these collisions.
    raw = """
<characters>
Lou => لو | male
Alice => آليس | female
</characters>
<scenes>
10-20 => نصائح حول الوقوف وتأثيره على الصحة
21-25 => آليس تطمئن
</scenes>
"""
    ctx = parse_context_response(raw)
    # "Lou" (2-char target "لو") must NOT match inside "الوقوف" (standing).
    assert ctx.scenes[0].participants == []
    assert ctx.scenes[1].participants == ["Alice"]


def test_parse_scenes_skips_malformed_lines():
    raw = """
<scenes>
- 10-20 => Two characters (M, F)
- no-range => missing range
- 30 40 => bad separator
- 50-60 =>
- 70-80 => good one
</scenes>
"""
    ctx = parse_context_response(raw)
    assert [(s.start, s.end) for s in ctx.scenes] == [(10, 20), (70, 80)]


def test_parse_scenes_swaps_reversed_range():
    raw = """
<scenes>
200-100 => Accidentally reversed
</scenes>
"""
    ctx = parse_context_response(raw)
    assert ctx.scenes[0].start == 100
    assert ctx.scenes[0].end == 200


def test_render_includes_overlapping_scenes_only():
    ctx = FileContext(
        scenes=[
            SceneHint(start=1, end=5, description="Scene A"),
            SceneHint(start=10, end=20, description="Scene B"),
            SceneHint(start=50, end=60, description="Scene C"),
        ],
    )
    # Batch covers blocks 15-25 — touches scene B only.
    batch = [_block(15, "line"), _block(25, "line")]
    rendered = ctx.render_for_batch(batch)
    assert "Scene B" in rendered
    assert "Scene A" not in rendered
    assert "Scene C" not in rendered
    assert "Blocks 10-20" in rendered


def test_render_scene_boundary_touch_is_match():
    # Batch first-block equals scene end — still overlaps.
    ctx = FileContext(scenes=[SceneHint(start=5, end=10, description="Boundary scene")])
    batch = [_block(10, "line"), _block(15, "line")]
    assert "Boundary scene" in ctx.render_for_batch(batch)


def test_render_includes_scene_participants_even_if_unnamed_in_batch_text():
    # Carol's name isn't vocatively spoken in the batch blocks, but she IS a
    # scene participant — the translator still needs to know her gender.
    ctx = FileContext(
        characters=[
            CharacterHint("Carol", "Carol", "female"),
            CharacterHint("Dave", "Dave", "male"),
        ],
        scenes=[SceneHint(
            start=1, end=2, description="A conversation",
            participants=["Carol"],
        )],
    )
    batch = [_block(1, "Drink water."), _block(2, "Oh, right.")]
    out = ctx.render_for_batch(batch)
    assert "Carol => Carol (female)" in out
    # Dave isn't a participant and isn't in the text — must NOT be listed.
    assert "Dave" not in out


def test_render_scene_tags_participants_with_gender():
    ctx = FileContext(
        characters=[
            CharacterHint("Alice", "Alice", "female"),
            CharacterHint("Bob", "Bob", "male"),
        ],
        scenes=[
            SceneHint(
                start=10, end=20,
                description="Alice gives Bob an update",
                participants=["Alice", "Bob"],
            ),
        ],
    )
    batch = [_block(10, "x"), _block(20, "y")]
    rendered = ctx.render_for_batch(batch)
    assert "Alice (F)" in rendered
    assert "Bob (M)" in rendered
    assert "Alice gives Bob an update" in rendered


def test_render_scene_without_participants_falls_back_to_description():
    ctx = FileContext(
        scenes=[SceneHint(start=1, end=5, description="Crowd murmurs")],
    )
    batch = [_block(1, "x")]
    rendered = ctx.render_for_batch(batch)
    assert "Crowd murmurs" in rendered
    # No square-bracket prefix when no participants were detected.
    assert "[" not in rendered.split("Crowd murmurs")[0].split("Blocks 1-5:")[-1]


def test_is_empty_considers_scenes():
    ctx = FileContext(scenes=[SceneHint(start=1, end=2, description="x")])
    assert not ctx.is_empty()


def test_serialize_for_scan_returns_all_text_when_under_budget():
    blocks = [_block(i, f"Line {i}.") for i in range(1, 6)]
    out = serialize_for_scan(blocks, _TEST_BUDGET)
    for i in range(1, 6):
        assert f"Line {i}." in out
        assert f"[{i}]" in out


def test_serialize_for_scan_samples_large_files_under_budget():
    # Build a file that clearly exceeds the scan budget.
    long_line = "x" * 500
    blocks = [_block(i, f"{long_line}-{i}") for i in range(1, 500)]
    out = serialize_for_scan(blocks, _TEST_BUDGET)
    assert len(out) <= _TEST_BUDGET * 1.1  # small slack for newlines
    # Sampled output must include blocks from across the whole file,
    # not just the first N.
    assert any(f"-{i}" in out for i in range(1, 20))
    assert any(f"-{i}" in out for i in range(450, 500))


def test_serialize_for_scan_joins_multiline_block_text():
    # Multi-line text must be joined onto the [N] line so the prefix stays
    # usable for scene-range references.
    blocks = [_block(1, "First line\nSecond line")]
    out = serialize_for_scan(blocks, _TEST_BUDGET)
    assert out.splitlines()[0].startswith("[1] ")
    assert "First line" in out
    assert "Second line" in out


# === Recurring-phrase seeding ================================================


# Different filler either side of every cue, so only the phrase itself repeats.
_OPENERS = ["Nadia says", "Omar asks", "Priya replies", "Karim notes",
            "Lena writes", "Rafi jokes", "Tomas sighs"]
_CLOSERS = ["today", "downstairs", "quietly", "afterwards", "twice"]


def _repeat(phrase: str, times: int, start: int = 1) -> list[SubtitleBlock]:
    return [
        _block(n, f"{_OPENERS[n % len(_OPENERS)]} {phrase} {_CLOSERS[n % len(_CLOSERS)]}.")
        for n in range(start, start + times)
    ]


def test_a_phrase_repeated_three_times_is_pinned():
    assert "safety briefing" in recurring_phrases(_repeat("safety briefing", 3))


def test_a_phrase_seen_twice_is_not_pinned():
    assert recurring_phrases(_repeat("safety briefing", 2)) == []


def test_a_short_phrase_is_not_worth_pinning():
    # "the cat" is under the 9-character floor: too generic to fix a rendering.
    assert recurring_phrases(_repeat("the cat", 5)) == []


def test_a_phrase_of_pure_function_words_is_dropped():
    assert recurring_phrases(_repeat("what about that", 5)) == []


def test_the_longest_form_of_a_phrase_wins_when_counts_match():
    phrases = recurring_phrases(_repeat("stack it like it's fragile", 3))
    assert "stack it like it's fragile" in phrases
    assert "stack it like" not in phrases


def test_a_shorter_phrase_seen_more_often_survives_on_its_own():
    blocks = _repeat("safety briefing session", 3)
    blocks += _repeat("safety briefing", 2, start=10)
    phrases = recurring_phrases(blocks)
    assert "safety briefing" in phrases
    assert "safety briefing session" in phrases


def test_phrases_do_not_run_across_cue_boundaries():
    blocks = [_block(i, "ending here") for i in range(1, 4)]
    blocks += [_block(i, "starting there") for i in range(4, 7)]
    assert "here starting there" not in recurring_phrases(blocks)


def test_formatting_tags_are_not_part_of_a_phrase():
    blocks = [_block(i, "{\\i1}safety briefing{\\i0}") for i in range(1, 4)]
    assert recurring_phrases(blocks) == ["safety briefing"]


def test_the_ranking_puts_the_heaviest_phrase_first():
    blocks = _repeat("safety briefing", 5) + _repeat("the video", 3, start=10)
    assert recurring_phrases(blocks)[0] == "safety briefing"


def test_the_phrase_list_is_capped():
    blocks: list[SubtitleBlock] = []
    for i in range(PHRASE_LIMIT + 10):
        blocks += _repeat(f"quarterly report number {i}", 3, start=i * 10)
    assert len(recurring_phrases(blocks)) == PHRASE_LIMIT


def test_a_file_with_nothing_repeated_seeds_nothing():
    assert recurring_phrases([_block(1, "Hello there."), _block(2, "Bye.")]) == []


# === File-level phrase consistency ===========================================

# The graded run alternated between two renderings of the episode's central
# motif across seven cues and nothing noticed: no batch sees more than a
# tenth of a file, and the drift report only knows targets the glossary pinned.


def _rendered(blocks: list[SubtitleBlock], texts: list[str]):
    return [SubtitleBlock(b.number, b.timestamp, text)
            for b, text in zip(blocks, texts, strict=True)]


def test_a_phrase_rendered_two_ways_is_reported():
    source = _repeat("safety briefing", 4)
    output = _rendered(source, ["نبذة أمنية", "إحاطة السلامة",
                                "نبذة أمنية", "إحاطة السلامة"])
    [split] = find_inconsistent_phrases(source, output)
    assert split.phrase == "safety briefing"
    assert (split.occurrences, split.distinct_renderings) == (4, 2)
    assert split.blocks == (1, 2, 3, 4)


def test_a_phrase_every_cue_shares_a_word_for_is_left_alone():
    source = _repeat("safety briefing", 4)
    output = _rendered(source, ["إحاطة السلامة", "إحاطة أمنية",
                                "إحاطة السلامة اليوم", "إحاطة قصيرة"])
    assert find_inconsistent_phrases(source, output) == []


def test_three_cues_disagreeing_are_not_enough():
    # Below CONSISTENCY_MIN_OCCURRENCES: three paraphrases of three different
    # sentences, not yet a rendering the file failed to make.
    source = _repeat("safety briefing", 3)
    assert find_inconsistent_phrases(
        source, _rendered(source, ["نبذة", "إحاطة", "تنبيه"])) == []


def test_an_eight_character_motif_is_still_checked():
    # "dry dock" is under the scan's own floor, where a fragment costs a term
    # slot. Here it costs nothing, and it is the phrase the graded run split.
    source = [_block(n, f"{opener} dry dock {closer}.") for n, (opener, closer)
              in enumerate([("Nadia says she crossed", "today"),
                            ("Omar asks where is", "downstairs"),
                            ("Priya replies that Karim crossed", "quietly"),
                            ("Lena writes about", "twice")], start=1)]
    output = _rendered(source, ["الحد", "الخط", "الحدود", "المسموح"])
    assert [s.phrase for s in find_inconsistent_phrases(source, output)] == \
        ["dry dock"]


def test_one_word_wearing_function_words_is_a_word_not_a_phrase():
    # "the match" comes out of Arabic as one inflected word; pinning it is the
    # glossary's job, and reading its varied endings as drift was pure noise.
    assert recurring_phrases(_repeat("the match", 5), CONSISTENCY_MIN_CHARS) == []
    assert recurring_phrases(_repeat("should be", 5), CONSISTENCY_MIN_CHARS) == []


def test_the_best_ranked_window_speaks_for_a_repeated_line():
    # A quoted line longer than PHRASE_MAX_WORDS mines into overlapping
    # windows that recur in exactly the same cues; one seed says it all.
    line = "more painful than the risk it takes to blossom"
    assert recurring_phrases(_repeat(line, 3)) == ["more painful than the risk"]


def test_a_phrase_the_source_never_repeats_is_never_checked():
    source = [_block(1, "A safety briefing today."), _block(2, "Nothing else.")]
    assert find_inconsistent_phrases(source, _rendered(source, ["س", "ص"])) == []


def test_the_split_carries_the_cause_the_repair_groups_on():
    source = _repeat("safety briefing", 4)
    output = _rendered(source, ["نبذة", "إحاطة", "تنبيه", "تذكير"])
    assert find_inconsistent_phrases(source, output)[0].cause == \
        "phrase:safety briefing"


def test_the_warning_names_the_phrase_the_count_and_the_split():
    assert phrase_split_message(PhraseSplit("the line", 4, 3, (1, 2, 3, 4))) == (
        "'the line' is rendered 3 different ways across 4 cues; no wording is "
        "shared by all of them"
    )


def test_only_the_cues_whose_source_uses_the_phrase_are_compared():
    # The unrelated cue shares no word with any of them; including it would
    # make every phrase in the file look split.
    source = [*_repeat("safety briefing", 4), _block(9, "Unrelated line here.")]
    output = _rendered(source, ["إحاطة السلامة", "إحاطة أمنية",
                                "إحاطة قصيرة", "إحاطة أخرى", "لا شيء"])
    assert find_inconsistent_phrases(source, output) == []


def test_punctuation_between_the_words_does_not_hide_the_phrase():
    # The phrase is matched on the tokens it was mined from, so a comma the
    # source happens to carry cannot make the cue unfindable.
    source = _repeat("safety briefing", 4)
    source[0] = _block(source[0].number, "Nadia says safety, briefing today.")
    output = _rendered(source, ["نبذة", "إحاطة", "تنبيه", "تذكير"])
    assert find_inconsistent_phrases(source, output)[0].occurrences == 4


def test_a_formatting_tag_is_not_a_shared_rendering():
    source = _repeat("safety briefing", 4)
    output = _rendered(source, ["{\\i1}نبذة{\\i0}", "{\\i1}إحاطة{\\i0}",
                                "{\\i1}تنبيه{\\i0}", "{\\i1}تذكير{\\i0}"])
    assert len(find_inconsistent_phrases(source, output)) == 1


def test_a_cue_the_output_lost_costs_only_itself():
    # Matched by block number: a short output must not shift every rendering
    # onto the wrong source cue.
    source = _repeat("safety briefing", 5)
    output = _rendered(source, ["نبذة", "إحاطة", "تنبيه", "تذكير", "إشعار"])
    [split] = find_inconsistent_phrases(source, output[:1] + output[2:])
    assert (split.occurrences, split.blocks) == (4, (1, 3, 4, 5))


def test_an_empty_file_is_not_a_finding():
    assert find_inconsistent_phrases([], []) == []
    assert find_inconsistent_phrases(_repeat("safety briefing", 4), []) == []


# === Multi-word terms ========================================================


def test_a_multi_word_term_is_injected_into_its_batch():
    ctx = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    batch = [_block(1, "We sat through a safety briefing session.")]
    assert "safety briefing => جلسة السلامة" in ctx.render_for_batch(batch)


def test_a_multi_word_term_matches_across_a_line_break():
    ctx = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    batch = [_block(1, "a safety\nbriefing session")]
    assert "safety briefing" in ctx.render_for_batch(batch)


def test_a_multi_word_term_is_not_matched_by_one_of_its_words():
    ctx = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    batch = [_block(1, "a briefing case")]
    assert ctx.render_for_batch(batch) == ""


def test_a_multi_word_term_respects_word_boundaries():
    ctx = FileContext(terms=[TermHint("all right", "حسنا")])
    batch = [_block(1, "the hall rightly closed")]
    assert ctx.render_for_batch(batch) == ""


def test_a_multi_word_term_makes_a_batch_worth_reviewing():
    ctx = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])
    assert ctx.has_correctable_entries([_block(1, "safety briefing training")])


# === Glossary drift ==========================================================

_BRIEFING = FileContext(terms=[TermHint("safety briefing", "جلسة السلامة")])


def test_a_term_rendered_some_other_way_is_reported():
    batch = [_block(1, "A safety briefing session.")]
    output = [_block(1, "ندوة عن حصة أخرى الجنسية.")]
    assert _BRIEFING.drift_warnings(batch, output) == [
        "Block 1: glossary term 'safety briefing' was not rendered as "
        "'جلسة السلامة'"]


def test_a_term_rendered_as_asked_is_not_reported():
    batch = [_block(1, "A safety briefing session.")]
    output = [_block(1, "ندوة عن جلسة السلامة.")]
    assert _BRIEFING.drift_warnings(batch, output) == []


def test_a_term_the_batch_never_uses_is_not_reported():
    assert _BRIEFING.drift_warnings([_block(1, "Good morning.")],
                                      [_block(1, "صباح الخير.")]) == []


def test_the_drift_report_names_the_batch_so_it_can_be_found():
    batch = [_block(7, "Nothing here."), _block(8, "safety briefing again")]
    output = [_block(7, "لا شيء هنا."), _block(8, "حصة أخرى مجددا")]
    assert _BRIEFING.drift_warnings(batch, output)[0].startswith("Block 7:")


def test_a_term_rendered_with_an_attached_prefix_still_counts():
    # Arabic glues conjunctions to the word; a whole-word test would misfire.
    batch = [_block(1, "safety briefing")]
    output = [_block(1, "وجلسة السلامة")]
    assert _BRIEFING.drift_warnings(batch, output) == []


def test_an_empty_batch_reports_nothing():
    assert _BRIEFING.drift_warnings([], []) == []


# === Character-name drift ====================================================

# All three graded systems wrote فيليس at cues 340/358 and فيلس at 346, and
# nothing noticed: the drift check covered terms only.
_CAST = FileContext(characters=[CharacterHint("Phyllis", "فيليس", "female")])


def test_a_character_name_spelled_some_other_way_is_reported():
    batch = [_block(346, "Phyllis, close the door.")]
    output = [_block(346, "فيلس، أغلقي الباب.")]
    assert _CAST.drift_warnings(batch, output) == [
        "Block 346: character name 'Phyllis' was not rendered as 'فيليس'"]


def test_a_character_name_rendered_as_pinned_is_not_reported():
    batch = [_block(340, "Phyllis, close the door.")]
    assert _CAST.drift_warnings(batch, [_block(340, "فيليس، أغلقي الباب.")]) == []


def test_a_name_the_cues_never_say_is_not_reported():
    """A scene participant who says nothing by name has no target form to
    miss — flagging one would fire on most of the file."""
    ctx = FileContext(
        characters=[CharacterHint("Phyllis", "فيليس", "female")],
        scenes=[SceneHint(start=1, end=5, description="x",
                          participants=["Phyllis"])],
    )
    assert ctx.drift_warnings([_block(1, "Close the door.")],
                              [_block(1, "أغلقي الباب.")]) == []


def test_a_drifted_name_carries_its_own_repair_cause():
    batch = [_block(346, "Phyllis, close the door.")]
    drifts = _CAST.drift_entries(batch, [_block(346, "فيلس، أغلقي الباب.")])
    assert [d.cause for d in drifts] == ["name:Phyllis"]


def test_a_drifted_term_carries_its_own_repair_cause():
    batch = [_block(1, "A safety briefing session.")]
    drifts = _BRIEFING.drift_entries(batch, [_block(1, "ندوة أخرى.")])
    assert [d.cause for d in drifts] == ["term:safety briefing"]


def test_terms_and_names_are_reported_together():
    ctx = FileContext(
        characters=[CharacterHint("Phyllis", "فيليس", "female")],
        terms=[TermHint("safety briefing", "جلسة السلامة")],
    )
    batch = [_block(2, "Phyllis, the safety briefing.")]
    assert [d.kind for d in ctx.drift_entries(batch, [_block(2, "لا شيء.")])] \
        == ["term", "name"]


# === Term budget =============================================================


def test_the_parser_keeps_up_to_the_full_term_budget():
    lines = "\n".join(f"term{i} => hedef{i}" for i in range(MAX_TERMS + 5))
    ctx = parse_context_response(
        f"<register></register><characters></characters>"
        f"<terms>\n{lines}\n</terms><scenes></scenes><notes></notes>")
    assert len(ctx.terms) == MAX_TERMS
    assert MAX_TERMS == 25


# === Attribution response ====================================================

_ROSTER = [
    CharacterHint("Alice", "Alice", "female"),
    CharacterHint("Bob", "Bob", "male"),
]
_SCENE = SceneHint(start=10, end=12, description="x", participants=["Alice", "Bob"])


def test_parse_attribution_keeps_in_range_roster_names():
    out = parse_attribution_response("10=Alice\n11=Bob\n12=unknown", _SCENE, _ROSTER)
    assert out == {10: "Alice", 11: "Bob", 12: "unknown"}


def test_parse_attribution_drops_blocks_outside_the_scene():
    out = parse_attribution_response("9=Alice\n10=Alice\n13=Bob", _SCENE, _ROSTER)
    assert out == {10: "Alice"}


def test_parse_attribution_drops_names_not_on_the_roster():
    out = parse_attribution_response("10=Carol\n11=Bob", _SCENE, _ROSTER)
    assert out == {11: "Bob"}


def test_parse_attribution_strips_quotes_and_ignores_commentary():
    raw = "Here you go:\n10=\"Alice\"\n11='Bob'\nthat's all"
    assert parse_attribution_response(raw, _SCENE, _ROSTER) == {
        10: "Alice", 11: "Bob"}


def test_parse_attribution_returns_empty_for_no_usable_lines():
    assert parse_attribution_response("", _SCENE, _ROSTER) == {}
    assert parse_attribution_response("no idea", _SCENE, _ROSTER) == {}


def test_scenes_needing_attribution_skips_already_attributed_scenes():
    scene = SceneHint(start=10, end=12, description="x",
                      participants=["Alice", "Bob"])
    ctx = FileContext(characters=_ROSTER, scenes=[scene])
    assert scenes_needing_attribution(ctx) == [scene]
    scene.attribution = {10: "Alice"}
    assert scenes_needing_attribution(ctx) == []


# === Idioms ==================================================================


_IDIOMS = FileContext(idioms=[
    TermHint("that ship has sailed", "تعبير مجازي"),
    TermHint("break a leg", "بالتوفيق"),
])


def test_the_scan_idioms_section_is_parsed_into_its_own_list():
    ctx = parse_context_response("""
<terms>
safety briefing => جلسة السلامة
</terms>
<idioms>
that ship has sailed => تعبير مجازي
- break a leg => بالتوفيق
</idioms>
<scenes>
1-5 => Alice talks to Bob
</scenes>
""")
    assert [t.source for t in ctx.terms] == ["safety briefing"]
    assert [(i.source, i.target) for i in ctx.idioms] == [
        ("that ship has sailed", "تعبير مجازي"),
        ("break a leg", "بالتوفيق"),
    ]
    # The new section must not swallow the one after it.
    assert ctx.scenes[0].description == "Alice talks to Bob"


def test_a_response_without_an_idioms_section_still_parses():
    ctx = parse_context_response("<terms>\na => b\n</terms>")
    assert ctx.idioms == []
    assert not ctx.is_empty()


def test_idioms_alone_are_a_glossary_worth_having():
    assert not _IDIOMS.is_empty()


def test_the_idiom_cap_matches_what_the_scan_was_asked_for():
    body = "\n".join(f"idiom {n} => target {n}" for n in range(30))
    ctx = parse_context_response(f"<idioms>\n{body}\n</idioms>")
    assert len(ctx.idioms) == MAX_IDIOMS == 15


def test_only_the_idioms_in_the_batch_are_rendered():
    batch = [_block(1, "Well, that ship has sailed!")]
    rendered = _IDIOMS.render_for_batch(batch)
    assert rendered == (
        "Idioms - render by meaning, never word for word:\n"
        "- that ship has sailed => تعبير مجازي")


def test_an_idiom_split_over_a_line_break_is_still_matched():
    # Same phrase-aware matching terms use: the cue wrapped mid-phrase.
    batch = [_block(1, "Well, that ship\nhas sailed!")]
    assert "that ship has sailed" in _IDIOMS.render_for_batch(batch)


def test_a_batch_with_no_idiom_renders_nothing():
    assert _IDIOMS.render_for_batch([_block(1, "Good morning.")]) == ""


def test_idioms_are_rendered_after_the_terms_they_extend():
    ctx = FileContext(
        terms=[TermHint("headquarters", "المقر")],
        idioms=[TermHint("break a leg", "بالتوفيق")],
    )
    rendered = ctx.render_for_batch([_block(1, "Break a leg at headquarters")])
    assert rendered.index("Terms:") < rendered.index("Idioms - render by meaning")


def test_an_idiom_alone_is_worth_a_review_call():
    # The reviewer's idiom correction could otherwise only fire on a batch
    # that also named a character or a term — a cap on the weakest aspect.
    assert _IDIOMS.has_correctable_entries([_block(1, "break a leg")])


def test_a_batch_naming_nothing_at_all_is_still_not_reviewed():
    assert not _IDIOMS.has_correctable_entries([_block(1, "Good morning.")])


def test_a_register_alone_is_still_not_worth_a_review_call():
    """The reviewer may restore a softened register, but the register line is
    file-wide: on its own it says nothing about THIS batch."""
    ctx = FileContext(register="Modern Standard Arabic, crude sexual humour")
    assert not ctx.has_correctable_entries([_block(1, "Good morning.")])


def test_an_unrendered_idiom_is_not_reported_as_drift():
    """A pinned idiom is a suggestion — flagging every one would drown the
    real glossary drift and feed the repair pass noise."""
    batch = [_block(1, "break a leg")]
    assert _IDIOMS.drift_warnings(batch, [_block(1, "حظا موفقا")]) == []


# === One key, one table ======================================================

# The graded run's worst defect: the episode's signature punchline was pinned
# correctly under <terms> and defined under <idioms>, and the definition won.
_PUNCHLINE = "that's what she said"
_CORRECT = "هذا ما قالته هي"
_DEFINITION = "هذا تعبير ساخر يُستخدم للرد على جملة تحتمل معنى مزدوجا"


def test_a_term_and_an_idiom_may_not_pin_the_same_phrase():
    kept = usable_idioms(
        [TermHint(_PUNCHLINE, _CORRECT)],
        [TermHint(_PUNCHLINE, "شيء آخر")],
    )
    assert kept == []


def test_the_collision_is_judged_on_the_folded_key_not_the_spelling():
    kept = usable_idioms(
        [TermHint("That's  What   She Said", _CORRECT)],
        [TermHint("that's what she said", "شيء آخر")],
    )
    assert kept == []


@pytest.mark.parametrize("source,target", [
    ("break a leg", "بالتوفيق"),
    # Long, but not several times its source: a real equivalent may be wordy.
    ("that ship has sailed", "فات الأوان على ذلك تماما"),
    # Several times its source, but still short enough to read as a subtitle.
    ("big deal", "أمر جلل"),
])
def test_a_real_equivalent_survives_the_definition_check(source, target):
    hint = TermHint(source, target)
    assert is_definition(hint) is False
    assert usable_idioms([], [hint]) == [hint]


def test_an_idiom_target_that_explains_itself_is_dropped():
    hint = TermHint(_PUNCHLINE, _DEFINITION)
    assert len(_DEFINITION) > IDIOM_MAX_TARGET_CHARS
    assert is_definition(hint) is True
    assert usable_idioms([], [hint]) == []


def test_an_uncollided_idiom_is_left_alone():
    idioms = [TermHint("break a leg", "بالتوفيق")]
    assert usable_idioms([TermHint("headquarters", "المقر")], idioms) == idioms


@pytest.mark.parametrize("source,key", [
    ("  That's  What She Said ", "that's what she said"),
    ("BREAK A LEG", "break a leg"),
])
def test_the_key_folds_case_and_collapses_whitespace(source, key):
    assert glossary_key(source) == key


def test_the_parsed_glossary_never_holds_one_key_twice():
    ctx = parse_context_response(f"""
<terms>
{_PUNCHLINE} => {_CORRECT}
</terms>
<idioms>
{_PUNCHLINE} => {_DEFINITION}
break a leg => بالتوفيق
</idioms>
""")
    assert [(t.source, t.target) for t in ctx.terms] == [(_PUNCHLINE, _CORRECT)]
    assert [i.source for i in ctx.idioms] == ["break a leg"]


def test_a_dropped_idiom_does_not_spend_one_of_the_fifteen_slots():
    """Deduping before the budget, not after, so a poisoned entry costs the
    glossary nothing."""
    body = "\n".join(f"idiom {n} => target {n}" for n in range(MAX_IDIOMS + 5))
    ctx = parse_context_response(
        f"<terms>\nidiom 0 => pinned\n</terms>\n<idioms>\n{body}\n</idioms>")
    assert len(ctx.idioms) == MAX_IDIOMS
    assert "idiom 0" not in [i.source for i in ctx.idioms]


# === Full attribution ========================================================


def _scene(participants: list[str], start: int = 1, end: int = 5) -> SceneHint:
    return SceneHint(start=start, end=end, description="x",
                     participants=participants)


_GENDERS = {"alice": "female", "bob": "male", "ghost": "unknown"}


def test_full_attribution_ignores_whether_the_genders_are_known():
    two_unknown = _scene(["Ghost", "Wraith"])
    assert _needs_attribution(two_unknown, _GENDERS) is False
    assert _needs_attribution(two_unknown, _GENDERS, full=True) is True


def test_full_attribution_buys_a_call_for_every_scene_with_a_cast():
    alone = _scene(["Alice"])
    assert _needs_attribution(alone, _GENDERS) is False
    assert _needs_attribution(alone, _GENDERS, full=True) is True


def test_full_attribution_still_skips_a_scene_with_nobody_in_it():
    empty = _scene([])
    assert _needs_attribution(empty, _GENDERS, full=True,
                              target_inflects=True) is False


def test_full_attribution_never_shortens_the_block_floor():
    # A one- or two-block scene is too short for per-block speakers to pay off,
    # whatever the flag says.
    short = _scene(["Alice", "Bob"], start=1, end=2)
    assert _needs_attribution(short, _GENDERS, full=True,
                              target_inflects=True) is False


def test_scenes_needing_attribution_passes_the_mode_through():
    ctx = FileContext(
        characters=[CharacterHint("Ghost", "Ghost", "unknown"),
                    CharacterHint("Wraith", "Wraith", "unknown")],
        scenes=[_scene(["Ghost", "Wraith"])],
    )
    assert scenes_needing_attribution(ctx) == []
    assert len(scenes_needing_attribution(ctx, full=True)) == 1
