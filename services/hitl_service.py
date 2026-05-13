"""HITL approval / rejection service.

Used by both the Streamlit HITL panel (ui/app.py) and the MCP
resume_agency_workflow tool (mcp_server.py). Encapsulates the two
approve/reject flows so the SQL audit-trail bookkeeping is identical
across callers - previously each caller duplicated the
reject_video_plan(...) on cancel pattern, which is exactly the kind of
drift that breaks audit consistency.

Boundaries:
  - Reads/writes plan state via ClientContext (which already owns the
    atomic CAS transitions).
  - Asks the caller for a graph + config to resume; does not own graph
    construction.
  - Returns a small dataclass describing the outcome so the caller can
    render whatever UX they want without re-loading state.

Does NOT enforce the cost-control invariants directly - those live on
ClientContext.claim_plan_for_submission and inside producer_submit_node.
This module is a coordination shim, not the gatekeeper.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from core.client_context import ClientContext
from core.context_schema import VideoPlan
from core.supervisor import resume_supervisor_async

# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #


@dataclass
class ApprovalResult:
    """Outcome of approve_and_resume.

    `ok` is True when the graph resumed without raising. The graph itself
    may still have written an error into result['error'] (e.g. atomic
    claim failed because someone else won the race) - callers should
    inspect `result` for the authoritative outcome.
    """
    ok: bool
    result: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class RejectionResult:
    """Outcome of reject_pending_plan."""
    plan_marked_rejected: bool
    plan_id: Optional[str]
    note: str


# --------------------------------------------------------------------------- #
# Approve
# --------------------------------------------------------------------------- #


def approve_and_resume(
    graph: Any,
    config: dict,
) -> ApprovalResult:
    """Resume a paused supervisor graph past its interrupt_before gate.

    Called when the operator clicks Approve in the Streamlit HITL panel
    OR calls resume_agency_workflow(approve=True) via MCP. The submit
    node downstream still has its own atomic claim - this function
    therefore CANNOT cause double-spend even if invoked twice.

    The runtime config (thread_id + model) must match the original
    dispatch, otherwise LangGraph cannot find the checkpoint.
    """
    try:
        result = asyncio.run(resume_supervisor_async(graph, config=config))
        return ApprovalResult(ok=True, result=result)
    except Exception as e:
        return ApprovalResult(
            ok=False,
            error=f"{type(e).__name__}: {e}",
        )


# --------------------------------------------------------------------------- #
# Reject
# --------------------------------------------------------------------------- #


def reject_pending_plan(
    ctx: ClientContext,
    plan_id: Optional[str],
    *,
    decided_by: str = "human",
) -> RejectionResult:
    """Atomically mark a plan rejected (best-effort).

    Lifecycle (V1.4.1, see core/client_context.py::reject_video_plan):
        pending_approval | submitting -> rejected

    Returns a structured result so the caller can render an appropriate
    message. Three outcomes:
      - plan_id is None              -> no audit row to update; caller
                                        likely already cleared session
                                        state. Returns ok-but-no-write.
      - plan in pending/submitting   -> SQL row transitioned to rejected.
      - plan in any other status     -> race lost (already submitted /
                                        already rejected). No write.
    """
    if not plan_id:
        return RejectionResult(
            plan_marked_rejected=False,
            plan_id=None,
            note="No plan_id was associated with this checkpoint; nothing to reject.",
        )

    marked = ctx.reject_video_plan(plan_id, decided_by=decided_by)
    if marked:
        return RejectionResult(
            plan_marked_rejected=True,
            plan_id=plan_id,
            note=f"Plan {plan_id} marked rejected.",
        )
    # Status changed under us - probably already submitted or rejected by
    # another path. The audit trail is preserved either way.
    current = ctx.get_video_plan(plan_id)
    actual = current.status.value if current else "(missing)"
    return RejectionResult(
        plan_marked_rejected=False,
        plan_id=plan_id,
        note=(
            f"Plan {plan_id} could not be marked rejected; current status is "
            f"{actual!r}. No audit-trail change made."
        ),
    )


# --------------------------------------------------------------------------- #
# Plan loading helper (small but shared)
# --------------------------------------------------------------------------- #


def load_plan_for_review(
    ctx: ClientContext,
    plan_id: Optional[str],
) -> Optional[VideoPlan]:
    """Single-call shim used by the UI/MCP HITL panels to fetch the plan
    they're about to render. Returns None when plan_id is unset or the
    row is missing - the caller handles UX.
    """
    if not plan_id:
        return None
    return ctx.get_video_plan(plan_id)
