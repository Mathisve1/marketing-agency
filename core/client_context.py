"""Read/write the client silo (MASTER_CONTEXT.md, performance_log.json, assets).

All cross-agent communication routes through this module — agents never touch
the files directly, so the schema is enforced in one place and writes are atomic.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from core.context_schema import (
    ClientIdentity,
    MasterContext,
    NegativeConstraint,
    ReferralMotion,
    WinningHook,
)

FRONTMATTER_DELIMITER = "---"
CLIENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_CLIENTS_ROOT = Path("clients")
TEMPLATE_DIR_NAME = "_template"


def _validate_client_id(client_id: str) -> None:
    # Doubles as path-traversal guard: pattern rejects '/', '\', '..'.
    if not CLIENT_ID_PATTERN.match(client_id):
        raise ValueError(
            f"Invalid client_id {client_id!r}: must be lowercase alphanumeric "
            f"with underscores/hyphens, max 64 chars, starting with [a-z0-9]."
        )


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a markdown file with YAML frontmatter into (yaml_str, body_str)."""
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
    # Write to a sibling temp file, then os.replace — survives crashes mid-write.
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


def _next_id(prefix: str, existing_ids: list[str]) -> str:
    """e.g. _next_id('WH', ['WH-001', 'WH-003']) -> 'WH-004'."""
    nums: list[int] = []
    for raw in existing_ids:
        m = re.match(rf"^{prefix}-(\d+)$", raw)
        if m:
            nums.append(int(m.group(1)))
    next_num = (max(nums) + 1) if nums else 1
    return f"{prefix}-{next_num:03d}"


class ClientContext:
    """Per-client file silo. Single chokepoint for MASTER_CONTEXT.md I/O."""

    def __init__(self, client_id: str, clients_root: Optional[Path] = None):
        _validate_client_id(client_id)
        self.client_id = client_id
        self.clients_root = (clients_root or DEFAULT_CLIENTS_ROOT).resolve()
        self.root = self.clients_root / client_id
        self.context_path = self.root / "MASTER_CONTEXT.md"
        self.performance_log_path = self.root / "performance_log.json"

    # ---------- lifecycle ----------

    @classmethod
    def load(cls, client_id: str, clients_root: Optional[Path] = None) -> "ClientContext":
        ctx = cls(client_id, clients_root)
        if not ctx.context_path.exists():
            raise FileNotFoundError(
                f"No MASTER_CONTEXT.md for client {client_id!r} at {ctx.context_path}. "
                f"Call ClientContext.onboard(...) to create it from the template."
            )
        # Validate up-front so we fail fast rather than later inside an agent.
        ctx.read()
        return ctx

    @classmethod
    def onboard(
        cls,
        client_id: str,
        name: str,
        locale: str = "nl-BE",
        clients_root: Optional[Path] = None,
    ) -> "ClientContext":
        """Clone _template/ into clients/<client_id>/ and stamp identity."""
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
        fm, body = ctx.read()
        fm.client = ClientIdentity(
            id=client_id,
            name=name,
            locale=locale,
            last_updated=datetime.now(timezone.utc),
        )
        body = body.replace("[Client Name]", name)
        ctx.write(fm, body)
        return ctx

    # ---------- read/write ----------

    def read(self) -> tuple[MasterContext, str]:
        text = self.context_path.read_text(encoding="utf-8")
        yaml_block, body = _split_frontmatter(text)
        raw = yaml.safe_load(yaml_block) or {}
        return MasterContext.model_validate(raw), body

    def write(self, frontmatter: MasterContext, body: str) -> None:
        frontmatter.client.last_updated = datetime.now(timezone.utc)
        _atomic_write(self.context_path, _join_frontmatter(frontmatter, body))

    # ---------- structured mutators (agent-friendly) ----------

    def add_winning_hook(self, hook: WinningHook) -> str:
        fm, body = self.read()
        if not hook.id:
            hook = hook.model_copy(update={"id": _next_id("WH", [h.id for h in fm.winning_hooks])})
        fm.winning_hooks.append(hook)
        self.write(fm, body)
        return hook.id

    def add_referral_motion(self, motion: ReferralMotion) -> str:
        fm, body = self.read()
        if not motion.id:
            motion = motion.model_copy(update={"id": _next_id("RM", [m.id for m in fm.referral_motions])})
        fm.referral_motions.append(motion)
        self.write(fm, body)
        return motion.id

    def add_negative_constraint(self, constraint: NegativeConstraint) -> str:
        fm, body = self.read()
        if not constraint.id:
            constraint = constraint.model_copy(
                update={"id": _next_id("NC", [c.id for c in fm.negative_constraints])}
            )
        fm.negative_constraints.append(constraint)
        self.write(fm, body)
        return constraint.id

    def append_narrative(self, section_heading: str, paragraph: str) -> None:
        """Append a paragraph under '## <heading>' in the markdown body."""
        fm, body = self.read()
        marker = f"## {section_heading}"
        if marker in body:
            body = body.replace(marker, f"{marker}\n\n{paragraph}\n", 1)
        else:
            body = f"{body.rstrip()}\n\n{marker}\n\n{paragraph}\n"
        self.write(fm, body)

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


def list_clients(clients_root: Optional[Path] = None) -> list[str]:
    root = (clients_root or DEFAULT_CLIENTS_ROOT).resolve()
    if not root.exists():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )
