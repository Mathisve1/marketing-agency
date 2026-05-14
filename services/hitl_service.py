"""HITL approval / rejection service.

Used by both the Streamlit HITL panel (ui/app.py) and the MCP
resume_agency_workflow tool (mcp_server.py). Encapsulates the two
approve/reject flows so the SQL audit-trail bookkeeping is identical
across callers - previously each caller duplicated the
reject_video_plan(...) on cancel pattern, which is exactly the kind of
drift that breaks audit consistency.

V1.5 extension: rejection now also DRAINS the LangGraph checkpoint via
graph.update_state(...) + ainvoke(None), so the paused workflow
terminates cleanly instead of leaving snapshot.next stuck on
'producer_submit' forever. Without this, the persistent checkpoint
(SqliteSaver) would accumulate orphaned paused threads on disk.

Boundaries:
  - Reads/writes plan state via ClientContext (which already owns the
    atomic CAS transitions).
  - Asks the caller for a graph + config to resume / drain; does not
    own graph construction.
  - Returns a small dataclass describing the outcome so the caller can
    render whatever UX they want without re-loading state.

Does NOT enforce the cost-control invariants directly - those live on
ClientContext.claim_plan_for_submission and inside producer_submit_node.
This module is a coordination shim, not the gatekeeper.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
from typing import Any, Optional

from core.client_context import ClientContext
from core.context_schema import VideoPlan
from core.supervisor import resume_supervisor_async


def _run_async(coro):
    """Run a coroutine from a sync context that may already be inside a
    running event loop.  Worker thread has no event loop, so asyncio.run()
    works there even when called from an MCP handler."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()

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
    """Outcome of reject_pending_plan.

    V1.5 adds `checkpoint_drained`: True when the LangGraph checkpoint
    was successfully advanced past the paused node so snapshot.next is
    now empty. False means the SQL row was marked rejected but the
    graph checkpoint is still pending (e.g. caller did not pass graph
    + config, or the drain raised).
    """
    plan_marked_rejected: bool
    plan_id: Optional[str]
    note: str
    checkpoint_drained: bool = False


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
        result = _run_async(resume_supervisor_async(graph, config=config))
        return ApprovalResult(ok=True, result=result)
    except Exception as e:
        return ApprovalResult(
            ok=False,
            error=f"{type(e).__name__}: {e}",
        )


# --------------------------------------------------------------------------- #
# Reject
# --------------------------------------------------------------------------- #


_REJECTION_REASON = "User rejected the workflow."


def _drain_rejected_checkpoint(graph: Any, config: dict) -> bool:
    """Drive the paused checkpoint to END so snapshot.next empties.

    Mechanism (see V1.5 design note in module docstring):
      1. Read snapshot.next - typically ('producer_submit',) for our HITL.
      2. graph.update_state(..., as_node=pending) - LangGraph treats
         the paused node as if it ran with our rejection payload merged
         into state. The next checkpoint snapshot is therefore "after"
         that node.
      3. ainvoke(None) - evaluates the outgoing edge from `pending`,
         which is END for producer_submit, terminating the run.

    We attribute the update to the PAUSED node (typically producer_submit),
    not to producer_plan. Attributing to producer_plan would re-evaluate
    _after_plan(state) - see plan_id still set - and route back to
    producer_submit. Subtle infinite-loop trap.

    Returns True when the post-drain snapshot.next is empty.
    """
    snapshot = graph.get_state(config=config)
    if not snapshot.next:
        return True  # already drained - idempotent

    pending_node = snapshot.next[0]
    graph.update_state(
        config,
        {
            "error": _REJECTION_REASON,
            "current_agent": "rejected",
        },
        as_node=pending_node,
    )

    # Drive any remaining graph evaluation. For producer_submit -> END
    # this returns immediately at the END barrier.
    _run_async(resume_supervisor_async(graph, config=config))

    final = graph.get_state(config=config)
    return not final.next


def reject_pending_plan(
    ctx: ClientContext,
    plan_id: Optional[str],
    *,
    decided_by: str = "human",
    graph: Any = None,
    config: Optional[dict] = None,
) -> RejectionResult:
    """Atomically mark a plan rejected (best-effort) AND drain the
    paused LangGraph checkpoint.

    Lifecycle (V1.4.1, see core/client_context.py::reject_video_plan):
        pending_approval | submitting -> rejected

    V1.5: pass `graph` + `config` to also clear the LangGraph
    checkpoint. Without those, the SQL row gets marked rejected but
    snapshot.next stays on 'producer_submit' forever - the persistent
    SqliteSaver would accumulate orphaned paused threads on disk.

    Returns a structured result. Possible outcomes:
      - plan_id is None              -> no audit row to update; caller
                                        likely already cleared session
                                        state. Returns ok-but-no-write.
      - plan in pending/submitting   -> SQL row transitioned to rejected.
      - plan in any other status     -> race lost (already submitted /
                                        already rejected). No write.
    In all cases, if graph + config were supplied, the checkpoint drain
    runs regardless of the SQL outcome (rejecting a workflow whose plan
    was already submitted is still a valid "stop this thread" gesture).
    """
    if not plan_id:
        # Still drain the checkpoint if a graph + config were provided -
        # the operator may have rejected a turn that did not produce a
        # plan_id (e.g. a different paused workflow entirely).
        drained = False
        if graph is not None and config is not None:
            try:
                drained = _drain_rejected_checkpoint(graph, config)
            except Exception:
                drained = False
        return RejectionResult(
            plan_marked_rejected=False,
            plan_id=None,
            note="No plan_id was associated with this checkpoint; nothing to reject.",
            checkpoint_drained=drained,
        )

    marked = ctx.reject_video_plan(plan_id, decided_by=decided_by)

    drained = False
    if graph is not None and config is not None:
        try:
            drained = _drain_rejected_checkpoint(graph, config)
        except Exception:
            # Don't fail the rejection just because the drain raised - the
            # SQL audit trail is the source of truth. Operator can see
            # checkpoint_drained=False and dig into logs if needed.
            drained = False

    if marked:
        return RejectionResult(
            plan_marked_rejected=True,
            plan_id=plan_id,
            note=f"Plan {plan_id} marked rejected.",
            checkpoint_drained=drained,
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
        checkpoint_drained=drained,
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
