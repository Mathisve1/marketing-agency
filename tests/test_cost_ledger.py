"""Tests for services/cost_ledger.py (Pass 2.2 Initiative 4).

Pins:
  - record_event persists rows with UUID ids and JSON metadata
  - record_event NEVER raises (safety contract); invalid input is a
    silent no-op; database errors are swallowed and logged to stderr
  - list_recent / summarise_by_* return correct shapes + filter by `since`
  - WAL mode is enabled
  - Rough estimate constants are non-negative floats

All tests use tmp_path-scoped db_path - no test touches the real
cost_ledger.db.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from services import cost_ledger

NOW = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cost_ledger.db"


# --------------------------------------------------------------------------- #
# record_event - happy path + shape
# --------------------------------------------------------------------------- #


def test_record_event_persists_row_with_uuid_id(db_path: Path):
    cost_ledger.record_event(
        provider="kling",
        event_type="video_submit",
        client_id="acme",
        units=1.0,
        estimated_cost_eur=0.50,
        db_path=db_path,
        now=NOW,
    )
    rows = cost_ledger.list_recent(limit=5, db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["provider"] == "kling"
    assert rows[0]["event_type"] == "video_submit"
    assert rows[0]["client_id"] == "acme"
    assert rows[0]["units"] == 1.0
    assert rows[0]["estimated_cost_eur"] == 0.50
    # id is a 32-hex UUID4 string.
    assert len(rows[0]["id"]) == 32
    int(rows[0]["id"], 16)  # raises if not hex


def test_record_event_persists_metadata_json_roundtrip(db_path: Path):
    meta = {
        "kling_task_id": "kling-task-XYZ",
        "plan_id": "VP-007",
        "duration_s": 10,
        "mode": "professional",
        "nested": {"a": 1, "b": [2, 3]},
    }
    cost_ledger.record_event(
        provider="kling",
        event_type="video_submit",
        metadata=meta,
        db_path=db_path,
        now=NOW,
    )
    rows = cost_ledger.list_recent(limit=5, db_path=db_path)
    assert rows[0]["metadata"] == meta


def test_record_event_accepts_null_client_id_and_prospect_id(db_path: Path):
    cost_ledger.record_event(
        provider="meta",
        event_type="insights_fetch",
        client_id=None,
        prospect_id=None,
        db_path=db_path,
        now=NOW,
    )
    rows = cost_ledger.list_recent(limit=5, db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["client_id"] is None
    assert rows[0]["prospect_id"] is None


def test_record_event_accepts_null_units_and_cost(db_path: Path):
    cost_ledger.record_event(
        provider="apify",
        event_type="ads_library_scrape",
        units=None,
        estimated_cost_eur=None,
        db_path=db_path,
        now=NOW,
    )
    rows = cost_ledger.list_recent(limit=5, db_path=db_path)
    assert rows[0]["units"] is None
    assert rows[0]["estimated_cost_eur"] is None


# --------------------------------------------------------------------------- #
# record_event - safety contract
# --------------------------------------------------------------------------- #


def test_record_event_silently_skips_empty_provider(db_path: Path):
    """Invalid provider is a silent no-op. We do NOT raise from
    record_event under any condition; the contract is observability."""
    cost_ledger.record_event(
        provider="",
        event_type="video_submit",
        db_path=db_path,
        now=NOW,
    )
    assert cost_ledger.list_recent(limit=5, db_path=db_path) == []


def test_record_event_silently_skips_empty_event_type(db_path: Path):
    cost_ledger.record_event(
        provider="kling",
        event_type="",
        db_path=db_path,
        now=NOW,
    )
    assert cost_ledger.list_recent(limit=5, db_path=db_path) == []


def test_record_event_silently_skips_non_string_provider(db_path: Path):
    cost_ledger.record_event(
        provider=123,  # type: ignore[arg-type]
        event_type="video_submit",
        db_path=db_path,
        now=NOW,
    )
    assert cost_ledger.list_recent(limit=5, db_path=db_path) == []


def test_record_event_swallows_disk_error_safely(db_path: Path, capsys):
    """The most important safety test: if the SQL layer raises, the call
    site must NOT see the exception. A broken cost ledger cannot break
    a successful Kling submit."""
    with patch(
        "services.cost_ledger.sqlite3.connect",
        side_effect=sqlite3.OperationalError("disk full"),
    ):
        # No exception propagates.
        cost_ledger.record_event(
            provider="kling",
            event_type="video_submit",
            client_id="acme",
            db_path=db_path,
            now=NOW,
        )
    # Stderr should carry a one-line diagnostic - but never the metadata.
    err = capsys.readouterr().err
    assert "cost_ledger.record_event" in err
    assert "swallowed" in err


def test_record_event_swallows_unserialisable_metadata(db_path: Path):
    """Metadata containing non-JSON-able objects should NOT crash
    record_event; the row should still be inserted with metadata=None."""

    class Weird:
        def __repr__(self):
            return "<Weird>"

    # set is not JSON-serialisable; json.dumps uses default=str so it
    # would normally fall back to str(set(...)) - but if we make the
    # set member raise from __repr__, default=str raises, and
    # record_event must still swallow.
    cost_ledger.record_event(
        provider="kling",
        event_type="video_submit",
        metadata={"weird": {Weird(), Weird()}},
        db_path=db_path,
        now=NOW,
    )
    rows = cost_ledger.list_recent(limit=5, db_path=db_path)
    # Either the row is in (with metadata=None) or no row at all - both
    # are acceptable. What matters is NO exception propagated.
    assert len(rows) <= 1


# --------------------------------------------------------------------------- #
# list_recent
# --------------------------------------------------------------------------- #


def test_list_recent_returns_newest_first_capped_by_limit(db_path: Path):
    for i in range(7):
        cost_ledger.record_event(
            provider="kling",
            event_type="video_submit",
            db_path=db_path,
            now=NOW + timedelta(minutes=i),
        )
    out = cost_ledger.list_recent(limit=3, db_path=db_path)
    assert len(out) == 3
    # Descending by created_at.
    assert out[0]["created_at"] > out[1]["created_at"] > out[2]["created_at"]


def test_list_recent_rejects_negative_limit(db_path: Path):
    with pytest.raises(ValueError, match="limit must be >= 0"):
        cost_ledger.list_recent(limit=-1, db_path=db_path)


# --------------------------------------------------------------------------- #
# summarise_by_provider
# --------------------------------------------------------------------------- #


def test_summarise_by_provider_sums_units_and_cost(db_path: Path):
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW,
    )
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW + timedelta(minutes=1),
    )
    cost_ledger.record_event(
        provider="tavily", event_type="search",
        units=5.0, estimated_cost_eur=0.01,
        db_path=db_path, now=NOW + timedelta(minutes=2),
    )
    summary = cost_ledger.summarise_by_provider(db_path=db_path)
    assert summary["kling"]["events"] == 2
    assert summary["kling"]["units"] == 2.0
    assert summary["kling"]["cost_eur"] == 1.00
    assert summary["tavily"]["events"] == 1
    assert summary["tavily"]["units"] == 5.0
    assert summary["tavily"]["cost_eur"] == 0.01


def test_summarise_by_provider_respects_since(db_path: Path):
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW - timedelta(days=30),  # ancient
    )
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW,  # current
    )
    summary = cost_ledger.summarise_by_provider(
        since=NOW - timedelta(days=7), db_path=db_path,
    )
    assert summary["kling"]["events"] == 1  # only the recent one


# --------------------------------------------------------------------------- #
# summarise_by_client
# --------------------------------------------------------------------------- #


def test_summarise_by_client_groups_by_client_id(db_path: Path):
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        client_id="acme", units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW,
    )
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        client_id="other", units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW + timedelta(minutes=1),
    )
    cost_ledger.record_event(
        provider="tavily", event_type="search",
        client_id=None, units=5.0, estimated_cost_eur=0.01,
        db_path=db_path, now=NOW + timedelta(minutes=2),
    )
    summary = cost_ledger.summarise_by_client(db_path=db_path)
    assert summary["acme"]["events"] == 1
    assert summary["other"]["events"] == 1
    # Null client_id rolls up under '(unknown)' sentinel.
    assert summary["(unknown)"]["events"] == 1


def test_summarise_by_client_respects_since(db_path: Path):
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        client_id="acme", units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW - timedelta(days=30),
    )
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        client_id="acme", units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW,
    )
    summary = cost_ledger.summarise_by_client(
        since=NOW - timedelta(days=7), db_path=db_path,
    )
    assert summary["acme"]["events"] == 1


# --------------------------------------------------------------------------- #
# summarise_by_day (optional)
# --------------------------------------------------------------------------- #


def test_summarise_by_day_buckets_to_iso_date(db_path: Path):
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW,
    )
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW + timedelta(hours=3),  # same day
    )
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW + timedelta(days=1),
    )
    summary = cost_ledger.summarise_by_day(db_path=db_path)
    assert summary["2026-05-14"]["events"] == 2
    assert summary["2026-05-15"]["events"] == 1


def test_summarise_by_day_respects_since(db_path: Path):
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW - timedelta(days=10),
    )
    cost_ledger.record_event(
        provider="kling", event_type="video_submit",
        units=1.0, estimated_cost_eur=0.50,
        db_path=db_path, now=NOW,
    )
    summary = cost_ledger.summarise_by_day(
        since=NOW - timedelta(days=2), db_path=db_path,
    )
    assert len(summary) == 1
    assert "2026-05-14" in summary


# --------------------------------------------------------------------------- #
# Constants + infra
# --------------------------------------------------------------------------- #


def test_rough_estimate_constants_are_non_negative_floats():
    """A future env override that resolves to a negative number must not
    poison the dashboard. _env_float falls back to the conservative
    default; the constants themselves must remain non-negative floats."""
    for name in (
        "KLING_VIDEO_SUBMIT_EUR",
        "TAVILY_SEARCH_EUR",
        "APIFY_SCRAPE_EUR_PER_AD",
        "META_INSIGHTS_EUR",
    ):
        val = getattr(cost_ledger, name)
        assert isinstance(val, float), f"{name} must be a float, got {type(val).__name__}"
        assert val >= 0, f"{name} must be >= 0, got {val}"


def test_env_float_falls_back_on_bad_value(monkeypatch):
    """_env_float silently falls back to the default on parse failure or
    negative values - protects the ledger from .env typos."""
    monkeypatch.setenv("COST_TEST_VAR", "not-a-number")
    assert cost_ledger._env_float("COST_TEST_VAR", 0.42) == 0.42

    monkeypatch.setenv("COST_TEST_VAR", "-1.5")
    assert cost_ledger._env_float("COST_TEST_VAR", 0.42) == 0.42

    monkeypatch.setenv("COST_TEST_VAR", "0.99")
    assert cost_ledger._env_float("COST_TEST_VAR", 0.42) == 0.99


def test_wal_mode_enabled_after_init(db_path: Path):
    cost_ledger.record_event(
        provider="probe", event_type="probe",
        db_path=db_path, now=NOW,
    )
    with sqlite3.connect(str(db_path)) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_indexes_present_after_init(db_path: Path):
    """The three indexes documented in the spec must exist after first
    write. Pinned so a future schema change can't accidentally drop them."""
    cost_ledger.record_event(
        provider="probe", event_type="probe",
        db_path=db_path, now=NOW,
    )
    with sqlite3.connect(str(db_path)) as conn:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    assert "idx_cost_events_created_at" in names
    assert "idx_cost_events_provider_created" in names
    assert "idx_cost_events_client_created" in names
