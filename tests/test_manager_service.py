"""Tests for services/manager_service.py.

Pins the cost-control invariants the user explicitly asked for:
  - manager_request for produce intent dispatches but pauses at
    producer_submit; Kling submit is NEVER called.
  - approve_plan_safely refuses without confirm=True.
  - approve_plan_safely refuses when no MCP pending row matches the
    plan_id (Streamlit-originated pause).
  - approve_plan_safely refuses on ambiguous (multiple) matches.
  - approve_plan_safely with one match + confirm=True calls
    hitl_service.approve_and_resume - and ONLY that.
  - reject_plan_safely drives hitl_service.reject_pending_plan.
  - The classifier maps the user's example prompts to the right intent.
  - manager_service does NOT import KlingClient (structural pin).

Mocks the supervisor graph + hitl_service.approve_and_resume so no
real LangGraph / Anthropic / Kling code runs.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.client_context import ClientContext
from core.context_schema import PlanStatus, VideoPlan
from services import manager_service, mcp_pending_store
from services.supervisor_dispatch import DispatchResult

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


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
def mcp_db(tmp_path: Path) -> Path:
    return tmp_path / "mcp_pending_runs.db"


@pytest.fixture
def acme(clients_root: Path) -> ClientContext:
    return ClientContext.onboard("acme", "Acme Corp", clients_root=clients_root)


def _seed_pending_plan(ctx: ClientContext) -> str:
    return ctx.create_video_plan(VideoPlan(
        hook_id="WH-001", character_asset="references/characters/c.png",
        product_asset="references/products/p.png",
        duration=10, aspect_ratio="9:16", mode="professional", cfg_scale=0.5,
        prompt="prompt", negative_prompt="neg", created_at=NOW,
    ))


def _seed_mcp_pending(thread_id: str, *, client_id: str, plan_id: str,
                     mcp_db: Path) -> None:
    mcp_pending_store.record_pending(
        thread_id, client_id=client_id, model="claude-sonnet-4-6",
        prompt="Produce video", task_type="produce", plan_id=plan_id,
        config={"configurable": {"thread_id": thread_id, "model": "claude-sonnet-4-6"}},
        db_path=mcp_db,
    )


# --------------------------------------------------------------------------- #
# Classifier
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prompt,expected_kind", [
    ("What do I need to approve today?", "overview"),
    ("Give me a daily overview", "overview"),
    ("What is waiting for me?", "overview"),
    ("Show me my inbox", "overview"),

    ("Start outreach research for fitness brands in Belgium", "dispatch"),
    ("Find new prospects in DTC coffee", "dispatch"),

    ("Run strategist for client acme", "dispatch"),
    ("Run market research on competitors", "dispatch"),

    ("Create a video plan for client acme", "dispatch"),
    ("Make a video using hook WH-003", "dispatch"),

    ("Analyze performance for the last 14 days", "dispatch"),
    ("Review ROAS recommendations", "dispatch"),

    ("Approve plan VP-004 for client acme", "approve"),
    ("Reject plan VP-004", "reject"),

    ("Where can I find the pitch PDF for prospect gymshark?", "locate"),

    ("hello", "clarify"),
    ("", "clarify"),
])
def test_classify_basic_intents(prompt: str, expected_kind: str):
    intent = manager_service.classify_manager_request(
        prompt, known_clients=["acme"],
    )
    assert intent.kind == expected_kind, (
        f"prompt={prompt!r} classified as {intent.kind!r}, expected {expected_kind!r}"
    )


def test_classifier_extracts_plan_id():
    intent = manager_service.classify_manager_request(
        "Approve plan VP-007 for client acme", known_clients=["acme"],
    )
    assert intent.kind == "approve"
    assert intent.plan_id == "VP-007"
    assert intent.client_id == "acme"


def test_explicit_task_type_wins_over_keywords():
    """If the operator passes task_type='produce', that's a dispatch
    even if the prompt also contains 'overview'."""
    intent = manager_service.classify_manager_request(
        "give me a daily overview please",
        explicit_task_type="produce",
    )
    assert intent.kind == "dispatch"
    assert intent.task_type == "produce"


def test_dispatch_for_produce_intent_correctly_routes():
    intent = manager_service.classify_manager_request(
        "Create a video plan for client acme using hook WH-001",
        known_clients=["acme"],
    )
    assert intent.kind == "dispatch"
    assert intent.task_type == "produce"
    assert intent.client_id == "acme"


def test_dispatch_for_outreach_intent_routes_to_outreach():
    intent = manager_service.classify_manager_request(
        "Start outreach research for fitness brands in Belgium",
    )
    assert intent.kind == "dispatch"
    assert intent.task_type == "outreach"


# --------------------------------------------------------------------------- #
# manager_request: produce dispatch must pause, NEVER call Kling
# --------------------------------------------------------------------------- #


def test_manager_dispatch_for_produce_intent_pauses_before_kling(
    acme, clients_root,
):
    """The cost-control invariant: when the manager dispatches a produce
    intent, the supervisor pauses at producer_submit and the manager
    surfaces the paused markdown. Kling.submit_omni_video is NEVER called."""
    fake_graph = object()
    paused = DispatchResult(
        ok=True, paused=True,
        thread_id="mcp-acme-test", client_id="acme",
        model="claude-sonnet-4-6", task_type="produce",
        plan_id="VP-001", plan_summary="(plan)",
        result={"plan_id": "VP-001"},
        formatted_text="Workflow paused for financial safety. ...",
    )

    fake_kling = MagicMock()
    with patch("services.manager_service.dispatch_supervisor_run", return_value=paused) as fake_dispatch, \
         patch("agents.producer.kling.client.KlingClient", return_value=fake_kling):
        out = manager_service.route_manager_request(
            "Create a video plan for client acme",
            graph=fake_graph,
            clients_root=clients_root,
        )

    # Manager called the shared dispatcher (good - same code path as legacy).
    fake_dispatch.assert_called_once()
    # Manager did NOT call Kling itself.
    fake_kling.submit_omni_video.assert_not_called()
    # Manager surfaced the paused markdown.
    assert "Workflow paused for financial safety" in out
    assert "Manager dispatched" in out


# --------------------------------------------------------------------------- #
# approve_plan_safely
# --------------------------------------------------------------------------- #


def test_approve_refuses_without_confirm(acme, clients_root, mcp_db):
    pid = _seed_pending_plan(acme)
    fake_graph = object()
    out = manager_service.approve_plan_safely(
        "acme", pid, confirm=False, graph=fake_graph,
        mcp_db_path=mcp_db, clients_root=clients_root,
    )
    assert "Refused" in out
    assert "confirm=True" in out
    # Plan untouched.
    assert acme.get_video_plan(pid).status == PlanStatus.PENDING_APPROVAL


def test_approve_refuses_when_no_mcp_match_returns_streamlit_guidance(
    acme, clients_root, mcp_db,
):
    """The plan exists in pending_approval, but no MCP pending row
    matches it. The manager MUST refuse and return the Streamlit
    guidance, not silently submit."""
    pid = _seed_pending_plan(acme)
    fake_graph = MagicMock()
    fake_kling = MagicMock()

    with patch("services.hitl_service.approve_and_resume") as fake_approve, \
         patch("agents.producer.kling.client.KlingClient", return_value=fake_kling):
        out = manager_service.approve_plan_safely(
            "acme", pid, confirm=True, graph=fake_graph,
            mcp_db_path=mcp_db, clients_root=clients_root,
        )

    fake_approve.assert_not_called()
    fake_kling.submit_omni_video.assert_not_called()
    # V1.9: tighter "I cannot approve" phrasing + explicit channel label.
    # The deeper assertions live in tests/test_cross_channel_approval.py;
    # here we keep just enough to detect a regression of the safety path.
    assert "cannot approve" in out.lower()
    assert "Streamlit" in out
    assert "submit_omni_video" not in out  # we never describe the call
    # Plan untouched.
    assert acme.get_video_plan(pid).status == PlanStatus.PENDING_APPROVAL


def test_approve_refuses_on_ambiguous_multiple_matches(acme, clients_root, mcp_db):
    pid = _seed_pending_plan(acme)
    _seed_mcp_pending("mcp-acme-aaa", client_id="acme", plan_id=pid, mcp_db=mcp_db)
    _seed_mcp_pending("mcp-acme-bbb", client_id="acme", plan_id=pid, mcp_db=mcp_db)
    fake_graph = MagicMock()

    with patch("services.manager_service.approve_and_resume") as fake_approve:
        out = manager_service.approve_plan_safely(
            "acme", pid, confirm=True, graph=fake_graph,
            mcp_db_path=mcp_db, clients_root=clients_root,
        )

    fake_approve.assert_not_called()
    assert "Ambiguous" in out
    assert "mcp-acme-aaa" in out and "mcp-acme-bbb" in out


def test_approve_with_one_match_and_confirm_drives_hitl_service(
    acme, clients_root, mcp_db,
):
    """The happy path: one MCP pending row matches, confirm=True. The
    manager calls hitl_service.approve_and_resume with the matching
    config. It does NOT call any Kling code itself."""
    pid = _seed_pending_plan(acme)
    _seed_mcp_pending("mcp-acme-only", client_id="acme", plan_id=pid, mcp_db=mcp_db)
    fake_graph = MagicMock()

    from services.hitl_service import ApprovalResult
    with patch("services.manager_service.approve_and_resume",
               return_value=ApprovalResult(ok=True, result={"current_agent": "producer_submit"})) as fake_approve:
        out = manager_service.approve_plan_safely(
            "acme", pid, confirm=True, graph=fake_graph,
            mcp_db_path=mcp_db, clients_root=clients_root,
        )

    fake_approve.assert_called_once()
    args, kwargs = fake_approve.call_args
    assert args[0] is fake_graph
    assert "configurable" in args[1]
    assert "Approved" in out
    assert "VP-" in out


def test_approve_refuses_when_plan_not_pending_approval(acme, clients_root, mcp_db):
    pid = _seed_pending_plan(acme)
    acme.reject_video_plan(pid)
    fake_graph = MagicMock()
    out = manager_service.approve_plan_safely(
        "acme", pid, confirm=True, graph=fake_graph,
        mcp_db_path=mcp_db, clients_root=clients_root,
    )
    assert "Refused" in out
    assert "rejected" in out


# --------------------------------------------------------------------------- #
# reject_plan_safely
# --------------------------------------------------------------------------- #


def test_reject_marks_plan_rejected_via_service(acme, clients_root, mcp_db):
    pid = _seed_pending_plan(acme)
    out = manager_service.reject_plan_safely(
        "acme", pid, graph=None,
        mcp_db_path=mcp_db, clients_root=clients_root,
    )
    assert "Rejected" in out
    assert acme.get_video_plan(pid).status == PlanStatus.REJECTED


# --------------------------------------------------------------------------- #
# locate_artifact
# --------------------------------------------------------------------------- #


def test_locate_prospect_pitch_returns_path(tmp_path):
    prospects_root = tmp_path / "prospects"
    pitch = prospects_root / "gymshark" / "pitch.pdf"
    pitch.parent.mkdir(parents=True)
    pitch.write_bytes(b"%PDF-1.3 fake")
    loc = manager_service.locate_artifact(
        "prospect_pitch", "gymshark", prospects_root=prospects_root,
    )
    assert loc.exists is True
    assert "gymshark" in loc.path


def test_locate_unknown_source_type_returns_helpful_hint(tmp_path):
    loc = manager_service.locate_artifact("not_a_thing", "foo")
    assert loc.exists is False
    assert "Don't know how to locate" in loc.hint


# --------------------------------------------------------------------------- #
# route_manager_request: end-to-end intent surfaces
# --------------------------------------------------------------------------- #


def test_route_manager_overview_returns_inbox_markdown(
    acme, clients_root, mcp_db, tmp_path,
):
    _seed_pending_plan(acme)
    out = manager_service.route_manager_request(
        "What do I need to approve today?",
        graph=None,
        clients_root=clients_root,
        prospects_root=tmp_path / "prospects",
        mcp_db_path=mcp_db,
        eval_path=tmp_path / "evals" / "x.jsonl",
    )
    assert "Agency overview" in out
    assert "CRITICAL" in out


def test_route_manager_client_overview_filters(
    clients_root, mcp_db, tmp_path,
):
    a = ClientContext.onboard("acme", "Acme", clients_root=clients_root)
    b = ClientContext.onboard("zelda", "Zelda", clients_root=clients_root)
    _seed_pending_plan(a)
    _seed_pending_plan(b)
    out = manager_service.route_manager_request(
        "Give me overview for client acme",
        graph=None,
        client_id="acme",
        clients_root=clients_root,
        prospects_root=tmp_path / "prospects",
        mcp_db_path=mcp_db,
        eval_path=tmp_path / "evals" / "x.jsonl",
    )
    assert "acme overview" in out
    # zelda's plan must NOT appear in the acme overview.
    assert "zelda" not in out


def test_route_manager_clarify_for_unknown_request(clients_root):
    out = manager_service.route_manager_request(
        "ramble ramble random text",
        graph=None,
        clients_root=clients_root,
    )
    assert "Clarification needed" in out


def test_route_manager_approve_without_plan_id_clarifies(clients_root):
    out = manager_service.route_manager_request(
        "approve plan",  # no VP-### token
        graph=None,
        clients_root=clients_root,
    )
    assert "Clarification needed" in out


# --------------------------------------------------------------------------- #
# Structural pin: manager_service must NOT import the Kling client
# --------------------------------------------------------------------------- #


def test_manager_service_does_not_import_kling_client():
    """Hard guarantee that the manager has no path to Kling. This
    prevents a future contributor from 'helpfully' adding a fast-path
    that bypasses the producer_submit gate."""
    src = (REPO_ROOT / "services" / "manager_service.py").read_text(encoding="utf-8")
    assert "from agents.producer.kling" not in src, (
        "manager_service.py must NOT import KlingClient. The producer_submit "
        "node is the ONLY place that calls Kling."
    )
    assert "import agents.producer.kling" not in src
    # Also check it doesn't construct a graph (would let it bypass HITL).
    assert "build_supervisor_graph" not in src, (
        "manager_service.py must not build its own graph; it must use the "
        "graph the caller passes (so interrupt_before is honoured)."
    )
