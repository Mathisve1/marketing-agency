"""Strategist worker - researches competitors, extracts winning hooks, writes
findings to MASTER_CONTEXT.md (static brand) + client_data.db (hooks/motions),
and generates a PDF report.

V1.2: hooks/motions/constraints come from SQL via ctx.get_*() rather than
ctx.read()'s YAML frontmatter, which no longer carries those lists.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
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
from core.models import SUPPORTED_MODEL_IDS, validate_model_id
from core.state import AgentState

SYSTEM_PROMPT = """You are the Strategist for an AI performance marketing agency.

Client: {client_name}  (locale: {client_locale})
Brand context: {brand_context}

The client already has {hooks_count} candidate hooks and {constraints_count}
negative constraints recorded. Call `read_existing_hooks` BEFORE
recording new ones so you never duplicate.

LANGUAGE DISCIPLINE: a long-running competitor ad is a MARKET SIGNAL worth
studying, not proof of performance. Frame hooks as "candidate" / "evidence-
informed" / "longevity signal" / "worth testing". Avoid "winning" / "proven"
/ "guaranteed" in any output the operator might forward to a customer.

Your workflow this turn:
  1. If the user didn't name competitors, call `tavily_competitor_search` to
     identify 3-5 of them in {client_locale}.
  2. Call `search_fb_ads_library` for those competitors.
  3. Pass the raw ads through `score_ads_by_longevity` (min_days=14) - only
     ads that have run >= 14 days carry a longevity signal worth studying.
  4. For each *pattern* (not each ad), call `record_winning_hook`. A pattern
     is a recurring angle like "price comparison shock", not a one-off line.
     For visually distinctive video ads, also call `record_referral_motion`.
  5. Call `generate_pdf_report` with a 2-3 sentence executive summary.
     Phrase the summary as observed signals + suggested tests, not as
     proven wins.
  6. Call `append_research_summary` with the same summary.
  7. Stop.

Be selective. Aim for 5-10 high-confidence candidate hooks per run, not 30
mediocre ones. Mark `confidence: high` only when the same pattern shows up
across multiple competitors AND has long longevity (60+ days)."""


def _build_context_writers(ctx: ClientContext):
    """Wraps ClientContext mutators as LangChain tools.

    Each closure captures `ctx` so the LLM doesn't need to know client_id.
    V1.2: hooks/motions/constraints write through to client_data.db; reads
    via ctx.get_*() (no longer fm.X).
    """

    @tool("record_winning_hook")
    def record_winning_hook(
        pattern: str,
        description: str,
        source_ad_id: str,
        days_active: int,
        confidence: str,
    ) -> str:
        """Persist a newly identified candidate hook into client_data.db.
        (Internal table name remains `winning_hooks` for stable schema; the
        operator-facing framing is "candidate hook backed by a longevity
        signal", not "proven winner".) `confidence` must be one of:
        'high', 'medium', 'low'. Returns the auto-assigned hook ID (e.g.
        WH-007)."""
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
        into client_data.db. `reference_path` should be the relative path under
        references/referral_videos/ once the asset is downloaded."""
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
        """Return existing winning_hooks and negative_constraints from
        client_data.db so we don't duplicate work or violate prior constraints."""
        return {
            "winning_hooks": [
                h.model_dump(mode="json") for h in ctx.get_winning_hooks()
            ],
            "negative_constraints": [
                c.model_dump(mode="json") for c in ctx.get_negative_constraints()
            ],
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
        anything under min_days (default 14) - i.e. ads that lack a
        longevity signal strong enough to be worth studying. Surviving ads
        are CANDIDATES for testing, not proven winners."""
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
            winning_hooks=ctx.get_winning_hooks(),
            referral_motions=ctx.get_referral_motions(),
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


def strategist_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    model_name = (config or {}).get("configurable", {}).get("model")
    if not model_name:
        raise ValueError(
            "Model not selected. The Strategist requires an explicit model "
            "choice per run. Pass via config={'configurable': {'model': '<id>'}}. "
            f"Supported: {', '.join(SUPPORTED_MODEL_IDS)}."
        )
    validate_model_id(model_name)

    ctx = ClientContext.load(state["client_id"])
    fm, _ = ctx.read()

    tools = [
        make_tavily_competitor_search_tool(),
        make_fb_ads_search_tool(),
        *_build_context_writers(ctx),
    ]

    llm = ChatAnthropic(model=model_name, max_tokens=4096, temperature=0.2)

    # V1.2: counts come from SQL, not YAML frontmatter.
    existing_hooks = ctx.get_winning_hooks()
    existing_constraints = ctx.get_negative_constraints()

    system_prompt = SYSTEM_PROMPT.format(
        client_name=fm.client.name,
        client_locale=fm.client.locale,
        brand_context=_format_brand_context(fm),
        hooks_count=len(existing_hooks),
        constraints_count=len(existing_constraints),
    )

    react = create_react_agent(llm, tools, prompt=system_prompt)
    result = react.invoke({"messages": state["messages"]})

    new_messages = result["messages"][len(state["messages"]):]

    artifacts = dict(state.get("artifacts") or {})
    artifacts["model_used"] = model_name
    for msg in reversed(new_messages):
        if hasattr(msg, "name") and msg.name == "generate_pdf_report":
            artifacts["strategist_report_pdf"] = msg.content
            break

    return {
        "messages": new_messages,
        "current_agent": "strategist",
        "artifacts": artifacts,
    }
