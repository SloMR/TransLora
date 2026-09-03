"""The three quality bundles, mirrored from web/src/app/run-presets.ts: one
answer instead of six knobs. Balanced is the pipeline's own constants — the
classic command's defaults. The guided session opens on Best, as the web does.
tests/test_parity.py holds the two lists together."""

from __future__ import annotations

from dataclasses import dataclass, fields

from .constants import (
    DEFAULT_CONTEXT_OVERLAP,
    DEFAULT_FIX_FLAGGED,
    DEFAULT_FULL_ATTRIBUTION,
    DEFAULT_REFINE_ATTRIBUTION,
    DEFAULT_REVIEW,
    DEFAULT_VERIFY_ADEQUACY,
)


@dataclass(frozen=True)
class QualityKnobs:
    """The knobs a preset decides. Batch size, retries and the provider are not
    quality choices and are left exactly as the user set them."""
    review: bool
    refine_attribution: bool
    fix_flagged: bool
    verify_adequacy: bool
    full_attribution: bool
    context_overlap: int


@dataclass(frozen=True)
class QualityPreset:
    key: str
    label: str
    # One line under the choice: what this buys and what it costs.
    summary: str
    knobs: QualityKnobs


QUALITY_PRESETS: dict[str, QualityPreset] = {
    p.key: p for p in (
        QualityPreset(
            "fast", "Fast",
            "One pass per batch. No review, no repairs — about a third of the calls.",
            QualityKnobs(review=False, refine_attribution=False, fix_flagged=False,
                         verify_adequacy=False, full_attribution=False,
                         context_overlap=1),
        ),
        QualityPreset(
            "balanced", "Balanced",
            "Review where the glossary can help, and one repair pass for what the detectors flag.",
            QualityKnobs(review=DEFAULT_REVIEW,
                         refine_attribution=DEFAULT_REFINE_ATTRIBUTION,
                         fix_flagged=DEFAULT_FIX_FLAGGED,
                         verify_adequacy=DEFAULT_VERIFY_ADEQUACY,
                         full_attribution=DEFAULT_FULL_ATTRIBUTION,
                         context_overlap=DEFAULT_CONTEXT_OVERLAP),
        ),
        QualityPreset(
            "best", "Best",
            "Everything on: every scene attributed, and a back-translation check on a sample.",
            QualityKnobs(review=True, refine_attribution=True, fix_flagged=True,
                         verify_adequacy=True, full_attribution=True,
                         context_overlap=3),
        ),
    )
}

QUALITY_CHOICES: tuple[str, ...] = tuple(QUALITY_PRESETS)
KNOB_NAMES: tuple[str, ...] = tuple(f.name for f in fields(QualityKnobs))
