"""Brief compiler: turns MASTER_CONTEXT findings into a Kling 3.0 V2V brief.

Injects the 9-layer UGC formula and the authenticity rules extracted from
krusemediallc/arcads-claude-code (seedance-2-ugc.md + ugc-selfie-style.md)
so every Producer run defaults to UGC realism rather than polished AI gloss.

HARD negative constraints from MASTER_CONTEXT.md are enforced via Kling's
`negative_prompt` field. Soft constraints are merely hinted in the main prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.context_schema import (
    Brand,
    NegativeConstraint,
    ReferralMotion,
    Severity,
    WinningHook,
)


# --------------------------------------------------------------------------- #
# UGC authenticity library (ported from krusemediallc/arcads-claude-code)
# --------------------------------------------------------------------------- #

# 9-layer UGC formula, adapted from seedance-2-ugc.md and tuned for Kling 3.0.
UGC_LAYER_TEMPLATES = [
    "Opening hook: {hook_pattern}",                                                # 1
    "Product reveal: close-up handling, texture visible, natural grip",            # 2
    "Benefit demonstration: {hook_description}",                                   # 3
    "Real imperfection: slight pause, subtle stumble, brief laugh - authenticity", # 4
    "Genuine emotional reaction: surprise or quiet approval",                      # 5
    "Subtle CTA, no hard sell",                                                    # 6
    "Ambient micro-gesture: hair touch or weight shift or head tilt",              # 7
    "Lighting/framing: natural window light, off-center selfie framing",           # 8
    "Audio tone: conversational pace, breath pauses, no studio polish",            # 9
]

# Universal UGC visual rules (ugc-selfie-style.md). Concatenated into the prompt.
UGC_VISUAL_RULES = [
    "iPhone 15 Pro front camera in selfie mode",
    "autofocus micro-pulses, rolling-shutter micro-artifacts",
    "mild luminance grain, documentary-style handheld",
    "visible pores, subtle blemishes, skin tone variation",
    "asymmetrical expressions, micro-expressions, natural blinks",
    "shifts weight, briefly breaks eye contact, adjusts grip",
]

# Kling 3.0-specific sweet spot (per the arcads-claude-code per-model table).
KLING_STYLE_HINT = (
    "compact pacing, physics-driven motion, anchor hands in frame, "
    "emphasize product texture"
)

# Universal negative prompt (ugc-selfie-style.md "universal negations").
DEFAULT_UGC_NEGATIVE_PROMPT = (
    "studio lighting, professional photography, cinematic, LUT, color graded, "
    "stabilization, perfect skin, heavy makeup, subtitles, captions, "
    "text overlays, symmetrical face, glossy, filtered"
)


# --------------------------------------------------------------------------- #
# Output shape - what the Producer agent hands to KlingClient
# --------------------------------------------------------------------------- #


@dataclass
class KlingVideoBrief:
    """Self-contained Kling V2V request envelope.

    Composed by `compile_brief`; consumed by the Producer's
    `generate_ugc_video` tool which translates this into a
    KlingClient.submit_video_to_video call.
    """
    prompt: str
    negative_prompt: str
    duration: int
    aspect_ratio: str = "9:16"
    mode: str = "professional"
    cfg_scale: float = 0.5

    # Resolved absolute paths inside the client silo.
    reference_video_path: Optional[Path] = None
    character_image_path: Optional[Path] = None
    product_image_path: Optional[Path] = None

    # Audit trail: which MASTER_CONTEXT findings produced this brief?
    source_hook_id: Optional[str] = None
    source_motion_id: Optional[str] = None
    enforced_constraint_ids: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Compiler
# --------------------------------------------------------------------------- #


def compile_brief(
    hook: WinningHook,
    motion: Optional[ReferralMotion],
    character_image_path: Path,
    product_image_path: Path,
    negative_constraints: list[NegativeConstraint],
    brand: Optional[Brand] = None,
    duration: int = 10,
    aspect_ratio: str = "9:16",
) -> KlingVideoBrief:
    """Compile a WinningHook + ReferralMotion into a Kling V2V brief.

    HARD negative constraints are appended to `negative_prompt` and their IDs
    recorded on the brief so the Producer can prove enforcement in the audit log.
    """
    # ---- Prompt: 9-layer formula filled from the hook ----
    layers: list[str] = []
    for i, template in enumerate(UGC_LAYER_TEMPLATES, start=1):
        rendered = template.format(
            hook_pattern=hook.pattern,
            hook_description=hook.description,
        )
        layers.append(f"{i}. {rendered}")

    if motion is not None:
        motion_parts = [f"Motion reference: {motion.description}"]
        if motion.pacing:
            motion_parts.append(f"pacing={motion.pacing}")
        if motion.camera_style:
            motion_parts.append(f"camera={motion.camera_style}")
        layers.append("Motion: " + " | ".join(motion_parts))

    layers.append(f"Style: {KLING_STYLE_HINT}")
    layers.append("Visual: " + "; ".join(UGC_VISUAL_RULES))

    if brand and brand.voice_attributes:
        layers.append(f"Brand voice: {', '.join(brand.voice_attributes)}")

    prompt = "\n".join(layers)

    # ---- Negative prompt: defaults + brand-forbidden + HARD constraints ----
    negative_parts: list[str] = [DEFAULT_UGC_NEGATIVE_PROMPT]
    enforced_ids: list[str] = []

    if brand and brand.forbidden_terms:
        negative_parts.append(", ".join(brand.forbidden_terms))

    for c in negative_constraints:
        if c.severity == Severity.HARD:
            negative_parts.append(c.rule)
            enforced_ids.append(c.id)

    negative_prompt = "; ".join(negative_parts)

    return KlingVideoBrief(
        prompt=prompt,
        negative_prompt=negative_prompt,
        duration=duration,
        aspect_ratio=aspect_ratio,
        reference_video_path=Path(motion.reference_path) if motion else None,
        character_image_path=character_image_path,
        product_image_path=product_image_path,
        source_hook_id=hook.id,
        source_motion_id=motion.id if motion else None,
        enforced_constraint_ids=enforced_ids,
    )
