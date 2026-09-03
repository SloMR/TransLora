"""Per-file orchestration: decoding, failure reporting, and the dry-run plan."""

from __future__ import annotations

import pytest

import core.adequacy as ad
import core.batch_runner as br
import core.context_scan as cs
from core.batch_runner import FileTranslationError
from core.prompt import (
    BACK_TRANSLATION_SYSTEM_PROMPT,
    CONTEXT_SYSTEM_PROMPT,
    FIX_FLAGGED_RULE,
)
from core.srt_parser import SubtitleBlock, parse_lite, serialize_lite
from core.translator import (
    FALLBACK_ENCODINGS,
    plan_file,
    read_subtitle_text,
    translate_file_async,
)
from tests.conftest import run_async

SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\nWorld\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\nAgain\n\n"
    "4\n00:00:07,000 --> 00:00:08,000\nAnd again\n"
)


# === Decoding ================================================================


def test_reads_utf8_with_bom(tmp_path) -> None:
    path = tmp_path / "a.srt"
    path.write_bytes("﻿1\n00:00:01,000 --> 00:00:02,000\nHi\n".encode())
    text, encoding = read_subtitle_text(path)
    assert encoding == "utf-8-sig"
    assert not text.startswith("﻿")


def test_cp1256_file_is_read_with_an_explicit_encoding(tmp_path) -> None:
    body = "1\n00:00:01,000 --> 00:00:02,000\nمرحبا\n"
    path = tmp_path / "arabic.srt"
    path.write_bytes(body.encode("cp1256"))
    text, encoding = read_subtitle_text(path, "cp1256")
    assert encoding == "cp1256"
    assert text == body


def test_cp1256_file_does_not_crash_on_auto(tmp_path) -> None:
    # No detector: auto falls through to the codepage list, and cp1252 decodes
    # almost anything. The contract is only that it never raises — pass
    # --encoding when the file's codepage is known.
    path = tmp_path / "arabic.srt"
    path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nمرحبا\n".encode("cp1256"))
    text, encoding = read_subtitle_text(path, "auto")
    assert encoding in FALLBACK_ENCODINGS
    assert "00:00:01,000" in text


def test_missing_file_raises_file_translation_error(tmp_path) -> None:
    with pytest.raises(FileTranslationError, match="could not read"):
        read_subtitle_text(tmp_path / "nope.srt")


# === Failure reporting =======================================================


def test_zero_block_file_is_a_failure(tmp_path, cfg) -> None:
    src = tmp_path / "prose.srt"
    src.write_text("this file has no cues at all\n", encoding="utf-8")
    with pytest.raises(FileTranslationError, match="no subtitle blocks"):
        run_async(translate_file_async(src, tmp_path / "out.srt", cfg))


def test_unparseable_file_is_a_failure(tmp_path, cfg) -> None:
    src = tmp_path / "movie.xyz"
    src.write_text("irrelevant\n", encoding="utf-8")
    with pytest.raises(FileTranslationError, match="Unsupported subtitle format"):
        run_async(translate_file_async(src, tmp_path / "out.xyz", cfg))


def test_output_is_checked_before_any_api_call(tmp_path, cfg) -> None:
    src = tmp_path / "a.srt"
    src.write_text(SRT, encoding="utf-8")
    missing = tmp_path / "nodir" / "a.ar.srt"
    with pytest.raises(FileTranslationError, match="output directory does not exist"):
        run_async(translate_file_async(src, missing, cfg))


def test_symlinked_output_is_refused(tmp_path, cfg) -> None:
    src = tmp_path / "a.srt"
    src.write_text(SRT, encoding="utf-8")
    target = tmp_path / "important.txt"
    target.write_text("do not touch", encoding="utf-8")
    link = tmp_path / "a.ar.srt"
    link.symlink_to(target)

    with pytest.raises(FileTranslationError, match="symlink"):
        run_async(translate_file_async(src, link, cfg))
    assert target.read_text(encoding="utf-8") == "do not touch"


# === Dry-run pricing =========================================================


def test_plan_file_counts_batches_and_calls_without_calling_the_api(
    tmp_path, cfg,
) -> None:
    src = tmp_path / "a.srt"
    src.write_text(SRT, encoding="utf-8")
    cfg.batch_size = 3
    cfg.review = True

    plan = plan_file(src, tmp_path / "a.ar.srt", cfg)

    assert (plan.blocks, plan.batches, plan.reused) == (4, 2, 0)
    assert plan.scan_calls == 1
    assert plan.translate_calls == 2
    assert plan.review_calls == 2
    # Only the scan can know how many scenes need attribution.
    assert plan.attribution_calls is None
    assert plan.known_calls == 3


def test_plan_file_knows_attribution_is_off(tmp_path, cfg) -> None:
    src = tmp_path / "a.srt"
    src.write_text(SRT, encoding="utf-8")
    cfg.refine_attribution = False
    assert plan_file(src, tmp_path / "a.ar.srt", cfg).attribution_calls == 0


# === File-level orthography ==================================================

PLAIN_AR = "مرحبا بك"
VOCALIZED_AR = "مَرْحَبًا بِكَ"


def _install_chat(monkeypatch, reply) -> None:
    """Answer both the prepass scan and every batch without a network."""
    async def fake_call(client, system_prompt, user_message, cfg, max_tokens):
        return reply(system_prompt, user_message)

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    monkeypatch.setattr(cs, "call_chat_api", fake_call)


def _vocalize_block(number: int):
    def reply(system_prompt: str, user_message: str) -> str:
        if system_prompt == CONTEXT_SYSTEM_PROMPT:
            return ""
        return serialize_lite([
            SubtitleBlock(b.number, "",
                          VOCALIZED_AR if b.number == number else PLAIN_AR)
            for b in parse_lite(user_message)
        ])

    return reply


def test_a_vocalized_tail_is_stripped_before_the_file_is_written(
    tmp_path, cfg, monkeypatch,
) -> None:
    # Real runs drift into full vocalisation near the end of an episode; the
    # baseline is file-wide, so no single batch could have caught it.
    src = tmp_path / "a.srt"
    src.write_text(SRT, encoding="utf-8")
    output = tmp_path / "a.ar.srt"
    warnings: list[str] = []
    cfg.warn = warnings.append
    cfg.batch_size = 2
    _install_chat(monkeypatch, _vocalize_block(4))

    run_async(translate_file_async(src, output, cfg))

    written = output.read_text(encoding="utf-8")
    assert VOCALIZED_AR not in written
    assert written.count(PLAIN_AR) == 4
    assert any("Removed diacritics from 1 cue(s)" in w for w in warnings)


def test_a_latin_target_is_never_touched_by_the_orthography_pass(
    tmp_path, cfg, monkeypatch,
) -> None:
    cfg.target_lang = "Spanish"
    src = tmp_path / "a.srt"
    src.write_text(SRT, encoding="utf-8")
    output = tmp_path / "a.es.srt"
    _install_chat(monkeypatch, _vocalize_block(4))

    run_async(translate_file_async(src, output, cfg))

    assert VOCALIZED_AR in output.read_text(encoding="utf-8")


# The graded run where gpt-5-mini returned 211 of 372 cues in Egyptian against
# a Modern Standard Arabic target, fluent and unusable, and nothing noticed.
EGYPTIAN_AR = "مش عارف إزاي"


def _colloquial(system_prompt: str, user_message: str) -> str:
    if system_prompt == CONTEXT_SYSTEM_PROMPT:
        return ""
    return serialize_lite([SubtitleBlock(b.number, "", EGYPTIAN_AR)
                           for b in parse_lite(user_message)])


def _run_colloquial(tmp_path, cfg, monkeypatch) -> list[str]:
    src = tmp_path / "a.srt"
    src.write_text(SRT, encoding="utf-8")
    warnings: list[str] = []
    cfg.warn = warnings.append
    _install_chat(monkeypatch, _colloquial)
    run_async(translate_file_async(src, tmp_path / "a.ar.srt", cfg))
    return warnings


def test_a_file_that_came_back_in_the_wrong_variant_is_reported(
    tmp_path, cfg, monkeypatch,
) -> None:
    warnings = _run_colloquial(tmp_path, cfg, monkeypatch)
    assert any("Output looks like Egyptian rather than the standard written "
               "form (4 of 4 cues)" in w for w in warnings)


def test_the_variant_a_user_asked_for_is_not_reported(
    tmp_path, cfg, monkeypatch,
) -> None:
    cfg.dialect = "Egyptian Arabic"
    assert not any("Output looks like" in w
                   for w in _run_colloquial(tmp_path, cfg, monkeypatch))


def test_the_variant_check_never_rewrites_the_file(
    tmp_path, cfg, monkeypatch,
) -> None:
    """A warning, never a repair: which variant to write is the user's call."""
    _run_colloquial(tmp_path, cfg, monkeypatch)
    assert (tmp_path / "a.ar.srt").read_text(
        encoding="utf-8").count(EGYPTIAN_AR) == 4


# === Flagged-batch repair ====================================================

# The italics sit inside the cue, not around it: a wrapping pair is restored
# deterministically, so only this shape reaches the flag.
TAGGED_SRT = "".join(
    f"{n}\n00:00:{n:02d},000 --> 00:00:{n + 1:02d},000\n"
    f"Line {{\\i1}}{n}{{\\i0}} here\n\n"
    for n in range(1, 7)
)


def _install_recording_chat(monkeypatch, reply) -> list[tuple[str, str]]:
    """Answer every pass — scan, translate, repair, back-translation — and
    record what each one was asked."""
    calls: list[tuple[str, str]] = []

    async def fake_call(client, system_prompt, user_message, cfg, max_tokens):
        calls.append((system_prompt, user_message))
        return reply(system_prompt, user_message)

    monkeypatch.setattr(br, "call_chat_api", fake_call)
    monkeypatch.setattr(cs, "call_chat_api", fake_call)
    monkeypatch.setattr(ad, "call_chat_api", fake_call)
    return calls


def _drops_tags(fix: bool = True):
    """A model that loses the italics, and puts them back only when the retry
    tells it to."""
    def reply(system_prompt: str, user_message: str) -> str:
        if system_prompt == CONTEXT_SYSTEM_PROMPT:
            return ""
        repairing = fix and user_message.startswith(FIX_FLAGGED_RULE)
        return serialize_lite([
            SubtitleBlock(
                b.number, "",
                f"سطر {{\\i1}}{b.number}{{\\i0}} هنا" if repairing
                else f"سطر {b.number} هنا")
            for b in parse_lite(user_message)
        ])
    return reply


# Six cues long enough for the adequacy check to score (it skips anything
# under five content words) and unlike each other, so the consistency check
# has no repeated phrase to weigh in on.
LONG_SRT = "".join(
    f"{n}\n00:00:{n:02d},000 --> 00:00:{n + 1:02d},000\n{text}\n\n"
    for n, text in enumerate([
        "the harbour master signs the log book at eleven tonight",
        "a stranger left three parcels on the kitchen table",
        "nobody told the driver where the road actually ends",
        "her brother keeps the garden keys under the loose brick",
        "the choir rehearses in the old chapel every second thursday",
        "somebody painted the lighthouse door a different green",
    ], start=1)
)


def _fixes_only_alone():
    """A model that keeps dropping the italics however the batch is re-issued,
    and puts them back only when a cue is sent on its own."""
    def reply(system_prompt: str, user_message: str) -> str:
        if system_prompt == CONTEXT_SYSTEM_PROMPT:
            return ""
        blocks = parse_lite(user_message)
        alone = user_message.startswith(FIX_FLAGGED_RULE) and len(blocks) == 1
        return serialize_lite([
            SubtitleBlock(
                b.number, "",
                f"سطر {{\\i1}}{b.number}{{\\i0}} هنا" if alone
                else f"سطر {b.number} هنا")
            for b in blocks
        ])
    return reply


def _run_tagged(tmp_path, cfg, monkeypatch, reply, srt: str = TAGGED_SRT):
    src = tmp_path / "a.srt"
    src.write_text(srt, encoding="utf-8")
    output = tmp_path / "a.ar.srt"
    calls = _install_recording_chat(monkeypatch, reply)
    run_async(translate_file_async(src, output, cfg))
    return output.read_text(encoding="utf-8"), calls


def test_a_flagged_batch_is_re_issued_and_the_fix_is_kept(
    tmp_path, cfg, monkeypatch,
) -> None:
    cfg.batch_size = 6
    written, calls = _run_tagged(tmp_path, cfg, monkeypatch, _drops_tags())

    # The .srt writer renders the wire's override tags back as HTML italics.
    assert written.count("<i>") == 6
    assert cfg.calls.repair == 1
    assert sum(1 for sys_p, user in calls
               if user.startswith(FIX_FLAGGED_RULE)) == 1


def test_a_retry_that_does_not_help_leaves_the_file_alone(
    tmp_path, cfg, monkeypatch,
) -> None:
    cfg.batch_size = 6
    written, _ = _run_tagged(tmp_path, cfg, monkeypatch, _drops_tags(fix=False))

    # One call was spent; the first translation is kept because it is no worse.
    assert "<i>" not in written
    assert written.count("سطر") == 6
    assert cfg.calls.repair == 1


def test_the_repair_is_capped_and_says_what_it_skipped(
    tmp_path, cfg, monkeypatch,
) -> None:
    # Six one-block batches, all flagged on the same cause; a quarter of six
    # rounds up to two, which is also the floor.
    cfg.batch_size = 1
    warnings: list[str] = []
    cfg.warn = warnings.append
    _run_tagged(tmp_path, cfg, monkeypatch, _drops_tags())

    assert cfg.calls.repair == 2
    assert any("6 flagged batch(es) across 1 cause(s); repairing 2 (cap 2), "
               "leaving 4" in w for w in warnings)
    assert any("Repaired 2/2 flagged batch(es)" in w for w in warnings)


def test_no_fix_flagged_spends_nothing_on_repair(
    tmp_path, cfg, monkeypatch,
) -> None:
    cfg.batch_size = 6
    cfg.fix_flagged = False
    written, _ = _run_tagged(tmp_path, cfg, monkeypatch, _drops_tags())

    assert "<i>" not in written
    assert cfg.calls.repair == 0


def test_a_systematic_failure_buys_more_than_the_five_percent_cap(
    tmp_path, cfg, monkeypatch,
) -> None:
    """The graded run flagged 16 of 38 batches on one cause and repaired 2.
    One term drifting across the file is one problem, and 5% cannot answer it."""
    cfg.batch_size = 1
    src = tmp_path / "long.srt"
    src.write_text("".join(
        f"{n}\n00:00:{n:02d},000 --> 00:00:{n + 1:02d},000\n"
        f"Line {{\\i1}}{n}{{\\i0}} here\n\n" for n in range(1, 21)
    ), encoding="utf-8")
    warnings: list[str] = []
    cfg.warn = warnings.append
    _install_recording_chat(monkeypatch, _drops_tags())
    run_async(translate_file_async(src, tmp_path / "long.ar.srt", cfg))

    # Twenty one-block batches, all flagged on "tags": a quarter of the file.
    assert cfg.calls.repair == 5
    assert any("20 flagged batch(es) across 1 cause(s); repairing 5 (cap 5), "
               "leaving 15" in w for w in warnings)


def test_a_clean_run_never_pays_for_a_repair(tmp_path, cfg, monkeypatch) -> None:
    src = tmp_path / "a.srt"
    src.write_text(SRT, encoding="utf-8")
    _install_recording_chat(monkeypatch, _vocalize_block(0))
    run_async(translate_file_async(src, tmp_path / "a.ar.srt", cfg))
    assert cfg.calls.repair == 0


# === Adequacy plumbing =======================================================


def test_verify_adequacy_feeds_the_repair_pass(tmp_path, cfg, monkeypatch) -> None:
    cfg.batch_size = 3
    cfg.verify_adequacy = True

    def reply(system_prompt: str, user_message: str) -> str:
        if system_prompt == CONTEXT_SYSTEM_PROMPT:
            return ""
        if system_prompt == BACK_TRANSLATION_SYSTEM_PROMPT:
            # Nothing of the source survives the round trip.
            return serialize_lite([
                SubtitleBlock(b.number, "", "unrelated words")
                for b in parse_lite(user_message)
            ])
        return serialize_lite([
            SubtitleBlock(b.number, "", f"سطر {b.number} هنا")
            for b in parse_lite(user_message)
        ])

    _, calls = _run_tagged(tmp_path, cfg, monkeypatch, reply, srt=LONG_SRT)

    assert cfg.calls.back_translation == 2
    # Both batches came back inadequate, and both fit under the cap.
    assert cfg.calls.repair == 2
    problems = [user for sys_p, user in calls if user.startswith(FIX_FLAGGED_RULE)]
    assert all("leaves out part of what the source says" in p for p in problems)


def test_verify_adequacy_needs_a_source_language(tmp_path, cfg, monkeypatch) -> None:
    cfg.source_lang = ""
    cfg.verify_adequacy = True
    cfg.batch_size = 6
    warnings: list[str] = []
    cfg.warn = warnings.append
    _run_tagged(tmp_path, cfg, monkeypatch, _drops_tags())

    assert cfg.calls.back_translation == 0
    assert any("Adequacy check skipped" in w for w in warnings)


# === Throughput reporting ====================================================


def test_the_completion_summary_reports_the_calls_the_file_made(
    tmp_path, cfg, monkeypatch, capsys,
) -> None:
    cfg.quiet = False
    cfg.batch_size = 6
    _run_tagged(tmp_path, cfg, monkeypatch, _drops_tags())

    out = capsys.readouterr().out
    assert "LLM calls: 3 (1 scan, 1 translate, 1 repair)" in out
    assert "blocks/s" in out


def test_the_summary_counts_only_this_file(tmp_path, cfg, monkeypatch, capsys) -> None:
    # One config can translate a whole folder; the per-file line must not
    # report the calls an earlier file made.
    cfg.quiet = False
    cfg.batch_size = 6
    cfg.calls.count("translate", 99)
    _run_tagged(tmp_path, cfg, monkeypatch, _drops_tags())

    assert "LLM calls: 3 (" in capsys.readouterr().out
    assert cfg.calls.translate == 100


# === File-level phrase consistency ===========================================

# The graded run alternated between two renderings of the episode's central
# motif across seven cues in five different batches. No batch could see it: a
# batch is a tenth of the file, and the drift report only knows the targets the
# glossary actually pinned.

# Different words either side of the motif, so it is the only phrase the file
# repeats.
_MOTIF_SRT = "".join(
    f"{n}\n00:00:{n:02d},000 --> 00:00:{n + 1:02d},000\n{opener} "
    f"crosses the line {closer}.\n\n"
    for n, (opener, closer) in enumerate(
        [("Nadia says he", "today"), ("Omar asks if she", "downstairs"),
         ("Priya replies that Karim", "quietly"), ("Lena writes that Rafi", "twice"),
         ("Tomas notes that someone", "again"), ("Yara insists he", "afterwards")],
        start=1)
)

_SPLIT = ("يتجاوز الحد", "يتخطى الخط", "يعبر الحدود",
          "يتعدى المسموح", "يخرق القاعدة", "يفوق المألوف")
_SETTLED = tuple(f"{verb} الخط المرسوم"
                 for verb in ("يتجاوز", "يتخطى", "يعبر", "يتعدى", "يخرق", "يفوق"))


def _renders(texts: tuple[str, ...]):
    def reply(system_prompt: str, user_message: str) -> str:
        if system_prompt == CONTEXT_SYSTEM_PROMPT:
            return ""
        return serialize_lite([
            SubtitleBlock(b.number, "", texts[b.number - 1])
            for b in parse_lite(user_message)
        ])
    return reply


def _run_motif(tmp_path, cfg, monkeypatch, texts):
    src = tmp_path / "motif.srt"
    src.write_text(_MOTIF_SRT, encoding="utf-8")
    warnings: list[str] = []
    cfg.warn = warnings.append
    cfg.batch_size = 2
    calls = _install_recording_chat(monkeypatch, _renders(texts))
    run_async(translate_file_async(src, tmp_path / "motif.ar.srt", cfg))
    return warnings, calls


def test_a_phrase_the_file_never_settled_on_is_reported(
    tmp_path, cfg, monkeypatch,
) -> None:
    warnings, _ = _run_motif(tmp_path, cfg, monkeypatch, _SPLIT)
    assert sum("crosses the line" in w for w in warnings) == 1
    assert any("'crosses the line' is rendered 6 different ways across 6 "
               "cues; no wording is shared by all of them" in w
               for w in warnings)


def test_the_split_phrase_is_one_cause_the_repair_can_budget_for(
    tmp_path, cfg, monkeypatch,
) -> None:
    # All three batches carry the phrase, so all three are flagged on one
    # cause; three batches is under the systematic threshold, so the ordinary
    # floor of two applies and the third is reported rather than dropped.
    warnings, calls = _run_motif(tmp_path, cfg, monkeypatch, _SPLIT)
    assert cfg.calls.repair == 2
    assert any("3 flagged batch(es) across 1 cause(s); repairing 2 (cap 2), "
               "leaving 1" in w for w in warnings)
    retries = [user for _, user in calls if user.startswith(FIX_FLAGGED_RULE)]
    assert len(retries) == 2
    assert all("the recurring phrase 'crosses the line' is rendered "
               "differently elsewhere in the file; use one wording for it"
               in user for user in retries)


def test_a_file_that_settles_on_one_rendering_pays_nothing(
    tmp_path, cfg, monkeypatch,
) -> None:
    # Every cue keeps "الخط" — the wording they share is what makes it one
    # rendering, however differently the rest of the cue reads.
    warnings, _ = _run_motif(tmp_path, cfg, monkeypatch, _SETTLED)
    assert not any("is rendered" in w for w in warnings)
    assert cfg.calls.repair == 0
