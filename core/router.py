"""Routing helpers for the Supervisor.

Phase 1: deterministic keyword routing. Phase 2+: swap in an LLM router that
returns the same string surface ('research' | 'produce' | 'analyze' | 'outreach').
"""
from __future__ import annotations

from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage


TaskType = Literal["research", "produce", "analyze", "outreach"]


_KEYWORDS: dict[TaskType, tuple[str, ...]] = {
    "research": (
        "research", "audit", "market", "competitor", "scrape",
        "ads library", "intelligence", "find hooks", "discover",
    ),
    "produce": (
        "produce", "create video", "generate video", "make a video",
        "render", "kling", "ad creative", "shoot",
    ),
    "analyze": (
        "analyze", "performance", "roas", "ctr", "insights",
        "feedback", "report results", "meta ads",
    ),
    "outreach": (
        "outreach", "lead generation", "lead gen", "prospect", "prospects",
        "pitch", "new client", "find brands", "client acquisition",
        "audit prospect",
    ),
}


def _latest_human_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            return content.lower()
    return ""


def route_task(messages: list[BaseMessage], default: TaskType = "research") -> TaskType:
    text = _latest_human_text(messages)
    if not text:
        return default
    scores = {task: sum(1 for kw in kws if kw in text) for task, kws in _KEYWORDS.items()}
    winner, score = max(scores.items(), key=lambda kv: kv[1])
    return winner if score > 0 else default
