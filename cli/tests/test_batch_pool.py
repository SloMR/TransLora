"""The per-file batch pool: ordering, first-failure propagation, the warn
sink, and the resume sidecar.

Every test replaces `translate_batch_with_retry`, so nothing here calls out.
"""

from __future__ import annotations

import time

import pytest

import core.batch_pool as batch_pool
import core.translator as translator
from core.batch_runner import BatchResult, FileTranslationError
from core.live_status import Colors
from core.srt_parser import SubtitleBlock, split_batches
from tests.conftest import make_blocks, run_async


def _fake_translate(calls: list[int]):
    async def fake(client, idx, batch, cfg, file_context, prev_tail=None):
        calls.append(idx)
        return BatchResult(
            [SubtitleBlock(b.number, b.timestamp, f"T{b.number}") for b in batch])
    return fake


# === Batch fan-out ===========================================================


def test_run_batches_returns_results_in_input_order(monkeypatch, cfg) -> None:
    cfg.concurrency = 4
    batches = split_batches(make_blocks(8), 2)
    calls: list[int] = []
    monkeypatch.setattr(batch_pool, "translate_batch_with_retry",
                        _fake_translate(calls))

    out = run_async(batch_pool.run_batches(
        None, batches, cfg, Colors(False), time.time()))

    assert sorted(calls) == [0, 1, 2, 3]
    assert [b.number for r in out for b in r.blocks] == list(range(1, 9))


def test_run_batches_propagates_a_fatal_batch(monkeypatch, cfg) -> None:
    cfg.concurrency = 1
    batches = split_batches(make_blocks(6), 2)

    async def fake(client, idx, batch, cfg_, file_context, prev_tail=None):
        raise FileTranslationError(f"batch {idx} is doomed")

    monkeypatch.setattr(batch_pool, "translate_batch_with_retry", fake)

    with pytest.raises(FileTranslationError, match="batch 0 is doomed"):
        run_async(batch_pool.run_batches(
            None, batches, cfg, Colors(False), time.time()))


def test_run_batches_restores_the_warn_sink(monkeypatch, cfg) -> None:
    cfg.verbose = True
    original = cfg.warn
    batches = split_batches(make_blocks(2), 2)
    monkeypatch.setattr(batch_pool, "translate_batch_with_retry",
                        _fake_translate([]))

    run_async(batch_pool.run_batches(
        None, batches, cfg, Colors(False), time.time()))

    assert cfg.warn is original


# === Resume sidecar ==========================================================


def _progress(tmp_path, cfg, blocks):
    return translator._open_progress(
        tmp_path / "a.srt", tmp_path / "a.ar.srt", cfg, len(blocks))


def test_resume_reuses_completed_batches_on_a_rerun(monkeypatch, tmp_path, cfg) -> None:
    cfg.resume = True
    cfg.batch_size = 2
    blocks = make_blocks(4)
    batches = split_batches(blocks, cfg.batch_size)
    calls: list[int] = []
    monkeypatch.setattr(batch_pool, "translate_batch_with_retry",
                        _fake_translate(calls))

    first = run_async(batch_pool.run_batches(
        None, batches, cfg, Colors(False), time.time(),
        None, _progress(tmp_path, cfg, blocks)))
    assert sorted(calls) == [0, 1]
    assert translator.progress_path(tmp_path / "a.ar.srt").exists()

    calls.clear()
    second = run_async(batch_pool.run_batches(
        None, batches, cfg, Colors(False), time.time(),
        None, _progress(tmp_path, cfg, blocks)))

    assert calls == []          # nothing was paid for twice
    assert second == first


def test_resume_ignores_a_sidecar_from_a_different_run(
    monkeypatch, tmp_path, cfg,
) -> None:
    cfg.resume = True
    cfg.batch_size = 2
    blocks = make_blocks(4)
    batches = split_batches(blocks, cfg.batch_size)
    calls: list[int] = []
    monkeypatch.setattr(batch_pool, "translate_batch_with_retry",
                        _fake_translate(calls))

    run_async(batch_pool.run_batches(
        None, batches, cfg, Colors(False), time.time(),
        None, _progress(tmp_path, cfg, blocks)))
    calls.clear()

    # Same output path, different run identity: the stored batches no longer
    # describe this run, so none of them may be reused.
    cfg.batch_size = 4
    rebatched = split_batches(blocks, cfg.batch_size)
    run_async(batch_pool.run_batches(
        None, rebatched, cfg, Colors(False), time.time(),
        None, _progress(tmp_path, cfg, blocks)))

    assert calls == [0]


def test_resume_disabled_writes_no_sidecar(tmp_path, cfg) -> None:
    cfg.resume = False
    assert _progress(tmp_path, cfg, make_blocks(2)) is None
