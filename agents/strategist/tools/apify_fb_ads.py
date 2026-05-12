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
    """
    ad_archive_id: str
    page_name: Optional[str] = None
    page_id: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None       # None => still active
    is_active: bool = True
    media_type: Optional[str] = None          # IMAGE | VIDEO | CAROUSEL | DCO
    body_text: Optional[str] = None
    cta_text: Optional[str] = None
    link_url: Optional[str] = None
    snapshot_url: Optional[str] = None


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


def _normalise(raw: dict) -> FacebookAd:
    snapshot = raw.get("snapshot") or {}
    body = snapshot.get("body") if isinstance(snapshot.get("body"), dict) else {}
    is_active_raw = _first(raw, "is_active", "isActive")
    return FacebookAd(
        ad_archive_id=str(_first(raw, "ad_archive_id", "adArchiveId", "id") or ""),
        page_name=_first(raw, "page_name", "pageName"),
        page_id=_first(raw, "page_id", "pageId"),
        start_date=_parse_date(_first(raw, "start_date", "startDate", "ad_delivery_start_time")),
        end_date=_parse_date(_first(raw, "end_date", "endDate", "ad_delivery_stop_time")),
        is_active=bool(is_active_raw) if is_active_raw is not None else True,
        media_type=(str(_first(raw, "display_format", "displayFormat", "mediaType") or "").upper() or None),
        body_text=_first(raw, "body_text", "ad_body", "body") or body.get("text"),
        cta_text=_first(raw, "cta_text", "ctaText"),
        link_url=_first(raw, "link_url", "linkUrl"),
        snapshot_url=_first(raw, "snapshot_url", "snapshotURL"),
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
        run_input = {
            "urls": [
                {"url": _build_ads_library_url(p, country, active_only)}
                for p in competitor_pages
            ],
            "count": max_ads_per_page,
            "scrapePageAds.activeStatus": "active" if active_only else "all",
            "country": country,
        }

        run = client.actor(actor_id).call(
            run_input=run_input,
            timeout_secs=DEFAULT_RUN_TIMEOUT_SECS,
        )
        if not run or "defaultDatasetId" not in run:
            raise RuntimeError(f"Apify actor {actor_id!r} returned no dataset.")

        dataset = client.dataset(run["defaultDatasetId"])
        return [_normalise(item).model_dump(mode="json") for item in dataset.iterate_items()]

    return search_fb_ads_library
