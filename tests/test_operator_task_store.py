"""Tests for services/operator_task_store.py (Pass 2.1 Initiative 1).

Pins the sync state machine on top of services.operator_inbox.OperatorTask:

  - sync inserts new inferred tasks as 'open' / origin='inferred'
  - re-sync preserves the store-assigned UUID id + refreshes content
  - done/dismissed rows do not reopen on re-sync (operator decision wins)
  - snoozed rows stay hidden until snoozed_until passes
  - stale open rows whose fingerprint disappears auto-close as 'done'
  - origin='manual' rows are immune to the auto-close sweep
  - mark_done / dismiss / snooze / reopen apply the right transitions
  - list_open_tasks filters by client_id, sorts critical-first
  - WAL mode is on after schema init

Tests use a tmp_path-scoped DB and a frozen `NOW` — no test touches the
real operator_tasks.db. Mirrors the fixture pattern in
tests/test_mcp_pending_store.py.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services import operator_task_store
from services.operator_inbox import OperatorTask

NOW = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures + helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "operator_tasks.db"


def _make_inferred(
    fingerprint: str = "plan-pending:acme:VP-001",
    *,
    priority: str = "critical",
    category: str = "approval",
    agent: str = "producer",
    client_id: str | None = "acme",
    prospect_id: str | None = None,
    title: str = "Approve plan VP-001",
    description: str = "Plan VP-001 awaiting approval.",
    source_type: str = "video_plan",
    source_id: str = "VP-001",
    action_hint: str | None = "approve_or_reject",
    location_hint: str | None = "Streamlit HITL panel",
    recommended_next_action: str = "Review compiled brief, then approve.",
) -> OperatorTask:
    return OperatorTask(
        id=fingerprint,
        priority=priority,
        category=category,
        agent=agent,
        client_id=client_id,
        prospect_id=prospect_id,
        title=title,
        description=description,
        source_type=source_type,
        source_id=source_id,
        action_hint=action_hint,
        location_hint=location_hint,
        recommended_next_action=recommended_next_action,
    )


def _get_row(db_path: Path, fingerprint: str) -> sqlite3.Row | None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM operator_tasks WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()


# --------------------------------------------------------------------------- #
# sync_inferred_tasks - insert path
# --------------------------------------------------------------------------- #


def test_sync_creates_open_task_from_inferred(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    row = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row is not None
    assert row["status"] == "open"
    assert row["origin"] == "inferred"
    assert row["priority"] == "critical"
    assert row["client_id"] == "acme"
    assert row["title"] == "Approve plan VP-001"
    assert row["inferred_seen_at"] == NOW.isoformat()
    assert row["created_at"] == NOW.isoformat()
    assert row["updated_at"] == NOW.isoformat()
    assert row["resolved_at"] is None
    assert row["snoozed_until"] is None


def test_sync_assigns_stable_uuid_id_separate_from_fingerprint(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    row = _get_row(db_path, "plan-pending:acme:VP-001")
    # id is a UUID4 hex (32 chars, all lowercase hex). fingerprint is
    # the inferred-task identity string and is structurally different.
    assert row["fingerprint"] == "plan-pending:acme:VP-001"
    assert len(row["id"]) == 32
    int(row["id"], 16)  # raises if not hex - the assertion is the success


# --------------------------------------------------------------------------- #
# sync_inferred_tasks - idempotency on open
# --------------------------------------------------------------------------- #


def test_sync_idempotent_open_preserves_id_and_refreshes_content(db_path: Path):
    """Same fingerprint synced twice keeps its store id but refreshes
    title / priority / description on the second pass."""
    first = _make_inferred(priority="medium", title="OLD TITLE")
    operator_task_store.sync_inferred_tasks([first], db_path=db_path, now=NOW)
    row1 = _get_row(db_path, "plan-pending:acme:VP-001")
    stored_id = row1["id"]
    inferred_at_1 = row1["inferred_seen_at"]

    later = NOW + timedelta(hours=1)
    second = _make_inferred(priority="critical", title="NEW TITLE")
    operator_task_store.sync_inferred_tasks([second], db_path=db_path, now=later)
    row2 = _get_row(db_path, "plan-pending:acme:VP-001")

    assert row2["id"] == stored_id  # stable across syncs
    assert row2["priority"] == "critical"
    assert row2["title"] == "NEW TITLE"
    assert row2["status"] == "open"
    assert row2["inferred_seen_at"] == later.isoformat()
    assert row2["inferred_seen_at"] != inferred_at_1


# --------------------------------------------------------------------------- #
# sync_inferred_tasks - terminal-state stickiness
# --------------------------------------------------------------------------- #


def test_sync_does_not_reopen_done_task(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    row = _get_row(db_path, "plan-pending:acme:VP-001")
    operator_task_store.mark_task_done(row["id"], db_path=db_path, now=NOW)

    later = NOW + timedelta(hours=2)
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=later,
    )
    row_after = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row_after["status"] == "done"


def test_sync_does_not_reopen_dismissed_task(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    row = _get_row(db_path, "plan-pending:acme:VP-001")
    operator_task_store.dismiss_task(row["id"], db_path=db_path, now=NOW)

    later = NOW + timedelta(hours=2)
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=later,
    )
    row_after = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row_after["status"] == "dismissed"


def test_sync_bumps_inferred_seen_at_on_done_row(db_path: Path):
    """Done rows still get inferred_seen_at bumped on re-sync - it's an
    audit signal showing 'the inference layer still thinks this matters'."""
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    row = _get_row(db_path, "plan-pending:acme:VP-001")
    operator_task_store.mark_task_done(row["id"], db_path=db_path, now=NOW)
    seen_before = _get_row(db_path, "plan-pending:acme:VP-001")["inferred_seen_at"]

    later = NOW + timedelta(hours=3)
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=later,
    )
    row_after = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row_after["status"] == "done"
    assert row_after["inferred_seen_at"] == later.isoformat()
    assert row_after["inferred_seen_at"] != seen_before


# --------------------------------------------------------------------------- #
# sync_inferred_tasks - snooze semantics
# --------------------------------------------------------------------------- #


def test_sync_snoozed_task_hidden_until_expiry(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    row = _get_row(db_path, "plan-pending:acme:VP-001")
    operator_task_store.snooze_task(
        row["id"], NOW + timedelta(hours=1), db_path=db_path, now=NOW,
    )

    # Just before expiry: still hidden.
    visible_at = NOW + timedelta(minutes=30)
    visible = operator_task_store.list_open_tasks(db_path=db_path, now=visible_at)
    assert visible == []


def test_sync_snoozed_task_surfaces_after_expiry(db_path: Path):
    """Expiry sweep inside list_open_tasks resurfaces the row as 'open',
    clears snoozed_until."""
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    row = _get_row(db_path, "plan-pending:acme:VP-001")
    operator_task_store.snooze_task(
        row["id"], NOW + timedelta(hours=1), db_path=db_path, now=NOW,
    )

    after = NOW + timedelta(hours=2)
    visible = operator_task_store.list_open_tasks(db_path=db_path, now=after)
    assert len(visible) == 1
    assert visible[0].status == "open"
    assert visible[0].snoozed_until is None


# --------------------------------------------------------------------------- #
# sync_inferred_tasks - auto-close
# --------------------------------------------------------------------------- #


def test_sync_auto_closes_stale_open_task(db_path: Path):
    """An open inferred row whose fingerprint disappears from the next
    sync's inferred set is auto-closed as 'done' with resolved_at."""
    operator_task_store.sync_inferred_tasks(
        [_make_inferred("a"), _make_inferred("b")], db_path=db_path, now=NOW,
    )
    later = NOW + timedelta(hours=4)
    operator_task_store.sync_inferred_tasks(
        [_make_inferred("a")], db_path=db_path, now=later,  # 'b' missing
    )

    row_a = _get_row(db_path, "a")
    row_b = _get_row(db_path, "b")
    assert row_a["status"] == "open"
    assert row_b["status"] == "done"
    assert row_b["resolved_at"] == later.isoformat()


def test_sync_does_not_auto_close_terminal_rows(db_path: Path):
    """Done/dismissed/snoozed rows whose fingerprint is gone stay put.
    The auto-close sweep only targets status='open'."""
    operator_task_store.sync_inferred_tasks(
        [_make_inferred("done-row"),
         _make_inferred("dismissed-row"),
         _make_inferred("snoozed-row")],
        db_path=db_path, now=NOW,
    )
    done_id = _get_row(db_path, "done-row")["id"]
    dismissed_id = _get_row(db_path, "dismissed-row")["id"]
    snoozed_id = _get_row(db_path, "snoozed-row")["id"]
    operator_task_store.mark_task_done(done_id, db_path=db_path, now=NOW)
    operator_task_store.dismiss_task(dismissed_id, db_path=db_path, now=NOW)
    operator_task_store.snooze_task(
        snoozed_id, NOW + timedelta(days=7), db_path=db_path, now=NOW,
    )

    # Sync with EMPTY inferred set. All three fingerprints are "gone".
    operator_task_store.sync_inferred_tasks(
        [], db_path=db_path, now=NOW + timedelta(hours=1),
    )

    assert _get_row(db_path, "done-row")["status"] == "done"
    assert _get_row(db_path, "dismissed-row")["status"] == "dismissed"
    assert _get_row(db_path, "snoozed-row")["status"] == "snoozed"


def test_sync_preserves_manual_origin_rows_across_syncs(db_path: Path):
    """origin='manual' rows are never auto-closed. Operator-created tasks
    survive sync sweeps regardless of inferred state."""
    operator_task_store.insert_manual_task(
        fingerprint="manual:investigate-something",
        priority="high", category="follow_up", agent="manager",
        title="Investigate something",
        description="Operator-authored.",
        source_type="manual",
        source_id="investigate-something",
        recommended_next_action="Dig in.",
        db_path=db_path, now=NOW,
    )
    operator_task_store.sync_inferred_tasks(
        [], db_path=db_path, now=NOW + timedelta(hours=1),
    )
    row = _get_row(db_path, "manual:investigate-something")
    assert row["status"] == "open"
    assert row["origin"] == "manual"


# --------------------------------------------------------------------------- #
# mark_task_done
# --------------------------------------------------------------------------- #


def test_mark_task_done_transitions_open_to_done(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    task_id = _get_row(db_path, "plan-pending:acme:VP-001")["id"]
    ok = operator_task_store.mark_task_done(task_id, db_path=db_path, now=NOW)
    assert ok is True

    row = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row["status"] == "done"
    assert row["resolved_at"] == NOW.isoformat()


def test_mark_task_done_is_idempotent_on_already_done(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    task_id = _get_row(db_path, "plan-pending:acme:VP-001")["id"]
    assert operator_task_store.mark_task_done(task_id, db_path=db_path, now=NOW)
    # Second call: row exists (rowcount>0 even though the value is the same).
    assert operator_task_store.mark_task_done(task_id, db_path=db_path, now=NOW)
    assert _get_row(db_path, "plan-pending:acme:VP-001")["status"] == "done"


def test_mark_task_done_returns_false_for_unknown_id(db_path: Path):
    """Unknown id is a no-op + returns False so callers can detect bad input."""
    assert operator_task_store.mark_task_done(
        "does-not-exist", db_path=db_path, now=NOW,
    ) is False


# --------------------------------------------------------------------------- #
# dismiss_task
# --------------------------------------------------------------------------- #


def test_dismiss_task_transitions_open_to_dismissed(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    task_id = _get_row(db_path, "plan-pending:acme:VP-001")["id"]
    ok = operator_task_store.dismiss_task(task_id, db_path=db_path, now=NOW)
    assert ok is True

    row = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row["status"] == "dismissed"
    assert row["resolved_at"] == NOW.isoformat()


# --------------------------------------------------------------------------- #
# snooze_task
# --------------------------------------------------------------------------- #


def test_snooze_task_sets_status_and_until(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    task_id = _get_row(db_path, "plan-pending:acme:VP-001")["id"]
    until = NOW + timedelta(days=1)
    assert operator_task_store.snooze_task(
        task_id, until, db_path=db_path, now=NOW,
    )

    row = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row["status"] == "snoozed"
    assert row["snoozed_until"] == until.isoformat()


def test_snooze_task_rejects_past_until(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    task_id = _get_row(db_path, "plan-pending:acme:VP-001")["id"]
    past = NOW - timedelta(hours=1)
    with pytest.raises(ValueError, match="snooze_task requires until > now"):
        operator_task_store.snooze_task(task_id, past, db_path=db_path, now=NOW)


# --------------------------------------------------------------------------- #
# reopen_task
# --------------------------------------------------------------------------- #


def test_reopen_task_brings_done_back_to_open(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    task_id = _get_row(db_path, "plan-pending:acme:VP-001")["id"]
    operator_task_store.mark_task_done(task_id, db_path=db_path, now=NOW)
    assert operator_task_store.reopen_task(task_id, db_path=db_path, now=NOW)

    row = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row["status"] == "open"
    assert row["resolved_at"] is None


def test_reopen_task_clears_snoozed_until(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    task_id = _get_row(db_path, "plan-pending:acme:VP-001")["id"]
    operator_task_store.snooze_task(
        task_id, NOW + timedelta(days=1), db_path=db_path, now=NOW,
    )
    operator_task_store.reopen_task(task_id, db_path=db_path, now=NOW)

    row = _get_row(db_path, "plan-pending:acme:VP-001")
    assert row["status"] == "open"
    assert row["snoozed_until"] is None


# --------------------------------------------------------------------------- #
# get_task
# --------------------------------------------------------------------------- #


def test_get_task_round_trips_full_payload(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred()], db_path=db_path, now=NOW,
    )
    task_id = _get_row(db_path, "plan-pending:acme:VP-001")["id"]
    stored = operator_task_store.get_task(task_id, db_path=db_path)
    assert stored is not None
    assert stored.id == task_id
    assert stored.fingerprint == "plan-pending:acme:VP-001"
    assert stored.title == "Approve plan VP-001"
    assert stored.priority == "critical"
    assert stored.category == "approval"
    assert stored.client_id == "acme"
    assert stored.status == "open"
    assert stored.origin == "inferred"


# --------------------------------------------------------------------------- #
# list_open_tasks
# --------------------------------------------------------------------------- #


def test_list_open_tasks_excludes_done_dismissed_snoozed(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [_make_inferred("a"), _make_inferred("b"), _make_inferred("c"),
         _make_inferred("d")],
        db_path=db_path, now=NOW,
    )
    a_id = _get_row(db_path, "a")["id"]
    b_id = _get_row(db_path, "b")["id"]
    c_id = _get_row(db_path, "c")["id"]
    operator_task_store.mark_task_done(a_id, db_path=db_path, now=NOW)
    operator_task_store.dismiss_task(b_id, db_path=db_path, now=NOW)
    operator_task_store.snooze_task(
        c_id, NOW + timedelta(days=7), db_path=db_path, now=NOW,
    )

    open_tasks = operator_task_store.list_open_tasks(db_path=db_path, now=NOW)
    fingerprints = {t.fingerprint for t in open_tasks}
    assert fingerprints == {"d"}


def test_list_open_tasks_ordered_critical_first(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [
            _make_inferred("low-1", priority="low", title="L"),
            _make_inferred("critical-1", priority="critical", title="C"),
            _make_inferred("medium-1", priority="medium", title="M"),
            _make_inferred("high-1", priority="high", title="H"),
        ],
        db_path=db_path, now=NOW,
    )
    out = operator_task_store.list_open_tasks(db_path=db_path, now=NOW)
    assert [t.priority for t in out] == ["critical", "high", "medium", "low"]


def test_list_open_tasks_filters_by_client_id(db_path: Path):
    operator_task_store.sync_inferred_tasks(
        [
            _make_inferred("a", client_id="acme"),
            _make_inferred("b", client_id="other"),
            _make_inferred("c", client_id="acme"),
        ],
        db_path=db_path, now=NOW,
    )
    acme_only = operator_task_store.list_open_tasks(
        client_id="acme", db_path=db_path, now=NOW,
    )
    assert {t.fingerprint for t in acme_only} == {"a", "c"}


# --------------------------------------------------------------------------- #
# WAL mode
# --------------------------------------------------------------------------- #


def test_wal_mode_enabled_after_init(db_path: Path):
    """Schema init must enable WAL so the Streamlit / Manager / daily-
    summary readers don't deadlock with the sync writer."""
    operator_task_store.sync_inferred_tasks([], db_path=db_path, now=NOW)
    with sqlite3.connect(str(db_path)) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
