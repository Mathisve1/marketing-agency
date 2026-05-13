"""V1.4 Producer plan/submit split tests.

Pins the cost-control invariants:
  - compile_video_plan never calls Kling.
  - compile_video_plan persists the EXACT compiled brief to SQL so the
    operator can review prompt + assets before approval.
  - compile_video_plan refuses a second call per planner turn.
  - producer_submit_node only calls Kling AFTER a plan exists in
    pending_approval, with the EXACT prompt/assets/duration from SQL.
  - producer_submit_node transitions the plan pending_approval -> submitted
    and writes a corresponding video_jobs row.
  - format_plan_summary returns operator-readable text containing every
    decision-relevant field so UI/MCP can show it before approval.

Tests stub KlingClient so no network calls happen and no real credits move.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.producer.agent import (
    _build_planner_tools,
    format_plan_summary,
    producer_submit_node,
)
from core.client_context import ClientContext
from core.context_schema import (
    AddedBy,
    Confidence,
    JobStatus,
    PlanStatus,
    WinningHook,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    src = REPO_ROOT / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


@pytest.fixture
def ctx_with_assets(clients_root: Path) -> ClientContext:
    """Onboard 'acme' and seed: one hook, one character asset, one product asset."""
    ctx = ClientContext.onboard("acme", "Acme", clients_root=clients_root)
    ctx.add_winning_hook(WinningHook(
        pattern="Price comparison",
        description="Side-by-side cart total",
        days_active=42,
        confidence=Confidence.HIGH,
        added_by=AddedBy.STRATEGIST,
        added_at=datetime.now(timezone.utc),
    ))
    (ctx.root / "references" / "characters" / "c.png").write_bytes(b"\x89PNG fake")
    (ctx.root / "references" / "products" / "p.png").write_bytes(b"\x89PNG fake")
    return ctx


# --------------------------------------------------------------------------- #
# compile_video_plan tool
# --------------------------------------------------------------------------- #


def test_compile_video_plan_persists_brief(ctx_with_assets: ClientContext):
    """Calling the planner tool must produce a video_plans row whose
    prompt + negative_prompt + assets exactly match what would be sent
    to Kling on approval."""
    fake_kling = MagicMock()
    tools = _build_planner_tools(ctx_with_assets, fake_kling)
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")

    result = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
        "duration": 10,
    })
    assert result.startswith("PLAN_COMPILED plan_id=")

    plan_id = result.split("plan_id=")[1].split()[0]
    plan = ctx_with_assets.get_video_plan(plan_id)
    assert plan is not None
    assert plan.status == PlanStatus.PENDING_APPROVAL
    assert plan.hook_id == "WH-001"
    assert plan.character_asset == "references/characters/c.png"
    assert plan.product_asset == "references/products/p.png"
    assert plan.duration == 10
    # The brief compiler injects the assets header + 9-layer formula.
    assert "<<<image_1>>>" in plan.prompt
    assert "<<<image_2>>>" in plan.prompt


def test_compile_video_plan_does_not_call_kling(ctx_with_assets: ClientContext):
    """The whole point of the split: the planner cannot spend money."""
    fake_kling = MagicMock()
    tools = _build_planner_tools(ctx_with_assets, fake_kling)
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")

    compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    })
    fake_kling.submit_omni_video.assert_not_called()
    fake_kling.poll_task.assert_not_called()
    fake_kling.download_video.assert_not_called()


def test_compile_video_plan_safety_limit(ctx_with_assets: ClientContext):
    """MAX_PLANS_PER_TURN=1 must be enforced in code, not just the prompt."""
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    args = {
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    }
    first = compile_tool.invoke(args)
    assert first.startswith("PLAN_COMPILED")
    second = compile_tool.invoke(args)
    assert second.startswith("SAFETY LIMIT:")


def test_compile_video_plan_validates_assets(ctx_with_assets: ClientContext):
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    result = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/missing.png",
        "product_asset": "references/products/p.png",
    })
    assert result.startswith("ERROR:")
    assert "character asset not found" in result


# --------------------------------------------------------------------------- #
# producer_submit_node
# --------------------------------------------------------------------------- #


def _state_for_submit(client_id: str, plan_id: str) -> dict:
    return {
        "messages": [],
        "client_id": client_id,
        "task_type": "produce",
        "current_agent": "producer_plan",
        "artifacts": {},
        "error": None,
        "plan_id": plan_id,
    }


def test_submit_calls_kling_with_plan_fields(ctx_with_assets: ClientContext, clients_root: Path):
    """producer_submit must call Kling.submit_omni_video using the EXACT
    fields persisted on the plan row - not whatever the LLM most recently
    said in chat."""
    # First, plan-compile via the tool so a real row exists.
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    result = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    })
    plan_id = result.split("plan_id=")[1].split()[0]
    plan = ctx_with_assets.get_video_plan(plan_id)

    fake_kling = MagicMock()
    fake_kling.submit_omni_video.return_value = "kling-task-XYZ"

    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        out = producer_submit_node(_state_for_submit("acme", plan_id), config={})

    fake_kling.submit_omni_video.assert_called_once()
    kwargs = fake_kling.submit_omni_video.call_args.kwargs
    assert kwargs["prompt"] == plan.prompt
    assert kwargs["negative_prompt"] == plan.negative_prompt
    assert kwargs["duration"] == plan.duration
    assert kwargs["aspect_ratio"] == plan.aspect_ratio
    assert kwargs["mode"] == plan.mode
    # Plan moved through SUBMITTING -> SUBMITTED, job row exists with task_id.
    final = ctx_with_assets.get_video_plan(plan_id)
    assert final.status == PlanStatus.SUBMITTED
    assert final.submit_attempts == 1
    assert final.submit_error is None  # cleared on success
    job = ctx_with_assets.get_video_job("kling-task-XYZ")
    assert job is not None
    assert job.plan_id == plan_id
    assert job.status == JobStatus.PENDING
    assert "kling-task-XYZ" in out["artifacts"]["producer_submitted_task_id"]


def test_submit_sets_submitting_status_BEFORE_calling_kling(ctx_with_assets: ClientContext):
    """V1.4.1 cost-control invariant: the plan must be in SUBMITTING by
    the time Kling.submit_omni_video is invoked. Anything earlier means
    a parallel submitter could also be holding the (claim absent) plan
    in pending_approval and double-spend.

    Implementation: the fake submit_omni_video reads the plan back inside
    the call and asserts its status. The test fails if the assert raises
    OR if status is anything other than 'submitting' at the moment Kling
    is called.
    """
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    plan_id = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    }).split("plan_id=")[1].split()[0]

    observed_status: dict = {}

    def fake_submit(*args, **kwargs):
        # At the moment of the (mocked) Kling call, check what's in SQL.
        plan_at_call = ctx_with_assets.get_video_plan(plan_id)
        observed_status["status"] = plan_at_call.status
        return "kling-task-OBSERVED"

    fake_kling = MagicMock()
    fake_kling.submit_omni_video.side_effect = fake_submit

    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        producer_submit_node(_state_for_submit("acme", plan_id), config={})

    assert observed_status["status"] == PlanStatus.SUBMITTING, (
        f"plan was {observed_status['status']!r} at the moment of Kling "
        f"submit; the atomic claim invariant is violated"
    )


def test_atomic_claim_prevents_double_submission(ctx_with_assets: ClientContext):
    """Two concurrent producer_submit_node invocations on the SAME plan
    must result in exactly ONE Kling submission. The losing call must
    return the REFUSED message and never invoke Kling."""
    import threading

    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    plan_id = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    }).split("plan_id=")[1].split()[0]

    # Counter incremented atomically by the fake submit; lets us prove
    # exactly-once even under threading.
    submit_count = {"n": 0}
    submit_lock = threading.Lock()

    def fake_submit(*args, **kwargs):
        with submit_lock:
            submit_count["n"] += 1
        return f"kling-task-{submit_count['n']}"

    fake_kling = MagicMock()
    fake_kling.submit_omni_video.side_effect = fake_submit

    outputs: list[dict] = []
    outputs_lock = threading.Lock()

    def run_submit():
        out = producer_submit_node(_state_for_submit("acme", plan_id), config={})
        with outputs_lock:
            outputs.append(out)

    # IMPORTANT: patch ONCE in the main thread. mock.patch is a context
    # manager that mutates module attributes; two threads entering/exiting
    # the same patch concurrently race on the unpatch step.
    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        t1 = threading.Thread(target=run_submit)
        t2 = threading.Thread(target=run_submit)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert submit_count["n"] == 1, (
        f"Kling was called {submit_count['n']} times for the same plan; "
        f"atomic claim is broken"
    )
    refused = [o for o in outputs if o.get("error", "").startswith("could not claim")]
    submitted = [o for o in outputs if "producer_submitted_task_id" in (o.get("artifacts") or {})]
    assert len(refused) == 1, "the losing thread must surface a REFUSED outcome"
    assert len(submitted) == 1, "exactly one thread must report a submitted task"
    final = ctx_with_assets.get_video_plan(plan_id)
    assert final.status == PlanStatus.SUBMITTED
    assert final.submit_attempts == 1, (
        f"submit_attempts={final.submit_attempts}; the failed-claim path "
        f"must not increment the counter"
    )


def test_submit_refuses_without_plan_id(ctx_with_assets: ClientContext):
    fake_kling = MagicMock()
    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        state = _state_for_submit("acme", plan_id=None)
        out = producer_submit_node(state, config={})
    assert "ERROR" in out["messages"][0].content
    fake_kling.submit_omni_video.assert_not_called()


def test_submit_refuses_already_decided_plan(ctx_with_assets: ClientContext):
    """V1.4.1: a rejected plan cannot be claimed, so submit returns the
    REFUSED message without ever calling Kling and without mutating the
    plan back into any other state."""
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    plan_id = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    }).split("plan_id=")[1].split()[0]
    ctx_with_assets.reject_video_plan(plan_id)

    fake_kling = MagicMock()
    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        out = producer_submit_node(_state_for_submit("acme", plan_id), config={})

    assert "REFUSED" in out["messages"][0].content
    assert "could not claim" in out["messages"][0].content
    fake_kling.submit_omni_video.assert_not_called()
    # Plan stays REJECTED, not flipped back to anything.
    assert ctx_with_assets.get_video_plan(plan_id).status == PlanStatus.REJECTED
    assert ctx_with_assets.get_video_plan(plan_id).submit_attempts == 0


def test_submit_kling_failure_records_error_and_reverts(ctx_with_assets: ClientContext):
    """V1.4.1: on Kling failure the plan goes submitting -> pending_approval
    with submit_error PERSISTED, NOT silently looking like a clean
    untouched pending_approval plan. submit_attempts increments. No
    video_jobs row is written."""
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    plan_id = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    }).split("plan_id=")[1].split()[0]

    fake_kling = MagicMock()
    fake_kling.submit_omni_video.side_effect = RuntimeError("Kling 500 internal")
    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        out = producer_submit_node(_state_for_submit("acme", plan_id), config={})

    assert "ERROR" in out["messages"][0].content
    after = ctx_with_assets.get_video_plan(plan_id)
    assert after.status == PlanStatus.PENDING_APPROVAL
    assert after.submit_attempts == 1, "failed attempt must increment counter"
    assert after.submit_error is not None
    assert "Kling 500 internal" in after.submit_error
    assert "TIMEOUT_WARNING" not in after.submit_error  # not a timeout shape
    assert ctx_with_assets.list_video_jobs() == []


def test_submit_timeout_failure_includes_duplicate_warning(ctx_with_assets: ClientContext):
    """If the Kling failure looks like a timeout / connection drop, the
    submit_error MUST flag that the provider may have accepted the
    request anyway, so the operator doesn't blindly re-approve and
    create a duplicate render."""
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    plan_id = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    }).split("plan_id=")[1].split()[0]

    fake_kling = MagicMock()
    fake_kling.submit_omni_video.side_effect = TimeoutError("read timed out after 60s")
    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        out = producer_submit_node(_state_for_submit("acme", plan_id), config={})

    after = ctx_with_assets.get_video_plan(plan_id)
    assert after.status == PlanStatus.PENDING_APPROVAL
    assert after.submit_error is not None
    assert "TIMEOUT_WARNING" in after.submit_error
    assert "duplicate" in after.submit_error.lower()
    # Operator-facing message must also surface the warning text from the row.
    assert "TIMEOUT_WARNING" in out["messages"][0].content


def test_submit_after_failure_can_retry_via_fresh_claim(ctx_with_assets: ClientContext):
    """A reverted plan must be re-claimable - that's the explicit retry
    path. Two paid calls can happen IF the operator approves twice; the
    cost-control invariant is one-call-per-claim, not one-call-per-plan."""
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    plan_id = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    }).split("plan_id=")[1].split()[0]

    # Attempt 1: fails.
    fake_kling = MagicMock()
    fake_kling.submit_omni_video.side_effect = RuntimeError("first try fails")
    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        producer_submit_node(_state_for_submit("acme", plan_id), config={})

    # Attempt 2: succeeds.
    fake_kling.submit_omni_video.side_effect = None
    fake_kling.submit_omni_video.return_value = "kling-task-RETRY"
    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets):
        producer_submit_node(_state_for_submit("acme", plan_id), config={})

    final = ctx_with_assets.get_video_plan(plan_id)
    assert final.status == PlanStatus.SUBMITTED
    assert final.submit_attempts == 2
    assert final.submit_error is None  # cleared on the successful retry
    assert ctx_with_assets.get_video_job("kling-task-RETRY") is not None


# --------------------------------------------------------------------------- #
# Plan summary - the text the UI/MCP show before approval
# --------------------------------------------------------------------------- #


def test_format_plan_summary_includes_decision_critical_fields(ctx_with_assets: ClientContext):
    """Whatever cosmetic changes happen, the summary MUST include the
    fields an operator needs to decide whether to spend Kling credits."""
    tools = _build_planner_tools(ctx_with_assets, MagicMock())
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    result = compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
        "duration": 12,
    })
    plan_id = result.split("plan_id=")[1].split()[0]
    plan = ctx_with_assets.get_video_plan(plan_id)
    text = format_plan_summary(plan)

    for required in (
        plan_id, "WH-001",
        "references/characters/c.png", "references/products/p.png",
        "12s", "9:16", "professional",
        "Compiled Kling prompt", "Negative prompt",
    ):
        assert required in text, f"plan summary missing {required!r}"
