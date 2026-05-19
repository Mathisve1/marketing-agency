"""Map a `DemoGenerationJob` onto a Seedance UGC `/queue` payload.

Phase 1G ships only the UGC payload (`enhancor_seedance` provider,
`ugc` mode). Other provider modes (text-to-video, multi_reference) and
other providers (`enhancor_audio_fixer`) are not built here — the Audio
Fixer remains manual, and Phase 1G never auto-creates an audio_fixer
payload.

This builder is the single place where the dashboard's job model meets
the provider's wire shape. Both sides have to agree on:

  - 720p as the strategic default (not 1080p)
  - 9:16 aspect ratio for UGC
  - fast_mode=true for 720p (Enhancor only forces fast_mode=false at
    1080p)
  - duration as the integer `duration_seconds` from the job row,
    re-emitted as the dashboard-required STRING in the payload
  - prompt composed from the prompt_versions row, never invented
"""

from __future__ import annotations

from typing import Any, Optional

from agents.producer.providers.enhancor_seedance import (
    DEFAULT_ASPECT_RATIO,
    build_ugc_payload,
)

from .demo_jobs import DemoGenerationJob

# How long the composed prompt body is allowed to be. Enhancor accepts
# long prompts but the dashboard contract (docs/enhancor_api_spec.md)
# recommends keeping the negative prompt tight; we cap the negative
# prompt at 500 chars per the user instruction. The main prompt body
# itself is not capped here — long-form prompts are legitimate.
NEGATIVE_PROMPT_MAX_CHARS = 500


def _truncate(value: Optional[str], max_chars: int) -> Optional[str]:
    """Trim `value` to `max_chars` and emit a short marker if cut."""
    if value is None:
        return None
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"


def _compose_prompt(job: DemoGenerationJob) -> str:
    """Stitch the operator-side prompt fields into a single string the
    provider can consume.

    Operators iterate on hook / script / prompt_body / scene_plan /
    creator_direction / product_constraints separately so each piece is
    easy to edit. The wire format needs ONE prompt string; this composer
    is the canonical recipe. Empty fields are skipped without leaving
    blank lines.
    """
    sections: list[tuple[str, Optional[str]]] = [
        ("Hook", job.prompt_hook),
        ("Script", None),  # Body field stands in for both — see below.
        ("Prompt body", job.prompt_body),
        ("Scene plan", job.prompt_scene_plan),
        ("Creator direction", job.prompt_creator_direction),
        ("Product constraints", job.prompt_product_constraints),
    ]
    parts: list[str] = []
    for label, body in sections:
        if body and body.strip():
            parts.append(f"{label}: {body.strip()}")
    composed = "\n\n".join(parts)
    if not composed.strip():
        raise ValueError(
            "Composed prompt is empty — the linked prompt_version has no "
            "hook / prompt_body / scene_plan / creator_direction. Fix the "
            "prompt version in the operator editor before running."
        )
    return composed


def build_seedance_payload_from_job(
    job: DemoGenerationJob,
    *,
    webhook_url: str,
    product_urls: Optional[list[str]] = None,
    influencer_urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Build the Seedance UGC payload that mirrors `job`.

    Args:
      job: the demo job, already loaded from the catalogue or future
        Supabase adapter.
      webhook_url: public HTTPS callback URL. Mandatory for every
        Seedance submission per the dashboard contract.
      product_urls: public HTTPS image URLs for the product (Pai serum
        bottle, packaging, etc.). Falls back to the job's placeholder
        list when None. The runner refuses to --submit while any URL
        on this list is a placeholder.
      influencer_urls: public HTTPS image URLs for the creator. Same
        fallback + placeholder rule as product_urls.

    Returns:
      A dict matching the Seedance `image-to-video / ugc` wire body.
      The underlying `build_ugc_payload` helper validates HTTPS, asset
      counts, the duration / resolution / aspect-ratio enums, and the
      1080p ↔ fast_mode rule.
    """
    if job.provider != "enhancor_seedance":
        raise ValueError(
            f"Phase 1G builder only supports provider=enhancor_seedance; "
            f"got {job.provider!r}"
        )
    if job.provider_mode and job.provider_mode != "ugc":
        raise ValueError(
            f"Phase 1G builder only supports provider_mode=ugc; "
            f"got {job.provider_mode!r}"
        )

    composed_prompt = _compose_prompt(job)
    negative_prompt = _truncate(job.prompt_negative, NEGATIVE_PROMPT_MAX_CHARS)
    if negative_prompt:
        composed_prompt = f"{composed_prompt}\n\nNegative: {negative_prompt}"

    products = list(product_urls or job.placeholder_product_urls)
    influencers = list(influencer_urls or job.placeholder_influencer_urls)

    # Seedance rule: 1080p requires fast_mode=false. The default tier is
    # 720p where fast_mode=true is allowed.
    fast_mode = job.resolution != "1080p"

    return build_ugc_payload(
        prompt=composed_prompt,
        webhook_url=webhook_url,
        products=products,
        influencers=influencers,
        # Seedance's wire format wants the duration as a STRING in the
        # 4..15 range. The dashboard stores it as int duration_seconds.
        duration_sec=str(job.duration_seconds),
        resolution=job.resolution,
        aspect_ratio=DEFAULT_ASPECT_RATIO,  # 9:16
        fast_mode=fast_mode,
        # full_access is mandatory for UGC takes featuring a creator.
        full_access=True,
    )
