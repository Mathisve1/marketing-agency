"""MCP server exposing the LangGraph marketing agency to Claude Desktop.

Tools:
  - run_agency_agent          dispatches the supervisor; pauses before Producer.
  - resume_agency_workflow    approves or cancels a paused Producer run.

V1.3 (4/N) hardening - Initiative 1: the MCP "God Mode" backdoor is plugged.
The compiled graph is now built once at module import with
interrupt_before=['producer'], matching the Streamlit UI's HITL gate.
A pending-runs registry caches the per-run config so resume_agency_workflow
can re-supply the original `model` to the Producer node.

Run standalone:  python mcp_server.py
Or wire into Claude Desktop via claude_desktop_config.json (see README).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

# Ensure we run from the repo root so relative paths (clients/, prospects/,
# logs/) resolve correctly regardless of how Claude Desktop spawns us.
REPO_ROOT = Path(__file__).resolve().parent
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load .env before any agent imports so API keys are available without
# having to duplicate them in claude_desktop_config.json's `env` block.
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass  # python-dotenv is optional; env vars can also come from the host config.

from mcp.server.fastmcp import FastMCP

from agents.producer.agent import format_plan_summary
from core.client_context import ClientContext
from core.models import SUPPORTED_MODEL_IDS
from core.supervisor import (
    build_supervisor_graph,
    get_pending_node,
    initial_state,
    run_supervisor_async,
)
from services import mcp_pending_store
from services.hitl_service import approve_and_resume, reject_pending_plan

# Hidden default - Sonnet 4.6 is the cheaper/faster tier. Override per call
# via the `model` arg if a heavier reasoning pass is worth the cost.
DEFAULT_MCP_MODEL = "claude-sonnet-4-6"


mcp = FastMCP("marketing-agency")


# --------------------------------------------------------------------------- #
# Module-level graph singleton + pending-runs registry.
#
# Graph: V1.5 SqliteSaver checkpointer persists graph state across MCP
# restarts; the graph is built once at import to keep the compiled
# nodes + checkpointer alive for the life of the process.
#
# Pending runs registry: V1.7 moved this off a process-local dict and
# into SQLite (`mcp_pending_runs.db`) via services/mcp_pending_store.py.
# An MCP restart no longer loses the operator-facing context
# (thread_id, prompt, client_id, model, task_type, plan_id, config) that
# resume_agency_workflow needs.
# --------------------------------------------------------------------------- #


# V1.4: pause point moved from 'producer' (a black-box approval) to
# 'producer_submit' (which has a fully compiled plan in SQL ready for
# review). The pause message includes the plan's exact prompt + assets so
# Claude Desktop can show the operator what is about to be spent on.
_GRAPH = build_supervisor_graph(interrupt_before=["producer_submit"])

# V1.7: the operator-facing pending-runs registry now lives in SQLite
# (mcp_pending_runs.db) so an MCP restart no longer strands the
# checkpoint-without-context. The dict-shaped helpers in
# services/mcp_pending_store.py preserve the call-site semantics that
# used to be a process-local dict here.


def _format_run_result(result: dict, chosen_model: str) -> str:
    """Render a completed graph result as the text payload returned to Claude."""
    if result.get("error"):
        return f"ERROR: {result['error']}"

    lines: list[str] = [
        f"Routed to: {result.get('current_agent', 'unknown')}",
        f"Task type: {result.get('task_type', 'unknown')}",
        f"Model:     {chosen_model}",
        "---",
    ]
    for msg in result.get("messages", []):
        role = msg.__class__.__name__
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"[{role}] {content}")

    artifacts = result.get("artifacts") or {}
    if artifacts:
        lines.append("---")
        lines.append("Artifacts:")
        for key, value in artifacts.items():
            lines.append(f"  {key}: {value}")

    return "\n".join(lines)


@mcp.tool()
def run_agency_agent(
    prompt: str,
    client_id: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Run the marketing agency LangGraph supervisor and return the result.

    The supervisor routes to one of four workers:
      - 'research' (Strategist)  - competitor scrape + winning hook extraction. Needs client_id.
      - 'produce'  (Producer)    - Kling Omni-Video generation. Needs client_id.
                                   PAUSES for human approval before running.
      - 'analyze'  (Analyst)     - Meta Insights -> negative constraints. Needs client_id.
      - 'outreach' (Outreach)    - prospect discovery + pitch PDFs. NO client_id.

    HITL: when routing resolves to the Producer, the graph pauses before any
    paid Kling API call. This tool returns a 'paused' message containing the
    thread_id; call resume_agency_workflow(thread_id, approve=True/False) to
    finish or abandon the run.

    Args:
        prompt: The instruction (e.g. 'Find 5 fitness apparel brands in the UK',
                'Produce a video using hook WH-003 with our default character').
        client_id: Optional client slug for client-scoped work. Required when
                   task_type is research/produce/analyze. Must be None (or
                   omitted) when task_type is outreach.
        task_type: Optional explicit routing ('research' | 'produce' | 'analyze'
                   | 'outreach'). When omitted, the supervisor's keyword router
                   infers from the prompt.
        model: Optional Anthropic model ID. Defaults to 'claude-sonnet-4-6'.
               Pass 'claude-opus-4-7' for premium reasoning (~3x cost).

    Returns:
        Either a formatted text result, or a 'paused' message with the
        thread_id when the Producer HITL gate triggered.
    """
    chosen_model = model or DEFAULT_MCP_MODEL
    if chosen_model not in SUPPORTED_MODEL_IDS:
        return (
            f"ERROR: Unsupported model {chosen_model!r}. "
            f"Choose one of: {', '.join(SUPPORTED_MODEL_IDS)}"
        )

    # Fresh thread_id per submission - prevents accidental collision with a
    # prior pending run on the same client.
    thread_id = f"mcp-{client_id or 'global'}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id, "model": chosen_model}}

    try:
        result = asyncio.run(run_supervisor_async(
            _GRAPH,
            initial_state(
                client_id=client_id, user_message=prompt, task_type=task_type
            ),
            config=config,
        ))
    except Exception as e:
        # Catch-all so the MCP server never crashes mid-tool-call; surface
        # the error back to Claude Desktop as text.
        return f"ERROR running agency graph: {type(e).__name__}: {e}"

    # V1.4: paused before producer_submit? Pull the compiled plan from SQL
    # so the operator reviews the EXACT prompt about to be sent to Kling.
    pending_node = get_pending_node(_GRAPH, config=config)
    if pending_node == "producer_submit":
        plan_id = result.get("plan_id")
        plan_summary = "(no plan compiled - submit will refuse)"
        if plan_id and client_id:
            try:
                ctx = ClientContext.load(client_id)
                plan = ctx.get_video_plan(plan_id)
                if plan is not None:
                    plan_summary = format_plan_summary(plan)
            except Exception as e:
                plan_summary = f"(could not load plan {plan_id}: {e})"

        # V1.7: durable replacement for the in-memory _PENDING_RUNS dict.
        mcp_pending_store.record_pending(
            thread_id,
            client_id=client_id,
            model=chosen_model,
            prompt=prompt,
            task_type=result.get("task_type"),
            plan_id=plan_id,
            config=config,
        )
        return (
            "Workflow paused for financial safety. Review the COMPILED plan "
            "below and use the resume_agency_workflow tool to approve or reject.\n"
            f"\nthread_id: {thread_id}"
            f"\nclient_id: {client_id}"
            f"\nmodel:     {chosen_model}"
            f"\nrouted_to: producer_submit"
            f"\n---\nOperator instruction:\n{prompt}"
            f"\n---\nCompiled plan:\n{plan_summary}"
            f"\n---\nNext step: call resume_agency_workflow("
            f"thread_id='{thread_id}', approve=True) to submit this plan "
            f"to Kling (this WILL spend credits), or approve=False to "
            f"cancel (plan will be marked rejected)."
        )

    return _format_run_result(result, chosen_model)


@mcp.tool()
def resume_agency_workflow(thread_id: str, approve: bool) -> str:
    """Approve or reject a previously-paused Producer workflow.

    Pair tool to run_agency_agent: when run_agency_agent returns a
    'Workflow paused' message, the Producer node is queued behind the HITL
    gate. Call this with approve=True to spend the Kling credits and finish
    the run, or approve=False to abandon the checkpoint.

    Args:
        thread_id: The thread_id from the 'Workflow paused' message.
        approve:   True to run the Producer; False to cancel.

    Returns:
        On approve: the formatted result of the now-completed Producer run
                    (videos generated, performance log entries, etc.).
        On reject:  a confirmation that the run was cancelled with no Kling
                    spend.
    """
    # V1.7: pop_pending mirrors the old dict.pop(k, None) semantics but
    # against SQLite, so an MCP restart no longer loses the registry.
    pending = mcp_pending_store.pop_pending(thread_id)
    if pending is None:
        return (
            f"ERROR: no paused workflow with thread_id={thread_id!r}. "
            f"Either it was already approved/rejected, or the thread_id "
            f"is a typo. (The MCP pending-runs registry is durable as of "
            f"V1.7, so MCP restart no longer drops in-flight thread IDs.)"
        )

    plan_id = pending.get("plan_id")
    client_id = pending.get("client_id")

    if not approve:
        # Service owns the SQL audit-trail bookkeeping (lifecycle:
        # pending_approval | submitting -> rejected) AND (V1.5) the
        # LangGraph checkpoint drain. Best-effort; an audit failure must
        # not prevent the cancellation acknowledgement.
        rejection_note = ""
        if client_id:
            try:
                outcome = reject_pending_plan(
                    ClientContext.load(client_id), plan_id, decided_by="human",
                    graph=_GRAPH, config=pending["config"],
                )
                rejection_note = " " + outcome.note
            except Exception:
                pass
        # V1.5: reject_pending_plan now drains the SqliteSaver checkpoint
        # via graph.update_state + ainvoke(None) so snapshot.next is empty
        # by the time we return - matches the Streamlit UI's Reject.
        # V1.7: re-record + mark_decided so the rejection shows up in the
        # MCP audit panel (pop_pending DELETEd the row above).
        mcp_pending_store.restore_pending(pending)
        mcp_pending_store.mark_decided(thread_id, "rejected")
        return (
            f"Producer workflow cancelled for thread_id={thread_id}. "
            f"No Kling credits were spent.{rejection_note}"
        )

    chosen_model = pending["model"]
    outcome = approve_and_resume(_GRAPH, pending["config"])
    if not outcome.ok:
        # Restore the pending entry so the operator can retry approve/reject
        # rather than losing the checkpoint reference entirely.
        mcp_pending_store.restore_pending(pending)
        return (
            f"ERROR resuming Producer for thread_id={thread_id}: "
            f"{outcome.error}. The pause is still active - retry "
            f"resume_agency_workflow when the underlying issue is resolved."
        )
    result = outcome.result

    # V1.7: keep the audit row (pop_pending DELETEd it; mark_decided is a
    # no-op now because the row is gone, but we re-record + mark so the
    # decision shows up in list_decided for the Streamlit status panel).
    mcp_pending_store.restore_pending(pending)
    mcp_pending_store.mark_decided(thread_id, "approved")
    return f"Producer approved.\n{_format_run_result(result, chosen_model)}"


if __name__ == "__main__":
    mcp.run()
