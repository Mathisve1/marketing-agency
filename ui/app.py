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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from agents.outreach.prospect_store import promote_to_client
from agents.producer.kling.client import KlingAPIError, KlingClient
from core.client_context import ClientContext, list_clients
from core.context_schema import JobStatus
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
            with st.spinner(
                f"Running Outreach with {model_id} (Apify ad scraping is slow)..."
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
            # Service owns the SQL audit-trail bookkeeping consistently
            # across UI + MCP callers.
            reject_pending_plan(plan_ctx, plan_id, decided_by="human")
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

tab_run, tab_ctx, tab_hooks, tab_motions, tab_constraints, tab_videos, tab_log = st.tabs([
    "Run agent", "Context", "Winning hooks", "Referral motions",
    "Negative constraints", "Generated videos", "Performance log",
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

        with st.spinner(
            f"Running {agent_label} with {model_id} "
            f"(Producer submits are fast; Strategist/Analyst can take ~30s)..."
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
