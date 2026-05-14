"""Shared supervisor-dispatch helper.

Why this exists: V1.8 adds a Manager Agent that auto-dispatches safe
workflows through the same supervisor `mcp_server.run_agency_agent` uses.
Doing that without a shared helper would mean copy-pasting the
dispatch + HITL-pause-detection block, which is exactly the kind of
drift that breaks Producer safety.

This module centralises the dispatch into ONE function. Both the
existing `mcp_server.run_agency_agent` MCP tool AND the new
`services.manager_service.route_manager_request` call into it. The
graph instance (and therefore the `interrupt_before=['producer_submit']`
HITL gate) is injected by the caller, NOT constructed here, so the
manager cannot accidentally bypass the gate by building its own graph.

NEVER calls Kling directly. The producer_submit node is gated by the
graph's `interrupt_before`; this helper just detects the pause and
records the operator-facing context to the durable MCP pending store.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from agents.producer.agent import format_plan_summary
from core.client_context import ClientContext
from core.models import SUPPORTED_MODEL_IDS
from core.supervisor import (
    get_pending_node,
    initial_state,
    run_supervisor_async,
)
from services import mcp_pending_store

DEFAULT_MODEL = "claude-sonnet-4-6"


def _run_async(coro):
    """Run a coroutine from a sync context that may already be inside a
    running event loop (e.g. an MCP tool handler).  asyncio.run() raises
    RuntimeError in that situation; spawning a worker thread sidesteps it
    because the new thread has no event loop of its own."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class DispatchResult:
    """Outcome of one supervisor dispatch.

    `paused` is True when the graph stopped at producer_submit awaiting
    HITL approval. In that case `plan_id`, `thread_id`, and `plan_summary`
    are all populated and the caller MUST surface the approval requirement
    to the operator (markdown for MCP, panel for Streamlit).

    `result` is the raw dict from graph.ainvoke - kept so callers that
    want to render artifacts/messages have access without re-running.
    `formatted_text` is the operator-facing markdown for callers that
    just want to print it (legacy MCP tool semantics).
    """
    ok: bool
    paused: bool = False
    error: Optional[str] = None
    thread_id: Optional[str] = None
    client_id: Optional[str] = None
    model: Optional[str] = None
    task_type: Optional[str] = None
    plan_id: Optional[str] = None
    plan_summary: Optional[str] = None
    result: Optional[dict] = None
    formatted_text: Optional[str] = None


# --------------------------------------------------------------------------- #
# Internal helpers (pure)
# --------------------------------------------------------------------------- #


def format_run_result(result: dict, chosen_model: str) -> str:
    """Render a completed graph result as the markdown text returned to
    Claude / the operator. Mirrors the legacy mcp_server formatter exactly
    so callers see no behavior change from the refactor.

    Public so resume_agency_workflow (which doesn't go through
    dispatch_supervisor_run) can reuse the same formatting.
    """
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


def _format_paused_text(
    *,
    thread_id: str,
    client_id: Optional[str],
    chosen_model: str,
    task_type: Optional[str],
    prompt: str,
    plan_summary: str,
) -> str:
    """The HITL-paused markdown. Identical contract to what
    mcp_server.run_agency_agent used to emit inline so existing Claude
    Desktop conversations still parse it the same way."""
    return (
        "Workflow paused for financial safety. Review the COMPILED plan "
        "below and use the resume_agency_workflow tool to approve or reject.\n"
        f"\nthread_id: {thread_id}"
        f"\nclient_id: {client_id}"
        f"\nmodel:     {chosen_model}"
        f"\ntask_type: {task_type}"
        f"\nrouted_to: producer_submit"
        f"\n---\nOperator instruction:\n{prompt}"
        f"\n---\nCompiled plan:\n{plan_summary}"
        f"\n---\nNext step: call resume_agency_workflow("
        f"thread_id='{thread_id}', approve=True) to submit this plan "
        f"to Kling (this WILL spend credits), or approve=False to "
        f"cancel (plan will be marked rejected)."
    )


def _new_thread_id(client_id: Optional[str]) -> str:
    """Fresh per-submission thread_id - prevents accidental collision
    with a prior pending run on the same client."""
    return f"mcp-{client_id or 'global'}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def dispatch_supervisor_run(
    *,
    graph: Any,
    prompt: str,
    client_id: Optional[str],
    task_type: Optional[str],
    model: Optional[str],
) -> DispatchResult:
    """Dispatch one supervisor turn against the supplied graph.

    Caller responsibilities:
      - Pass a graph that was built with
        `interrupt_before=['producer_submit']`. This helper does NOT
        verify that, but it relies on it for HITL safety.
      - Validate `task_type` is one of the supervisor's known options
        (the supervisor's pre-flight will return an error in `result`
        if not, but a clean refusal upstream is friendlier).

    On HITL pause this helper:
      - records the operator-facing payload to the durable
        mcp_pending_store (so MCP restart no longer strands the context),
      - returns DispatchResult(paused=True, ...) with the plan summary.

    On clean completion: returns DispatchResult(ok=True, paused=False,
    result=...).

    On model validation failure / supervisor exception: returns
    DispatchResult(ok=False, error=...). NEVER raises to the caller.
    """
    chosen_model = model or DEFAULT_MODEL
    if chosen_model not in SUPPORTED_MODEL_IDS:
        return DispatchResult(
            ok=False,
            error=(
                f"Unsupported model {chosen_model!r}. "
                f"Choose one of: {', '.join(SUPPORTED_MODEL_IDS)}"
            ),
        )

    thread_id = _new_thread_id(client_id)
    config = {"configurable": {"thread_id": thread_id, "model": chosen_model}}

    try:
        result = _run_async(run_supervisor_async(
            graph,
            initial_state(
                client_id=client_id, user_message=prompt, task_type=task_type
            ),
            config=config,
        ))
    except Exception as e:
        return DispatchResult(
            ok=False,
            error=f"{type(e).__name__}: {e}",
            thread_id=thread_id,
            client_id=client_id,
            model=chosen_model,
            task_type=task_type,
        )

    pending_node = get_pending_node(graph, config=config)
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

        # V1.7 durable registry: persists the operator-facing context so
        # MCP restart no longer drops the thread_id needed to resume.
        # V1.9: tag the row with source_channel='mcp' - this helper backs
        # both run_agency_agent and manager_request, and both run inside
        # the MCP server process. A future Streamlit-side dispatch helper
        # would pass source_channel='streamlit' instead.
        mcp_pending_store.record_pending(
            thread_id,
            client_id=client_id,
            model=chosen_model,
            prompt=prompt,
            task_type=result.get("task_type"),
            plan_id=plan_id,
            config=config,
            source_channel="mcp",
        )

        formatted = _format_paused_text(
            thread_id=thread_id,
            client_id=client_id,
            chosen_model=chosen_model,
            task_type=result.get("task_type"),
            prompt=prompt,
            plan_summary=plan_summary,
        )
        return DispatchResult(
            ok=True,
            paused=True,
            thread_id=thread_id,
            client_id=client_id,
            model=chosen_model,
            task_type=result.get("task_type"),
            plan_id=plan_id,
            plan_summary=plan_summary,
            result=result,
            formatted_text=formatted,
        )

    return DispatchResult(
        ok=True,
        paused=False,
        thread_id=thread_id,
        client_id=client_id,
        model=chosen_model,
        task_type=result.get("task_type"),
        result=result,
        formatted_text=format_run_result(result, chosen_model),
    )
