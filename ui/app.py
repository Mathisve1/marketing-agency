"""Streamlit Command Center.

V1.3 (3/N): adds HITL approval gate before the Producer runs. The
supervisor graph is built once per session with interrupt_before=
['producer'] and cached in st.session_state so the MemorySaver
checkpoints survive across Streamlit reruns. On dispatch, if the graph
pauses at the producer node, the UI hides the dispatch tab and renders
an Approve/Reject panel instead. Approve resumes via
asyncio.run(resume_supervisor_async(...)); Reject just clears session
state (the orphaned checkpoint stays in memory until Streamlit
restart - acceptable for single-operator use).
"""
from __future__ import annotations

import asyncio
import json

# V1.7 imports for the operator status strip + tab_eval. Importing the
# eval_review module from scripts/ requires adding scripts/ to sys.path
# (this app already runs with the repo root as cwd via Streamlit, so
# scripts/ is a sibling and not on sys.path by default).
import sys as _sys  # noqa: E402
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from agents.outreach.prospect_store import promote_to_client
from agents.producer.kling.client import KlingAPIError, KlingClient
from core.client_context import ClientContext, list_clients
from core.context_schema import JobStatus, PlanStatus
from core.models import SUPPORTED_MODELS
from core.supervisor import (
    build_supervisor_graph,
    get_pending_node,
    initial_state,
    run_supervisor_async,
)
from services.hitl_service import (
    approve_and_resume,
    load_plan_for_review,
    reject_pending_plan,
)

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)
import eval_review  # noqa: E402

st.set_page_config(page_title="Agency Command Center", layout="wide")
st.title("Agency Command Center")


# --------------------------------------------------------------------------- #
# Session-scoped graph cache. MemorySaver state for HITL checkpoints lives
# inside the compiled graph - caching the graph object keeps that state alive
# across Streamlit script reruns within the same session.
# --------------------------------------------------------------------------- #


if "supervisor_graph" not in st.session_state:
    # V1.4: pause point moved from 'producer' (which approved nothing concrete)
    # to 'producer_submit' (which has a fully compiled plan in SQL ready for
    # human review).
    st.session_state["supervisor_graph"] = build_supervisor_graph(
        interrupt_before=["producer_submit"]
    )
graph = st.session_state["supervisor_graph"]

if "hitl_pending" not in st.session_state:
    st.session_state["hitl_pending"] = None
if "hitl_last_status" not in st.session_state:
    st.session_state["hitl_last_status"] = None
if "hitl_last_result" not in st.session_state:
    st.session_state["hitl_last_result"] = None


# --------------------------------------------------------------------------- #
# Mode selector
# --------------------------------------------------------------------------- #


with st.sidebar:
    mode = st.radio(
        "Mode",
        options=["Client work", "Lead generation"],
        index=0,
        help=(
            "Client work: Strategist / Producer / Analyst running inside a "
            "specific client silo. Lead generation: Outreach agent hunting "
            "for new prospects globally."
        ),
    )
    st.divider()


# --------------------------------------------------------------------------- #
# LEAD GENERATION MODE
# --------------------------------------------------------------------------- #


if mode == "Lead generation":
    st.subheader("Lead generation")
    st.caption(
        "The Outreach agent runs outside any client silo. It scrapes target "
        "brands' Meta ad libraries and generates Brand Audit & Pitch PDFs in "
        "the `prospects/` folder for cold outreach. (No HITL gate - outreach "
        "doesn't route through Producer.)"
    )

    tab_run, tab_prospects = st.tabs(["Run outreach", "Prospects"])

    with tab_run:
        prompt = st.text_area(
            "Outreach instruction",
            placeholder=(
                "e.g. Find 5 fitness apparel brands in the UK, audit their "
                "current Meta ads, and generate a pitch PDF for each."
            ),
        )
        model_label = st.selectbox(
            "Model",
            options=list(SUPPORTED_MODELS.keys()),
            index=0,
            help="Sonnet 4.6 is faster/cheaper; Opus 4.7 reasons deeper but costs ~3x.",
            key="outreach_model",
        )
        model_id = SUPPORTED_MODELS[model_label]

        if st.button("Dispatch outreach", type="primary", disabled=not prompt.strip()):
            # V1.7 long-run UX: Apify ad-library scrapes are 30-60s each
            # and the agent does one per prospect. Without an explicit
            # warning, operators reflexively refresh.
            st.info(
                ":hourglass: **Outreach run in progress.** One Apify ad-library "
                "scrape per prospect (~30-60s each), capped at 5. Total "
                "wall time can exceed 5 minutes for a full batch. "
                "**Do not refresh** while the spinner is visible."
            )
            with st.spinner(
                f"Running Outreach with {model_id}..."
            ):
                result = asyncio.run(run_supervisor_async(
                    graph,
                    initial_state(
                        client_id=None,
                        user_message=prompt,
                        task_type="outreach",
                    ),
                    config={"configurable": {
                        "thread_id": f"outreach-{uuid.uuid4().hex[:8]}",
                        "model": model_id,
                    }},
                ))

            if result.get("error"):
                st.error(result["error"])
            else:
                st.success(f"Outreach run complete (model: `{model_id}`)")
                for msg in result["messages"]:
                    st.markdown(f"**{msg.__class__.__name__}**: {msg.content}")
                artifacts = result.get("artifacts") or {}
                pitches = artifacts.get("outreach_pitches") or []
                if pitches:
                    st.divider()
                    st.markdown("### Generated pitches")
                    for path in pitches:
                        p = Path(path)
                        if p.exists():
                            st.caption(p.name)
                            with p.open("rb") as f:
                                st.download_button(
                                    "Download pitch PDF",
                                    data=f.read(),
                                    file_name=p.name,
                                    mime="application/pdf",
                                    key=f"dl_pitch_{p.name}",
                                )
                if artifacts:
                    with st.expander("All artifacts (audit)"):
                        st.json(artifacts)

    with tab_prospects:
        prospects_dir = Path("prospects")
        prospect_folders = (
            sorted(
                (p for p in prospects_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if prospects_dir.exists() else []
        )

        if not prospect_folders:
            st.info("No prospects yet. Dispatch an outreach run to populate this.")
        else:
            st.caption(f"{len(prospect_folders)} prospect(s) tracked")
            for folder in prospect_folders:
                with st.expander(folder.name):
                    audit_path = folder / "audit.json"
                    pitch_path = folder / "pitch.pdf"

                    audit_data: dict = {}
                    if audit_path.exists():
                        try:
                            audit_data = json.loads(audit_path.read_text("utf-8"))
                        except json.JSONDecodeError as e:
                            st.error(f"Could not parse audit.json: {e}")

                    if audit_data:
                        hooks_count = len(audit_data.get("winning_hooks", []))
                        motions_count = len(audit_data.get("referral_motions", []))
                        st.caption(
                            f"{hooks_count} hook(s) - {motions_count} motion(s) identified - "
                            f"audited {audit_data.get('audited_at', 'unknown')}"
                        )
                        with st.expander("Full audit JSON", expanded=False):
                            st.json(audit_data)

                    if pitch_path.exists():
                        with pitch_path.open("rb") as f:
                            st.download_button(
                                "Download pitch PDF",
                                data=f.read(),
                                file_name=f"{folder.name}-pitch.pdf",
                                mime="application/pdf",
                                key=f"dl_existing_{folder.name}",
                            )

                    st.divider()
                    st.markdown("**Promote to active client**")
                    with st.form(key=f"promote_{folder.name}"):
                        new_id = st.text_input(
                            "client_id (lowercase, no spaces)",
                            value=folder.name,
                        )
                        new_name = st.text_input(
                            "Display name",
                            value=audit_data.get("prospect_name") or folder.name,
                        )
                        new_locale = st.text_input(
                            "Locale",
                            value=audit_data.get("locale") or "en-US",
                        )
                        submitted = st.form_submit_button(
                            "Onboard + seed from audit", type="primary"
                        )

                        if submitted:
                            try:
                                new_ctx, h, m = promote_to_client(
                                    prospect_id=folder.name,
                                    new_client_id=new_id,
                                    new_client_name=new_name,
                                    new_client_locale=new_locale,
                                )
                                st.success(
                                    f"Promoted `{folder.name}` -> client `{new_id}`. "
                                    f"Seeded {h} hook(s) and {m} motion(s) into "
                                    f"client_data.db. Switch to Client work mode "
                                    f"and select `{new_id}` to continue."
                                )
                                # V1.3 polish (Initiative 1): sleep so the
                                # success banner is readable (it lists hook +
                                # motion counts), then rerun so the prospect
                                # row reflects its new promoted status and
                                # list_clients() picks up the new silo.
                                time.sleep(2)
                                st.rerun()
                            except FileExistsError:
                                st.error(
                                    f"Client `{new_id}` already exists. Pick a different ID."
                                )
                            except FileNotFoundError as e:
                                st.error(f"Promotion failed: {e}")
                            except ValueError as e:
                                st.error(f"Invalid input: {e}")

    st.stop()


# --------------------------------------------------------------------------- #
# CLIENT WORK MODE
# --------------------------------------------------------------------------- #


clients = list_clients()

with st.sidebar:
    st.header("Clients")
    if not clients:
        st.info("No clients onboarded yet. Use the panel below to create one.")
    selection = st.selectbox("Active client", options=clients) if clients else None

    with st.expander("Onboard a new client"):
        new_id = st.text_input("client_id (lowercase, no spaces)")
        new_name = st.text_input("Display name")
        new_locale = st.text_input("Locale", value="nl-BE")
        if st.button("Create silo"):
            try:
                ClientContext.onboard(new_id, new_name, locale=new_locale)
                st.success(f"Onboarded {new_id!r}. Refreshing...")
                # V1.3 polish (Initiative 1): sleep briefly so the success
                # banner is visible, then rerun so list_clients() picks up
                # the new silo and the sidebar dropdown updates. Without
                # this the operator clicks Create again and hits FileExistsError.
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(str(e))

if not selection:
    st.stop()

ctx = ClientContext.load(selection)
fm, body = ctx.read()


# --------------------------------------------------------------------------- #
# HITL APPROVAL GATE
# Renders prominently at the top when the supervisor paused before producer.
# Blocks the rest of the page (st.stop) so the operator focuses on the decision.
# --------------------------------------------------------------------------- #


def _render_hitl_approval(graph) -> bool:
    """Returns True if a pending HITL was rendered (caller should st.stop).

    V1.4: reads the COMPILED plan from SQL by plan_id and shows the operator
    the EXACT Kling brief about to be submitted (full prompt, negative
    prompt, asset paths, duration, aspect_ratio, mode, enforced HARD
    constraint IDs). Reject marks the plan rejected in SQL so the audit
    trail records the decision.
    """
    pending = st.session_state.get("hitl_pending")
    if not pending:
        return False

    plan_id = pending.get("plan_id")
    plan_ctx = ClientContext.load(pending["client_id"])
    plan = load_plan_for_review(plan_ctx, plan_id)

    st.warning("Plan review required - paid Kling submission about to occur")

    cols = st.columns(3)
    cols[0].metric("Client", pending["client_id"])
    cols[1].metric("Model", pending["model"])
    cols[2].metric("Plan", plan_id or "(none compiled)")

    if plan is None:
        st.error(
            f"Could not load plan {plan_id!r} from video_plans. The submitter "
            f"will refuse to run; reject to clear the checkpoint."
        )
    else:
        c = st.columns(4)
        c[0].metric("Hook", plan.hook_id)
        c[1].metric("Motion", plan.motion_id or "(none)")
        c[2].metric("Duration", f"{plan.duration}s")
        c[3].metric("Aspect", plan.aspect_ratio)

        # V1.4.1: surface prior failed attempts so re-approval is informed.
        if plan.submit_attempts > 0 or plan.submit_error:
            st.error(
                f"This plan has {plan.submit_attempts} prior submit "
                f"attempt(s). Last error:\n\n{plan.submit_error or '(none recorded)'}"
            )
            if plan.submit_error and "TIMEOUT_WARNING" in plan.submit_error:
                st.warning(
                    "**Timeout caveat**: Kling may have accepted the previous "
                    "attempt despite the local error. Verify in the Kling "
                    "dashboard before re-approving to avoid a duplicate render."
                )

        st.markdown("**Character asset:** `" + plan.character_asset + "`")
        st.markdown("**Product asset:** `" + plan.product_asset + "`")
        if plan.enforced_constraint_ids:
            st.markdown(
                "**Enforced HARD negative constraints:** "
                + ", ".join(plan.enforced_constraint_ids)
            )

        with st.expander("Compiled Kling prompt", expanded=True):
            st.code(plan.prompt, language=None)
        with st.expander("Negative prompt", expanded=False):
            st.code(plan.negative_prompt, language=None)
        st.caption(
            f"mode={plan.mode}  cfg_scale={plan.cfg_scale}  created={plan.created_at}"
        )

    if pending.get("supervisor_messages"):
        with st.expander("Planner messages this turn", expanded=False):
            for msg in pending["supervisor_messages"]:
                st.markdown(f"**{msg.__class__.__name__}**: {msg.content}")

    col_a, col_r = st.columns(2)
    with col_a:
        approve_disabled = plan is None
        if st.button(
            "Approve - submit to Kling",
            type="primary",
            key="hitl_approve",
            disabled=approve_disabled,
        ):
            with st.spinner("Submitting to Kling (render happens async)..."):
                outcome = approve_and_resume(graph, pending["config"])
            if outcome.ok:
                st.session_state["hitl_last_result"] = outcome.result
                st.session_state["hitl_last_status"] = "approved"
            else:
                st.session_state["hitl_last_result"] = None
                st.session_state["hitl_last_status"] = f"error:{outcome.error}"
            st.session_state["hitl_pending"] = None
            st.rerun()
    with col_r:
        if st.button("Reject - cancel", key="hitl_reject"):
            # Service owns the SQL audit-trail bookkeeping AND (V1.5) the
            # LangGraph checkpoint drain consistently across UI + MCP
            # callers. Passing graph+config triggers _drain_rejected_checkpoint
            # so snapshot.next empties and the persistent SqliteSaver
            # doesn't accumulate orphaned paused threads on disk.
            reject_pending_plan(
                plan_ctx, plan_id, decided_by="human",
                graph=graph, config=pending["config"],
            )
            st.session_state["hitl_pending"] = None
            st.session_state["hitl_last_status"] = "rejected"
            st.session_state["hitl_last_result"] = None
            st.rerun()

    return True


if _render_hitl_approval(graph):
    st.stop()


# --------------------------------------------------------------------------- #
# Normal Client Work UI
# --------------------------------------------------------------------------- #


# V1.2: dynamic lists come from SQL. Fetched once per page render.
hooks = ctx.get_winning_hooks()
motions = ctx.get_referral_motions()
constraints = ctx.get_negative_constraints()

st.subheader(f"Client: {fm.client.name}")
st.caption(
    f"Locale: {fm.client.locale} - Updated: {fm.client.last_updated} - "
    f"{len(hooks)} hooks - {len(motions)} motions - "
    f"{len(constraints)} constraints"
)

# --------------------------------------------------------------------------- #
# V1.7 operator status strip - rendered above the tabs so the operator
# sees the system state at a glance without clicking into multiple tabs.
# Counts only; the per-row triage UI lives in tab_videos and tab_log.
# --------------------------------------------------------------------------- #


def _render_operator_status(ctx: ClientContext) -> None:
    pending_plans = ctx.list_video_plans(status=PlanStatus.PENDING_APPROVAL)
    submitting_plans = ctx.list_video_plans(status=PlanStatus.SUBMITTING)
    rejected_plans = ctx.list_video_plans(status=PlanStatus.REJECTED, limit=20)
    pending_jobs = ctx.list_video_jobs(status=JobStatus.PENDING)
    completed_jobs = ctx.list_video_jobs(status=JobStatus.COMPLETED, limit=50)
    failed_jobs = ctx.list_video_jobs(status=JobStatus.FAILED, limit=20)

    st.markdown("### Operator status")
    cols = st.columns(6)
    cols[0].metric("Plans pending", len(pending_plans))
    cols[1].metric("Plans submitting", len(submitting_plans))
    cols[2].metric("Plans rejected", len(rejected_plans))
    cols[3].metric("Jobs pending", len(pending_jobs))
    cols[4].metric("Jobs failed", len(failed_jobs))
    cols[5].metric("Jobs completed", len(completed_jobs))
    st.caption(
        "Triage pending plans + jobs in the *Generated videos* and "
        "*Performance log* tabs."
    )


_render_operator_status(ctx)
st.divider()


(
    tab_overview, tab_run, tab_ctx, tab_hooks, tab_motions, tab_constraints,
    tab_videos, tab_log, tab_eval,
) = st.tabs([
    "Agency overview", "Run agent", "Context", "Winning hooks",
    "Referral motions", "Negative constraints", "Generated videos",
    "Performance log", "Grade output",
])

TASK_TYPE_OPTIONS: dict[str, str | None] = {
    "Auto-detect from prompt": None,
    "Strategist  -  research competitors, extract hooks": "research",
    "Producer  -  generate UGC video via Kling": "produce",
    "Analyst  -  performance feedback loop": "analyze",
}


def _display_result(result: dict, model_id_used: str) -> None:
    """Render a graph result block (used by both fresh dispatch + post-HITL)."""
    if result.get("error"):
        st.error(result["error"])
        return
    st.success(
        f"Routed to: **{result.get('current_agent', 'unknown')}**  "
        f"(task: {result.get('task_type', 'unknown')}, model: `{model_id_used}`)"
    )
    for msg in result.get("messages", []):
        st.markdown(f"**{msg.__class__.__name__}**: {msg.content}")
    artifacts = result.get("artifacts") or {}
    videos = artifacts.get("producer_videos") or []
    if videos:
        st.divider()
        st.markdown("### Generated videos")
        for path in videos:
            p = Path(path)
            if p.exists():
                st.caption(p.name)
                st.video(str(p))
            else:
                st.warning(f"Video path returned but file not found: {p}")
    if artifacts:
        with st.expander("All artifacts (audit)"):
            st.json(artifacts)


# --------------------------------------------------------------------------- #
# V1.8 Agency Overview tab - the operator's single dashboard for "what is
# waiting across all clients/prospects". Backed by services/operator_inbox
# (read-only, no agent run, no API call).
# --------------------------------------------------------------------------- #


with tab_overview:
    from services import operator_inbox  # local import to avoid cycle at module load
    from services.operator_inbox import VALID_PRIORITIES

    st.markdown("### Agency overview")
    st.caption(
        "Read-only inbox inferred from `video_plans`, `video_jobs`, "
        "`mcp_pending_runs`, `prospects/`, and `evals/output_reviews.jsonl`. "
        "No agent runs from this tab. Use *Run agent* to dispatch."
    )

    # Filter widgets
    fcols = st.columns([1, 1, 1])
    with fcols[0]:
        priority_filter = st.selectbox(
            "Priority",
            options=["all", *VALID_PRIORITIES],
            index=0,
        )
    with fcols[1]:
        # Client filter populated from the actual client list + a sentinel
        # for prospect/system rows that have no client_id.
        all_clients = list_clients()
        client_filter = st.selectbox(
            "Client",
            options=["all", "(prospects + system)", *all_clients],
            index=0,
        )
    with fcols[2]:
        if st.button("Refresh", help="Re-scan SQL + filesystem"):
            st.rerun()

    all_tasks = operator_inbox.collect_operator_tasks()

    def _matches(task: operator_inbox.OperatorTask) -> bool:
        if priority_filter != "all" and task.priority != priority_filter:
            return False
        if client_filter == "(prospects + system)":
            return task.client_id is None
        if client_filter != "all" and task.client_id != client_filter:
            return False
        return True

    filtered = [t for t in all_tasks if _matches(t)]

    # Counts strip
    by_pri = operator_inbox.group_tasks_by_priority(filtered)
    cstrip = st.columns(4)
    cstrip[0].metric("Critical", len(by_pri.get("critical") or []))
    cstrip[1].metric("High", len(by_pri.get("high") or []))
    cstrip[2].metric("Medium", len(by_pri.get("medium") or []))
    cstrip[3].metric("Low", len(by_pri.get("low") or []))

    st.divider()

    # ----- V1.9: cross-channel approval guidance -------------------------- #
    # Operators kept asking "where do I approve plan VP-X?". This compact
    # panel correlates every pending Producer plan with its MCP pending
    # registry row (if any) so the right approval surface is obvious.
    # Read-only; no buttons (per scope guard G.4 - approve must stay on
    # the existing safe HITL surfaces).
    from services import mcp_pending_store as _mcp_store

    _mcp_pending_rows = _mcp_store.list_pending()
    _mcp_by_plan: dict[tuple[str | None, str], dict] = {
        (r.get("client_id"), r.get("plan_id")): r
        for r in _mcp_pending_rows
        if r.get("plan_id")
    }

    # Walk every onboarded client + collect pending_approval plans.
    _pending_plans: list[tuple[str, object]] = []  # (client_id, VideoPlan)
    for _cid in list_clients():
        try:
            _ctx = ClientContext.load(_cid)
        except Exception:
            continue
        for _plan in _ctx.list_video_plans(status=PlanStatus.PENDING_APPROVAL):
            _pending_plans.append((_cid, _plan))

    if _pending_plans:
        st.markdown("#### Pending approvals — where to act")
        st.caption(
            "Manager will only auto-approve via MCP when a matching pending "
            "row exists. Other plans must be approved on the originating "
            "channel; the table below tells you which."
        )
        for _cid, _plan in _pending_plans:
            _row = _mcp_by_plan.get((_cid, _plan.id))
            if _row is None:
                _channel = "streamlit"
                _hint = (
                    f"Approve in **Streamlit**: select client `{_cid}` in "
                    f"the sidebar, the HITL panel renders the compiled plan."
                )
            else:
                _ch = _row.get("source_channel") or "unknown"
                _channel = _ch
                _tid = _row.get("thread_id", "?")
                if _ch == "mcp":
                    _hint = (
                        f"Approve via **MCP**: "
                        f"`resume_agency_workflow(thread_id='{_tid}', approve=True)` "
                        f"or `manager_request('approve plan {_plan.id} for "
                        f"client {_cid}', confirm=True)`."
                    )
                elif _ch == "streamlit":
                    _hint = (
                        f"Approve via **Streamlit** HITL panel for client "
                        f"`{_cid}`. (MCP row exists but channel is "
                        f"streamlit; do not cross-approve via MCP.)"
                    )
                else:
                    _hint = (
                        f"Channel **unknown**. Resolve via "
                        f"`resume_agency_workflow(thread_id='{_tid}', "
                        f"approve=True)` if you originated this MCP turn, "
                        f"else use Streamlit."
                    )
            with st.container(border=True):
                _c = st.columns([2, 2, 5])
                _c[0].markdown(f"**{_plan.id}**")
                _c[1].markdown(f"client `{_cid}`")
                _c[2].markdown(f"channel: **{_channel}**")
                st.caption(_hint)
        st.divider()

    if not filtered:
        st.success("No open tasks for the current filter.")
    else:
        st.markdown(operator_inbox.summarize_operator_tasks(filtered))


with tab_run:
    # Show post-HITL outcome from a previous rerun, if any.
    if st.session_state.get("hitl_last_status"):
        status = st.session_state.pop("hitl_last_status")
        last_result = st.session_state.pop("hitl_last_result", None)
        if status == "approved" and last_result:
            st.success("Producer approved and completed.")
            _display_result(last_result, last_result.get("artifacts", {}).get("model_used", "unknown"))
            st.divider()
        elif status == "rejected":
            st.info("Producer run cancelled at HITL checkpoint - no Kling API calls made.")
            st.divider()
        elif status.startswith("error:"):
            st.error(f"Producer resume failed: {status[len('error:'):]}")
            st.divider()

    prompt = st.text_area(
        "Instruction for the Supervisor",
        placeholder="e.g. Produce a 10-second video using hook WH-003 with our default character and product.",
    )

    col1, col2 = st.columns(2)
    with col1:
        task_label = st.selectbox(
            "Agent",
            options=list(TASK_TYPE_OPTIONS.keys()),
            index=0,
            help="Pick the agent explicitly, or leave on auto-detect.",
        )
        task_type = TASK_TYPE_OPTIONS[task_label]
    with col2:
        model_label = st.selectbox(
            "Model",
            options=list(SUPPORTED_MODELS.keys()),
            index=0,
            help="Sonnet 4.6 is faster/cheaper; Opus 4.7 reasons deeper but costs ~3x.",
        )
        model_id = SUPPORTED_MODELS[model_label]

    if st.button("Dispatch", type="primary", disabled=not prompt.strip()):
        agent_label = task_label.split("  -  ")[0]
        thread_id = f"{selection}-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id, "model": model_id}}

        # V1.7 long-run UX: keep the operator informed during a 30-90s
        # graph step so they don't refresh and corrupt the dispatch.
        st.info(
            ":hourglass: **Dispatch in progress.** Strategist and Analyst "
            "runs typically take 30-90 seconds. Outreach can take longer "
            "(one Apify scrape per prospect). **Do not refresh** while the "
            "spinner is visible. Producer submissions are still gated by "
            "your approval on the next screen."
        )
        with st.spinner(
            f"Running {agent_label} with {model_id}..."
        ):
            result = asyncio.run(run_supervisor_async(
                graph,
                initial_state(selection, prompt, task_type=task_type),
                config=config,
            ))

        # Did interrupt_before pause us at producer_submit?
        pending_node = get_pending_node(graph, config=config)
        if pending_node == "producer_submit":
            st.session_state["hitl_pending"] = {
                "config": config,
                "prompt": prompt,
                "client_id": selection,
                "model": model_id,
                "plan_id": result.get("plan_id"),
                "supervisor_messages": result.get("messages", []),
            }
            st.rerun()
        else:
            _display_result(result, model_id)

with tab_ctx:
    st.markdown(body)

with tab_hooks:
    if not hooks:
        st.info("No winning hooks yet - the Strategist will populate these on first run.")
    else:
        for h in hooks:
            st.markdown(f"**{h.id}** - {h.pattern}  \n_{h.description}_")
            st.caption(
                f"days_active={h.days_active} - confidence={h.confidence.value} - "
                f"by {h.added_by.value} on {h.added_at}"
            )

with tab_motions:
    if not motions:
        st.info("No referral motions yet.")
    else:
        for m in motions:
            st.markdown(f"**{m.id}** - {m.description}")
            st.caption(
                f"pacing={m.pacing or '-'} - camera={m.camera_style or '-'} - "
                f"duration={m.duration_seconds or '?'}s - path={m.reference_path}"
            )

with tab_constraints:
    if not constraints:
        st.info("No negative constraints yet.")
    else:
        for c in constraints:
            st.markdown(f"**{c.id}** ({c.severity.value}) - {c.rule}")
            st.caption(f"Reason: {c.reason}")

def _poll_pending_kling_job(ctx: ClientContext, kling_task_id: str) -> tuple[str, str]:
    """Poll a single pending Kling task and reconcile its video_jobs row.

    V1.4: backed by SQL (no more JSON read-modify-write). All status writes
    go through ctx.update_video_job_by_task_id which is atomic per
    statement; concurrent polls from the LLM tool and the UI button no
    longer race.

    Returns (status, message) where status is
    'rendering' | 'failed' | 'completed' | 'error'.
    """
    try:
        kling_client = KlingClient()
        task = kling_client.poll_task(kling_task_id)
    except (KlingAPIError, Exception) as e:
        return "error", f"Poll failed: {type(e).__name__}: {e}"

    data = task.get("data") or task
    status = str(data.get("task_status") or data.get("status") or "pending").lower()

    if status in {"submitted", "processing", "pending", "queued", "running"}:
        return "rendering", f"Still rendering (Kling status: {status})."

    if status in {"failed", "error"}:
        err = data.get("error") or data.get("message") or "unknown failure"
        ctx.update_video_job_by_task_id(
            kling_task_id,
            status=JobStatus.FAILED,
            error=str(err),
            completed_at=datetime.now(timezone.utc),
        )
        return "failed", f"Kling reported failure: {err}"

    # Terminal success: mirror the Producer's filename convention. Pull
    # hook/motion off the parent video_plans row via the FK.
    job = ctx.get_video_job(kling_task_id)
    plan = ctx.get_video_plan(job.plan_id) if job else None
    hook_id = plan.hook_id if plan else "unknown"
    motion_id = plan.motion_id if plan else None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{hook_id}-{motion_id or 'images'}-{timestamp}.mp4"
    dest = ctx.root / "outputs" / "videos" / name

    try:
        kling_client.download_video(task, dest)
    except Exception as e:
        return "error", f"Download failed: {type(e).__name__}: {e}"

    rel_path = str(dest.relative_to(ctx.root))
    ctx.update_video_job_by_task_id(
        kling_task_id,
        status=JobStatus.COMPLETED,
        video_path=rel_path,
        completed_at=datetime.now(timezone.utc),
    )
    return "completed", f"Video downloaded to {rel_path}."


with tab_videos:
    # ----- V1.7: Failed jobs section (rendered first so failures are
    #             impossible to miss) -----
    failed_jobs = ctx.list_video_jobs(status=JobStatus.FAILED, limit=20)
    if failed_jobs:
        st.markdown(f"### :red[Failed Kling jobs ({len(failed_jobs)})]")
        st.caption(
            "Jobs Kling reported as failed. The error string is stored "
            "verbatim from the provider. To retry, dispatch a fresh "
            "Producer request - the existing failed row stays for audit."
        )
        for job in failed_jobs:
            plan = ctx.get_video_plan(job.plan_id)
            with st.container(border=True):
                st.markdown(f"**Task** `{job.kling_task_id}`")
                if plan:
                    st.caption(
                        f"plan={plan.id} - hook={plan.hook_id} - "
                        f"motion={plan.motion_id or '-'} - "
                        f"submitted {job.submitted_at.isoformat()}"
                    )
                st.error(job.error or "(no error text recorded)")
        st.divider()

    # ----- Pending Kling tasks: SQL-backed list + polling UI -----
    pending_jobs = ctx.list_video_jobs(status=JobStatus.PENDING)

    if pending_jobs:
        st.markdown("### Pending Kling tasks")
        st.caption(
            f"{len(pending_jobs)} task(s) submitted to Kling but not yet "
            f"reconciled. Click *Check Status* to poll. Completed renders "
            f"download to `outputs/videos/` and the row leaves this list."
        )
        for job in pending_jobs:
            plan = ctx.get_video_plan(job.plan_id)
            with st.container(border=True):
                cols = st.columns([3, 1])
                with cols[0]:
                    st.markdown(f"**Task** `{job.kling_task_id}`")
                    if plan:
                        st.caption(
                            f"plan={plan.id} - hook={plan.hook_id} - "
                            f"motion={plan.motion_id or '-'} - "
                            f"submitted {job.submitted_at.isoformat()}"
                        )
                    else:
                        st.caption(f"plan={job.plan_id} (not found) - submitted {job.submitted_at.isoformat()}")
                with cols[1]:
                    if st.button("Check Status", key=f"poll_{job.kling_task_id}"):
                        with st.spinner(f"Polling Kling for {job.kling_task_id}..."):
                            outcome, message = _poll_pending_kling_job(ctx, job.kling_task_id)
                        if outcome == "completed":
                            st.success(message)
                        elif outcome == "rendering":
                            st.info(message)
                        elif outcome == "failed":
                            st.error(message)
                        else:
                            st.warning(message)
                        st.rerun()
        st.divider()

    # ----- Existing on-disk MP4 listing (unchanged behavior). -----
    videos_dir = ctx.root / "outputs" / "videos"
    mp4_files = (
        sorted(videos_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if videos_dir.exists() else []
    )
    if not mp4_files:
        if not pending_jobs:
            st.info("No videos generated yet.")
    else:
        st.caption(f"{len(mp4_files)} video(s) saved to `{videos_dir}`")
        for mp4 in mp4_files:
            st.markdown(f"**{mp4.name}**")
            st.video(str(mp4))
            st.download_button(
                "Download MP4",
                data=mp4.read_bytes(),
                file_name=mp4.name,
                mime="video/mp4",
                key=f"dl_{mp4.name}",
            )
            st.divider()

with tab_log:
    # V1.4.1: stale-plan triage section. Plans in pending_approval (incl.
    # those reverted from a failed submit) and submitting (in flight, may
    # be stuck if MCP/UI crashed mid-submission) need operator action.
    # Manual SQL is not acceptable UX, hence this dedicated panel.
    unresolved = ctx.list_unresolved_video_plans()
    if unresolved:
        st.markdown(f"### Unresolved plans ({len(unresolved)})")
        st.caption(
            "Plans awaiting operator decision. *submitting* status means a "
            "submit attempt is in flight (or was, if MCP/UI crashed). Reject "
            "marks the plan rejected in SQL. To re-submit a *pending_approval* "
            "plan, dispatch a fresh Producer request from the Run agent tab - "
            "do NOT click Approve here, this panel cannot bypass the atomic "
            "claim or HITL gate."
        )
        for p in unresolved:
            with st.container(border=True):
                cols = st.columns([3, 1])
                with cols[0]:
                    badge = (
                        ":orange[PENDING_APPROVAL]"
                        if p.status.value == "pending_approval"
                        else ":red[SUBMITTING]"
                    )
                    st.markdown(f"**{p.id}** — {badge}")
                    st.caption(
                        f"hook={p.hook_id} - motion={p.motion_id or '-'} - "
                        f"created {p.created_at.isoformat()} - "
                        f"attempts={p.submit_attempts}"
                    )
                    st.caption(
                        f"character: `{p.character_asset}` | "
                        f"product: `{p.product_asset}`"
                    )
                    if p.submit_error:
                        st.error(f"Last submit_error: {p.submit_error}")
                with cols[1]:
                    if st.button("Reject stale plan", key=f"reject_stale_{p.id}"):
                        ok = ctx.reject_video_plan(p.id, decided_by="human")
                        if ok:
                            st.success(f"{p.id} marked rejected.")
                        else:
                            st.warning(
                                f"{p.id} could not be rejected (status may "
                                f"have changed under us). Refresh and retry."
                            )
                        st.rerun()
        st.divider()

    # Full audit trail
    plans = ctx.list_video_plans(limit=50)
    jobs = ctx.list_video_jobs(limit=50)
    if not plans and not jobs:
        st.info("No video plans or jobs yet.")
    else:
        st.markdown(f"### Video plans ({len(plans)})")
        st.json([p.model_dump(mode="json") for p in plans])
        st.markdown(f"### Video jobs ({len(jobs)})")
        st.json([j.model_dump(mode="json") for j in jobs])


# --------------------------------------------------------------------------- #
# V1.7 Manual evaluation tab.
# Single-form grading writes to evals/output_reviews.jsonl via the same
# helper the CLI uses (scripts/eval_review.py). Below the form we show
# the most recent rows so the operator has context.
# --------------------------------------------------------------------------- #


with tab_eval:
    st.markdown("### Grade an output")
    st.caption(
        "Manual quality grade for an artifact this client just produced. "
        "Writes one JSONL row to `evals/output_reviews.jsonl` (gitignored). "
        "Same data layout as `python scripts/eval_review.py`; either path "
        "works and they share storage."
    )

    with st.form("eval_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            agent_choice = st.selectbox("Agent", options=list(eval_review.VALID_AGENTS))
            output_type = st.text_input(
                "Output type",
                placeholder="pdf_report | pitch_pdf | kling_video | constraint | audit_json",
            )
            source = st.text_input(
                "Source path (optional)",
                placeholder=f"clients/{selection}/outputs/...",
            )
        with c2:
            specificity = st.slider("Specificity", 1, 5, 3)
            accuracy = st.slider("Accuracy", 1, 5, 3)
            usefulness = st.slider("Usefulness", 1, 5, 3)
            sendable = st.toggle("Sendable / usable to a real customer?", value=False)

        notes = st.text_area("Notes (free text)", height=80)
        submitted = st.form_submit_button("Save review", type="primary")

        if submitted:
            if not output_type.strip():
                st.error("output_type is required (e.g. `pdf_report`, `kling_video`).")
            else:
                review = eval_review.build_review(
                    agent=agent_choice,
                    output_type=output_type.strip(),
                    client_id=selection,
                    source=source.strip() or None,
                    specificity=specificity,
                    accuracy=accuracy,
                    usefulness=usefulness,
                    sendable=sendable,
                    notes=notes,
                )
                eval_review.append_review(review)
                st.success(
                    f"Review appended for agent={review.agent} "
                    f"output={review.output_type}."
                )

    st.divider()
    st.markdown("### Recent reviews (last 10)")
    eval_path = eval_review.DEFAULT_EVAL_PATH
    if not eval_path.exists():
        st.caption("No reviews yet. Use the form above after a real workflow run.")
    else:
        try:
            lines = eval_path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            st.error(f"Could not read {eval_path}: {e}")
            lines = []
        recent = [json.loads(line) for line in lines[-10:] if line.strip()]
        if not recent:
            st.caption("Eval file exists but is empty.")
        else:
            # Newest first.
            st.json(list(reversed(recent)))
