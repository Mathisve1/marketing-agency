"""Smoke tests for ClientContext — verifies the YAML/Markdown round-trip
and the structured mutators (Phase 1 quality gate)."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.client_context import ClientContext, _next_id
from core.context_schema import (
    AddedBy,
    Confidence,
    NegativeConstraint,
    Severity,
    WinningHook,
)


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    """Copy the real _template into a tmp dir so tests don't touch the repo."""
    src = Path(__file__).parent.parent / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


def test_onboard_creates_silo(clients_root: Path):
    ctx = ClientContext.onboard("acme", "Acme Corp", "en-US", clients_root=clients_root)
    assert ctx.root.exists()
    fm, body = ctx.read()
    assert fm.client.id == "acme"
    assert fm.client.name == "Acme Corp"
    assert "Acme Corp" in body  # [Client Name] placeholder was substituted


def test_invalid_client_id_rejected(clients_root: Path):
    with pytest.raises(ValueError):
        ClientContext.onboard("../escape", "x", clients_root=clients_root)
    with pytest.raises(ValueError):
        ClientContext.onboard("UPPER", "x", clients_root=clients_root)


def test_add_winning_hook_assigns_id(clients_root: Path):
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
    fm, _ = ctx.read()
    assert fm.winning_hooks[0].pattern == "Price comparison"
    # second insert increments
    hid2 = ctx.add_winning_hook(hook)
    assert hid2 == "WH-002"


def test_negative_constraint_round_trip(clients_root: Path):
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
    fm, _ = ctx.read()
    assert fm.negative_constraints[0].severity == Severity.HARD


def test_next_id_handles_gaps():
    assert _next_id("WH", []) == "WH-001"
    assert _next_id("WH", ["WH-001", "WH-003"]) == "WH-004"
    assert _next_id("RM", ["RM-007"]) == "RM-008"
