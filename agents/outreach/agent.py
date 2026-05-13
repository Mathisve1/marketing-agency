"""Outreach worker - hunts for new client prospects.

Workflow:
  1. Tavily competitor search to discover brand names in a target niche/country.
  2. Apify FB Ads scrape for each brand's current Meta ad library.
  3. Analyze each library: identify weaknesses, extract winning hooks
     (longevity-proven) and referral motions.
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
from agents.strategist.tools.apify_fb_ads import make_fb_ads_search_tool
from agents.strategist.tools.tavily_search import make_tavily_competitor_search_tool
from core.models import SUPPORTED_MODEL_IDS, validate_model_id
from core.state import AgentState


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
     The query is framed as competitor discovery but works fine for category
     discovery. Extract distinct brand names from the result titles + snippets.
     Skip review sites, marketplaces, and blog roundups.
  3. For each brand (up to the requested count):
     a. Slugify the name into a prospect_id (lowercase, alphanumeric +
        hyphens only, e.g. "gymshark" or "on-running").
     b. Call `search_fb_ads_library` with competitor_pages=[brand_name] and
        country=<country> to scrape their current Meta ads.
     c. Analyze the returned ads. Identify:
        - 2-4 weaknesses (low diversity, dated creative, no UGC, polished
          studio look, lack of social proof, etc.) - short bullets.
        - 2-3 winning_hooks: recurring patterns from ads running >= 14 days.
          Each hook needs: pattern, description, source_ad_id, days_active,
          confidence ('high' | 'medium' | 'low').
        - 0-2 referral_motions for visually distinctive video ads. Each
          motion needs: description, reference_path (use a placeholder like
          'references/referral_videos/PLACEHOLDER.mp4' since we don't have
          the video downloaded yet), pacing, camera_style, duration_seconds.
     d. Call `save_prospect_audit` with the prospect_id, prospect_name,
        niche, country, locale (e.g. 'en-GB'), weaknesses, winning_hooks,
        referral_motions, and competitor_ads (the raw scrape results,
        capped at the top 5 by days_active).
     e. Call `generate_pitch_pdf` with the prospect_id and a one-sentence
        CTA (e.g. "15-minute call to walk through how we'd rebuild your
        top-performing ad as UGC"). framework_strengths can be omitted to
        use the agency defaults.
  4. Report back: list of prospects audited with their pitch PDF paths.

CRITICAL: Each Tavily search and Apify scrape costs money. Cap brand
discovery at the user's requested count. If the user asks for 5 brands,
audit exactly 5 - not 10. If you can only find 3 valid brands, audit 3
and tell the user."""


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
        identified weaknesses, extracted winning hooks (longevity-proven),
        referral motions for visual V2V, and a snapshot of the competitor's
        ad library (top 5 by days_active). Returns the absolute path of the
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

        REQUIRES save_prospect_audit to have been called for this prospect_id
        first - the pitch PDF reads from audit.json.

        cta: one sentence describing the next step (e.g. '15-minute call to
             walk through how we'd rebuild your top-performing ad as UGC').
        framework_strengths: optional list of agency value props to override
                             the defaults. Pass None to use DEFAULT_FRAMEWORK_STRENGTHS.
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

    # Silo-less worker - no ClientContext load. The Outreach agent operates
    # globally and writes to prospects/<slug>/ via the ProspectStore.

    tools = [
        make_tavily_competitor_search_tool(),
        make_fb_ads_search_tool(),
        _make_save_audit_tool(),
        _make_generate_pitch_tool(),
    ]

    llm = ChatAnthropic(model=model_name, max_tokens=4096, temperature=0.2)
    react = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
    result = react.invoke({"messages": state["messages"]})

    new_messages = result["messages"][len(state["messages"]):]

    # Surface audit JSON + pitch PDF paths to the UI's Lead Generation panel.
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
