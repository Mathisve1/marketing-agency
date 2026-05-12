"""Strategist worker — researches competitors, extracts winning hooks, writes
findings to MASTER_CONTEXT.md, and generates a PDF report.

Architecture: a LangGraph create_react_agent wraps ChatAnthropic with three
tool families:
  1. Discovery — Tavily (competitor search) + Apify (FB Ads Library scrape).
  2. Analysis — longevity scorer (filters < 14-day ads as unproven).
  3. State mutation — wraps ClientContext methods so the LLM can persist hooks,
     referral motions, and narrative summaries straight into MASTER_CONTEXT.md.

The state mutators are built as closures over the loaded ClientContext so the
LLM never has to thread client_id through tool calls.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from agents.strategist.analysis.longevity_scorer import score_ads
from agents.strategist.reporting.pdf_builder import build_market_analysis_pdf
from agents.strategist.tools.apify_fb_ads import make_fb_ads_search_tool
from agents.strategist.tools.tavily_search import make_tavily_competitor_search_tool
from core.client_context import ClientContext
from core.context_schema import (
    AddedBy,
    Confidence,
    ReferralMotion,
    WinningHook,
)
from core.state import AgentState


SYSTEM_PROMPT = """You are the Strategist for an AI performance marketing agency.

Client: {client_name}  (locale: {client_locale})
Brand context: {brand_context}

The client already has {hooks_count} winning hooks and {constraints_count}
negative constraints in MASTER_CONTEXT.md. Call `read_existing_hooks` BEFORE
recording new ones so you never duplicate.

Your workflow this turn:
  1. If the user didn't name competitors, call `tavily_competitor_search` to
     identify 3-5 of them in {client_locale}.
  2. Call `search_fb_ads_library` for those competitors.
  3. Pass the raw ads through `score_ads_by_longevity` (min_days=14) — only
     ads that have run >= 14 days count as proven hooks.
  4. For each *pattern* (not each ad), call `record_winning_hook`. A pattern
     is a recurring angle like "price comparison shock", not a one-off line.
     For visually distinctive video ads, also call `record_referral_motion`.
  5. Call `generate_pdf_report` with a 2-3 sentence executive summary.
  6. Call `append_research_summary` with the same summary.
  7. Stop.

Be selective. Aim for 5-10 high-confidence hooks per run, not 30 mediocre ones.
Mark `confidence: high` only when the same pattern shows up across multiple
competitors AND has high longevity (60+ days)."""


def _build_context_writers(ctx: ClientContext):
    """Wraps ClientContext mutators as LangChain tools.

    Each closure captures `ctx` so the LLM doesn't need to know client_id.
    """

    @tool("record_winning_hook")
    def record_winning_hook(
        pattern: str,
        description: str,
        source_ad_id: str,
        days_active: int,
        confidence: str,
    ) -> str:
        """Persist a newly identified winning hook into MASTER_CONTEXT.md.
        `confidence` must be one of: 'high', 'medium', 'low'. Returns the
        auto-assigned hook ID (e.g. WH-007)."""
        hook = WinningHook(
            pattern=pattern,
            description=description,
            source_ad_id=source_ad_id,
            days_active=days_active,
            confidence=Confidence(confidence.lower()),
            added_by=AddedBy.STRATEGIST,
            added_at=datetime.now(timezone.utc),
        )
        return ctx.add_winning_hook(hook)

    @tool("record_referral_motion")
    def record_referral_motion(
        description: str,
        reference_path: str,
        pacing: str,
        camera_style: str,
        duration_seconds: int,
    ) -> str:
        """Persist a referral motion (the visual pattern of a winning video ad)
        into MASTER_CONTEXT.md. `reference_path` should be the relative path
        under references/referral_videos/ once the asset is downloaded."""
        motion = ReferralMotion(
            description=description,
            reference_path=reference_path,
            pacing=pacing,
            camera_style=camera_style,
            duration_seconds=duration_seconds,
            added_by=AddedBy.STRATEGIST,
            added_at=datetime.now(timezone.utc),
        )
        return ctx.add_referral_motion(motion)

    @tool("read_existing_hooks")
    def read_existing_hooks() -> dict:
        """Return existing winning_hooks and negative_constraints so we don't
        duplicate work or violate prior constraints."""
        fm, _ = ctx.read()
        return {
            "winning_hooks": [h.model_dump(mode="json") for h in fm.winning_hooks],
            "negative_constraints": [c.model_dump(mode="json") for c in fm.negative_constraints],
        }

    @tool("append_research_summary")
    def append_research_summary(summary: str) -> str:
        """Append a 2-4 sentence summary of this research run to the
        'Recent Strategic Notes' section of MASTER_CONTEXT.md."""
        ctx.append_narrative("Recent Strategic Notes", summary)
        return "OK"

    @tool("score_ads_by_longevity")
    def score_ads_by_longevity(ads: list[dict], min_days: int = 14) -> list[dict]:
        """Filter and rank ads by how long they have been running. Drops
        anything under min_days (default 14) — proven hooks only."""
        return score_ads(ads, min_days=min_days)

    @tool("generate_pdf_report")
    def generate_pdf_report(title: str, executive_summary: str) -> str:
        """Render the Market Analysis & Hook Strategy PDF into
        clients/<id>/outputs/reports/. Call this AFTER all winning hooks have
        been recorded. Returns the absolute path."""
        fm, _ = ctx.read()
        path = build_market_analysis_pdf(
            client_root=ctx.root,
            client_name=fm.client.name,
            title=title,
            executive_summary=executive_summary,
            winning_hooks=fm.winning_hooks,
            referral_motions=fm.referral_motions,
        )
        return str(path)

    return [
        record_winning_hook,
        record_referral_motion,
        read_existing_hooks,
        append_research_summary,
        score_ads_by_longevity,
        generate_pdf_report,
    ]


def _format_brand_context(fm) -> str:
    parts: list[str] = []
    if fm.brand.voice_attributes:
        parts.append(f"Voice: {', '.join(fm.brand.voice_attributes)}")
    if fm.brand.primary_products:
        parts.append(f"Products: {', '.join(fm.brand.primary_products)}")
    if fm.brand.forbidden_terms:
        parts.append(f"Forbidden: {', '.join(fm.brand.forbidden_terms)}")
    return " | ".join(parts) if parts else "(none recorded - run an initial brand audit)"


def strategist_node(state: AgentState) -> dict[str, Any]:
    ctx = ClientContext.load(state["client_id"])
    fm, _ = ctx.read()

    tools = [
        make_tavily_competitor_search_tool(),
        make_fb_ads_search_tool(),
        *_build_context_writers(ctx),
    ]

    llm = ChatAnthropic(
        model=os.getenv("STRATEGIST_MODEL", "claude-sonnet-4-6"),
        max_tokens=4096,
        temperature=0.2,
    )

    system_prompt = SYSTEM_PROMPT.format(
        client_name=fm.client.name,
        client_locale=fm.client.locale,
        brand_context=_format_brand_context(fm),
        hooks_count=len(fm.winning_hooks),
        constraints_count=len(fm.negative_constraints),
    )

    react = create_react_agent(llm, tools, prompt=system_prompt)
    result = react.invoke({"messages": state["messages"]})

    # Return only the messages produced this turn - the supervisor's
    # add_messages reducer would otherwise duplicate the original user input.
    new_messages = result["messages"][len(state["messages"]):]

    # Surface the generated PDF path (if any) into shared artifacts.
    artifacts = dict(state.get("artifacts") or {})
    for msg in reversed(new_messages):
        if hasattr(msg, "name") and msg.name == "generate_pdf_report":
            artifacts["strategist_report_pdf"] = msg.content
            break

    return {
        "messages": new_messages,
        "current_agent": "strategist",
        "artifacts": artifacts,
    }
