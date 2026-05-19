"""Yuvo Studio — multi-clip ad duration planning model (Python mirror).

Pure module: no I/O, no network, no Supabase. Deterministic. Mirrors
web/lib/planning/duration-plan.ts so the runner side (Python) and the
dashboard side (TypeScript) agree byte-for-byte on the clip split.

Product decision: a 25-30s ad is PLANNED whole up front, then realised
as N connected clips. We never generate 15s and randomly extend.

    15s -> 1 clip  [15]
    20s -> 2 clips [15, 5]
    25s -> 2 clips [15, 10]
    30s -> 2 clips [15, 15]

Pai V4 lesson baked in: products with label-hallucination risk get a
blank-label / label-stripped reference and "product as soft prop, no
readable label text".

NO paid call is ever made by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TargetDuration = Literal[15, 20, 25, 30]
TARGET_DURATION_OPTIONS: tuple[int, ...] = (15, 20, 25, 30)

# Per-second credit estimate by tier (mirrors web/lib/quality-tiers.ts).
_PER_SECOND_CREDITS: dict[str, float] = {
    "draft_480p": 82.0,
    "standard_720p": 176.4,
    "premium_1080p": 396.0,
}

_CLIP_DURATIONS: dict[int, list[int]] = {
    15: [15],
    20: [15, 5],
    25: [15, 10],
    30: [15, 15],
}


@dataclass(frozen=True)
class PlannedClip:
    clip_number: int
    duration_seconds: int
    purpose: str
    continuation_role: str  # standalone | open_loop | close_loop
    script_window_start_sec: int
    script_window_end_sec: int
    script_segment: str
    visual_direction: str
    continuity_notes: str
    stitch_notes: str
    estimated_credits: int


@dataclass(frozen=True)
class AdDurationPlan:
    target_duration_seconds: int
    clip_strategy: str  # single_clip | multi_clip_stitched
    clip_count: int
    clips: list[PlannedClip]
    shared_negative_prompt: str
    blank_label_required: bool
    product_reference_rationale: str
    total_estimated_credits: int
    generation_count: int
    stitch_method: str = "concat"
    stitch_transition: str = "hard_cut_or_natural_pause"
    stitch_audio_strategy: str = "native_audio_per_clip_then_review"
    stitch_final_asset_kind: str = "stitched_video"


def _estimate_credits(tier_id: str, duration_sec: int) -> int:
    per_sec = _PER_SECOND_CREDITS.get(tier_id, _PER_SECOND_CREDITS["standard_720p"])
    return round(per_sec * duration_sec)


def _ad_beats(target: int) -> list[tuple[int, int, str]]:
    if target == 15:
        return [
            (0, 3, "relatable hook"),
            (3, 10, "creator context + soft proof"),
            (10, 15, "natural closing line"),
        ]
    return [
        (0, 5, "relatable hook / problem"),
        (5, 12, "creator explains context"),
        (12, 15, "soft transition (NOT an ending)"),
        (15, max(22, target - 8), "routine / application / proof continuation"),
        (max(22, target - 8), target - 3, "benefit / reason-to-believe"),
        (target - 3, target, "natural CTA / closing line"),
    ]


def _beats_in_window(
    beats: list[tuple[int, int, str]], start: int, end: int
) -> list[str]:
    return [label for (s, e, label) in beats if s < end and e > start]


def plan_ad_duration(
    *,
    target_duration_seconds: int,
    quality_tier: str = "standard_720p",
    shared_negative_prompt: str = "",
    label_hallucination_risk: bool = False,
) -> AdDurationPlan:
    """Build the deterministic clip plan for a target duration.

    Raises ValueError on an unsupported duration so callers fail loudly
    rather than silently producing a 15s ad.
    """
    if target_duration_seconds not in _CLIP_DURATIONS:
        raise ValueError(
            f"unsupported target_duration_seconds={target_duration_seconds!r}; "
            f"must be one of {TARGET_DURATION_OPTIONS}"
        )

    durations = _CLIP_DURATIONS[target_duration_seconds]
    clip_strategy = "single_clip" if len(durations) == 1 else "multi_clip_stitched"
    beats = _ad_beats(target_duration_seconds)

    clips: list[PlannedClip] = []
    cursor = 0
    for idx, dur in enumerate(durations):
        clip_number = idx + 1
        start_sec = cursor
        end_sec = cursor + dur
        cursor = end_sec

        is_only = len(durations) == 1
        is_last = clip_number == len(durations)
        role = (
            "standalone"
            if is_only
            else "close_loop"
            if is_last
            else "open_loop"
        )
        window_beats = _beats_in_window(beats, start_sec, end_sec)
        beat_summary = "; ".join(window_beats)

        purpose = (
            "Full self-contained 15s UGC ad"
            if is_only
            else "Hook + context - opens a loop, does NOT resolve the ad"
            if clip_number == 1
            else "Routine/proof continuation + natural CTA - closes the loop"
        )

        if role == "open_loop":
            tail = (
                "End on an unfinished thought / soft transition - clip 2 "
                "must feel like the same take continuing, not a new ad."
            )
        elif role == "close_loop":
            tail = (
                "Pick up exactly where clip 1 left off (same sentence "
                "energy, same setting/lighting/creator), then land the CTA."
            )
        else:
            tail = "Self-contained: hook -> context -> soft close."
        script_segment = (
            f"[Full-ad timeline {start_sec}-{end_sec}s] {beat_summary}. {tail}"
        )

        visual_direction = (
            "Same creator, room, lighting, wardrobe as every other clip. "
            "Real-friend UGC register, handheld feel. Product is a soft "
            "prop, never a label-hero shot. "
            + (
                "Establish the creator + setting in the first 2s."
                if clip_number == 1
                else "Match clip 1's framing + colour exactly so the cut "
                "is invisible."
            )
        )

        if is_only:
            continuity = "Single clip - no cross-clip continuity needed."
        elif clip_number == 1:
            continuity = (
                "Lock creator identity, wardrobe, room, lighting, focal "
                "length, and product-reference image - clip 2 reuses ALL."
            )
        else:
            continuity = (
                "MUST reuse clip 1's exact creator/wardrobe/room/lighting "
                "and the SAME product reference image. No new location, "
                "outfit, or time-of-day change."
            )

        if is_only:
            stitch = "No stitch - single deliverable."
        elif is_last:
            stitch = (
                "Concatenate after clip 1. Expect a hard cut or natural "
                "pause at the seam; review audio before client share."
            )
        else:
            stitch = (
                "This clip ends mid-thought; the seam to clip 2 should "
                "land on a natural pause/breath, not a finished sentence."
            )

        clips.append(
            PlannedClip(
                clip_number=clip_number,
                duration_seconds=dur,
                purpose=purpose,
                continuation_role=role,
                script_window_start_sec=start_sec,
                script_window_end_sec=end_sec,
                script_segment=script_segment,
                visual_direction=visual_direction,
                continuity_notes=continuity,
                stitch_notes=stitch,
                estimated_credits=_estimate_credits(quality_tier, dur),
            )
        )

    total = sum(c.estimated_credits for c in clips)
    rationale = (
        "Pai V4 lesson: prompt-only label guardrails FAILED; a "
        "blank-label / label-stripped product reference SUCCEEDED. Use a "
        "label-stripped reference image, product as soft prop, verify no "
        "readable label text before client share."
        if label_hallucination_risk
        else "No known label-hallucination risk. Standard reference "
        "acceptable; still keep product a soft prop, avoid packaging-text "
        "close-ups."
    )

    return AdDurationPlan(
        target_duration_seconds=target_duration_seconds,
        clip_strategy=clip_strategy,
        clip_count=len(clips),
        clips=clips,
        shared_negative_prompt=shared_negative_prompt,
        blank_label_required=label_hallucination_risk,
        product_reference_rationale=rationale,
        total_estimated_credits=total,
        generation_count=len(clips),
    )


def to_clip_plan_json(plan: AdDurationPlan) -> dict:
    """Compact JSON for generation_batches.clip_plan (migration 007)."""
    return {
        "target_duration_seconds": plan.target_duration_seconds,
        "clip_strategy": plan.clip_strategy,
        "clips": [
            {
                "clip_number": c.clip_number,
                "duration_seconds": c.duration_seconds,
                "purpose": c.purpose,
                "continuation_role": c.continuation_role,
            }
            for c in plan.clips
        ],
        "stitch_plan": {
            "method": plan.stitch_method,
            "transition": plan.stitch_transition,
            "audio_strategy": plan.stitch_audio_strategy,
            "final_asset_kind": plan.stitch_final_asset_kind,
        },
        "product_reference_strategy": {
            "blank_label_required": plan.blank_label_required,
        },
        "estimated_credits_total": plan.total_estimated_credits,
        "generation_count": plan.generation_count,
    }
