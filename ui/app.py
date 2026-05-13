"""Streamlit Command Center."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from core.client_context import ClientContext, list_clients
from core.context_schema import AddedBy, Confidence, ReferralMotion, WinningHook
from core.models import SUPPORTED_MODELS
from core.supervisor import build_supervisor_graph, initial_state


st.set_page_config(page_title="Agency Command Center", layout="wide")
st.title("Agency Command Center")


# --------------------------------------------------------------------------- #
# Auto-promote helper - reads a prospect's audit.json and seeds the new
# client's MASTER_CONTEXT.md with the identified hooks + motions.
# Will migrate to agents/outreach/prospect_store.py in Phase 5 (2/3).
# --------------------------------------------------------------------------- #


def _seed_from_audit_data(ctx: ClientContext, audit_data: dict) -> tuple[int, int]:
    """Returns (hooks_seeded, motions_seeded)."""
    now = datetime.now(timezone.utc)
    hooks_seeded = 0
    motions_seeded = 0

    for raw in audit_data.get("winning_hooks", []):
        try:
            hook = WinningHook(
                pattern=raw["pattern"],
                description=raw["description"],
                source_ad_id=raw.get("source_ad_id"),
                days_active=raw.get("days_active"),
                confidence=Confidence(str(raw.get("confidence", "medium")).lower()),
                added_by=AddedBy.STRATEGIST,
                added_at=now,
            )
            ctx.add_winning_hook(hook)
            hooks_seeded += 1
        except (KeyError, ValueError):
            continue  # Skip malformed entries; surface the count to the user.

    for raw in audit_data.get("referral_motions", []):
        try:
            motion = ReferralMotion(
                description=raw["description"],
                reference_path=raw["reference_path"],
                pacing=raw.get("pacing"),
                camera_style=raw.get("camera_style"),
                duration_seconds=raw.get("duration_seconds"),
                added_by=AddedBy.STRATEGIST,
                added_at=now,
            )
            ctx.add_referral_motion(motion)
            motions_seeded += 1
        except (KeyError, ValueError):
            continue

    return hooks_seeded, motions_seeded


# --------------------------------------------------------------------------- #
# Mode selector - Client work (silo'd) vs Lead generation (global outreach).
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
        "the `prospects/` folder for cold outreach."
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
            graph = build_supervisor_graph()
            with st.spinner(
                f"Running Outreach with {model_id} (Apify ad scraping is slow)..."
            ):
                result = graph.invoke(
                    initial_state(
                        client_id=None,
                        user_message=prompt,
                        task_type="outreach",
                    ),
                    config={"configurable": {
                        "thread_id": "outreach-ui",
                        "model": model_id,
                    }},
                )

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
                                new_ctx = ClientContext.onboard(
                                    new_id, new_name, locale=new_locale,
                                )
                                h, m = _seed_from_audit_data(new_ctx, audit_data)
                                st.success(
                                    f"Promoted `{folder.name}` -> client `{new_id}`. "
                                    f"Seeded {h} hook(s) and {m} motion(s) into "
                                    f"MASTER_CONTEXT.md. Switch to Client work mode "
                                    f"and select `{new_id}` to continue."
                                )
                            except FileExistsError:
                                st.error(
                                    f"Client `{new_id}` already exists. Pick a different ID."
                                )
                            except (ValueError, FileNotFoundError) as e:
                                st.error(f"Promotion failed: {e}")

    st.stop()


# --------------------------------------------------------------------------- #
# CLIENT WORK MODE (existing UI)
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

st.subheader(f"Client: {fm.client.name}")
st.caption(
    f"Locale: {fm.client.locale} - Updated: {fm.client.last_updated} - "
    f"{len(fm.winning_hooks)} hooks - {len(fm.referral_motions)} motions - "
    f"{len(fm.negative_constraints)} constraints"
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
            st.markdown(f"**{h.id}** - {h.pattern}  \n_{h.description}_")
            st.caption(
                f"days_active={h.days_active} - confidence={h.confidence.value} - "
                f"by {h.added_by.value} on {h.added_at}"
            )

with tab_motions:
    if not fm.referral_motions:
        st.info("No referral motions yet.")
    else:
        for m in fm.referral_motions:
            st.markdown(f"**{m.id}** - {m.description}")
            st.caption(
                f"pacing={m.pacing or '-'} - camera={m.camera_style or '-'} - "
                f"duration={m.duration_seconds or '?'}s - path={m.reference_path}"
            )

with tab_constraints:
    if not fm.negative_constraints:
        st.info("No negative constraints yet.")
    else:
        for c in fm.negative_constraints:
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
