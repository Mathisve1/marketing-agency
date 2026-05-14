"""Tests for the V1.8 Manager MCP tools.

Pins:
  - get_agency_overview / get_client_overview return markdown (not raw JSON)
  - get_client_overview filters out other clients
  - manager_request for produce intent goes through the shared dispatch
    helper and DOES NOT bypass the producer_submit HITL gate
  - manager_request never imports Kling itself (the manager_service
    structural pin in test_manager_service.py covers the underlying
    module; here we cover the MCP tool surface)
  - Existing tools (run_agency_agent, resume_agency_workflow) are still
    registered alongside the new ones (backward compat)

The MCP tool decorator wraps the underlying function. We unwrap it here
via the module-level `.fn` attribute that FastMCP exposes - this gives
us the raw callable without going through MCP transport.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.client_context import ClientContext
from core.context_schema import VideoPlan

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    src = REPO_ROOT / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


def _seed_pending_plan(ctx: ClientContext) -> str:
    return ctx.create_video_plan(VideoPlan(
        hook_id="WH-001", character_asset="references/characters/c.png",
        product_asset="references/products/p.png",
        duration=10, aspect_ratio="9:16", mode="professional", cfg_scale=0.5,
        prompt="prompt", negative_prompt="neg", created_at=NOW,
    ))


def _unwrap(tool):
    """FastMCP tools store the underlying callable on `.fn`. Unwrap so we
    can call them in tests without going through the MCP transport."""
    return getattr(tool, "fn", tool)


# --------------------------------------------------------------------------- #
# Tools are registered (backward compat + new ones)
# --------------------------------------------------------------------------- #


def test_existing_tools_are_still_registered():
    """Backward compat: V1.8 must NOT remove run_agency_agent /
    resume_agency_workflow. They remain for programmatic dispatch."""
    import mcp_server
    assert hasattr(mcp_server, "run_agency_agent")
    assert hasattr(mcp_server, "resume_agency_workflow")


def test_new_manager_tools_are_registered():
    import mcp_server
    assert hasattr(mcp_server, "get_agency_overview")
    assert hasattr(mcp_server, "get_client_overview")
    assert hasattr(mcp_server, "manager_request")


# --------------------------------------------------------------------------- #
# get_agency_overview returns markdown
# --------------------------------------------------------------------------- #


def test_get_agency_overview_returns_markdown(monkeypatch):
    """The tool delegates to manager_service.get_agency_overview, which
    returns markdown. Pin that the tool surface returns the same string,
    not a JSON-stringified dict."""
    fake_md = "## Agency overview\n\n**3 open task(s).**"
    with patch("mcp_server.manager_service.get_agency_overview",
               return_value=fake_md):
        import mcp_server
        out = _unwrap(mcp_server.get_agency_overview)()
    assert out == fake_md
    assert out.startswith("## ")  # markdown header, not JSON


def test_get_client_overview_returns_markdown_scoped_to_client():
    fake_md = "## acme overview\n\nNo open tasks for this client."
    with patch("mcp_server.manager_service.get_client_overview",
               return_value=fake_md) as fake:
        import mcp_server
        out = _unwrap(mcp_server.get_client_overview)("acme")
    fake.assert_called_once_with("acme")
    assert "acme overview" in out


# --------------------------------------------------------------------------- #
# manager_request: cost-control invariants
# --------------------------------------------------------------------------- #


def test_manager_request_for_produce_intent_does_not_call_kling(
    clients_root,
):
    """End-to-end through the MCP tool surface: a produce-intent
    manager_request must NOT trigger Kling. The underlying dispatch
    pauses at producer_submit; manager surfaces the paused markdown."""
    ctx = ClientContext.onboard("acme", "Acme", clients_root=clients_root)
    _seed_pending_plan(ctx)

    from services.supervisor_dispatch import DispatchResult
    paused = DispatchResult(
        ok=True, paused=True,
        thread_id="mcp-acme-aaa", client_id="acme",
        model="claude-sonnet-4-6", task_type="produce",
        plan_id="VP-001", plan_summary="(brief)",
        result={"plan_id": "VP-001"},
        formatted_text="Workflow paused for financial safety. (...)",
    )

    fake_kling = MagicMock()
    with patch("services.manager_service.dispatch_supervisor_run",
               return_value=paused) as fake_dispatch, \
         patch("agents.producer.kling.client.KlingClient",
               return_value=fake_kling):
        import mcp_server
        out = _unwrap(mcp_server.manager_request)(
            "Create a video plan for client acme",
        )

    fake_dispatch.assert_called_once()
    # Sanity: the manager passed the SAME _GRAPH the legacy tool uses,
    # so interrupt_before is preserved.
    kwargs = fake_dispatch.call_args.kwargs
    assert kwargs["graph"] is mcp_server._GRAPH
    fake_kling.submit_omni_video.assert_not_called()
    assert "Workflow paused for financial safety" in out
    assert "Manager dispatched" in out


def test_manager_request_overview_uses_inbox_not_supervisor():
    """Read-only intents must not touch the supervisor at all."""
    with patch("services.manager_service.dispatch_supervisor_run") as fake_dispatch, \
         patch("services.manager_service.get_agency_overview",
               return_value="## Agency overview\n\nClean.") as fake_overview:
        import mcp_server
        out = _unwrap(mcp_server.manager_request)("What is waiting for me?")

    fake_dispatch.assert_not_called()
    fake_overview.assert_called_once()
    assert "Agency overview" in out


def test_manager_request_clarify_for_unknown_request():
    with patch("services.manager_service.dispatch_supervisor_run") as fake_dispatch:
        import mcp_server
        out = _unwrap(mcp_server.manager_request)("ramble random text")
    fake_dispatch.assert_not_called()
    assert "Clarification needed" in out


def test_manager_request_approve_without_confirm_refuses(clients_root):
    """The operator typed 'approve plan VP-001 for acme' but didn't pass
    confirm=True. Manager must refuse and not dispatch anything."""
    ctx = ClientContext.onboard("acme", "Acme", clients_root=clients_root)
    pid = _seed_pending_plan(ctx)
    fake_kling = MagicMock()

    with patch("services.manager_service.dispatch_supervisor_run") as fake_dispatch, \
         patch("services.manager_service.approve_and_resume") as fake_approve, \
         patch("agents.producer.kling.client.KlingClient",
               return_value=fake_kling), \
         patch("core.client_context.list_clients", return_value=["acme"]), \
         patch("core.client_context.DEFAULT_CLIENTS_ROOT", clients_root):
        import mcp_server
        out = _unwrap(mcp_server.manager_request)(
            f"approve plan {pid} for client acme",
            client_id="acme",
        )

    fake_dispatch.assert_not_called()
    fake_approve.assert_not_called()
    fake_kling.submit_omni_video.assert_not_called()
    assert "Refused" in out
    assert "confirm=True" in out
