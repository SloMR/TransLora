from core.srt_parser import (
    SubtitleBlock,
    parse_srt,
    serialize_srt,
    split_batches,
    validate_batch,
)


SAMPLE = (
    "1\n"
    "00:00:01,000 --> 00:00:02,500\n"
    "Hello world\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,500\n"
    "Two\n"
    "lines\n"
)


def test_parse_basic() -> None:
    blocks = parse_srt(SAMPLE)
    assert [b.number for b in blocks] == [1, 2]
    assert blocks[0].timestamp == "00:00:01,000 --> 00:00:02,500"
    assert blocks[1].text == "Two\nlines"


def test_parse_strips_bom_and_crlf() -> None:
    raw = "\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\nHi\r\n"
    blocks = parse_srt(raw)
    assert len(blocks) == 1
    assert blocks[0].text == "Hi"


def test_parse_skips_malformed_blocks() -> None:
    raw = (
        "not-a-number\n"
        "00:00:01,000 --> 00:00:02,500\n"
        "text\n"
        "\n"
        "2\n"
        "00:00:03,000 --> 00:00:04,500\n"
        "good\n"
    )
    blocks = parse_srt(raw)
    assert [b.number for b in blocks] == [2]


def test_serialize_roundtrip() -> None:
    blocks = parse_srt(SAMPLE)
    out = serialize_srt(blocks)
    assert parse_srt(out) == blocks


def test_split_batches_exact_and_remainder() -> None:
    blocks = [SubtitleBlock(i, "00:00:00,000 --> 00:00:01,000", "x") for i in range(1, 8)]
    assert [len(b) for b in split_batches(blocks, 3)] == [3, 3, 1]
    assert [len(b) for b in split_batches(blocks, 7)] == [7]
    assert split_batches([], 5) == []


def test_validate_batch_pass() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    b = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hola")]
    assert validate_batch(a, b).ok


def test_validate_batch_count_mismatch() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    result = validate_batch(a, [])
    assert not result.ok
    assert "count" in result.error.lower()


def test_validate_batch_number_mismatch() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    b = [SubtitleBlock(2, "00:00:01,000 --> 00:00:02,000", "hola")]
    assert not validate_batch(a, b).ok


def test_validate_batch_timestamp_modified() -> None:
    a = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,000", "hi")]
    b = [SubtitleBlock(1, "00:00:01,000 --> 00:00:02,500", "hola")]
    result = validate_batch(a, b)
    assert not result.ok
    assert "timestamp" in result.error.lower()
