"""Smoke tests for the Producer's brief compiler (Phase 3).

Covers the load-bearing invariants of compile_brief:
- 9-layer UGC formula renders in order, hook fields substituted correctly
- HARD vs SOFT NegativeConstraint enforcement (only HARD reaches the prompt)
- Motion injection (and absence when motion=None)
- Brand voice / forbidden terms merging
- Kling style hint + default UGC negatives always present
- source_hook_id / source_motion_id / enforced_constraint_ids audit trail
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agents.producer.brief_compiler import (
    DEFAULT_UGC_NEGATIVE_PROMPT,
    KLING_STYLE_HINT,
    compile_brief,
)
from core.context_schema import (
    AddedBy,
    Brand,
    Confidence,
    NegativeConstraint,
    ReferralMotion,
    Severity,
    WinningHook,
)


NOW = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)


def _hook(
    hook_id: str = "WH-001",
    pattern: str = "Price comparison shock",
    description: str = "Side-by-side cart total reveal vs competitor",
) -> WinningHook:
    return WinningHook(
        id=hook_id,
        pattern=pattern,
        description=description,
        days_active=42,
        confidence=Confidence.HIGH,
        added_by=AddedBy.STRATEGIST,
        added_at=NOW,
    )


def _motion(
    motion_id: str = "RM-001",
    description: str = "Handheld aisle walk-and-talk",
    reference_path: str = "references/referral_videos/walkthrough.mp4",
) -> ReferralMotion:
    return ReferralMotion(
        id=motion_id,
        description=description,
        reference_path=reference_path,
        pacing="medium-fast",
        camera_style="handheld_POV",
        duration_seconds=15,
        added_by=AddedBy.STRATEGIST,
        added_at=NOW,
    )


def _constraint(
    rule: str,
    severity: Severity = Severity.HARD,
    constraint_id: str = "NC-001",
) -> NegativeConstraint:
    return NegativeConstraint(
        id=constraint_id,
        rule=rule,
        reason="Documented incident",
        severity=severity,
        added_by=AddedBy.ANALYST,
        added_at=NOW,
    )


def _empty_brief_inputs() -> dict:
    return dict(
        hook=_hook(),
        motion=None,
        character_image_path=Path("char.jpg"),
        product_image_path=Path("prod.jpg"),
        negative_constraints=[],
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_nine_layer_formula_in_order_and_complete():
    """All 9 numbered layers must appear in the prompt in order 1..9."""
    brief = compile_brief(**_empty_brief_inputs())
    indices = [brief.prompt.find(f"{i}. ") for i in range(1, 10)]
    assert all(i >= 0 for i in indices), f"missing layer(s); indices={indices}"
    assert indices == sorted(indices), "layers must appear in numeric order"


def test_hook_pattern_renders_in_layer_one_and_description_in_layer_three():
    brief = compile_brief(
        **{**_empty_brief_inputs(),
           "hook": _hook(pattern="Price shock", description="Bigger cart vs ours")}
    )
    assert "Opening hook: Price shock" in brief.prompt
    assert "Benefit demonstration: Bigger cart vs ours" in brief.prompt


def test_hard_constraint_enforced_soft_constraint_ignored():
    hard = _constraint("No alcohol depictions", Severity.HARD, "NC-001")
    soft = _constraint("Avoid sterile labs", Severity.SOFT, "NC-002")
    brief = compile_brief(
        **{**_empty_brief_inputs(), "negative_constraints": [hard, soft]}
    )
    assert "No alcohol depictions" in brief.negative_prompt
    assert "Avoid sterile labs" not in brief.negative_prompt
    assert brief.enforced_constraint_ids == ["NC-001"]


def test_multiple_hard_constraints_all_enforced_and_tracked():
    c1 = _constraint("rule one", Severity.HARD, "NC-001")
    c2 = _constraint("rule two", Severity.HARD, "NC-002")
    brief = compile_brief(
        **{**_empty_brief_inputs(), "negative_constraints": [c1, c2]}
    )
    assert "rule one" in brief.negative_prompt
    assert "rule two" in brief.negative_prompt
    assert brief.enforced_constraint_ids == ["NC-001", "NC-002"]


def test_motion_injected_when_provided():
    motion = _motion(description="quick zoom on product")
    brief = compile_brief(**{**_empty_brief_inputs(), "motion": motion})
    assert "Motion reference: quick zoom on product" in brief.prompt
    assert "pacing=medium-fast" in brief.prompt
    assert "camera=handheld_POV" in brief.prompt
    assert brief.source_motion_id == "RM-001"
    assert brief.reference_video_path == Path("references/referral_videos/walkthrough.mp4")


def test_no_motion_means_no_motion_line_and_no_reference_path():
    brief = compile_brief(**_empty_brief_inputs())
    assert "Motion reference:" not in brief.prompt
    assert brief.reference_video_path is None
    assert brief.source_motion_id is None


def test_brand_voice_in_prompt_forbidden_in_negative():
    brand = Brand(
        voice_attributes=["practical", "value-first"],
        forbidden_terms=["cheap", "discount-only"],
    )
    brief = compile_brief(**{**_empty_brief_inputs(), "brand": brand})
    assert "Brand voice: practical, value-first" in brief.prompt
    assert "cheap, discount-only" in brief.negative_prompt


def test_default_ugc_negative_always_present():
    brief = compile_brief(**_empty_brief_inputs())
    assert DEFAULT_UGC_NEGATIVE_PROMPT in brief.negative_prompt


def test_kling_style_hint_always_present():
    brief = compile_brief(**_empty_brief_inputs())
    assert KLING_STYLE_HINT in brief.prompt


def test_source_hook_id_preserved_on_brief():
    brief = compile_brief(
        **{**_empty_brief_inputs(), "hook": _hook(hook_id="WH-042")}
    )
    assert brief.source_hook_id == "WH-042"


def test_duration_and_aspect_ratio_pass_through():
    brief = compile_brief(
        **_empty_brief_inputs(),
        duration=15,
        aspect_ratio="16:9",
    )
    assert brief.duration == 15
    assert brief.aspect_ratio == "16:9"


def test_visual_rules_appear_in_prompt():
    """Universal UGC visual rules from ugc-selfie-style.md must be present."""
    brief = compile_brief(**_empty_brief_inputs())
    assert "iPhone 15 Pro front camera in selfie mode" in brief.prompt
    assert "visible pores, subtle blemishes, skin tone variation" in brief.prompt
