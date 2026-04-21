from core.context_pass import (
    FileContext,
    CharacterHint,
    TermHint,
    parse_context_response,
    serialize_for_scan,
    _SCAN_CHAR_BUDGET,
)
from core.srt_parser import SubtitleBlock


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


def test_serialize_for_scan_returns_all_text_when_under_budget():
    blocks = [_block(i, f"Line {i}.") for i in range(1, 6)]
    out = serialize_for_scan(blocks)
    for i in range(1, 6):
        assert f"Line {i}." in out


def test_serialize_for_scan_samples_large_files_under_budget():
    # Build a file that clearly exceeds the scan budget.
    long_line = "x" * 500
    blocks = [_block(i, f"{long_line}-{i}") for i in range(1, 500)]
    out = serialize_for_scan(blocks)
    assert len(out) <= _SCAN_CHAR_BUDGET * 1.1  # small slack for newlines
    # Sampled output must include blocks from across the whole file,
    # not just the first N.
    assert any(f"-{i}" in out for i in range(1, 20))
    assert any(f"-{i}" in out for i in range(450, 500))
