"""ProspectStore - the Outreach agent's equivalent of ClientContext.

Manages the prospects/ tree (one folder per prospect). Each prospect carries:
  - audit.json: scraped Meta ads + Strategist-style hook/motion extraction
  - pitch.pdf:  branded 'Brand Audit & Agency Pitch' PDF

Promotion-to-client (used by the Streamlit 'Promote to active client' button)
lives here as well so the UI and CLI share the same code path.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.client_context import ClientContext
from core.context_schema import (
    AddedBy,
    Confidence,
    ReferralMotion,
    WinningHook,
)


PROSPECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_PROSPECTS_ROOT = Path("prospects")


def _validate_prospect_id(prospect_id: str) -> None:
    # Same shape as ClientContext._validate_client_id - blocks path traversal.
    if not PROSPECT_ID_PATTERN.match(prospect_id):
        raise ValueError(
            f"Invalid prospect_id {prospect_id!r}: must be lowercase alphanumeric "
            f"with underscores/hyphens, max 64 chars, starting with [a-z0-9]."
        )


def _atomic_write_text(path: Path, content: str) -> None:
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


# --------------------------------------------------------------------------- #
# audit.json schema
# --------------------------------------------------------------------------- #


@dataclass
class ProspectAudit:
    """The shape the Outreach agent's audit tool writes; the Promote button reads."""
    prospect_id: str
    prospect_name: str
    niche: Optional[str] = None
    country: Optional[str] = None
    locale: Optional[str] = None       # e.g. 'en-GB' for a UK prospect
    audited_at: str = ""
    competitor_ads: list[dict] = field(default_factory=list)   # raw FB ads
    weaknesses: list[str] = field(default_factory=list)        # short bullets
    winning_hooks: list[dict] = field(default_factory=list)    # WinningHook-shaped
    referral_motions: list[dict] = field(default_factory=list) # ReferralMotion-shaped

    def to_dict(self) -> dict:
        return {
            "prospect_id": self.prospect_id,
            "prospect_name": self.prospect_name,
            "niche": self.niche,
            "country": self.country,
            "locale": self.locale,
            "audited_at": self.audited_at or datetime.now(timezone.utc).isoformat(),
            "competitor_ads": self.competitor_ads,
            "weaknesses": self.weaknesses,
            "winning_hooks": self.winning_hooks,
            "referral_motions": self.referral_motions,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ProspectAudit":
        return cls(
            prospect_id=raw["prospect_id"],
            prospect_name=raw["prospect_name"],
            niche=raw.get("niche"),
            country=raw.get("country"),
            locale=raw.get("locale"),
            audited_at=raw.get("audited_at", ""),
            competitor_ads=raw.get("competitor_ads", []),
            weaknesses=raw.get("weaknesses", []),
            winning_hooks=raw.get("winning_hooks", []),
            referral_motions=raw.get("referral_motions", []),
        )


# --------------------------------------------------------------------------- #
# ProspectStore
# --------------------------------------------------------------------------- #


class ProspectStore:
    """Per-prospect file silo. Single chokepoint for prospects/<slug>/ I/O."""

    def __init__(self, prospect_id: str, prospects_root: Optional[Path] = None):
        _validate_prospect_id(prospect_id)
        self.prospect_id = prospect_id
        self.prospects_root = (prospects_root or DEFAULT_PROSPECTS_ROOT).resolve()
        self.root = self.prospects_root / prospect_id
        self.audit_path = self.root / "audit.json"
        self.pitch_path = self.root / "pitch.pdf"

    @classmethod
    def list_prospects(cls, prospects_root: Optional[Path] = None) -> list[str]:
        root = (prospects_root or DEFAULT_PROSPECTS_ROOT).resolve()
        if not root.exists():
            return []
        return sorted(
            p.name for p in root.iterdir()
            if p.is_dir() and not p.name.startswith(("_", "."))
        )

    def exists(self) -> bool:
        return self.root.exists()

    def save_audit(self, audit: ProspectAudit) -> Path:
        """Atomic write of audit.json. Creates the prospect folder if needed."""
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            self.audit_path,
            json.dumps(audit.to_dict(), indent=2, default=str, ensure_ascii=False),
        )
        return self.audit_path

    def read_audit(self) -> Optional[ProspectAudit]:
        if not self.audit_path.exists():
            return None
        raw = json.loads(self.audit_path.read_text(encoding="utf-8"))
        return ProspectAudit.from_dict(raw)

    def save_pitch(self, source_pdf_path: Path) -> Path:
        """Copy the generated pitch PDF into the prospect's folder."""
        self.root.mkdir(parents=True, exist_ok=True)
        if source_pdf_path.resolve() == self.pitch_path.resolve():
            return self.pitch_path
        shutil.copy2(source_pdf_path, self.pitch_path)
        return self.pitch_path


# --------------------------------------------------------------------------- #
# Promotion: prospect -> active client
# --------------------------------------------------------------------------- #


def promote_to_client(
    prospect_id: str,
    new_client_id: str,
    new_client_name: str,
    new_client_locale: str = "en-US",
    prospects_root: Optional[Path] = None,
    clients_root: Optional[Path] = None,
) -> tuple[ClientContext, int, int]:
    """Promote a prospect into an active client silo.

    1. ClientContext.onboard(...) - clones the client template, stamps identity.
    2. Reads prospects/<id>/audit.json and seeds MASTER_CONTEXT.md with the
       identified winning_hooks + referral_motions via ctx.add_*.

    Returns (new_ctx, hooks_seeded, motions_seeded). Raises FileExistsError
    when new_client_id is taken, FileNotFoundError when the prospect has no
    audit.json.
    """
    store = ProspectStore(prospect_id, prospects_root)
    audit = store.read_audit()
    if audit is None:
        raise FileNotFoundError(
            f"Prospect {prospect_id!r} has no audit.json; run an outreach "
            f"audit before promoting."
        )

    new_ctx = ClientContext.onboard(
        new_client_id,
        new_client_name,
        locale=new_client_locale,
        clients_root=clients_root,
    )

    now = datetime.now(timezone.utc)
    hooks_seeded = 0
    motions_seeded = 0

    for raw in audit.winning_hooks:
        try:
            hook = WinningHook(
                pattern=raw["pattern"],
                description=raw["description"],
                source_ad_id=raw.get("source_ad_id"),
                days_active=raw.get("days_active"),
                confidence=Confidence(str(raw.get("confidence", "medium")).lower()),
                added_by=AddedBy.STRATEGIST,
                added_at=now,
            )
            new_ctx.add_winning_hook(hook)
            hooks_seeded += 1
        except (KeyError, ValueError):
            # Skip malformed entries - the count surfaced back to the UI
            # tells the operator some were dropped.
            continue

    for raw in audit.referral_motions:
        try:
            motion = ReferralMotion(
                description=raw["description"],
                reference_path=raw["reference_path"],
                pacing=raw.get("pacing"),
                camera_style=raw.get("camera_style"),
                duration_seconds=raw.get("duration_seconds"),
                added_by=AddedBy.STRATEGIST,
                added_at=now,
            )
            new_ctx.add_referral_motion(motion)
            motions_seeded += 1
        except (KeyError, ValueError):
            continue

    return new_ctx, hooks_seeded, motions_seeded
