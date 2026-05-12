"""Strategist worker — Phase 1 stub.

Phase 2 will wire Tavily + Perplexity for brand audit, Apify FB Ads scraper for
competitor ad extraction, longevity scoring, and FPDF2 report generation. It
will write `winning_hooks` and `referral_motions` into MASTER_CONTEXT.md.
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from core.client_context import ClientContext
from core.state import AgentState


def strategist_node(state: AgentState) -> dict[str, Any]:
    ctx = ClientContext.load(state["client_id"])
    fm, _ = ctx.read()
    return {
        "messages": [AIMessage(content=(
            f"[Strategist · stub] Loaded {fm.client.name} ({fm.client.id}). "
            f"Phase 2 will run Tavily + Apify, score by ad longevity, write "
            f"winning_hooks/referral_motions into MASTER_CONTEXT.md, and emit "
            f"a PDF report under outputs/reports/."
        ))],
    }
