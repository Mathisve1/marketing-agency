"""Outreach worker - hunts for new client prospects.

V1.1 refactor: the Apify FB Ads tool is wrapped with a 10-ad cap (sorted
by days_active desc) before its payload reaches the LLM, preventing
context bloat from 100-ad scrapes. The SYSTEM_PROMPT pivots from
fabricated visual critiques to text/metadata-derivable strategic gaps.

Workflow:
  1. Tavily competitor search to discover brand names in a target niche/country.
  2. Capped Apify FB Ads scrape for each brand's current Meta ad library.
  3. Analyze each library STRICTLY on text + metadata: identify strategic
     marketing gaps, extract winning hooks (longevity-proven), referral motions.
  4. Save audit JSON + render Brand Audit & Pitch PDF per prospect.

Silo-less worker: state['client_id'] is None, no ClientContext loaded.
Writes go to prospects/<slug>/ via the ProspectStore.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from agents.outreach.prospect_store import ProspectAudit, ProspectStore
from agents.outreach.reporting.pitch_builder import (
    DEFAULT_FRAMEWORK_STRENGTHS,
    build_pitch_pdf,
)
from agents.strategist.analysis.longevity_scorer import score_ads
from agents.strategist.tools.apify_fb_ads import make_fb_ads_search_tool
from agents.strategist.tools.tavily_search import make_tavily_competitor_search_tool
from core.models import SUPPORTED_MODEL_IDS, validate_model_id
from core.state import AgentState


OUTREACH_MAX_ADS_TO_LLM = 10


SYSTEM_PROMPT = """You are the Outreach agent for an AI performance marketing agency.

You hunt for new client prospects: brands in a target niche/country that have
running Meta ads but aren't yet our clients. For each prospect you produce a
Brand Audit & Pitch PDF for cold outreach.

Your workflow this turn:
  1. Parse the user's brief for:
     - niche / vertical (e.g. "fitness apparel", "skincare", "DTC coffee")
     - country code (e.g. "GB", "US", "BE", "NL", "DE")
     - target count (default 5; cap at the explicit number requested)
  2. Call `tavily_competitor_search` with brand=<niche> and country=<country>.
     Extract distinct brand names from the result titles + snippets. Skip
     review sites, marketplaces, and blog roundups.
  3. For each brand (up to the requested count):
     a. Slugify the name into a prospect_id (lowercase, alphanumeric +
        hyphens only, e.g. "gymshark" or "on-running").
     b. Call `search_fb_ads_library` with competitor_pages=[brand_name] and
        country=<country>. The tool returns the TOP 10 ads sorted by
        days_active descending - long-running ads carry the most signal.

     c. Analyze the returned ads STRICTLY on text and metadata. You CANNOT
        see the videos, so do NOT critique visual quality (lighting,
        framing, color grading, "polished look", studio aesthetic, etc.).

        Identify 2-4 STRATEGIC MARKETING GAPS from the data you DO have.
        Examples of legitimate gaps (use these as templates, derived from
        the metadata in each ad row):
          - Copy diversity: "Brand relies on a single copy angle across
            all 10 long-running ads"   (count distinct body_text patterns)
          - Creative freshness: "No fresh creative launched in 67+ days
            (oldest start_date: 2026-02-15)"   (check newest start_date)
          - Format mix: "Image-only ad library; no video presence on Meta"
            (check media_type distribution)
          - CTA repetition: "All 10 ads use the same CTA"
            (count distinct cta_text)
          - Framework: "Captions read as product-feature lists; no
            Hook-Story-Offer narrative"   (inspect body_text structure)
          - Audience signal: "Body text is generic - no persona-specific
            language"   (qualitative read of body_text)

        DO NOT fabricate gaps. If the data shows a healthy ad library
        (diverse copy, fresh creative, varied CTAs), say so honestly and
        cap the gaps list at fewer than 4.

     d. Extract 2-3 winning_hooks from the body_text of the longest-running
        ads. Each hook: {pattern, description, source_ad_id, days_active,
        confidence: 'high'|'medium'|'low'}.

     e. Extract 0-2 referral_motions ONLY for ads where media_type is VIDEO.
        Each motion: {description, reference_path:
        'references/referral_videos/PLACEHOLDER.mp4', pacing: null or
        'inferred from ad title', camera_style: null, duration_seconds:
        null}. DO NOT fabricate pacing/camera details you cannot verify
        from the metadata alone.

     f. Call `save_prospect_audit` with prospect_id, prospect_name, niche,
        country, locale, weaknesses (the strategic gaps from step c),
        winning_hooks, referral_motions, and competitor_ads (the top 10
        scrape from step b).
     g. Call `generate_pitch_pdf` with the prospect_id and a one-sentence
        CTA.
  4. Report back: list of prospects audited with their pitch PDF paths.

CRITICAL: Every Tavily search and Apify scrape costs money. Cap brand
discovery at the user's requested count.

CRITICAL: NEVER critique visual quality of ads. You only see metadata and
text. Identify strategic gaps the brand could fix - never aesthetic
judgments you cannot back up with the data."""


def _make_capped_fb_ads_tool(max_ads: int = OUTREACH_MAX_ADS_TO_LLM):
    """Wraps the Strategist's search_fb_ads_library tool. Sorts the scraped
    ads by days_active desc and caps the LLM-visible payload at `max_ads`.

    Prevents two failure modes:
      - Token bloat: a single Apify scrape can return 50-100 ads at 1-2 KB
        each. 10 ads is enough strategic signal for a pitch.
      - Distractor noise: short-running test ads bury the long-running
        winners. Sorting by days_active surfaces only the proven creative
        before the LLM sees it.
    """
    underlying = make_fb_ads_search_tool()

    @tool("search_fb_ads_library")
    def search_fb_ads_library(
        competitor_pages: list[str],
        country: str = "BE",
        active_only: bool = True,
    ):
        """Scrape competitor Meta ads via Apify, then sort by days_active
        descending and return only the top 10. Long-running ads carry the
        most strategic signal; the cap keeps context tight.

        Returns a list[dict] of scored ads on success, or a string starting
        with "API ERROR:" on Apify failure (see below).
        """
        # V1.3 hardening (Initiative 3): Apify actor failures and HTTP
        # timeouts raise RuntimeError from the underlying tool. Without this
        # guard the exception bubbles through the ReAct agent and crashes
        # the LangGraph node mid-prospect, losing every audit completed
        # earlier in the same run. Catching here lets the LLM see the
        # failure as a string tool result and skip the broken brand
        # gracefully (or report a clean failure to the operator).
        try:
            raw = underlying.invoke({
                "competitor_pages": competitor_pages,
                "country": country,
                "max_ads_per_page": 50,
                "active_only": active_only,
            })
            # score_ads adds the `days_active` field and sorts desc.
            # min_days=0 keeps every ad - the cap is by count, not longevity.
            scored = score_ads(raw, min_days=0)
            return scored[:max_ads]
        except Exception as e:
            return f"API ERROR: {str(e)}"

    return search_fb_ads_library


def _make_save_audit_tool():
    @tool("save_prospect_audit")
    def save_prospect_audit(
        prospect_id: str,
        prospect_name: str,
        niche: str,
        country: str,
        weaknesses: list[str],
        winning_hooks: list[dict],
        referral_motions: list[dict],
        competitor_ads: list[dict],
        locale: Optional[str] = None,
    ) -> str:
        """Persist a prospect's audit findings to prospects/<prospect_id>/audit.json.

        Call this once per prospect, AFTER scraping their ads via
        search_fb_ads_library and analyzing the results. The audit captures
        identified strategic gaps, extracted winning hooks (longevity-proven),
        referral motions, and a snapshot of the competitor's ad library
        (capped at top 5 by days_active). Returns the absolute path of the
        saved audit.json.

        winning_hooks: list of {pattern, description, source_ad_id, days_active, confidence}
        referral_motions: list of {description, reference_path, pacing, camera_style, duration_seconds}
        competitor_ads: list of raw FB ad dicts from search_fb_ads_library
        locale: e.g. 'en-GB' for UK, 'en-US' for US, 'nl-BE' for Flemish-Belgium
        """
        audit = ProspectAudit(
            prospect_id=prospect_id,
            prospect_name=prospect_name,
            niche=niche,
            country=country,
            locale=locale,
            audited_at=datetime.now(timezone.utc).isoformat(),
            competitor_ads=competitor_ads[:5],
            weaknesses=list(weaknesses),
            winning_hooks=list(winning_hooks),
            referral_motions=list(referral_motions),
        )
        store = ProspectStore(prospect_id)
        path = store.save_audit(audit)
        return str(path.resolve())

    return save_prospect_audit


def _make_generate_pitch_tool():
    @tool("generate_pitch_pdf")
    def generate_pitch_pdf(
        prospect_id: str,
        cta: str,
        framework_strengths: Optional[list[str]] = None,
    ) -> str:
        """Render the Brand Audit & Pitch PDF for a prospect using their
        previously saved audit.json. Output is stored at
        prospects/<prospect_id>/pitch.pdf. Returns the absolute output path.

        REQUIRES save_prospect_audit to have been called for this
        prospect_id first.

        cta: one sentence describing the next step.
        framework_strengths: optional override of the agency's default
                             value props.
        """
        store = ProspectStore(prospect_id)
        audit = store.read_audit()
        if audit is None:
            raise FileNotFoundError(
                f"No audit.json for prospect {prospect_id!r}. Call "
                f"save_prospect_audit before generate_pitch_pdf."
            )
        store.root.mkdir(parents=True, exist_ok=True)
        build_pitch_pdf(
            output_path=store.pitch_path,
            prospect_name=audit.prospect_name,
            niche=audit.niche or "(unspecified)",
            weaknesses=audit.weaknesses,
            competitor_ad_summary=audit.competitor_ads,
            our_framework_strengths=(
                framework_strengths if framework_strengths
                else list(DEFAULT_FRAMEWORK_STRENGTHS)
            ),
            cta=cta,
            agency_name=os.getenv("AGENCY_NAME", "Our Agency"),
        )
        return str(store.pitch_path.resolve())

    return generate_pitch_pdf


def outreach_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    model_name = (config or {}).get("configurable", {}).get("model")
    if not model_name:
        raise ValueError(
            "Model not selected. The Outreach agent requires an explicit "
            "model choice per run via config['configurable']['model']. "
            f"Supported: {', '.join(SUPPORTED_MODEL_IDS)}."
        )
    validate_model_id(model_name)

    tools = [
        make_tavily_competitor_search_tool(),
        _make_capped_fb_ads_tool(),
        _make_save_audit_tool(),
        _make_generate_pitch_tool(),
    ]

    llm = ChatAnthropic(model=model_name, max_tokens=4096, temperature=0.2)
    react = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
    result = react.invoke({"messages": state["messages"]})

    new_messages = result["messages"][len(state["messages"]):]

    artifacts = dict(state.get("artifacts") or {})
    artifacts["model_used"] = model_name
    audits: list[str] = []
    pitches: list[str] = []
    for msg in new_messages:
        if hasattr(msg, "name"):
            if msg.name == "save_prospect_audit":
                audits.append(str(msg.content))
            elif msg.name == "generate_pitch_pdf":
                pitches.append(str(msg.content))
    if audits:
        artifacts["outreach_audits"] = audits
    if pitches:
        artifacts["outreach_pitches"] = pitches

    return {
        "messages": new_messages,
        "current_agent": "outreach",
        "artifacts": artifacts,
    }
