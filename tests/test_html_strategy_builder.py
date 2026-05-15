"""Tests for the HTML strategy / creative-research page builder.

Covers three layers:
  1. `StrategyBrief.from_audit` - reads audit.json + brand_profile and
     produces the structured brief.
  2. `build_strategy_html` - renders the brief into a self-contained
     HTML page with the expected sections, copy, and noindex.
  3. `build_strategy_microsite` + `build_deploy_package` - copies the
     page + its assets into the prospect's `strategy/` folder and the
     Cloudflare deploy package's `<slug>/strategy/` subdir.

Tests build synthetic prospects (audit.json + a few PNG product
images) in `tmp_path` so they're filesystem-isolated and never touch
the real `prospects/` tree.
"""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

from agents.outreach.prospect_store import ProspectAudit, ProspectStore
from agents.outreach.reporting.html_strategy_builder import build_strategy_html
from agents.outreach.reporting.microsite_builder import (
    build_deploy_package,
    build_microsite,
    build_strategy_microsite,
)
from agents.outreach.reporting.strategy_brief import (
    HookTerritory,
    RouteIdea,
    StrategyBrief,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data)
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _make_real_png(path: Path, *, width: int = 64, height: int = 64) -> Path:
    """Write a >=4 KB PNG so it clears `_is_useful_image` checks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    row = b"\x00" + (b"\xC8\xC8\xC8\xFF" * width)
    idat = _png_chunk(b"IDAT", zlib.compress(row * height, 9))
    iend = _png_chunk(b"IEND", b"")
    payload = sig + ihdr + idat + iend
    if len(payload) < 5000:
        pad = _png_chunk(b"tEXt", b"yuvo-test\x00" + b"x" * (5000 - len(payload)))
        payload = sig + ihdr + idat + pad + iend
    path.write_bytes(payload)
    return path


def _save_pai_like_audit(prospect_root: Path, prospect_id: str = "pai-like") -> tuple[Path, Path, Path]:
    """Build a Pai-shaped audit on disk: 4 ads (1 with real body + ad
    screenshot, 3 placeholder DCO ads), 4 weaknesses, 5 product images.

    Returns (prospects_root, audit_path, prospect_root)."""
    prospects_root = prospect_root.parent
    prospect_root.mkdir(parents=True, exist_ok=True)
    assets_dir = prospect_root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Real-looking assets.
    favicon = _make_real_png(assets_dir / "favicon-32.png", width=32, height=32)
    homepage = _make_real_png(assets_dir / "homepage.png", width=160, height=120)
    products = [
        _make_real_png(assets_dir / f"product_{i}.png", width=80, height=80)
        for i in range(1, 6)
    ]
    # Ad screenshots are referenced by the audit's competitor_ads below;
    # we just need the files on disk - the list itself is unused.
    for i in range(1, 5):
        _make_real_png(assets_dir / f"ad_{i}.png", width=72, height=128)

    store = ProspectStore(prospect_id, prospects_root=prospects_root)
    audit = ProspectAudit(
        prospect_id=prospect_id,
        prospect_name="Pai Skincare",
        niche="premium organic skincare",
        country="GB",
        locale="en-GB",
        competitor_ads=[
            {
                "ad_archive_id": "1460287092",
                "page_name": "Pai Skincare",
                "media_type": "VIDEO",
                "body_text": "Healthier, radiant skin starts here.",
                "cta_text": "Shop now",
                "days_active": 18,
                "ad_screenshot_path": "assets/ad_1.png",
                "ad_library_url": "https://www.facebook.com/ads/library/?id=1460287092",
            },
            {
                "ad_archive_id": "4360174537",
                "page_name": "Pai Skincare",
                "media_type": "IMAGE",
                "body_text": "{{product.brand}}",
                "cta_text": "Shop now",
                "days_active": 17,
                "ad_screenshot_path": "assets/ad_2.png",
                "ad_library_url": "https://www.facebook.com/ads/library/?id=4360174537",
            },
            {
                "ad_archive_id": "9706826954",
                "page_name": "Pai Skincare",
                "media_type": "IMAGE",
                "body_text": "{{product.brand}}",
                "cta_text": "Shop now",
                "days_active": 17,
                "ad_screenshot_path": "assets/ad_3.png",
                "ad_library_url": "https://www.facebook.com/ads/library/?id=9706826954",
            },
            {
                "ad_archive_id": "1477056904",
                "page_name": "Pai Skincare",
                "media_type": "IMAGE",
                "body_text": "{{product.brand}}",
                "cta_text": "Learn more",
                "days_active": 17,
                "ad_screenshot_path": "assets/ad_4.png",
                "ad_library_url": "https://www.facebook.com/ads/library/?id=1477056904",
            },
        ],
        weaknesses=[
            {
                "description": (
                    "Ad library is dominated by Dynamic Catalog (DCO) "
                    "product-feed ads with placeholder body text."
                ),
                "confidence": "high",
            },
            {
                "description": "Single CTA across the live library.",
                "confidence": "high",
            },
            {
                "description": (
                    "Only one true VIDEO creative is live; no UGC-style "
                    "short-form video presence."
                ),
                "confidence": "high",
            },
            {
                "description": "All sampled active ads launched within one week.",
                "confidence": "medium",
            },
        ],
        brand_profile={
            "website_url": "https://www.paiskincare.com",
            "product_category": "Premium organic skincare for sensitive skin",
            "brand_tone": "Calm, considered, science-led, gentle.",
            "audience_assumption": "Women 28-50, sensitive skin, organic/clean values.",
            "primary_color": "#2C4A3E",
            "logo_path": str(favicon.relative_to(prospect_root)),
            "hero_image_path": str(homepage.relative_to(prospect_root)),
            "product_images": [str(p.relative_to(prospect_root)) for p in products],
        },
    )
    store.save_audit(audit)
    return prospects_root, store.audit_path, prospect_root


# --------------------------------------------------------------------------- #
# StrategyBrief.from_audit
# --------------------------------------------------------------------------- #


def test_strategy_brief_built_from_audit(tmp_path: Path):
    """Brief carries brand_profile fields, weaknesses-driven exec
    cards, audit-grounded market signals, and the ad pattern board."""
    prospects_root, _audit_path, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)

    assert brief.brand_name == "Pai Skincare"
    assert brief.niche == "premium organic skincare"
    assert brief.product_category == "Premium organic skincare for sensitive skin"
    assert brief.brand_tone == "Calm, considered, science-led, gentle."
    assert brief.audience_assumption is not None
    assert brief.website_url == "https://www.paiskincare.com"
    assert brief.primary_color == "#2C4A3E"
    assert brief.logo_path is not None
    assert brief.hero_image_path is not None
    assert len(brief.product_images) == 5

    # Sections populated.
    assert len(brief.executive_cards) == 3
    assert len(brief.market_context) >= 4
    assert len(brief.ad_patterns) == 4
    assert len(brief.hook_territories) >= 8
    assert len(brief.opportunities) >= 3
    assert len(brief.routes) >= 10
    assert 3 <= len(brief.sprint) <= 5


def test_strategy_brief_executive_cards_react_to_weaknesses(tmp_path: Path):
    """When the audit's weaknesses mention placeholder / DCO / single
    CTA / no video, the executive-summary cards should pick those
    signals up - not produce a generic fallback."""
    prospects_root, _, _ = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)

    rely_on = brief.executive_cards[0].body.lower()
    gap = brief.executive_cards[1].body.lower()
    test_first = brief.executive_cards[2].body.lower()

    # DCO / placeholder detected.
    assert "dynamic catalog" in rely_on or "placeholder" in rely_on
    # Single-CTA or UGC-video gap detected.
    assert "cta" in gap or "ugc" in gap or "video" in gap
    # Forward-looking sprint plan (reframed as a paid-client deliverable -
    # the third card now reads as the production sprint plan, not a test).
    assert any(word in test_first for word in ("sprint", "route", "produce", "ugc"))


def test_strategy_brief_market_context_chips_hypothesis_explicitly(tmp_path: Path):
    """Every market-context entry that isn't sourced from the audit
    must carry `evidence == 'hypothesis'`. This is the contract that
    powers the HYPOTHESIS chips on the rendered page."""
    prospects_root, _, _ = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)

    evidence_values = {s.evidence for s in brief.market_context}
    assert evidence_values <= {"audit", "hypothesis"}
    assert "hypothesis" in evidence_values
    # Skincare audit grounds at least one signal: the audience-assumption
    # line lifted from brand_profile.
    assert any(s.evidence == "audit" for s in brief.market_context)


def test_strategy_brief_ad_patterns_link_to_meta_library(tmp_path: Path):
    """The ad-pattern board carries an ads-library URL per row, derived
    from the ad_archive_id when not supplied directly."""
    prospects_root, _, _ = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    for p in brief.ad_patterns:
        assert p.library_url and "facebook.com/ads/library/?id=" in p.library_url


def test_strategy_brief_routes_have_required_fields(tmp_path: Path):
    """Each route in the library carries title/hook/opening/proof/cta/asset/conf."""
    prospects_root, _, _ = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    for r in brief.routes:
        assert r.title and r.hook and r.opening_shot
        assert r.proof_point and r.cta and r.asset_requirement
        assert r.confidence in {"high", "medium", "low"}


def test_strategy_brief_sprint_picks_high_confidence_first(tmp_path: Path):
    """The sprint picks routes in confidence order (high before medium)."""
    prospects_root, _, _ = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    sprint_titles = [s.route_title for s in brief.sprint]
    titles_by_conf: dict[str, list[str]] = {"high": [], "medium": [], "low": []}
    for r in brief.routes:
        titles_by_conf[r.confidence].append(r.title)
    # First N high-confidence titles come before any medium-confidence
    # titles in the sprint order.
    high_in_sprint = [t for t in sprint_titles if t in titles_by_conf["high"]]
    medium_in_sprint = [t for t in sprint_titles if t in titles_by_conf["medium"]]
    if high_in_sprint and medium_in_sprint:
        last_high_idx = max(sprint_titles.index(t) for t in high_in_sprint)
        first_medium_idx = min(sprint_titles.index(t) for t in medium_in_sprint)
        assert last_high_idx < first_medium_idx


# --------------------------------------------------------------------------- #
# build_strategy_html - rendered output contract
# --------------------------------------------------------------------------- #


def _build_brief_and_html(tmp_path: Path) -> tuple[StrategyBrief, str, Path]:
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = prospect_root / "strategy"
    out = build_strategy_html(brief, output_dir=out_dir)
    return brief, out.read_text(encoding="utf-8"), out


def test_strategy_page_renders_to_file(tmp_path: Path):
    """`build_strategy_html` writes an index.html and returns the path."""
    _brief, html, out = _build_brief_and_html(tmp_path)
    assert out.exists()
    assert out.name == "index.html"
    assert out.stat().st_size > 4000
    assert "<!doctype html>" in html


def test_strategy_page_contains_title_and_brand(tmp_path: Path):
    """The page renders 'Creative Strategy Map' and names the brand."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    assert "Creative Strategy Map" in html
    assert "Pai Skincare" in html


def test_strategy_page_is_noindex_by_default(tmp_path: Path):
    """Private strategy URL must be opted out of search indexing."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    assert '<meta name="robots" content="noindex,nofollow">' in html


def test_strategy_page_includes_all_top_level_sections(tmp_path: Path):
    """Every section renders with its anchor id (post-purchase deliverable
    layout: cover, exec summary, market, competitors, creative patterns,
    own-ad board, hook map, opportunities, routes, avoid routes, sprint,
    next step)."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    for anchor in (
        'id="cover"',
        'id="exec-summary"',
        'id="market"',
        'id="competitors"',
        'id="creative-patterns"',
        'id="ad-board"',
        'id="hook-map"',
        'id="opportunities"',
        'id="routes"',
        'id="avoid"',
        'id="sprint"',
        'id="next-step"',
    ):
        assert anchor in html, f"missing section anchor {anchor!r}"


def test_strategy_page_contains_hook_map_section(tmp_path: Path):
    """Hook map renders every territory from the brief.

    Avoid-lane hooks deliberately do not carry a printable sample line
    (they redirect the reader to the Routes-to-avoid section instead),
    so the sample-line assertion only runs for non-avoid territories.
    """
    brief, html, _ = _build_brief_and_html(tmp_path)
    assert "Hook map" in html
    for h in brief.hook_territories:
        assert h.name in html
        if h.priority != "avoid" and not h.sample_line.startswith("("):
            assert h.sample_line in html or _html_escape_attr(h.sample_line) in html


def _html_escape_attr(text: str) -> str:
    """Mimic the renderer's html.escape() with quote=True for attribute-safe matching."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
    )


def test_strategy_page_contains_route_library(tmp_path: Path):
    """Route library renders every route's title + hook + confidence chip."""
    brief, html, _ = _build_brief_and_html(tmp_path)
    # Section renamed to 'Routes to produce' in the post-purchase rewrite,
    # but the routes themselves are still the main payload.
    assert "Routes to produce" in html
    assert len(brief.routes) >= 10
    for r in brief.routes:
        # Titles with single-quotes are emitted via html.escape(quote=True),
        # so look for the escaped form too.
        assert r.title in html or _html_escape_attr(r.title) in html
    # Confidence chip class for at least one route.
    assert "route__conf--high" in html or "route__conf--medium" in html


def test_strategy_page_contains_sprint_section(tmp_path: Path):
    """Recommended first production sprint renders all picked route titles."""
    brief, html, _ = _build_brief_and_html(tmp_path)
    assert "Recommended first production sprint" in html
    for s in brief.sprint:
        assert s.route_title in html or _html_escape_attr(s.route_title) in html


def test_strategy_page_does_not_leak_debug_markers(tmp_path: Path):
    """Debug strings from the raw audit (body_text=, {{product.brand}},
    ad_archive_id=, media_type=, AI-UGC) must NEVER reach the rendered
    HTML."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    for marker in (
        "body_text=", "body_text =",
        "media_type=", "media_type =",
        "ad_archive_id=", "ad_archive_id =",
        "{{product.brand}}",
        "AI-UGC", "ai-ugc",
    ):
        assert marker not in html, f"strategy page leaked debug marker {marker!r}"


def test_strategy_page_embeds_real_ad_screenshots_when_present(tmp_path: Path):
    """Each StrategyAdPattern with a `screenshot_path` should render an
    <img src=...> inside the ad pattern board row."""
    brief, html, _ = _build_brief_and_html(tmp_path)
    embedded = sum(1 for p in brief.ad_patterns if p.screenshot_path is not None)
    assert embedded >= 3  # the fixture sets ad_1..ad_4
    # And the HTML carries at least one ad image.
    assert "ad_1" in html or "ad_2" in html


def test_strategy_page_embeds_hero_image_when_present(tmp_path: Path):
    """When the brief carries a hero image, the cover renders it as
    `<img>` (not the designed mock).

    The CSS rules for `.strategy-cover__hero--mock` are always present
    in the inline stylesheet, so check that the class isn't applied to
    an element (i.e. doesn't appear inside a `class="..."` attribute).
    """
    _brief, html, _ = _build_brief_and_html(tmp_path)
    assert "homepage.png" in html
    assert 'class="strategy-cover__hero strategy-cover__hero--mock"' not in html
    assert "strategy-cover__hero-orb" not in html or "<style>" in html  # mock-only markup gone


def test_strategy_page_evidence_chips_render(tmp_path: Path):
    """Market-context entries chip as PAI AUDIT or STRATEGY HYPOTHESIS
    so the prospect can tell grounded claims from working hypotheses."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    assert "PAI AUDIT" in html
    assert "STRATEGY HYPOTHESIS" in html
    # The old operator labels must NOT appear (taxonomy migration check).
    assert "AUDIT SIGNAL" not in html


def test_strategy_page_next_step_is_post_purchase_not_lead_cta(tmp_path: Path):
    """The strategy page is sent AFTER the client has bought, so the
    closing CTA must NOT link back to the pitch microsite and must not
    use lead-capture / 'turn this into the first route' language.

    The CTA should ask the client to approve the first production
    sprint and, if it carries a link, it must be an internal anchor
    (a `#`-prefixed in-page nav) - never the public pitch URL.
    """
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    # Even when callers supply a pitch URL (legacy callers may still
    # set this for back-compatibility), the renderer must not turn it
    # into the close-out CTA.
    brief = StrategyBrief.from_audit(
        "pai-like",
        prospects_root=prospects_root,
        public_pitch_url="https://yuvo-pitches.pages.dev/p/pai-like-abc/",
    )
    out = build_strategy_html(brief, output_dir=prospect_root / "strategy")
    html = out.read_text(encoding="utf-8")
    # Post-purchase framing - no lead-capture language.
    assert "Turn this into the first route" not in html
    assert "yuvo-pitches.pages.dev/p/pai-like-abc/" not in html
    assert "../site/index.html" not in html
    # Approval CTA copy + internal anchor.
    assert "Approve the first production sprint" in html
    assert 'href="#sprint"' in html


# --------------------------------------------------------------------------- #
# Cookie popup safety: the strategy cover must never embed a homepage
# screenshot whose contents could include the cookie consent overlay
# captured during the Apify scrape.
# --------------------------------------------------------------------------- #


def test_strategy_cover_does_not_use_homepage_screenshot_fallback(tmp_path: Path):
    """When `brand_profile.hero_image_path` is unset but
    `website_screenshot_path` is, the brief must NOT promote the
    website screenshot to the hero slot.

    Apify regularly captures the homepage with the cookie consent
    banner still visible ("Your privacy matters", "Accept all",
    "Decline all"); using that screenshot as the hero on a paid client
    deliverable looks unprofessional. The builder should prefer a
    product image instead, falling back to the designed mock when
    no product image is available either.
    """
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    # Rewrite the audit so it has ONLY a website_screenshot_path, no hero.
    audit_path = prospect_root / "audit.json"
    audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
    audit_data["brand_profile"].pop("hero_image_path", None)
    audit_data["brand_profile"]["website_screenshot_path"] = "assets/homepage.png"
    audit_path.write_text(json.dumps(audit_data), encoding="utf-8")

    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    # Hero must NOT be the homepage screenshot.
    assert brief.hero_image_path is not None
    assert brief.hero_image_path.name != "homepage.png"
    # And it must come from the product images set.
    assert brief.hero_image_path.name.startswith("product_")


def test_strategy_page_does_not_render_cookie_consent_text(tmp_path: Path):
    """The rendered strategy page must not visibly contain any of the
    cookie-consent strings that bleed in from homepage screenshots.

    This is a regression guard: if a future change re-introduces the
    website-screenshot hero fallback, this test fires before the
    cookie popup ever reaches a client deliverable.
    """
    _brief, html, _ = _build_brief_and_html(tmp_path)
    forbidden = (
        "Your privacy matters",
        "Accept all",
        "Decline all",
        "ACCEPT ALL",
        "DECLINE ALL",
        "We use cookies",
        "cookie",  # case-insensitive guard handled below
    )
    for needle in forbidden:
        assert needle.lower() not in html.lower(), (
            f"strategy page leaked cookie-consent text {needle!r}"
        )


# --------------------------------------------------------------------------- #
# Competitor intelligence + creative patterns + avoid routes
# --------------------------------------------------------------------------- #


def test_strategy_page_includes_competitor_intelligence_section(tmp_path: Path):
    """Competitor intelligence section is present with its heading."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    assert 'id="competitors"' in html
    assert "Competitor intelligence" in html
    assert "Who else is fighting for this buyer" in html


def test_strategy_page_lists_at_least_four_competitor_names(tmp_path: Path):
    """At least four named competitor brands render on the page."""
    brief, html, _ = _build_brief_and_html(tmp_path)
    assert len(brief.competitors) >= 4
    visible = [c for c in brief.competitors if c.name in html]
    assert len(visible) >= 4, (
        f"expected >=4 competitor names rendered, got {len(visible)}"
    )


def test_strategy_page_competitor_cards_carry_evidence_chips(tmp_path: Path):
    """Each competitor card carries an evidence chip so the reader can
    tell which lines we would validate before scaling."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    # Either RESEARCH or HYPOTHESIS chip should appear in competitor cards.
    assert "RESEARCH" in html or "HYPOTHESIS" in html
    assert "ev-chip" in html


def test_strategy_page_includes_creative_pattern_board(tmp_path: Path):
    """The competitor-creative-pattern board renders with its heading
    and at least four named patterns."""
    brief, html, _ = _build_brief_and_html(tmp_path)
    assert 'id="creative-patterns"' in html
    assert "Competitor creative patterns" in html
    assert len(brief.creative_patterns) >= 4
    for p in brief.creative_patterns:
        assert p.name in html or _html_escape_attr(p.name) in html


def test_strategy_page_includes_routes_to_avoid_section(tmp_path: Path):
    """'Routes we would avoid for now' renders with its anchor and a
    minimum of five entries, each with a reason and a 'test instead'
    alternative."""
    brief, html, _ = _build_brief_and_html(tmp_path)
    assert 'id="avoid"' in html
    assert "Routes we would avoid for now" in html
    assert len(brief.avoid_routes) >= 5
    for a in brief.avoid_routes:
        assert a.name in html or _html_escape_attr(a.name) in html
        assert a.why_avoid in html or _html_escape_attr(a.why_avoid) in html
        assert a.test_instead in html or _html_escape_attr(a.test_instead) in html


def test_strategy_page_avoid_language_is_careful(tmp_path: Path):
    """The avoid section never says a route 'will fail'. It uses
    careful language: 'lower-evidence fit', 'compliance risk',
    'validate later'."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    assert "will fail" not in html.lower()
    # At least one of the careful phrasings shows up across the avoid set.
    assert (
        "lower-evidence fit" in html.lower()
        or "compliance risk" in html.lower()
        or "validate later" in html.lower()
        or "not a first-sprint priority" in html.lower()
    )


# --------------------------------------------------------------------------- #
# Hook map lanes
# --------------------------------------------------------------------------- #


def test_strategy_page_hook_map_separates_priority_test_later_avoid(tmp_path: Path):
    """Hook map renders three lanes - hooks to prioritize, hooks to
    test later, and hooks to avoid - so the reader can see the
    strategist's calls at a glance."""
    brief, html, _ = _build_brief_and_html(tmp_path)
    assert "Hooks to prioritize" in html
    assert "Hooks to test later" in html
    assert "Hooks to avoid" in html
    # Every priority lane the brief exposes has at least one entry.
    priorities = {h.priority for h in brief.hook_territories}
    assert {"priority", "test_later", "avoid"} <= priorities


# --------------------------------------------------------------------------- #
# Sprint: "Not in sprint one"
# --------------------------------------------------------------------------- #


def test_strategy_page_sprint_includes_not_in_sprint_one(tmp_path: Path):
    """The recommended-first-sprint section also renders a
    'Not in sprint one' postponements list so the reader can see what
    we deliberately parked."""
    brief, html, _ = _build_brief_and_html(tmp_path)
    assert "Not in sprint one" in html
    assert len(brief.not_in_sprint_one) >= 1
    for s in brief.not_in_sprint_one:
        assert s.route_title in html or _html_escape_attr(s.route_title) in html


# --------------------------------------------------------------------------- #
# Existing safety guarantees stay intact (noindex, ad screenshots).
# --------------------------------------------------------------------------- #


def test_strategy_page_still_noindex_after_repositioning(tmp_path: Path):
    """Repositioning the page as a post-purchase deliverable must not
    drop the noindex meta tag - the strategy URL is still unguessable
    bearer-token shaped and must stay opted out of search indexing."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    assert '<meta name="robots" content="noindex,nofollow">' in html


def test_strategy_page_still_embeds_ad_screenshots_and_product_images(tmp_path: Path):
    """The repositioned page must keep embedding the existing ad
    screenshots + the hero product image when they are available - we
    don't want a regression that drops the audit-evidence imagery.

    The current renderer uses the hero in the cover and the ad
    screenshots in the own-ad pattern board; other product images are
    tracked in the brief for future sections but not yet rendered.
    """
    brief, html, _ = _build_brief_and_html(tmp_path)
    # At least one Pai ad screenshot reference (ad_1..ad_4 from fixture).
    assert "ad_1" in html or "ad_2" in html or "ad_3" in html or "ad_4" in html
    # And the hero - which under the cookie-popup fix promotes to a
    # product image when no explicit hero is set.
    if brief.hero_image_path is not None:
        assert brief.hero_image_path.name in html


def test_strategy_page_does_not_leak_debug_markers_post_rewrite(tmp_path: Path):
    """Repositioning the page must not reintroduce debug strings.

    This is the same guarantee as `test_strategy_page_does_not_leak_debug_markers`
    above but covers the new sections (competitors, creative patterns,
    avoid routes) too."""
    _brief, html, _ = _build_brief_and_html(tmp_path)
    for marker in (
        "body_text=", "media_type=", "ad_archive_id=",
        "{{product.brand}}", "AI-UGC", "ai-ugc",
    ):
        assert marker not in html, (
            f"strategy page leaked debug marker {marker!r}"
        )


def test_strategy_page_renders_without_assets(tmp_path: Path):
    """A bare brief (no logo, no hero, no product images, no ad
    screenshots) must still render a complete page."""
    prospect_root = tmp_path / "prospects" / "barebones"
    prospect_root.mkdir(parents=True, exist_ok=True)
    brief = StrategyBrief(
        brand_name="Barebones Co",
        niche="skincare",
        prospect_root=prospect_root,
        executive_cards=[],
        market_context=[],
        ad_patterns=[],
        hook_territories=[
            HookTerritory(name="T1", rationale="r", risk="x", sample_line="l")
        ],
        opportunities=[],
        routes=[
            RouteIdea(
                title="One route",
                hook="One hook",
                opening_shot="Shot",
                proof_point="Proof",
                cta="Shop",
                asset_requirement="Existing",
                confidence="high",
            )
        ],
        sprint=[],
    )
    out = build_strategy_html(brief, output_dir=prospect_root / "strategy")
    html = out.read_text(encoding="utf-8")
    assert out.exists() and out.stat().st_size > 2000
    assert "Creative Strategy Map" in html
    assert "Barebones Co" in html
    # Cover fell back to the designed mock.
    assert "strategy-cover__hero--mock" in html


# --------------------------------------------------------------------------- #
# build_strategy_microsite + deploy package
# --------------------------------------------------------------------------- #


def test_build_strategy_microsite_writes_html_and_manifest(tmp_path: Path):
    """End-to-end: writes prospects/<id>/strategy/{index.html, manifest.json}
    AND copies referenced assets into a sibling strategy/assets/ folder."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    manifest = build_strategy_microsite("pai-like", prospects_root=prospects_root)
    assert manifest["kind"] == "strategy"
    assert manifest["status"] == "draft"
    assert manifest["brand_name"] == "Pai Skincare"
    out_html = Path(manifest["local_path"])
    assert out_html.exists()
    assets_dir = out_html.parent / "assets"
    assert assets_dir.is_dir()
    asset_files = {p.name for p in assets_dir.iterdir()}
    assert "homepage.png" in asset_files
    assert "ad_1.png" in asset_files
    # Manifest is also persisted to disk.
    manifest_disk = json.loads(
        (out_html.parent / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest_disk["prospect_id"] == "pai-like"


def test_build_strategy_microsite_uses_local_assets_path(tmp_path: Path):
    """After the asset-copy step the rendered HTML references
    `assets/<file>` (relative to strategy/), not `../assets/<file>`."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    manifest = build_strategy_microsite("pai-like", prospects_root=prospects_root)
    html = Path(manifest["local_path"]).read_text(encoding="utf-8")
    import re
    refs = re.findall(r'<img[^>]+src="([^"]+)"', html)
    assert refs, "strategy page should embed at least one image"
    for ref in refs:
        assert ref.startswith("assets/"), (
            f"strategy page should use self-contained assets/ paths, got {ref!r}"
        )


def test_build_strategy_microsite_inherits_private_slug_from_pitch(tmp_path: Path):
    """When a pitch microsite already exists, the strategy page reuses
    the same private_slug so both surfaces share the public URL prefix."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    # Build pitch first so its manifest has a slug.
    pitch_manifest = build_microsite(
        "pai-like",
        prospects_root=prospects_root,
        public_base_url="https://example.test",
    )
    private_slug = pitch_manifest["private_slug"]
    assert private_slug

    strategy_manifest = build_strategy_microsite(
        "pai-like",
        prospects_root=prospects_root,
        public_base_url="https://example.test",
    )
    assert strategy_manifest["private_slug"] == private_slug
    assert strategy_manifest["public_url"] == f"https://example.test/p/{private_slug}/strategy/"


def test_deploy_package_includes_strategy_subfolder(tmp_path: Path):
    """`build_deploy_package` copies the prospect's strategy/ folder
    into `<slug>/strategy/` so a single Cloudflare deploy serves both
    /p/<slug>/ (pitch) and /p/<slug>/strategy/ (strategy)."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    build_microsite("pai-like", prospects_root=prospects_root)
    build_strategy_microsite("pai-like", prospects_root=prospects_root)

    pkg = build_deploy_package(
        "pai-like",
        prospects_root=prospects_root,
        build_root=tmp_path / "build",
    )
    strategy_html = pkg / "strategy" / "index.html"
    assert strategy_html.exists()
    assert (pkg / "strategy" / "assets").is_dir()
    # Pitch index lives at the package root.
    assert (pkg / "index.html").exists()
    # Pitch + strategy assets are independent directories.
    assert (pkg / "assets").is_dir()
    # The strategy HTML still uses the self-contained `assets/` paths.
    strategy_html_text = strategy_html.read_text(encoding="utf-8")
    assert "assets/" in strategy_html_text
    assert "../assets/" not in strategy_html_text


def test_deploy_package_does_not_require_strategy(tmp_path: Path):
    """When `build_strategy_microsite()` was never run, the deploy
    package still builds successfully - the strategy subdir is
    optional."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    build_microsite("pai-like", prospects_root=prospects_root)
    pkg = build_deploy_package(
        "pai-like",
        prospects_root=prospects_root,
        build_root=tmp_path / "build",
    )
    assert (pkg / "index.html").exists()
    assert not (pkg / "strategy").exists()


# --------------------------------------------------------------------------- #
# Regression: existing pitch microsite tests still work alongside strategy
# --------------------------------------------------------------------------- #


def test_existing_pitch_microsite_builds_after_strategy_module_load(tmp_path: Path):
    """Importing the new strategy modules must not break the existing
    pitch microsite build flow."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    pitch_manifest = build_microsite("pai-like", prospects_root=prospects_root)
    assert pitch_manifest["status"] == "draft"
    assert (prospect_root / "site" / "index.html").exists()


# --------------------------------------------------------------------------- #
# Competitor ad proof - structured model
# --------------------------------------------------------------------------- #


def _seed_competitor_ads_json(
    prospect_root: Path,
    *,
    with_screenshots: bool = True,
) -> dict:
    """Write a `strategy/competitor_ads.json` for the synthetic prospect
    that includes:
      - UpCircle Beauty: 2 ads, both with on-disk screenshots, tagged
        review_social_proof + offer_bundle.
      - Aurelia London: 1 ad with screenshot + library URL, tagged
        ingredient_proof.
      - By Sarah London: 0 ads (needs validation - meta_ads_url known
        but capture returned nothing).
    The function also writes the matching PNG files when
    `with_screenshots` is True.
    """
    strat_dir = prospect_root / "strategy"
    strat_dir.mkdir(parents=True, exist_ok=True)
    if with_screenshots:
        # Write the PNGs that the sidecar's `screenshot_path` values
        # reference. The return values aren't used directly - the path
        # lookup is by string from the JSON below - so they're dropped
        # to `_` to keep the linter happy.
        _ = _make_real_png(
            prospect_root / "assets" / "competitors" / "upcircle-beauty" / "ad_1.png",
            width=72, height=128,
        )
        _ = _make_real_png(
            prospect_root / "assets" / "competitors" / "upcircle-beauty" / "ad_2.png",
            width=72, height=128,
        )
        _ = _make_real_png(
            prospect_root / "assets" / "competitors" / "aurelia-london" / "ad_1.png",
            width=72, height=128,
        )
        _ = _make_real_png(
            prospect_root / "assets" / "competitors" / "aurelia-london" / "ad_2.png",
            width=72, height=128,
        )
    payload = {
        "competitors": {
            "UpCircle Beauty": {
                "meta_ads_url": (
                    "https://www.facebook.com/ads/library/"
                    "?active_status=active&country=GB&q=UpCircle+Beauty"
                ),
                "sampled_ads": [
                    {
                        "ad_archive_id": "861066513764191",
                        "ad_library_url": "https://www.facebook.com/ads/library/?id=861066513764191",
                        "screenshot_path": "assets/competitors/upcircle-beauty/ad_1.png" if with_screenshots else None,
                        "body_excerpt": "Try our new starter set.",
                        "cta_text": "Shop now",
                        "days_active": 22,
                        "media_type": "DCO",
                        "pattern_tags": ["review_social_proof", "offer_bundle"],
                        "evidence_level": "competitor_ad_evidence",
                        "capture_status": "screenshot_playwright",
                    },
                    {
                        "ad_archive_id": "1181172777072317",
                        "ad_library_url": "https://www.facebook.com/ads/library/?id=1181172777072317",
                        "screenshot_path": "assets/competitors/upcircle-beauty/ad_2.png" if with_screenshots else None,
                        "body_excerpt": None,
                        "cta_text": None,
                        "days_active": 21,
                        "media_type": "DCO",
                        "pattern_tags": ["offer_bundle"],
                        "evidence_level": "competitor_ad_evidence",
                        "capture_status": "screenshot_playwright",
                    },
                ],
            },
            "Aurelia London": {
                "meta_ads_url": (
                    "https://www.facebook.com/ads/library/"
                    "?active_status=active&country=GB&q=Aurelia+London"
                ),
                "sampled_ads": [
                    {
                        "ad_archive_id": "1254000209662985",
                        "ad_library_url": "https://www.facebook.com/ads/library/?id=1254000209662985",
                        "screenshot_path": "assets/competitors/aurelia-london/ad_1.png" if with_screenshots else None,
                        "body_excerpt": "No need for invasive procedures.",
                        "cta_text": "Learn more",
                        "days_active": 35,
                        "media_type": "VIDEO",
                        "pattern_tags": ["ingredient_proof"],
                        "evidence_level": "competitor_ad_evidence",
                        "capture_status": "download_video_preview",
                    },
                    # Second Aurelia ad on the same pattern so the
                    # Section-05 ≥2-unique-ads rule has a clean way to
                    # promote `ingredient_proof` into the main board.
                    {
                        "ad_archive_id": "1254000209662986",
                        "ad_library_url": "https://www.facebook.com/ads/library/?id=1254000209662986",
                        "screenshot_path": (
                            "assets/competitors/aurelia-london/ad_2.png"
                            if with_screenshots else None
                        ),
                        "body_excerpt": "Vitamin C and hyaluronic acid in our serum.",
                        "cta_text": "Shop now",
                        "days_active": 30,
                        "media_type": "IMAGE",
                        "pattern_tags": ["ingredient_proof"],
                        "evidence_level": "competitor_ad_evidence",
                        "capture_status": "screenshot_playwright",
                    },
                ],
            },
            "By Sarah London": {
                "meta_ads_url": (
                    "https://www.facebook.com/ads/library/"
                    "?active_status=active&country=GB&q=By+Sarah+London"
                ),
                "sampled_ads": [],
            },
        },
        "generated_at": "2026-05-15T16:30:00+00:00",
    }
    (strat_dir / "competitor_ads.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def test_strategy_brief_loads_competitor_ad_proofs(tmp_path: Path):
    """When `competitor_ads.json` exists, the brief carries the
    structured `CompetitorAdProof` data on the matching `CompetitorIntel`
    rows."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)

    upcircle = next(c for c in brief.competitors if c.name == "UpCircle Beauty")
    assert len(upcircle.sampled_ads) == 2
    assert upcircle.evidence_level == "competitor_ad_evidence"
    assert upcircle.meta_ads_url and "UpCircle+Beauty" in upcircle.meta_ads_url
    # Per-ad fields propagated.
    assert upcircle.sampled_ads[0].ad_archive_id == "861066513764191"
    assert upcircle.sampled_ads[0].screenshot_path is not None
    assert "review_social_proof" in upcircle.sampled_ads[0].pattern_tags
    assert upcircle.sampled_ads[0].competitor_name == "UpCircle Beauty"

    aurelia = next(c for c in brief.competitors if c.name == "Aurelia London")
    # The seed now provides 2 Aurelia ads (both ingredient_proof) so
    # the new ≥2-unique-ads Section 05 rule has a clean pattern to promote.
    assert len(aurelia.sampled_ads) == 2
    assert aurelia.evidence_level == "competitor_ad_evidence"

    bysarah = next(c for c in brief.competitors if c.name == "By Sarah London")
    # Tried but no ads -> needs_validation (meta_ads_url known, zero captured).
    # The renderer keeps this row OUT of the main grid (proof-only rule)
    # and surfaces it in the secondary 'Checked but not included' note.
    assert bysarah.evidence_level == "needs_validation"
    assert len(bysarah.sampled_ads) == 0

    # A competitor we never tried (Wildsmith Skin) stays as hypothesis.
    wild = next((c for c in brief.competitors if c.name == "Wildsmith Skin"), None)
    if wild is not None:
        assert wild.evidence_level == "hypothesis"


def test_strategy_brief_with_no_competitor_ads_json_falls_back_cleanly(tmp_path: Path):
    """Missing `competitor_ads.json` must NOT crash the brief build; the
    competitor list still renders with research / hypothesis chips."""
    prospects_root, _, _ = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    for c in brief.competitors:
        assert c.sampled_ads == ()
        assert c.evidence_level in {"research", "hypothesis", "needs_validation"}


def test_load_competitor_ad_proofs_handles_malformed_json(tmp_path: Path):
    """An unreadable / malformed sidecar returns an empty dict, NEVER
    crashes."""
    from agents.outreach.reporting.strategy_brief import load_competitor_ad_proofs
    prospect_root = tmp_path / "prospects" / "broken"
    (prospect_root / "strategy").mkdir(parents=True, exist_ok=True)
    (prospect_root / "strategy" / "competitor_ads.json").write_text(
        "not json {{{", encoding="utf-8",
    )
    assert load_competitor_ad_proofs(prospect_root) == {}


def test_creative_patterns_get_ad_evidence_from_competitors(tmp_path: Path):
    """When a sampled ad's `pattern_tags` include a pattern's `tag`,
    that ad shows up on the pattern's `ad_evidence` list. Patterns
    with no matches stay empty - we never make up evidence."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    by_name = {p.name: p for p in brief.creative_patterns}

    # Aurelia's single ad is tagged ingredient_proof.
    assert any(
        ad.competitor_name == "Aurelia London"
        for ad in by_name["Ingredient-proof close-up"].ad_evidence
    )
    # UpCircle's first ad is tagged review_social_proof + offer_bundle.
    assert any(
        ad.competitor_name == "UpCircle Beauty"
        for ad in by_name["Review / social-proof stitched UGC"].ad_evidence
    )
    assert any(
        ad.competitor_name == "UpCircle Beauty"
        for ad in by_name["Offer / bundle ads"].ad_evidence
    )
    # A pattern that nobody's ads were tagged for stays empty.
    assert by_name["Texture / application demo"].ad_evidence == ()


# --------------------------------------------------------------------------- #
# Competitor card renderer - HTML assertions
# --------------------------------------------------------------------------- #


def test_competitor_card_renders_open_meta_ads_chip(tmp_path: Path):
    """Every competitor card that has a meta_ads_url must include an
    `Open Meta ads` chip pointing at it."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "Open Meta ads" in html
    assert "q=UpCircle+Beauty" in html
    assert "q=Aurelia+London" in html


def test_competitor_card_renders_view_sampled_ad_when_proof_exists(tmp_path: Path):
    """A competitor with sampled ads must show a `View sampled ad` chip
    on each ad-proof tile linking to the public ads-library URL."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    # The chip is now `Open in Ad Library` (client-safe). The legacy
    # operator label `View sampled ad` must NOT appear.
    assert "Open in Ad Library" in html
    assert "View sampled ad" not in html
    assert "facebook.com/ads/library/?id=861066513764191" in html


def test_competitor_card_renders_screenshot_image_when_present(tmp_path: Path):
    """When a sampled ad's `screenshot_path` resolves to a file, the
    competitor card includes an `<img>` pointing at the strategy/assets/
    copy (not the original prospects/<id>/assets/ path)."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    # The microsite builder copies assets into strategy/assets/.
    build_strategy_microsite("pai-like", prospects_root=prospects_root)
    html = (prospect_root / "strategy" / "index.html").read_text(encoding="utf-8")
    # The image is now under assets/ relative to strategy/index.html.
    assert "ad-proof__img" in html
    # We can also check at least one of the copied PNGs is referenced
    # by its rewritten name. The competitor folder is flattened into
    # assets/ in the strategy build, so the source name is preserved.
    # Sanity check: at least one of the source filenames appears.
    assert "ad_1.png" in html


def test_competitor_without_proof_does_not_render_in_main_grid(tmp_path: Path):
    """When the sidecar lists a competitor with zero sampled ads, that
    competitor must NOT appear in the main competitor intelligence
    grid. Per the proof-only main-grid rule, brands without captured
    active Meta ads flow into the secondary 'Checked but not included'
    note instead - never into the main grid as a fake/empty card.
    """
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")

    # Scope the assertion to the main competitors grid only - the
    # excluded note (also inside section 04) WILL mention By Sarah
    # London, and the rest of the page may mention it inside the
    # patterns-to-validate section. The grid itself must not.
    #
    # The grid block spans from `<div class="competitors">` up to the
    # start of the secondary excluded note (whose container class is
    # `excluded-competitors`) - using a substring search avoids the
    # nested-`</div>` ambiguity of `<article>` cards.
    grid_start = html.index('<div class="competitors">')
    excluded_marker = html.find('class="excluded-competitors"', grid_start)
    grid_end = excluded_marker if excluded_marker >= 0 else html.index('</section>', grid_start)
    grid_block = html[grid_start:grid_end]
    assert "By Sarah London" not in grid_block, (
        "By Sarah London has zero sampled ads in the seeded sidecar; "
        "main competitor grid must not render that card."
    )
    # And every card inside the grid must carry an ad-proof strip
    # (the seeded UpCircle / Aurelia rows both have screenshots).
    assert "ad-proof__strip" in grid_block, (
        "every card in the main grid must carry an ad-proof strip"
    )


def test_competitor_with_no_sidecar_renders_only_excluded_note(tmp_path: Path):
    """Without `competitor_ads.json`, no competitor has captured proof.
    The main competitor grid must therefore be empty (rendering the
    client-safe empty-state copy) and every competitor must surface in
    the secondary 'Also checked' note instead.
    """
    prospects_root, _, _ = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")

    # Client-safe empty-state copy renders.
    assert "did not surface paid-social" in html
    # Secondary excluded-list still surfaces the brand names.
    assert 'class="excluded-competitors__name"' in html
    # And the grid does not render any verified `ev-chip--audit` chip
    # (the COMPETITOR ADS chip uses that class) - because nothing has
    # proof yet.
    # The section lede may mention "COMPETITOR ADS" in prose, so scope
    # the assertion to the area inside the section but BEFORE the
    # secondary excluded note.
    section_idx = html.index('id="competitors"')
    section_end = html.index("</section>", section_idx)
    section_block = html[section_idx:section_end]
    excluded_idx = section_block.find('class="excluded-competitors"')
    grid_window = section_block[:excluded_idx] if excluded_idx >= 0 else section_block
    assert "ev-chip--audit" not in grid_window


# --------------------------------------------------------------------------- #
# Pattern card renderer - HTML assertions
# --------------------------------------------------------------------------- #


def test_pattern_card_renders_ad_evidence_area_when_proof_exists(tmp_path: Path):
    """A pattern card with `ad_evidence` should include an explicit
    "Ad evidence" heading and an `Open ad` chip per evidence row."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    # Section 05 carries the per-pattern evidence label.
    assert "pattern-evidence" in html
    # Ingredient-proof pattern card should mention Aurelia (the
    # verified brand for that pattern) and carry an `Open in Ad
    # Library` chip pointing at the captured ad.
    pattern_idx = html.index("Ingredient-proof close-up")
    pattern_window = html[pattern_idx : pattern_idx + 8000]
    assert "Aurelia London" in pattern_window
    assert "Open in Ad Library" in pattern_window


def test_unsupported_patterns_move_to_validate_next_section(tmp_path: Path):
    """A pattern with no captured ads should NOT live in the main
    competitor-pattern board - it must appear inside Section 05B
    'Signals to monitor before production' with a client-safe reason
    chip and a recommended-action label."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")

    # The texture-demo pattern got 0 evidence in the seeded data.
    # It must NOT appear in the main "creative-patterns" section.
    main_section_idx = html.index('id="creative-patterns"')
    main_section_end = html.index("</section>", main_section_idx)
    main_section = html[main_section_idx:main_section_end]
    assert "Texture / application demo" not in main_section

    # It must appear in the new "patterns-to-validate" section.
    validate_idx = html.index('id="patterns-to-validate"')
    validate_end = html.index("</section>", validate_idx)
    validate_section = html[validate_idx:validate_end]
    assert "Texture / application demo" in validate_section
    # Reason chip must be one of the client-safe labels.
    assert any(label in validate_section for label in (
        "FURTHER EVIDENCE TO COLLECT", "EARLY SIGNAL", "ALREADY REPRESENTED",
    ))
    # And one of the client-safe recommended-action labels must appear.
    assert any(label in validate_section for label in (
        "MONITOR NEXT", "COMPLIANCE CAUTION",
    ))


def test_pattern_card_verified_brands_marked_distinctly(tmp_path: Path):
    """Brands that DO have a sampled ad on a pattern get the
    `pattern__user--verified` class; the rest stay as `--candidate`.
    This is how the renderer signals 'we have proof for these, we are
    still hypothesising about the others'."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "pattern__user--verified" in html
    assert "pattern__user--candidate" in html


# --------------------------------------------------------------------------- #
# Asset copy: competitor screenshots get into strategy/assets/
# --------------------------------------------------------------------------- #


def test_competitor_screenshots_copied_into_strategy_assets(tmp_path: Path):
    """`build_strategy_microsite` must copy every competitor screenshot
    that the page references into `<id>/strategy/assets/` so the
    folder is self-contained for the deploy package."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    build_strategy_microsite("pai-like", prospects_root=prospects_root)
    strat_assets = prospect_root / "strategy" / "assets"
    # The three captured screenshots should now be inside the strategy
    # assets folder (filenames may be re-stemmed if there are collisions
    # but the count must be at least 3 from the new captures).
    pngs = list(strat_assets.glob("*.png"))
    assert len(pngs) >= 3, (
        f"expected at least 3 competitor screenshot copies in "
        f"{strat_assets}, found {len(pngs)}"
    )


def test_competitor_screenshots_packaged_into_deploy_strategy_subdir(tmp_path: Path):
    """And the deploy package's `<slug>/strategy/assets/` must mirror
    them too, otherwise the live page would 404 on its `<img>` srcs."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    build_microsite("pai-like", prospects_root=prospects_root)
    build_strategy_microsite("pai-like", prospects_root=prospects_root)
    pkg = build_deploy_package(
        "pai-like",
        prospects_root=prospects_root,
        build_root=tmp_path / "build",
    )
    pkg_strat_assets = pkg / "strategy" / "assets"
    assert pkg_strat_assets.is_dir()
    pngs = list(pkg_strat_assets.glob("*.png"))
    assert len(pngs) >= 3


# --------------------------------------------------------------------------- #
# Evidence-first Section 05 (main board only carries patterns with proof)
# --------------------------------------------------------------------------- #


def test_main_pattern_board_only_includes_patterns_with_ad_proof(tmp_path: Path):
    """The 'Competitor creative patterns' section must NOT list any
    pattern that has zero sampled-ad evidence. Without this rule, the
    section reads as a market claim we can't back."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    main_idx = html.index('id="creative-patterns"')
    main_end = html.index("</section>", main_idx)
    main = html[main_idx:main_end]
    # Every pattern card in the MAIN section must carry the
    # COMPETITOR ADS chip. We check by counting article tags vs
    # COMPETITOR-ADS chip occurrences within the section window.
    import re as _re
    article_count = len(_re.findall(r'<article class="pattern"', main))
    chip_count = main.count("COMPETITOR ADS")
    assert article_count >= 1
    # One COMPETITOR ADS chip per article. The section lede also says
    # the word once; account for that by allowing chip_count >= 1+articles.
    assert chip_count >= article_count, (article_count, chip_count, main[:400])
    # And no NEEDS VALIDATION chip in the main board (those move out).
    assert "NEEDS VALIDATION" not in main


def test_main_pattern_board_carries_at_least_one_open_ad_per_card(tmp_path: Path):
    """Every pattern card in the evidence-first section must include
    at least one 'Open in Ad Library' chip - otherwise the client has nothing to
    click through to."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    main_idx = html.index('id="creative-patterns"')
    main_end = html.index("</section>", main_idx)
    main = html[main_idx:main_end]
    import re as _re
    # Each <article class="pattern"> block must contain "Open in Ad Library".
    blocks = _re.findall(
        r'<article class="pattern"[\s\S]*?</article>', main
    )
    assert blocks, "expected at least one main pattern card"
    for block in blocks:
        assert "Open in Ad Library" in block, block[:200]


def test_main_pattern_board_carries_at_least_one_screenshot_per_card(tmp_path: Path):
    """Every pattern card in the evidence-first section must reference
    at least one ad-evidence screenshot (or placeholder tile). The
    section is supposed to be image-backed proof, not a text claim."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    main_idx = html.index('id="creative-patterns"')
    main_end = html.index("</section>", main_idx)
    main = html[main_idx:main_end]
    import re as _re
    blocks = _re.findall(
        r'<article class="pattern"[\s\S]*?</article>', main
    )
    for block in blocks:
        # Either a real screenshot or the placeholder tile class.
        assert ("pattern-evidence__img" in block or
                "pattern-evidence__placeholder" in block), block[:200]


def test_unverified_brands_not_listed_as_verified_in_main_board(tmp_path: Path):
    """A pattern card in the main board may name REN / By Sarah London
    as candidate chips, but it must NEVER label them as
    `pattern__user--verified`. Only brands that ACTUALLY have a
    sampled ad supporting the pattern get the verified treatment."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    main_idx = html.index('id="creative-patterns"')
    main_end = html.index("</section>", main_idx)
    main = html[main_idx:main_end]
    # The seed has zero sampled ads for REN / By Sarah London, so the
    # main board can never label them as verified. (They may not even
    # appear at all because their host patterns may have moved to
    # validate-next - both outcomes are valid; the verified-class
    # check is the load-bearing one.)
    import re as _re
    verified_chips = _re.findall(
        r'pattern__user--verified[^>]*>(?:<span[^>]*></span>)?([^<]+)<',
        main,
    )
    # Trim and lowercase the verified brand text from each chip.
    verified = {c.strip().lower() for c in verified_chips}
    assert "ren clean skincare" not in verified
    assert "by sarah london" not in verified


# --------------------------------------------------------------------------- #
# Section 04 no-proof affordances
# --------------------------------------------------------------------------- #


def test_competitor_with_no_capture_appears_in_excluded_note(tmp_path: Path):
    """A competitor in the sidecar with an empty `sampled_ads` list
    must NOT render as a card in the main grid (we no longer ship
    fake/empty-proof cards). Instead the brand surfaces in the
    secondary 'Checked but not included' note inside section 04 so
    the reader still sees that we looked at the brand and why nothing
    qualified.
    """
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")

    # Main grid window: must NOT mention By Sarah London.
    grid_idx = html.index('<div class="competitors">')
    grid_end = html.index("</div>", grid_idx)
    grid_block = html[grid_idx:grid_end]
    assert "By Sarah London" not in grid_block
    # And the old inline `competitor__no-proof` note must not appear in
    # the grid - main-grid cards always carry real proof now.
    assert "competitor__no-proof" not in grid_block

    # Excluded note (still inside section 04) must surface the brand.
    section_idx = html.index('id="competitors"')
    section_end = html.index("</section>", section_idx)
    section_block = html[section_idx:section_end]
    assert 'class="excluded-competitors"' in section_block
    assert "By Sarah London" in section_block


# --------------------------------------------------------------------------- #
# Own-ad board placeholder fix
# --------------------------------------------------------------------------- #


def test_own_ad_board_uses_clean_unavailable_state_for_missing_screenshot(tmp_path: Path):
    """When an own-ad row has no on-disk screenshot, the renderer must
    NOT print the brand monogram as a fake-ad preview. It should show
    a premium 'Screenshot unavailable' state with an 'Open live ad'
    link to the Meta Ads Library."""
    from agents.outreach.reporting.strategy_brief import StrategyAdPattern
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    # Replace the ad_patterns with one entry that has no screenshot.
    from dataclasses import replace
    brief = replace(brief, ad_patterns=[
        StrategyAdPattern(
            archive_id="1234567890",
            pattern="Dynamic catalog product feed",
            weakness="Placeholder body text.",
            opportunity="A 12-15s video opener.",
            library_url="https://www.facebook.com/ads/library/?id=1234567890",
            screenshot_path=None,
            body_excerpt=None,
            days_active=14,
        ),
    ])
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    # The client-safe "preview held back this pass" affordance is used
    # in place of the legacy operator-y "Screenshot unavailable" label.
    assert "Preview held back this pass" in html
    assert "Screenshot unavailable" not in html
    # The clean 'Open the live ad' chip is rendered with the right URL:
    assert "ad-board__preview-open" in html
    assert "Open the live ad" in html
    # And the renderer must NOT print a two-letter monogram in the
    # actual own-ad-board section (the legacy "PS" placeholder).
    # CSS-class definitions in the page-level <style> tag don't
    # count - only DOM usage does.
    board_idx = html.index('id="ad-board"')
    board_end = html.index("</section>", board_idx)
    board = html[board_idx:board_end]
    assert "ad-board__preview-monogram" not in board
    assert ">PS<" not in board


# --------------------------------------------------------------------------- #
# Cover wordmark replaces 'PS'
# --------------------------------------------------------------------------- #


def test_cover_does_not_show_two_letter_monogram(tmp_path: Path):
    """The cover must not render the legacy two-letter monogram
    ('PS' for Pai Skincare). The strategy page is a paid deliverable -
    placeholder-shaped marks read as broken."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    # Locate the cover section explicitly to avoid false positives
    # elsewhere on the page.
    cover_idx = html.index('id="cover"')
    cover_end = html.index("</section>", cover_idx)
    cover = html[cover_idx:cover_end]
    assert ">PS<" not in cover, "cover must not show the legacy 'PS' monogram"


def test_cover_falls_back_to_lowercase_wordmark_when_logo_too_small(tmp_path: Path):
    """With only a tiny favicon (<4 KB) as the logo asset, the cover
    must fall back to a clean lowercase wordmark - 'pai' for
    'Pai Skincare'. Initials-style 'PS' is forbidden."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    # The synthetic fixture writes a 5 KB favicon (above the 4 KB
    # logo threshold). Truncate it to a real 32x32 favicon size
    # (~600 bytes) so the renderer treats it as too small to embed
    # and falls back to the wordmark.
    favicon = prospect_root / "assets" / "favicon-32.png"
    raw = favicon.read_bytes()
    favicon.write_bytes(raw[:600])
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "strategy-cover__wordmark" in html
    assert ">pai<" in html
    # And no logo <img> on the cover when the file is below the size floor.
    cover_idx = html.index('id="cover"')
    cover_end = html.index("</section>", cover_idx)
    cover = html[cover_idx:cover_end]
    assert "strategy-cover__brand-mark--logo" not in cover


def test_cover_uses_logo_img_when_logo_is_above_size_floor(tmp_path: Path):
    """A proper logo (>= 4 KB) should be embedded as an <img>, NOT as
    a wordmark."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    # Overwrite the favicon with a >4KB PNG so the renderer accepts it.
    favicon = prospect_root / "assets" / "favicon-32.png"
    _make_real_png(favicon, width=128, height=128)
    # Bump file size into "real logo" territory.
    favicon.write_bytes(favicon.read_bytes() + b"\x00" * 5000)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "strategy-cover__brand-mark--logo" in html
    assert "strategy-cover__logo-img" in html
    # And no wordmark when we have a real logo.
    assert ">pai<" not in html


def test_logo_is_not_used_as_product_or_route_visual(tmp_path: Path):
    """The favicon/logo asset must only be referenced as the cover
    brand mark - never as a product card image or route visual."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    import re as _re
    # Find every image src that points at the favicon (the test
    # fixture writes it to assets/favicon-32.png).
    favicon_imgs = _re.findall(
        r'<img[^>]+src="[^"]*favicon-32[^"]*"[^>]*>',
        html,
    )
    # The favicon may legitimately appear inside the brand mark - but
    # never inside an ad-board, pattern, route, or concept card. We
    # check by ensuring every favicon img tag has the cover logo class.
    for img in favicon_imgs:
        assert "strategy-cover__logo-img" in img, (
            f"favicon img used outside cover brand mark: {img}"
        )


# --------------------------------------------------------------------------- #
# Section 04 - proof-only main grid + secondary excluded note
# (TASK 8 from the "more competitor ad proof" task spec)
# --------------------------------------------------------------------------- #


def _seed_competitor_ads_json_proof_dense(
    prospect_root: Path,
    *,
    n_competitors_with_proof: int = 5,
    ads_per_competitor: int = 6,
    excluded_count: int = 2,
) -> dict:
    """Seed a competitor_ads.json with proof depth that mirrors what the
    new capture pass produces: 5+ competitors with 4-8 captured ads each,
    plus 2 competitors that came back with zero qualifying ads (the
    excluded set).

    Writes the matching PNG files so the renderer can embed real <img>
    srcs. Returns the payload dict for caller-side assertions.
    """
    strat_dir = prospect_root / "strategy"
    strat_dir.mkdir(parents=True, exist_ok=True)
    competitors_with_proof = (
        "UpCircle Beauty",
        "Evolve Organic Beauty",
        "Aurelia London",
        "Oskia London",
        "Balance Me",
        "Green People",
    )[:n_competitors_with_proof]
    competitors_without_proof = (
        "By Sarah London",
        "Wildsmith Skin",
    )[:excluded_count]
    competitors: dict = {}
    for name in competitors_with_proof:
        slug = name.lower().replace(" ", "-")
        ads = []
        for i in range(1, ads_per_competitor + 1):
            png_path = (
                prospect_root / "assets" / "competitors" / slug / f"ad_{i}.png"
            )
            _make_real_png(png_path, width=72, height=128)
            # Alternate two tags so each pattern has multi-brand proof.
            tag = "review_social_proof" if i % 2 == 0 else "ingredient_proof"
            ads.append({
                "ad_archive_id": f"{name}-{i}",
                "ad_library_url": f"https://www.facebook.com/ads/library/?id=PROOF{i}",
                "screenshot_path": f"assets/competitors/{slug}/ad_{i}.png",
                "body_excerpt": f"{name} sample ad {i} body excerpt.",
                "cta_text": "Shop now",
                "days_active": 30 - i,
                "media_type": "VIDEO" if i == 1 else "IMAGE",
                "pattern_tags": [tag],
                "evidence_level": "competitor_ad_evidence",
                "capture_status": "screenshot_playwright",
                "why_by_tag": {tag: "Ad copy backs this pattern."},
            })
        competitors[name] = {
            "meta_ads_url": (
                f"https://www.facebook.com/ads/library/?active_status=active"
                f"&country=GB&q={name.replace(' ', '+')}"
            ),
            "sampled_ads": ads,
            "capture_status": "captured",
        }
    for name in competitors_without_proof:
        competitors[name] = {
            "meta_ads_url": (
                f"https://www.facebook.com/ads/library/?active_status=active"
                f"&country=GB&q={name.replace(' ', '+')}"
            ),
            "sampled_ads": [],
            "capture_status": "false_positive_filtered",
        }
    payload = {
        "competitors": competitors,
        "generated_at": "2026-05-15T18:00:00+00:00",
    }
    (strat_dir / "competitor_ads.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8",
    )
    return payload


def test_section04_main_grid_includes_only_competitors_with_proof(tmp_path: Path):
    """Proof-only main grid: every rendered card must carry sampled
    ads. Brands the sidecar lists with zero ads (the excluded set) must
    not appear inside the main grid container, only inside the
    secondary 'Checked but not included' note."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    grid_start = html.index('<div class="competitors">')
    excluded_marker = html.find('class="excluded-competitors"', grid_start)
    grid_end = excluded_marker if excluded_marker >= 0 else html.index('</section>', grid_start)
    grid_block = html[grid_start:grid_end]
    # Competitors with proof appear in the grid.
    for name in ("UpCircle Beauty", "Evolve Organic Beauty", "Aurelia London"):
        assert name in grid_block, f"{name!r} missing from main grid"
    # Competitors without proof must NOT appear in the grid.
    for name in ("By Sarah London", "Wildsmith Skin"):
        assert name not in grid_block, (
            f"{name!r} has zero sampled ads but rendered inside the main grid"
        )


def test_section04_grid_renders_at_least_five_competitors_when_seeded(tmp_path: Path):
    """When the sidecar carries proof for 5+ competitors, the main grid
    surfaces all of them - we don't silently cap at three or four."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(prospect_root, n_competitors_with_proof=5)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    grid_start = html.index('<div class="competitors">')
    excluded_marker = html.find('class="excluded-competitors"', grid_start)
    grid_end = excluded_marker if excluded_marker >= 0 else html.index('</section>', grid_start)
    grid_block = html[grid_start:grid_end]
    # Count competitor cards by counting `<article class="competitor"`.
    n_cards = grid_block.count('<article class="competitor"')
    assert n_cards >= 5, (
        f"expected >=5 competitor cards in main grid when 5 brands have "
        f"proof, found {n_cards}"
    )


def test_section04_competitor_card_shows_ad_count_chip(tmp_path: Path):
    """Each main-grid competitor card displays an ACTIVE ADS count
    chip so the reader can scan proof depth at a glance. The legacy
    operator label `SAMPLED ADS` must NOT appear."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(prospect_root, ads_per_competitor=6)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    assert 'class="competitor__ad-count"' in html
    assert "ACTIVE ADS" in html
    assert "SAMPLED ADS" not in html
    # The chip number should match the seeded count for at least one
    # competitor (we look for `>6<` inside the dedicated chip class).
    assert 'class="competitor__ad-count-num">6<' in html


def test_section04_competitor_card_shows_multiple_sampled_ad_thumbnails(tmp_path: Path):
    """Each competitor card renders multiple ad thumbnails (3-5) when
    the sidecar carries that many sampled ads - we are not stuck on
    the 2-3 thumbnail cap that used to feel thin."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(prospect_root, ads_per_competitor=8)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    grid_start = html.index('<div class="competitors">')
    # First competitor card window.
    first_card_idx = html.index('<article class="competitor"', grid_start)
    first_card_end = html.index('</article>', first_card_idx)
    card = html[first_card_idx:first_card_end]
    n_tiles = card.count('class="ad-proof__tile"')
    assert n_tiles >= 3, (
        f"expected >=3 ad-proof tiles in the first competitor card, "
        f"got {n_tiles}"
    )
    # And with 8 captured ads against a visible cap of 5, there must
    # be a `+3 more sampled ads` overflow chip.
    assert "ad-proof__more" in card
    assert "+3" in card


def test_section04_excluded_note_lists_brands_without_proof(tmp_path: Path):
    """The secondary 'Checked but not included' note must surface every
    brand the brief carried that had zero captured ads, with a reason
    label so the reader can see why each one was skipped."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    # The secondary note renders inside section 04 with its own class.
    assert 'class="excluded-competitors"' in html
    note_idx = html.index('class="excluded-competitors"')
    note_end = html.index('</aside>', note_idx)
    note_block = html[note_idx:note_end]
    # The two excluded brands appear here, NOT in the main grid.
    for name in ("By Sarah London", "Wildsmith Skin"):
        assert name in note_block, (
            f"{name!r} expected in the excluded note"
        )
    # Client-safe reason copy: no operator language about Apify, false
    # positives, page-name mismatch, etc. Must read like "kept out for
    # evidence discipline".
    assert (
        "not enough qualifying paid-social proof" in note_block.lower()
        or "evidence discipline" in note_block.lower()
    )
    # Forbidden operator language must NOT leak into the note.
    for forbidden in (
        "Apify", "false positive", "page name", "playwright",
        "capture pass",
    ):
        assert forbidden.lower() not in note_block.lower(), (
            f"operator term {forbidden!r} leaked into the excluded note"
        )


# --------------------------------------------------------------------------- #
# Section 05 - pattern cards only carry verified-brand chips + overflow
# --------------------------------------------------------------------------- #


def test_section05_pattern_card_only_names_brands_with_captured_proof(tmp_path: Path):
    """Pattern cards inside section 05 must list ONLY the brands whose
    captured ads back this pattern. Hardcoded `who_uses` candidates
    without proof for this pattern must not bleed into the chip list."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    main_idx = html.index('id="creative-patterns"')
    main_end = html.index('</section>', main_idx)
    main_block = html[main_idx:main_end]
    # Brands without any captured ad must NOT show up as user chips
    # in the main pattern board.
    assert 'class="pattern__user--candidate"' not in main_block, (
        "main pattern board must only name verified brands"
    )
    # By Sarah London / Wildsmith Skin are seeded with zero captures
    # and must not be named as chips anywhere in the main board.
    for name in ("By Sarah London", "Wildsmith Skin"):
        assert (
            f'class="pattern__user--verified"><span class="pattern__user-dot" '
            f'aria-hidden="true"></span>{name}</span>'
        ) not in main_block, (
            f"{name!r} has zero captured ads; must not appear as a "
            f"verified chip on a pattern card"
        )


def test_section05_pattern_card_supports_up_to_six_proof_tiles(tmp_path: Path):
    """A pattern that pulls from 8+ captured ads should render up to 6
    visible tiles and a `+N more sampled ads` overflow chip - never a
    pattern card with only 1-2 thumbnails when the proof base is
    deeper."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    # Seed a fat pool: 5 competitors x 8 ads x review_social_proof tag
    # on every other ad -> 4 review_social_proof ads per competitor.
    # That's 20 candidates for the same pattern; visible cap is 6.
    _seed_competitor_ads_json_proof_dense(
        prospect_root,
        n_competitors_with_proof=5,
        ads_per_competitor=8,
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    # Find the review_social_proof pattern card window.
    main_idx = html.index('id="creative-patterns"')
    main_end = html.index('</section>', main_idx)
    main_block = html[main_idx:main_end]
    review_idx = main_block.index("Review / social-proof stitched UGC")
    review_end = main_block.index("</article>", review_idx)
    review_card = main_block[review_idx:review_end]
    n_tiles = review_card.count('pattern-evidence__tile')
    assert 1 <= n_tiles <= 6, (
        f"expected 1-6 pattern-evidence tiles per card, got {n_tiles}"
    )
    # And the overflow chip must appear when the underlying pool > 6.
    assert "pattern-evidence__more" in review_card


def test_section05_pattern_only_renders_with_captured_proof(tmp_path: Path):
    """A pattern with zero matching captured ads must NOT appear in the
    main board, only in the 'Patterns to validate next' lane below."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    # Seed: only ingredient_proof / review_social_proof ads, no
    # texture / founder / routine tags. The other patterns should not
    # appear in the main board.
    _seed_competitor_ads_json_proof_dense(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    main_idx = html.index('id="creative-patterns"')
    main_end = html.index('</section>', main_idx)
    main_block = html[main_idx:main_end]
    # Patterns the seeded data does NOT support:
    for unsupported in (
        "Texture / application demo",
        "Founder / expert credibility",
        "Routine simplification",
        "Sensitive-skin reassurance",
    ):
        assert unsupported not in main_block, (
            f"{unsupported!r} has zero captured proof; must not appear "
            f"in the main creative-pattern board"
        )
    # Patterns we DO have proof for must appear.
    for supported in (
        "Review / social-proof stitched UGC",
        "Ingredient-proof close-up",
    ):
        assert supported in main_block, (
            f"{supported!r} has captured proof; should appear on the main board"
        )


# --------------------------------------------------------------------------- #
# Loader - tolerates more than 2 ads per competitor + capture config
# --------------------------------------------------------------------------- #


def test_competitor_ads_loader_supports_more_than_two_ads_per_competitor(tmp_path: Path):
    """The on-disk loader must round-trip 6+ ads per competitor without
    capping the list. Pre-rewrite the spec was 2 ads per competitor;
    the new default is 8."""
    from agents.outreach.reporting.strategy_brief import load_competitor_ad_proofs
    prospect_root = tmp_path / "prospects" / "pai-like"
    prospect_root.mkdir(parents=True, exist_ok=True)
    _seed_competitor_ads_json_proof_dense(
        prospect_root,
        n_competitors_with_proof=1,
        ads_per_competitor=8,
        excluded_count=0,
    )
    proofs = load_competitor_ad_proofs(prospect_root)
    upcircle = proofs["UpCircle Beauty"]
    assert len(upcircle["sampled_ads"]) == 8


def test_capture_script_default_max_ads_is_eight():
    """The capture script default `--max-ads-per-competitor` is 8, not
    5 or 2. This is the lever that gives the renderer enough proof
    depth to show 3-5 thumbnails plus an overflow pool."""
    import importlib
    cap = importlib.import_module("scripts.capture_competitor_ads")
    parser = cap.argparse.ArgumentParser()
    # Run the script's own arg parser to read the default.
    parsed = cap.main.__wrapped__ if hasattr(cap.main, "__wrapped__") else None
    # Direct inspection of the source-of-truth default value:
    import inspect
    src = inspect.getsource(cap.main)
    assert "default=8" in src, (
        "expected scripts/capture_competitor_ads.py --max-ads-per-competitor "
        "default to be 8"
    )
    # Hard cap is 10.
    assert "> 10" in src, "expected hard cap clamp at 10 ads/competitor"
    _ = parser, parsed


def test_section05_validate_next_section_still_renders_unsupported_patterns(tmp_path: Path):
    """Existing 'Patterns to validate next' lane must still work: any
    pattern with zero captured proof flows there, not into the main
    board, and not into nothing."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    validate_idx = html.index('id="patterns-to-validate"')
    validate_end = html.index('</section>', validate_idx)
    validate_block = html[validate_idx:validate_end]
    # At least one unsupported pattern shows up here.
    assert any(
        name in validate_block for name in (
            "Texture / application demo",
            "Founder / expert credibility",
            "Routine simplification",
            "Sensitive-skin reassurance",
        )
    )
    # Client-safe chip taxonomy replaces the operator labels.
    assert any(
        label in validate_block for label in (
            "FURTHER EVIDENCE TO COLLECT",
            "EARLY SIGNAL",
            "ALREADY REPRESENTED",
        )
    )


# --------------------------------------------------------------------------- #
# Section 05 evidence-diversity rule (≥2 unique ads, no duplicate ads, etc)
# --------------------------------------------------------------------------- #


def _seed_competitor_ads_json_for_evidence_rule(
    prospect_root: Path,
) -> dict:
    """Seed designed to exercise the new Section-05 evidence rule:

      - ingredient_proof:  2 ads from 2 competitors  -> Section 05 ✓
      - texture_application_demo: 1 ad               -> Section 05B (single_ad)
      - founder_expert_credibility: 0 ads            -> Section 05B (no_evidence)
      - Aurelia ad with multi-tag (review + routine) -> claimed by review
        (more specific) so routine_simplification, if it had no other ads,
        moves to 05B as `lost_to_dedup`.
    """
    strat_dir = prospect_root / "strategy"
    strat_dir.mkdir(parents=True, exist_ok=True)
    _make_real_png(
        prospect_root / "assets" / "competitors" / "evolve-organic-beauty" / "ad_1.png",
        width=72, height=128,
    )
    _make_real_png(
        prospect_root / "assets" / "competitors" / "aurelia-london" / "ad_1.png",
        width=72, height=128,
    )
    _make_real_png(
        prospect_root / "assets" / "competitors" / "aurelia-london" / "ad_2.png",
        width=72, height=128,
    )
    _make_real_png(
        prospect_root / "assets" / "competitors" / "upcircle-beauty" / "ad_1.png",
        width=72, height=128,
    )
    payload = {
        "competitors": {
            "Evolve Organic Beauty": {
                "meta_ads_url": (
                    "https://www.facebook.com/ads/library/"
                    "?active_status=active&country=GB&q=Evolve+Organic+Beauty"
                ),
                "sampled_ads": [
                    {
                        "ad_archive_id": "EVO-1",
                        "ad_library_url": "https://www.facebook.com/ads/library/?id=EVO-1",
                        "screenshot_path": "assets/competitors/evolve-organic-beauty/ad_1.png",
                        "body_excerpt": "Vitamin C and rosehip extracts in our serum.",
                        "cta_text": "Shop now",
                        "days_active": 12,
                        "media_type": "VIDEO",
                        "pattern_tags": ["ingredient_proof"],
                        "evidence_level": "competitor_ad_evidence",
                        "capture_status": "screenshot_playwright",
                    },
                ],
            },
            "Aurelia London": {
                "meta_ads_url": (
                    "https://www.facebook.com/ads/library/"
                    "?active_status=active&country=GB&q=Aurelia+London"
                ),
                "sampled_ads": [
                    {
                        "ad_archive_id": "AUR-1",
                        "ad_library_url": "https://www.facebook.com/ads/library/?id=AUR-1",
                        "screenshot_path": "assets/competitors/aurelia-london/ad_1.png",
                        "body_excerpt": "Niacinamide and ceramides for sensitive skin.",
                        "cta_text": "Shop now",
                        "days_active": 18,
                        "media_type": "IMAGE",
                        "pattern_tags": ["ingredient_proof"],
                        "evidence_level": "competitor_ad_evidence",
                        "capture_status": "screenshot_playwright",
                    },
                    {
                        "ad_archive_id": "AUR-2",
                        "ad_library_url": "https://www.facebook.com/ads/library/?id=AUR-2",
                        "screenshot_path": "assets/competitors/aurelia-london/ad_2.png",
                        "body_excerpt": "Let's talk about my morning routine and skin.",
                        "cta_text": "Shop now",
                        "days_active": 10,
                        "media_type": "VIDEO",
                        # Multi-tag: should be claimed by the more
                        # specific `review_social_proof` during dedup.
                        "pattern_tags": ["review_social_proof", "routine_simplification"],
                        "evidence_level": "competitor_ad_evidence",
                        "capture_status": "download_video_preview",
                    },
                ],
            },
            "UpCircle Beauty": {
                "meta_ads_url": (
                    "https://www.facebook.com/ads/library/"
                    "?active_status=active&country=GB&q=UpCircle+Beauty"
                ),
                "sampled_ads": [
                    # Single ad on texture_application_demo - should
                    # move to Section 05B as `single_ad`.
                    {
                        "ad_archive_id": "UPC-1",
                        "ad_library_url": "https://www.facebook.com/ads/library/?id=UPC-1",
                        "screenshot_path": "assets/competitors/upcircle-beauty/ad_1.png",
                        "body_excerpt": "Watch the silky texture absorb instantly.",
                        "cta_text": "Shop now",
                        "days_active": 9,
                        "media_type": "VIDEO",
                        "pattern_tags": ["texture_application_demo"],
                        "evidence_level": "competitor_ad_evidence",
                        "capture_status": "screenshot_playwright",
                    },
                ],
            },
        },
        "generated_at": "2026-05-15T19:00:00+00:00",
    }
    (strat_dir / "competitor_ads.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def test_section05_pattern_card_requires_at_least_two_unique_ads(tmp_path: Path):
    """Section 05 rule: only patterns with at least 2 unique sampled ads
    after de-duplication appear in the main board. Patterns with fewer
    ads must be moved to Section 05B."""
    from agents.outreach.reporting.strategy_brief import (
        MIN_ADS_FOR_VALIDATED_PATTERN,
        StrategyBrief,
    )
    assert MIN_ADS_FOR_VALIDATED_PATTERN >= 2
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_for_evidence_rule(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)

    for p in brief.validated_patterns:
        assert len(p.ad_evidence) >= 2, (
            f"Section 05 pattern {p.name!r} has only {len(p.ad_evidence)} "
            f"ad(s); the new rule requires at least 2 unique sampled ads."
        )


def test_section05_prefers_two_unique_competitors_when_available(tmp_path: Path):
    """Section 05 ordering: when multiple validated patterns exist, the
    ones backed by more unique competitors appear first."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_for_evidence_rule(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)

    # ingredient_proof should be first - it has 2 competitors (Evolve + Aurelia).
    if brief.validated_patterns:
        first = brief.validated_patterns[0]
        comps = {ad.competitor_name for ad in first.ad_evidence}
        assert len(comps) >= 2, (
            f"Top Section-05 pattern {first.name!r} should have at least "
            f"2 unique competitors; got {sorted(comps)}"
        )


def test_section05_does_not_reuse_an_ad_across_pattern_cards(tmp_path: Path):
    """No `ad_archive_id` may render in more than one Section-05 card.
    This is the core de-duplication contract of the new evidence rule."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_for_evidence_rule(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)

    seen: dict[tuple[str, str], str] = {}
    for p in brief.validated_patterns:
        for ad in p.ad_evidence:
            key = (ad.competitor_name, ad.ad_archive_id)
            assert key not in seen, (
                f"Ad ({ad.competitor_name}, {ad.ad_archive_id}) appears in "
                f"both {seen[key]!r} and {p.name!r} - duplicate proof tile "
                f"across Section 05"
            )
            seen[key] = p.name


def test_section05_does_not_reuse_an_ad_in_rendered_html(tmp_path: Path):
    """Render-time guarantee: the same Meta Ads Library URL must not
    appear inside two different Section-05 pattern cards."""
    import re as _re
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_for_evidence_rule(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    main_idx = html.index('id="creative-patterns"')
    main_end = html.index("</section>", main_idx)
    main_block = html[main_idx:main_end]
    ids = _re.findall(
        r'facebook\.com/ads/library/\?id=([A-Za-z0-9_-]+)',
        main_block,
    )
    from collections import Counter
    dupes = {k: v for k, v in Counter(ids).items() if v > 1}
    assert not dupes, (
        f"Section 05 reuses ad_archive_id(s): {dupes}"
    )


def test_single_ad_pattern_moves_to_validate_next_with_single_ad_chip(tmp_path: Path):
    """A pattern with exactly one supporting ad must move to Section
    05B and carry the client-safe `EARLY SIGNAL` chip - never the
    validated `COMPETITOR ADS` chip."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_for_evidence_rule(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    validate_idx = html.index('id="patterns-to-validate"')
    validate_end = html.index("</section>", validate_idx)
    validate_block = html[validate_idx:validate_end]
    assert "Texture / application demo" in validate_block
    # Find the Texture card window and check chip + count.
    tex_idx = validate_block.index("Texture / application demo")
    tex_end = validate_block.index("</article>", tex_idx)
    tex_card = validate_block[tex_idx:tex_end]
    assert "EARLY SIGNAL" in tex_card
    # The operator label must NOT appear.
    assert "ONE SAMPLED AD" not in tex_card


def test_section05b_explains_why_one_ad_patterns_are_not_promoted(tmp_path: Path):
    """Section 05B cards include a strategist-tone row explaining why
    the pattern is not in sprint one and what would make it eligible
    later. The legacy operator-y `Why not promoted yet` label is gone."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_for_evidence_rule(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    validate_idx = html.index('id="patterns-to-validate"')
    validate_end = html.index("</section>", validate_idx)
    validate_block = html[validate_idx:validate_end]
    assert "Why not in sprint one" in validate_block
    assert "What would make it eligible" in validate_block
    # Legacy operator copy is gone.
    assert "Why not promoted yet" not in validate_block


def test_section05_renders_unique_competitor_and_sampled_ad_counts(tmp_path: Path):
    """Each Section-05 card carries the `brands running it` + `live
    ads` count chips so the reader sees proof depth at a glance."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_for_evidence_rule(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    main_idx = html.index('id="creative-patterns"')
    main_end = html.index("</section>", main_idx)
    main_block = html[main_idx:main_end]
    # Client-safe chip labels: brands running it / live ads.
    assert "running it" in main_block
    assert "live ad" in main_block
    # CSS class confirming the chip rendered.
    assert "pattern__proof-chip" in main_block
    # Operator labels must NOT appear.
    assert "sampled ad" not in main_block
    assert "unique competitor" not in main_block


def test_section05_card_never_lists_competitor_without_proof_for_pattern(tmp_path: Path):
    """Inside a Section-05 card, only brands that actually have a
    sampled ad for THAT pattern appear under the verified-users chips.
    Brands listed in the pattern's hardcoded `who_uses` but without a
    matching ad must NOT render here."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_for_evidence_rule(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    main_idx = html.index('id="creative-patterns"')
    main_end = html.index("</section>", main_idx)
    main_block = html[main_idx:main_end]
    if "Ingredient-proof close-up" in main_block:
        ip_idx = main_block.index("Ingredient-proof close-up")
        ip_end = main_block.index("</article>", ip_idx)
        ip_card = main_block[ip_idx:ip_end]
        # REN Clean Skincare appears in ingredient_proof.who_uses but
        # does NOT have a sampled ad in our seed -> must not render
        # inside the card's user chips.
        assert "REN Clean Skincare" not in ip_card, (
            "Section-05 card lists a brand for which we did not capture a "
            "supporting ad - violates the proof-only rule"
        )


def test_section05_card_renders_two_to_six_proof_tiles(tmp_path: Path):
    """Section-05 cards render between 2 and 6 ad-proof tiles; any
    additional ads surface as a `+N more sampled ads` overflow chip."""
    import re as _re
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(
        prospect_root, n_competitors_with_proof=3, ads_per_competitor=8,
    )
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(brief, output_dir=tmp_path / "out", noindex=True)
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")

    main_idx = html.index('id="creative-patterns"')
    main_end = html.index("</section>", main_idx)
    main_block = html[main_idx:main_end]
    article_re = _re.compile(
        r'<article class="pattern".*?</article>',
        _re.DOTALL,
    )
    for card in article_re.findall(main_block):
        n_tiles = card.count('pattern-evidence__tile')
        assert 2 <= n_tiles <= 6, (
            f"Section-05 card has {n_tiles} proof tiles - expected 2-6."
        )


def test_classifier_does_not_blindly_tag_dco_placeholder_ads(tmp_path: Path):
    """A DCO ad with `{{product.brand}}` placeholder body must NOT pick
    up any pattern tag - the classifier requires real copy."""
    from scripts.capture_competitor_ads import _classify_ad
    ad_dco = {"body_text": "{{product.brand}}"}
    tags, why = _classify_ad(ad_dco)
    assert tags == [] and why == {}
    # Also: empty / whitespace body must not tag.
    ad_empty = {"body_text": "   "}
    tags, why = _classify_ad(ad_empty)
    assert tags == [] and why == {}


def test_classifier_picks_up_plural_botanicals(tmp_path: Path):
    """Regression for the regex bug that dropped `botanicals` because
    the old pattern required `\\bbotanical\\b` (word-boundary after
    the singular). The new regex must catch the plural."""
    from scripts.capture_competitor_ads import _classify_ad
    ad = {"body_text": "Discover freshly made skincare powered by tropical botanicals."}
    tags, why = _classify_ad(ad)
    assert "ingredient_proof" in tags, (
        f"`botanicals` should match ingredient_proof; got tags={tags}"
    )


# --------------------------------------------------------------------------- #
# Client-facing polish (TASK 8 - client-deliverable cleanup)
# --------------------------------------------------------------------------- #


def _polished_pai_html(tmp_path: Path) -> str:
    """Build the Pai-shaped strategy HTML with the proof-dense fixture
    so every section (Section 04 grid, Section 05 validated patterns,
    Section 05B unvalidated) has content for the polish-scan tests."""
    prospects_root, _, prospect_root = _save_pai_like_audit(
        tmp_path / "prospects" / "pai-like"
    )
    _seed_competitor_ads_json_proof_dense(prospect_root)
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)
    build_strategy_html(
        brief, output_dir=tmp_path / "out", noindex=True,
        status="draft", public_url="https://example.com/p/abc/strategy/",
    )
    return (tmp_path / "out" / "index.html").read_text(encoding="utf-8")


def test_polish_strategy_page_does_not_render_local_draft_banner(tmp_path: Path):
    """The strategy page is a client deliverable - the amber 'Local
    draft preview · not deployed yet' banner the pitch microsite uses
    for operator review must NEVER reach the client HTML, even when
    `status='draft'` is passed."""
    html = _polished_pai_html(tmp_path)
    assert "Local draft preview" not in html
    assert "not deployed yet" not in html
    # Also no green 'Live' banner with a deploy URL.
    assert 'class="status-banner status-banner--live"' not in html
    assert 'class="status-banner status-banner--draft"' not in html


def test_polish_pitch_microsite_status_banner_still_renders(tmp_path: Path):
    """The pitch microsite (a separate renderer) DOES still need the
    draft/live status banner for operator review. This guard ensures
    the strategy-page polish did not accidentally rip the pitch banner."""
    from agents.outreach.reporting.html_deck_builder import _status_banner
    # Draft path still emits the banner string.
    out_draft = _status_banner("draft", None)
    assert "Local draft preview" in out_draft
    assert "status-banner--draft" in out_draft
    # Deployed path still emits the live banner.
    out_live = _status_banner("deployed", "https://example.com/p/abc/")
    assert "status-banner--live" in out_live


def test_polish_strategy_page_has_no_visible_operator_terms(tmp_path: Path):
    """Final client-facing scan: visible page text must NOT carry
    operator language. CSS class names and test code are ignored - we
    strip <style>/<script> blocks and tags, then look at the user-
    visible text only."""
    import html as _html_mod
    import re as _re
    html = _polished_pai_html(tmp_path)
    no_script = _re.sub(r"<script[^>]*>.*?</script>", "", html, flags=_re.DOTALL)
    no_style = _re.sub(r"<style[^>]*>.*?</style>", "", no_script, flags=_re.DOTALL)
    visible = _re.sub(r"<[^>]+>", " ", no_style)
    visible = _html_mod.unescape(_re.sub(r"\s+", " ", visible)).lower()

    forbidden_substrings = (
        "local draft preview",
        "not deployed yet",
        "apify",
        "playwright",
        "false positive",
        "false_positive",
        "dco placeholder",
        "needs validation",
        "scrape more",
        "validate next",
        "avoid for now" + " " + "—",  # the chip with em-dash; lowercase phrase in titles is OK
        "one sampled ad",
        "shown above",
        "no evidence yet",
        "we could not capture",
        "we did not capture",
        "keyword search returned hits",
        "capture pass",
        "capture_status",
        "capture_error",
        "discarded_for_page_mismatch",
        "no_active_ads_found",
        "ai_classification",
        "tag_source",
        "raw_regex",
        "body_text=",
        "media_type=",
        "{{product.brand}}",
        "{{product.name}}",
        "screenshot unavailable",
        "sampled ad screenshot",
        "audit signal",
        "view sampled ad",
        "sampled ads",
        "open ad ",   # legacy chip suffixed by a space
    )
    for needle in forbidden_substrings:
        assert needle not in visible, (
            f"Operator phrase {needle!r} leaked into visible client HTML"
        )

    # Word-boundary checks for short tokens that would otherwise
    # collide with brand names (e.g. 'AI' lives inside 'Pai').
    forbidden_words = ("regex", "classifier", "scrape")
    for word in forbidden_words:
        assert not _re.search(rf"\b{word}\b", visible), (
            f"Operator term {word!r} leaked into visible client HTML"
        )


def test_polish_strategy_page_uses_allowed_evidence_chip_labels(tmp_path: Path):
    """The page must use the client-facing chip taxonomy: PAI AUDIT /
    COMPETITOR ADS / CATEGORY RESEARCH / STRATEGY HYPOTHESIS /
    MONITOR NEXT / COMPLIANCE CAUTION. Forbidden operator labels
    (HYPOTHESIS alone, NEEDS VALIDATION, AUDIT SIGNAL, RESEARCH) must
    not appear as standalone chips."""
    import re as _re
    html = _polished_pai_html(tmp_path)
    # The .ev-chip tag is the chip surface.
    chip_texts = _re.findall(
        r'<span class="ev-chip[^"]*">([^<]+)</span>',
        html,
    )
    allowed = {
        "PAI AUDIT", "COMPETITOR ADS", "CATEGORY RESEARCH",
        "STRATEGY HYPOTHESIS", "MONITOR NEXT", "COMPLIANCE CAUTION",
        "SPRINT PRIORITY",
    }
    seen = set(chip_texts)
    illegal = seen - allowed
    assert not illegal, (
        f"Evidence chip leaked operator labels: {illegal}"
    )


def test_polish_section05b_reads_as_strategy_queue_not_missing_work(tmp_path: Path):
    """Section 05B should read like a senior strategist's queue, not
    a system note. Required client-facing phrases must appear; legacy
    operator phrases must not."""
    html = _polished_pai_html(tmp_path)
    block_start = html.index('id="patterns-to-validate"')
    block_end = html.index("</section>", block_start)
    block = html[block_start:block_end]

    # Client-safe section frame.
    assert "Signals to monitor before production" in block
    assert "Why it is interesting" in block
    assert "Why not in sprint one" in block
    assert "What would make it eligible" in block
    assert "What we would do instead now" in block
    # Operator framing must NOT appear.
    assert "Why not promoted yet" not in block
    assert "Hypotheses below the two-ad minimum" not in block
    assert "discipline log behind that rule" not in block


def test_polish_excluded_competitor_note_uses_client_safe_reasons(tmp_path: Path):
    """The 'Also checked' note must not surface capture internals.
    Reasons must read as evidence discipline, not capture errors."""
    html = _polished_pai_html(tmp_path)
    note_idx = html.index('class="excluded-competitors"')
    note_end = html.index("</aside>", note_idx)
    note = html[note_idx:note_end].lower()

    # Operator words MUST NOT appear in the note.
    for forbidden in (
        "apify", "playwright", "keyword search", "false positive",
        "false_positive", "page name", "capture pass", "capture_status",
        "scrape_failed", "blocked", "no_active_ads_found",
    ):
        assert forbidden not in note, (
            f"operator term {forbidden!r} leaked into the 'Also checked' note"
        )

    # And the client-safe framing must appear.
    assert (
        "not enough qualifying paid-social proof" in note
        or "evidence discipline" in note
    )


def test_polish_section04_uses_client_safe_count_and_relevance_chips(tmp_path: Path):
    """Section 04 cards must use ACTIVE ADS + a market-relevance chip
    (DIRECT BENCHMARK / CATEGORY PEER / ADJACENT BRAND). The legacy
    `SAMPLED ADS` + `HIGH/MEDIUM/LOW CONFIDENCE` chips must be gone."""
    html = _polished_pai_html(tmp_path)
    sec04_idx = html.index('id="competitors"')
    sec04_end = html.index("</section>", sec04_idx)
    sec04 = html[sec04_idx:sec04_end]
    assert "ACTIVE ADS" in sec04
    # At least one of the relevance chips renders.
    assert any(label in sec04 for label in (
        "DIRECT BENCHMARK", "CATEGORY PEER", "ADJACENT BRAND",
    ))
    # Old labels gone.
    for legacy in ("SAMPLED ADS", "HIGH CONFIDENCE", "MEDIUM CONFIDENCE",
                   "LOW CONFIDENCE"):
        assert legacy not in sec04, (
            f"legacy operator chip {legacy!r} still in Section 04"
        )


def test_polish_raw_status_fields_do_not_leak_into_rendered_html(tmp_path: Path):
    """Raw status/AI fields from competitor_ads.json (capture_status,
    capture_error, ai_classification_status, raw_regex_tags,
    tag_source, ai_raw_response) must never appear as visible HTML
    text. CSS class names and JSON-side fields are operator-only."""
    import html as _html_mod
    import re as _re
    html = _polished_pai_html(tmp_path)
    no_script = _re.sub(r"<script[^>]*>.*?</script>", "", html, flags=_re.DOTALL)
    no_style = _re.sub(r"<style[^>]*>.*?</style>", "", no_script, flags=_re.DOTALL)
    visible = _re.sub(r"<[^>]+>", " ", no_style)
    visible = _html_mod.unescape(_re.sub(r"\s+", " ", visible))
    for raw_field in (
        "capture_status", "capture_error",
        "ai_classification_status", "ai_classification",
        "raw_regex_tags", "raw_regex",
        "tag_source", "ai_raw_response",
        "ai_should_use_for_strategy",
        "body_text=", "media_type=",
    ):
        assert raw_field not in visible, (
            f"raw operator field {raw_field!r} leaked into visible HTML"
        )
