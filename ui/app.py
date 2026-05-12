"""Streamlit Command Center — Phase 1 shell.

Run with:  streamlit run ui/app.py
"""
from __future__ import annotations

import streamlit as st

from core.client_context import ClientContext, list_clients
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
    f"{len(fm.winning_hooks)} hooks · {len(fm.negative_constraints)} constraints"
)

tab_run, tab_ctx, tab_hooks, tab_constraints, tab_log = st.tabs(
    ["Run agent", "Context", "Winning hooks", "Negative constraints", "Performance log"]
)

with tab_run:
    prompt = st.text_area(
        "Instruction for the Supervisor",
        placeholder="e.g. Research Colruyt's top-performing Facebook competitors and extract winning hooks.",
    )
    if st.button("Dispatch", type="primary", disabled=not prompt.strip()):
        graph = build_supervisor_graph()
        result = graph.invoke(
            initial_state(selection, prompt),
            config={"configurable": {"thread_id": f"{selection}-ui"}},
        )
        if result.get("error"):
            st.error(result["error"])
        else:
            st.success(f"Routed to: **{result['current_agent']}**  (task: {result['task_type']})")
            for msg in result["messages"]:
                st.markdown(f"**{msg.__class__.__name__}**: {msg.content}")

with tab_ctx:
    st.markdown(body)

with tab_hooks:
    if not fm.winning_hooks:
        st.info("No winning hooks yet — the Strategist will populate these on first run.")
    else:
        for h in fm.winning_hooks:
            st.markdown(f"**{h.id}** · {h.pattern}  \n_{h.description}_")
            st.caption(
                f"days_active={h.days_active} · confidence={h.confidence.value} · "
                f"by {h.added_by.value} on {h.added_at}"
            )

with tab_constraints:
    if not fm.negative_constraints:
        st.info("No negative constraints yet — the Analyst writes these from live performance data.")
    else:
        for c in fm.negative_constraints:
            st.markdown(f"**{c.id}** ({c.severity.value}) · {c.rule}")
            st.caption(f"Reason: {c.reason}")

with tab_log:
    entries = ctx.read_performance_log()
    if not entries:
        st.info("No performance entries logged yet.")
    else:
        st.json(entries)
