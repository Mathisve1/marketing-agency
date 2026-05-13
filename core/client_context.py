"""Read/write the client silo:
  - MASTER_CONTEXT.md      static metadata (brand, locale, benchmarks)
  - client_data.db         SQLite: hooks/motions/constraints (V1.2+),
                           video_plans + video_jobs (V1.4+)
  - references/            asset directories

V1.3: SQLite connections enable WAL journaling so reads and writes from
different processes (Streamlit + MCP server + agents) don't block each
other.

V1.4: performance_log.json is dropped. Producer plan + job state lives
in client_data.db (video_plans + video_jobs tables) so concurrent polls
from the LLM and the UI no longer race on a JSON file. The empty
template performance_log.json file is kept on disk only because the
template copytree includes it; nothing in core code reads or writes it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Union

import yaml
from pydantic import ValidationError

from core.context_schema import (
    ALL_DDL,
    ClientIdentity,
    Confidence,
    JobStatus,
    MasterContext,
    NegativeConstraint,
    PlanStatus,
    ReferralMotion,
    Severity,
    VideoJob,
    VideoPlan,
    WinningHook,
)

FRONTMATTER_DELIMITER = "---"
CLIENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_CLIENTS_ROOT = Path("clients")
TEMPLATE_DIR_NAME = "_template"

# Subdirectories every client silo MUST have. Created defensively in
# onboard() so a fresh checkout (where git dropped the empty template
# subdirs) never produces a half-broken silo. Keep in lockstep with
# scripts/bootstrap_env.py and the Producer's asset_paths() reads.
_REQUIRED_CLIENT_SUBDIRS = (
    "references/characters",
    "references/products",
    "references/referral_videos",
    "outputs/videos",
    "outputs/reports",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _validate_client_id(client_id: str) -> None:
    if not CLIENT_ID_PATTERN.match(client_id):
        raise ValueError(
            f"Invalid client_id {client_id!r}: must be lowercase alphanumeric "
            f"with underscores/hyphens, max 64 chars, starting with [a-z0-9]."
        )


def _split_frontmatter(text: str) -> tuple[str, str]:
    normalised = text.replace("\r\n", "\n")
    if not normalised.startswith(FRONTMATTER_DELIMITER + "\n"):
        raise ValueError("MASTER_CONTEXT.md must start with '---' YAML frontmatter.")
    rest = normalised[len(FRONTMATTER_DELIMITER) + 1:]
    closing = rest.find("\n" + FRONTMATTER_DELIMITER + "\n")
    if closing == -1:
        raise ValueError("MASTER_CONTEXT.md frontmatter is not properly closed with '---'.")
    yaml_block = rest[:closing]
    body = rest[closing + len(FRONTMATTER_DELIMITER) + 2:]
    return yaml_block, body


def _join_frontmatter(frontmatter: MasterContext, body: str) -> str:
    yaml_str = yaml.safe_dump(
        frontmatter.model_dump(mode="json", exclude_none=False),
        sort_keys=False,
        allow_unicode=True,
    )
    return f"{FRONTMATTER_DELIMITER}\n{yaml_str}{FRONTMATTER_DELIMITER}\n{body}"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _next_id_sql(conn: sqlite3.Connection, table: str, prefix: str) -> str:
    """e.g. _next_id_sql(conn, 'winning_hooks', 'WH') -> 'WH-004'."""
    cursor = conn.execute(
        f"SELECT id FROM {table} WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
        (f"{prefix}-%",),
    )
    row = cursor.fetchone()
    if row is None:
        return f"{prefix}-001"
    try:
        num = int(row[0].split("-")[1])
    except (IndexError, ValueError):
        return f"{prefix}-001"
    return f"{prefix}-{(num + 1):03d}"


def _coerce_enum_value(value: Union[str, Confidence, Severity, None]) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "value"):
        return value.value
    return str(value).lower()


def _set_wal_mode(conn: sqlite3.Connection) -> None:
    """Enable Write-Ahead Logging. With WAL, readers do not block writers
    and writers do not block readers - critical now that Streamlit + MCP +
    a running agent may concurrently touch the same client_data.db.

    WAL mode is sticky (persisted in the DB header), so once set on a file
    it survives across connections. Setting it on every connect costs a
    single pragma round-trip but guarantees the mode even if a third-party
    tool flipped it back to DELETE.
    """
    conn.execute("PRAGMA journal_mode=WAL")


# --------------------------------------------------------------------------- #
# ClientContext
# --------------------------------------------------------------------------- #


class ClientContext:
    """Per-client file silo + SQLite database. Single chokepoint for all I/O."""

    def __init__(self, client_id: str, clients_root: Optional[Path] = None):
        _validate_client_id(client_id)
        self.client_id = client_id
        self.clients_root = (clients_root or DEFAULT_CLIENTS_ROOT).resolve()
        self.root = self.clients_root / client_id
        self.context_path = self.root / "MASTER_CONTEXT.md"
        self.performance_log_path = self.root / "performance_log.json"
        self.db_path = self.root / "client_data.db"

    # ---------- lifecycle ----------

    @classmethod
    def load(cls, client_id: str, clients_root: Optional[Path] = None) -> "ClientContext":
        ctx = cls(client_id, clients_root)
        if not ctx.context_path.exists():
            raise FileNotFoundError(
                f"No MASTER_CONTEXT.md for client {client_id!r} at {ctx.context_path}. "
                f"Call ClientContext.onboard(...) to create it from the template."
            )
        ctx.read()
        ctx._init_db()
        ctx._migrate_yaml_to_sql_if_needed()
        return ctx

    @classmethod
    def onboard(
        cls,
        client_id: str,
        name: str,
        locale: str = "nl-BE",
        clients_root: Optional[Path] = None,
    ) -> "ClientContext":
        """Clone _template/ into clients/<client_id>/ and stamp identity.

        Defensively creates every required subdirectory after the copy so
        a fresh checkout (where git dropped the empty template dirs) still
        produces a fully working silo. The Producer reads from references/
        and writes to outputs/; if either is missing it crashes with
        FileNotFoundError before the LLM ever runs.
        """
        _validate_client_id(client_id)
        if client_id == TEMPLATE_DIR_NAME:
            raise ValueError(f"{TEMPLATE_DIR_NAME!r} is reserved.")
        ctx = cls(client_id, clients_root)
        if ctx.root.exists():
            raise FileExistsError(f"Client {client_id!r} already exists at {ctx.root}.")
        template = ctx.clients_root / TEMPLATE_DIR_NAME
        if not template.exists():
            raise FileNotFoundError(f"Template missing at {template}.")
        shutil.copytree(template, ctx.root)
        for subdir in _REQUIRED_CLIENT_SUBDIRS:
            (ctx.root / subdir).mkdir(parents=True, exist_ok=True)
        fm, body = ctx.read()
        fm.client = ClientIdentity(
            id=client_id, name=name, locale=locale,
            last_updated=datetime.now(timezone.utc),
        )
        body = body.replace("[Client Name]", name)
        ctx.write(fm, body)
        ctx._init_db()
        return ctx

    # ---------- SQLite plumbing ----------

    def _init_db(self) -> None:
        """Create tables + indexes if missing. Enables WAL on the file.

        Idempotent: CREATE IF NOT EXISTS for tables/indexes; PRAGMA WAL is
        a no-op when the DB is already in WAL mode.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            _set_wal_mode(conn)
            for ddl in ALL_DDL:
                conn.execute(ddl)
            conn.commit()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """Open a SQLite connection in WAL journal mode. Commits on success,
        rolls back on exception, always closes.

        WAL is set on every connection so a concurrent process that may
        have flipped the file out of WAL mode still gets the non-blocking
        read/write semantics for this session.
        """
        conn = sqlite3.connect(str(self.db_path))
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

    def _read_raw_frontmatter(self) -> dict:
        text = self.context_path.read_text(encoding="utf-8")
        yaml_block, _body = _split_frontmatter(text)
        return yaml.safe_load(yaml_block) or {}

    def _migrate_yaml_to_sql_if_needed(self) -> int:
        """One-shot: if SQL tables are empty AND legacy YAML lists exist,
        move them into SQLite and strip the dropped keys from MASTER_CONTEXT.md."""
        with self._db() as conn:
            total = sum(
                conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("winning_hooks", "referral_motions", "negative_constraints")
            )
        if total > 0:
            return 0

        raw = self._read_raw_frontmatter()
        legacy_keys = ("winning_hooks", "referral_motions", "negative_constraints")
        if not any(raw.get(k) for k in legacy_keys):
            return 0

        migrated = 0
        for entry in raw.get("winning_hooks") or []:
            try:
                self.add_winning_hook(WinningHook.model_validate(entry))
                migrated += 1
            except (ValidationError, KeyError, TypeError):
                continue
        for entry in raw.get("referral_motions") or []:
            try:
                self.add_referral_motion(ReferralMotion.model_validate(entry))
                migrated += 1
            except (ValidationError, KeyError, TypeError):
                continue
        for entry in raw.get("negative_constraints") or []:
            try:
                self.add_negative_constraint(NegativeConstraint.model_validate(entry))
                migrated += 1
            except (ValidationError, KeyError, TypeError):
                continue

        if migrated > 0:
            fm, body = self.read()
            self.write(fm, body)

        return migrated

    # ---------- read/write the static MASTER_CONTEXT.md ----------

    def read(self) -> tuple[MasterContext, str]:
        text = self.context_path.read_text(encoding="utf-8")
        yaml_block, body = _split_frontmatter(text)
        raw = yaml.safe_load(yaml_block) or {}
        return MasterContext.model_validate(raw), body

    def write(self, frontmatter: MasterContext, body: str) -> None:
        frontmatter.client.last_updated = datetime.now(timezone.utc)
        _atomic_write(self.context_path, _join_frontmatter(frontmatter, body))

    def append_narrative(self, section_heading: str, paragraph: str) -> None:
        fm, body = self.read()
        marker = f"## {section_heading}"
        if marker in body:
            body = body.replace(marker, f"{marker}\n\n{paragraph}\n", 1)
        else:
            body = f"{body.rstrip()}\n\n{marker}\n\n{paragraph}\n"
        self.write(fm, body)

    # ---------- SQL-backed mutators ----------

    def add_winning_hook(self, hook: WinningHook) -> str:
        with self._db() as conn:
            if not hook.id:
                hook = hook.model_copy(update={"id": _next_id_sql(conn, "winning_hooks", "WH")})
            conn.execute(
                "INSERT INTO winning_hooks "
                "(id, pattern, description, source_ad_id, days_active, "
                " confidence, added_by, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hook.id,
                    hook.pattern,
                    hook.description,
                    hook.source_ad_id,
                    hook.days_active,
                    hook.confidence.value,
                    hook.added_by.value,
                    hook.added_at.isoformat(),
                ),
            )
        return hook.id

    def add_referral_motion(self, motion: ReferralMotion) -> str:
        with self._db() as conn:
            if not motion.id:
                motion = motion.model_copy(
                    update={"id": _next_id_sql(conn, "referral_motions", "RM")}
                )
            conn.execute(
                "INSERT INTO referral_motions "
                "(id, description, reference_path, pacing, camera_style, "
                " duration_seconds, added_by, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    motion.id,
                    motion.description,
                    motion.reference_path,
                    motion.pacing,
                    motion.camera_style,
                    motion.duration_seconds,
                    motion.added_by.value,
                    motion.added_at.isoformat(),
                ),
            )
        return motion.id

    def add_negative_constraint(self, constraint: NegativeConstraint) -> str:
        with self._db() as conn:
            if not constraint.id:
                constraint = constraint.model_copy(
                    update={"id": _next_id_sql(conn, "negative_constraints", "NC")}
                )
            conn.execute(
                "INSERT INTO negative_constraints "
                "(id, rule, reason, severity, added_by, added_at, source_log_entries) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    constraint.id,
                    constraint.rule,
                    constraint.reason,
                    constraint.severity.value,
                    constraint.added_by.value,
                    constraint.added_at.isoformat(),
                    json.dumps(constraint.source_log_entries),
                ),
            )
        return constraint.id

    # ---------- SQL-backed fetchers ----------

    def get_winning_hooks(
        self,
        *,
        confidence: Union[Confidence, str, None] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[WinningHook]:
        sql = "SELECT * FROM winning_hooks"
        params: list[Any] = []
        clauses: list[str] = []
        if confidence is not None:
            clauses.append("confidence = ?")
            params.append(_coerce_enum_value(confidence))
        if since is not None:
            clauses.append("added_at >= ?")
            params.append(since.isoformat())
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY added_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with self._db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [WinningHook.model_validate(dict(r)) for r in rows]

    def get_winning_hook(self, hook_id: str) -> Optional[WinningHook]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM winning_hooks WHERE id = ?", (hook_id,)
            ).fetchone()
        return WinningHook.model_validate(dict(row)) if row else None

    def get_referral_motions(
        self,
        *,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> list[ReferralMotion]:
        sql = "SELECT * FROM referral_motions"
        params: list[Any] = []
        if since is not None:
            sql += " WHERE added_at >= ?"
            params.append(since.isoformat())
        sql += " ORDER BY added_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with self._db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ReferralMotion.model_validate(dict(r)) for r in rows]

    def get_referral_motion(self, motion_id: str) -> Optional[ReferralMotion]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM referral_motions WHERE id = ?", (motion_id,)
            ).fetchone()
        return ReferralMotion.model_validate(dict(row)) if row else None

    def get_negative_constraints(
        self,
        *,
        severity: Union[Severity, str, None] = None,
        limit: Optional[int] = None,
    ) -> list[NegativeConstraint]:
        sql = "SELECT * FROM negative_constraints"
        params: list[Any] = []
        if severity is not None:
            sql += " WHERE severity = ?"
            params.append(_coerce_enum_value(severity))
        sql += " ORDER BY added_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with self._db() as conn:
            rows = conn.execute(sql, params).fetchall()

        out: list[NegativeConstraint] = []
        for r in rows:
            d = dict(r)
            d["source_log_entries"] = json.loads(d.get("source_log_entries") or "[]")
            out.append(NegativeConstraint.model_validate(d))
        return out

    # ---------- assets ----------

    def asset_paths(self) -> dict[str, Path]:
        fm, _ = self.read()
        inv = fm.asset_inventory
        return {
            "products": (self.root / inv.products_dir).resolve(),
            "characters": (self.root / inv.characters_dir).resolve(),
            "referral_videos": (self.root / inv.referral_videos_dir).resolve(),
        }

    def list_assets(self, kind: str) -> list[Path]:
        base = self.asset_paths()[kind]
        if not base.exists():
            return []
        return sorted(p for p in base.glob("*") if p.is_file())

    # ---------- video plans (V1.4 - replaces performance_log.json planning side) ----------

    def create_video_plan(self, plan: VideoPlan) -> str:
        """Persist a compiled Kling brief with status='pending_approval'.
        Returns the auto-assigned plan_id (VP-NNN)."""
        with self._db() as conn:
            if not plan.id:
                plan = plan.model_copy(
                    update={"id": _next_id_sql(conn, "video_plans", "VP")}
                )
            conn.execute(
                "INSERT INTO video_plans "
                "(id, status, hook_id, motion_id, character_asset, product_asset, "
                " duration, aspect_ratio, mode, cfg_scale, prompt, negative_prompt, "
                " enforced_constraint_ids, created_at, decided_at, decided_by, "
                " submit_attempts, submit_attempted_at, submit_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan.id,
                    plan.status.value,
                    plan.hook_id,
                    plan.motion_id,
                    plan.character_asset,
                    plan.product_asset,
                    plan.duration,
                    plan.aspect_ratio,
                    plan.mode,
                    plan.cfg_scale,
                    plan.prompt,
                    plan.negative_prompt,
                    json.dumps(plan.enforced_constraint_ids),
                    plan.created_at.isoformat(),
                    plan.decided_at.isoformat() if plan.decided_at else None,
                    plan.decided_by,
                    plan.submit_attempts,
                    plan.submit_attempted_at.isoformat() if plan.submit_attempted_at else None,
                    plan.submit_error,
                ),
            )
        return plan.id

    def get_video_plan(self, plan_id: str) -> Optional[VideoPlan]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM video_plans WHERE id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["enforced_constraint_ids"] = json.loads(d.get("enforced_constraint_ids") or "[]")
        return VideoPlan.model_validate(d)

    def list_video_plans(
        self,
        *,
        status: Union[PlanStatus, str, None] = None,
        limit: Optional[int] = None,
    ) -> list[VideoPlan]:
        sql = "SELECT * FROM video_plans"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(_coerce_enum_value(status))
        sql += " ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._db() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[VideoPlan] = []
        for r in rows:
            d = dict(r)
            d["enforced_constraint_ids"] = json.loads(d.get("enforced_constraint_ids") or "[]")
            out.append(VideoPlan.model_validate(d))
        return out

    def reject_video_plan(self, plan_id: str, *, decided_by: str = "human") -> bool:
        """Atomically transition pending_approval OR submitting -> rejected.

        V1.4.1: now also accepts 'submitting' as a valid source so the
        operator can clear a stuck plan (e.g. an MCP died mid-submit).
        Caller must understand the timeout caveat: if the plan was
        'submitting', the provider may have still accepted the request -
        check Kling dashboard before assuming nothing happened.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE video_plans "
                "SET status = ?, decided_at = ?, decided_by = ? "
                "WHERE id = ? AND status IN (?, ?)",
                (
                    PlanStatus.REJECTED.value, now, decided_by,
                    plan_id,
                    PlanStatus.PENDING_APPROVAL.value,
                    PlanStatus.SUBMITTING.value,
                ),
            )
        return cur.rowcount > 0

    def claim_plan_for_submission(
        self, plan_id: str, *, claimed_by: str = "human"
    ) -> bool:
        """Atomic compare-and-set: pending_approval -> submitting.

        ONLY the caller that gets True back may call Kling for this plan.
        If two processes (e.g. Streamlit double-click, MCP retry, parallel
        workers) try to claim simultaneously, exactly ONE wins because
        SQLite serializes the UPDATE statement and the WHERE clause filters
        on the source status.

        Side effects on success:
          - status: pending_approval -> submitting
          - submit_attempts: incremented
          - submit_attempted_at: set to now (UTC)
          - submit_error: NOT cleared - preserves the prior failure reason
            so the operator's audit trail keeps it visible until success.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE video_plans "
                "SET status = ?, "
                "    submit_attempts = submit_attempts + 1, "
                "    submit_attempted_at = ? "
                "WHERE id = ? AND status = ?",
                (
                    PlanStatus.SUBMITTING.value,
                    now,
                    plan_id,
                    PlanStatus.PENDING_APPROVAL.value,
                ),
            )
        return cur.rowcount > 0

    def release_plan_after_submit_failure(
        self, plan_id: str, error: str
    ) -> bool:
        """Atomic compare-and-set: submitting -> pending_approval, with the
        error string preserved on the row.

        Called by producer_submit_node when the Kling submit_omni_video
        call raises. Records the error so the operator can see WHY the
        last attempt failed when re-reviewing the plan.

        IMPORTANT TIMEOUT CAVEAT (already noted in the error string for
        timeout-shaped failures): if the underlying error was a timeout
        or other ambiguous network failure, Kling MAY have accepted the
        submission. Re-approving the plan would then submit a duplicate.
        The operator should check the Kling dashboard before re-approving
        a plan whose submit_error mentions a timeout.
        """
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE video_plans "
                "SET status = ?, submit_error = ? "
                "WHERE id = ? AND status = ?",
                (
                    PlanStatus.PENDING_APPROVAL.value,
                    error,
                    plan_id,
                    PlanStatus.SUBMITTING.value,
                ),
            )
        return cur.rowcount > 0

    def mark_plan_submitted(self, plan_id: str, *, decided_by: str = "human") -> bool:
        """Atomically transition submitting -> submitted (V1.4.1).

        Precondition: the caller must have already won the claim via
        claim_plan_for_submission. Clearing submit_error here records that
        the latest attempt succeeded (the row otherwise still carries the
        last failure reason from a prior reverted attempt).
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE video_plans "
                "SET status = ?, decided_at = ?, decided_by = ?, submit_error = NULL "
                "WHERE id = ? AND status = ?",
                (
                    PlanStatus.SUBMITTED.value, now, decided_by,
                    plan_id, PlanStatus.SUBMITTING.value,
                ),
            )
        return cur.rowcount > 0

    def list_unresolved_video_plans(self) -> list[VideoPlan]:
        """Plans the operator may still need to act on: pending_approval
        (awaiting first decision OR reverted from a failed submit) and
        submitting (in flight, possibly stuck if MCP/UI crashed). Used by
        the Streamlit log tab's stale-plan section."""
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM video_plans WHERE status IN (?, ?) "
                "ORDER BY created_at DESC",
                (
                    PlanStatus.PENDING_APPROVAL.value,
                    PlanStatus.SUBMITTING.value,
                ),
            ).fetchall()
        out: list[VideoPlan] = []
        for r in rows:
            d = dict(r)
            d["enforced_constraint_ids"] = json.loads(d.get("enforced_constraint_ids") or "[]")
            out.append(VideoPlan.model_validate(d))
        return out

    # ---------- video jobs (V1.4 - replaces performance_log.json runtime side) ----------

    def create_video_job(self, job: VideoJob) -> str:
        """Insert a new Kling submission record. Status starts 'pending'."""
        with self._db() as conn:
            conn.execute(
                "INSERT INTO video_jobs "
                "(kling_task_id, plan_id, status, video_path, error, "
                " submitted_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job.kling_task_id,
                    job.plan_id,
                    job.status.value,
                    job.video_path,
                    job.error,
                    job.submitted_at.isoformat(),
                    job.completed_at.isoformat() if job.completed_at else None,
                ),
            )
        return job.kling_task_id

    def get_video_job(self, kling_task_id: str) -> Optional[VideoJob]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM video_jobs WHERE kling_task_id = ?", (kling_task_id,)
            ).fetchone()
        return VideoJob.model_validate(dict(row)) if row else None

    def list_video_jobs(
        self,
        *,
        status: Union[JobStatus, str, None] = None,
        limit: Optional[int] = None,
    ) -> list[VideoJob]:
        sql = "SELECT * FROM video_jobs"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(_coerce_enum_value(status))
        sql += " ORDER BY submitted_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [VideoJob.model_validate(dict(r)) for r in rows]

    def update_video_job_by_task_id(
        self,
        kling_task_id: str,
        *,
        status: Optional[Union[JobStatus, str]] = None,
        video_path: Optional[str] = None,
        error: Optional[str] = None,
        completed_at: Optional[datetime] = None,
    ) -> bool:
        """Atomic SQL UPDATE - replaces the lost-update-prone JSON path.

        Sets only the fields the caller passes. Returns True if a row was
        affected. Concurrent calls from the Producer LLM and the UI's
        Check Status button are now safe: each goes through SQLite's
        per-statement lock instead of clobbering a shared JSON file.
        """
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(_coerce_enum_value(status))
        if video_path is not None:
            sets.append("video_path = ?")
            params.append(video_path)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at.isoformat())
        if not sets:
            return False
        params.append(kling_task_id)
        with self._db() as conn:
            cur = conn.execute(
                f"UPDATE video_jobs SET {', '.join(sets)} WHERE kling_task_id = ?",
                params,
            )
        return cur.rowcount > 0


def list_clients(clients_root: Optional[Path] = None) -> list[str]:
    root = (clients_root or DEFAULT_CLIENTS_ROOT).resolve()
    if not root.exists():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )
