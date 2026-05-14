"""LangChain tool: scrape the Meta Ads Library via Apify.

Wraps an Apify actor that crawls Facebook + Instagram Ads Library for the given
competitor pages and returns active ads with start date, media type, and copy.
The actor ID is configurable so you can swap providers without code changes.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote_plus

from apify_client import ApifyClient
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from services import cost_ledger

# Default to the most-used community FB Ads Library actor. Override via
# APIFY_FB_ADS_ACTOR_ID if you've licensed a different one.
DEFAULT_ACTOR_ID = os.getenv(
    "APIFY_FB_ADS_ACTOR_ID",
    "curious_coder/facebook-ads-library-scraper",
)
DEFAULT_RUN_TIMEOUT_SECS = int(os.getenv("APIFY_RUN_TIMEOUT_SECS", "900"))


class FacebookAd(BaseModel):
    """Normalised view of one ad from the Apify response.

    The raw Apify payload varies by actor version — `_normalise` flattens both
    snake_case and camelCase field names into this stable schema so downstream
    code (longevity scorer, hook extractor, PDF builder) is robust to upgrades.

    V3 additions (for the brand-immersive pitch deck): the renderer can use
    real ad imagery and brand profile signals when they are present, so the
    normaliser now preserves what the actor typically provides under
    `snapshot.{images,videos,page_profile_*,title,cta_type}` plus the
    Meta Ads Library archive URL. All new fields are optional - any
    consumer that ignores them keeps working as before.
    """
    ad_archive_id: str
    page_name: Optional[str] = None
    page_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None       # None => still active
    is_active: bool = True
    media_type: Optional[str] = None          # IMAGE | VIDEO | CAROUSEL | DCO
    body_text: Optional[str] = None
    title: Optional[str] = None               # ad headline / link headline
    cta_text: Optional[str] = None
    cta_type: Optional[str] = None            # e.g. SHOP_NOW, LEARN_MORE
    link_url: Optional[str] = None
    snapshot_url: Optional[str] = None
    ad_library_url: Optional[str] = None      # public Meta Ads Library link
    # Media: separate "primary" + full list so downstream renderers can
    # pick the first available without iterating, and still fall back to
    # the full list if they want a carousel grid later.
    image_url: Optional[str] = None
    image_urls: list[str] = Field(default_factory=list)
    video_url: Optional[str] = None
    video_preview_image_url: Optional[str] = None
    # Brand-level signals (same for every ad from one page, but easier
    # to carry per-ad than rebuild a parallel page table).
    page_profile_picture_url: Optional[str] = None
    page_profile_uri: Optional[str] = None


class FbAdsScrapeInput(BaseModel):
    """Schema the LLM sees when deciding to call this tool."""
    competitor_pages: list[str] = Field(
        ...,
        description=(
            "Competitor brand/page names, e.g. ['Aldi', 'Lidl'] or exact "
            "Facebook page slugs ['aldibelgium']. 1-10 entries."
        ),
        min_length=1,
        max_length=10,
    )
    country: str = Field(
        "BE",
        description="ISO 3166-1 alpha-2 country code that scopes the ads library.",
        pattern=r"^[A-Z]{2}$",
    )
    max_ads_per_page: int = Field(
        50,
        description="Maximum ads to fetch per competitor page.",
        ge=1,
        le=200,
    )
    active_only: bool = Field(
        True,
        description="If true, only return ads currently running (best for longevity scoring).",
    )


def _build_ads_library_url(page_query: str, country: str, active_only: bool) -> str:
    status = "active" if active_only else "all"
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status={status}&ad_type=all&country={country}"
        f"&q={quote_plus(page_query)}&search_type=keyword_unordered"
    )


def _parse_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Apify often emits epoch seconds for date fields.
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _first(raw: dict, *keys: str) -> Any:
    for k in keys:
        if k in raw and raw[k] is not None:
            return raw[k]
    return None


def _extract_image_urls(snapshot: dict) -> list[str]:
    """Pull every plausible image URL out of an Apify snapshot.

    The actor returns `snapshot.images` as a list of dicts with mixed
    snake_case / camelCase keys (`original_image_url`,
    `originalImageURL`, `resized_image_url`, …). We accept any of them
    and dedupe while preserving order so the first entry stays usable
    as the 'hero' image for the pitch deck.
    """
    images = snapshot.get("images") or []
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(images, list):
        for entry in images:
            if not isinstance(entry, dict):
                continue
            for key in (
                "original_image_url",
                "originalImageURL",
                "resized_image_url",
                "resizedImageURL",
                "watermarked_resized_image_url",
                "watermarkedResizedImageURL",
                "url",
            ):
                url = entry.get(key)
                if isinstance(url, str) and url and url not in seen:
                    out.append(url)
                    seen.add(url)
                    break
    return out


def _extract_video_assets(snapshot: dict) -> tuple[Optional[str], Optional[str]]:
    """Return (video_url, video_preview_image_url) from the first video
    asset in the snapshot, or (None, None)."""
    videos = snapshot.get("videos") or []
    if not isinstance(videos, list):
        return (None, None)
    for entry in videos:
        if not isinstance(entry, dict):
            continue
        v_url = _first(
            entry,
            "video_hd_url",
            "videoHDURL",
            "video_sd_url",
            "videoSDURL",
            "url",
        )
        v_preview = _first(
            entry,
            "video_preview_image_url",
            "videoPreviewImageURL",
            "preview_image_url",
        )
        if v_url or v_preview:
            return (v_url, v_preview)
    return (None, None)


def _normalise(raw: dict) -> FacebookAd:
    snapshot = raw.get("snapshot") or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    body = snapshot.get("body") if isinstance(snapshot.get("body"), dict) else {}
    is_active_raw = _first(raw, "is_active", "isActive")

    image_urls = _extract_image_urls(snapshot)
    video_url, video_preview = _extract_video_assets(snapshot)
    ad_archive_id = str(_first(raw, "ad_archive_id", "adArchiveId", "id") or "")

    # Public Meta Ads Library URL. Either the actor provides it
    # directly (varies by version) or we derive it from the archive ID
    # so the V3 pitch deck always has a clickable proof link.
    ad_library_url = _first(raw, "ad_library_url", "adLibraryURL", "url")
    if not ad_library_url and ad_archive_id:
        ad_library_url = f"https://www.facebook.com/ads/library/?id={ad_archive_id}"

    # V4: media_type inference. The actor occasionally returns
    # display_format on the snapshot rather than the top-level row, and
    # for ads where it's missing entirely we can infer from
    # snapshot.images/snapshot.videos contents. Knowing media_type lets
    # the pitch deck's gap-map fire its 'image-only library' / 'no
    # video' rows correctly even when the actor's display_format field
    # is empty.
    media_type_raw = _first(raw, "display_format", "displayFormat", "mediaType")
    if not media_type_raw:
        media_type_raw = snapshot.get("display_format") or snapshot.get("displayFormat")
    media_type = (str(media_type_raw).upper() or None) if media_type_raw else None
    if media_type is None:
        # Infer from media presence in the snapshot.
        if video_url or video_preview:
            media_type = "VIDEO"
        elif image_urls:
            media_type = "IMAGE"

    return FacebookAd(
        ad_archive_id=ad_archive_id,
        page_name=_first(raw, "page_name", "pageName") or snapshot.get("page_name"),
        page_id=_first(raw, "page_id", "pageId"),
        start_date=_parse_date(_first(raw, "start_date", "startDate", "ad_delivery_start_time")),
        end_date=_parse_date(_first(raw, "end_date", "endDate", "ad_delivery_stop_time")),
        is_active=bool(is_active_raw) if is_active_raw is not None else True,
        media_type=media_type,
        body_text=_first(raw, "body_text", "ad_body", "body") or body.get("text"),
        title=snapshot.get("title") or _first(raw, "title"),
        cta_text=_first(raw, "cta_text", "ctaText") or snapshot.get("cta_text"),
        cta_type=_first(raw, "cta_type", "ctaType") or snapshot.get("cta_type"),
        link_url=_first(raw, "link_url", "linkUrl") or snapshot.get("link_url"),
        snapshot_url=_first(raw, "snapshot_url", "snapshotURL"),
        ad_library_url=ad_library_url,
        image_url=image_urls[0] if image_urls else None,
        image_urls=image_urls,
        video_url=video_url,
        video_preview_image_url=video_preview,
        page_profile_picture_url=(
            snapshot.get("page_profile_picture_url")
            or _first(raw, "page_profile_picture_url", "pageProfilePictureURL")
        ),
        page_profile_uri=(
            snapshot.get("page_profile_uri")
            or _first(raw, "page_profile_uri", "pageProfileURI")
        ),
    )


def make_fb_ads_search_tool(actor_id: str = DEFAULT_ACTOR_ID):
    """Factory so the actor ID can be swapped per-client or per-test."""

    @tool("search_fb_ads_library", args_schema=FbAdsScrapeInput)
    def search_fb_ads_library(
        competitor_pages: list[str],
        country: str = "BE",
        max_ads_per_page: int = 50,
        active_only: bool = True,
    ) -> list[dict]:
        """Scrape the Meta (Facebook + Instagram) Ads Library for ads from the
        given competitor pages. Returns one row per ad with start date, media
        type, body text, and CTA. Call this when you need to discover competitor
        hooks or measure ad longevity in the market.
        """
        token = os.getenv("APIFY_API_TOKEN")
        if not token:
            raise RuntimeError(
                "APIFY_API_TOKEN is not set. Add it to .env before running the Strategist."
            )

        client = ApifyClient(token)
        # V4 asset-pipeline: explicitly enable `scrapeAdDetails`.
        # The actor's input schema gates the full per-ad snapshot
        # (images[], videos[], page_profile_*, snapshot_url, link_url,
        # cta_*, title) behind this flag - without it, the dataset only
        # carries id/body/timing fields and our normaliser cannot
        # populate brand/ad imagery for the pitch deck. The flag is
        # idempotent and adds no extra paid runs to the actor.
        run_input = {
            "urls": [
                {"url": _build_ads_library_url(p, country, active_only)}
                for p in competitor_pages
            ],
            "count": max_ads_per_page,
            "scrapeAdDetails": True,
            "scrapePageAds.activeStatus": "active" if active_only else "all",
            "scrapePageAds.countryCode": country,
            # `country` is not in the actor schema but historically helped
            # disambiguate the Ads Library URL; keep it as a no-op tag for
            # operator logs.
            "country": country,
        }

        run = client.actor(actor_id).call(
            run_input=run_input,
            timeout_secs=DEFAULT_RUN_TIMEOUT_SECS,
        )
        if not run or "defaultDatasetId" not in run:
            raise RuntimeError(f"Apify actor {actor_id!r} returned no dataset.")

        dataset = client.dataset(run["defaultDatasetId"])
        out = [_normalise(item).model_dump(mode="json") for item in dataset.iterate_items()]
        # Pass 2.2 cost ledger: success-only. The list comprehension above
        # materialised every ad without raising, and `run` had a dataset
        # ID. We can commit a single scrape event before returning.
        # record_event is internally safe.
        cost_ledger.record_event(
            provider="apify",
            event_type="ads_library_scrape",
            units=float(len(out)),
            estimated_cost_eur=cost_ledger.APIFY_SCRAPE_EUR_PER_AD * len(out),
            metadata={
                "actor_id": actor_id,
                "country": country,
                "max_ads_per_page": max_ads_per_page,
                "active_only": active_only,
                "competitor_pages_n": len(competitor_pages),
            },
        )
        return out

    return search_fb_ads_library
