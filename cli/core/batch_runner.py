"""Translating one batch: the two retry budgets, the recursive split, the
review pass, the deterministic repair of an accepted batch, and the one
focused retry the flags that repair raises can buy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from .chat_client import (
    FatalApiError,
    call_chat_api,
    is_context_overflow,
    is_retryable_http,
    retry_delay,
    strip_markdown_fences,
)
from .config import TranslationConfig
from .constants import ATTEMPTS_BEFORE_SPLIT
from .context_pass import FileContext, drift_phrase
from .languages import effective_norms, grammar_note_for
from .prompt import (
    REVIEW_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_fix_flagged_user_message,
    build_review_user_message,
    build_translate_user_message,
)
from .repair import (
    ScriptLeak,
    dialogue_dash_lines,
    enforce_line_length,
    find_cross_cue_shifts,
    find_script_leaks,
    find_tags,
    normalize_rtl_punctuation,
    reflow_to_line_count,
    repair_tags,
    restore_dialogue_dashes,
    restore_terminal_punctuation,
    shift_message,
)
from .srt_parser import (
    SubtitleBlock,
    parse_lite,
    serialize_lite,
    validate_batch,
)


class FileTranslationError(Exception):
    """A batch exhausted its retries; the whole file is considered failed."""


@dataclass(frozen=True)
class BatchFlag:
    """One problem a finished batch was flagged for: the warning a person
    reads, and the line the model is shown when the batch is re-issued.

    `cause` says WHAT went wrong, never where, so the same term drifting in
    sixteen batches is one problem the repair pass can budget for as one.
    """
    block: int
    message: str
    problem: str
    cause: str


@dataclass
class BatchResult:
    """An accepted batch and everything that looked wrong with it."""
    blocks: list[SubtitleBlock]
    flags: list[BatchFlag] = field(default_factory=list)


def output_token_budget(blocks: list[SubtitleBlock], wire: str | None = None) -> int:
    """Output cap sized from the payload — a fixed per-block figure truncates
    long or non-Latin blocks."""
    if wire is None:
        wire = serialize_lite(blocks)
    return max(300, len(wire) + 60 * len(blocks))


def _attach_input_timestamps(
    batch: list[SubtitleBlock], parsed: list[SubtitleBlock],
) -> list[SubtitleBlock]:
    return [
        SubtitleBlock(number=batch[i].number,
                      timestamp=batch[i].timestamp,
                      text=parsed[i].text)
        for i in range(len(batch))
    ]


def _tag_flag(src: SubtitleBlock, out: SubtitleBlock) -> BatchFlag:
    source_tags = find_tags(src.text)
    output_tags = find_tags(out.text)
    named = "...".join(source_tags)
    problem = (
        f"block {src.number}: the formatting tags {named} were changed to "
        + "...".join(output_tags)
        if output_tags else
        f"block {src.number}: the formatting tags {named} were dropped"
    )
    return BatchFlag(
        block=src.number,
        message=(f"Block {src.number}: formatting tags changed "
                 f"({','.join(source_tags)} -> {','.join(output_tags)})"),
        problem=problem,
        cause="tags",
    )


def _dash_flag(src: SubtitleBlock, output_text: str) -> BatchFlag:
    source = dialogue_dash_lines(src.text)
    output = dialogue_dash_lines(output_text)
    return BatchFlag(
        block=src.number,
        message=(f"Block {src.number}: speaker dashes changed "
                 f"({source} -> {output})"),
        problem=(f"block {src.number}: the source opens {source} line(s) "
                 f"with a dialogue dash and the translation opens {output}; "
                 f"keep one line per speaker"),
        cause="dashes",
    )


def _leak_flag(src: SubtitleBlock, leak: ScriptLeak) -> BatchFlag:
    return BatchFlag(
        block=src.number,
        message=f"Block {src.number}: {leak.message}",
        problem=f"block {src.number}: {leak.message}",
        cause=f"leak:{leak.script}",
    )


def _finalize_batch(
    batch: list[SubtitleBlock],
    output: list[SubtitleBlock],
    cfg: TranslationConfig,
    report: bool,
    file_context: FileContext | None = None,
) -> BatchResult:
    """Deterministic repair of an accepted batch: restore dropped formatting
    tags and speaker dashes, re-wrap to the source's line count, restore the
    sentence-final mark, then flag suspected bleeding, foreign-script leakage
    and glossary drift.
    None of this can fail a file — a tag we cannot restore is cosmetic.
    `report` is False for a split half, so the root warns once for the batch."""
    norms = effective_norms(cfg.target_lang, cfg.max_line_chars)
    flags: list[BatchFlag] = []
    repaired: list[SubtitleBlock] = []
    for src, out in zip(batch, output, strict=True):
        text, ok = repair_tags(src.text, out.text)
        if not ok:
            flags.append(_tag_flag(src, out))
        # Before the reflow, which leaves a cue with a dialogue dash alone.
        text, dashes_ok = restore_dialogue_dashes(src.text, text)
        if not dashes_ok:
            flags.append(_dash_flag(src, text))
        if cfg.reflow:
            text = reflow_to_line_count(
                text, len(src.text.split("\n")),
                norms.max_chars_per_line, norms.script)
            text = enforce_line_length(
                text, norms.max_chars_per_line, norms.script)
        text = normalize_rtl_punctuation(text, norms.script)
        # After the RTL mapping, so "?" is compared against the target's "؟".
        text = restore_terminal_punctuation(src.text, text, norms.script)
        # Last, so what is checked is the text the file will actually ship.
        # One flag per cue: two leaks are still one cue to look at.
        leaks = find_script_leaks(src.text, text, norms.script)
        if leaks:
            flags.append(_leak_flag(src, leaks[0]))
        repaired.append(SubtitleBlock(out.number, out.timestamp, text))

    if file_context is not None:
        flags.extend(
            BatchFlag(
                block=d.block,
                message=f"Block {d.block}: {drift_phrase(d)}",
                problem=f"block {d.block}: the {drift_phrase(d)}",
                cause=d.cause,
            )
            for d in file_context.drift_entries(batch, repaired)
        )
    flags.extend(
        BatchFlag(
            block=shift.first,
            message=shift_message(shift),
            problem=(f"block {shift.first}: text from the next line appears "
                     f"here ('{shift.run}')"),
            cause="shift",
        )
        for shift in find_cross_cue_shifts(batch, repaired)
    )

    if report:
        for flag in flags:
            cfg.warn(flag.message)
    return BatchResult(repaired, flags)


async def _review_pass(
    client: httpx.AsyncClient,
    batch: list[SubtitleBlock],
    first_pass: list[SubtitleBlock],
    cfg: TranslationConfig,
    file_context: FileContext | None,
) -> list[SubtitleBlock]:
    """Re-check first-pass against the glossary; returns first-pass unchanged if
    review output fails validation or the batch slice has nothing correctable."""
    if file_context is None or not file_context.has_correctable_entries(batch):
        return first_pass
    glossary = file_context.render_for_batch(batch)
    if not glossary:
        return first_pass
    norms = effective_norms(cfg.target_lang, cfg.max_line_chars)
    user_msg = build_review_user_message(
        batch, first_pass, glossary, grammar_note_for(norms.script))
    try:
        cfg.calls.count("review")
        raw = await call_chat_api(
            client, REVIEW_SYSTEM_PROMPT, user_msg, cfg,
            output_token_budget(batch), cfg.review_provider,
        )
    except Exception as e:
        cfg.warn(f"    Review failed, keeping first-pass: {e}")
        return first_pass
    parsed = parse_lite(strip_markdown_fences(raw))
    if not validate_batch(batch, parsed).ok:
        return first_pass
    return _attach_input_timestamps(batch, parsed)


def batch_user_message(
    batch: list[SubtitleBlock],
    cfg: TranslationConfig,
    file_context: FileContext | None,
    prev_tail: list[SubtitleBlock] | None,
    batch_wire: str | None = None,
) -> str:
    """The user message a batch is translated with — rebuilt identically for
    the flagged retry, so the model sees the first attempt's request plus the
    fixes."""
    norms = effective_norms(cfg.target_lang, cfg.max_line_chars)
    return build_translate_user_message(
        cfg.source_lang, cfg.target_lang,
        batch_wire if batch_wire is not None else serialize_lite(batch),
        file_context.render_for_batch(batch) if file_context else "",
        prev_tail or [],
        max_chars=norms.max_chars_per_line, max_lines=norms.max_lines,
        formality=cfg.formality, dialect=cfg.dialect,
    )


async def retry_flagged_batch(
    client: httpx.AsyncClient,
    batch: list[SubtitleBlock],
    cfg: TranslationConfig,
    file_context: FileContext | None,
    prev_tail: list[SubtitleBlock] | None,
    previous: BatchResult,
    flags: list[BatchFlag],
) -> BatchResult:
    """One focused retry of a batch the run flagged. The retry is kept only if
    it validates AND leaves strictly fewer flags behind, so it can never make
    the file worse; anything else keeps the original. Costs one call."""
    if not flags or not batch:
        return previous
    batch_wire = serialize_lite(batch)
    user_msg = build_fix_flagged_user_message(
        batch_user_message(batch, cfg, file_context, prev_tail, batch_wire),
        [flag.problem for flag in flags],
    )
    try:
        cfg.calls.count("repair")
        raw = await call_chat_api(
            client, SYSTEM_PROMPT, user_msg, cfg,
            output_token_budget(batch, batch_wire),
        )
    except Exception as e:
        cfg.warn(f"    Flagged-batch repair failed, keeping the first "
                 f"translation: {e}")
        return previous
    parsed = parse_lite(strip_markdown_fences(raw))
    if not validate_batch(batch, parsed).ok:
        return previous
    retried = _finalize_batch(
        batch, _attach_input_timestamps(batch, parsed), cfg, False, file_context)
    # An adequacy flag cannot be re-measured without another back-translation,
    # so it counts as cleared by a retry that validates.
    if len(retried.flags) >= len(flags):
        return previous
    cfg.warn(f"    Block {batch[0].number}: re-translated after {len(flags)} "
             f"flag(s); {len(retried.flags)} left")
    return retried


async def translate_batch_with_retry(
    client: httpx.AsyncClient,
    batch_idx: int,
    batch: list[SubtitleBlock],
    cfg: TranslationConfig,
    file_context: FileContext | None = None,
    _split_path: str = "",
    prev_tail: list[SubtitleBlock] | None = None,
) -> BatchResult:
    """Translate one batch; on repeated validation failure, halve and recurse.

    Persistent count mismatches usually mean the model is deterministically
    merging two adjacent similar-looking blocks. Halving keeps terminating
    because at N=1 a count mismatch is impossible.

    Validation and transport failures keep separate budgets: a 429 must not
    consume the split budget.
    """
    batch_wire = serialize_lite(batch)
    user_msg = batch_user_message(batch, cfg, file_context, prev_tail, batch_wire)
    label = f"Batch {batch_idx + 1}" + (f".{_split_path}" if _split_path else "")
    first_block = batch[0].number

    can_split = len(batch) > 1
    # A split child is reviewed once by its root, after reassembly.
    review_here = cfg.review and not _split_path
    validation_budget = ATTEMPTS_BEFORE_SPLIT if can_split else max(1, cfg.max_retries)
    transport_budget = max(1, cfg.max_retries)
    validation_failures = 0
    transport_failures = 0
    last_error = ""

    while validation_failures < validation_budget and transport_failures < transport_budget:
        try:
            cfg.calls.count("translate")
            raw = await call_chat_api(
                client, SYSTEM_PROMPT, user_msg, cfg,
                output_token_budget(batch, batch_wire),
            )
            output = parse_lite(strip_markdown_fences(raw))
            # Validate the model's own numbers before reattaching the input's.
            check = validate_batch(batch, output)
            if check.ok:
                output = _attach_input_timestamps(batch, output)
                if review_here:
                    output = await _review_pass(
                        client, batch, output, cfg, file_context,
                    )
                return _finalize_batch(
                    batch, output, cfg, not _split_path, file_context)
            validation_failures += 1
            last_error = check.error
            tag = f"validation {validation_failures}/{validation_budget}"
            cfg.warn(f"    {label} validation failed ({tag}): {check.error}")
            if validation_failures < validation_budget:
                await asyncio.sleep(min(validation_failures, 3))

        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            body = e.response.text or ""
            snippet = body[:200]
            transport_failures += 1
            last_error = f"HTTP {code}: {snippet}"
            tag = f"attempt {transport_failures}/{transport_budget}"
            cfg.warn(f"    {label} HTTP {code} ({tag}): {snippet}")

            if code == 400 and is_context_overflow(body):
                # Retrying an oversized request just pays for the same 400.
                raise FileTranslationError(
                    f"{label} (block {first_block}) HTTP 400: the request "
                    f"exceeded the model's context window. Lower --batch-size "
                    f"(currently {cfg.batch_size}) or --scan-budget "
                    f"(currently {cfg.scan_char_budget}). Provider said: {snippet}"
                ) from e
            if not is_retryable_http(code):
                raise FileTranslationError(
                    f"{label} (block {first_block}) HTTP {code}: {snippet}"
                ) from e
            if transport_failures < transport_budget:
                delay = retry_delay(
                    transport_failures, e.response.headers.get("retry-after"))
                if code == 429:
                    cfg.warn(f"    Rate limited - waiting {delay:.0f}s...")
                await asyncio.sleep(delay)

        except FatalApiError as e:
            # The provider has told us this request will never work; retrying
            # it would only spend the budget on the same answer.
            raise FileTranslationError(
                f"{label} (block {first_block}): {e}") from e

        except Exception as e:
            transport_failures += 1
            last_error = str(e)
            tag = f"attempt {transport_failures}/{transport_budget}"
            cfg.warn(f"    {label} request failed ({tag}): {e}")
            if transport_failures < transport_budget:
                await asyncio.sleep(retry_delay(transport_failures))

    if validation_failures >= validation_budget and can_split:
        mid = len(batch) // 2
        left, right = batch[:mid], batch[mid:]
        cfg.warn(
            f"    {label} splitting {len(batch)} -> {len(left)} + {len(right)} blocks"
        )
        left_path = (_split_path + "L") if _split_path else "L"
        right_path = (_split_path + "R") if _split_path else "R"
        # Sequential: parallel halves would oversubscribe the outer semaphore.
        left_result = await translate_batch_with_retry(
            client, batch_idx, left, cfg, file_context, left_path,
            prev_tail=prev_tail,
        )
        overlap = max(0, cfg.context_overlap)
        right_prev = left[-overlap:] if overlap else []
        right_result = await translate_batch_with_retry(
            client, batch_idx, right, cfg, file_context, right_path,
            prev_tail=right_prev,
        )
        combined = left_result.blocks + right_result.blocks
        if review_here:
            combined = await _review_pass(
                client, batch, combined, cfg, file_context,
            )
        return _finalize_batch(
            batch, combined, cfg, not _split_path, file_context)

    attempts = validation_failures + transport_failures
    raise FileTranslationError(
        f"{label} (block {first_block}) failed after {attempts} attempt(s)"
        + (f": {last_error}" if last_error else "")
    )
