"""Brief compiler: turns MASTER_CONTEXT findings into a Kling Omni-Video brief.

Injects the 9-layer UGC formula and the authenticity rules extracted from
krusemediallc/arcads-claude-code (seedance-2-ugc.md + ugc-selfie-style.md)
so every Producer run defaults to UGC realism rather than polished AI gloss.

For Kling Omni-Video, the prompt MUST reference assets via the explicit
asset tags <<<image_1>>>, <<<image_2>>>, <<<video_1>>> so the model knows
which input to apply where. The compiler injects an Assets header at the
top of the prompt mapping tags to roles, then references the tags inline
inside the relevant 9-layer steps.

Convention enforced across the codebase:
  <<<image_1>>> = character (UGC creator persona)
  <<<image_2>>> = product (what's being shown/sold)
  <<<video_1>>> = motion reference (pacing, camera, energy to inherit)

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

# 9-layer UGC formula, adapted from seedance-2-ugc.md and tuned for Kling
# Omni-Video. Asset tags (<<<image_1>>>, <<<image_2>>>) are embedded inline
# in the layers where the character or product naturally appears.
UGC_LAYER_TEMPLATES = [
    "Opening hook: {hook_pattern}, featuring <<<image_1>>>",                       # 1
    "Product reveal: close-up handling of <<<image_2>>>, texture visible, "        # 2
        "natural grip",
    "Benefit demonstration: {hook_description}",                                   # 3
    "Real imperfection: slight pause, subtle stumble, brief laugh - authenticity", # 4
    "Genuine emotional reaction: surprise or quiet approval",                      # 5
    "Subtle CTA, no hard sell",                                                    # 6
    "Ambient micro-gesture: hair touch or weight shift or head tilt",              # 7
    "Lighting/framing: natural window light, off-center selfie framing "           # 8
        "of <<<image_1>>>",
    "Audio tone: conversational pace, breath pauses, no studio polish",            # 9
]

# Universal UGC visual rules (ugc-selfie-style.md).
UGC_VISUAL_RULES = [
    "iPhone 15 Pro front camera in selfie mode",
    "autofocus micro-pulses, rolling-shutter micro-artifacts",
    "mild luminance grain, documentary-style handheld",
    "visible pores, subtle blemishes, skin tone variation",
    "asymmetrical expressions, micro-expressions, natural blinks",
    "shifts weight, briefly breaks eye contact, adjusts grip",
]

# Kling sweet spot (per the arcads-claude-code per-model table).
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
# Output shape
# --------------------------------------------------------------------------- #


@dataclass
class KlingVideoBrief:
    """Self-contained Kling Omni-Video request envelope.

    The agent's `generate_ugc_video` tool passes character_image_path +
    product_image_path as the first two entries of the Omni image_list (in
    that order, matching the <<<image_1>>> / <<<image_2>>> tag convention),
    and reference_video_path as the single video_list entry when present.
    """
    prompt: str
    negative_prompt: str
    duration: int
    aspect_ratio: str = "9:16"
    mode: str = "professional"
    cfg_scale: float = 0.5

    # Asset paths - order is load-bearing (matches <<<image_1>>>, <<<image_2>>>, <<<video_1>>>).
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


def _asset_header(has_motion: bool) -> list[str]:
    """The Assets block at the top of every Omni prompt. Lists which tag
    refers to which input so the Omni model can ground the layers below."""
    lines = [
        "Assets in this generation:",
        "- <<<image_1>>> = the character (UGC creator persona)",
        "- <<<image_2>>> = the product to feature",
    ]
    if has_motion:
        lines.append("- <<<video_1>>> = motion / pacing / camera reference to inherit")
    lines.append("")  # blank line separates from the layers
    return lines


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
    """Compile a WinningHook + ReferralMotion into a Kling Omni-Video brief.

    The prompt is structured so the Omni model can resolve every asset tag:
      1. Assets header - maps tags to roles.
      2. 9-layer UGC formula - tags referenced inline where natural.
      3. Motion guidance (if motion supplied) - explicitly references <<<video_1>>>.
      4. Kling style hint + UGC visual rules.
      5. Brand voice (when recorded).

    HARD negative constraints are appended to `negative_prompt` and their
    IDs recorded on the brief for audit-log proof of enforcement.
    """
    has_motion = motion is not None

    # ---- Assets header ----
    layers: list[str] = list(_asset_header(has_motion))

    # ---- 9-layer formula (tags pre-baked into layer 1, 2, 8 templates) ----
    for i, template in enumerate(UGC_LAYER_TEMPLATES, start=1):
        rendered = template.format(
            hook_pattern=hook.pattern,
            hook_description=hook.description,
        )
        layers.append(f"{i}. {rendered}")

    # ---- Motion guidance - references <<<video_1>>> directly ----
    if has_motion:
        motion_parts = [
            f"follow the pacing and camera of <<<video_1>>> ({motion.description})"
        ]
        if motion.pacing:
            motion_parts.append(f"pacing={motion.pacing}")
        if motion.camera_style:
            motion_parts.append(f"camera={motion.camera_style}")
        layers.append("Motion: " + " | ".join(motion_parts))

    # ---- Kling style + UGC visual rules ----
    layers.append(f"Style: {KLING_STYLE_HINT}")
    layers.append("Visual: " + "; ".join(UGC_VISUAL_RULES))

    # ---- Brand voice if recorded ----
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
