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

import os
import sys
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

from core.client_context import ClientContext
from core.supervisor import build_supervisor_graph
from services import manager_service, mcp_pending_store
from services.hitl_service import approve_and_resume, reject_pending_plan
from services.supervisor_dispatch import (
    dispatch_supervisor_run,
    format_run_result,
)

# DEFAULT_MCP_MODEL is now sourced from services.supervisor_dispatch so the
# manager service and the legacy run_agency_agent share one default.


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


@mcp.tool()
def run_agency_agent(
    prompt: str,
    client_id: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """(LEGACY) Programmatic dispatch to the supervisor graph.

    V1.8: prefer the `manager_request` tool for natural-language operator
    interaction. This tool stays for explicit programmatic dispatch and
    backward compatibility - both go through the same
    services.supervisor_dispatch.dispatch_supervisor_run helper, so the
    HITL safety guarantees are identical.

    The supervisor routes to one of four workers:
      - 'research' (Strategist)  - competitor scrape + candidate-hook extraction.
      - 'produce'  (Producer)    - Kling Omni-Video plan + paused HITL gate.
      - 'analyze'  (Analyst)     - Meta Insights -> negative constraints.
      - 'outreach' (Outreach)    - prospect discovery + pitch PDFs.

    Producer always pauses before Kling submission via
    interrupt_before=['producer_submit']. The pause message contains the
    thread_id; call resume_agency_workflow(thread_id, approve=True/False)
    to finish or cancel.
    """
    out = dispatch_supervisor_run(
        graph=_GRAPH,
        prompt=prompt,
        client_id=client_id,
        task_type=task_type,
        model=model,
    )
    if not out.ok:
        return f"ERROR running agency graph: {out.error}"
    return out.formatted_text or ""


# --------------------------------------------------------------------------- #
# V1.8 Manager Agent tools (preferred operator interface)
#
# manager_request is the single tool the operator should normally talk to.
# get_agency_overview / get_client_overview are useful direct shortcuts
# for read-only inspection without going through the classifier.
#
# All three preserve the existing HITL gate (the manager dispatches via
# the same shared dispatch_supervisor_run helper that backs
# run_agency_agent) and never call Kling directly.
# --------------------------------------------------------------------------- #


@mcp.tool()
def get_agency_overview() -> str:
    """Markdown overview of all open operator tasks across every client
    + prospect, critical first.

    Read-only. Inferred from the existing state (video_plans, video_jobs,
    mcp_pending_runs, prospects/, evals/output_reviews.jsonl) - no agent
    run, no external API call. Use this to answer "what do I need to
    approve today?" / "what is waiting?" / "give me a daily overview".
    """
    return manager_service.get_agency_overview()


@mcp.tool()
def get_client_overview(client_id: str) -> str:
    """Markdown overview scoped to one client. Excludes prospects + MCP
    rows for other clients. Read-only.
    """
    return manager_service.get_client_overview(client_id)


@mcp.tool()
def manager_request(
    prompt: str,
    client_id: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
    confirm: bool = False,
) -> str:
    """Central Manager Agent. The PREFERRED operator interface.

    The Manager classifies the request and:

      - Returns a markdown overview for "what do I need to approve",
        "what is waiting", "give me overview for client X".

      - Dispatches to the right sub-agent through the existing safe
        supervisor path:
          * outreach research / prospects -> Outreach
          * competitor research / market research -> Strategist
          * video plan / creative production -> Producer (HITL-paused)
          * performance / ROAS / recommendations -> Analyst
        Producer ALWAYS pauses before any paid Kling submission - the
        manager never bypasses interrupt_before=['producer_submit'].

      - Approves a paused plan via the existing safe HITL/resume path,
        but ONLY when:
          * the operator passes confirm=True, AND
          * a matching MCP pending-runs row exists for the plan.
        Otherwise refuses and returns Streamlit guidance.

      - Rejects a plan through hitl_service (works regardless of which
        channel paused the workflow).

      - Asks for clarification when the request is ambiguous instead of
        guessing.

    Args:
        prompt: Natural-language request from the operator.
        client_id: Optional explicit client scope. Wins over keyword
                   inference when supplied.
        task_type: Optional explicit task_type ('research' | 'produce' |
                   'analyze' | 'outreach'). When supplied, the manager
                   skips classification and dispatches directly.
        model: Optional Anthropic model override (default sonnet-4-6).
        confirm: REQUIRED to be True for approve intents. Without it,
                 approve requests are refused with a clarification.
    """
    return manager_service.route_manager_request(
        prompt,
        graph=_GRAPH,
        client_id=client_id,
        task_type=task_type,
        model=model,
        confirm=confirm,
    )


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
    return f"Producer approved.\n{format_run_result(result, chosen_model)}"


if __name__ == "__main__":
    mcp.run()
