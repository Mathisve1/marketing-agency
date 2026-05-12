"""Producer worker — Phase 1 stub.

Phase 3 will port the krusemediallc/arcads-claude-code orchestration patterns,
call Kling 3.0 Omni V2V with a referral video + product image + character
image, and write outputs to clients/<id>/outputs/videos/.
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from core.client_context import ClientContext
from core.state import AgentState


def producer_node(state: AgentState) -> dict[str, Any]:
    ctx = ClientContext.load(state["client_id"])
    fm, _ = ctx.read()
    return {
        "messages": [AIMessage(content=(
            f"[Producer · stub] Loaded {fm.client.name}. "
            f"{len(fm.winning_hooks)} hooks and {len(fm.referral_motions)} "
            f"referral motions available. Phase 3 will compile a Kling 3.0 "
            f"V2V job brief using these plus product/character assets."
        ))],
    }
