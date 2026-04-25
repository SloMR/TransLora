from core.context_pass import (
    FileContext,
    CharacterHint,
    TermHint,
    SceneHint,
    _needs_attribution,
    enrich_scenes_with_block_text,
    parse_context_response,
    serialize_for_scan,
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


def test_needs_attribution_triggers_on_multi_block_named_scenes():
    g = {"alice": "female", "bob": "male"}
    multi_named = SceneHint(start=1, end=5, description="x", participants=["Alice", "Bob"])
    multi_one = SceneHint(start=1, end=5, description="x", participants=["Alice"])
    two_block = SceneHint(start=1, end=2, description="x", participants=["Alice"])
    no_one = SceneHint(start=1, end=5, description="x", participants=[])
    assert _needs_attribution(multi_named, g) is True
    assert _needs_attribution(multi_one, g) is True
    assert _needs_attribution(two_block, g) is False
    assert _needs_attribution(no_one, g) is False


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
        _block(1, "Alice, I need to talk to you."),
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
