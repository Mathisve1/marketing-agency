"""Lightweight local cost / activity ledger. Pass 2.2 Initiative 4.

Records ONE row per successful paid (or rate-limited) provider call so the
operator has a back-of-the-napkin sense of where the credits are going.

What this is:
  - Append-only SQLite log at repo root (`cost_ledger.db`, WAL mode).
  - Per-event row with provider, event_type, optional client/prospect/task
    scoping, units, an APPROXIMATE EUR estimate, and a JSON metadata blob.
  - Read helpers for the Streamlit "Cost & Activity" section.

What this is NOT:
  - A billing system. The `estimated_cost_eur` values are env-overridable
    rough constants; do not reconcile them against provider invoices.
  - An Anthropic / LLM token cost tracker. That requires LangChain
    callbacks across every agent and is deferred to a later pass. The
    Streamlit caption + OPERATIONS.md call this out explicitly.

Safety invariant: `record_event` MUST NEVER raise. Cost logging is
observability; a failure to record (disk full, schema error, malformed
metadata) must not break the success branch of a paid call. The other
helpers (`list_recent`, `summarise_*`) raise normally so tests can
distinguish a logging failure from a query bug.

Concurrency: each public function opens a short-lived WAL connection,
mirroring `services/mcp_pending_store.py` and
`services/operator_task_store.py`. Writes serialise per DB file; reads
never block writers and vice versa.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "cost_ledger.db"


# --------------------------------------------------------------------------- #
# Rough estimate constants (env-overridable; ALL approximate).
#
# These exist so the Streamlit "Cost & Activity" section can show a EUR
# total. They are NOT billing accuracy. Operators who care about cents
# should reconcile against the provider's own dashboard. Keep defaults
# conservative-low so the dashboard reads as "at least X EUR spent"
# rather than over-claiming.
# --------------------------------------------------------------------------- #


def _env_float(name: str, default: float) -> float:
    """Read a float from env with a safe fallback. Bad values fall back
    silently so a typo in .env doesn't take down the whole ledger."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


# Per-event rough cost estimates. Names match the spec; defaults are
# conservative-low. Documentation: docs/OPERATIONS.md "Cost & activity
# ledger" section.
KLING_VIDEO_SUBMIT_EUR = _env_float("COST_KLING_VIDEO_SUBMIT_EUR", 0.50)
TAVILY_SEARCH_EUR = _env_float("COST_TAVILY_SEARCH_EUR", 0.01)
APIFY_SCRAPE_EUR_PER_AD = _env_float("COST_APIFY_SCRAPE_EUR_PER_AD", 0.002)
META_INSIGHTS_EUR = _env_float("COST_META_INSIGHTS_EUR", 0.00)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


_DDL = """
CREATE TABLE IF NOT EXISTS cost_events (
    id                 TEXT PRIMARY KEY,
    provider           TEXT NOT NULL,
    event_type         TEXT NOT NULL,
    client_id          TEXT,
    prospect_id        TEXT,
    task_type          TEXT,
    units              REAL,
    estimated_cost_eur REAL,
    metadata_json      TEXT,
    created_at         TEXT NOT NULL
)
"""

_INDEX_CREATED_AT = """
CREATE INDEX IF NOT EXISTS idx_cost_events_created_at
    ON cost_events (created_at DESC)
"""

_INDEX_PROVIDER_CREATED = """
CREATE INDEX IF NOT EXISTS idx_cost_events_provider_created
    ON cost_events (provider, created_at DESC)
"""

_INDEX_CLIENT_CREATED = """
CREATE INDEX IF NOT EXISTS idx_cost_events_client_created
    ON cost_events (client_id, created_at DESC)
"""


# --------------------------------------------------------------------------- #
# Connection plumbing - mirrors services/mcp_pending_store.py
# --------------------------------------------------------------------------- #


def _set_wal_mode(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")


def _init_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        _set_wal_mode(conn)
        conn.execute(_DDL)
        conn.execute(_INDEX_CREATED_AT)
        conn.execute(_INDEX_PROVIDER_CREATED)
        conn.execute(_INDEX_CLIENT_CREATED)
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
# record_event - the ONE function that must never raise
# --------------------------------------------------------------------------- #


def record_event(
    *,
    provider: str,
    event_type: str,
    client_id: Optional[str] = None,
    prospect_id: Optional[str] = None,
    task_type: Optional[str] = None,
    units: Optional[float] = None,
    estimated_cost_eur: Optional[float] = None,
    metadata: Optional[dict[str, Any]] = None,
    db_path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime] = None,
) -> None:
    """Append one row to the cost ledger.

    Safety: this function NEVER propagates an exception to the caller.
    Cost logging is observability and must never break the success path
    of a paid provider call. Any failure (invalid input, disk full,
    schema error, JSON encode failure) is swallowed; a single-line
    warning is written to stderr so the operator can investigate
    out-of-band.

    Invalid provider / event_type (empty string, non-string) result in a
    silent no-op for the same reason.

    Other helpers (`list_recent`, `summarise_*`) raise normally - tests
    and the Streamlit UI can distinguish "logging failed" from "query
    failed" by which function bubbles the error.
    """
    try:
        # Light validation. Invalid input -> silent no-op (cannot break
        # a Kling submit by passing a bad provider name).
        if not isinstance(provider, str) or not provider.strip():
            return
        if not isinstance(event_type, str) or not event_type.strip():
            return

        metadata_json: Optional[str] = None
        if metadata is not None:
            try:
                metadata_json = json.dumps(metadata, default=str, sort_keys=True)
            except (TypeError, ValueError):
                # Unserialisable metadata -> drop the metadata, keep the row.
                metadata_json = None

        created_at = (now or datetime.now(timezone.utc)).isoformat()
        row_id = uuid.uuid4().hex

        with _db(db_path) as conn:
            conn.execute(
                "INSERT INTO cost_events ("
                "  id, provider, event_type, client_id, prospect_id, "
                "  task_type, units, estimated_cost_eur, metadata_json, "
                "  created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id, provider.strip(), event_type.strip(),
                    client_id, prospect_id, task_type,
                    units, estimated_cost_eur, metadata_json,
                    created_at,
                ),
            )
    except Exception as e:
        # Last-line guarantee: never raise from record_event. We print a
        # one-line diagnostic so the operator notices something is off
        # if cost rows stop appearing; never the secret-bearing metadata.
        try:
            print(
                f"cost_ledger.record_event: swallowed {type(e).__name__}: {e}",
                file=sys.stderr,
            )
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Read helpers - these CAN raise so tests catch query bugs
# --------------------------------------------------------------------------- #


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    metadata: Optional[dict[str, Any]] = None
    raw_meta = row["metadata_json"]
    if raw_meta:
        try:
            metadata = json.loads(raw_meta)
        except json.JSONDecodeError:
            metadata = None
    return {
        "id": row["id"],
        "provider": row["provider"],
        "event_type": row["event_type"],
        "client_id": row["client_id"],
        "prospect_id": row["prospect_id"],
        "task_type": row["task_type"],
        "units": row["units"],
        "estimated_cost_eur": row["estimated_cost_eur"],
        "metadata": metadata,
        "created_at": row["created_at"],
    }


def list_recent(
    *,
    limit: int = 10,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Most-recent cost events, newest first. Powers the Streamlit
    'last 10 events' table."""
    if limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
    with _db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM cost_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def summarise_by_provider(
    *,
    since: Optional[datetime] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, dict[str, float]]:
    """Sum units + estimated_cost_eur grouped by provider.

    Returns: `{provider: {events: int, units: float, cost_eur: float}}`.
    Providers with no rows in the window do not appear.

    `since` filters by `created_at >= since.isoformat()`. None means
    no time filter.
    """
    return _summarise_by(
        column="provider", since=since, db_path=db_path, none_key="(unknown)",
    )


def summarise_by_client(
    *,
    since: Optional[datetime] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, dict[str, float]]:
    """Sum units + estimated_cost_eur grouped by client_id.

    Outreach events (client_id IS NULL) appear under the '(unknown)'
    sentinel key. Same return shape as `summarise_by_provider`.
    """
    return _summarise_by(
        column="client_id", since=since, db_path=db_path, none_key="(unknown)",
    )


def summarise_by_day(
    *,
    since: Optional[datetime] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, dict[str, float]]:
    """Sum units + estimated_cost_eur grouped by ISO date (YYYY-MM-DD).

    Useful for the optional "spend per day" chart in a future iteration;
    not currently rendered by the Streamlit section but exposed for
    daily-summary / scripted reporting.
    """
    if since is not None:
        since_iso = since.isoformat()
        sql = (
            "SELECT substr(created_at, 1, 10) AS day, "
            "       COUNT(*) AS events, "
            "       COALESCE(SUM(units), 0) AS units, "
            "       COALESCE(SUM(estimated_cost_eur), 0) AS cost_eur "
            "FROM cost_events WHERE created_at >= ? "
            "GROUP BY day ORDER BY day DESC"
        )
        params: tuple = (since_iso,)
    else:
        sql = (
            "SELECT substr(created_at, 1, 10) AS day, "
            "       COUNT(*) AS events, "
            "       COALESCE(SUM(units), 0) AS units, "
            "       COALESCE(SUM(estimated_cost_eur), 0) AS cost_eur "
            "FROM cost_events GROUP BY day ORDER BY day DESC"
        )
        params = ()

    with _db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return {
        r["day"]: {
            "events": int(r["events"]),
            "units": float(r["units"] or 0),
            "cost_eur": float(r["cost_eur"] or 0),
        }
        for r in rows
    }


def _summarise_by(
    *,
    column: str,
    since: Optional[datetime],
    db_path: Path,
    none_key: str,
) -> dict[str, dict[str, float]]:
    """Shared aggregator for by-provider / by-client.

    `column` is interpolated into the SQL so it must be a static
    identifier (we only call it with literals defined in this module).
    """
    if column not in ("provider", "client_id"):
        raise ValueError(f"unsupported group-by column {column!r}")

    if since is not None:
        sql = (
            f"SELECT {column} AS k, "
            f"       COUNT(*) AS events, "
            f"       COALESCE(SUM(units), 0) AS units, "
            f"       COALESCE(SUM(estimated_cost_eur), 0) AS cost_eur "
            f"FROM cost_events WHERE created_at >= ? "
            f"GROUP BY {column}"
        )
        params: tuple = (since.isoformat(),)
    else:
        sql = (
            f"SELECT {column} AS k, "
            f"       COUNT(*) AS events, "
            f"       COALESCE(SUM(units), 0) AS units, "
            f"       COALESCE(SUM(estimated_cost_eur), 0) AS cost_eur "
            f"FROM cost_events GROUP BY {column}"
        )
        params = ()

    with _db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    out: dict[str, dict[str, float]] = {}
    for r in rows:
        key = r["k"] if r["k"] is not None else none_key
        out[key] = {
            "events": int(r["events"]),
            "units": float(r["units"] or 0),
            "cost_eur": float(r["cost_eur"] or 0),
        }
    return out
