"""Smoke tests for ClientContext (V1.2+ SQL architecture).

Originally written against the YAML-frontmatter list storage. Rewritten
after the V1.2 migration moved winning_hooks / referral_motions /
negative_constraints into clients/<id>/client_data.db. The MASTER_CONTEXT.md
frontmatter now holds STATIC client/brand/benchmark metadata only; the
dynamic lists are read via ctx.get_*() and written via ctx.add_*().

V1.4 adds video_plans + video_jobs tables; those have dedicated test
modules (test_video_plans.py / test_video_jobs.py) and are not duplicated
here. This file covers the original ClientContext invariants only.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.client_context import ClientContext
from core.context_schema import (
    AddedBy,
    Confidence,
    NegativeConstraint,
    Severity,
    WinningHook,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    """Copy the real _template into a tmp dir so tests don't touch the repo."""
    src = REPO_ROOT / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


# --------------------------------------------------------------------------- #
# onboard / silo creation
# --------------------------------------------------------------------------- #


def test_onboard_creates_silo(clients_root: Path):
    ctx = ClientContext.onboard("acme", "Acme Corp", "en-US", clients_root=clients_root)
    assert ctx.root.exists()
    fm, body = ctx.read()
    assert fm.client.id == "acme"
    assert fm.client.name == "Acme Corp"
    assert "Acme Corp" in body  # [Client Name] placeholder substituted


def test_invalid_client_id_rejected(clients_root: Path):
    with pytest.raises(ValueError):
        ClientContext.onboard("../escape", "x", clients_root=clients_root)
    with pytest.raises(ValueError):
        ClientContext.onboard("UPPER", "x", clients_root=clients_root)


def test_template_id_is_reserved(clients_root: Path):
    with pytest.raises(ValueError):
        ClientContext.onboard("_template", "x", clients_root=clients_root)


# --------------------------------------------------------------------------- #
# winning_hooks (V1.2+ SQL backend)
# --------------------------------------------------------------------------- #


def test_add_winning_hook_assigns_id_and_persists(clients_root: Path):
    ctx = ClientContext.onboard("acme", "Acme Corp", clients_root=clients_root)
    hook = WinningHook(
        pattern="Price comparison",
        description="Side-by-side cart total",
        days_active=42,
        confidence=Confidence.HIGH,
        added_by=AddedBy.STRATEGIST,
        added_at=datetime.now(timezone.utc),
    )

    hid = ctx.add_winning_hook(hook)
    assert hid == "WH-001"

    # V1.2: list now lives in SQL, NOT on the MASTER_CONTEXT frontmatter model.
    rows = ctx.get_winning_hooks()
    assert len(rows) == 1
    assert rows[0].id == "WH-001"
    assert rows[0].pattern == "Price comparison"
    assert rows[0].confidence == Confidence.HIGH

    # Second insert auto-increments the ID.
    hid2 = ctx.add_winning_hook(hook)
    assert hid2 == "WH-002"


def test_get_winning_hook_by_id_round_trip(clients_root: Path):
    ctx = ClientContext.onboard("acme", "Acme Corp", clients_root=clients_root)
    ctx.add_winning_hook(WinningHook(
        pattern="Ingredient ASMR",
        description="Slow pour into glass jar",
        confidence=Confidence.MEDIUM,
        added_by=AddedBy.STRATEGIST,
        added_at=datetime.now(timezone.utc),
    ))
    fetched = ctx.get_winning_hook("WH-001")
    assert fetched is not None
    assert fetched.pattern == "Ingredient ASMR"

    assert ctx.get_winning_hook("WH-999") is None


# --------------------------------------------------------------------------- #
# negative_constraints (V1.2+ SQL backend)
# --------------------------------------------------------------------------- #


def test_negative_constraint_round_trip_via_sql(clients_root: Path):
    ctx = ClientContext.onboard("acme", "Acme Corp", clients_root=clients_root)
    nc = NegativeConstraint(
        rule="Avoid direct-review hooks for parents 35-45",
        reason="ROAS 1.1 over Q1",
        severity=Severity.HARD,
        added_by=AddedBy.ANALYST,
        added_at=datetime.now(timezone.utc),
    )
    ncid = ctx.add_negative_constraint(nc)
    assert ncid == "NC-001"

    # V1.2: read from SQL via the typed fetcher, NOT fm.negative_constraints.
    rows = ctx.get_negative_constraints()
    assert len(rows) == 1
    assert rows[0].id == "NC-001"
    assert rows[0].severity == Severity.HARD
    assert rows[0].source_log_entries == []


def test_get_negative_constraints_filtered_by_severity(clients_root: Path):
    ctx = ClientContext.onboard("acme", "Acme Corp", clients_root=clients_root)
    now = datetime.now(timezone.utc)
    ctx.add_negative_constraint(NegativeConstraint(
        rule="hard rule", reason="r", severity=Severity.HARD,
        added_by=AddedBy.ANALYST, added_at=now,
    ))
    ctx.add_negative_constraint(NegativeConstraint(
        rule="soft rule", reason="r", severity=Severity.SOFT,
        added_by=AddedBy.ANALYST, added_at=now,
    ))
    hard = ctx.get_negative_constraints(severity=Severity.HARD)
    soft = ctx.get_negative_constraints(severity=Severity.SOFT)
    assert {c.rule for c in hard} == {"hard rule"}
    assert {c.rule for c in soft} == {"soft rule"}


# --------------------------------------------------------------------------- #
# Auto-assigned IDs across multiple inserts (replaces the deleted
# _next_id_handles_gaps unit test which targeted a removed helper).
# Exercises the public API equivalent: sequential add_* calls produce
# monotonically increasing 3-digit suffixes.
# --------------------------------------------------------------------------- #


def test_auto_assigned_ids_increment_monotonically(clients_root: Path):
    ctx = ClientContext.onboard("acme", "Acme Corp", clients_root=clients_root)
    now = datetime.now(timezone.utc)

    ids = [
        ctx.add_winning_hook(WinningHook(
            pattern=f"hook {i}", description="x",
            added_by=AddedBy.STRATEGIST, added_at=now,
        ))
        for i in range(3)
    ]
    assert ids == ["WH-001", "WH-002", "WH-003"]

    nc_ids = [
        ctx.add_negative_constraint(NegativeConstraint(
            rule=f"r {i}", reason="x", severity=Severity.SOFT,
            added_by=AddedBy.ANALYST, added_at=now,
        ))
        for i in range(2)
    ]
    assert nc_ids == ["NC-001", "NC-002"]


def test_load_after_onboard_returns_same_data(clients_root: Path):
    """Round-trip: onboard, write, then ClientContext.load() picks it up."""
    ctx = ClientContext.onboard("acme", "Acme Corp", clients_root=clients_root)
    ctx.add_winning_hook(WinningHook(
        pattern="p", description="d",
        added_by=AddedBy.STRATEGIST, added_at=datetime.now(timezone.utc),
    ))

    reloaded = ClientContext.load("acme", clients_root=clients_root)
    rows = reloaded.get_winning_hooks()
    assert len(rows) == 1
    assert rows[0].pattern == "p"
