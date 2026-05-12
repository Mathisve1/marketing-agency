"""Tavily competitor search tool — import-stable stub.

The full implementation lands in Phase 2 (2/2). Until then this module exposes
the expected symbol so `agents/strategist/agent.py` imports cleanly and the
LangGraph supervisor stays loadable.
"""
from __future__ import annotations

from langchain_core.tools import tool


def make_tavily_competitor_search_tool():
    """Factory matching the pattern of make_fb_ads_search_tool."""

    @tool("tavily_competitor_search")
    def tavily_competitor_search(brand: str, country: str = "BE", limit: int = 5) -> list[dict]:
        """Find the top `limit` competitor brands for `brand` in `country`.
        Returns each competitor's name, website, and a one-line positioning
        summary based on web search results."""
        raise NotImplementedError(
            "tavily_search not yet implemented — pending Phase 2 (2/2)."
        )

    return tavily_competitor_search
