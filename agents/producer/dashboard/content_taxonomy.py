"""Phase 2D multi-format content taxonomy — Python mirror of
web/lib/content/taxonomy.ts. Pure constants + helpers; no I/O.

Keep this in lockstep with the TypeScript module when adding values.
"""
from __future__ import annotations

CONTENT_CHANNELS = (
    "instagram",
    "tiktok",
    "facebook",
    "linkedin",
    "email",
    "website",
    "other",
)

CONTENT_FORMATS = (
    "ugc_video_ad",
    "organic_reel",
    "story",
    "feed_post",
    "carousel",
    "static_image",
    "short_video",
    "long_video",
    "text_post",
    "email_snippet",
    "blog_snippet",
)

DISTRIBUTION_TYPES = ("paid", "organic", "client_review_only")

CONTENT_GOALS = (
    "awareness",
    "trust_building",
    "education",
    "offer",
    "launch",
    "testimonial",
    "conversion",
    "community",
    "retention",
)

RECOMMENDED_ASSET_TYPES = (
    "ugc_video",
    "short_video",
    "long_video",
    "static_image",
    "carousel_slides",
    "story_frames",
    "copy_only",
    "email_copy",
    "blog_copy",
)

_VIDEO_FORMATS = frozenset(
    {"ugc_video_ad", "organic_reel", "short_video", "long_video"}
)
_PROMPT_FORMATS = _VIDEO_FORMATS


def format_needs_video_generation(fmt: str) -> bool:
    """True iff this format eventually needs a paid video generation
    (behind the existing operator gate — never auto-triggered)."""
    return fmt in _VIDEO_FORMATS


def format_needs_prompt_version(fmt: str) -> bool:
    """True iff this format's workflow runs through a prompt_versions
    row (vs a copy/brief path)."""
    return fmt in _PROMPT_FORMATS


def default_asset_type_for_format(fmt: str) -> str:
    return {
        "ugc_video_ad": "ugc_video",
        "organic_reel": "short_video",
        "short_video": "short_video",
        "long_video": "long_video",
        "carousel": "carousel_slides",
        "story": "story_frames",
        "static_image": "static_image",
        "feed_post": "static_image",
        "text_post": "copy_only",
        "email_snippet": "email_copy",
        "blog_snippet": "blog_copy",
    }.get(fmt, "copy_only")


def is_content_channel(v: object) -> bool:
    return isinstance(v, str) and v in CONTENT_CHANNELS


def is_content_format(v: object) -> bool:
    return isinstance(v, str) and v in CONTENT_FORMATS
