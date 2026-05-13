"""Durable registry for MCP-paused supervisor runs.

Why this exists: the LangGraph SqliteSaver (V1.5) persists the GRAPH
checkpoint across MCP restarts, but the operator-facing context
(thread_id, prompt, client_id, model, task_type, plan_id, config)
lived in a process-local `dict` inside `mcp_server.py`. After an MCP
restart the checkpoint survived but the message that told the operator
the thread_id was gone, so `resume_agency_workflow` was unreachable.

This module replaces that dict with a tiny SQLite store
(`mcp_pending_runs.db` at repo root, mirrors the existing
`checkpoints.db` convention). The store keeps the same dict-like
semantics so the call sites in mcp_server.py barely change:

    record_pending(thread_id, ...)        # like dict[k] = payload
    pop_pending(thread_id) -> payload     # like dict.pop(k, None)
    restore_pending(payload)              # like dict[k] = payload again
                                           # (used on graph-resume failure)
    mark_decided(thread_id, decision)     # writes terminal status
                                           # for the audit trail
    list_pending() -> list[dict]          # for the Streamlit status strip

Concurrency: each method opens its own short-lived connection in WAL
mode, mirroring `core/client_context.py`. SQLite serialises writes per
DB file, which is exactly the guarantee we need - MCP is single-process
and even a future multi-tenant deployment would be safe.

Storage location is gitignored. See .gitignore for the rules.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "mcp_pending_runs.db"


_DDL = """
CREATE TABLE IF NOT EXISTS mcp_pending_runs (
    thread_id    TEXT PRIMARY KEY,
    client_id    TEXT,
    model        TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    task_type    TEXT,
    plan_id      TEXT,
    config_json  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
    created_at   TEXT NOT NULL,
    decided_at   TEXT,
    decided_by   TEXT
)
"""

_INDEX_STATUS = """
CREATE INDEX IF NOT EXISTS idx_mcp_pending_runs_status_created
    ON mcp_pending_runs (status, created_at DESC)
"""


# --------------------------------------------------------------------------- #
# Connection plumbing
# --------------------------------------------------------------------------- #


def _set_wal_mode(conn: sqlite3.Connection) -> None:
    """Mirrors ClientContext._set_wal_mode. Readers do not block writers
    and vice versa - critical because Streamlit's status strip queries
    the same DB while MCP may be writing to it."""
    conn.execute("PRAGMA journal_mode=WAL")


def _init_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        _set_wal_mode(conn)
        conn.execute(_DDL)
        conn.execute(_INDEX_STATUS)
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
# Row <-> payload conversion
#
# The "payload" dict is the same shape mcp_server.py was pushing into
# its in-memory _PENDING_RUNS dict. Keeping the dict shape identical at
# the API boundary means the call sites barely change, which keeps this
# pass reviewable.
# --------------------------------------------------------------------------- #


def _row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "thread_id": row["thread_id"],
        "client_id": row["client_id"],
        "model": row["model"],
        "prompt": row["prompt"],
        "task_type": row["task_type"],
        "plan_id": row["plan_id"],
        "config": json.loads(row["config_json"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
        "decided_by": row["decided_by"],
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def record_pending(
    thread_id: str,
    *,
    client_id: Optional[str],
    model: str,
    prompt: str,
    task_type: Optional[str],
    plan_id: Optional[str],
    config: dict,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Upsert a pending-run row. INSERT OR REPLACE so a re-pause on the
    same thread_id (rare but possible if MCP retries) doesn't deadlock
    on the PRIMARY KEY."""
    now = datetime.now(timezone.utc).isoformat()
    with _db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mcp_pending_runs "
            "(thread_id, client_id, model, prompt, task_type, plan_id, "
            " config_json, status, created_at, decided_at, decided_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL)",
            (
                thread_id, client_id, model, prompt, task_type, plan_id,
                json.dumps(config), now,
            ),
        )


def get_pending(
    thread_id: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> Optional[dict]:
    """Read without mutating. Returns None if no row OR row is not pending."""
    with _db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM mcp_pending_runs WHERE thread_id = ? AND status = 'pending'",
            (thread_id,),
        ).fetchone()
    return _row_to_payload(row) if row else None


def pop_pending(
    thread_id: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> Optional[dict]:
    """Atomic SELECT + DELETE. Mirrors the old `_PENDING_RUNS.pop(thread_id, None)`
    semantics so the call site in mcp_server.py barely changes.

    Returns the full payload (or None if no pending row). The row is
    deleted, not marked - the audit trail is recorded via mark_decided
    in the approve/reject branches.

    NOTE: this is a true "remove and forget". Callers that want to
    preserve the audit trail must call mark_decided BEFORE pop_pending,
    or use the higher-level approve/reject flows which do both.
    """
    with _db(db_path) as conn:
        cur = conn.execute(
            "SELECT * FROM mcp_pending_runs WHERE thread_id = ? AND status = 'pending'",
            (thread_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        conn.execute(
            "DELETE FROM mcp_pending_runs WHERE thread_id = ?",
            (thread_id,),
        )
    return _row_to_payload(row)


def restore_pending(payload: dict, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Re-insert a previously-popped payload as pending again. Used by
    mcp_server.py when a graph resume fails after we already popped the
    row, so the operator can retry the approve/reject decision instead
    of the row vanishing."""
    record_pending(
        payload["thread_id"],
        client_id=payload.get("client_id"),
        model=payload["model"],
        prompt=payload["prompt"],
        task_type=payload.get("task_type"),
        plan_id=payload.get("plan_id"),
        config=payload["config"],
        db_path=db_path,
    )


def mark_decided(
    thread_id: str,
    decision: str,
    *,
    decided_by: str = "human",
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Terminal transition for the audit trail.

    `decision` must be 'approved' or 'rejected'. Use this when you want
    to keep the row visible in `list_decided()` rather than DELETE via
    pop_pending. Returns True if a row was affected (had to be in
    'pending' state).
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")
    now = datetime.now(timezone.utc).isoformat()
    with _db(db_path) as conn:
        cur = conn.execute(
            "UPDATE mcp_pending_runs "
            "SET status = ?, decided_at = ?, decided_by = ? "
            "WHERE thread_id = ? AND status = 'pending'",
            (decision, now, decided_by, thread_id),
        )
    return cur.rowcount > 0


def list_pending(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """All currently-pending rows, newest first. Used by the Streamlit
    operator status strip so the operator can see at a glance which
    threads are awaiting a resume_agency_workflow call."""
    with _db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM mcp_pending_runs WHERE status = 'pending' "
            "ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_payload(r) for r in rows]


def list_decided(
    *,
    limit: int = 50,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Recent terminal rows for the audit trail. Newest first."""
    with _db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM mcp_pending_runs "
            "WHERE status IN ('approved', 'rejected') "
            "ORDER BY decided_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_payload(r) for r in rows]
