"""Tests for services/hitl_service.py.

Pins the coordination invariants of the approve/reject path:
  - Approve drives resume_supervisor_async with the supplied config.
  - Reject calls reject_video_plan and returns a structured outcome.
  - load_plan_for_review handles the None / missing edge cases.
  - Race losses (plan already submitted/rejected) surface cleanly
    instead of silently mutating SQL.

Mocks the LangGraph resume so no agent code runs and no external API
is touched.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.client_context import ClientContext
from core.context_schema import PlanStatus, VideoPlan
from services.hitl_service import (
    approve_and_resume,
    load_plan_for_review,
    reject_pending_plan,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    src = REPO_ROOT / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


@pytest.fixture
def ctx(clients_root: Path) -> ClientContext:
    return ClientContext.onboard("acme", "Acme", clients_root=clients_root)


def _seed_plan(ctx: ClientContext, **overrides) -> str:
    defaults = dict(
        hook_id="WH-001",
        character_asset="references/characters/c.png",
        product_asset="references/products/p.png",
        duration=10,
        aspect_ratio="9:16",
        mode="professional",
        cfg_scale=0.5,
        prompt="prompt",
        negative_prompt="neg",
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ctx.create_video_plan(VideoPlan(**defaults))


# --------------------------------------------------------------------------- #
# approve_and_resume
# --------------------------------------------------------------------------- #


def test_approve_calls_resume_with_supplied_config():
    """approve_and_resume must drive resume_supervisor_async with the
    config the caller passed in - not a different/cached one."""
    fake_config = {"configurable": {"thread_id": "t-1", "model": "claude-sonnet-4-6"}}
    fake_graph = object()  # opaque - patch will intercept the call
    fake_result = {"current_agent": "producer_submit", "artifacts": {}}

    async def fake_resume(graph, *, config):
        assert graph is fake_graph
        assert config is fake_config
        return fake_result

    with patch("services.hitl_service.resume_supervisor_async", side_effect=fake_resume):
        out = approve_and_resume(fake_graph, fake_config)

    assert out.ok is True
    assert out.error is None
    assert out.result == fake_result


def test_approve_surfaces_resume_exception():
    """When LangGraph raises during resume, the service must NOT swallow
    it - return ok=False with the error string preserved."""
    async def boom(graph, *, config):
        raise RuntimeError("checkpoint missing")

    with patch("services.hitl_service.resume_supervisor_async", side_effect=boom):
        out = approve_and_resume(object(), {"configurable": {"thread_id": "x"}})

    assert out.ok is False
    assert out.result is None
    assert "RuntimeError" in out.error
    assert "checkpoint missing" in out.error


# --------------------------------------------------------------------------- #
# reject_pending_plan
# --------------------------------------------------------------------------- #


def test_reject_marks_pending_plan_rejected(ctx: ClientContext):
    pid = _seed_plan(ctx)
    out = reject_pending_plan(ctx, pid)
    assert out.plan_marked_rejected is True
    assert out.plan_id == pid
    assert pid in out.note
    assert ctx.get_video_plan(pid).status == PlanStatus.REJECTED


def test_reject_marks_submitting_plan_rejected(ctx: ClientContext):
    """V1.4.1: operator may reject a stuck submitting plan from the UI
    stale-plan panel. The service must accept this transition."""
    pid = _seed_plan(ctx)
    ctx.claim_plan_for_submission(pid)  # -> submitting

    out = reject_pending_plan(ctx, pid)
    assert out.plan_marked_rejected is True
    assert ctx.get_video_plan(pid).status == PlanStatus.REJECTED


def test_reject_no_plan_id_is_noop(ctx: ClientContext):
    out = reject_pending_plan(ctx, None)
    assert out.plan_marked_rejected is False
    assert out.plan_id is None
    assert "No plan_id" in out.note


def test_reject_already_decided_plan_returns_clean_note(ctx: ClientContext):
    """If the plan was already submitted (or rejected) by another path,
    reject must NOT silently flip status. It must return a structured
    note describing the actual state."""
    pid = _seed_plan(ctx)
    ctx.claim_plan_for_submission(pid)
    ctx.mark_plan_submitted(pid)  # -> submitted

    out = reject_pending_plan(ctx, pid)
    assert out.plan_marked_rejected is False
    assert "submitted" in out.note
    # SQL state is unchanged.
    assert ctx.get_video_plan(pid).status == PlanStatus.SUBMITTED


def test_reject_unknown_plan_id_does_not_crash(ctx: ClientContext):
    out = reject_pending_plan(ctx, "VP-DOES-NOT-EXIST")
    assert out.plan_marked_rejected is False
    assert "VP-DOES-NOT-EXIST" in out.note
    assert "missing" in out.note


# --------------------------------------------------------------------------- #
# load_plan_for_review
# --------------------------------------------------------------------------- #


def test_load_plan_for_review_round_trip(ctx: ClientContext):
    pid = _seed_plan(ctx, hook_id="WH-077")
    plan = load_plan_for_review(ctx, pid)
    assert plan is not None
    assert plan.id == pid
    assert plan.hook_id == "WH-077"


def test_load_plan_for_review_handles_none_plan_id(ctx: ClientContext):
    assert load_plan_for_review(ctx, None) is None


def test_load_plan_for_review_handles_missing_row(ctx: ClientContext):
    assert load_plan_for_review(ctx, "VP-DOES-NOT-EXIST") is None
