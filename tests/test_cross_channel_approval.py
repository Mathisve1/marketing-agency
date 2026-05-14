"""V1.9 cross-channel approval clarity tests.

Pins:
  - Manager success message for an MCP-channel approval explicitly says
    so (the operator knows where the audit row lives).
  - Manager refusal message when no MCP context exists explicitly says
    "approve in Streamlit here" with the client_id and plan_id.
  - Manager ambiguity message lists each candidate's source_channel so
    the operator can pick the right thread_id.
  - source_channel='streamlit' on a future Streamlit-paused row is
    surfaced as "Streamlit HITL panel" in markdown.

Mocks the supervisor graph + hitl_service.approve_and_resume so no
real LangGraph / Anthropic / Kling code runs.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.client_context import ClientContext
from core.context_schema import VideoPlan
from services import manager_service, mcp_pending_store
from services.hitl_service import ApprovalResult

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
                     mcp_db: Path, source_channel: str = "mcp") -> None:
    mcp_pending_store.record_pending(
        thread_id, client_id=client_id, model="claude-sonnet-4-6",
        prompt="Produce video", task_type="produce", plan_id=plan_id,
        config={"configurable": {"thread_id": thread_id, "model": "claude-sonnet-4-6"}},
        source_channel=source_channel,
        db_path=mcp_db,
    )


# --------------------------------------------------------------------------- #
# Refusal: no matching MCP row -> explicit Streamlit guidance
# --------------------------------------------------------------------------- #


def test_no_mcp_match_returns_explicit_streamlit_guidance(
    acme, clients_root, mcp_db,
):
    pid = _seed_pending_plan(acme)
    fake_graph = MagicMock()

    with patch("services.manager_service.approve_and_resume") as fake_approve:
        out = manager_service.approve_plan_safely(
            "acme", pid, confirm=True, graph=fake_graph,
            mcp_db_path=mcp_db, clients_root=clients_root,
        )

    fake_approve.assert_not_called()
    # Channel-clarity language the user explicitly asked for.
    assert "I cannot approve" in out
    assert "via MCP" in out
    assert "Approval channel:" in out and "streamlit" in out
    # Concrete approval steps with client_id + plan_id present.
    assert pid in out
    assert "client `acme`" in out
    assert "Approve - submit to Kling" in out
    # MCP escape hatch is mentioned but not the only option.
    assert "resume_agency_workflow" in out


# --------------------------------------------------------------------------- #
# Success: MCP-channel approval calls out the channel
# --------------------------------------------------------------------------- #


def test_successful_mcp_approval_message_states_mcp_channel(
    acme, clients_root, mcp_db,
):
    pid = _seed_pending_plan(acme)
    _seed_mcp_pending("mcp-acme-only", client_id="acme", plan_id=pid,
                     mcp_db=mcp_db, source_channel="mcp")
    fake_graph = MagicMock()

    with patch(
        "services.manager_service.approve_and_resume",
        return_value=ApprovalResult(ok=True, result={"current_agent": "producer_submit"}),
    ):
        out = manager_service.approve_plan_safely(
            "acme", pid, confirm=True, graph=fake_graph,
            mcp_db_path=mcp_db, clients_root=clients_root,
        )

    assert "Approved" in out
    assert "Approval channel:" in out
    assert "MCP" in out
    assert "mcp-acme-only" in out


# --------------------------------------------------------------------------- #
# Ambiguity: every candidate's channel is listed
# --------------------------------------------------------------------------- #


def test_ambiguous_match_lists_each_candidate_channel(
    acme, clients_root, mcp_db,
):
    pid = _seed_pending_plan(acme)
    _seed_mcp_pending("mcp-acme-aaa", client_id="acme", plan_id=pid,
                     mcp_db=mcp_db, source_channel="mcp")
    _seed_mcp_pending("mcp-acme-bbb", client_id="acme", plan_id=pid,
                     mcp_db=mcp_db, source_channel="streamlit")
    fake_graph = MagicMock()

    with patch("services.manager_service.approve_and_resume") as fake_approve:
        out = manager_service.approve_plan_safely(
            "acme", pid, confirm=True, graph=fake_graph,
            mcp_db_path=mcp_db, clients_root=clients_root,
        )

    fake_approve.assert_not_called()
    assert "Ambiguous" in out
    # Both threads + their channels are surfaced so the operator picks.
    assert "mcp-acme-aaa" in out
    assert "mcp-acme-bbb" in out
    assert "MCP" in out
    assert "Streamlit" in out
    # Manager refuses to choose.
    assert "will not pick" in out.lower() or "manager will not" in out.lower()


# --------------------------------------------------------------------------- #
# Default channel for a normal MCP dispatch is 'mcp'
# --------------------------------------------------------------------------- #


def test_default_record_pending_channel_is_mcp(mcp_db):
    """supervisor_dispatch passes source_channel='mcp' explicitly. This
    test pins the default at the store layer too so a different writer
    (e.g. a future Streamlit dispatch helper) doesn't accidentally land
    in an 'unknown' channel for lack of an explicit value."""
    mcp_pending_store.record_pending(
        "t-default-channel",
        client_id="acme", model="claude-sonnet-4-6",
        prompt="x", task_type="produce", plan_id="VP-001",
        config={"configurable": {"thread_id": "t-default-channel"}},
        # source_channel deliberately omitted - test the default
        db_path=mcp_db,
    )
    payload = mcp_pending_store.get_pending("t-default-channel", db_path=mcp_db)
    assert payload["source_channel"] == "mcp"


def test_supervisor_dispatch_records_with_mcp_channel():
    """End-to-end pin: when dispatch_supervisor_run records a pause, the
    row is tagged source_channel='mcp'. This is the contract that tells
    the Manager to recommend the MCP approval path.

    AsyncMock so the awaited run_supervisor_async resolves to our fake
    state without needing pytest-asyncio plumbing.
    """
    from services import supervisor_dispatch

    fake_graph = MagicMock()
    fake_state = {"plan_id": "VP-DISPATCH", "task_type": "produce", "messages": []}

    with patch(
        "services.supervisor_dispatch.run_supervisor_async",
        new=AsyncMock(return_value=fake_state),
    ), patch(
        "services.supervisor_dispatch.get_pending_node",
        return_value="producer_submit",
    ), patch(
        "services.supervisor_dispatch.ClientContext.load",
        side_effect=Exception("not exercised here"),
    ), patch(
        "services.supervisor_dispatch.mcp_pending_store.record_pending",
    ) as fake_record:
        supervisor_dispatch.dispatch_supervisor_run(
            graph=fake_graph,
            prompt="produce video for acme",
            client_id="acme",
            task_type="produce",
            model="claude-sonnet-4-6",
        )

    fake_record.assert_called_once()
    assert fake_record.call_args.kwargs["source_channel"] == "mcp"
