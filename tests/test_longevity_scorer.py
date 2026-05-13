"""Smoke tests for the longevity scorer (Phase 2).

Covers the business rule (drop ads < 14 days), end-date handling for paused
ads, sort order, and date-format robustness against the variants the Apify
normaliser may produce (ISO string, ISO with Z, datetime object, epoch float).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from agents.strategist.analysis.longevity_scorer import score_ads

NOW = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)


def _ad(days_ago: Optional[int], end_days_ago: Optional[int] = None, **extra) -> dict:
    """Build a fake ad dict with start_date `days_ago` days before NOW.

    Pass days_ago=None to omit the start_date entirely.
    """
    start = (NOW - timedelta(days=days_ago)).isoformat() if days_ago is not None else None
    end = (NOW - timedelta(days=end_days_ago)).isoformat() if end_days_ago is not None else None
    return {"start_date": start, "end_date": end, **extra}


def test_drops_ads_under_14_days():
    ads = [_ad(days_ago=10, ad_archive_id="A"), _ad(days_ago=30, ad_archive_id="B")]
    result = score_ads(ads, now=NOW)
    assert len(result) == 1
    assert result[0]["ad_archive_id"] == "B"
    assert result[0]["days_active"] == 30


def test_keeps_ad_at_exact_14_day_boundary():
    ads = [_ad(days_ago=14, ad_archive_id="A")]
    result = score_ads(ads, now=NOW)
    assert len(result) == 1
    assert result[0]["days_active"] == 14


def test_drops_ads_with_no_start_date():
    ads = [_ad(days_ago=None, ad_archive_id="A")]
    assert score_ads(ads, now=NOW) == []


def test_paused_ad_uses_end_date_for_longevity():
    # Started 100 days ago, paused 60 days ago -> 40-day run.
    ads = [_ad(days_ago=100, end_days_ago=60, ad_archive_id="A")]
    result = score_ads(ads, now=NOW)
    assert len(result) == 1
    assert result[0]["days_active"] == 40


def test_results_sorted_by_longevity_desc():
    ads = [
        _ad(days_ago=20, ad_archive_id="A"),
        _ad(days_ago=80, ad_archive_id="B"),
        _ad(days_ago=45, ad_archive_id="C"),
    ]
    result = score_ads(ads, now=NOW)
    assert [r["ad_archive_id"] for r in result] == ["B", "C", "A"]


def test_min_days_threshold_is_configurable():
    ads = [_ad(days_ago=20, ad_archive_id="A"), _ad(days_ago=40, ad_archive_id="B")]
    result = score_ads(ads, min_days=30, now=NOW)
    assert [r["ad_archive_id"] for r in result] == ["B"]


def test_accepts_epoch_seconds_as_start_date():
    epoch = (NOW - timedelta(days=30)).timestamp()
    ads = [{"start_date": epoch, "ad_archive_id": "A"}]
    result = score_ads(ads, now=NOW)
    assert result[0]["days_active"] == 30


def test_iso_string_with_z_suffix_parses():
    iso_z = (NOW - timedelta(days=25)).isoformat().replace("+00:00", "Z")
    ads = [{"start_date": iso_z, "ad_archive_id": "A"}]
    result = score_ads(ads, now=NOW)
    assert result[0]["days_active"] == 25


def test_native_datetime_start_date():
    ads = [{"start_date": NOW - timedelta(days=22), "ad_archive_id": "A"}]
    result = score_ads(ads, now=NOW)
    assert result[0]["days_active"] == 22


def test_empty_input_returns_empty():
    assert score_ads([], now=NOW) == []


def test_extra_fields_are_preserved():
    ads = [_ad(days_ago=30, ad_archive_id="A", body_text="hello world", media_type="VIDEO")]
    result = score_ads(ads, now=NOW)
    assert result[0]["body_text"] == "hello world"
    assert result[0]["media_type"] == "VIDEO"


def test_unparseable_string_is_dropped():
    ads = [{"start_date": "not-a-date", "ad_archive_id": "A"}]
    assert score_ads(ads, now=NOW) == []
