"""Read/write the client silo:
  - MASTER_CONTEXT.md      static metadata (brand, locale, benchmarks)
  - client_data.db         SQLite for the three dynamic lists (V1.2+)
  - performance_log.json   append-only generation log
  - references/            asset directories

V1.3: SQLite connections enable WAL journaling so reads and writes from
different processes (Streamlit + MCP server + agents) don't block each
other. A new update_performance_entry_by_task_id() supports the
Producer's async submit/poll pattern.
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
    MasterContext,
    NegativeConstraint,
    ReferralMotion,
    Severity,
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

    # ---------- performance log ----------

    def read_performance_log(self) -> list[dict]:
        if not self.performance_log_path.exists():
            return []
        raw = self.performance_log_path.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else []

    def append_performance_entry(self, entry: dict) -> None:
        entries = self.read_performance_log()
        entries.append(entry)
        _atomic_write(
            self.performance_log_path,
            json.dumps(entries, indent=2, default=str, ensure_ascii=False),
        )

    def update_performance_entry_by_task_id(
        self,
        task_id: str,
        updates: dict,
    ) -> bool:
        """In-place merge `updates` into the entry whose kling_task_id matches.

        Powers the Producer's async submit/poll lifecycle - the submit step
        writes a 'pending' entry; check_video_status mutates that same entry
        to 'completed' or 'failed' once Kling reports a terminal state.

        Returns True if a matching entry was found and updated, False otherwise.
        Atomic via _atomic_write (temp file -> os.replace).
        """
        entries = self.read_performance_log()
        matched = False
        for entry in entries:
            if entry.get("kling_task_id") == task_id:
                entry.update(updates)
                matched = True
                break
        if matched:
            _atomic_write(
                self.performance_log_path,
                json.dumps(entries, indent=2, default=str, ensure_ascii=False),
            )
        return matched


def list_clients(clients_root: Optional[Path] = None) -> list[str]:
    root = (clients_root or DEFAULT_CLIENTS_ROOT).resolve()
    if not root.exists():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )
