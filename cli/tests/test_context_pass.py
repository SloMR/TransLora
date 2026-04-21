from core.context_pass import (
    FileContext,
    CharacterHint,
    TermHint,
    parse_context_response,
)
from core.srt_parser import SubtitleBlock


def _block(n: int, text: str) -> SubtitleBlock:
    return SubtitleBlock(number=n, timestamp="00:00:00,000 --> 00:00:01,000", text=text)


def test_parse_well_formed_response():
    raw = """
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
    assert ctx.characters == [
        CharacterHint("Amy", "إيمي", "female"),
        CharacterHint("Jake", "جيك", "male"),
        CharacterHint("Stranger", "غريب", "unknown"),
    ]
    assert ctx.terms == [TermHint("precinct", "قسم الشرطة")]
    assert ctx.notes == ["Modern police procedural", "Casual register"]


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
