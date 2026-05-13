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
import uuid
from pathlib import Path

import streamlit as st

from agents.outreach.prospect_store import promote_to_client
from core.client_context import ClientContext, list_clients
from core.models import SUPPORTED_MODELS
from core.supervisor import (
    build_supervisor_graph,
    get_pending_node,
    initial_state,
    resume_supervisor_async,
    run_supervisor_async,
)


st.set_page_config(page_title="Agency Command Center", layout="wide")
st.title("Agency Command Center")


# --------------------------------------------------------------------------- #
# Session-scoped graph cache. MemorySaver state for HITL checkpoints lives
# inside the compiled graph - caching the graph object keeps that state alive
# across Streamlit script reruns within the same session.
# --------------------------------------------------------------------------- #


if "supervisor_graph" not in st.session_state:
    st.session_state["supervisor_graph"] = build_supervisor_graph(
        interrupt_before=["producer"]
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
                st.success(f"Onboarded {new_id!r}. Refresh the page to select it.")
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
    """Returns True if a pending HITL was rendered (caller should st.stop)."""
    pending = st.session_state.get("hitl_pending")
    if not pending:
        return False

    st.warning("Producer approval required - paid Kling API workflow about to start")

    st.markdown("**Operator instruction:**")
    st.info(pending["prompt"])

    cols = st.columns(3)
    cols[0].metric("Client", pending["client_id"])
    cols[1].metric("Model", pending["model"])
    cols[2].metric("Routed to", pending["agent"])

    if pending.get("supervisor_messages"):
        with st.expander("Supervisor messages so far"):
            for msg in pending["supervisor_messages"]:
                st.markdown(f"**{msg.__class__.__name__}**: {msg.content}")

    st.caption(
        "Note: at this checkpoint the Producer LLM has NOT yet decided which "
        "hook / motion / assets to use - those tool calls happen after approval. "
        "Approve gates the entire Producer flow (read_master_context + "
        "generate_ugc_video tool calls + paid Kling submission)."
    )

    col_a, col_r = st.columns(2)
    with col_a:
        if st.button("Approve - run Producer", type="primary", key="hitl_approve"):
            with st.spinner("Producer running (submission is fast - renders happen async)..."):
                try:
                    result = asyncio.run(resume_supervisor_async(
                        graph, config=pending["config"]
                    ))
                    st.session_state["hitl_last_result"] = result
                    st.session_state["hitl_last_status"] = "approved"
                except Exception as e:
                    st.session_state["hitl_last_result"] = None
                    st.session_state["hitl_last_status"] = (
                        f"error:{type(e).__name__}: {e}"
                    )
            st.session_state["hitl_pending"] = None
            st.rerun()
    with col_r:
        if st.button("Reject - cancel", key="hitl_reject"):
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

        # Did interrupt_before pause us at the Producer?
        pending_node = get_pending_node(graph, config=config)
        if pending_node == "producer":
            st.session_state["hitl_pending"] = {
                "config": config,
                "prompt": prompt,
                "client_id": selection,
                "model": model_id,
                "agent": result.get("current_agent") or "producer",
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

with tab_videos:
    videos_dir = ctx.root / "outputs" / "videos"
    mp4_files = (
        sorted(videos_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if videos_dir.exists() else []
    )
    if not mp4_files:
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
    entries = ctx.read_performance_log()
    if not entries:
        st.info("No performance entries yet.")
    else:
        st.json(entries)
