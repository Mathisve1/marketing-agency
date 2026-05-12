"""Longevity scorer — import-stable stub.

Full implementation lands in Phase 2 (2/2): compute `days_active` = (now -
start_date), drop ads under min_days (default 14, i.e. unproven), and sort
descending so the LLM sees the strongest signal first.
"""
from __future__ import annotations


def score_ads(ads: list[dict], min_days: int = 14) -> list[dict]:
    """Filter and rank Facebook ads by days running. Returns ads with a
    `days_active` field added, sorted desc."""
    raise NotImplementedError(
        "longevity_scorer not yet implemented — pending Phase 2 (2/2)."
    )
