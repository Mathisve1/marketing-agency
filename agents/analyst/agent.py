"""Analyst worker — feedback loop. Phase 4 will pull Meta Ads Insights API
and write `negative_constraints` into MASTER_CONTEXT.md when a hook
underperforms a configurable ROAS / CTR threshold.
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from core.client_context import ClientContext
from core.state import AgentState


def analyst_node(state: AgentState) -> dict[str, Any]:
    ctx = ClientContext.load(state["client_id"])
    fm, _ = ctx.read()
    log_entries = len(ctx.read_performance_log())
    return {
        "messages": [AIMessage(content=(
            f"[Analyst · stub] {fm.client.name}: {log_entries} historical "
            f"performance entries, {len(fm.negative_constraints)} active "
            f"negative constraints. Phase 4 will pull Meta Insights and "
            f"append constraints when ROAS falls below "
            f"{fm.performance_benchmarks.roas_target}."
        ))],
    }
