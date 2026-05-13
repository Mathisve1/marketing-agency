"""LangChain tool: discover top competitors via Tavily web search.

Returns the top `limit` search results for "competitors of {brand} in {country}".
The Strategist's react-agent LLM extracts competitor brand names from the
titles and snippets in its next reasoning step.
"""
from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from tavily import TavilyClient

from services.api_errors import format_api_error


class TavilySearchInput(BaseModel):
    brand: str = Field(..., description="Client brand name to find competitors of.")
    country: str = Field(
        "BE",
        description="ISO 3166-1 alpha-2 country code for geographic scoping.",
        pattern=r"^[A-Z]{2}$",
    )
    limit: int = Field(5, description="Number of search results to return.", ge=1, le=10)


def make_tavily_competitor_search_tool():
    """Factory matching the pattern of make_fb_ads_search_tool."""

    @tool("tavily_competitor_search", args_schema=TavilySearchInput)
    def tavily_competitor_search(brand: str, country: str = "BE", limit: int = 5):
        """Search the web for top competitors of `brand` in `country`. Returns a
        list of search results (title, url, snippet, score) on success, or a
        string starting with "API ERROR:" if Tavily failed.

        Use this when the user did not name competitors explicitly. Extract
        competitor brand names from the titles/snippets, then pass them to
        search_fb_ads_library."""
        # V1.3 polish (Initiative 2): match the analyst + outreach hardening.
        # Tavily HTTP timeouts, rate-limits, or malformed responses raise from
        # the SDK and would otherwise crash the LangGraph node. Returning the
        # error as a string lets the LLM choose to retry, switch tactics, or
        # report a clean failure to the operator.
        try:
            api_key = os.getenv("TAVILY_API_KEY")
            if not api_key:
                raise RuntimeError("TAVILY_API_KEY is not set. Add it to .env.")

            client = TavilyClient(api_key=api_key)
            query = f"top competitors of {brand} consumer brand in {country}"
            response: dict[str, Any] = client.search(
                query=query,
                search_depth="advanced",
                max_results=limit * 2,  # over-fetch then trim, accounts for low-quality hits
            )
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": (r.get("content", "") or "")[:500],  # cap to keep LLM context tight
                    "score": r.get("score"),
                }
                for r in (response.get("results") or [])[:limit]
            ]
        except Exception as e:
            # V1.7: classified error format - operator can grep on the
            # bracketed token (RATE_LIMIT, MISSING_KEY, TIMEOUT, ...)
            # without reading English.
            return format_api_error("tavily", e)

    return tavily_competitor_search
