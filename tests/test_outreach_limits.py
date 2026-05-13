"""V1.4 outreach hard cost limits.

Pins that the Apify and Tavily call counters trip BEFORE the underlying
paid client is invoked, regardless of LLM intent. These are Python-side
circuit breakers, not prompt instructions.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agents.outreach.agent import (
    MAX_APIFY_CALLS_PER_RUN,
    MAX_TAVILY_CALLS_PER_RUN,
    _make_capped_fb_ads_tool,
    _make_capped_tavily_tool,
)

# --------------------------------------------------------------------------- #
# Apify cap
# --------------------------------------------------------------------------- #


def test_apify_counter_trips_at_cap():
    """The (cap+1)-th call must return SAFETY LIMIT without invoking the
    underlying Apify tool."""
    fake_underlying = MagicMock()
    fake_underlying.invoke.return_value = []  # no ads, but valid shape

    with patch(
        "agents.outreach.agent.make_fb_ads_search_tool",
        return_value=fake_underlying,
    ):
        tool = _make_capped_fb_ads_tool()

        # First MAX_APIFY_CALLS_PER_RUN calls all hit the underlying tool.
        for _ in range(MAX_APIFY_CALLS_PER_RUN):
            result = tool.invoke({"competitor_pages": ["x"], "country": "BE"})
            assert not (isinstance(result, str) and result.startswith("SAFETY LIMIT"))

        # The (cap+1)-th call must short-circuit.
        result = tool.invoke({"competitor_pages": ["x"], "country": "BE"})
        assert isinstance(result, str)
        assert result.startswith("SAFETY LIMIT:")
        assert str(MAX_APIFY_CALLS_PER_RUN) in result
        assert fake_underlying.invoke.call_count == MAX_APIFY_CALLS_PER_RUN


def test_apify_failures_count_against_cap():
    """A bug-prone LLM that retries a failing Apify call must not bypass
    the cap by burning the budget on errors."""
    fake_underlying = MagicMock()
    fake_underlying.invoke.side_effect = RuntimeError("apify down")

    with patch(
        "agents.outreach.agent.make_fb_ads_search_tool",
        return_value=fake_underlying,
    ):
        tool = _make_capped_fb_ads_tool()
        for _ in range(MAX_APIFY_CALLS_PER_RUN):
            assert tool.invoke({"competitor_pages": ["x"]}).startswith("API ERROR:")
        # Cap reached; next call hits the SAFETY LIMIT, not the underlying.
        assert tool.invoke({"competitor_pages": ["x"]}).startswith("SAFETY LIMIT:")


def test_apify_counter_is_per_factory_call():
    """Each fresh outreach_node invocation builds a new tool with a fresh
    counter. Two factory builds must be independent."""
    fake_underlying = MagicMock()
    fake_underlying.invoke.return_value = []
    with patch("agents.outreach.agent.make_fb_ads_search_tool", return_value=fake_underlying):
        tool_a = _make_capped_fb_ads_tool()
        for _ in range(MAX_APIFY_CALLS_PER_RUN):
            tool_a.invoke({"competitor_pages": ["x"]})
        assert tool_a.invoke({"competitor_pages": ["x"]}).startswith("SAFETY LIMIT:")

        tool_b = _make_capped_fb_ads_tool()
        # Fresh counter - first call should NOT trip.
        assert not str(tool_b.invoke({"competitor_pages": ["y"]})).startswith("SAFETY LIMIT:")


# --------------------------------------------------------------------------- #
# Tavily cap
# --------------------------------------------------------------------------- #


def test_tavily_counter_trips_at_cap():
    fake_underlying = MagicMock()
    fake_underlying.invoke.return_value = []

    with patch(
        "agents.outreach.agent.make_tavily_competitor_search_tool",
        return_value=fake_underlying,
    ):
        tool = _make_capped_tavily_tool()
        for _ in range(MAX_TAVILY_CALLS_PER_RUN):
            result = tool.invoke({"brand": "acme", "country": "BE", "limit": 5})
            assert not (isinstance(result, str) and result.startswith("SAFETY LIMIT"))

        result = tool.invoke({"brand": "acme", "country": "BE", "limit": 5})
        assert isinstance(result, str)
        assert result.startswith("SAFETY LIMIT:")
        assert str(MAX_TAVILY_CALLS_PER_RUN) in result
        assert fake_underlying.invoke.call_count == MAX_TAVILY_CALLS_PER_RUN
