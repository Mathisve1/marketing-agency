"""Outreach worker - Phase 5 (1/3) import-stable stub.

Routes through the LangGraph supervisor's new 'outreach' branch but doesn't
yet do anything substantive. Full implementation (Tavily competitor
discovery -> Apify ad audit -> FPDF2 pitch builder -> ProspectStore writes)
lands in Phase 5 (3/3) once pitch_builder.py and prospect_store.py are
approved and written.

The Outreach worker is the first silo-less agent: state['client_id'] is
None and no ClientContext is loaded. It writes to prospects/<slug>/ via
the (forthcoming) ProspectStore instead of MASTER_CONTEXT.md.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from core.state import AgentState


def outreach_node(
    state: AgentState,
    config: Optional[RunnableConfig] = None,
) -> dict[str, Any]:
    return {
        "messages": [AIMessage(content=(
            "[Outreach . stub] Routing infrastructure online. The supervisor "
            "successfully bypassed client-load pre-flight (client_id is None). "
            "Full agent (Tavily competitor discovery -> Apify ad audit -> "
            "FPDF2 pitch -> ProspectStore writes) lands in Phase 5 (3/3)."
        ))],
        "current_agent": "outreach",
    }
