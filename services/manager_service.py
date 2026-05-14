"""Central Manager Agent service.

The operator wants to talk to ONE thing. This module is that thing.

The Manager:
  - answers READ-ONLY questions directly using operator_inbox / state
    (overview, "what's waiting", locate-artifact),
  - DISPATCHES safe sub-agent runs through the same shared dispatcher
    the legacy run_agency_agent MCP tool uses (no duplicated supervisor
    wiring, identical HITL gate),
  - SAFELY approves a paused MCP-pending plan only when the matching
    pending row exists AND the operator passed confirm=True; refuses
    otherwise with explicit Streamlit guidance,
  - REJECTS plans through hitl_service.reject_pending_plan,
  - asks for clarification when a request is ambiguous.

Hard guarantees enforced in this module:
  - Never imports KlingClient (verified by tests/test_manager_service.py).
  - Never builds a supervisor graph (uses the caller's _GRAPH).
  - Never bypasses interrupt_before=['producer_submit'].
  - approve_plan_safely() does NOT itself call Kling - only calls
    hitl_service.approve_and_resume against an MCP-recorded config.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from core.client_context import (
    DEFAULT_CLIENTS_ROOT,
    ClientContext,
)
from core.context_schema import PlanStatus
from services import mcp_pending_store, operator_inbox
from services.hitl_service import approve_and_resume, reject_pending_plan
from services.supervisor_dispatch import DispatchResult, dispatch_supervisor_run

# --------------------------------------------------------------------------- #
# Intent classification
# --------------------------------------------------------------------------- #


@dataclass
class ManagerIntent:
    """Result of classify_manager_request.

    `kind` is the high-level branch the manager will take:
      - "overview"       : read-only inbox/agency summary
      - "client_overview": read-only inbox scoped to one client
      - "locate"         : find an artifact path
      - "dispatch"       : route to a sub-agent via the supervisor
      - "approve"        : caller asked us to approve a plan
      - "reject"         : caller asked us to reject a plan
      - "clarify"        : ambiguous; manager asks for more info
    `task_type` is set when kind=='dispatch' and we know which worker.
    `confidence` is "high" / "medium" / "low" based on keyword strength.
    """
    kind: str
    confidence: str = "high"
    task_type: Optional[str] = None
    client_id: Optional[str] = None
    plan_id: Optional[str] = None
    notes: Optional[str] = None


_OVERVIEW_KEYWORDS = (
    "overview", "approve today", "what do i need", "what's waiting",
    "whats waiting", "what is waiting", "what needs", "pending tasks",
    "open tasks", "summary", "daily", "inbox", "to do", "todo",
)

_LOCATE_KEYWORDS = (
    "where can i find", "where is", "path to", "locate", "find the",
    "show me the file", "where do i find",
)

_APPROVE_KEYWORDS = (
    "approve plan", "approve vp-", "approve pending", "approve the plan",
    "approve producer plan",
)

_REJECT_KEYWORDS = (
    "reject plan", "reject vp-", "cancel plan", "discard plan",
    "reject the plan",
)

# Sub-agent dispatch keywords. Reuses the spirit of core/router.py but
# with manager-flavoured phrasing.
_DISPATCH_KEYWORDS: dict[str, tuple[str, ...]] = {
    "outreach": (
        "outreach", "lead generation", "lead gen", "find leads",
        "find brands", "find prospects", "find new clients", "prospect ",
        "prospects ", "client acquisition",
    ),
    "research": (
        "strategist", "research competitors", "market research",
        "competitor research", "competitive intelligence", "discover hooks",
        "find hooks", "scrape ad library", "audit competitors",
        "run strategist", "competitor scrape",
    ),
    "produce": (
        "video plan", "create video", "create a video", "produce a video",
        "make a video", "render video", "compile video", "kling video",
        "ad creative", "video for client", "creative production",
        "run producer",
    ),
    "analyze": (
        "performance", "analyze performance", "roas", "ctr", "meta insights",
        "campaign analysis", "review performance", "feedback loop",
        "negative constraint", "run analyst",
    ),
}


def _extract_plan_id(text: str) -> Optional[str]:
    """Extract a VP-### token if present. Conservative: only matches the
    canonical format, avoids false positives on 'VP' as a word."""
    import re
    m = re.search(r"\b(VP-\d{1,4})\b", text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _extract_client_id(text: str, *, known_clients: list[str]) -> Optional[str]:
    """Match against the actual list of onboarded clients - safer than
    a free regex."""
    text_lower = text.lower()
    for cid in known_clients:
        if cid.lower() in text_lower:
            return cid
    return None


def classify_manager_request(
    prompt: str,
    *,
    explicit_client_id: Optional[str] = None,
    explicit_task_type: Optional[str] = None,
    known_clients: Optional[list[str]] = None,
    clients_root: Optional[Path] = None,
) -> ManagerIntent:
    """Deterministic keyword classifier. No LLM. Returns a ManagerIntent
    that downstream code switches on.

    `explicit_*` arguments WIN over keyword inference. The MCP tool
    surfaces them so the operator can override the classifier when
    needed (e.g. force task_type='produce' when the prompt is ambiguous).
    """
    if not prompt or not prompt.strip():
        return ManagerIntent(kind="clarify", confidence="low",
                             notes="Empty prompt.")

    text = prompt.lower()

    # If caller supplied explicit task_type, dispatch is intended.
    if explicit_task_type in ("research", "produce", "analyze", "outreach"):
        return ManagerIntent(
            kind="dispatch",
            confidence="high",
            task_type=explicit_task_type,
            client_id=explicit_client_id,
        )

    # Approve / reject - explicit verbs first, since "approve" is rare
    # outside that intent.
    def _client_from_prompt() -> Optional[str]:
        if explicit_client_id:
            return explicit_client_id
        nonlocal known_clients
        if known_clients is None:
            from core.client_context import list_clients as _list_clients
            known_clients = _list_clients(clients_root=clients_root)
        return _extract_client_id(prompt, known_clients=known_clients)

    if any(kw in text for kw in _APPROVE_KEYWORDS):
        return ManagerIntent(
            kind="approve",
            confidence="high",
            client_id=_client_from_prompt(),
            plan_id=_extract_plan_id(prompt),
        )
    if any(kw in text for kw in _REJECT_KEYWORDS):
        return ManagerIntent(
            kind="reject",
            confidence="high",
            client_id=_client_from_prompt(),
            plan_id=_extract_plan_id(prompt),
        )

    # Locate
    if any(kw in text for kw in _LOCATE_KEYWORDS):
        return ManagerIntent(kind="locate", confidence="medium",
                             client_id=explicit_client_id, notes=prompt)

    # Overview / inbox
    if any(kw in text for kw in _OVERVIEW_KEYWORDS):
        if explicit_client_id:
            return ManagerIntent(kind="client_overview", confidence="high",
                                 client_id=explicit_client_id)
        # "overview for client X" - look for client name in the prompt.
        if known_clients is None:
            from core.client_context import list_clients as _list_clients
            known_clients = _list_clients(clients_root=clients_root)
        cid = _extract_client_id(prompt, known_clients=known_clients)
        if cid:
            return ManagerIntent(kind="client_overview", confidence="high",
                                 client_id=cid)
        return ManagerIntent(kind="overview", confidence="high")

    # Sub-agent dispatch
    scores = {
        tt: sum(1 for kw in kws if kw in text)
        for tt, kws in _DISPATCH_KEYWORDS.items()
    }
    winner, score = max(scores.items(), key=lambda kv: kv[1])
    if score >= 1:
        # Try to scrape a client_id from the prompt if not explicit.
        cid = explicit_client_id
        if cid is None:
            if known_clients is None:
                from core.client_context import list_clients as _list_clients
                known_clients = _list_clients(clients_root=clients_root)
            cid = _extract_client_id(prompt, known_clients=known_clients)
        return ManagerIntent(
            kind="dispatch",
            confidence="high" if score >= 2 else "medium",
            task_type=winner,
            client_id=cid,
        )

    return ManagerIntent(kind="clarify", confidence="low",
                         notes=("Could not classify the request as overview, "
                                "dispatch, approve, reject, or locate."))


# --------------------------------------------------------------------------- #
# Read-only answers
# --------------------------------------------------------------------------- #


def get_agency_overview(
    *,
    clients_root: Optional[Path] = None,
    prospects_root: Optional[Path] = None,
    mcp_db_path: Optional[Path] = None,
    eval_path: Optional[Path] = None,
) -> str:
    """Markdown overview across all clients + prospects. Read-only."""
    tasks = operator_inbox.collect_operator_tasks(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db_path,
        eval_path=eval_path,
    )
    return operator_inbox.summarize_operator_tasks(tasks)


def get_client_overview(
    client_id: str,
    *,
    clients_root: Optional[Path] = None,
    eval_path: Optional[Path] = None,
) -> str:
    """Markdown overview scoped to one client. Read-only."""
    tasks = operator_inbox.collect_client_tasks(
        client_id, clients_root=clients_root, eval_path=eval_path,
    )
    if not tasks:
        return f"## {client_id} overview\n\nNo open tasks for this client."
    md = operator_inbox.summarize_operator_tasks(tasks)
    # Replace the generic header with a client-scoped one for clarity.
    return md.replace("## Agency overview", f"## {client_id} overview", 1)


# --------------------------------------------------------------------------- #
# Locate artifact
# --------------------------------------------------------------------------- #


@dataclass
class ArtifactLocation:
    path: Optional[str]
    exists: bool
    hint: str


def locate_artifact(
    source_type: str,
    source_id: str,
    *,
    client_id: Optional[str] = None,
    prospect_id: Optional[str] = None,
    clients_root: Optional[Path] = None,
    prospects_root: Optional[Path] = None,
) -> ArtifactLocation:
    """Best-effort path resolution. Returns a hint even when the file is
    missing so the operator knows where it WOULD be."""
    if clients_root is None:
        clients_root = DEFAULT_CLIENTS_ROOT
    if prospects_root is None:
        from agents.outreach.prospect_store import DEFAULT_PROSPECTS_ROOT
        prospects_root = DEFAULT_PROSPECTS_ROOT

    if source_type == "prospect_pitch" and (prospect_id or source_id):
        pid = prospect_id or source_id
        path = prospects_root / pid / "pitch.pdf"
        return ArtifactLocation(
            path=str(path), exists=path.exists(),
            hint=f"Pitch PDF for prospect {pid}.",
        )
    if source_type == "prospect_audit" and (prospect_id or source_id):
        pid = prospect_id or source_id
        path = prospects_root / pid / "audit.json"
        return ArtifactLocation(
            path=str(path), exists=path.exists(),
            hint=f"Outreach audit JSON for prospect {pid}.",
        )
    if source_type in ("video_plan", "video_job") and client_id:
        return ArtifactLocation(
            path=None, exists=False,
            hint=(
                f"{source_type} {source_id} lives in "
                f"clients/{client_id}/client_data.db. View it via the "
                f"Streamlit Performance log tab or get_client_overview('{client_id}')."
            ),
        )
    if source_type == "client_silo" and client_id:
        path = clients_root / client_id
        return ArtifactLocation(
            path=str(path), exists=path.exists(),
            hint=f"Client silo for {client_id}.",
        )
    return ArtifactLocation(
        path=None, exists=False,
        hint=(
            f"Don't know how to locate source_type={source_type!r}. "
            f"Try one of: prospect_pitch, prospect_audit, video_plan, "
            f"video_job, client_silo."
        ),
    )


# --------------------------------------------------------------------------- #
# Safe approve / reject
# --------------------------------------------------------------------------- #


def _streamlit_guidance_for_plan(client_id: str, plan_id: str) -> str:
    """Markdown the manager returns when MCP can't safely approve."""
    return (
        f"**Cannot approve plan `{plan_id}` for client `{client_id}` via MCP.**\n\n"
        f"The plan exists in `video_plans` with status `pending_approval`, "
        f"but no matching MCP pending-runs row was found. This usually means "
        f"the pause originated in Streamlit, or the MCP server was restarted "
        f"before the durable-pending-registry landed.\n\n"
        f"To approve, use Streamlit:\n"
        f"1. `streamlit run ui/app.py`\n"
        f"2. Sidebar -> select client `{client_id}`\n"
        f"3. The HITL approval panel renders at the top with the compiled plan.\n"
        f"4. Click **Approve - submit to Kling**.\n\n"
        f"Alternatively, if you originated this via an MCP `run_agency_agent` "
        f"call in the current session and remember the thread_id, run:\n\n"
        f"    resume_agency_workflow(thread_id=\"<that-thread-id>\", approve=True)\n\n"
        f"The manager will not silently submit this plan to Kling."
    )


def approve_plan_safely(
    client_id: str,
    plan_id: str,
    *,
    confirm: bool = False,
    graph: Any = None,
    mcp_db_path: Optional[Path] = None,
    clients_root: Optional[Path] = None,
) -> str:
    """Approve a pending Producer plan, ONLY via the safe MCP-resume path.

    Refuses (returns markdown, no side effects) when:
      - confirm is False (operator must explicitly say so)
      - graph is None (caller didn't pass the supervisor graph)
      - no MCP pending row matches the plan_id (Streamlit-originated
        pause, or MCP context lost)
      - more than one MCP pending row matches (ambiguous)
      - the plan is not currently in pending_approval status
    """
    if not confirm:
        return (
            f"**Refused.** Manager will not approve plan `{plan_id}` "
            f"without `confirm=True`. Re-issue the request with explicit "
            f"confirmation if you intend to spend Kling credits."
        )

    # Sanity: does the plan even exist in the right state?
    try:
        ctx = ClientContext.load(client_id, clients_root=clients_root)
    except Exception as e:
        return f"**Refused.** Could not load client `{client_id}`: {e}"

    plan = ctx.get_video_plan(plan_id)
    if plan is None:
        return f"**Refused.** Plan `{plan_id}` does not exist for client `{client_id}`."
    if plan.status != PlanStatus.PENDING_APPROVAL:
        return (
            f"**Refused.** Plan `{plan_id}` is in status "
            f"`{plan.status.value}`, not `pending_approval`. "
            f"No action taken."
        )

    # Find a matching MCP pending row by (client_id, plan_id).
    pending_rows = (
        mcp_pending_store.list_pending(db_path=mcp_db_path)
        if mcp_db_path is not None
        else mcp_pending_store.list_pending()
    )
    matches = [
        r for r in pending_rows
        if r.get("client_id") == client_id and r.get("plan_id") == plan_id
    ]

    if len(matches) == 0:
        return _streamlit_guidance_for_plan(client_id, plan_id)

    if len(matches) > 1:
        thread_ids = ", ".join(repr(r["thread_id"]) for r in matches)
        return (
            f"**Refused.** Ambiguous: {len(matches)} MCP pending workflows "
            f"target plan `{plan_id}` for client `{client_id}`. "
            f"Resolve explicitly via "
            f"`resume_agency_workflow(thread_id=...)` for one of: "
            f"{thread_ids}."
        )

    if graph is None:
        return (
            f"**Refused.** No supervisor graph supplied to the manager - "
            f"cannot resume the LangGraph checkpoint for plan `{plan_id}`. "
            f"This is a wiring bug; report it."
        )

    row = matches[0]
    thread_id = row["thread_id"]
    config = row["config"]

    # Pop first so we mirror the legacy resume path exactly. If the
    # graph resume raises we restore + return an error so the operator
    # can retry without the row vanishing.
    popped = (
        mcp_pending_store.pop_pending(thread_id, db_path=mcp_db_path)
        if mcp_db_path is not None
        else mcp_pending_store.pop_pending(thread_id)
    )
    if popped is None:
        # Race: someone else popped it between list and pop.
        return (
            f"**Refused.** Lost a race against another approve/reject for "
            f"thread `{thread_id}`. Re-run get_agency_overview to see the "
            f"current state."
        )

    outcome = approve_and_resume(graph, config)
    if not outcome.ok:
        if mcp_db_path is not None:
            mcp_pending_store.restore_pending(popped, db_path=mcp_db_path)
        else:
            mcp_pending_store.restore_pending(popped)
        return (
            f"**Resume failed** for plan `{plan_id}` (thread `{thread_id}`): "
            f"{outcome.error}. Pending row restored - retry when the underlying "
            f"issue is resolved."
        )

    # Restore + mark_decided so the audit trail keeps the row.
    if mcp_db_path is not None:
        mcp_pending_store.restore_pending(popped, db_path=mcp_db_path)
        mcp_pending_store.mark_decided(thread_id, "approved", db_path=mcp_db_path)
    else:
        mcp_pending_store.restore_pending(popped)
        mcp_pending_store.mark_decided(thread_id, "approved")

    return (
        f"**Approved** plan `{plan_id}` for client `{client_id}` "
        f"(thread `{thread_id}`). Producer submitted to Kling. "
        f"Poll status via the Streamlit Generated videos tab."
    )


def reject_plan_safely(
    client_id: str,
    plan_id: str,
    *,
    graph: Any = None,
    mcp_db_path: Optional[Path] = None,
    clients_root: Optional[Path] = None,
) -> str:
    """Mark a plan rejected through the existing safe service path.

    Works regardless of whether the plan was paused via Streamlit or
    MCP - the underlying SQL transition is independent of the channel.
    If a matching MCP pending row exists, also drains the LangGraph
    checkpoint via hitl_service.reject_pending_plan.
    """
    try:
        ctx = ClientContext.load(client_id, clients_root=clients_root)
    except Exception as e:
        return f"**Refused.** Could not load client `{client_id}`: {e}"

    # Look for a matching MCP pending row so we can drain the checkpoint
    # too. If none, hitl_service.reject_pending_plan still works on the
    # SQL side.
    pending_rows = (
        mcp_pending_store.list_pending(db_path=mcp_db_path)
        if mcp_db_path is not None
        else mcp_pending_store.list_pending()
    )
    matches = [
        r for r in pending_rows
        if r.get("client_id") == client_id and r.get("plan_id") == plan_id
    ]
    config = matches[0]["config"] if (matches and graph is not None) else None

    outcome = reject_pending_plan(
        ctx, plan_id, decided_by="human", graph=graph, config=config,
    )

    # If we drained an MCP-pending row, mark it rejected for audit.
    if matches:
        thread_id = matches[0]["thread_id"]
        if mcp_db_path is not None:
            mcp_pending_store.mark_decided(thread_id, "rejected", db_path=mcp_db_path)
        else:
            mcp_pending_store.mark_decided(thread_id, "rejected")

    return f"**Rejected** plan `{plan_id}` for client `{client_id}`. {outcome.note}"


# --------------------------------------------------------------------------- #
# The single operator-facing entry point
# --------------------------------------------------------------------------- #


def route_manager_request(
    prompt: str,
    *,
    graph: Any,
    client_id: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
    confirm: bool = False,
    clients_root: Optional[Path] = None,
    prospects_root: Optional[Path] = None,
    mcp_db_path: Optional[Path] = None,
    eval_path: Optional[Path] = None,
) -> str:
    """Top-level manager dispatch. Returns markdown.

    The routing decisions:
      overview           -> get_agency_overview() markdown
      client_overview    -> get_client_overview() markdown
      locate             -> structured location guidance (best-effort)
      dispatch           -> dispatch_supervisor_run via the SAME helper
                            that backs run_agency_agent (so HITL is
                            preserved). The Producer pauses; the manager
                            surfaces the pause exactly as the legacy tool.
      approve            -> approve_plan_safely(confirm=...) - SAFE.
                            Refuses if confirm=False or no MCP context.
      reject             -> reject_plan_safely() - SAFE.
      clarify            -> question back to operator.
    """
    intent = classify_manager_request(
        prompt,
        explicit_client_id=client_id,
        explicit_task_type=task_type,
        clients_root=clients_root,
    )

    if intent.kind == "overview":
        return get_agency_overview(
            clients_root=clients_root,
            prospects_root=prospects_root,
            mcp_db_path=mcp_db_path,
            eval_path=eval_path,
        )

    if intent.kind == "client_overview":
        target = intent.client_id or client_id
        if not target:
            return (
                "**Clarification needed.** I read this as a client-scoped "
                "overview but couldn't identify which client. Please pass "
                "`client_id=...` or include the client name in the prompt."
            )
        return get_client_overview(
            target, clients_root=clients_root, eval_path=eval_path,
        )

    if intent.kind == "locate":
        return (
            "**Locate request.** Manager can locate prospect_pitch / "
            "prospect_audit / video_plan / video_job / client_silo. "
            "Call locate_artifact(source_type, source_id, ...) directly "
            "for a structured answer, or run get_agency_overview() to see "
            "the current artifact paths in context."
        )

    if intent.kind == "approve":
        target_client = intent.client_id or client_id
        if not target_client or not intent.plan_id:
            return (
                "**Clarification needed.** Approve which plan, for which "
                "client? Re-issue with both, e.g. "
                "`approve plan VP-004 for client acme` (and pass "
                "`confirm=True` to spend Kling credits)."
            )
        return approve_plan_safely(
            target_client, intent.plan_id,
            confirm=confirm, graph=graph,
            mcp_db_path=mcp_db_path, clients_root=clients_root,
        )

    if intent.kind == "reject":
        target_client = intent.client_id or client_id
        if not target_client or not intent.plan_id:
            return (
                "**Clarification needed.** Reject which plan, for which "
                "client? Re-issue with both, e.g. "
                "`reject plan VP-004 for client acme`."
            )
        return reject_plan_safely(
            target_client, intent.plan_id, graph=graph,
            mcp_db_path=mcp_db_path, clients_root=clients_root,
        )

    if intent.kind == "dispatch":
        if intent.confidence == "low":
            return (
                "**Clarification needed.** I'm not confident about the "
                "intended sub-agent. Re-issue with `task_type=` set to one "
                "of `research` / `produce` / `analyze` / `outreach`."
            )
        return _dispatch_via_supervisor(
            graph=graph,
            prompt=prompt,
            client_id=intent.client_id or client_id,
            task_type=intent.task_type or task_type,
            model=model,
        )

    # clarify
    return (
        f"**Clarification needed.** {intent.notes or ''}\n\n"
        f"Try one of:\n"
        f"- 'What do I need to approve today?'  (overview)\n"
        f"- 'Give me overview for client acme.'  (client overview)\n"
        f"- 'Run strategist for client acme.'  (dispatch)\n"
        f"- 'Approve plan VP-004 for client acme.'  (HITL approval)\n"
        f"- 'Reject plan VP-004 for client acme.'  (HITL rejection)"
    )


def _dispatch_via_supervisor(
    *,
    graph: Any,
    prompt: str,
    client_id: Optional[str],
    task_type: Optional[str],
    model: Optional[str],
) -> str:
    """Wraps dispatch_supervisor_run + renders manager-flavoured markdown.

    Identical HITL behaviour to run_agency_agent: when the dispatch
    pauses at producer_submit, the same paused message is surfaced and
    the same MCP pending row is recorded by the helper.
    """
    out: DispatchResult = dispatch_supervisor_run(
        graph=graph,
        prompt=prompt,
        client_id=client_id,
        task_type=task_type,
        model=model,
    )
    if not out.ok:
        return f"**Manager dispatch failed:** {out.error}"

    if out.paused:
        # Manager-flavoured prefix on top of the dispatcher's standard
        # paused markdown so the operator knows the manager routed it.
        return (
            f"**Manager dispatched the request and the workflow paused for "
            f"approval.**\n\n{out.formatted_text}"
        )

    return (
        f"**Manager dispatched the request.** Result:\n\n"
        f"{out.formatted_text}"
    )
