"""Tests for services/mcp_pending_store.py.

Pins the dict-replacement contract so mcp_server.py can swap its
in-memory `_PENDING_RUNS` dict for this store with no behaviour change:

  - record_pending then get_pending round-trips the full payload.
  - pop_pending behaves like dict.pop(default=None): returns and removes
    on hit, returns None on miss, idempotent on miss.
  - restore_pending re-inserts a payload as pending again (used when a
    graph resume fails after the pop).
  - mark_decided keeps a terminal-state audit row.
  - list_pending / list_decided expose what the Streamlit status strip
    needs.
  - All operations isolate to the supplied db_path - no test touches
    the real mcp_pending_runs.db at repo root.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services import mcp_pending_store

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "mcp_pending_runs.db"


def _record(
    db_path: Path,
    thread_id: str = "mcp-acme-abc12345",
    client_id: str = "acme",
    plan_id: str = "VP-007",
) -> None:
    mcp_pending_store.record_pending(
        thread_id,
        client_id=client_id,
        model="claude-sonnet-4-6",
        prompt="Produce a video using hook WH-003.",
        task_type="produce",
        plan_id=plan_id,
        config={"configurable": {"thread_id": thread_id, "model": "claude-sonnet-4-6"}},
        db_path=db_path,
    )


# --------------------------------------------------------------------------- #
# record + get round-trip
# --------------------------------------------------------------------------- #


def test_record_then_get_roundtrips_full_payload(db_path: Path):
    _record(db_path)
    got = mcp_pending_store.get_pending("mcp-acme-abc12345", db_path=db_path)
    assert got is not None
    assert got["thread_id"] == "mcp-acme-abc12345"
    assert got["client_id"] == "acme"
    assert got["model"] == "claude-sonnet-4-6"
    assert got["plan_id"] == "VP-007"
    assert got["task_type"] == "produce"
    assert got["status"] == "pending"
    assert got["created_at"]
    assert got["decided_at"] is None
    assert got["decided_by"] is None
    # config came back as a real dict (JSON-roundtripped), not a string.
    assert got["config"] == {
        "configurable": {"thread_id": "mcp-acme-abc12345", "model": "claude-sonnet-4-6"}
    }


def test_get_pending_returns_none_for_missing_thread(db_path: Path):
    assert mcp_pending_store.get_pending("does-not-exist", db_path=db_path) is None


def test_record_is_upsert_on_thread_id(db_path: Path):
    """If MCP somehow re-records the same thread_id (rare retry path),
    the second record must overwrite cleanly rather than crash on
    UNIQUE constraint."""
    _record(db_path, plan_id="VP-001")
    _record(db_path, plan_id="VP-002")
    got = mcp_pending_store.get_pending("mcp-acme-abc12345", db_path=db_path)
    assert got["plan_id"] == "VP-002"


# --------------------------------------------------------------------------- #
# pop semantics - mirrors dict.pop(k, None)
# --------------------------------------------------------------------------- #


def test_pop_pending_returns_payload_and_deletes(db_path: Path):
    _record(db_path)
    payload = mcp_pending_store.pop_pending("mcp-acme-abc12345", db_path=db_path)
    assert payload is not None
    assert payload["plan_id"] == "VP-007"
    # Second pop must return None, not the same payload.
    assert mcp_pending_store.pop_pending("mcp-acme-abc12345", db_path=db_path) is None
    # And get_pending also sees nothing.
    assert mcp_pending_store.get_pending("mcp-acme-abc12345", db_path=db_path) is None


def test_pop_pending_returns_none_for_missing_thread(db_path: Path):
    assert mcp_pending_store.pop_pending("never-existed", db_path=db_path) is None


def test_restore_pending_makes_a_popped_thread_recoverable(db_path: Path):
    """Used when graph resume fails after the pop - we put the row back
    so the operator can retry approve/reject."""
    _record(db_path)
    payload = mcp_pending_store.pop_pending("mcp-acme-abc12345", db_path=db_path)
    assert payload is not None
    mcp_pending_store.restore_pending(payload, db_path=db_path)
    again = mcp_pending_store.get_pending("mcp-acme-abc12345", db_path=db_path)
    assert again is not None
    assert again["plan_id"] == payload["plan_id"]
    assert again["status"] == "pending"


# --------------------------------------------------------------------------- #
# mark_decided - terminal audit transition
# --------------------------------------------------------------------------- #


def test_mark_decided_approved_writes_audit_row(db_path: Path):
    _record(db_path)
    ok = mcp_pending_store.mark_decided("mcp-acme-abc12345", "approved", db_path=db_path)
    assert ok is True
    # No longer in pending list.
    assert mcp_pending_store.get_pending("mcp-acme-abc12345", db_path=db_path) is None
    # Visible in decided list.
    decided = mcp_pending_store.list_decided(db_path=db_path)
    assert len(decided) == 1
    assert decided[0]["status"] == "approved"
    assert decided[0]["decided_at"] is not None
    assert decided[0]["decided_by"] == "human"


def test_mark_decided_rejected_keeps_decided_by(db_path: Path):
    _record(db_path)
    mcp_pending_store.mark_decided(
        "mcp-acme-abc12345", "rejected", decided_by="auto", db_path=db_path,
    )
    decided = mcp_pending_store.list_decided(db_path=db_path)
    assert decided[0]["status"] == "rejected"
    assert decided[0]["decided_by"] == "auto"


def test_mark_decided_rejects_invalid_decision(db_path: Path):
    _record(db_path)
    with pytest.raises(ValueError, match="decision must be"):
        mcp_pending_store.mark_decided("mcp-acme-abc12345", "maybe", db_path=db_path)


def test_mark_decided_only_transitions_pending(db_path: Path):
    """Idempotency: marking an already-decided row a second time must
    return False, not silently overwrite the prior decision."""
    _record(db_path)
    assert mcp_pending_store.mark_decided("mcp-acme-abc12345", "approved", db_path=db_path)
    assert not mcp_pending_store.mark_decided("mcp-acme-abc12345", "rejected", db_path=db_path)
    decided = mcp_pending_store.list_decided(db_path=db_path)
    assert decided[0]["status"] == "approved"  # unchanged


# --------------------------------------------------------------------------- #
# list_pending - powers the Streamlit status strip
# --------------------------------------------------------------------------- #


def test_list_pending_returns_only_pending_newest_first(db_path: Path):
    _record(db_path, thread_id="t-1", plan_id="VP-001")
    _record(db_path, thread_id="t-2", plan_id="VP-002")
    _record(db_path, thread_id="t-3", plan_id="VP-003")
    mcp_pending_store.mark_decided("t-2", "approved", db_path=db_path)

    pending = mcp_pending_store.list_pending(db_path=db_path)
    plan_ids = [p["plan_id"] for p in pending]
    assert "VP-002" not in plan_ids  # decided row is excluded
    assert set(plan_ids) == {"VP-001", "VP-003"}
