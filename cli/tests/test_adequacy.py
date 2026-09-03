"""The opt-in back-translation spot check: what it samples, what it flags,
and how it behaves when the round trip comes back unusable.

Every test replaces `call_chat_api`, so nothing here touches the network.
"""

from __future__ import annotations

import core.adequacy as ad
from core.adequacy import token_overlap, verify_adequacy
from core.batch_runner import BatchResult
from core.constants import ADEQUACY_MIN_OVERLAP
from core.prompt import BACK_TRANSLATION_SYSTEM_PROMPT
from core.srt_parser import SubtitleBlock, serialize_lite, split_batches
from tests.conftest import make_block, make_blocks, run_async


def _install_chat(monkeypatch, handler):
    calls: list[tuple[str, str]] = []

    async def fake_call(client, system_prompt, user_message, cfg, max_tokens):
        calls.append((system_prompt, user_message))
        return handler(user_message, len(calls))

    monkeypatch.setattr(ad, "call_chat_api", fake_call)
    return calls


def _echo_source(batches: list[list[SubtitleBlock]]):
    """A perfect round trip: every block comes back as its own source text."""
    by_number = {b.number: b.text for batch in batches for b in batch}

    def handler(user_message: str, n: int) -> str:
        numbers = [int(line) for line in user_message.splitlines()
                   if line.strip().isdigit()]
        return serialize_lite([
            SubtitleBlock(number, "", by_number[number]) for number in numbers
        ])
    return handler


# === Token overlap ===========================================================


def test_an_exact_round_trip_scores_one() -> None:
    assert token_overlap("Any re-orders today?", "any reorders today") == 1.0


def test_a_dropped_clause_scores_below_the_threshold() -> None:
    source = "I am the head of dispatch and it is how I run the yard"
    assert token_overlap(source, "I am the head") < ADEQUACY_MIN_OVERLAP


def test_a_rewording_that_keeps_the_content_still_passes() -> None:
    source = "Check your storage locker."
    assert token_overlap(source, "check the storage locker now") >= ADEQUACY_MIN_OVERLAP


def test_a_cue_with_no_words_at_all_counts_as_covered() -> None:
    # "..." or "♪" carries no meaning to lose, so it can never be flagged.
    assert token_overlap("...", "") == 1.0


def test_overlap_is_measured_against_the_source_not_the_reply() -> None:
    # A padded back-translation must not be punished for its own extra words.
    assert token_overlap("the briefing", "the long boring briefing today") == 1.0


# === The pass ================================================================


def test_a_clean_round_trip_flags_nothing(monkeypatch, cfg) -> None:
    cfg.verify_adequacy = True
    batches = split_batches(make_blocks(20), 5)
    results = [BatchResult(list(b)) for b in batches]
    calls = _install_chat(monkeypatch, _echo_source(batches))

    found = run_async(verify_adequacy(None, batches, results, cfg))

    assert found == {}
    # 4 batches: the two-batch minimum rounds up to half the file here.
    assert len(calls) == 2
    assert cfg.calls.back_translation == 2
    assert all(c[0] is BACK_TRANSLATION_SYSTEM_PROMPT for c in calls)
    assert "Translate back to English:" in calls[0][1]


SENTENCE = "the harbour master signs the log book at eleven tonight"


def test_a_cue_that_lost_its_meaning_is_flagged(monkeypatch, cfg) -> None:
    cfg.verify_adequacy = True
    blocks = [make_block(n, f"{SENTENCE} {n}") for n in range(1, 5)]
    batches = split_batches(blocks, 2)
    results = [BatchResult(list(b)) for b in batches]

    def handler(user_message: str, n: int) -> str:
        numbers = [int(line) for line in user_message.splitlines()
                   if line.strip().isdigit()]
        # Block 1 comes back as something else entirely; the rest survive.
        return serialize_lite([
            SubtitleBlock(number, "",
                          "completely unrelated words about nothing"
                          if number == 1 else f"{SENTENCE} {number}")
            for number in numbers
        ])

    _install_chat(monkeypatch, handler)
    found = run_async(verify_adequacy(None, batches, results, cfg))

    assert list(found) == [0]
    flag = found[0][0]
    assert flag.block == 1
    assert flag.problem == (
        "block 1: the translation leaves out part of what the source says")
    assert "recovered only 0% of the source wording" in flag.message


def test_a_cue_too_short_to_score_fairly_is_not_scored(monkeypatch, cfg) -> None:
    """A three-word cue that comes back with one synonym scores 33%: not
    evidence of anything, so lines that short are left alone."""
    cfg.verify_adequacy = True
    batches = split_batches(make_blocks(4), 2)  # "line 1" .. "line 4"
    results = [BatchResult(list(b)) for b in batches]
    _install_chat(monkeypatch, lambda user, n: serialize_lite([
        SubtitleBlock(int(line), "", "completely unrelated words")
        for line in user.splitlines() if line.strip().isdigit()]))

    assert run_async(verify_adequacy(None, batches, results, cfg)) == {}


def test_a_block_the_round_trip_dropped_is_not_flagged(monkeypatch, cfg) -> None:
    # A short reply costs us an opinion on those cues, not a false accusation.
    cfg.verify_adequacy = True
    batches = split_batches(make_blocks(4), 2)
    results = [BatchResult(list(b)) for b in batches]
    _install_chat(monkeypatch, lambda user, n: "1\nline 1")

    assert run_async(verify_adequacy(None, batches, results, cfg)) == {}


def test_a_failed_back_translation_never_fails_the_file(monkeypatch, cfg) -> None:
    cfg.verify_adequacy = True
    warnings: list[str] = []
    cfg.warn = warnings.append
    batches = split_batches(make_blocks(4), 2)
    results = [BatchResult(list(b)) for b in batches]

    def boom(user_message: str, n: int) -> str:
        raise RuntimeError("network down")

    _install_chat(monkeypatch, boom)
    assert run_async(verify_adequacy(None, batches, results, cfg)) == {}
    assert any("Adequacy check failed for 2 batch(es)" in w for w in warnings)


def test_nothing_is_sampled_when_there_are_no_batches(monkeypatch, cfg) -> None:
    calls = _install_chat(monkeypatch, lambda user, n: "")
    assert run_async(verify_adequacy(None, [], [], cfg)) == {}
    assert calls == []
