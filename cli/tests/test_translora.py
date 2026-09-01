"""CLI driver: file collection, job planning, argument validation, the quality
flags' plumbing into TranslationConfig, and exit codes."""

from __future__ import annotations

import sys
from collections.abc import Collection
from pathlib import Path

import pytest
import translora
from translora import RunTotals, _collect_files, _plan_jobs, _run

from core.batch_runner import FileTranslationError
from core.cli_args import EXIT_FAILURE, EXIT_OK, build_parser
from core.constants import (
    DEFAULT_DIALECT,
    DEFAULT_FIX_FLAGGED,
    DEFAULT_FORMALITY,
    DEFAULT_FULL_ATTRIBUTION,
    DEFAULT_REFLOW,
    DEFAULT_SEND_TEMPERATURE,
    DEFAULT_VERIFY_ADEQUACY,
    TOKEN_PARAM_COMPLETION,
)
from tests.conftest import run_async

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"


def _args(*argv: str):
    return build_parser(translora.__version__).parse_args(list(argv))


def _write(path: Path, text: str = SRT) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# === File collection =========================================================


def test_collect_files_expands_a_directory_sorted_and_filtered(tmp_path, capsys) -> None:
    _write(tmp_path / "b.srt")
    _write(tmp_path / "a.vtt")
    _write(tmp_path / "notes.txt")
    (tmp_path / "nested").mkdir()

    found = _collect_files([tmp_path], "ar")

    assert [f.name for f in found] == ["a.vtt", "b.srt"]


def test_collect_files_skips_previously_translated_output(tmp_path, capsys) -> None:
    _write(tmp_path / "movie.srt")
    _write(tmp_path / "movie.ar.srt")

    found = _collect_files([tmp_path], "ar")

    assert [f.name for f in found] == ["movie.srt"]
    assert "Skipping 1 previously translated" in capsys.readouterr().out


def test_collect_files_keeps_another_targets_output(tmp_path) -> None:
    _write(tmp_path / "movie.srt")
    _write(tmp_path / "movie.fr.srt")
    assert [f.name for f in _collect_files([tmp_path], "ar")] == [
        "movie.fr.srt", "movie.srt"]


def test_collect_files_honours_an_explicit_translated_file(tmp_path) -> None:
    # The directory filter is a convenience; naming the file is an instruction.
    path = _write(tmp_path / "movie.ar.srt")
    assert _collect_files([path], "ar") == [path]


def test_collect_files_reports_unusable_paths(tmp_path, capsys) -> None:
    _write(tmp_path / "notes.txt")
    found = _collect_files([tmp_path / "notes.txt", tmp_path / "ghost.srt"], "ar")
    err = capsys.readouterr().err
    assert found == []
    assert "Skipping non-subtitle file" in err
    assert "Not found" in err


# === Job planning ============================================================


def test_plan_jobs_derives_the_output_name_from_the_target(tmp_path) -> None:
    srt = _write(tmp_path / "movie.srt")
    vtt = _write(tmp_path / "movie.vtt")
    args = _args(str(srt), "-t", "Arabic", "--api-url", "http://x")

    jobs, skipped, refused = _plan_jobs(args, [srt, vtt])

    assert [j.output_path.name for j in jobs] == ["movie.ar.srt", "movie.ar.vtt"]
    assert (skipped, refused) == (0, [])


def test_plan_jobs_skips_an_existing_output_unless_forced(tmp_path) -> None:
    srt = _write(tmp_path / "movie.srt")
    _write(tmp_path / "movie.ar.srt")

    args = _args(str(srt), "-t", "Arabic", "--api-url", "http://x")
    jobs, skipped, _ = _plan_jobs(args, [srt])
    assert jobs == [] and skipped == 1

    forced = _args(str(srt), "-t", "Arabic", "--api-url", "http://x", "--force")
    jobs, skipped, _ = _plan_jobs(forced, [srt])
    assert len(jobs) == 1 and skipped == 0


def test_plan_jobs_refuses_an_output_that_is_another_input(tmp_path) -> None:
    source = _write(tmp_path / "movie.srt")
    already = _write(tmp_path / "movie.ar.srt")

    args = _args(str(source), str(already), "-t", "Arabic",
                 "--api-url", "http://x", "--force")
    jobs, _, refused = _plan_jobs(args, [source, already])

    assert [j.input_path for j in jobs] == [already]
    assert len(refused) == 1
    assert "is another input file" in refused[0]


def test_plan_jobs_refuses_a_symlinked_output(tmp_path) -> None:
    source = _write(tmp_path / "movie.srt")
    secret = _write(tmp_path / "secret.txt", "keep me")
    (tmp_path / "movie.ar.srt").symlink_to(secret)

    args = _args(str(source), "-t", "Arabic", "--api-url", "http://x", "--force")
    jobs, _, refused = _plan_jobs(args, [source])

    assert jobs == []
    assert "output is a symlink" in refused[0]
    assert secret.read_text(encoding="utf-8") == "keep me"


def test_plan_jobs_honours_an_explicit_output(tmp_path) -> None:
    srt = _write(tmp_path / "movie.srt")
    out = tmp_path / "custom.srt"
    args = _args(str(srt), "-t", "Arabic", "--api-url", "http://x",
                 "-o", str(out))
    jobs, _, _ = _plan_jobs(args, [srt])
    assert jobs[0].output_path == out


# === Argument validation =====================================================


@pytest.mark.parametrize("flag", [
    "--batch-size", "--concurrency", "--parallel-files", "--max-retries",
    "--scan-budget",
])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_count_flags_reject_zero_and_negatives(flag: str, value: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _args("a.srt", "-t", "Arabic", "--api-url", "http://x", flag, value)
    assert "must be >= 1" in str(excinfo.value)


def test_context_overlap_allows_zero_but_not_negative() -> None:
    assert _args("a.srt", "-t", "Arabic", "--api-url", "http://x",
                 "--context-overlap", "0").context_overlap == 0
    with pytest.raises(SystemExit) as excinfo:
        _args("a.srt", "-t", "Arabic", "--api-url", "http://x",
              "--context-overlap", "-2")
    assert "must be >= 0" in str(excinfo.value)


@pytest.mark.parametrize("value", ["0", "-5", "abc"])
def test_timeout_must_be_positive(value: str) -> None:
    with pytest.raises(SystemExit):
        _args("a.srt", "-t", "Arabic", "--api-url", "http://x", "--timeout", value)


def test_encoding_must_be_a_known_codec() -> None:
    assert _args("a.srt", "-t", "Arabic", "--api-url", "http://x",
                 "--encoding", "cp1256").encoding == "cp1256"
    with pytest.raises(SystemExit) as excinfo:
        _args("a.srt", "-t", "Arabic", "--api-url", "http://x",
              "--encoding", "klingon-8")
    assert "unknown encoding" in str(excinfo.value)


def test_target_must_not_be_blank() -> None:
    # A blank target would translate "to nothing" and write `movie..srt`.
    with pytest.raises(SystemExit) as excinfo:
        _args("a.srt", "-t", "   ", "--api-url", "http://x")
    assert "must not be empty" in str(excinfo.value)


def test_missing_required_arguments_exit_non_zero() -> None:
    with pytest.raises(SystemExit):
        _args("a.srt")


# === Exit codes ==============================================================


def _fake_translation(monkeypatch, failing: Collection[str] = ()):
    """Replace the per-file translator; records the config it was handed."""
    seen: list = []

    async def fake(input_path, output_path, cfg):
        seen.append((input_path.name, cfg))
        if input_path.name in failing:
            raise FileTranslationError("batch 1 failed")
        output_path.write_text("translated\n", encoding="utf-8")

    monkeypatch.setattr(translora, "translate_file_async", fake)
    return seen


def test_run_exits_zero_when_every_file_succeeds(tmp_path, monkeypatch, capsys) -> None:
    srt = _write(tmp_path / "movie.srt")
    _fake_translation(monkeypatch)
    args = _args(str(srt), "-t", "Arabic", "--api-url", "http://x")

    assert run_async(_run(args, RunTotals())) == EXIT_OK
    assert (tmp_path / "movie.ar.srt").read_text(encoding="utf-8") == "translated\n"


def test_run_exits_one_when_a_file_fails_and_finishes_the_others(
    tmp_path, monkeypatch, capsys,
) -> None:
    good = _write(tmp_path / "good.srt")
    bad = _write(tmp_path / "bad.srt")
    _fake_translation(monkeypatch, failing={"bad.srt"})
    args = _args(str(good), str(bad), "-t", "Arabic", "--api-url", "http://x")
    totals = RunTotals()

    assert run_async(_run(args, totals)) == EXIT_FAILURE
    assert totals.completed == 1
    assert [p.name for p, _ in totals.failed] == ["bad.srt"]
    # The healthy file was still translated.
    assert (tmp_path / "good.ar.srt").exists()


def test_run_exits_one_when_there_is_nothing_to_translate(tmp_path, capsys) -> None:
    args = _args(str(tmp_path / "ghost.srt"), "-t", "Arabic", "--api-url", "http://x")
    assert run_async(_run(args, RunTotals())) == EXIT_FAILURE


def test_run_rejects_output_and_glossary_out_with_many_files(tmp_path) -> None:
    a = _write(tmp_path / "a.srt")
    b = _write(tmp_path / "b.srt")
    common = ["-t", "Arabic", "--api-url", "http://x"]

    args = _args(str(a), str(b), *common, "-o", str(tmp_path / "out.srt"))
    assert run_async(_run(args, RunTotals())) == EXIT_FAILURE

    args = _args(str(a), str(b), *common, "--glossary-out", str(tmp_path / "g.json"))
    assert run_async(_run(args, RunTotals())) == EXIT_FAILURE


def test_dry_run_makes_no_api_calls_and_reports_the_plan(
    tmp_path, monkeypatch, capsys,
) -> None:
    srt = _write(tmp_path / "movie.srt")
    seen = _fake_translation(monkeypatch)
    args = _args(str(srt), "-t", "Arabic", "--api-url", "http://x", "--dry-run")

    assert run_async(_run(args, RunTotals())) == EXIT_OK
    assert seen == []
    out = capsys.readouterr().out
    assert "Dry run" in out and "LLM calls" in out
    assert not (tmp_path / "movie.ar.srt").exists()


def test_main_exits_one_on_failure_and_zero_on_success(
    tmp_path, monkeypatch,
) -> None:
    srt = _write(tmp_path / "movie.srt")
    _fake_translation(monkeypatch, failing={"movie.srt"})
    monkeypatch.setattr(sys, "argv", [
        "translora.py", str(srt), "-t", "Arabic", "--api-url", "http://x"])

    with pytest.raises(SystemExit) as excinfo:
        translora.main()
    assert excinfo.value.code == EXIT_FAILURE

    _fake_translation(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        translora.main()
    assert excinfo.value.code == EXIT_OK


def test_api_key_falls_back_to_the_environment(tmp_path, monkeypatch) -> None:
    srt = _write(tmp_path / "movie.srt")
    seen = _fake_translation(monkeypatch)
    monkeypatch.setenv(translora.API_KEY_ENV, "sk-from-env")
    monkeypatch.setattr(sys, "argv", [
        "translora.py", str(srt), "-t", "Arabic", "--api-url", "http://x"])

    with pytest.raises(SystemExit):
        translora.main()
    assert seen[0][1].api_key == "sk-from-env"


def test_explicit_api_key_wins_over_the_environment(tmp_path, monkeypatch) -> None:
    srt = _write(tmp_path / "movie.srt")
    seen = _fake_translation(monkeypatch)
    monkeypatch.setenv(translora.API_KEY_ENV, "sk-from-env")
    monkeypatch.setattr(sys, "argv", [
        "translora.py", str(srt), "-t", "Arabic", "--api-url", "http://x",
        "--api-key", "sk-explicit"])

    with pytest.raises(SystemExit):
        translora.main()
    assert seen[0][1].api_key == "sk-explicit"


# === Quality flags ===========================================================


def _cfg(*argv: str):
    return translora._build_config(
        _args("a.srt", "-t", "Arabic", "--api-url", "http://x", *argv),
        multi_file=False,
    )


def test_quality_flags_default_to_the_shared_constants() -> None:
    cfg = _cfg()
    assert cfg.formality == DEFAULT_FORMALITY
    assert cfg.dialect == DEFAULT_DIALECT
    assert cfg.reflow == DEFAULT_REFLOW
    assert cfg.max_line_chars is None
    assert cfg.fix_flagged == DEFAULT_FIX_FLAGGED is True
    assert cfg.verify_adequacy == DEFAULT_VERIFY_ADEQUACY is False
    assert cfg.full_attribution == DEFAULT_FULL_ATTRIBUTION is False


@pytest.mark.parametrize("formality", ["auto", "formal", "informal"])
def test_formality_reaches_the_config(formality: str) -> None:
    assert _cfg("--formality", formality).formality == formality


def test_an_unknown_formality_is_rejected() -> None:
    with pytest.raises(SystemExit):
        _args("a.srt", "-t", "Arabic", "--api-url", "http://x",
              "--formality", "chummy")


def test_a_dialect_reaches_the_config_as_free_text() -> None:
    assert _cfg("--dialect", "Egyptian Arabic").dialect == "Egyptian Arabic"


def test_a_blank_dialect_is_rejected_rather_than_silently_ignored() -> None:
    with pytest.raises(SystemExit) as excinfo:
        _args("a.srt", "-t", "Arabic", "--api-url", "http://x", "--dialect", "  ")
    assert "must not be empty" in str(excinfo.value)


def test_max_line_chars_reaches_the_config() -> None:
    assert _cfg("--max-line-chars", "38").max_line_chars == 38


@pytest.mark.parametrize("value", ["0", "-1"])
def test_max_line_chars_rejects_zero_and_negatives(value: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _args("a.srt", "-t", "Arabic", "--api-url", "http://x",
              "--max-line-chars", value)
    assert "must be >= 1" in str(excinfo.value)


def test_no_reflow_turns_the_deterministic_pass_off() -> None:
    assert _cfg("--no-reflow").reflow is False


@pytest.mark.parametrize("target,expected", [
    ("Arabic", "script arabic | 42 chars/line | max 2 lines | ~20 chars/sec | RTL"),
    ("Japanese", "script japanese | 16 chars/line | max 2 lines | ~4 chars/sec"),
    ("Korean", "script korean | 20 chars/line | max 2 lines | ~12 chars/sec"),
    ("Klingon", "script default | 42 chars/line | max 2 lines | ~17 chars/sec"),
])
def test_dry_run_reports_the_norms_it_will_enforce(
    tmp_path, monkeypatch, capsys, target: str, expected: str,
) -> None:
    srt = _write(tmp_path / "movie.srt")
    _fake_translation(monkeypatch)
    args = _args(str(srt), "-t", target, "--api-url", "http://x", "--dry-run")

    assert run_async(_run(args, RunTotals())) == EXIT_OK
    out = capsys.readouterr().out
    assert expected in out
    assert "reflow on" in out


def test_dry_run_marks_an_overridden_line_length_and_a_disabled_reflow(
    tmp_path, monkeypatch, capsys,
) -> None:
    srt = _write(tmp_path / "movie.srt")
    _fake_translation(monkeypatch)
    args = _args(str(srt), "-t", "Arabic", "--api-url", "http://x",
                 "--dry-run", "--max-line-chars", "38", "--no-reflow")

    assert run_async(_run(args, RunTotals())) == EXIT_OK
    out = capsys.readouterr().out
    assert "38 chars/line (overridden)" in out
    assert "reflow off" in out


def test_no_fix_flagged_turns_the_focused_retry_off() -> None:
    assert _cfg("--no-fix-flagged").fix_flagged is False


def test_verify_adequacy_is_opt_in() -> None:
    assert _cfg("--verify-adequacy").verify_adequacy is True


def test_full_attribution_is_opt_in() -> None:
    assert _cfg("--full-attribution").full_attribution is True


# === Provider dialect ========================================================


def test_a_temperature_is_sent_unless_the_run_says_otherwise() -> None:
    assert _cfg().send_temperature is DEFAULT_SEND_TEMPERATURE is True


def test_no_temperature_starts_every_dialect_without_one() -> None:
    cfg = _cfg("--no-temperature")
    assert cfg.send_temperature is False
    assert cfg.dialect_for(cfg.provider).send_temperature is False


def test_the_summary_reports_the_dialect_the_run_negotiated(capsys) -> None:
    cfg = _cfg("--no-temperature")
    cfg.dialect_for(cfg.provider).token_param = TOKEN_PARAM_COMPLETION

    translora._print_summary(1.0, 1, RunTotals(), 0, cfg)

    assert "Dialect:    max_completion_tokens, no temperature" in \
        capsys.readouterr().out


def test_the_summary_says_nothing_about_a_provider_that_needed_nothing(
    capsys,
) -> None:
    translora._print_summary(1.0, 1, RunTotals(), 0, _cfg())
    assert "Dialect" not in capsys.readouterr().out


# === Review provider =========================================================


def test_the_review_pass_defaults_to_the_main_provider() -> None:
    cfg = _cfg("--model", "qwen")
    assert cfg.review_provider == cfg.provider
    assert (cfg.review_api_url, cfg.review_api_key, cfg.review_model) == \
        ("", "", None)


@pytest.mark.parametrize("flag,field,value", [
    ("--review-api-url", "api_url", "https://review.example/v1"),
    ("--review-api-key", "api_key", "sk-review"),
    ("--review-model", "model", "stronger"),
])
def test_each_review_override_replaces_only_its_own_field(
    flag: str, field: str, value: str,
) -> None:
    cfg = _cfg("--model", "qwen", "--api-key", "sk-main", flag, value)
    review, main = cfg.review_provider, cfg.provider
    assert getattr(review, field) == value
    # Everything the flag did not name still comes from the main provider.
    for other in ("api_url", "api_key", "model"):
        if other != field:
            assert getattr(review, other) == getattr(main, other)


def test_the_review_key_falls_back_to_its_own_environment_variable(
    tmp_path, monkeypatch,
) -> None:
    srt = _write(tmp_path / "movie.srt")
    seen = _fake_translation(monkeypatch)
    monkeypatch.setenv(translora.REVIEW_API_KEY_ENV, "sk-review-env")
    monkeypatch.setattr(sys, "argv", [
        "translora.py", str(srt), "-t", "Arabic", "--api-url", "http://x",
        "--api-key", "sk-main"])

    with pytest.raises(SystemExit):
        translora.main()
    cfg = seen[0][1]
    assert cfg.review_provider.api_key == "sk-review-env"
    assert cfg.provider.api_key == "sk-main"


def test_an_explicit_review_key_wins_over_the_environment(
    tmp_path, monkeypatch,
) -> None:
    srt = _write(tmp_path / "movie.srt")
    seen = _fake_translation(monkeypatch)
    monkeypatch.setenv(translora.REVIEW_API_KEY_ENV, "sk-review-env")
    monkeypatch.setattr(sys, "argv", [
        "translora.py", str(srt), "-t", "Arabic", "--api-url", "http://x",
        "--review-api-key", "sk-explicit"])

    with pytest.raises(SystemExit):
        translora.main()
    assert seen[0][1].review_provider.api_key == "sk-explicit"


def test_with_no_review_key_anywhere_the_review_pass_uses_the_main_one(
    tmp_path, monkeypatch,
) -> None:
    srt = _write(tmp_path / "movie.srt")
    seen = _fake_translation(monkeypatch)
    monkeypatch.delenv(translora.REVIEW_API_KEY_ENV, raising=False)
    monkeypatch.setenv(translora.API_KEY_ENV, "sk-main")
    monkeypatch.setattr(sys, "argv", [
        "translora.py", str(srt), "-t", "Arabic", "--api-url", "http://x",
        "--review-model", "stronger"])

    with pytest.raises(SystemExit):
        translora.main()
    assert seen[0][1].review_provider.api_key == "sk-main"


def test_dry_run_names_one_provider_when_every_pass_shares_it(
    tmp_path, monkeypatch, capsys,
) -> None:
    out = _dry_run_output(tmp_path, monkeypatch, capsys, "--model", "qwen")
    assert "Provider: http://x (model qwen) — every pass" in out
    assert "Review:" not in out


def test_dry_run_names_both_providers_without_printing_a_key(
    tmp_path, monkeypatch, capsys,
) -> None:
    out = _dry_run_output(
        tmp_path, monkeypatch, capsys, "--model", "qwen",
        "--api-key", "sk-main-secret",
        "--review-api-url", "https://review.example/v1/chat/completions",
        "--review-api-key", "sk-review-secret", "--review-model", "stronger")
    assert "Provider: http://x (model qwen) — every pass except review" in out
    assert ("Review:   https://review.example/v1/chat/completions "
            "(model stronger) — review pass (own key)") in out
    assert "secret" not in out


def test_dry_run_says_when_the_review_provider_shares_the_main_key(
    tmp_path, monkeypatch, capsys,
) -> None:
    out = _dry_run_output(tmp_path, monkeypatch, capsys,
                          "--review-model", "stronger")
    assert "— review pass (same key as above)" in out


def test_dry_run_strips_a_credential_query_param_from_the_url(
    tmp_path, monkeypatch, capsys,
) -> None:
    _write(tmp_path / "movie.srt")
    _fake_translation(monkeypatch)
    args = _args(str(tmp_path / "movie.srt"), "-t", "Arabic",
                 "--api-url", "http://host/v1?api_key=sk-in-the-url&beta=1",
                 "--dry-run")
    assert run_async(_run(args, RunTotals())) == EXIT_OK
    out = capsys.readouterr().out
    assert "Provider: http://host/v1?beta=1" in out
    assert "sk-in-the-url" not in out


# === Throughput projection ===================================================


def _dry_run_output(tmp_path, monkeypatch, capsys, *argv: str) -> str:
    _write(tmp_path / "movie.srt")
    _fake_translation(monkeypatch)
    args = _args(str(tmp_path / "movie.srt"), "-t", "Arabic",
                 "--api-url", "http://x", "--dry-run", *argv)
    assert run_async(_run(args, RunTotals())) == EXIT_OK
    return str(capsys.readouterr().out)


def test_dry_run_prices_the_repair_pass_and_says_it_is_a_ceiling(
    tmp_path, monkeypatch, capsys,
) -> None:
    out = _dry_run_output(tmp_path, monkeypatch, capsys)
    assert "repair <=2" in out
    assert "up to 2 repair" in out
    # Off by default, so it must not appear as a cost until it is asked for.
    assert "back-translation" not in out


def test_dry_run_prices_the_back_translation_only_when_it_is_asked_for(
    tmp_path, monkeypatch, capsys,
) -> None:
    out = _dry_run_output(tmp_path, monkeypatch, capsys,
                          "-s", "English", "--verify-adequacy")
    assert "back-translation 1" in out


def test_dry_run_drops_the_repair_line_when_the_retry_is_off(
    tmp_path, monkeypatch, capsys,
) -> None:
    out = _dry_run_output(tmp_path, monkeypatch, capsys, "--no-fix-flagged")
    assert "repair 0" in out
    totals = next(line for line in out.splitlines() if "LLM calls" in line)
    assert "repair" not in totals


def test_dry_run_estimates_the_wall_clock_and_admits_it_is_an_estimate(
    tmp_path, monkeypatch, capsys,
) -> None:
    out = _dry_run_output(tmp_path, monkeypatch, capsys)
    assert "Estimated wall clock:" in out
    assert "assumes 3.5s per call; an estimate, not a measurement." in out
    assert "at 1 concurrent batch(es)" in out


def test_a_higher_concurrency_shortens_the_estimate(
    tmp_path, monkeypatch, capsys,
) -> None:
    serial = _dry_run_output(tmp_path, monkeypatch, capsys, "-c", "1")
    parallel = _dry_run_output(tmp_path, monkeypatch, capsys, "-c", "6")
    assert "at 1 concurrent batch(es)" in serial
    assert "at 6 concurrent batch(es)" in parallel
    assert serial.count("Estimated wall clock") == 1
    # The same work, six lanes wide, cannot take longer.
    assert _seconds(serial) > _seconds(parallel)


def _seconds(dry_run_output: str) -> float:
    """The estimate line's duration, in seconds."""
    line = next(line for line in dry_run_output.splitlines()
                if "Estimated wall clock" in line)
    text = line.split("Estimated wall clock:")[1].split(" at ")[0].strip()
    units = {"h": 3600, "m": 60, "s": 1}
    return sum(float(part[:-1]) * units[part[-1]] for part in text.split())


def test_a_multi_file_run_reports_what_the_whole_run_cost(
    tmp_path, monkeypatch, capsys,
) -> None:
    _write(tmp_path / "a.srt")
    _write(tmp_path / "b.srt")
    seen = _fake_translation(monkeypatch)
    args = _args(str(tmp_path / "a.srt"), str(tmp_path / "b.srt"),
                 "-t", "Arabic", "--api-url", "http://x")

    async def counted(input_path, output_path, cfg):
        cfg.calls.count("scan")
        cfg.calls.count("translate", 4)
        output_path.write_text("t\n", encoding="utf-8")

    monkeypatch.setattr(translora, "translate_file_async", counted)
    assert run_async(_run(args, RunTotals())) == EXIT_OK

    out = capsys.readouterr().out
    assert "LLM calls:  10 (2 scan, 8 translate)" in out
    assert seen == []


def test_a_single_file_run_does_not_repeat_the_call_line(
    tmp_path, monkeypatch, capsys,
) -> None:
    # The per-file completion banner already carries it, with blocks/s.
    _write(tmp_path / "a.srt")
    _fake_translation(monkeypatch)
    args = _args(str(tmp_path / "a.srt"), "-t", "Arabic", "--api-url", "http://x")

    assert run_async(_run(args, RunTotals())) == EXIT_OK
    assert "LLM calls" not in capsys.readouterr().out
