"""Tests for provider instrumentation (Pass 2.2 Initiative 4).

Pins the exact-one-event-on-success / exact-zero-on-failure contract for
each of the four instrumented provider boundaries:

  - Kling submit (agents/producer/agent.py producer_submit_node)
  - Tavily search (agents/strategist/tools/tavily_search.py)
  - Apify FB ads scrape (agents/strategist/tools/apify_fb_ads.py)
  - Meta insights fetch (agents/analyst/meta_insights.py)

We monkeypatch BOTH:
  - the provider boundary (so the test never hits a real API)
  - cost_ledger.record_event (so we can assert call shape + count)

The cost ledger DB is NEVER opened by these tests - record_event is
replaced by a MagicMock entirely.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.client_context import ClientContext
from core.context_schema import AddedBy, Confidence, WinningHook

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures (mirror tests/test_producer_split.py)
# --------------------------------------------------------------------------- #


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    src = REPO_ROOT / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


@pytest.fixture
def ctx_with_assets(clients_root: Path) -> ClientContext:
    ctx = ClientContext.onboard("acme", "Acme", clients_root=clients_root)
    ctx.add_winning_hook(WinningHook(
        pattern="Price comparison",
        description="Side-by-side cart total",
        days_active=42,
        confidence=Confidence.HIGH,
        added_by=AddedBy.STRATEGIST,
        added_at=datetime.now(timezone.utc),
    ))
    (ctx.root / "references" / "characters" / "c.png").write_bytes(b"\x89PNG fake")
    (ctx.root / "references" / "products" / "p.png").write_bytes(b"\x89PNG fake")
    return ctx


def _state_for_submit(client_id: str, plan_id: str | None) -> dict:
    return {
        "messages": [],
        "client_id": client_id,
        "task_type": "produce",
        "current_agent": "producer_submit",
        "artifacts": {},
        "error": None,
        "plan_id": plan_id,
    }


def _compile_plan(ctx: ClientContext) -> str:
    """Compile a plan via the planner tool so a real pending_approval row
    exists - same approach as test_producer_split.py."""
    from agents.producer.agent import _build_planner_tools
    fake_kling = MagicMock()
    tools = _build_planner_tools(ctx, fake_kling)
    compile_tool = next(t for t in tools if t.name == "compile_video_plan")
    return compile_tool.invoke({
        "hook_id": "WH-001",
        "character_asset": "references/characters/c.png",
        "product_asset": "references/products/p.png",
    }).split("plan_id=")[1].split()[0]


# --------------------------------------------------------------------------- #
# Kling submit instrumentation
# --------------------------------------------------------------------------- #


def test_producer_submit_records_one_kling_event_on_success(
    ctx_with_assets: ClientContext,
):
    """Happy path: Kling submit returns a task_id, instrumentation records
    exactly one 'kling/video_submit' event with the task_id in metadata."""
    from agents.producer.agent import producer_submit_node

    plan_id = _compile_plan(ctx_with_assets)

    fake_kling = MagicMock()
    fake_kling.submit_omni_video.return_value = "kling-task-PROBE"
    fake_record = MagicMock()

    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets), \
         patch("agents.producer.agent.cost_ledger.record_event", fake_record):
        out = producer_submit_node(_state_for_submit("acme", plan_id), config={})

    fake_kling.submit_omni_video.assert_called_once()
    fake_record.assert_called_once()
    kwargs = fake_record.call_args.kwargs
    assert kwargs["provider"] == "kling"
    assert kwargs["event_type"] == "video_submit"
    assert kwargs["client_id"] == "acme"
    assert kwargs["task_type"] == "produce"
    assert kwargs["metadata"]["kling_task_id"] == "kling-task-PROBE"
    assert kwargs["metadata"]["plan_id"] == plan_id
    # Sanity: the run actually succeeded.
    assert "submitted to Kling" in out["messages"][0].content


def test_producer_submit_records_zero_kling_events_on_submit_exception(
    ctx_with_assets: ClientContext,
):
    """submit_omni_video raises -> no record_event call. The except branch
    reverts the plan and returns ERROR; instrumentation must be skipped."""
    from agents.producer.agent import producer_submit_node

    plan_id = _compile_plan(ctx_with_assets)

    fake_kling = MagicMock()
    fake_kling.submit_omni_video.side_effect = RuntimeError("Kling 500")
    fake_record = MagicMock()

    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets), \
         patch("agents.producer.agent.cost_ledger.record_event", fake_record):
        out = producer_submit_node(_state_for_submit("acme", plan_id), config={})

    fake_record.assert_not_called()
    assert "ERROR" in out["messages"][0].content


def test_producer_submit_records_zero_kling_events_when_claim_fails(
    ctx_with_assets: ClientContext,
):
    """The atomic-claim refusal path returns REFUSED without calling Kling.
    Instrumentation must be skipped because no money was spent."""
    from agents.producer.agent import producer_submit_node

    plan_id = _compile_plan(ctx_with_assets)
    # Pre-reject the plan so claim_plan_for_submission returns False
    # (rejected -> not claimable).
    ctx_with_assets.reject_video_plan(plan_id)

    fake_kling = MagicMock()
    fake_record = MagicMock()

    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets), \
         patch("agents.producer.agent.cost_ledger.record_event", fake_record):
        out = producer_submit_node(_state_for_submit("acme", plan_id), config={})

    fake_kling.submit_omni_video.assert_not_called()
    fake_record.assert_not_called()
    assert "REFUSED" in out["messages"][0].content


def test_producer_submit_records_zero_kling_events_when_plan_id_missing(
    ctx_with_assets: ClientContext,
):
    """No plan_id in state -> early ERROR return -> no record_event call."""
    from agents.producer.agent import producer_submit_node

    fake_kling = MagicMock()
    fake_record = MagicMock()

    with patch("agents.producer.agent.KlingClient", return_value=fake_kling), \
         patch("agents.producer.agent.ClientContext.load", return_value=ctx_with_assets), \
         patch("agents.producer.agent.cost_ledger.record_event", fake_record):
        out = producer_submit_node(_state_for_submit("acme", plan_id=None), config={})

    fake_kling.submit_omni_video.assert_not_called()
    fake_record.assert_not_called()
    assert "ERROR" in out["messages"][0].content


# --------------------------------------------------------------------------- #
# Tavily instrumentation
# --------------------------------------------------------------------------- #


def test_tavily_tool_records_one_event_on_success(monkeypatch):
    """TavilyClient.search returns results -> exactly one
    'tavily/search' event recorded with brand/country/limit metadata."""
    monkeypatch.setenv("TAVILY_API_KEY", "stub-tavily")
    from agents.strategist.tools.tavily_search import make_tavily_competitor_search_tool

    fake_client = MagicMock()
    fake_client.search.return_value = {
        "results": [
            {"title": "t1", "url": "u1", "content": "c1", "score": 0.9},
            {"title": "t2", "url": "u2", "content": "c2", "score": 0.8},
        ],
    }
    fake_record = MagicMock()

    with patch("agents.strategist.tools.tavily_search.TavilyClient", return_value=fake_client), \
         patch("agents.strategist.tools.tavily_search.cost_ledger.record_event", fake_record):
        tool = make_tavily_competitor_search_tool()
        out = tool.invoke({"brand": "Acme", "country": "BE", "limit": 2})

    fake_client.search.assert_called_once()
    fake_record.assert_called_once()
    kwargs = fake_record.call_args.kwargs
    assert kwargs["provider"] == "tavily"
    assert kwargs["event_type"] == "search"
    assert kwargs["units"] == 2.0
    assert kwargs["metadata"]["brand"] == "Acme"
    assert kwargs["metadata"]["country"] == "BE"
    assert isinstance(out, list)
    assert len(out) == 2


def test_tavily_tool_records_zero_events_on_api_error(monkeypatch):
    """TavilyClient.search raises -> the except returns format_api_error
    string -> no record_event call."""
    monkeypatch.setenv("TAVILY_API_KEY", "stub-tavily")
    from agents.strategist.tools.tavily_search import make_tavily_competitor_search_tool

    fake_client = MagicMock()
    fake_client.search.side_effect = RuntimeError("Tavily 503")
    fake_record = MagicMock()

    with patch("agents.strategist.tools.tavily_search.TavilyClient", return_value=fake_client), \
         patch("agents.strategist.tools.tavily_search.cost_ledger.record_event", fake_record):
        tool = make_tavily_competitor_search_tool()
        out = tool.invoke({"brand": "Acme", "country": "BE", "limit": 2})

    fake_record.assert_not_called()
    # Sanity: the function returned the classified error string.
    assert isinstance(out, str)
    assert "API ERROR" in out


# --------------------------------------------------------------------------- #
# Apify instrumentation
# --------------------------------------------------------------------------- #


def test_apify_tool_records_one_event_on_success(monkeypatch):
    """Apify actor call returns + dataset materialises -> exactly one
    'apify/ads_library_scrape' event with actor_id + country metadata."""
    monkeypatch.setenv("APIFY_API_TOKEN", "stub-apify")
    from agents.strategist.tools.apify_fb_ads import make_fb_ads_search_tool

    fake_client = MagicMock()
    fake_client.actor.return_value.call.return_value = {"defaultDatasetId": "ds-abc"}
    fake_client.dataset.return_value.iterate_items.return_value = iter([
        {"ad_archive_id": "AD1", "page_name": "Acme"},
        {"ad_archive_id": "AD2", "page_name": "Acme"},
        {"ad_archive_id": "AD3", "page_name": "Acme"},
    ])
    fake_record = MagicMock()

    with patch("agents.strategist.tools.apify_fb_ads.ApifyClient", return_value=fake_client), \
         patch("agents.strategist.tools.apify_fb_ads.cost_ledger.record_event", fake_record):
        tool = make_fb_ads_search_tool(actor_id="probe/actor")
        out = tool.invoke({
            "competitor_pages": ["Acme"],
            "country": "BE",
            "max_ads_per_page": 50,
            "active_only": True,
        })

    fake_record.assert_called_once()
    kwargs = fake_record.call_args.kwargs
    assert kwargs["provider"] == "apify"
    assert kwargs["event_type"] == "ads_library_scrape"
    assert kwargs["units"] == 3.0
    assert kwargs["metadata"]["actor_id"] == "probe/actor"
    assert kwargs["metadata"]["country"] == "BE"
    assert kwargs["metadata"]["competitor_pages_n"] == 1
    assert len(out) == 3


def test_apify_tool_records_zero_events_when_no_dataset(monkeypatch):
    """Actor call returns {} (no defaultDatasetId) -> RuntimeError raised
    before instrumentation point. No record_event call."""
    monkeypatch.setenv("APIFY_API_TOKEN", "stub-apify")
    from agents.strategist.tools.apify_fb_ads import make_fb_ads_search_tool

    fake_client = MagicMock()
    fake_client.actor.return_value.call.return_value = {}  # no defaultDatasetId
    fake_record = MagicMock()

    with patch("agents.strategist.tools.apify_fb_ads.ApifyClient", return_value=fake_client), \
         patch("agents.strategist.tools.apify_fb_ads.cost_ledger.record_event", fake_record):
        tool = make_fb_ads_search_tool(actor_id="probe/actor")
        with pytest.raises(RuntimeError, match="returned no dataset"):
            tool.invoke({
                "competitor_pages": ["Acme"],
                "country": "BE",
                "max_ads_per_page": 50,
                "active_only": True,
            })

    fake_record.assert_not_called()


# --------------------------------------------------------------------------- #
# Meta insights instrumentation
# --------------------------------------------------------------------------- #


def test_meta_insights_records_one_event_after_loop_success(monkeypatch):
    """Graph API returns one paginated page -> exactly one 'meta/insights_fetch'
    event recorded with time_preset metadata and rows count."""
    monkeypatch.setenv("META_ACCESS_TOKEN", "stub-meta-token")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_stub")

    from agents.analyst import meta_insights

    fake_resp = MagicMock()
    fake_resp.ok = True
    fake_resp.json.return_value = {
        "data": [
            {"ad_id": "1", "ad_name": "Hook_WH-001_RM-002", "spend": "10.0",
             "impressions": "1000", "inline_link_clicks": "20", "purchase_roas": []},
            {"ad_id": "2", "ad_name": "Hook_WH-003_RM-004", "spend": "5.0",
             "impressions": "500", "inline_link_clicks": "10", "purchase_roas": []},
        ],
        "paging": {},  # no next page
    }
    fake_record = MagicMock()

    with patch("agents.analyst.meta_insights.requests.get", return_value=fake_resp), \
         patch("agents.analyst.meta_insights.cost_ledger.record_event", fake_record):
        rows = meta_insights.fetch_meta_insights_data(time_preset="last_7d")

    fake_record.assert_called_once()
    kwargs = fake_record.call_args.kwargs
    assert kwargs["provider"] == "meta"
    assert kwargs["event_type"] == "insights_fetch"
    assert kwargs["units"] == 2.0
    assert kwargs["metadata"]["time_preset"] == "last_7d"
    assert kwargs["metadata"]["rows_returned"] == 2
    # ad_account_id is sensitive and intentionally NOT in metadata.
    assert "ad_account_id" not in kwargs["metadata"]
    assert "act_stub" not in str(kwargs["metadata"])
    assert len(rows) == 2


def test_meta_insights_records_zero_events_on_graph_api_error(monkeypatch):
    """Graph API returns 5xx -> RuntimeError raised before instrumentation
    point. No record_event call."""
    monkeypatch.setenv("META_ACCESS_TOKEN", "stub-meta-token")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_stub")

    from agents.analyst import meta_insights

    fake_resp = MagicMock()
    fake_resp.ok = False
    fake_resp.status_code = 500
    fake_resp.text = "Internal Server Error"
    fake_record = MagicMock()

    with patch("agents.analyst.meta_insights.requests.get", return_value=fake_resp), \
         patch("agents.analyst.meta_insights.cost_ledger.record_event", fake_record):
        with pytest.raises(RuntimeError, match="Meta API"):
            meta_insights.fetch_meta_insights_data(time_preset="last_7d")

    fake_record.assert_not_called()
