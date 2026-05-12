"""Streamlit Command Center."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.client_context import ClientContext, list_clients
from core.models import SUPPORTED_MODELS
from core.supervisor import build_supervisor_graph, initial_state


st.set_page_config(page_title="Agency Command Center", layout="wide")
st.title("Agency Command Center")

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

st.subheader(f"Client: {fm.client.name}")
st.caption(
    f"Locale: {fm.client.locale} · Updated: {fm.client.last_updated} · "
    f"{len(fm.winning_hooks)} hooks · {len(fm.referral_motions)} motions · "
    f"{len(fm.negative_constraints)} constraints"
)

tab_run, tab_ctx, tab_hooks, tab_motions, tab_constraints, tab_videos, tab_log = st.tabs([
    "Run agent", "Context", "Winning hooks", "Referral motions",
    "Negative constraints", "Generated videos", "Performance log",
])


# Friendly label -> task_type passed into AgentState. None = auto-route.
TASK_TYPE_OPTIONS: dict[str, str | None] = {
    "Auto-detect from prompt": None,
    "Strategist  -  research competitors, extract hooks": "research",
    "Producer  -  generate UGC video via Kling": "produce",
    "Analyst  -  performance feedback loop": "analyze",
}


with tab_run:
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
            help="Pick the agent explicitly, or leave on auto-detect to let the supervisor's keyword router decide.",
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
        graph = build_supervisor_graph()
        agent_label = task_label.split("  -  ")[0]
        with st.spinner(
            f"Running {agent_label} with {model_id} "
            f"(Producer runs may take 1-5 minutes per video)..."
        ):
            result = graph.invoke(
                initial_state(selection, prompt, task_type=task_type),
                config={"configurable": {
                    "thread_id": f"{selection}-ui",
                    "model": model_id,
                }},
            )

        if result.get("error"):
            st.error(result["error"])
        else:
            st.success(
                f"Routed to: **{result['current_agent']}**  "
                f"(task: {result['task_type']}, model: `{model_id}`)"
            )
            for msg in result["messages"]:
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


with tab_ctx:
    st.markdown(body)

with tab_hooks:
    if not fm.winning_hooks:
        st.info("No winning hooks yet - the Strategist will populate these on first run.")
    else:
        for h in fm.winning_hooks:
            st.markdown(f"**{h.id}** · {h.pattern}  \n_{h.description}_")
            st.caption(
                f"days_active={h.days_active} · confidence={h.confidence.value} · "
                f"by {h.added_by.value} on {h.added_at}"
            )

with tab_motions:
    if not fm.referral_motions:
        st.info("No referral motions yet - the Strategist records these from V2V-suitable competitor ads.")
    else:
        for m in fm.referral_motions:
            st.markdown(f"**{m.id}** · {m.description}")
            st.caption(
                f"pacing={m.pacing or '-'} · camera={m.camera_style or '-'} · "
                f"duration={m.duration_seconds or '?'}s · path={m.reference_path}"
            )

with tab_constraints:
    if not fm.negative_constraints:
        st.info("No negative constraints yet - the Analyst writes these from live performance data.")
    else:
        for c in fm.negative_constraints:
            st.markdown(f"**{c.id}** ({c.severity.value}) · {c.rule}")
            st.caption(f"Reason: {c.reason}")

with tab_videos:
    videos_dir = ctx.root / "outputs" / "videos"
    mp4_files = (
        sorted(videos_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if videos_dir.exists() else []
    )
    if not mp4_files:
        st.info("No videos generated yet. Dispatch a Producer run to create one.")
    else:
        st.caption(f"{len(mp4_files)} video(s) saved to `{videos_dir}` (most recent first)")
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
