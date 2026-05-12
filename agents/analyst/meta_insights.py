"""Meta Ads Insights API wrapper.

Queries /v<version>/act_<AD_ACCOUNT_ID>/insights for ad-level performance
metrics (spend, impressions, CTR, ROAS) and parses the agency-standard
ad_name convention [Audience]_[WH-xxx]_[RM-yyy]_[Notes] to extract the
hook_id and motion_id tags used in MASTER_CONTEXT.md.

Returns are capped at MAX_ADS_RETURNED (200) and sorted by spend descending
so the LLM gets the most relevant signal without context-window blowout.

The Graph API version and base URL are env-overridable so Meta's quarterly
version deprecations don't require a code release:
  META_GRAPH_VERSION  (default: v19.0 - update to match your token's version)
  META_API_BASE_URL   (derived from version if unset)
"""
from __future__ import annotations

import os
import re
from typing import Optional

import requests
from langchain_core.tools import tool
from pydantic import BaseModel, Field


META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v19.0")
META_API_BASE = os.getenv(
    "META_API_BASE_URL",
    f"https://graph.facebook.com/{META_GRAPH_VERSION}",
)
HTTP_TIMEOUT_SEC = 60

# Hard cap on rows returned to the LLM. After pagination + sort by spend desc,
# we slice to this many rows. Prevents context-window blowout for accounts
# with hundreds of active ads in a 14-day window.
MAX_ADS_RETURNED = int(os.getenv("META_MAX_ADS_RETURNED", "200"))

# Whitelist of Meta time_preset values per Graph API docs.
SUPPORTED_TIME_PRESETS = {
    "today", "yesterday",
    "this_week_mon_today", "this_week_sun_today",
    "last_week_mon_sun", "last_week_sun_sat",
    "this_month", "last_month",
    "this_quarter", "last_quarter",
    "this_year", "last_year",
    "lifetime",
    "last_3d", "last_7d", "last_14d", "last_28d", "last_30d", "last_90d",
}

# Common informal names users write - map to Meta's canonical values.
TIME_PRESET_ALIASES = {
    "last_3_days": "last_3d",
    "last_7_days": "last_7d",
    "last_14_days": "last_14d",
    "last_28_days": "last_28d",
    "last_30_days": "last_30d",
    "last_90_days": "last_90d",
}

INSIGHT_FIELDS = [
    "ad_id", "ad_name", "spend", "impressions",
    "inline_link_clicks", "clicks", "purchase_roas",
]

_HOOK_ID_RX = re.compile(r"(WH-\d+)")
_MOTION_ID_RX = re.compile(r"(RM-\d+)")


def _parse_ad_name(ad_name: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse the agency convention [Audience]_[WH-xxx]_[RM-yyy]_[Notes].

    Returns (audience_name, hook_id, motion_id). Any may be None if the
    media buyer didn't follow the convention for that ad.
    """
    if not ad_name:
        return None, None, None
    hook = _HOOK_ID_RX.search(ad_name)
    motion = _MOTION_ID_RX.search(ad_name)
    audience = ad_name.split("_")[0] if "_" in ad_name else None
    return (
        audience or None,
        hook.group(1) if hook else None,
        motion.group(1) if motion else None,
    )


def _extract_purchase_roas(raw: Optional[list]) -> Optional[float]:
    """Meta returns purchase_roas as [{action_type, value}, ...]. Pick the
    most representative number: omni_purchase > offsite_conversion.fb_pixel
    > any parseable. Returns None when no usable value exists."""
    if not raw:
        return None
    by_type = {item.get("action_type"): item.get("value") for item in raw}
    for preferred in ("omni_purchase", "offsite_conversion.fb_pixel_purchase", "purchase"):
        if preferred in by_type:
            try:
                return float(by_type[preferred])
            except (TypeError, ValueError):
                continue
    for item in raw:
        try:
            return float(item.get("value"))
        except (TypeError, ValueError):
            continue
    return None


def _normalise_time_preset(raw: str) -> str:
    if raw in SUPPORTED_TIME_PRESETS:
        return raw
    if raw in TIME_PRESET_ALIASES:
        return TIME_PRESET_ALIASES[raw]
    raise ValueError(
        f"Unsupported time_preset {raw!r}. "
        f"Supported: {', '.join(sorted(SUPPORTED_TIME_PRESETS))}."
    )


class MetaInsightsInput(BaseModel):
    time_preset: str = Field(
        "last_14d",
        description=(
            "Meta time preset. Common: last_7d, last_14d, last_28d, last_30d. "
            "Aliases like 'last_14_days' are accepted."
        ),
    )


def make_meta_insights_tool():
    """Factory matching the pattern of make_fb_ads_search_tool / make_tavily..."""

    @tool("pull_meta_insights", args_schema=MetaInsightsInput)
    def pull_meta_insights(time_preset: str = "last_14d") -> list[dict]:
        """Pull ad-level performance from Meta Ads Insights API. Returns at most
        200 rows, sorted by spend descending (highest spend first). Each row
        carries spend, impressions, ctr, purchase_roas, and the hook_id /
        motion_id parsed from ad_name per the agency convention
        [Audience]_[WH-xxx]_[RM-yyy]_[Notes]. Use this as the first step of
        any analyst run; pass the result to `evaluate_performance`."""
        access_token = os.getenv("META_ACCESS_TOKEN")
        ad_account = os.getenv("META_AD_ACCOUNT_ID")
        if not access_token or not ad_account:
            raise RuntimeError(
                "META_ACCESS_TOKEN and META_AD_ACCOUNT_ID must be in .env "
                "(consider per-client overrides in clients/<id>/.env)."
            )
        if not ad_account.startswith("act_"):
            ad_account = f"act_{ad_account}"

        normalised = _normalise_time_preset(time_preset)
        url = f"{META_API_BASE}/{ad_account}/insights"
        params: Optional[dict] = {
            "access_token": access_token,
            "level": "ad",
            "time_preset": normalised,
            "fields": ",".join(INSIGHT_FIELDS),
            "limit": min(MAX_ADS_RETURNED, 100),
            # Hint server-side sort - Meta may or may not honor it depending
            # on the API version. The client-side sort below is the actual
            # safety net.
            "sort": "spend_descending",
        }

        rows: list[dict] = []
        next_url: Optional[str] = url
        # Stop paginating once we have enough rows (with some headroom in case
        # server-side sort isn't honored, so client-side sort still picks the
        # right top-N).
        fetch_cap = MAX_ADS_RETURNED * 3
        while next_url and len(rows) < fetch_cap:
            r = requests.get(next_url, params=params, timeout=HTTP_TIMEOUT_SEC)
            if not r.ok:
                raise RuntimeError(
                    f"Meta API GET {next_url} -> {r.status_code}: {r.text}"
                )
            payload = r.json()
            for item in payload.get("data", []):
                ad_name = item.get("ad_name", "") or ""
                audience, hook_id, motion_id = _parse_ad_name(ad_name)
                impressions = int(item.get("impressions") or 0)
                clicks = int(item.get("inline_link_clicks") or 0)
                ctr = (clicks / impressions) if impressions else 0.0
                rows.append({
                    "ad_id": item.get("ad_id"),
                    "ad_name": ad_name,
                    "spend": float(item.get("spend") or 0),
                    "impressions": impressions,
                    "inline_link_clicks": clicks,
                    "ctr": round(ctr, 6),
                    "purchase_roas": _extract_purchase_roas(item.get("purchase_roas")),
                    "audience_name": audience,
                    "hook_id": hook_id,
                    "motion_id": motion_id,
                })
            # Meta's pagination: paging.next is a full URL with cursor; clear
            # params after first call so we don't double-append access_token.
            next_url = (payload.get("paging") or {}).get("next")
            params = None

        # Defensive client-side sort + slice. Server-side sort is a hint;
        # this is the actual contract with the LLM.
        rows.sort(key=lambda r: r.get("spend") or 0, reverse=True)
        return rows[:MAX_ADS_RETURNED]

    return pull_meta_insights
