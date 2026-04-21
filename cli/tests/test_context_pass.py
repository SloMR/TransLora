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
Modern Standard Arabic, neutral
</register>
<characters>
Amy => إيمي | female
Jake => جيك | male
Stranger => غريب | unknown
</characters>
<terms>
precinct => قسم الشرطة
</terms>
<notes>
- Modern police procedural
- Casual register
</notes>
"""
    ctx = parse_context_response(raw)
    assert ctx.register == "Modern Standard Arabic, neutral"
    assert ctx.characters == [
        CharacterHint("Amy", "إيمي", "female"),
        CharacterHint("Jake", "جيك", "male"),
        CharacterHint("Stranger", "غريب", "unknown"),
    ]
    assert ctx.terms == [TermHint("precinct", "قسم الشرطة")]
    assert ctx.notes == ["Modern police procedural", "Casual register"]


def test_parse_register_collapses_whitespace_and_bullet():
    raw = """
<register>
  - Brazilian Portuguese,
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
    assert ctx.register == "Brazilian Portuguese, casual"


def test_render_includes_register_line_even_when_no_matches():
    ctx = FileContext(
        register="Modern Standard Arabic, neutral",
        characters=[CharacterHint("Amy", "إيمي", "female")],
        terms=[],
        notes=[],
    )
    batch = [_block(1, "Nobody named here.")]
    rendered = ctx.render_for_batch(batch)
    assert "Target register: Modern Standard Arabic, neutral" in rendered
    assert "Amy" not in rendered


def test_is_empty_considers_register():
    assert FileContext().is_empty()
    assert not FileContext(register="MSA").is_empty()


def test_parse_tolerates_missing_sections_and_bullets():
    raw = """
<characters>
- Amy => إيمي | female
* Jake => جيك | MALE
</characters>
"""
    ctx = parse_context_response(raw)
    assert [h.source for h in ctx.characters] == ["Amy", "Jake"]
    assert ctx.characters[1].gender == "male"
    assert ctx.terms == []
    assert ctx.notes == []


def test_parse_garbage_returns_empty():
    assert parse_context_response("").is_empty()
    assert parse_context_response("sorry I cannot help").is_empty()


def test_render_for_batch_only_includes_matching_characters():
    ctx = FileContext(
        characters=[
            CharacterHint("Amy", "إيمي", "female"),
            CharacterHint("Jake", "جيك", "male"),
        ],
        terms=[TermHint("precinct", "قسم الشرطة")],
        notes=["Police procedural"],
    )
    batch = [_block(1, "Amy, come here."), _block(2, "I'm tired.")]
    rendered = ctx.render_for_batch(batch)
    assert "Amy" in rendered
    assert "Jake" not in rendered
    assert "precinct" not in rendered
    assert "Police procedural" in rendered


def test_render_for_batch_empty_when_nothing_matches_and_no_notes():
    ctx = FileContext(
        characters=[CharacterHint("Amy", "إيمي", "female")],
        terms=[],
        notes=[],
    )
    batch = [_block(1, "I'm tired.")]
    assert ctx.render_for_batch(batch) == ""


def test_render_word_boundary_does_not_match_substrings():
    ctx = FileContext(
        characters=[CharacterHint("Amy", "إيمي", "female")],
        terms=[],
        notes=[],
    )
    # "Amy" inside "Amyloid" should not match.
    batch = [_block(1, "Amyloid plaques.")]
    assert "Amy" not in ctx.render_for_batch(batch)


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
