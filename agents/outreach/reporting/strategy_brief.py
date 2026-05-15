"""StrategyBrief - structured intermediate between an audit.json and the
HTML creative-strategy page.

Mirrors the role of `DeckBrief` for the pitch microsite, but with a
different shape: the strategy page is a long-form private research /
hook map / route library, not a 7-section pitch deck. Keeping the two
briefs in separate modules so the pitch surface and the strategy
surface can evolve independently.

The brief is a pure dataclass tree - no I/O during construction other
than the one `from_audit()` factory that reads `prospects/<id>/audit.json`.
Tests build synthetic briefs directly without touching the filesystem.

Evidence discipline: every text field is sanitised through the same
helpers the pitch deck uses (`_sanitize`, `_strip_debug_inline`,
`_clean_body_excerpt`) so debug strings like `body_text=` /
`ad_archive_id=` / `{{product.brand}}` never reach the rendered HTML.
Section-level copy is always labelled as "hypothesis" when it isn't
backed by audit evidence.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from agents.outreach.prospect_store import ProspectAudit, ProspectStore
from agents.outreach.reporting.pitch_builder import (
    _ad_brand_initial,
    _clean_body_excerpt,
    _niche_profile,
    _parse_weakness,
    _resolve_asset_path,
    _sanitize,
    _strip_debug_inline,
)

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Sub-types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExecutiveCard:
    """One of the three top-of-page summary cards."""
    label: str       # e.g. "What the current ads rely on"
    body: str        # one or two sentences


@dataclass(frozen=True)
class MarketSignal:
    """A single bullet on the market-context section. Each carries an
    explicit `evidence` flag so the rendered page can chip it as either
    'audit signal' (grounded) or 'working hypothesis' (claim we'd
    validate before scaling)."""
    title: str
    body: str
    evidence: str    # "audit" | "hypothesis"


@dataclass(frozen=True)
class StrategyAdPattern:
    """One row on the competitor/ad pattern board."""
    archive_id: str
    pattern: str         # what the ad does (e.g. "Dynamic catalog feed")
    weakness: str        # what's limiting it
    opportunity: str     # what we'd test next to it
    library_url: Optional[str] = None
    screenshot_path: Optional[Path] = None
    body_excerpt: Optional[str] = None
    days_active: Optional[int] = None


@dataclass(frozen=True)
class HookTerritory:
    """One cell on the hook map.

    `priority` triages the hook into one of three lanes:
      - "priority"   -> tested first; high-evidence fit
      - "test_later" -> tested in a later sprint; medium fit
      - "avoid"      -> not tested first; compliance / fit risk

    Default is "priority" so existing in-process briefs that haven't
    been re-built against this schema still land in the top lane.
    """
    name: str            # e.g. "Sensitive-skin trust"
    rationale: str       # why this could work
    risk: str            # what to avoid
    sample_line: str     # sample hook line
    priority: str = "priority"  # "priority" | "test_later" | "avoid"


@dataclass(frozen=True)
class CompetitorAdProof:
    """One sampled competitor ad with on-disk proof.

    Mirrors the per-ad shape the Outreach `ad_screenshots` pipeline
    already produces for the prospect's own ads, but tagged with the
    competitor it belongs to and the creative-pattern tags the strategy
    builder used to bucket it. Lives on `CompetitorIntel.sampled_ads`
    and (cross-referenced by `(competitor_name, ad_archive_id)`) on
    `CreativePattern.ad_evidence` so the strategy page never lists a
    brand under a pattern without a verifiable ad to point at.

    Evidence discipline:
      - `evidence_level == "competitor_ad_evidence"` REQUIRES a
        captured screenshot OR a public ad-library URL the client can
        click; we never label something as ad-evidence on hand-waving.
      - `evidence_level == "needs_validation"` is used when we know
        the competitor runs ads (e.g. ad_library_search_url resolves)
        but we did not capture a specific creative yet.
      - `evidence_level == "hypothesis"` is the fallback when no
        observation backs the claim at all.

    `pattern_tags` is the strategy-builder's bridge to the creative
    pattern catalogue: each tag is a short slug (e.g.
    `"review_ugc"`, `"texture_demo"`) that `CreativePattern.ad_evidence`
    keys on so a pattern card only lists brand-ad pairs that actually
    embody it.
    """
    competitor_name: str
    ad_archive_id: str
    ad_library_url: Optional[str] = None
    screenshot_path: Optional[Path] = None
    body_excerpt: Optional[str] = None
    cta_text: Optional[str] = None
    days_active: Optional[int] = None
    media_type: Optional[str] = None
    pattern_tags: Sequence[str] = ()
    evidence_level: str = "needs_validation"   # see docstring
    capture_status: Optional[str] = None       # mirrors ad_screenshots.py
    capture_error: Optional[str] = None
    # `why_by_tag[tag]` is a short reason ("Ad copy quotes review /
    # star ratings / customer voice.") the renderer surfaces under
    # the evidence tile when the operator hovers / reads the proof.
    # Empty dict when the classifier couldn't justify any tag.
    why_by_tag: dict[str, str] = field(default_factory=dict)
    # Where the final `pattern_tags` decision came from. Set by the
    # capture script's `merge_ai_tags_with_regex` policy:
    #   "ai"                                -> AI vision tags promoted
    #                                          (high/medium confidence,
    #                                          should_use_for_strategy)
    #   "regex"                             -> AI was unavailable or
    #                                          AI failed; regex wins.
    #   "regex_fallback_low_confidence"     -> AI ran but came back low
    #                                          confidence; regex wins.
    # The Section 05 selector treats all three sources equally - the
    # field is informational, for the operator audit trail.
    tag_source: Optional[str] = None
    tag_source_reason: Optional[str] = None
    # The raw regex tag list that the classifier produced before any
    # AI promotion. Always populated when the capture script ran the
    # AI pass; None on older JSON files. Preserved so the operator can
    # see what regex would have called the ad.
    raw_regex_tags: Optional[Sequence[str]] = None
    # Optional AI-classifier outputs. Populated when --ai-classify ran.
    ai_classification_status: Optional[str] = None
    ai_confidence: Optional[str] = None        # "high" | "medium" | "low"
    ai_primary_pattern: Optional[str] = None
    ai_evidence_notes: Sequence[str] = ()
    ai_visual_evidence: Sequence[str] = ()
    ai_text_evidence: Sequence[str] = ()
    ai_caution: Optional[str] = None
    ai_should_use_for_strategy: bool = False
    ai_model: Optional[str] = None


@dataclass(frozen=True)
class CompetitorIntel:
    """One row on the competitor intelligence board.

    Evidence labels:
      - "audit"        -> grounded in this audit's data
      - "web_research" -> reasoning from public brand / web context
      - "hypothesis"   -> working assumption that needs validation

    Ad-proof fields (new):
      - `meta_ads_url` -> public Meta Ad Library search URL for the
        competitor's page. Lets the client open the live library
        themselves even when we did not capture an individual ad.
      - `sampled_ads`  -> structured list of ad proofs (screenshot +
        ad_library_url + tags). Empty when we have not captured any
        ads for this competitor yet.
      - `evidence_level` -> high-level chip the renderer shows next
        to the competitor name:
          * "competitor_ad_evidence" -> at least one sampled_ads entry
            with a real screenshot OR a real ad_library_url.
          * "research"               -> web_research only (no ads).
          * "needs_validation"       -> hypothesis but worth checking.
          * "hypothesis"             -> reasoning, no observation.
        Computed by `_compute_competitor_evidence_level()` if left as
        None so the section builder stays declarative.
    """
    name: str
    website_url: Optional[str]
    why_relevant: str
    positioning_angle: str
    creative_pattern: str    # observed creative / ad cadence
    pai_can_learn: str       # what we'd lift, safely
    confidence: str          # "high" | "medium" | "low"
    evidence: str            # "audit" | "web_research" | "hypothesis"
    meta_ads_url: Optional[str] = None
    sampled_ads: Sequence[CompetitorAdProof] = ()
    evidence_level: Optional[str] = None
    # High-level outcome of the most recent capture attempt for this
    # competitor. Mirrors the `capture_status` values produced by
    # `scripts/capture_competitor_ads.py`:
    #   "captured" | "no_active_ads_found" | "false_positive_filtered"
    #   | "playwright_failed" | "blocked" | "scrape_failed"
    # None means we never attempted a capture (the renderer keeps
    # this row in the "HYPOTHESIS" lane).
    capture_status: Optional[str] = None


@dataclass(frozen=True)
class CreativePattern:
    """One pattern card on the competitor creative-pattern board.

    Compares Pai's current behaviour with the broader market on a
    specific tactic (review proof, ingredient credibility, founder
    voice, routine simplification, etc).

    Ad-proof rules:
      - `tag` is the short pattern slug used to bridge to
        `CompetitorAdProof.pattern_tags`. When set, the renderer
        attaches matching sampled ads as "ad evidence" cards. When
        empty, only the human-readable `who_uses` list is shown.
      - `ad_evidence` is populated by the section builder, not by the
        section *renderer*, so the renderer stays a pure transform.
        Each entry is one ad we can point the client at. When this
        list is non-empty, the renderer treats the pattern as
        observed evidence; when empty, the pattern is rendered with a
        clear "needs validation" / "hypothesis" tone and the
        `who_uses` chips are shown as candidate hypotheses, not as
        claims that we have ad-level proof.
    """
    name: str
    who_uses: Sequence[str]  # competitor / market names (candidates)
    why_works: str
    brand_status: str        # what the brand currently does / does not do
    safe_adaptation: str     # how we'd adapt without crossing compliance
    risk: str
    evidence: str            # "audit" | "web_research" | "hypothesis"
    tag: Optional[str] = None
    ad_evidence: Sequence[CompetitorAdProof] = ()


@dataclass(frozen=True)
class PatternValidationGap:
    """One pattern that did not clear the Section 05 evidence bar.

    Wraps a `CreativePattern` and records WHY we did not promote it
    plus the recommended next action, so the renderer's "Patterns to
    validate next" section can show real diagnostics instead of a
    generic "no evidence yet" line.

    `reason_code` values:
      "no_evidence"   -> we have not captured ANY ad supporting this
                         pattern yet.
      "single_ad"     -> we have one ad supporting this pattern but the
                         Section-05 rule requires at least two.
      "lost_to_dedup" -> every ad that supports this pattern was claimed
                         by a more-specific pattern during display-level
                         de-duplication; the supporting ads are not lost,
                         they just power a different card.

    `recommended_action` values:
      "scrape_more"    -> capture more sampled ads from the named
                          competitors before sprint two.
      "validate_next"  -> audit-grounded hypothesis; pick up a proof ad
                          in the next outreach pass.
      "avoid_for_now"  -> compliance / off-brand risk - park for now.
    """
    pattern: CreativePattern
    current_ad_count: int
    current_competitor_count: int
    reason_code: str
    reason_text: str
    recommended_action: str
    action_text: str
    # When `reason_code == "lost_to_dedup"`, the name of the pattern
    # that ended up claiming the supporting ads. Empty otherwise.
    superseded_by: Optional[str] = None
    # When set, ad_evidence we DID find (even if below the threshold)
    # so the 05B card can still show a hint of proof.
    surviving_ad_evidence: Sequence[CompetitorAdProof] = ()


@dataclass(frozen=True)
class AvoidRoute:
    """One row on the 'routes we would avoid for now' section.

    Language is deliberately careful:
      - We never say a route "will fail".
      - We frame the recommendation as lower-evidence fit, higher
        compliance risk, or 'validate later, not first'.
    """
    name: str
    why_tempting: str
    why_avoid: str
    evidence: str   # "low_evidence" | "compliance_risk" | "off_brand_fit" | "validate_later"
    test_instead: str


@dataclass(frozen=True)
class OpportunityMap:
    """One creative-opportunity row (3-5 total)."""
    can_own: str         # what the brand can own
    why_others_dont: str # why competitors may not own it
    proof_video: str     # what kind of video proves it


@dataclass(frozen=True)
class RouteIdea:
    """One short-form video idea in the route library."""
    title: str
    hook: str
    opening_shot: str
    proof_point: str
    cta: str
    asset_requirement: str
    confidence: str   # "high" | "medium" | "low"


@dataclass(frozen=True)
class SprintRecommendation:
    """One concept selected for the first sprint."""
    route_title: str
    reason: str


@dataclass
class StrategyBrief:
    """All content + assets the strategy page needs."""
    brand_name: str
    niche: str
    agency_name: str = "Yuvo Studio"
    website_url: Optional[str] = None
    product_category: Optional[str] = None
    brand_tone: Optional[str] = None
    audience_assumption: Optional[str] = None
    primary_color: Optional[str] = None
    logo_path: Optional[Path] = None
    hero_image_path: Optional[Path] = None
    product_images: list[Path] = field(default_factory=list)

    # Sections
    cover_subhead: str = ""
    executive_cards: list[ExecutiveCard] = field(default_factory=list)
    market_context: list[MarketSignal] = field(default_factory=list)
    competitors: list[CompetitorIntel] = field(default_factory=list)
    # The full enriched pattern list. Includes every pattern (validated
    # or not), with `ad_evidence` populated from the captured competitor
    # ads. Kept for backwards compatibility; Section 05 consumes
    # `validated_patterns` and Section 05B consumes `unvalidated_patterns`.
    creative_patterns: list[CreativePattern] = field(default_factory=list)
    # Patterns that cleared the ≥2-unique-ads bar after de-duplication.
    # The renderer's Section 05 reads this list exclusively, so the
    # main board never lists a pattern backed by only one ad.
    validated_patterns: list[CreativePattern] = field(default_factory=list)
    # Patterns that did NOT clear the bar, each wrapped with a reason
    # code, current evidence count and recommended next action. The
    # renderer's "Patterns to validate next" section reads this list.
    unvalidated_patterns: list[PatternValidationGap] = field(default_factory=list)
    ad_patterns: list[StrategyAdPattern] = field(default_factory=list)
    hook_territories: list[HookTerritory] = field(default_factory=list)
    opportunities: list[OpportunityMap] = field(default_factory=list)
    routes: list[RouteIdea] = field(default_factory=list)
    avoid_routes: list[AvoidRoute] = field(default_factory=list)
    sprint: list[SprintRecommendation] = field(default_factory=list)
    not_in_sprint_one: list[SprintRecommendation] = field(default_factory=list)

    # Wiring
    prospect_root: Optional[Path] = None
    # Reserved for backwards compatibility; the strategy page is a
    # post-purchase deliverable so the Next-step CTA no longer points
    # at the pitch microsite. The field is kept so existing callers
    # don't break, but the renderer does NOT use it as a CTA target.
    public_pitch_url: Optional[str] = None

    # --------------------------------------------------------------- #
    # Factory
    # --------------------------------------------------------------- #

    @classmethod
    def from_audit(
        cls,
        prospect_id: str,
        *,
        prospects_root: Optional[Path] = None,
        agency_name: str = "Yuvo Studio",
        public_pitch_url: Optional[str] = None,
    ) -> "StrategyBrief":
        store = ProspectStore(prospect_id, prospects_root=prospects_root)
        audit = store.read_audit()
        if audit is None:
            raise FileNotFoundError(
                f"StrategyBrief.from_audit: no audit.json for {prospect_id!r}"
            )
        prospect_root = store.root
        competitor_ads = load_competitor_ad_proofs(prospect_root)
        return cls.from_audit_data(
            audit,
            prospect_root=prospect_root,
            agency_name=agency_name,
            public_pitch_url=public_pitch_url,
            competitor_ad_proofs=competitor_ads,
        )

    @classmethod
    def from_audit_data(
        cls,
        audit: ProspectAudit,
        *,
        prospect_root: Path,
        agency_name: str = "Yuvo Studio",
        public_pitch_url: Optional[str] = None,
        competitor_ad_proofs: Optional[dict[str, dict]] = None,
    ) -> "StrategyBrief":
        bp = audit.brand_profile or {}
        brand_name = audit.prospect_name or "this brand"
        niche = audit.niche or "your category"
        product_category = (bp.get("product_category") or "").strip() or None
        brand_tone = (bp.get("brand_tone") or "").strip() or None
        audience_assumption = (bp.get("audience_assumption") or "").strip() or None

        weaknesses = [_parse_weakness(w) for w in (audit.weaknesses or [])]

        # Assets - logo + hero + dedup'd product images. The strategy
        # page only embeds local image paths; remote URLs are never
        # fetched at render time.
        #
        # Hero policy (post-purchase deliverable):
        #   1. Prefer an explicit `hero_image_path` from the audit.
        #   2. Otherwise, promote the first product image - a clean
        #      product still life always reads better than a homepage
        #      screenshot.
        #   3. We deliberately do NOT fall back to
        #      `website_screenshot_path`. Apify captures the homepage
        #      with cookie consent modals visible ("Your privacy
        #      matters", "Accept all", "Decline all"), and that overlay
        #      is unprofessional on a paid client deliverable. When no
        #      hero can be resolved, the renderer drops to a designed
        #      mock cover instead.
        logo = _resolve_asset_path(bp.get("logo_path"), prospect_root)
        raw_product_images: list[Path] = []
        raw_product_seen: set[Path] = set()
        for raw in (bp.get("product_images") or []):
            p = _resolve_asset_path(raw, prospect_root)
            if p is None or p in raw_product_seen:
                continue
            raw_product_seen.add(p)
            raw_product_images.append(p)

        hero = _resolve_asset_path(bp.get("hero_image_path"), prospect_root)
        if hero is None and raw_product_images:
            hero = raw_product_images[0]

        product_images: list[Path] = [
            p for p in raw_product_images if p != hero
        ]

        # Sections
        executive_cards = _build_executive_cards(weaknesses=weaknesses, niche=niche)
        market_context = _build_market_context(niche=niche, audience_assumption=audience_assumption)
        proofs_by_competitor = dict(competitor_ad_proofs or {})
        competitors = _build_competitors(
            brand_name=brand_name,
            niche=niche,
            ad_proofs_by_competitor=proofs_by_competitor,
        )
        creative_patterns = _build_creative_patterns(
            brand_name=brand_name,
            niche=niche,
            competitors=competitors,
        )
        validated_patterns, unvalidated_patterns = select_pattern_evidence(
            creative_patterns,
        )
        ad_patterns = _build_ad_patterns(
            audit_ads=list(audit.competitor_ads or []),
            prospect_root=prospect_root,
        )
        hook_territories = _build_hook_territories(niche=niche)
        opportunities = _build_opportunities(brand_name=brand_name, niche=niche)
        routes = _build_routes(brand_name=brand_name, niche=niche)
        avoid_routes = _build_avoid_routes(niche=niche)
        sprint = _build_sprint(routes=routes, niche=niche)
        not_in_sprint_one = _build_not_in_sprint_one(
            routes=routes, sprint=sprint, niche=niche,
        )

        # Reframed as a paid client deliverable - written after the
        # creative audit, prepared for the first production sprint.
        cover_subhead = (
            f"Creative strategy map for {brand_name}, prepared after the "
            "first creative audit. Routes to produce, routes to avoid, "
            "and the decision map for the first batch of videos."
        )

        return cls(
            brand_name=brand_name,
            niche=niche,
            agency_name=agency_name,
            website_url=(bp.get("website_url") or "").strip() or None,
            product_category=product_category,
            brand_tone=brand_tone,
            audience_assumption=audience_assumption,
            primary_color=(bp.get("primary_color") or "").strip() or None,
            logo_path=logo,
            hero_image_path=hero,
            product_images=product_images,
            cover_subhead=cover_subhead,
            executive_cards=executive_cards,
            market_context=market_context,
            competitors=competitors,
            creative_patterns=creative_patterns,
            validated_patterns=validated_patterns,
            unvalidated_patterns=unvalidated_patterns,
            ad_patterns=ad_patterns,
            hook_territories=hook_territories,
            opportunities=opportunities,
            routes=routes,
            avoid_routes=avoid_routes,
            sprint=sprint,
            not_in_sprint_one=not_in_sprint_one,
            prospect_root=prospect_root,
            public_pitch_url=public_pitch_url,
        )


# --------------------------------------------------------------------------- #
# Competitor ad proof - on-disk loader + helpers
# --------------------------------------------------------------------------- #


COMPETITOR_ADS_FILENAME = "competitor_ads.json"
"""Strategy-only sidecar file holding the structured competitor ad
proof. Lives at `prospects/<id>/strategy/competitor_ads.json` so it is
discovered automatically by `StrategyBrief.from_audit()`.

Shape:
    {
        "competitors": {
            "UpCircle Beauty": {
                "meta_ads_url": "https://www.facebook.com/ads/library/?...",
                "sampled_ads": [
                    {
                        "ad_archive_id": "123...",
                        "ad_library_url": "https://...",
                        "screenshot_path": "assets/competitors/upcircle-beauty/ad_1.png",
                        "body_excerpt": "...",
                        "cta_text": "Shop Now",
                        "days_active": 28,
                        "media_type": "VIDEO",
                        "pattern_tags": ["review_ugc", "routine_simplification"],
                        "capture_status": "screenshot_playwright",
                        "capture_error": null
                    }
                ]
            },
            ...
        }
    }

Paths are stored relative to `prospects/<id>/` (matching the
`audit.json` `ad_screenshot_path` convention), so the file is portable
across machines.
"""


# Slugify a competitor name into a folder-safe stub.
_COMPETITOR_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _competitor_slug(name: str) -> str:
    raw = (name or "").strip().lower()
    slug = _COMPETITOR_SLUG_RE.sub("-", raw).strip("-")
    return slug or "competitor"


def _str_list(v: Any) -> list[str]:
    """Coerce a JSON-decoded value into a clean list of non-empty
    stripped strings. Used to defensively read optional list fields
    from competitor_ads.json."""
    if not isinstance(v, list):
        return []
    return [str(s).strip() for s in v if isinstance(s, str) and s.strip()]


def load_competitor_ad_proofs(
    prospect_root: Path,
) -> dict[str, dict]:
    """Read `prospects/<id>/strategy/competitor_ads.json` if it exists
    and return:

        {
            competitor_name: {
                "meta_ads_url": str | None,
                "sampled_ads": list[CompetitorAdProof],
            },
            ...
        }

    Missing file -> empty dict (callers treat that as "no proof yet").
    Malformed JSON or schema mismatch -> empty dict + warning log; the
    strategy page still renders the hand-coded competitor list, just
    without ad evidence.

    Paths are resolved against `prospect_root` so the renderer can use
    absolute Paths through its `_resolve_asset_path` helper without
    needing to know the on-disk convention.
    """
    path = prospect_root / "strategy" / COMPETITOR_ADS_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(
            "strategy_brief: %s could not be read (%s); ignoring competitor ad proof",
            path, e,
        )
        return {}
    if not isinstance(data, dict):
        return {}
    competitors = data.get("competitors")
    if not isinstance(competitors, dict):
        return {}
    out: dict[str, dict] = {}
    for name, blob in competitors.items():
        if not isinstance(name, str) or not isinstance(blob, dict):
            continue
        raw_ads = blob.get("sampled_ads")
        if not isinstance(raw_ads, list):
            continue
        meta_ads_url = (blob.get("meta_ads_url") or "").strip() or None
        competitor_capture_status = (
            (blob.get("capture_status") or "").strip() or None
        )
        ads: list[CompetitorAdProof] = []
        for raw in raw_ads:
            if not isinstance(raw, dict):
                continue
            archive_id = str(raw.get("ad_archive_id") or "").strip()
            if not archive_id:
                continue
            screenshot_rel = raw.get("screenshot_path")
            screenshot = (
                _resolve_asset_path(screenshot_rel, prospect_root)
                if screenshot_rel
                else None
            )
            raw_regex_tags_field = raw.get("raw_regex_tags")
            raw_regex_tags = (
                tuple(
                    t.strip() for t in raw_regex_tags_field
                    if isinstance(t, str) and t.strip()
                )
                if isinstance(raw_regex_tags_field, list)
                else None
            )
            tags_raw = raw.get("pattern_tags") or []
            base_pattern_tags = tuple(
                t.strip() for t in tags_raw
                if isinstance(t, str) and t.strip()
            )
            raw_why = raw.get("why_by_tag") or {}
            why_by_tag: dict[str, str] = (
                {str(k): str(v) for k, v in raw_why.items() if v}
                if isinstance(raw_why, dict)
                else {}
            )
            days_raw = raw.get("days_active")
            days_active = (
                int(days_raw) if isinstance(days_raw, (int, float)) and days_raw > 0
                else None
            )
            evidence_level = (raw.get("evidence_level") or "").strip() or (
                "competitor_ad_evidence" if (screenshot or raw.get("ad_library_url"))
                else "needs_validation"
            )

            # Read the AI classification block (all optional fields - the
            # JSON pre-dates AI tagging until the operator runs
            # --ai-classify).
            ai_status = (raw.get("ai_classification_status") or "").strip() or None
            ai_confidence = (raw.get("ai_confidence") or "").strip().lower() or None
            ai_primary = (raw.get("ai_primary_pattern") or "").strip() or None
            ai_should_use = bool(raw.get("ai_should_use_for_strategy", False))
            ai_evidence_notes = _str_list(raw.get("ai_evidence_notes"))
            ai_visual_evidence = _str_list(raw.get("ai_visual_evidence"))
            ai_text_evidence = _str_list(raw.get("ai_text_evidence"))
            ai_caution = (raw.get("ai_caution") or None) or None
            if isinstance(ai_caution, str):
                ai_caution = ai_caution.strip() or None
            ai_model = (raw.get("ai_model") or "").strip() or None

            tag_source = (raw.get("tag_source") or "").strip() or None
            tag_source_reason = (raw.get("tag_source_reason") or "").strip() or None

            # Re-derive the merged pattern_tags at load time. This is
            # idempotent when the capture script already wrote merged
            # tags, and forward-compatible when an operator hand-adds
            # AI fields to the JSON without re-running the merge.
            pattern_tags = base_pattern_tags
            if ai_status:
                try:
                    from agents.outreach.reporting.ad_pattern_classifier import (
                        merge_ai_tags_with_regex,
                    )
                    regex_baseline = (
                        list(raw_regex_tags)
                        if raw_regex_tags is not None
                        else list(base_pattern_tags)
                    )
                    merged = merge_ai_tags_with_regex(regex_baseline, raw)
                    pattern_tags = tuple(merged["pattern_tags"])
                    if raw_regex_tags is None:
                        raw_regex_tags = tuple(merged["raw_regex_tags"])
                    if not tag_source:
                        tag_source = merged["tag_source"]
                        tag_source_reason = merged["tag_source_reason"]
                except Exception as exc:
                    log.warning(
                        "strategy_brief: load-time AI tag merge failed "
                        "for %s/%s: %r",
                        name, archive_id, exc,
                    )

            ads.append(CompetitorAdProof(
                competitor_name=name,
                ad_archive_id=archive_id,
                ad_library_url=(raw.get("ad_library_url") or "").strip() or None,
                screenshot_path=screenshot,
                body_excerpt=(raw.get("body_excerpt") or "").strip() or None,
                cta_text=(raw.get("cta_text") or "").strip() or None,
                days_active=days_active,
                media_type=(raw.get("media_type") or "").strip().upper() or None,
                pattern_tags=pattern_tags,
                evidence_level=evidence_level,
                capture_status=(raw.get("capture_status") or "").strip() or None,
                capture_error=(raw.get("capture_error") or "").strip() or None,
                why_by_tag=why_by_tag,
                tag_source=tag_source,
                tag_source_reason=tag_source_reason,
                raw_regex_tags=raw_regex_tags,
                ai_classification_status=ai_status,
                ai_confidence=ai_confidence,
                ai_primary_pattern=ai_primary,
                ai_evidence_notes=tuple(ai_evidence_notes),
                ai_visual_evidence=tuple(ai_visual_evidence),
                ai_text_evidence=tuple(ai_text_evidence),
                ai_caution=ai_caution,
                ai_should_use_for_strategy=ai_should_use,
                ai_model=ai_model,
            ))
        out[name] = {
            "meta_ads_url": meta_ads_url,
            "sampled_ads": ads,
            "capture_status": competitor_capture_status,
        }
    return out


def _compute_competitor_evidence_level(
    *,
    base_evidence: str,
    sampled_ads: Sequence[CompetitorAdProof],
    meta_ads_url: Optional[str],
) -> str:
    """Roll the per-row evidence into a single chip label for the
    section 04 card. Priority (highest to lowest):

      1. competitor_ad_evidence -> at least one sampled_ad with a
         screenshot OR an ad_library_url the client can verify.
      2. research               -> meta_ads_url known (the live library
         is one click away) OR base_evidence == "web_research".
      3. needs_validation       -> base_evidence == "hypothesis" and
         we have any meta_ads_url or sampled_ad listing.
      4. hypothesis             -> nothing to point at.
    """
    has_real_ad = any(
        (ad.screenshot_path is not None) or bool(ad.ad_library_url)
        for ad in sampled_ads
    )
    if has_real_ad:
        return "competitor_ad_evidence"
    if base_evidence == "web_research":
        return "research"
    if meta_ads_url:
        return "needs_validation"
    if sampled_ads:
        return "needs_validation"
    return "hypothesis"


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #


def _build_executive_cards(*, weaknesses: list[dict], niche: str) -> list[ExecutiveCard]:
    """Three top-of-page summary cards derived from observed weaknesses
    plus a forward-looking line. Falls back to niche-aware defaults
    when no weaknesses were captured."""
    blob = " ".join((w.get("description") or "").lower() for w in weaknesses)

    rely_on: str
    gap: str
    if "placeholder" in blob or "dynamic catalog" in blob or "dco" in blob or "{{product" in blob:
        rely_on = (
            "Dynamic catalog product-feed ads with placeholder body text. "
            "The current library is doing the algorithm's work but not "
            "telling the brand's story."
        )
    elif "single copy" in blob or "identical" in blob or "duplicat" in blob:
        rely_on = (
            "A single copy angle carried across most of the active ads - "
            "good for testing one message, bad for a creative library that "
            "needs to keep refreshing."
        )
    elif "no video" in blob or "image-only" in blob or "static" in blob:
        rely_on = (
            "An image-led library with little or no short-form video. "
            "Static creative caps view-time on the surfaces where buyers "
            "actually scroll."
        )
    else:
        rely_on = (
            "A small, recently-launched library with one tone of voice. "
            "Strong category positioning but a narrow set of creative angles."
        )

    if "no cta" in blob or "missing cta" in blob or "single cta" in blob:
        gap = (
            "No clear CTA layer per concept and no testing of intent variants - "
            "the buyer is shown the product but never asked to commit to a "
            "specific next action."
        )
    elif "no video" in blob or "image-only" in blob:
        gap = (
            "No UGC-style or founder-led short-form video presence. "
            "Competitors on TikTok/Reels are owning the scroll-stop layer."
        )
    elif "placeholder" in blob or "dynamic catalog" in blob:
        gap = (
            "Zero narrative or persona context behind the product feeds. "
            "Buyers see the product but never the reason to choose it."
        )
    else:
        gap = (
            "A creative library that fatigues together - no rolling fresh "
            "creative, no cohort-specific openings, no proof layer."
        )

    # Reframed for a post-purchase deliverable: this is the sprint
    # plan, not the pitch.
    test_first = (
        "Three UGC-style short-form video routes against the strongest "
        "weakness in the current library, each opening with a different "
        "hook territory. Existing product photography covers most of the "
        "asset requirement; one short founder / customer shoot fills the rest."
    )

    return [
        ExecutiveCard(label="What the current ads rely on", body=_sanitize(rely_on)),
        ExecutiveCard(label="Where the creative gap is", body=_sanitize(gap)),
        ExecutiveCard(label="What the first sprint produces", body=_sanitize(test_first)),
    ]


def _build_market_context(*, niche: str, audience_assumption: Optional[str]) -> list[MarketSignal]:
    """Market-context signals. Every entry carries an `evidence` flag so
    the renderer chips it appropriately. We default to "hypothesis"
    unless the brief itself is the source.

    Niche-aware: skincare gets sensitive-skin / organic / clean-beauty
    territories; default gets generic short-form / paid-social context.
    """
    profile = _niche_profile(niche)
    signals: list[MarketSignal] = []

    if audience_assumption:
        signals.append(MarketSignal(
            title="Audience assumption (from brief)",
            body=_sanitize(audience_assumption),
            evidence="audit",
        ))

    if profile == "skincare":
        signals.extend([
            MarketSignal(
                title="Sensitive / reactive skin is a high-trust category",
                body=(
                    "Buyers in this segment over-research before purchase. Ingredient "
                    "lists, certifications and brand authority influence conversion "
                    "more than discount."
                ),
                evidence="hypothesis",
            ),
            MarketSignal(
                title="Clean-beauty messaging fatigues fast",
                body=(
                    "Generic 'natural / organic / clean' opening lines now read as "
                    "category table-stakes. Specificity (named ingredient, certification, "
                    "expert voice) tends to outperform broad clean-beauty wording."
                ),
                evidence="hypothesis",
            ),
            MarketSignal(
                title="UGC-style review video punches above its weight in skincare",
                body=(
                    "Texture demos, application moments and quiet routine cuts carry "
                    "trust signal that polished brand films struggle to match - "
                    "particularly for first-time buyers."
                ),
                evidence="hypothesis",
            ),
            MarketSignal(
                title="Compliance gate on health claims",
                body=(
                    "Medical / clinical wording in paid creative carries platform and "
                    "regulatory risk. Best to frame skin-condition language as "
                    "lived experience or product-page-supported claims only."
                ),
                evidence="audit",
            ),
        ])
    elif profile == "fitness":
        signals.append(MarketSignal(
            title="Process > product in fitness creative",
            body=(
                "Day-1-vs-day-30 framing, weekly check-in language and "
                "honest before / after restraint outperform aspirational gym shots "
                "on cold audiences."
            ),
            evidence="hypothesis",
        ))
    else:
        signals.append(MarketSignal(
            title="Short-form video is the scroll-stop layer",
            body=(
                "Static creative caps view-time on Reels/TikTok-style placements. "
                "A small short-form video pack run alongside the current static library "
                "is usually the cheapest creative lever to pull."
            ),
            evidence="hypothesis",
        ))

    return signals


def _build_ad_patterns(
    *,
    audit_ads: list[dict],
    prospect_root: Path,
) -> list[StrategyAdPattern]:
    """Up to 6 rows for the competitor/ad pattern board. Each one names
    the pattern, the limiting weakness, and the opportunity - derived
    from the ad's body, CTA, media_type and library URL."""
    out: list[StrategyAdPattern] = []
    for ad in audit_ads[:6]:
        archive_id = str(ad.get("ad_archive_id") or "").strip()
        if not archive_id:
            continue
        body = (ad.get("body_text") or "").strip()
        is_placeholder = "{{" in body and "}}" in body
        body_excerpt = _clean_body_excerpt(body)
        media = (ad.get("media_type") or "").strip().upper() or None

        if is_placeholder:
            pattern = "Dynamic catalog product feed"
            weakness = (
                "Placeholder body text - no narrative, no persona, no hook."
            )
            opportunity = (
                "A 12-15s video opener that pays off the product page the "
                "feed already points at."
            )
        elif media == "VIDEO":
            pattern = "Single brand-led video"
            weakness = "Only one video angle is in market - no variant to learn from."
            opportunity = (
                "Two UGC-style cuts against the same product, different "
                "first three seconds."
            )
        elif media == "IMAGE" or body_excerpt:
            pattern = "Static product image with brand-led copy"
            weakness = (
                "Static creative caps view-time on Reels/TikTok-style placements - "
                "the buyer never gets to the proof."
            )
            opportunity = (
                "Short-form video built from the same product still: "
                "scroll-stop opener, product proof, single CTA."
            )
        else:
            pattern = "Active ad (signal worth studying)"
            weakness = (
                "Pattern observable from public Meta Ad Library but copy not captured "
                "in this sample."
            )
            opportunity = "Worth a manual review before treating as a creative anchor."

        # Library URL: prefer ad_library_url, else derive from archive id.
        library_url = (ad.get("ad_library_url") or "").strip() or None
        if not library_url and archive_id.isdigit():
            library_url = f"https://www.facebook.com/ads/library/?id={archive_id}"

        screenshot = _resolve_asset_path(ad.get("ad_screenshot_path"), prospect_root)
        days_raw = ad.get("days_active")
        days_active = int(days_raw) if isinstance(days_raw, (int, float)) and days_raw > 0 else None

        out.append(StrategyAdPattern(
            archive_id=archive_id,
            pattern=_sanitize(pattern),
            weakness=_sanitize(weakness),
            opportunity=_sanitize(opportunity),
            library_url=library_url,
            screenshot_path=screenshot,
            body_excerpt=body_excerpt,
            days_active=days_active,
        ))
    return out


def _build_hook_territories(*, niche: str) -> list[HookTerritory]:
    """Hook territories with explicit priority lanes.

    The renderer groups them into three columns:
      - PRIORITY   -> high-evidence fit, tested first
      - TEST LATER -> medium-evidence, parked for sprint 2-3
      - AVOID      -> compliance / brand-fit risk, not tested first

    Decisions here read as a senior strategist's calls, not a brain
    dump. Niche-aware: skincare gets the sensitive-skin-aware set;
    default returns a smaller generic short-form territory list.
    """
    profile = _niche_profile(niche)
    if profile == "skincare":
        return [
            HookTerritory(
                name="Sensitive-skin trust",
                rationale=(
                    "Sensitive-skin buyers over-research and value brand authority. "
                    "An opener that signals 'we understand reactive skin' earns the "
                    "first three seconds."
                ),
                risk=(
                    "Do not name medical conditions or claim treatment. Keep it "
                    "lived-experience or product-page-supported."
                ),
                sample_line="I stopped trusting skincare for a while. This is what brought me back.",
                priority="priority",
            ),
            HookTerritory(
                name="Ingredient proof",
                rationale=(
                    "Named single ingredient + a one-line reason it matters outperforms "
                    "generic clean-beauty wording in this segment."
                ),
                risk="Avoid clinical-sounding %-strength claims unless supported on the PDP.",
                sample_line="The one ingredient I now check for on every label.",
                priority="priority",
            ),
            HookTerritory(
                name="Routine simplicity",
                rationale=(
                    "Buyers who reacted to 10-step routines are looking for permission "
                    "to do less. Short routine = less to react to = more confidence."
                ),
                risk="Do not promise 'fixes' - the hook is about restraint, not transformation.",
                sample_line="The three-step routine I went back to after every reaction.",
                priority="priority",
            ),
            HookTerritory(
                name="Review / social proof",
                rationale=(
                    "Real-buyer phrasing carries higher trust than studio copy. Lift "
                    "from on-site reviews where consent is clear."
                ),
                risk="Quote with permission; never fabricate reviews or imply they're paid.",
                sample_line="\"I almost gave up on serums. This was the one that didn't fight my skin.\"",
                priority="priority",
            ),
            HookTerritory(
                name="Founder / expert credibility",
                rationale=(
                    "A founder-led 25s explainer on a single ingredient choice tends to "
                    "out-convert anonymous brand voice in trust-heavy categories."
                ),
                risk="Founder needs to be on-camera, not behind a logo card.",
                sample_line="Why we left this ingredient out, even when every brief said to include it.",
                priority="test_later",
            ),
            HookTerritory(
                name="Texture / application",
                rationale=(
                    "Quiet texture closeups (no music, no voiceover) are a powerful "
                    "scroll-stop on cold audiences in skincare."
                ),
                risk="Resist the urge to add a brand intro card. The texture IS the hook.",
                sample_line="The way it sits on the skin in the first 30 seconds.",
                priority="priority",
            ),
            HookTerritory(
                name="Skin-barrier anxiety",
                rationale=(
                    "Buyers who damaged their barrier with active ingredients are looking "
                    "for permission to step back. A 'pause-and-repair' hook lands hard "
                    "on this cohort."
                ),
                risk="Do not diagnose. Keep language to 'felt' / 'noticed' / 'looked'.",
                sample_line="My skin was tired before I was tired of skincare.",
                priority="test_later",
            ),
            HookTerritory(
                name="Gift / set / bundle logic",
                rationale=(
                    "Bundles answer the 'where do I start' problem for first-time buyers. "
                    "A set is a permission-to-try, not just a discount."
                ),
                risk="Lead with the routine, not the saving. Discount-first reads as bargain skincare.",
                sample_line="The set I'd hand a friend with reactive skin and no patience.",
                priority="test_later",
            ),
            HookTerritory(
                name="Certification / organic proof",
                rationale=(
                    "Named certification + the one practical thing it changed on the "
                    "formula is more useful than the seal on its own."
                ),
                risk="Don't list every cert; pick one and explain it.",
                sample_line="The one certification I actually look for on a skincare label.",
                priority="test_later",
            ),
            HookTerritory(
                name="Aggressive before / after transformation",
                rationale=(
                    "Transformation hooks pull strong CTR in unregulated categories, but "
                    "they sit at the wrong end of the premium / compliance spectrum for "
                    "a sensitive-skin brand."
                ),
                risk=(
                    "Higher compliance risk on health-adjacent claims; weakens premium "
                    "positioning. Would validate later with restrained framing only."
                ),
                sample_line="(not a first-sprint priority - see Routes to avoid.)",
                priority="avoid",
            ),
            HookTerritory(
                name="Discount-led urgency",
                rationale=(
                    "Discount-first openers convert hot retargeting traffic; they do "
                    "not build the premium / organic equity a brand of this shape needs "
                    "in cold creative."
                ),
                risk=(
                    "Lowers perceived price-anchor; conflicts with premium positioning. "
                    "Park for retargeting tests, not cold prospecting."
                ),
                sample_line="(not a first-sprint priority - see Routes to avoid.)",
                priority="avoid",
            ),
            HookTerritory(
                name="Trend-only TikTok format",
                rationale=(
                    "Trend-driven formats (point-at-text, dance-to-product) have a "
                    "short half-life and do not carry the considered brand voice."
                ),
                risk=(
                    "Off-brand fit; high creative refresh cost. Worth a separate "
                    "experiment lane once the core sprint has produced learning."
                ),
                sample_line="(not a first-sprint priority - see Routes to avoid.)",
                priority="avoid",
            ),
        ]
    # Default - smaller generic short-form territory set, with one
    # priority/test-later/avoid example so the renderer's grouping
    # still has all three lanes populated.
    return [
        HookTerritory(
            name="Lived-experience opener",
            rationale="First-person opening line out-performs brand-led copy on cold buyers.",
            risk="Do not invent the experience. Lift from real reviews where consent is clear.",
            sample_line="I almost didn't try this. Here is what changed.",
            priority="priority",
        ),
        HookTerritory(
            name="Product proof close-up",
            rationale="Quiet, texture-led close-up scrolls slower than studio brand films.",
            risk="No music, no overlay. The product is the hook.",
            sample_line="What it actually does in the first thirty seconds.",
            priority="priority",
        ),
        HookTerritory(
            name="Founder one-line explainer",
            rationale="Founder-on-camera builds trust faster than anonymous brand voice.",
            risk="Founder needs to be on-camera, not behind a logo card.",
            sample_line="Why we built this in the first place.",
            priority="test_later",
        ),
        HookTerritory(
            name="Routine moment",
            rationale="Lived routine moment ('I changed once today') pulls a different cohort than launch copy.",
            risk="Hook needs to feel real - if it reads as a script, it costs you the first 3 seconds.",
            sample_line="One change in my morning that made everything easier.",
            priority="test_later",
        ),
        HookTerritory(
            name="Discount-led urgency",
            rationale="Discount-first openers may convert hot retargeting traffic; they do not build brand equity on cold creative.",
            risk="Lowers perceived price-anchor. Park for retargeting, not cold prospecting.",
            sample_line="(not a first-sprint priority - see Routes to avoid.)",
            priority="avoid",
        ),
    ]


def _build_opportunities(*, brand_name: str, niche: str) -> list[OpportunityMap]:
    """3-5 strategic creative opportunities. Niche-aware."""
    profile = _niche_profile(niche)
    if profile == "skincare":
        return [
            OpportunityMap(
                can_own=(
                    f"Sensitive-skin trust through scientific clarity - {brand_name} "
                    "explaining one ingredient choice at a time."
                ),
                why_others_dont=(
                    "Most competitors lead with 'natural / organic' as a category "
                    "claim, which doesn't differentiate inside clean beauty."
                ),
                proof_video=(
                    "Founder-led 25s explainer per ingredient: why it's in, why "
                    "something else is out, no marketing language."
                ),
            ),
            OpportunityMap(
                can_own=(
                    "The restraint angle - 'the routine I went back to after every "
                    "reaction' as a brand voice."
                ),
                why_others_dont=(
                    "The category is built on adding steps; very few brands are "
                    "willing to make the case for fewer products."
                ),
                proof_video=(
                    "Quiet 15s routine cut, no voiceover, just the three steps in "
                    "real time."
                ),
            ),
            OpportunityMap(
                can_own=(
                    "Texture proof at scroll speed - product behaviour on skin as "
                    "the scroll-stop, not as the punchline."
                ),
                why_others_dont=(
                    "Polished brand films open with the logo; UGC films open with "
                    "the face. The product texture moment is an under-used opener."
                ),
                proof_video=(
                    "10-15s close-up loop: pump, application, absorb. Sound on, "
                    "no music."
                ),
            ),
            OpportunityMap(
                can_own=(
                    "Reactive-skin permission - lived testimony as the brand's "
                    "primary trust signal."
                ),
                why_others_dont=(
                    "Compliance fear pushes most brands toward sanitised language; "
                    "lived voices clear the bar when sourced with consent."
                ),
                proof_video=(
                    "Two on-camera customer voices, each under 20s, focused on the "
                    "decision moment, not the result."
                ),
            ),
        ]
    return [
        OpportunityMap(
            can_own="A specific buying-moment opening line, written in the audience's voice.",
            why_others_dont="Most competitors open with brand copy, not lived language.",
            proof_video="20s on-camera testimonial built around the moment, not the product.",
        ),
        OpportunityMap(
            can_own="Product behaviour as the scroll-stop instead of the brand voice.",
            why_others_dont="Static creative caps view-time on Reels/TikTok placements.",
            proof_video="Quiet 12-15s product close-up loop, no music, no voiceover.",
        ),
        OpportunityMap(
            can_own="Founder-led trust signal, 25s on-camera, one decision at a time.",
            why_others_dont="Most brands hide behind anonymous voiceover.",
            proof_video="Founder-on-camera explainer, no slide cards, no music.",
        ),
    ]


def _build_routes(*, brand_name: str, niche: str) -> list[RouteIdea]:
    """10-15 short-form video route ideas. Niche-aware."""
    profile = _niche_profile(niche)
    if profile == "skincare":
        return [
            RouteIdea(
                title="The reactive-skin routine I went back to",
                hook="I stopped trusting skincare for a while. This is what brought me back.",
                opening_shot="On-camera close-up, sensitive lighting, talent applying cleanser.",
                proof_point="Three named products on the shelf, single ingredient called out for each.",
                cta="Find my routine",
                asset_requirement="One real customer voice + product close-ups (existing brand library).",
                confidence="high",
            ),
            RouteIdea(
                title="One ingredient, one reason",
                hook="The one ingredient I now check for on every label.",
                opening_shot="Founder on-camera, holding the product, indoor daylight.",
                proof_point="On-screen text quotes the ingredient + the one practical reason it's used.",
                cta="See the formula",
                asset_requirement="Founder availability for 25s on-camera shoot OR existing founder footage.",
                confidence="medium",
            ),
            RouteIdea(
                title="The three-step routine after the reaction",
                hook="The three-step routine I went back to after every reaction.",
                opening_shot="POV shot - hands picking up the three products from a bathroom shelf.",
                proof_point="Real-time application, no voiceover, on-screen step titles.",
                cta="Shop the routine",
                asset_requirement="Existing product photography + 1-hour bathroom shoot.",
                confidence="high",
            ),
            RouteIdea(
                title="Texture proof, fifteen seconds",
                hook="The way it sits on the skin in the first 30 seconds.",
                opening_shot="Macro shot of the product pump, droplet hitting the skin.",
                proof_point="Tight close-up of product absorbing - sound on, no music.",
                cta="See the serum",
                asset_requirement="Existing product still + 30-min macro reshoot.",
                confidence="high",
            ),
            RouteIdea(
                title="What 'clean' actually means here",
                hook="The one certification I actually look for on a skincare label.",
                opening_shot="Founder on-camera, certification mark visible.",
                proof_point="One named certification + the one practical thing it changed in the formula.",
                cta="See what we left out",
                asset_requirement="Founder on-camera + existing brand cert assets.",
                confidence="medium",
            ),
            RouteIdea(
                title="The set I'd hand a friend",
                hook="The set I'd hand a friend with reactive skin and no patience.",
                opening_shot="Founder on-camera, holding the starter kit.",
                proof_point="Brief explanation of the three products, one sentence per product.",
                cta="Try the starter kit",
                asset_requirement="Starter kit pack shot (already in library).",
                confidence="high",
            ),
            RouteIdea(
                title="My skin was tired before I was",
                hook="My skin was tired before I was tired of skincare.",
                opening_shot="On-camera customer, quiet light, talking directly to camera.",
                proof_point="The customer names one product they kept using and why.",
                cta="Pause your routine",
                asset_requirement="Customer voice with documented consent.",
                confidence="medium",
            ),
            RouteIdea(
                title="One product, three buyers",
                hook="Same serum, three skin types, three honest reads.",
                opening_shot="Split-screen of three customers each in their own mirror, one beat apart.",
                proof_point="Each customer names one specific change they noticed.",
                cta="Shop the serum",
                asset_requirement="Three customer voices + one product hero shot.",
                confidence="medium",
            ),
            RouteIdea(
                title="The morning, on-camera",
                hook="Three minutes, three products, no filter.",
                opening_shot="Bathroom POV at first light, products lined up.",
                proof_point="Real-time application with on-screen timestamps.",
                cta="See the routine",
                asset_requirement="Existing brand bathroom-set footage OR 1-hour shoot.",
                confidence="medium",
            ),
            RouteIdea(
                title="Why we left this ingredient out",
                hook="Why we left this ingredient out, even when every brief said to include it.",
                opening_shot="Founder on-camera, close on hands holding the bottle.",
                proof_point="One sentence per excluded ingredient + why.",
                cta="Read the formula",
                asset_requirement="Founder on-camera, indoor lighting.",
                confidence="medium",
            ),
            RouteIdea(
                title="A quiet review",
                hook="\"I almost gave up on serums. This was the one that didn't fight my skin.\"",
                opening_shot="On-camera customer in profile, looking out a window.",
                proof_point="Direct customer voice, no music, no overlay.",
                cta="Read more reviews",
                asset_requirement="One on-camera review with consent.",
                confidence="medium",
            ),
            RouteIdea(
                title="The decision, not the result",
                hook="The first time I caught myself in a mirror and didn't need a filter.",
                opening_shot="Quiet selfie-style POV, daylight.",
                proof_point="No clinical claim. Lived-experience framing only.",
                cta="Shop the line",
                asset_requirement="Customer-led shoot with documented consent.",
                confidence="low",
            ),
        ]
    # Default route library - generic short-form ideas.
    return [
        RouteIdea(
            title="One product, three openings",
            hook="Same product. Different first three seconds.",
            opening_shot="Three intercut customer shots, one beat apart.",
            proof_point="Each opener carries a different hook line; product is held.",
            cta="Shop the line",
            asset_requirement=f"Three customer voices + {brand_name} product still.",
            confidence="medium",
        ),
        RouteIdea(
            title="Quiet product proof",
            hook="What it actually does in the first thirty seconds.",
            opening_shot="Macro close-up of the product in use.",
            proof_point="No music, no overlay - the product behaviour is the hook.",
            cta="See the product",
            asset_requirement="30-minute macro reshoot of existing product.",
            confidence="high",
        ),
        RouteIdea(
            title="Founder one-line explainer",
            hook="Why we built this in the first place.",
            opening_shot="Founder on-camera, indoor daylight, holding the product.",
            proof_point="One sentence per design decision.",
            cta="Read the story",
            asset_requirement="Founder availability for 25s on-camera shoot.",
            confidence="medium",
        ),
        RouteIdea(
            title="A real review, on-camera",
            hook="The one thing I keep telling people about it.",
            opening_shot="Customer on-camera in their own space.",
            proof_point="Direct customer voice, no music, no overlay.",
            cta="Read more",
            asset_requirement="One on-camera review with consent.",
            confidence="medium",
        ),
        RouteIdea(
            title="The morning, on-camera",
            hook="A small change in my morning routine.",
            opening_shot="POV bathroom shot at first light.",
            proof_point="Real-time use with on-screen timestamps.",
            cta="See the routine",
            asset_requirement="Existing brand bathroom set OR 1-hour shoot.",
            confidence="medium",
        ),
    ]


def _build_sprint(*, routes: list[RouteIdea], niche: str) -> list[SprintRecommendation]:
    """Pick 3-5 routes for the first sprint based on (in order):
      1. confidence == "high" (easiest to evidence)
      2. asset requirement is fully met by existing library
      3. clearest fit to the niche's biggest creative gap

    The implementation keeps this deterministic: sort by confidence
    bucket, take the first three high-confidence routes; if fewer than
    three are high, top up with the first medium-confidence routes.
    """
    _ = niche  # reserved for niche-specific overrides
    by_conf: dict[str, list[RouteIdea]] = {"high": [], "medium": [], "low": []}
    for r in routes:
        by_conf.setdefault(r.confidence, []).append(r)

    sprint: list[RouteIdea] = []
    sprint.extend(by_conf.get("high") or [])
    if len(sprint) < 3:
        sprint.extend(by_conf.get("medium") or [])
    sprint = sprint[:4]

    out: list[SprintRecommendation] = []
    for r in sprint:
        reason = (
            "Existing brand library covers the asset requirement; "
            "high-confidence opener against the strongest weakness in the "
            "current ad pattern. Cheap learning loop."
            if r.confidence == "high"
            else "Stronger creative bet that needs one small piece of new "
                 "footage; pair it with a high-confidence route to share "
                 "audience and budget."
        )
        out.append(SprintRecommendation(
            route_title=r.title,
            reason=_sanitize(reason),
        ))
    return out


def _build_not_in_sprint_one(
    *,
    routes: list[RouteIdea],
    sprint: list[SprintRecommendation],
    niche: str,
) -> list[SprintRecommendation]:
    """Routes that are valid creative bets but parked out of sprint one.

    Produces three categories of postponements:
      1. Low-confidence routes (need new footage or new consent).
      2. Medium routes not picked because the sprint cap (4) was hit.
      3. Niche-specific parked angles ('clinical / before-after style').
    """
    _ = niche  # niche-aware reasoning lives in route bodies already
    picked_titles = {s.route_title for s in sprint}
    parked: list[SprintRecommendation] = []
    for r in routes:
        if r.title in picked_titles:
            continue
        if r.confidence == "low":
            reason = (
                "Lower evidence fit; parked until we have a customer "
                "voice with documented consent on the exact angle."
            )
        elif r.confidence == "medium":
            reason = (
                "Strong second-sprint candidate; postponed only because "
                "sprint one is capped at four routes for the first "
                "learning loop."
            )
        else:
            reason = (
                "Postponed until the first sprint produces audience and "
                "creative learning."
            )
        parked.append(SprintRecommendation(
            route_title=r.title,
            reason=_sanitize(reason),
        ))
    # Cap to keep the section scannable.
    return parked[:6]


# --------------------------------------------------------------------------- #
# Competitor intelligence + creative patterns + avoid routes
# --------------------------------------------------------------------------- #


def _build_competitors(
    *,
    brand_name: str,
    niche: str,
    ad_proofs_by_competitor: Optional[dict[str, dict]] = None,
) -> list[CompetitorIntel]:
    """4-6 competitor intelligence rows, merged with any captured ad
    proof for that competitor (from
    `prospects/<id>/strategy/competitor_ads.json`).

    Evidence policy:
      - "web_research"  -> publicly known positioning / category context
      - "hypothesis"    -> reasoning that needs a Meta Ads Library
                           sweep before we treat it as a creative anchor

    When `ad_proofs_by_competitor` carries a non-empty list for a
    competitor, the row's `sampled_ads` is populated and the
    `evidence_level` field is recomputed via
    `_compute_competitor_evidence_level()` so the renderer can show a
    `COMPETITOR ADS` chip instead of the bare `RESEARCH` /
    `HYPOTHESIS` chip. Matching is case-sensitive on the competitor
    name; we intentionally do NOT do fuzzy matching so a typo in the
    sidecar surfaces as "no proof" rather than silently swapping
    brands.
    """
    proofs_by_competitor = ad_proofs_by_competitor or {}
    base_rows = _build_competitors_base(brand_name=brand_name, niche=niche)
    return [
        _enrich_competitor(row, proofs_by_competitor)
        for row in base_rows
    ]


def _enrich_competitor(
    row: CompetitorIntel,
    proofs_by_competitor: dict[str, dict],
) -> CompetitorIntel:
    """Merge any captured ad proof onto a base CompetitorIntel row and
    compute the chip-level `evidence_level`. Returns a new frozen
    instance; the input is untouched.

    Accepts the proof entry in the shape
    `{"meta_ads_url": ..., "sampled_ads": [...]}` returned by
    `load_competitor_ad_proofs`. When the entry is missing for a
    competitor the row falls through with empty ads and the
    `evidence_level` is computed from the base `evidence` field only.
    """
    from dataclasses import replace
    entry = proofs_by_competitor.get(row.name) or {}
    ads = tuple(entry.get("sampled_ads") or ())
    meta_ads_url = row.meta_ads_url or entry.get("meta_ads_url")
    capture_status = row.capture_status or entry.get("capture_status")
    evidence_level = _compute_competitor_evidence_level(
        base_evidence=row.evidence,
        sampled_ads=ads,
        meta_ads_url=meta_ads_url,
    )
    return replace(
        row,
        sampled_ads=ads,
        meta_ads_url=meta_ads_url,
        evidence_level=evidence_level,
        capture_status=capture_status,
    )


def _build_competitors_base(*, brand_name: str, niche: str) -> list[CompetitorIntel]:
    """Hand-coded base list, without ad proof. Split out so the public
    `_build_competitors()` stays a simple enrich-and-return path."""
    profile = _niche_profile(niche)
    if profile == "skincare":
        return [
            CompetitorIntel(
                name="UpCircle Beauty",
                website_url="https://upcirclebeauty.com",
                why_relevant=(
                    f"Same UK premium-organic shelf as {brand_name}, comparable "
                    "price points, sensitive-skin language, sustainability angle."
                ),
                positioning_angle=(
                    "Upcycled-ingredient story (coffee grounds, fruit-stones). "
                    "Lifestyle-first photography, not lab-first."
                ),
                creative_pattern=(
                    "Reviewer-led UGC stitched with founder voice; ingredient-"
                    "provenance reels and Father's-Day / gifting bundles."
                ),
                pai_can_learn=(
                    "Provenance story per ingredient is a credible scroll-stop. "
                    "Adapt by leading with the one ingredient choice and the one "
                    "reason it changed the formula."
                ),
                confidence="medium",
                evidence="web_research",
            ),
            CompetitorIntel(
                name="REN Clean Skincare",
                website_url="https://www.renskincare.com",
                why_relevant=(
                    "Premium UK clean-skincare benchmark. Larger budget than "
                    f"{brand_name}, so a useful read on where the category's "
                    "creative ceiling currently sits."
                ),
                positioning_angle=(
                    "Clinical credibility with a clean-beauty veneer; biome / "
                    "barrier vocabulary front-and-centre on PDPs and ads."
                ),
                creative_pattern=(
                    "Glossy product close-ups, before/after-adjacent claims, "
                    "expert-narration voiceovers."
                ),
                pai_can_learn=(
                    "Permission to lean on barrier-health language when it is "
                    "supported on the PDP. Avoid REN's heavier clinical "
                    "framing - it does not fit the brand's restraint."
                ),
                confidence="medium",
                evidence="web_research",
            ),
            CompetitorIntel(
                name="Evolve Organic Beauty",
                website_url="https://evolvebeauty.co.uk",
                why_relevant=(
                    "UK organic-skincare peer; similar founder-led / certified-"
                    "organic narrative."
                ),
                positioning_angle=(
                    "Founder-as-formulator. Hand-blended, small-batch story."
                ),
                creative_pattern=(
                    "Founder-on-camera explainers, behind-the-bench production "
                    "footage, sustainability call-outs."
                ),
                pai_can_learn=(
                    "Founder-led 25s explainer is a believable trust signal in "
                    "this segment. Worth a controlled test once founder time is "
                    "available."
                ),
                confidence="medium",
                evidence="hypothesis",
            ),
            CompetitorIntel(
                name="Aurelia London",
                website_url="https://aurelialondon.com",
                why_relevant=(
                    "Premium UK probiotic-skincare brand at the same price "
                    "ceiling; meaningful overlap on sensitive-skin / organic "
                    "search intent."
                ),
                positioning_angle=(
                    "Probiotic + botanical narrative. Editorial brand voice, "
                    "boutique-luxury photography."
                ),
                creative_pattern=(
                    "Editorial stills, restrained brand voice, light use of "
                    "influencer creators."
                ),
                pai_can_learn=(
                    "A premium editorial photography read still has shelf space, "
                    "but UGC and texture-proof video out-perform it on cold "
                    "audiences. Keep editorial for organic, lead with UGC on paid."
                ),
                confidence="medium",
                evidence="hypothesis",
            ),
            CompetitorIntel(
                name="By Sarah London",
                website_url="https://bysarahlondon.com",
                why_relevant=(
                    "Independent UK skincare brand with a strong sensitive-skin "
                    "/ allergy-aware narrative; smallest budget peer in this set."
                ),
                positioning_angle=(
                    "Allergy-aware, single-formulator story. Strong "
                    "personal-narrative voice."
                ),
                creative_pattern=(
                    "Founder voice notes, before/after restraint, Instagram-"
                    "first short-form."
                ),
                pai_can_learn=(
                    "Personal-narrative voice carries trust at small budget. "
                    f"{brand_name} can do the same with one founder explainer "
                    "per ingredient choice."
                ),
                confidence="medium",
                evidence="hypothesis",
            ),
            CompetitorIntel(
                name="Wildsmith Skin",
                website_url="https://wildsmithskin.com",
                why_relevant=(
                    "Premium UK natural-skincare benchmark with editorial-led "
                    "creative; useful read on what the very top of the price "
                    "ceiling is currently doing."
                ),
                positioning_angle=(
                    "Heritage / estate-grown botanicals; quietly luxurious."
                ),
                creative_pattern=(
                    "Editorial photography, slow product films, light paid "
                    "social presence."
                ),
                pai_can_learn=(
                    "Editorial restraint reads as premium but does not scale "
                    "on paid social. Borrow the tone, not the format."
                ),
                confidence="low",
                evidence="hypothesis",
            ),
            CompetitorIntel(
                name="Oskia London",
                website_url="https://www.oskiaskincare.com",
                why_relevant=(
                    "UK premium clean-skincare peer with proven active Meta "
                    "ads; useful read on a clean-beauty brand running a "
                    "consistent paid social cadence."
                ),
                positioning_angle=(
                    "Bio-active clean skincare. MSM / nutrient-led ingredient "
                    "story, premium boutique tone."
                ),
                creative_pattern=(
                    "Dynamic catalog product ads with consistent shop / sign-"
                    "up CTAs; restrained editorial brand voice."
                ),
                pai_can_learn=(
                    "A clean-beauty competitor running a consistent always-on "
                    "DCO cadence at premium price point - useful proof that "
                    "the brand budget shape is viable on Meta."
                ),
                confidence="medium",
                evidence="web_research",
            ),
            CompetitorIntel(
                name="Balance Me",
                website_url="https://balanceme.com",
                why_relevant=(
                    "UK independent natural-skincare brand at a similar price "
                    "ceiling; sensitive-skin / barrier vocabulary."
                ),
                positioning_angle=(
                    "Naturally-active formulations, sister-founded brand "
                    "story."
                ),
                creative_pattern=(
                    "Mixed image / video paid creative with discount / "
                    "bundle anchors; founder-told brand moments."
                ),
                pai_can_learn=(
                    "Cohesive bundle / starter-set creative is a credible "
                    "scroll-stop at this price ceiling - if proof shows up in "
                    "their library, it is worth a controlled adaptation."
                ),
                confidence="medium",
                evidence="hypothesis",
            ),
            CompetitorIntel(
                name="Green People",
                website_url="https://www.greenpeople.co.uk",
                why_relevant=(
                    "UK organic-skincare peer with overlapping sensitive-skin "
                    "audience; established always-on Meta presence."
                ),
                positioning_angle=(
                    "Organic / vegan brand story, family-owned heritage, "
                    "wide product range across face / body / baby."
                ),
                creative_pattern=(
                    "Always-on catalog + offer ads with seasonal bundle "
                    "creative; consistent organic / vegan messaging."
                ),
                pai_can_learn=(
                    "Long-running creative library shows what an organic "
                    "skincare brand can sustain on always-on Meta - cadence "
                    "and offer logic, not aesthetic."
                ),
                confidence="medium",
                evidence="hypothesis",
            ),
            CompetitorIntel(
                name="Tropic Skincare",
                website_url="https://www.tropicskincare.com",
                why_relevant=(
                    "UK clean-beauty peer with a strong B Corp / sustainable "
                    "narrative; meaningful overlap on values-led skincare "
                    "buyers."
                ),
                positioning_angle=(
                    "B Corp, planet-positive ingredients, ambassador / "
                    "direct-sales channel."
                ),
                creative_pattern=(
                    "Lifestyle + ingredient-story video, regular short-form "
                    "content cadence, ambassador-led UGC."
                ),
                pai_can_learn=(
                    "A clean-beauty brand running consistent short-form "
                    "video with values-led copy. If their captured ads "
                    "show this, treat it as proof the budget shape works."
                ),
                confidence="medium",
                evidence="hypothesis",
            ),
            CompetitorIntel(
                name="Neal's Yard Remedies",
                website_url="https://www.nealsyardremedies.com",
                why_relevant=(
                    "Long-established UK organic-skincare peer; meaningful "
                    "overlap on certified-organic / clean-beauty intent."
                ),
                positioning_angle=(
                    "Heritage organic apothecary; aromatherapy + botanical "
                    "story; broad product breadth."
                ),
                creative_pattern=(
                    "Catalog + bundle creative with regular sale anchors; "
                    "long-form heritage storytelling alongside."
                ),
                pai_can_learn=(
                    "A heritage organic competitor's bundle / offer creative "
                    "logic is a useful read on which routine-led bundles "
                    "convert at this price ceiling."
                ),
                confidence="medium",
                evidence="hypothesis",
            ),
            CompetitorIntel(
                name="Medik8",
                website_url="https://www.medik8.com",
                why_relevant=(
                    "Premium UK clinical-skincare peer; useful read on the "
                    "actives-first end of the same price ceiling that "
                    f"{brand_name} occupies. Outside Pai's restraint, so a "
                    "useful contrast on what we do NOT want to copy."
                ),
                positioning_angle=(
                    "Clinical, actives-led, dermatologist-endorsed."
                ),
                creative_pattern=(
                    "Mixed video + DCO with strong before/after-adjacent "
                    "claims; expert-narration voiceovers."
                ),
                pai_can_learn=(
                    "Clinical / before-after framing is a high-volume pattern "
                    "at this price ceiling but a poor fit for Pai's restraint. "
                    "Worth watching as a contrast, not as a model."
                ),
                confidence="medium",
                evidence="hypothesis",
            ),
        ]
    # Default - smaller, evidence-honest set.
    return [
        CompetitorIntel(
            name="Direct category benchmark",
            website_url=None,
            why_relevant=(
                "Largest brand in the same audience pocket; useful read on "
                "where the category's creative ceiling sits."
            ),
            positioning_angle="Polished brand voice, high media budget.",
            creative_pattern="Glossy product films, expert-narration voiceover.",
            pai_can_learn=(
                "Borrow the audience read, not the polish. UGC out-converts "
                "polished brand films on cold audiences."
            ),
            confidence="low",
            evidence="hypothesis",
        ),
        CompetitorIntel(
            name="Mid-tier independent peer",
            website_url=None,
            why_relevant=(
                "Same price ceiling, smaller media budget - the realistic "
                "creative shape to benchmark against."
            ),
            positioning_angle="Founder-led, more personal voice.",
            creative_pattern="Founder voice, UGC stitched with brand stills.",
            pai_can_learn=(
                "Founder-led 25s explainer is a believable trust signal at "
                "small budget."
            ),
            confidence="medium",
            evidence="hypothesis",
        ),
        CompetitorIntel(
            name="Disruptor / new entrant",
            website_url=None,
            why_relevant=(
                "Recently funded entrant aggressively buying short-form video "
                "placements."
            ),
            positioning_angle="Bold, hook-led creative.",
            creative_pattern="Trend-led TikTok formats, paid creator pack.",
            pai_can_learn=(
                "Watch their hook library for emerging openers, but do not "
                "copy the format - their compliance risk is not yours."
            ),
            confidence="medium",
            evidence="hypothesis",
        ),
        CompetitorIntel(
            name="Adjacent-category overlap",
            website_url=None,
            why_relevant=(
                "Brand in an adjacent category competing for the same buyer "
                "attention pocket."
            ),
            positioning_angle="Lifestyle, audience-first storytelling.",
            creative_pattern="Customer-led narrative ads, longer-form content.",
            pai_can_learn=(
                "Useful read on hook territory, not on creative format."
            ),
            confidence="low",
            evidence="hypothesis",
        ),
    ]


def _build_creative_patterns(
    *,
    brand_name: str,
    niche: str,
    competitors: Optional[Sequence[CompetitorIntel]] = None,
) -> list[CreativePattern]:
    """Competitor / market creative-pattern board, enriched with the
    captured competitor-ad proof for the pattern's tag.

    Eight named patterns covering the spectrum of short-form skincare
    creative. Each row spells out who appears to use the pattern, why
    it works, what the brand currently does or doesn't do, and how we
    would adapt it safely.

    Evidence labels:
      - "audit"       -> grounded in the prospect's audit (we observed it)
      - "web_research"-> reasoned from public brand context
      - "hypothesis"  -> assumption pending Meta Ads Library validation

    Ad-evidence rule:
      When `competitors` is supplied (the normal path from
      `StrategyBrief.from_audit_data`), each pattern's `tag` is matched
      against every competitor's `sampled_ads[*].pattern_tags`. Matches
      are attached to the pattern's `ad_evidence` field so the renderer
      can show real screenshots and `Open ad` chips. A pattern with no
      matches keeps an empty `ad_evidence` list - the renderer treats
      that as "needs validation" and tones down the `who_uses` chips
      so we never imply ad-level proof we did not capture.
    """
    base_patterns = _build_creative_patterns_base(
        brand_name=brand_name, niche=niche,
    )
    if not competitors:
        return list(base_patterns)
    return [
        _enrich_pattern(p, competitors)
        for p in base_patterns
    ]


def _enrich_pattern(
    pattern: CreativePattern,
    competitors: Sequence[CompetitorIntel],
) -> CreativePattern:
    """Attach matching sampled ads to a pattern via its `tag`. Returns
    a new frozen instance; the input pattern is untouched.

    Brands listed under `who_uses` that DO appear in `ad_evidence` are
    promoted to verified evidence. Brands that DO NOT appear there
    stay in `who_uses` (the renderer chips them as candidates) so the
    pattern card still names everyone we have reasoning for, but only
    the verified ones get a screenshot.
    """
    from dataclasses import replace
    if not pattern.tag:
        return pattern
    tag = pattern.tag
    matches: list[CompetitorAdProof] = []
    seen_keys: set[tuple[str, str]] = set()
    for c in competitors:
        for ad in c.sampled_ads:
            if tag in ad.pattern_tags:
                key = (c.name, ad.ad_archive_id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                matches.append(ad)
    return replace(pattern, ad_evidence=tuple(matches))


# --------------------------------------------------------------------------- #
# Display-level pattern evidence selection
# --------------------------------------------------------------------------- #


# Pattern-specificity order. Used during de-duplication: each ad gets
# assigned to the FIRST pattern in this list whose tag it carries, so a
# narrow tag (e.g. "before_after_claim", "review_social_proof") wins
# over a broad tag (e.g. "routine_simplification", "editorial_luxury")
# when the same ad would otherwise appear in multiple Section-05 cards.
#
# The intuition: more specific tags require more specific evidence in
# the body copy (e.g. "let's talk skincare" for review_social_proof vs
# the much-more-common word "routine" for routine_simplification), so
# the more-specific tag has a stronger claim on the ad.
_PATTERN_SPECIFICITY_ORDER: tuple[str, ...] = (
    "before_after_claim",
    "founder_expert_credibility",
    "sensitive_skin_reassurance",
    "review_social_proof",
    "discount_led",
    "offer_bundle",
    "ingredient_proof",
    "editorial_luxury",
    "texture_application_demo",
    "routine_simplification",
)


# Strategic relevance order. Used as the final Section-05 ordering
# tiebreaker after (unique competitors desc, unique ads desc). Earlier
# tags are MORE strategically relevant to a sprint-one creative push.
_PATTERN_STRATEGIC_PRIORITY: tuple[str, ...] = (
    "routine_simplification",
    "review_social_proof",
    "ingredient_proof",
    "sensitive_skin_reassurance",
    "founder_expert_credibility",
    "texture_application_demo",
    "offer_bundle",
    "discount_led",
    "editorial_luxury",
    "before_after_claim",
)


# Minimum unique sampled ads required to promote a pattern to Section 05.
# Patterns below this bar surface in "Patterns to validate next" instead.
MIN_ADS_FOR_VALIDATED_PATTERN: int = 2


def select_pattern_evidence(
    patterns: Sequence[CreativePattern],
    *,
    min_ads: int = MIN_ADS_FOR_VALIDATED_PATTERN,
) -> tuple[list[CreativePattern], list[PatternValidationGap]]:
    """Apply the Section-05 evidence rule to a list of enriched patterns.

    Rules (matches the brief):
      1. Each ad (`(competitor_name, ad_archive_id)`) is assigned to its
         single most-specific pattern, so the same screenshot never
         renders in more than one Section-05 card.
      2. A pattern is promoted to Section 05 only when it has at least
         `min_ads` unique sampled ads after de-duplication.
      3. Patterns that fail the bar surface in "Patterns to validate
         next" with a reason code (`no_evidence` / `single_ad` /
         `lost_to_dedup`) and a recommended action.
      4. Section 05 is ordered by (unique competitor count desc, unique
         ad count desc, strategic-priority tag asc).

    Returns `(validated, validate_next)` - two lists the renderer can
    consume directly. The input `patterns` list is not mutated.
    """
    from dataclasses import replace
    if not patterns:
        return [], []

    # Specificity rank lookup (smaller rank = more specific). Tags not
    # in the list get a rank past the end so they still sort but stay
    # at the bottom.
    spec_rank: dict[str, int] = {
        tag: i for i, tag in enumerate(_PATTERN_SPECIFICITY_ORDER)
    }
    fallback_rank = len(_PATTERN_SPECIFICITY_ORDER)

    strat_rank: dict[str, int] = {
        tag: i for i, tag in enumerate(_PATTERN_STRATEGIC_PRIORITY)
    }

    # Order patterns by specificity so the more-specific pattern claims
    # an ad first if multiple patterns match it.
    ordered = sorted(
        list(patterns),
        key=lambda p: spec_rank.get(p.tag or "", fallback_rank),
    )

    claimed: dict[tuple[str, str], str] = {}  # (brand, ad_id) -> claiming pattern tag
    selected_ads: dict[str, list[CompetitorAdProof]] = {p.name: [] for p in patterns}
    original_match_count: dict[str, int] = {p.name: 0 for p in patterns}

    for p in ordered:
        if not p.tag:
            continue
        for ad in p.ad_evidence:
            original_match_count[p.name] = original_match_count.get(p.name, 0) + 1
            key = (ad.competitor_name, ad.ad_archive_id)
            if key in claimed:
                continue
            claimed[key] = p.tag
            selected_ads[p.name].append(ad)

    validated: list[CreativePattern] = []
    validate_next: list[PatternValidationGap] = []

    for p in patterns:
        kept = selected_ads.get(p.name, [])
        ad_count = len(kept)
        comp_count = len({ad.competitor_name for ad in kept})

        if ad_count >= min_ads:
            validated.append(replace(p, ad_evidence=tuple(kept)))
            continue

        # Diagnose WHY this pattern did not clear the bar.
        had_evidence = original_match_count.get(p.name, 0) > 0
        if not had_evidence:
            reason_code = "no_evidence"
            reason_text = (
                "Not enough paid-social proof in the category to prioritize "
                "this in sprint one."
            )
            superseded_by: Optional[str] = None
        elif ad_count == 0:
            # Every supporting ad was claimed by a more-specific pattern.
            reason_code = "lost_to_dedup"
            # Find which pattern claimed at least one of the ads.
            superseded_by = None
            for ad in p.ad_evidence:
                key = (ad.competitor_name, ad.ad_archive_id)
                claiming_tag = claimed.get(key)
                if claiming_tag and claiming_tag != p.tag:
                    by_tag = {p2.tag: p2.name for p2 in patterns if p2.tag}
                    superseded_by = by_tag.get(claiming_tag)
                    break
            reason_text = (
                "Every live ad in the category that touches this pattern "
                "fits a more specific card above more cleanly. We are "
                "showing the proof there instead of duplicating it here."
            )
        else:
            # ad_count == 1
            reason_code = "single_ad"
            reason_text = (
                "We see an early signal for this pattern but only have one "
                "live ad on file. We monitor before committing budget."
            )
            superseded_by = None

        action_code, action_text = _recommend_action_for_pattern(p, reason_code)

        validate_next.append(PatternValidationGap(
            pattern=replace(p, ad_evidence=tuple(kept)),
            current_ad_count=ad_count,
            current_competitor_count=comp_count,
            reason_code=reason_code,
            reason_text=reason_text,
            recommended_action=action_code,
            action_text=action_text,
            superseded_by=superseded_by,
            surviving_ad_evidence=tuple(kept),
        ))

    # Section-05 sort: competitor count desc, ad count desc, strategic-
    # priority tag asc (smaller rank = more relevant).
    def sort_key(p: CreativePattern) -> tuple[int, int, int]:
        comp_count = len({ad.competitor_name for ad in p.ad_evidence})
        ad_count = len(p.ad_evidence)
        return (
            -comp_count,
            -ad_count,
            strat_rank.get(p.tag or "", len(_PATTERN_STRATEGIC_PRIORITY)),
        )
    validated.sort(key=sort_key)

    return validated, validate_next


def _recommend_action_for_pattern(
    pattern: CreativePattern,
    reason_code: str,
) -> tuple[str, str]:
    """Return `(action_code, action_text)` for a pattern that failed
    the Section-05 bar.

    `action_text` is client-facing copy shown next to the action chip
    in Section 05B; it should read as a strategist's note, not a
    capture/scrape system message.
    """
    name_lc = (pattern.name or "").lower()
    if "before" in name_lc and "after" in name_lc:
        return (
            "avoid_for_now",
            "Compliance bar on transformation claims is high. We would "
            "park this until a documented customer story with consent is "
            "in hand.",
        )
    if "discount" in name_lc and reason_code != "single_ad":
        return (
            "avoid_for_now",
            "Off-brand fit for cold prospecting. Better held for "
            "retargeting once we have a second live ad backing it.",
        )
    if reason_code == "lost_to_dedup":
        return (
            "validate_next",
            "Pattern shows up in the category but the live ads we have "
            "on file fit a more specific card above. Worth monitoring "
            "for a clearer example before sprint two.",
        )
    if reason_code == "single_ad":
        return (
            "scrape_more",
            "Early signal worth tracking. A second live ad from another "
            "brand would move this into the main board.",
        )
    if pattern.evidence == "audit":
        return (
            "validate_next",
            "Audit-grounded hypothesis. Worth a second look in the next "
            "evidence pass before we commit production budget.",
        )
    return (
        "scrape_more",
        "Plausible market angle. We would want at least two live ads in "
        "the category before producing against it.",
    )


def _build_creative_patterns_base(
    *, brand_name: str, niche: str,
) -> list[CreativePattern]:
    """Hand-coded base pattern catalogue, without ad-evidence
    enrichment. Each skincare pattern carries a `tag` so the
    enrichment pass can match captured competitor ads to it."""
    profile = _niche_profile(niche)
    if profile == "skincare":
        return [
            CreativePattern(
                name="Review / social-proof stitched UGC",
                who_uses=("UpCircle Beauty", "REN Clean Skincare", "By Sarah London"),
                tag="review_social_proof",
                why_works=(
                    "Reviewer-led voice carries higher trust than studio copy "
                    "and scales easily across audiences."
                ),
                brand_status=(
                    f"{brand_name} does not run UGC-style review video; review "
                    "voice lives only on the PDP today."
                ),
                safe_adaptation=(
                    "Lift on-site review copy (with consent), shoot two 20s "
                    "stitched UGC reads against the strongest serum."
                ),
                risk=(
                    "Never fabricate reviews. Always document consent for any "
                    "lifted quote."
                ),
                evidence="hypothesis",
            ),
            CreativePattern(
                name="Ingredient-proof close-up",
                who_uses=("Aurelia London", "REN Clean Skincare", "Evolve Organic Beauty"),
                tag="ingredient_proof",
                why_works=(
                    "Named single-ingredient + a one-line reason it matters "
                    "out-performs generic clean-beauty wording."
                ),
                brand_status=(
                    "DCO product feeds today; no ingredient-led narrative "
                    "video is live in the sampled library."
                ),
                safe_adaptation=(
                    "25s founder explainer per priority ingredient: why it is "
                    "in, what changed when it was added, no marketing language."
                ),
                risk=(
                    "Avoid clinical-sounding %-strength claims unless supported "
                    "on the PDP."
                ),
                evidence="audit",
            ),
            CreativePattern(
                name="Sensitive-skin reassurance",
                who_uses=("By Sarah London", "Evolve Organic Beauty"),
                tag="sensitive_skin_reassurance",
                why_works=(
                    "Buyers in this segment over-research before purchase. An "
                    "opener that names the reactive-skin pain earns the first "
                    "three seconds."
                ),
                brand_status=(
                    "Audience assumption already names sensitive skin, but no "
                    "creative leads with the reactive-skin promise."
                ),
                safe_adaptation=(
                    "Lived-experience hook ('I stopped trusting skincare for "
                    "a while...') tied to the routine simplification angle."
                ),
                risk=(
                    "Never name medical conditions or claim treatment - keep "
                    "the framing lived-experience."
                ),
                evidence="audit",
            ),
            CreativePattern(
                name="Founder / expert credibility",
                who_uses=("Evolve Organic Beauty", "By Sarah London"),
                tag="founder_expert_credibility",
                why_works=(
                    "Founder-on-camera builds trust faster than anonymous "
                    "brand voice in trust-heavy categories."
                ),
                brand_status=(
                    "No founder-led short-form is currently live in the paid "
                    "library."
                ),
                safe_adaptation=(
                    "25s founder explainer on a single formulation decision, "
                    "no slide cards, no music."
                ),
                risk=(
                    "Founder needs to be on-camera, not behind a logo card. "
                    "Schedule the shoot before committing the route."
                ),
                evidence="hypothesis",
            ),
            CreativePattern(
                name="Texture / application demo",
                who_uses=("REN Clean Skincare", "Aurelia London", "Wildsmith Skin"),
                tag="texture_application_demo",
                why_works=(
                    "Quiet texture close-ups (no music, no voiceover) are a "
                    "powerful scroll-stop in skincare."
                ),
                brand_status=(
                    "Static product photography is in the library, but no "
                    "macro-texture short-form is running."
                ),
                safe_adaptation=(
                    "10-15s macro close-up loop of pump + absorb, sound on, "
                    "no music."
                ),
                risk=(
                    "Resist the urge to add a brand intro card - the texture "
                    "IS the hook."
                ),
                evidence="audit",
            ),
            CreativePattern(
                name="Routine simplification",
                who_uses=("UpCircle Beauty", "By Sarah London"),
                tag="routine_simplification",
                why_works=(
                    "Buyers who reacted to 10-step routines are looking for "
                    "permission to do less. Short routine reads as confidence, "
                    "not minimalism."
                ),
                brand_status=(
                    "Bundles / starter kits exist as SKUs but routine logic "
                    "is not the creative anchor."
                ),
                safe_adaptation=(
                    "Quiet 15s three-step routine cut, no voiceover, on-screen "
                    "step titles only."
                ),
                risk=(
                    "Do not promise 'fixes' - the hook is about restraint, "
                    "not transformation."
                ),
                evidence="hypothesis",
            ),
            CreativePattern(
                name="Offer / bundle ads",
                who_uses=("UpCircle Beauty", "Pai Skincare"),
                tag="offer_bundle",
                why_works=(
                    "Bundles answer the 'where do I start' question for first-"
                    "time buyers. A set is permission-to-try, not just a discount."
                ),
                brand_status=(
                    "One bundle ad is live (Rosehip Reset Bundle) but reads "
                    "as a SKU, not a routine."
                ),
                safe_adaptation=(
                    "Lead with the routine logic, not the saving. Frame as the "
                    "starter set for reactive skin."
                ),
                risk=(
                    "Discount-first reads as bargain skincare and weakens the "
                    "premium / organic positioning."
                ),
                evidence="audit",
            ),
            CreativePattern(
                name="Before / after transformation claim",
                who_uses=("REN Clean Skincare", "industry adjacent"),
                tag="before_after_claim",
                why_works=(
                    "Transformation framing pulls strong CTR, but it sits at "
                    "the wrong end of the compliance spectrum for sensitive-"
                    "skin brands."
                ),
                brand_status=(
                    "Not used today; do not introduce in sprint one."
                ),
                safe_adaptation=(
                    "Quiet, lived-experience after-shot only - no medical "
                    "claims, no aspirational transformation copy."
                ),
                risk=(
                    "Higher compliance risk. Park until we have a customer "
                    "voice with documented consent on a specific lived-"
                    "experience moment."
                ),
                evidence="hypothesis",
            ),
            CreativePattern(
                name="Discount / urgency offer",
                who_uses=("Aurelia London", "Neal's Yard Remedies"),
                tag="discount_led",
                why_works=(
                    "Discount and limited-time framing pulls strong retargeting "
                    "CTR but does little for brand equity on cold prospecting "
                    "traffic - it's a closing-the-loop tactic, not a brand build."
                ),
                brand_status=(
                    "No discount-led creative is live in the sampled library; "
                    "premium positioning is well-protected on cold prospecting."
                ),
                safe_adaptation=(
                    "Reserve discount creative for retargeting only. Frame as "
                    "a routine starter (free travel set with a £60 spend) so "
                    "the cue carries product logic, not bargain language."
                ),
                risk=(
                    "Discount-led cold creative anchors the brand low. Keep "
                    "off the prospecting layer and watch frequency closely."
                ),
                evidence="audit",
            ),
            CreativePattern(
                name="Editorial / heritage tone",
                who_uses=("Evolve Organic Beauty", "By Sarah London"),
                tag="editorial_luxury",
                why_works=(
                    "Editorial copy with named ritual / heritage / craft cues "
                    "earns brand permission in the premium organic tier - it "
                    "reads as 'considered', not 'mass-market'."
                ),
                brand_status=(
                    "Premium organic positioning is consistent with this tone, "
                    "but no editorial / heritage short-form video is currently "
                    "in market."
                ),
                safe_adaptation=(
                    "20-25s editorial cuts that frame a single product as a "
                    "ritual moment. Voice-over restraint, no music, sound on."
                ),
                risk=(
                    "Editorial gets self-indulgent fast. Keep the proof point "
                    "(named ingredient, named routine moment) inside the cut."
                ),
                evidence="hypothesis",
            ),
        ]
    # Default - generic short-form pattern catalogue.
    return [
        CreativePattern(
            name="Review / social-proof stitched UGC",
            who_uses=("Mid-tier independent peer", "Disruptor / new entrant"),
            why_works=(
                "Reviewer-led voice carries higher trust than studio copy."
            ),
            brand_status=(
                f"No UGC-style review video is live for {brand_name} today."
            ),
            safe_adaptation=(
                "Shoot two 20s stitched UGC reads against the strongest SKU."
            ),
            risk="Always document consent for lifted reviews.",
            evidence="hypothesis",
        ),
        CreativePattern(
            name="Product-proof close-up",
            who_uses=("Direct category benchmark", "Adjacent-category overlap"),
            why_works=(
                "Quiet, texture-led close-ups out-scroll glossy brand films."
            ),
            brand_status="Static-only product creative today.",
            safe_adaptation=(
                "10-15s macro close-up loop, sound on, no music."
            ),
            risk="Resist the urge to add a brand intro card.",
            evidence="hypothesis",
        ),
        CreativePattern(
            name="Founder explainer",
            who_uses=("Mid-tier independent peer",),
            why_works="Founder-on-camera trust > anonymous brand voice.",
            brand_status="No founder-led short-form is currently live.",
            safe_adaptation="25s founder explainer on one design choice.",
            risk="Founder needs to be on-camera, not behind a logo card.",
            evidence="hypothesis",
        ),
        CreativePattern(
            name="Discount / urgency ad",
            who_uses=("Direct category benchmark",),
            why_works=(
                "Pulls hot retargeting traffic but does not build brand "
                "equity on cold creative."
            ),
            brand_status="Not currently lead creative.",
            safe_adaptation="Reserve for retargeting, not cold prospecting.",
            risk="Lowers price-anchor on cold buyers.",
            evidence="hypothesis",
        ),
    ]


def _build_avoid_routes(*, niche: str) -> list[AvoidRoute]:
    """Routes we would deliberately NOT prioritise for sprint one.

    Language is intentionally careful: we never say a route will fail.
    We say lower-evidence fit, higher compliance risk, less aligned with
    current proof, or validate later. Each row gives the reader what to
    test instead.
    """
    profile = _niche_profile(niche)
    if profile == "skincare":
        return [
            AvoidRoute(
                name="Overpromised before / after transformation",
                why_tempting=(
                    "Transformation-led hooks pull strong CTR and feel like "
                    "an easy proof point for a result-driven product."
                ),
                why_avoid=(
                    "Higher compliance risk on health-adjacent claims; weakens "
                    "premium / organic positioning. Would validate later with "
                    "restrained framing only."
                ),
                evidence="compliance_risk",
                test_instead=(
                    "Quiet, lived-experience after-shot tied to a specific "
                    "lived moment - never a 'result' claim."
                ),
            ),
            AvoidRoute(
                name="Heavy dermatologist-style authority claim",
                why_tempting=(
                    "Clinical voice reads as credibility and is easy to source "
                    "via white-coat creators."
                ),
                why_avoid=(
                    "Less aligned with the brand's restrained, organic voice; "
                    "compliance bar on clinical / medical claims is high. "
                    "Would validate later with named, real experts only."
                ),
                evidence="off_brand_fit",
                test_instead=(
                    "Founder-led ingredient explainer; lived-experience voice "
                    "carries the same trust at lower risk."
                ),
            ),
            AvoidRoute(
                name="Trend-only TikTok format",
                why_tempting=(
                    "Trend-driven formats (point-at-text, dance-to-product) "
                    "produce cheap reach in the short window the trend lasts."
                ),
                why_avoid=(
                    "Off-brand fit with a premium / considered tone. Short "
                    "half-life means high creative refresh cost. Not a first-"
                    "sprint priority."
                ),
                evidence="off_brand_fit",
                test_instead=(
                    "A separate experiment lane in sprint three after the "
                    "core learning loop is in place."
                ),
            ),
            AvoidRoute(
                name="Aggressive discount-led creative",
                why_tempting=(
                    "Discount openers convert hot retargeting traffic and "
                    "look like an easy ROAS win."
                ),
                why_avoid=(
                    "Weakens the premium / organic positioning and lowers the "
                    "price-anchor on cold buyers. Validate later inside "
                    "retargeting cohorts, not cold prospecting."
                ),
                evidence="off_brand_fit",
                test_instead=(
                    "Bundle / starter-set creative led by routine logic, not "
                    "by saving."
                ),
            ),
            AvoidRoute(
                name="Generic clean-beauty claims",
                why_tempting=(
                    "Clean-beauty language is the category's shared vocabulary "
                    "and is cheap to produce."
                ),
                why_avoid=(
                    "Lower-evidence fit: clean-beauty wording now reads as "
                    "category table-stakes. Specificity out-performs the broad "
                    "claim."
                ),
                evidence="low_evidence",
                test_instead=(
                    "Pick one ingredient or one certification and explain the "
                    "one practical thing it changed."
                ),
            ),
            AvoidRoute(
                name="Overly clinical skin-condition language",
                why_tempting=(
                    "Naming a condition (eczema, rosacea, dermatitis) creates "
                    "an instant audience fit and feels like a credible search-"
                    "intent match."
                ),
                why_avoid=(
                    "Higher compliance risk; ad-platform policy is tight on "
                    "medical / condition language. Not a first-sprint priority."
                ),
                evidence="compliance_risk",
                test_instead=(
                    "Lived-experience phrasing ('reactive skin', 'flares') "
                    "with PDP-backed claims only."
                ),
            ),
            AvoidRoute(
                name="\"Miracle product\" hook",
                why_tempting=(
                    "Single-product-solves-everything hooks are pattern-"
                    "interrupts and easy to write."
                ),
                why_avoid=(
                    "Lower-evidence fit with the brand's restraint; reads as "
                    "bargain skincare even when produced beautifully. Would "
                    "validate later only with a documented case study."
                ),
                evidence="low_evidence",
                test_instead=(
                    "One ingredient, one reason; the proof is the restraint."
                ),
            ),
            AvoidRoute(
                name="Too-polished studio ads",
                why_tempting=(
                    "High-production-value brand films feel premium and reuse "
                    "easily across surfaces."
                ),
                why_avoid=(
                    "UGC-style creative out-performs polished brand films on "
                    "cold audiences in this category. The polish hides the "
                    "product behaviour that scrolls."
                ),
                evidence="validate_later",
                test_instead=(
                    "Quiet, texture-led close-up loops; lived-experience UGC "
                    "first, polished editorial second."
                ),
            ),
        ]
    # Default - smaller generic avoid set.
    return [
        AvoidRoute(
            name="Aggressive discount-led creative",
            why_tempting=(
                "Discount openers convert hot retargeting traffic."
            ),
            why_avoid=(
                "Off-brand fit on cold prospecting; weakens price-anchor."
            ),
            evidence="off_brand_fit",
            test_instead="Bundle / starter-set creative led by routine logic.",
        ),
        AvoidRoute(
            name="\"Miracle product\" hook",
            why_tempting="Pattern-interrupts are easy to write.",
            why_avoid="Lower-evidence fit; reads as bargain even when polished.",
            evidence="low_evidence",
            test_instead="One product, one specific change; restraint as proof.",
        ),
        AvoidRoute(
            name="Trend-only short-form format",
            why_tempting="Cheap reach inside the trend window.",
            why_avoid=(
                "Short half-life; high refresh cost; off-brand fit for a "
                "considered tone."
            ),
            evidence="validate_later",
            test_instead="A separate experiment lane after the core sprint.",
        ),
        AvoidRoute(
            name="Generic category claim",
            why_tempting="Category vocabulary is cheap to produce.",
            why_avoid="Lower-evidence fit; specificity out-performs the broad claim.",
            evidence="low_evidence",
            test_instead="Name one ingredient / one decision; explain the change.",
        ),
        AvoidRoute(
            name="Too-polished studio ad",
            why_tempting="Brand films feel premium and reuse easily.",
            why_avoid=(
                "UGC out-performs polished brand films on cold audiences."
            ),
            evidence="validate_later",
            test_instead="Quiet, texture-led close-up loops first.",
        ),
    ]


# --------------------------------------------------------------------------- #
# Re-export brand-mark helper so the renderer doesn't need to import from
# pitch_builder directly. Keeps the html_strategy_builder import graph tight.
# --------------------------------------------------------------------------- #


def _initials_for(name: str) -> str:
    """Up to 2 uppercase letters from a brand name."""
    return _ad_brand_initial(name)


__all__ = [
    "AvoidRoute",
    "CompetitorAdProof",
    "CompetitorIntel",
    "COMPETITOR_ADS_FILENAME",
    "CreativePattern",
    "ExecutiveCard",
    "HookTerritory",
    "MarketSignal",
    "OpportunityMap",
    "RouteIdea",
    "SprintRecommendation",
    "StrategyAdPattern",
    "StrategyBrief",
    "_competitor_slug",
    "_initials_for",
    "load_competitor_ad_proofs",
]


# Defensive: surface unused-import lints cleanly by referencing every
# helper imported from pitch_builder. They're used in the build steps
# above but ruff sometimes misses chained references through dataclass
# defaults.
_ = (
    _strip_debug_inline,
    Sequence,
)
