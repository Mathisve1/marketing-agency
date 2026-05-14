"""Outreach worker - hunts for new client prospects.

V1.1 refactor: the Apify FB Ads tool is wrapped with a 10-ad cap (sorted
by days_active desc) before its payload reaches the LLM, preventing
context bloat from 100-ad scrapes. The SYSTEM_PROMPT pivots from
fabricated visual critiques to text/metadata-derivable strategic gaps.

V1.6 language audit: "winning_hooks (longevity-proven)" framing softened
throughout. Long-running ads are a market signal worth studying, not
proof of performance. The internal field name `winning_hooks` is kept
for stable schema; only customer-facing language changes.

Workflow:
  1. Tavily competitor search to discover brand names in a target niche/country.
  2. Capped Apify FB Ads scrape for each brand's current Meta ad library.
  3. Analyze each library STRICTLY on text + metadata: identify strategic
     marketing gaps with cited evidence, extract candidate hooks
     (longevity signal, not proof), referral motions.
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
from services.api_errors import format_api_error

OUTREACH_MAX_ADS_TO_LLM = 10

# V1.4 hard cost limits enforced in Python, not in the system prompt. The
# LLM cannot exceed these even if it hallucinates a loop. Each is per
# OUTREACH NODE INVOCATION (one supervisor turn).
#
# Apify: each call is a paid scrape against a Meta ad library. Capping at
# 5 = max 5 prospect brands audited per turn.
# Tavily: each call is a paid web search. One discovery search is usually
# enough to extract 5-10 brand candidates; cap at 2 to allow a fallback
# query if the first returns weak results.
MAX_APIFY_CALLS_PER_RUN = 5
MAX_TAVILY_CALLS_PER_RUN = 2


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
        EVERY gap MUST cite at least one piece of evidence the operator
        can verify against the scraped ads. Use the structured form for
        each weakness:
          {
            "description": "<one-sentence claim>",
            "evidence":    ["<ad_archive_id or source_ad_id>",
                            "<short body_text excerpt <=120 chars>",
                            "<observed count e.g. '8 of 10 ads use same CTA'>"],
            "confidence":  "high" | "medium" | "low"
          }
        Confidence guide:
          - "high"   = three or more evidence items, or a clean count
                       across the full sample (e.g. 10 of 10 ads).
          - "medium" = one or two evidence items, plausible pattern.
          - "low"    = thin signal; surface the observation but flag as
                       worth verifying. Prefer "low" over fabricating.

        Legitimate gap templates (each shows description + the kind of
        evidence the gap requires):
          - Copy diversity: "Brand relies on a single copy angle"
            evidence: ["8 of 10 ads share the body_text opener 'Save 30%...'"]
          - Creative freshness: "No fresh creative launched recently"
            evidence: ["newest start_date is 2026-02-15 (67 days ago)"]
          - Format mix: "Image-only ad library; no video presence"
            evidence: ["10 of 10 ads have media_type=IMAGE"]
          - CTA repetition: "All ads use the same CTA"
            evidence: ["10 of 10 ads use cta_text='Shop Now'"]
          - Framework: "Captions read as product-feature lists; no
            Hook-Story-Offer narrative"
            evidence: ["body_text starts with feature bullets in ads
                       <ad_archive_id_1>, <ad_archive_id_2>, <ad_archive_id_3>"]
          - Audience signal: "Body text is generic, not persona-specific"
            evidence: ["no demographic / use-case language in any of the
                       10 sampled body_text fields"]

        DO NOT fabricate evidence. If you cannot back a claim with at
        least one verifiable item from the scraped ads, EITHER mark
        confidence="low" with a clear note OR drop the claim. If the data
        shows a healthy ad library (diverse copy, fresh creative, varied
        CTAs), say so honestly and cap the gaps list at fewer than 4.

     d. Extract 2-3 candidate hooks from the body_text of the
        longest-running ads. Long-running ads are a market signal worth
        studying, NOT proof of performance - phrase recommendations
        accordingly. Each hook: {pattern, description, source_ad_id,
        days_active, confidence: 'high'|'medium'|'low'}.

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
     g. (Optional) Call `collect_brand_assets` with the prospect_id and
        the brand's website URL (if known). This pulls logo + a couple
        of product images from the homepage so the pitch deck renders
        real brand imagery. Safe to skip - failures are non-fatal.
     h. (Optional) Call `capture_ad_screenshots` with the prospect_id.
        This downloads any inline `image_url` / `video_preview_image_url`
        already on each ad (and attempts a Playwright screenshot of the
        Meta Ads Library URL when those are missing). The pitch deck
        embeds the resulting local images on the 'From live ad to video
        route' page. Safe to skip - failures are non-fatal.
     i. Call `generate_pitch_pdf` with the prospect_id and a one-sentence
        CTA.
  4. Report back: list of prospects audited with their pitch PDF paths.

CRITICAL: Every Tavily search and Apify scrape costs money. Cap brand
discovery at the user's requested count.

CRITICAL: NEVER critique visual quality of ads. You only see metadata and
text. Identify strategic gaps the brand could fix - never aesthetic
judgments you cannot back up with the data."""


def _make_capped_fb_ads_tool(max_ads: int = OUTREACH_MAX_ADS_TO_LLM):
    """Wraps the Strategist's search_fb_ads_library tool with a per-turn
    Apify call counter (V1.4) and the existing top-N + try/except hardening.

    Prevents three failure modes:
      - Token bloat: 50-100 ads per scrape; 10 is enough strategic signal.
      - Distractor noise: short-running ads bury winners.
      - Cost runaway: nonlocal counter caps Apify calls at
        MAX_APIFY_CALLS_PER_RUN regardless of LLM intent.
    """
    underlying = make_fb_ads_search_tool()
    _apify_calls_this_run = 0  # nonlocal counter, reset per outreach_node invocation

    @tool("search_fb_ads_library")
    def search_fb_ads_library(
        competitor_pages: list[str],
        country: str = "BE",
        active_only: bool = True,
    ):
        """Scrape competitor Meta ads via Apify, sort by days_active desc,
        return the top 10. Hard-capped at MAX_APIFY_CALLS_PER_RUN per
        outreach run; further calls return a SAFETY LIMIT string.

        Returns a list[dict] of scored ads on success, or a string starting
        with "API ERROR:" / "SAFETY LIMIT:" otherwise.
        """
        nonlocal _apify_calls_this_run
        if _apify_calls_this_run >= MAX_APIFY_CALLS_PER_RUN:
            return (
                f"SAFETY LIMIT: Maximum of {MAX_APIFY_CALLS_PER_RUN} Apify "
                f"scrape(s) per outreach run. STOP calling search_fb_ads_library. "
                f"Report the {_apify_calls_this_run} prospect(s) already audited "
                f"to the user; for more, submit a fresh outreach request."
            )
        try:
            raw = underlying.invoke({
                "competitor_pages": competitor_pages,
                "country": country,
                "max_ads_per_page": 50,
                "active_only": active_only,
            })
            scored = score_ads(raw, min_days=0)
            _apify_calls_this_run += 1
            return scored[:max_ads]
        except Exception as e:
            # Failures count against the cap too - prevents an LLM from
            # retrying an Apify failure in a tight loop and burning the
            # cap on a permanently-broken brand.
            _apify_calls_this_run += 1
            # V1.7: classified error string so the operator can immediately
            # see whether this was MISSING_KEY (fix .env) vs RATE_LIMIT
            # (back off) vs PROVIDER_ERROR (Apify down).
            return format_api_error("apify", e)

    return search_fb_ads_library


def _make_capped_tavily_tool():
    """V1.4: wrap the Tavily competitor-search tool with a per-turn call
    counter. Tavily is paid; one discovery query usually surfaces 5-10
    brand candidates so MAX_TAVILY_CALLS_PER_RUN=2 leaves room for a
    fallback query if the first returns weak results."""
    underlying = make_tavily_competitor_search_tool()
    _tavily_calls_this_run = 0

    @tool("tavily_competitor_search")
    def tavily_competitor_search(brand: str, country: str = "BE", limit: int = 5):
        """Discover competitor brand names via Tavily. Hard-capped at
        MAX_TAVILY_CALLS_PER_RUN per outreach run."""
        nonlocal _tavily_calls_this_run
        if _tavily_calls_this_run >= MAX_TAVILY_CALLS_PER_RUN:
            return (
                f"SAFETY LIMIT: Maximum of {MAX_TAVILY_CALLS_PER_RUN} Tavily "
                f"search(es) per outreach run. STOP calling "
                f"tavily_competitor_search. Use the brand candidates already "
                f"discovered, or ask the user to refine the niche/country."
            )
        # underlying tool already has its own try/except returning
        # "API ERROR: Tavily search failed - ..." on Tavily failure.
        result = underlying.invoke({"brand": brand, "country": country, "limit": limit})
        _tavily_calls_this_run += 1
        return result

    return tavily_competitor_search


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
        brand_profile: Optional[dict] = None,
    ) -> str:
        """Persist a prospect's audit findings to prospects/<prospect_id>/audit.json.

        Call this once per prospect, AFTER scraping their ads via
        search_fb_ads_library and analyzing the results. The audit captures
        identified strategic gaps (each with evidence + confidence),
        extracted candidate hooks (longevity signal, not proof of
        performance), referral motions, and a snapshot of the
        competitor's ad library
        (capped at top 5 by days_active). Returns the absolute path of the
        saved audit.json.

        winning_hooks: list of {pattern, description, source_ad_id, days_active, confidence}
        referral_motions: list of {description, reference_path, pacing, camera_style, duration_seconds}
        competitor_ads: list of raw FB ad dicts from search_fb_ads_library
        locale: e.g. 'en-GB' for UK, 'en-US' for US, 'nl-BE' for Flemish-Belgium
        brand_profile: optional V3 brand-immersive context for the pitch
            deck. All fields optional; the renderer degrades gracefully
            on absence. Suggested keys:
              brand_name, website_url, instagram_url, facebook_url,
              logo_url, logo_path, primary_color (hex), secondary_color,
              visual_style_notes, product_category, brand_tone,
              audience_assumption, website_screenshot_path,
              social_screenshot_path
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
            brand_profile=brand_profile,
        )
        store = ProspectStore(prospect_id)
        path = store.save_audit(audit)
        return str(path.resolve())

    return save_prospect_audit


def _make_collect_brand_assets_tool():
    """V4: lightweight optional brand-asset collector.

    The LLM can call this AFTER save_prospect_audit (so prospects/<id>/
    exists) and BEFORE generate_pitch_pdf. It fetches the prospect's
    website homepage, extracts logo + hero + a few product images, and
    writes them under prospects/<id>/assets/. The collected paths are
    merged into the audit's brand_profile so the pitch deck renders
    real brand imagery.

    Safe to skip - generate_pitch_pdf degrades gracefully if no assets
    are present.
    """
    from agents.outreach.brand_assets import collect_brand_assets

    @tool("collect_brand_assets")
    def collect_brand_assets_tool(prospect_id: str, website_url: str) -> str:
        """Download a prospect's homepage logo + hero + a couple of
        product images to prospects/<prospect_id>/assets/ and merge the
        local paths into the audit's brand_profile.

        Conservative: 1 HTTP request, max 6 images downloaded total,
        4MB cap per image, same-origin or recognised CDN only. Failures
        are non-fatal - the pitch deck still renders without assets.

        Returns a short status string summarising what was collected.
        """
        from agents.outreach.prospect_store import ProspectStore

        store = ProspectStore(prospect_id)
        audit = store.read_audit()
        if audit is None:
            return f"ERROR: no audit.json for prospect {prospect_id!r}; call save_prospect_audit first."

        result = collect_brand_assets(prospect_id, website_url)

        # Merge collected fields into the audit's brand_profile, never
        # overwriting an operator-supplied value.
        bp = dict(audit.brand_profile or {})
        for key in ("website_url", "logo_path", "hero_image_path", "title", "meta_description"):
            if not bp.get(key) and result.get(key):
                bp[key] = result[key]
        if result.get("product_images"):
            existing = bp.get("product_images") or []
            merged = list(existing)
            for p in result["product_images"]:
                if p not in merged:
                    merged.append(p)
            bp["product_images"] = merged
        audit.brand_profile = bp
        store.save_audit(audit)

        n_products = len(result.get("product_images") or [])
        bits = []
        if result.get("logo_path"):
            bits.append("logo")
        if result.get("hero_image_path"):
            bits.append("hero")
        if n_products:
            bits.append(f"{n_products} product image(s)")
        if not bits:
            err = ",".join(result.get("errors") or ["no_assets_found"])
            return f"collected nothing usable for {prospect_id}: {err}"
        return f"collected for {prospect_id}: {', '.join(bits)}"

    return collect_brand_assets_tool


def _make_capture_ad_screenshots_tool():
    """V4 asset pipeline: capture local screenshots / thumbnails for
    each sampled ad in an existing audit.

    Two-path: (1) direct download of any `image_url` /
    `video_preview_image_url` already on the ad, (2) optional
    Playwright screenshot of the Meta Ads Library URL. Local paths are
    merged onto the audit's competitor_ads as `ad_screenshot_path`,
    which the pitch deck embeds on the 'From live ad to video route'
    page.

    Safe to skip - the deck still renders without ad imagery. Failures
    are non-fatal."""
    from agents.outreach.ad_screenshots import capture_ad_screenshots

    @tool("capture_ad_screenshots")
    def capture_ad_screenshots_tool(prospect_id: str) -> str:
        """Capture local screenshots / thumbnails for each ad in a
        prospect's saved audit. Writes the files under
        prospects/<prospect_id>/assets/ and stores the relative path
        on each ad as `ad_screenshot_path`.

        Returns a short status string summarising what was captured.
        """
        from agents.outreach.prospect_store import ProspectStore

        store = ProspectStore(prospect_id)
        audit = store.read_audit()
        if audit is None:
            return f"ERROR: no audit.json for prospect {prospect_id!r}; call save_prospect_audit first."

        result = capture_ad_screenshots(prospect_id, list(audit.competitor_ads))
        audit.competitor_ads = result["ads"]
        store.save_audit(audit)

        bits = []
        if result["captured"]:
            bits.append(f"{result['captured']} ad screenshot(s)")
        if not result["playwright_available"]:
            bits.append("playwright not installed (only direct image_url downloads attempted)")
        if not bits:
            errs = "; ".join(result.get("errors") or ["no_capture"])
            return f"captured nothing for {prospect_id}: {errs}"
        return f"captured for {prospect_id}: {', '.join(bits)}"

    return capture_ad_screenshots_tool


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
            brand_profile=audit.brand_profile,
            prospect_root=store.root,
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
        _make_capped_tavily_tool(),
        _make_capped_fb_ads_tool(),
        _make_save_audit_tool(),
        _make_collect_brand_assets_tool(),
        _make_capture_ad_screenshots_tool(),
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
