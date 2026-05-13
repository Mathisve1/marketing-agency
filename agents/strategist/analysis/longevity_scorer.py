"""Longevity scorer: filter and rank ads by how long they've been running.

Business rule from the Strategist brief: ads under 14 days are filtered
out as below the longevity-signal threshold. Long-running ads are a market
SIGNAL worth studying - not proof of performance. Surviving ads get a
`days_active` field and are sorted descending.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _to_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def score_ads(
    ads: list[dict],
    min_days: int = 14,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Filter and rank ads by longevity.

    Drops:
      - Ads without a parseable start_date.
      - Ads under `min_days` (default 14) - treated as unproven.

    For ads with an end_date (paused ads), longevity is `end - start`. For ads
    still running, longevity is `now - start`. Returns surviving ads with a
    `days_active: int` field added, sorted desc.
    """
    now = now or datetime.now(timezone.utc)
    scored: list[dict] = []
    for ad in ads:
        start = _to_datetime(ad.get("start_date"))
        if start is None:
            continue
        # If the ad has an end_date (paused/finished), longevity caps there;
        # otherwise the ad is still running and we measure to now.
        end = _to_datetime(ad.get("end_date")) or now
        days_active = max(0, (end - start).days)
        if days_active < min_days:
            continue
        scored.append({**ad, "days_active": days_active})
    scored.sort(key=lambda x: x["days_active"], reverse=True)
    return scored
