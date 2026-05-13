"""Meta Ads Insights API wrapper - pure data layer (V1.1 refactor).

The older make_meta_insights_tool factory was deprecated in V1.1: the LLM
no longer routes raw insights JSON between tools. The new
analyze_campaign_performance tool (in context_writer.py) calls
fetch_meta_insights_data() internally and returns only a compressed text
summary to the LLM.

Parses the agency-standard ad_name convention
[Audience]_[WH-xxx]_[RM-yyy]_[Notes] to extract hook + motion tags.
Results are capped at MAX_ADS_RETURNED (200) and sorted by spend desc.

Env-overridable:
  META_GRAPH_VERSION       (default: v19.0)
  META_API_BASE_URL        (derived from version if unset)
  META_MAX_ADS_RETURNED    (default: 200)
"""
from __future__ import annotations

import os
import re
from typing import Optional

import requests


META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v19.0")
META_API_BASE = os.getenv(
    "META_API_BASE_URL",
    f"https://graph.facebook.com/{META_GRAPH_VERSION}",
)
HTTP_TIMEOUT_SEC = 60
MAX_ADS_RETURNED = int(os.getenv("META_MAX_ADS_RETURNED", "200"))

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
    """Parse [Audience]_[WH-xxx]_[RM-yyy]_[Notes] -> (audience, hook_id, motion_id)."""
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
    """Meta returns purchase_roas as [{action_type, value}, ...]. Prefer
    omni_purchase > offsite_conversion.fb_pixel_purchase > anything parseable."""
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


def fetch_meta_insights_data(time_preset: str = "last_14d") -> list[dict]:
    """Pull ad-level performance from Meta Ads Insights API. Returns at most
    MAX_ADS_RETURNED rows, sorted by spend descending. Each row carries
    spend, impressions, ctr, purchase_roas, and the hook_id / motion_id
    parsed from ad_name per the agency convention
    [Audience]_[WH-xxx]_[RM-yyy]_[Notes].

    Plain Python function - NOT a LangChain tool. Called internally by the
    Analyst's analyze_campaign_performance tool so the raw insights array
    never crosses the LLM context boundary.
    """
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
        "sort": "spend_descending",
    }

    rows: list[dict] = []
    next_url: Optional[str] = url
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
        next_url = (payload.get("paging") or {}).get("next")
        params = None

    rows.sort(key=lambda r: r.get("spend") or 0, reverse=True)
    return rows[:MAX_ADS_RETURNED]
