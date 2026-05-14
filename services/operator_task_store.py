"""Persistent operator task store. Pass 2 Initiative 1.

This module sits ON TOP of `services.operator_inbox`. The inbox is the
read-only inference layer that scans existing SQL + filesystem state and
returns `OperatorTask` dataclasses. This store gives those tasks
persistence: the operator can mark them done, dismiss them, snooze them,
or reopen them, and re-syncing the inbox does not silently undo those
decisions.

Storage: `operator_tasks.db` at repo root, WAL mode. The file is
gitignored and hygiene-protected (Phase 1 placeholders, see
`.gitignore` lines 69-72 and `scripts/check_repo_hygiene.py` rule
`operator-tasks-db`).

Sync semantics (the state machine that `sync_inferred_tasks` applies for
each inferred fingerprint, see PR A plan section 4 for the rationale):

  inferred present  | stored status | snoozed_until        | action
  ------------------+---------------+----------------------+-------------------
  yes               | (no row)      |  -                   | INSERT open
  yes               | open          |  -                   | refresh content
  yes               | done          |  -                   | bump seen, keep
  yes               | dismissed     |  -                   | bump seen, keep
  yes               | snoozed       | future               | refresh content
  yes               | snoozed       | past                 | resurface as open
  no                | open          |  -                   | auto-close as done
  no                | done/dism/sn  |  -                   | no-op

Auto-close uses `status='done'` with `resolved_at=now`. We do NOT add a
fifth status value in this pass; the disambiguation signal between
operator-close vs system-close is `inferred_seen_at + resolved_at`
timing. A future additive `resolved_by` column can be added later if
audit consumers need it.

Concurrency: each public function opens a short-lived connection in WAL
mode. SQLite serialises writes per DB file; Streamlit (which calls
sync_inferred_tasks on every Agency Overview render) and a future cron-
driven daily_summary cannot deadlock.

Never imports KlingClient. Never calls a network. Never reaches outside
the SQLite DB and the inferred-task list it receives as input.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from services.operator_inbox import (
    VALID_CATEGORIES,
    VALID_PRIORITIES,
    OperatorTask,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "operator_tasks.db"


VALID_STATUSES = ("open", "done", "dismissed", "snoozed")
VALID_ORIGINS = ("inferred", "manual", "system")


_DDL = """
CREATE TABLE IF NOT EXISTS operator_tasks (
    id                      TEXT PRIMARY KEY,
    fingerprint             TEXT NOT NULL UNIQUE,
    status                  TEXT NOT NULL
        CHECK(status IN ('open','done','dismissed','snoozed')),
    priority                TEXT NOT NULL
        CHECK(priority IN ('critical','high','medium','low')),
    category                TEXT NOT NULL,
    agent                   TEXT NOT NULL,
    client_id               TEXT,
    prospect_id             TEXT,
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL,
    source_type             TEXT NOT NULL,
    source_id               TEXT NOT NULL,
    action_hint             TEXT,
    location_hint           TEXT,
    recommended_next_action TEXT NOT NULL,
    origin                  TEXT NOT NULL
        CHECK(origin IN ('inferred','manual','system')),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    resolved_at             TEXT,
    snoozed_until           TEXT,
    inferred_seen_at        TEXT
)
"""

_INDEX_STATUS_PRIORITY = """
CREATE INDEX IF NOT EXISTS idx_operator_tasks_status_priority
    ON operator_tasks (status, priority, created_at DESC)
"""

_INDEX_STATUS_CLIENT = """
CREATE INDEX IF NOT EXISTS idx_operator_tasks_status_client
    ON operator_tasks (status, client_id)
"""

# Partial index keeps the snooze-expiry sweep cheap as the table grows.
_INDEX_SNOOZED_UNTIL = """
CREATE INDEX IF NOT EXISTS idx_operator_tasks_snoozed_until
    ON operator_tasks (status, snoozed_until)
    WHERE status = 'snoozed'
"""


# --------------------------------------------------------------------------- #
# Connection plumbing - mirrors services/mcp_pending_store.py
# --------------------------------------------------------------------------- #


def _set_wal_mode(conn: sqlite3.Connection) -> None:
    """Same justification as mcp_pending_store: readers must not block
    writers because Streamlit's Agency Overview render and the daily-
    summary script both read from this DB while sync writes to it."""
    conn.execute("PRAGMA journal_mode=WAL")


def _init_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        _set_wal_mode(conn)
        conn.execute(_DDL)
        conn.execute(_INDEX_STATUS_PRIORITY)
        conn.execute(_INDEX_STATUS_CLIENT)
        conn.execute(_INDEX_SNOOZED_UNTIL)
        conn.commit()


@contextmanager
def _db(db_path: Path) -> Iterator[sqlite3.Connection]:
    _init_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    _set_wal_mode(conn)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# StoredTask: full row shape
# --------------------------------------------------------------------------- #


@dataclass
class StoredTask:
    """A persisted operator task. Mirrors OperatorTask + adds the store-
    only fields (id, status, origin, lifecycle timestamps). `id` is the
    store-assigned UUID; `fingerprint` is the inferred-task identity
    string (e.g. 'plan-pending:acme:VP-007') that survives across syncs.
    """
    id: str
    fingerprint: str
    status: str
    priority: str
    category: str
    agent: str
    client_id: Optional[str]
    prospect_id: Optional[str]
    title: str
    description: str
    source_type: str
    source_id: str
    action_hint: Optional[str]
    location_hint: Optional[str]
    recommended_next_action: str
    origin: str
    created_at: str
    updated_at: str
    resolved_at: Optional[str] = None
    snoozed_until: Optional[str] = None
    inferred_seen_at: Optional[str] = None

    def to_operator_task(self) -> OperatorTask:
        """Project back to the OperatorTask shape so existing helpers
        (`summarize_operator_tasks`, `group_tasks_by_priority`) work
        unchanged. The store's UUID `id` is replaced by `fingerprint`
        because OperatorTask uses `id` to mean "inferred identity"."""
        return OperatorTask(
            id=self.fingerprint,
            priority=self.priority,
            category=self.category,
            agent=self.agent,
            client_id=self.client_id,
            prospect_id=self.prospect_id,
            title=self.title,
            description=self.description,
            source_type=self.source_type,
            source_id=self.source_id,
            created_at=self.created_at,
            action_hint=self.action_hint,
            location_hint=self.location_hint,
            recommended_next_action=self.recommended_next_action,
        )


def _row_to_stored_task(row: sqlite3.Row) -> StoredTask:
    return StoredTask(
        id=row["id"],
        fingerprint=row["fingerprint"],
        status=row["status"],
        priority=row["priority"],
        category=row["category"],
        agent=row["agent"],
        client_id=row["client_id"],
        prospect_id=row["prospect_id"],
        title=row["title"],
        description=row["description"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        action_hint=row["action_hint"],
        location_hint=row["location_hint"],
        recommended_next_action=row["recommended_next_action"],
        origin=row["origin"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        resolved_at=row["resolved_at"],
        snoozed_until=row["snoozed_until"],
        inferred_seen_at=row["inferred_seen_at"],
    )


def _utcnow_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


# --------------------------------------------------------------------------- #
# Sync inferred tasks - the core upsert + auto-close logic
# --------------------------------------------------------------------------- #


def sync_inferred_tasks(
    inferred_tasks: list[OperatorTask],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> None:
    """Apply the sync state machine to `inferred_tasks`.

    Pure SQL: opens one connection, runs the inferred-set upsert and the
    auto-close sweep, commits. Safe to call repeatedly (idempotent at
    any point in the state machine). Never calls operator_inbox or any
    external service; the caller owns the inferred list.

    `now` is injectable for deterministic testing of snooze expiry +
    timestamps. Defaults to `datetime.now(timezone.utc)`.
    """
    now_dt = now or datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    inferred_fingerprints = {t.id for t in inferred_tasks}

    with _db(db_path) as conn:
        # ---- Pass 1: snoozed-expiry sweep ------------------------------- #
        # Move 'snoozed' rows whose snoozed_until <= now back to 'open'.
        # Done first so the inferred upsert below sees the resurfaced
        # state and can refresh content on the same row.
        conn.execute(
            "UPDATE operator_tasks "
            "   SET status='open', snoozed_until=NULL, updated_at=? "
            " WHERE status='snoozed' "
            "   AND snoozed_until IS NOT NULL "
            "   AND snoozed_until <= ?",
            (now_iso, now_iso),
        )

        # ---- Pass 2: per-inferred-task upsert --------------------------- #
        for task in inferred_tasks:
            fingerprint = task.id  # OperatorTask.id is the fingerprint
            cur = conn.execute(
                "SELECT id, status, snoozed_until FROM operator_tasks "
                "WHERE fingerprint = ?",
                (fingerprint,),
            )
            existing = cur.fetchone()

            if existing is None:
                # New: INSERT as open + origin=inferred.
                conn.execute(
                    "INSERT INTO operator_tasks ("
                    "  id, fingerprint, status, priority, category, agent, "
                    "  client_id, prospect_id, title, description, "
                    "  source_type, source_id, action_hint, location_hint, "
                    "  recommended_next_action, origin, "
                    "  created_at, updated_at, inferred_seen_at"
                    ") VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "          'inferred', ?, ?, ?)",
                    (
                        uuid.uuid4().hex, fingerprint,
                        task.priority, task.category, task.agent,
                        task.client_id, task.prospect_id,
                        task.title, task.description,
                        task.source_type, task.source_id,
                        task.action_hint, task.location_hint,
                        task.recommended_next_action,
                        now_iso, now_iso, now_iso,
                    ),
                )
                continue

            status = existing["status"]
            if status == "open":
                # Refresh content (priority/category/title/description/...
                # may have changed since last sync). Status untouched.
                conn.execute(
                    "UPDATE operator_tasks SET "
                    "  priority=?, category=?, agent=?, "
                    "  client_id=?, prospect_id=?, "
                    "  title=?, description=?, "
                    "  source_type=?, source_id=?, "
                    "  action_hint=?, location_hint=?, "
                    "  recommended_next_action=?, "
                    "  updated_at=?, inferred_seen_at=? "
                    "WHERE fingerprint = ?",
                    (
                        task.priority, task.category, task.agent,
                        task.client_id, task.prospect_id,
                        task.title, task.description,
                        task.source_type, task.source_id,
                        task.action_hint, task.location_hint,
                        task.recommended_next_action,
                        now_iso, now_iso, fingerprint,
                    ),
                )
            elif status == "snoozed":
                # Still snoozed (the expiry sweep above didn't surface it,
                # so snoozed_until is in the future or NULL). Refresh
                # content but preserve the snoozed status + snoozed_until.
                conn.execute(
                    "UPDATE operator_tasks SET "
                    "  priority=?, category=?, agent=?, "
                    "  client_id=?, prospect_id=?, "
                    "  title=?, description=?, "
                    "  source_type=?, source_id=?, "
                    "  action_hint=?, location_hint=?, "
                    "  recommended_next_action=?, "
                    "  updated_at=?, inferred_seen_at=? "
                    "WHERE fingerprint = ?",
                    (
                        task.priority, task.category, task.agent,
                        task.client_id, task.prospect_id,
                        task.title, task.description,
                        task.source_type, task.source_id,
                        task.action_hint, task.location_hint,
                        task.recommended_next_action,
                        now_iso, now_iso, fingerprint,
                    ),
                )
            else:
                # done / dismissed: terminal from the operator's POV.
                # Bump inferred_seen_at so the audit shows the inferred
                # set still mentions this fingerprint. Do NOT reopen.
                conn.execute(
                    "UPDATE operator_tasks SET inferred_seen_at=? "
                    "WHERE fingerprint = ?",
                    (now_iso, fingerprint),
                )

        # ---- Pass 3: auto-close open rows that disappeared from inferred  #
        # Only rows with origin='inferred' get auto-closed. Manual or
        # system-origin rows are immune (the operator owns them).
        if inferred_fingerprints:
            placeholders = ",".join("?" * len(inferred_fingerprints))
            conn.execute(
                f"UPDATE operator_tasks "
                f"   SET status='done', resolved_at=?, updated_at=? "
                f" WHERE status='open' "
                f"   AND origin='inferred' "
                f"   AND fingerprint NOT IN ({placeholders})",
                (now_iso, now_iso, *inferred_fingerprints),
            )
        else:
            # No inferred tasks at all: close every open inferred row.
            conn.execute(
                "UPDATE operator_tasks "
                "   SET status='done', resolved_at=?, updated_at=? "
                " WHERE status='open' AND origin='inferred'",
                (now_iso, now_iso),
            )


def sync_from_inbox(
    *,
    clients_root: Optional[Path] = None,
    prospects_root: Optional[Path] = None,
    mcp_db_path: Optional[Path] = None,
    eval_path: Optional[Path] = None,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> None:
    """Convenience wrapper: call `operator_inbox.collect_operator_tasks`
    with the standard injection points, then `sync_inferred_tasks`.

    Used by `services.manager_service.get_agency_overview`,
    `ui/app.py`'s Agency Overview tab, and `scripts/daily_summary.py`.
    Tests should call `sync_inferred_tasks` directly with crafted lists
    so they don't depend on the inbox's filesystem scan.
    """
    # Local import: operator_inbox imports prospect_store which is heavy
    # at import time; keeping the import here also matches the rest of
    # this codebase's circular-import-defence pattern.
    from services import operator_inbox

    inferred = operator_inbox.collect_operator_tasks(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db_path,
        eval_path=eval_path,
        now=now,
    )
    sync_inferred_tasks(inferred, db_path=db_path, now=now)


# --------------------------------------------------------------------------- #
# Read-only queries
# --------------------------------------------------------------------------- #


_PRIORITY_RANK = {p: i for i, p in enumerate(VALID_PRIORITIES)}


def list_open_tasks(
    *,
    client_id: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> list[StoredTask]:
    """All tasks visible to the operator right now.

    "Visible" means status='open'. Snoozed rows whose snoozed_until has
    already passed are surfaced as `open` by an in-line expiry sweep
    BEFORE the SELECT (so the result is monotonic in `now`).

    Sorted critical-first, then created_at DESC within a priority bucket.
    """
    now_iso = _utcnow_iso(now)
    with _db(db_path) as conn:
        # Inline expiry sweep so list_open_tasks alone is sufficient for
        # the UI / daily summary; callers don't have to remember to call
        # sync_inferred_tasks before reading.
        conn.execute(
            "UPDATE operator_tasks "
            "   SET status='open', snoozed_until=NULL, updated_at=? "
            " WHERE status='snoozed' "
            "   AND snoozed_until IS NOT NULL "
            "   AND snoozed_until <= ?",
            (now_iso, now_iso),
        )
        if client_id is None:
            rows = conn.execute(
                "SELECT * FROM operator_tasks WHERE status='open' "
                "ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM operator_tasks "
                "WHERE status='open' AND client_id=? "
                "ORDER BY created_at DESC",
                (client_id,),
            ).fetchall()

    stored = [_row_to_stored_task(r) for r in rows]
    stored.sort(key=lambda s: (_PRIORITY_RANK.get(s.priority, 99), s.created_at))
    return stored


def get_task(
    task_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> Optional[StoredTask]:
    """Look up by the store's UUID `id`. Returns None when not found."""
    with _db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM operator_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    return _row_to_stored_task(row) if row else None


# --------------------------------------------------------------------------- #
# Mutations - operator-driven status transitions
# --------------------------------------------------------------------------- #


def _transition_to_terminal(
    task_id: str,
    new_status: str,
    *,
    db_path: Path,
    now: Optional[datetime],
) -> bool:
    """Shared helper for mark_done / dismiss. Sets status + resolved_at.

    Returns True iff the row existed and was updated. Idempotent on
    rows already in the target status.
    """
    now_iso = _utcnow_iso(now)
    with _db(db_path) as conn:
        cur = conn.execute(
            "UPDATE operator_tasks "
            "   SET status=?, resolved_at=?, updated_at=?, snoozed_until=NULL "
            " WHERE id = ?",
            (new_status, now_iso, now_iso, task_id),
        )
    return cur.rowcount > 0


def mark_task_done(
    task_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> bool:
    """Operator-driven 'I handled this'. Returns False if no row matches."""
    return _transition_to_terminal(task_id, "done", db_path=db_path, now=now)


def dismiss_task(
    task_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> bool:
    """Operator-driven 'not interested'. Returns False if no row matches."""
    return _transition_to_terminal(task_id, "dismissed", db_path=db_path, now=now)


def snooze_task(
    task_id: str,
    until: datetime,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> bool:
    """Hide the task until `until`. Must be strictly in the future.

    The next `sync_inferred_tasks` or `list_open_tasks` call after
    `until` will resurface it as 'open'.
    """
    now_dt = now or datetime.now(timezone.utc)
    if until <= now_dt:
        raise ValueError(
            f"snooze_task requires until > now; got until={until.isoformat()} "
            f"<= now={now_dt.isoformat()}"
        )
    now_iso = now_dt.isoformat()
    until_iso = until.isoformat()
    with _db(db_path) as conn:
        cur = conn.execute(
            "UPDATE operator_tasks "
            "   SET status='snoozed', snoozed_until=?, updated_at=?, "
            "       resolved_at=NULL "
            " WHERE id = ?",
            (until_iso, now_iso, task_id),
        )
    return cur.rowcount > 0


def reopen_task(
    task_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> bool:
    """Reverse a done/dismissed/snoozed transition: row goes back to 'open'.

    Clears `resolved_at` and `snoozed_until`. Used by the Streamlit
    inbox when an operator clicks an "undo" affordance on a card they
    just closed.
    """
    now_iso = _utcnow_iso(now)
    with _db(db_path) as conn:
        cur = conn.execute(
            "UPDATE operator_tasks "
            "   SET status='open', resolved_at=NULL, snoozed_until=NULL, "
            "       updated_at=? "
            " WHERE id = ?",
            (now_iso, task_id),
        )
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# Manual task creation - operator-authored, not inference-driven
#
# Kept narrow: PR A's UI does NOT expose a "create task" button. This
# helper exists for tests and for a future manual-task UI without
# requiring another schema migration. origin='manual' rows are never
# auto-closed by the sync sweep.
# --------------------------------------------------------------------------- #


def insert_manual_task(
    *,
    fingerprint: str,
    priority: str,
    category: str,
    agent: str,
    title: str,
    description: str,
    source_type: str,
    source_id: str,
    recommended_next_action: str,
    client_id: Optional[str] = None,
    prospect_id: Optional[str] = None,
    action_hint: Optional[str] = None,
    location_hint: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> str:
    """Insert a manual-origin task. Returns the store-assigned UUID id.

    Raises ValueError on invalid priority/category. Raises sqlite3
    IntegrityError if the fingerprint collides with an existing row
    (no automatic upsert: a manual collision is a programming error).
    """
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"priority must be one of {VALID_PRIORITIES}, got {priority!r}")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {VALID_CATEGORIES}, got {category!r}")
    now_iso = _utcnow_iso(now)
    task_id = uuid.uuid4().hex
    with _db(db_path) as conn:
        conn.execute(
            "INSERT INTO operator_tasks ("
            "  id, fingerprint, status, priority, category, agent, "
            "  client_id, prospect_id, title, description, "
            "  source_type, source_id, action_hint, location_hint, "
            "  recommended_next_action, origin, "
            "  created_at, updated_at"
            ") VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "          'manual', ?, ?)",
            (
                task_id, fingerprint,
                priority, category, agent,
                client_id, prospect_id,
                title, description,
                source_type, source_id,
                action_hint, location_hint,
                recommended_next_action,
                now_iso, now_iso,
            ),
        )
    return task_id
