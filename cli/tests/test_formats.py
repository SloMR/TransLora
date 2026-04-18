import pytest

from core.formats import parse_subtitle


def _translate(blocks):
    """Return a shallow-cloned block list with text reversed, preserving number/timestamp."""
    return [
        type(b)(number=b.number, timestamp=b.timestamp, text=b.text[::-1])
        for b in blocks
    ]


def test_srt_roundtrip() -> None:
    src = (
        "1\n00:00:01,000 --> 00:00:02,500\nHello <i>world</i>\n\n"
        "2\n00:00:03,000 --> 00:00:04,500\nTwo\nlines\n"
    )
    doc = parse_subtitle("a.srt", src)
    assert doc.format == "srt"
    assert len(doc.blocks) == 2
    out = doc.rebuild(doc.blocks)
    # Timestamps preserved and italic survives the roundtrip.
    assert "00:00:01,000 --> 00:00:02,500" in out
    assert "<i>" in out


def test_vtt_roundtrip_preserves_header() -> None:
    src = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.500\nHello\n\n"
        "00:00:03.000 --> 00:00:04.500\nTwo\nlines\n"
    )
    doc = parse_subtitle("a.vtt", src)
    assert doc.format == "vtt"
    assert len(doc.blocks) == 2
    out = doc.rebuild(doc.blocks)
    assert out.startswith("WEBVTT")


def test_ass_preserves_script_info_and_styles() -> None:
    src = (
        "[Script Info]\n"
        "Title: MyTitle\n"
        "ScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize\n"
        "Style: Default,Arial,20\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hello\n"
        "Dialogue: 0,0:00:03.00,0:00:04.50,Default,,0,0,0,,Line one\\NLine two\n"
    )
    doc = parse_subtitle("a.ass", src)
    assert doc.format == "ass"
    assert len(doc.blocks) == 2
    # Multi-line ASS `\N` becomes `\n` in normalized text.
    assert doc.blocks[1].text == "Line one\nLine two"
    out = doc.rebuild(doc.blocks)
    assert "MyTitle" in out
    assert "[Events]" in out
    assert "Default" in out
    # Multi-line translation re-emits as `\N`.
    assert "\\N" in out


def test_ssa_roundtrip() -> None:
    src = (
        "[Script Info]\n"
        "ScriptType: v4.00\n\n"
        "[V4 Styles]\n"
        "Format: Name, Fontname, Fontsize\n"
        "Style: Default,Arial,20\n\n"
        "[Events]\n"
        "Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: Marked=0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hi there\n"
    )
    doc = parse_subtitle("a.ssa", src)
    assert doc.format == "ssa"
    assert len(doc.blocks) == 1
    assert "Hi there" in doc.blocks[0].text
    assert "[Events]" in doc.rebuild(doc.blocks)


def test_sbv_roundtrip() -> None:
    src = (
        "0:00:01.000,0:00:02.500\nHello\n\n"
        "0:00:03.000,0:00:04.500\nTwo\nlines\n"
    )
    doc = parse_subtitle("a.sbv", src)
    assert doc.format == "sbv"
    assert len(doc.blocks) == 2
    out = doc.rebuild(doc.blocks)
    assert "0:00:01.000,0:00:02.500" in out


def test_sub_microdvd_roundtrip() -> None:
    src = "{1}{2}Line one|Line two\n{3}{4}Another\n"
    doc = parse_subtitle("a.sub", src)
    assert doc.format == "sub"
    assert len(doc.blocks) == 2
    # MicroDVD '|' line-break becomes '\n' in normalized text.
    assert doc.blocks[0].text == "Line one\nLine two"
    out = doc.rebuild(doc.blocks)
    assert "Line one|Line two" in out


def test_unknown_extension_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        parse_subtitle("a.xyz", "irrelevant")


def test_translation_applied_on_rebuild() -> None:
    src = "1\n00:00:01,000 --> 00:00:02,500\nhello\n"
    doc = parse_subtitle("a.srt", src)
    translated = _translate(doc.blocks)
    out = doc.rebuild(translated)
    assert "olleh" in out
    assert "hello" not in out
