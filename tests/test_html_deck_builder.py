"""Tests for the HTML deck builder and the deck brief layer.

The brief is tested first because the HTML builder is downstream:
broken briefs hide HTML-builder bugs. Tests intentionally avoid
launching a browser or shelling out to weasyprint - we assert on the
generated HTML string + filesystem layout only.
"""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

from agents.outreach.prospect_store import ProspectStore
from agents.outreach.reporting.deck_brief import (
    AdProof,
    ConceptRoute,
    DeckBrief,
    FortyFiveSecondCard,
    GapMapRow,
    PricingTier,
    ProcessStep,
)
from agents.outreach.reporting.html_deck_builder import build_html_deck

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_real_png(path: Path, *, width: int = 64, height: int = 64) -> Path:
    """Write a minimal but real PNG so file-size checks (>4 KB) pass.

    We need >4 KB to clear `_is_useful_image`. A 64x64 RGBA image with
    one IDAT chunk hits ~17 KB before compression - simpler than fiddling
    with `Pillow` in tests.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR: 64x64, 8-bit, RGBA.
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    ihdr = _png_chunk(b"IHDR", ihdr_data)
    # Build raw image data: one filter byte (0) per scanline + RGBA pixels.
    row = b"\x00" + (b"\xC8\xC8\xC8\xFF" * width)
    raw = row * height
    idat = _png_chunk(b"IDAT", zlib.compress(raw, 9))
    iend = _png_chunk(b"IEND", b"")
    payload = sig + ihdr + idat + iend
    # Pad to clear the 4 KB threshold even when zlib eats most of the rows.
    if len(payload) < 5000:
        pad = _png_chunk(b"tEXt", b"yuvo-test\x00" + b"x" * (5000 - len(payload)))
        payload = sig + ihdr + idat + pad + iend
    path.write_bytes(payload)
    return path


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data)
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _make_synthetic_brief(prospect_root: Path) -> DeckBrief:
    """A DeckBrief with one of every feature so HTML rendering exercises every branch."""
    logo = _make_real_png(prospect_root / "assets" / "logo.png")
    hero = _make_real_png(prospect_root / "assets" / "hero.png", width=128, height=128)
    product_a = _make_real_png(prospect_root / "assets" / "product_a.png")
    product_b = _make_real_png(prospect_root / "assets" / "product_b.png")
    ad_shot = _make_real_png(prospect_root / "assets" / "ad_1.png")

    return DeckBrief(
        prospect_name="Acme Skincare",
        niche="Independent UK skincare brand",
        agency_name="Yuvo Studio",
        website_url="https://example.com",
        facebook_url="https://facebook.com/acme",
        instagram_url=None,
        brand_tone="Minimal, founder-led",
        product_category="skincare",
        audience_assumption="UK 25-45 clean beauty buyers",
        primary_color="#2C4A3E",
        secondary_color="#F5F0E8",
        accent_text_color="#FFFFFF",
        logo_path=logo,
        hero_image_path=hero,
        product_images=[product_a, product_b],
        cover_headline="A cleaner skincare video route for Acme Skincare.",
        cover_subhead="A short, private creative note.",
        forty_five_second_cards=[
            FortyFiveSecondCard("What we saw", "Catalogue-templated copy across all five active ads."),
            FortyFiveSecondCard("What we'd make", "Three short-form routes against existing imagery."),
            FortyFiveSecondCard("What it costs to try", "GBP 90 for one finished route. GBP 260 for the starter trio."),
            FortyFiveSecondCard("Why it is low-risk", "Two-round revisions. You keep every original asset."),
        ],
        ads=[
            AdProof(
                archive_id="1111111111111111",
                issue_label="PLACEHOLDER COPY",
                issue_explainer="This ad appears to show an unfilled product placeholder.",
                body_excerpt="A cleaner morning routine, in three steps.",
                cta_text="Shop now",
                days_active=329,
                library_url="https://www.facebook.com/ads/library/?id=1111111111111111",
                screenshot_path=ad_shot,
                suggested_route="Founder-voiced 9:16 with a hand-held product close.",
            ),
            AdProof(
                archive_id="2222222222222222",
                issue_label="NO CTA CAPTURED",
                issue_explainer="No CTA captured for this ad.",
                body_excerpt=None,
                cta_text=None,
                days_active=62,
                library_url="https://www.facebook.com/ads/library/?id=2222222222222222",
                screenshot_path=None,
                suggested_route="Hold a one-line CTA on screen for the last two seconds.",
            ),
        ],
        gap_map_rows=[
            GapMapRow(
                current_pattern="No video creative across the active library.",
                why_it_limits_growth="Static creative caps scroll-stop on cold audiences.",
                ugc_test="Two 12-15s founder cuts shot against existing brand imagery.",
                confidence="high",
            ),
            GapMapRow(
                current_pattern="Single CTA across all sampled ads.",
                why_it_limits_growth="Without a clear CTA the audience does not know which next step is cheap.",
                ugc_test="Add a held-text + voice CTA in the final 2 seconds.",
                confidence="medium",
            ),
        ],
        concepts=[
            ConceptRoute("Route A", "Morning skin reset", "What three minutes actually changes", "SHOP THE ROUTINE", product_a),
            ConceptRoute("Route B", "Texture proof close-up", "What it looks like one week in", "SHOP THE SERUM", product_b),
            ConceptRoute("Route C", "Sensitive-skin routine", "What I use when my skin reacts to everything", "SHOP SENSITIVE", None),
            ConceptRoute("Route D", "Founder explains the formula", "Why we left this ingredient out", "READ THE FORMULA", None),
        ],
        process_steps=[
            ProcessStep("01", "No shoot day", "We work from imagery you already own."),
            ProcessStep("02", "Brand inputs", "30 minutes on tone."),
        ],
        pricing=[
            PricingTier("Single video", "£90", "Try one route.", ["One cut", "Two rounds"]),
            PricingTier("Starter trio", "£260", "Three openings.", ["Three cuts"], is_recommended=True),
            PricingTier("Growth pack", "£499", "Tested library.", ["Six cuts"]),
        ],
        cta_headline="Want to see one Acme Skincare video?",
        cta_body="Reply with 'send the route'.",
        prospect_root=prospect_root,
    )


# --------------------------------------------------------------------------- #
# HTML output contract
# --------------------------------------------------------------------------- #


def test_html_deck_written_at_expected_path(tmp_path: Path):
    """Output lands at prospects/<id>/deck/index.html relative to prospect_root."""
    brief = _make_synthetic_brief(tmp_path)
    out = build_html_deck(brief)
    assert out == tmp_path / "deck" / "index.html"
    assert out.exists()
    assert out.stat().st_size > 2_000


def test_html_contains_prospect_name(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "Acme Skincare" in html


def test_html_contains_private_creative_note(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "Private Creative Note" in html


def test_html_contains_live_ads_section(tmp_path: Path):
    """V2: replaces the old 'From live ad to video route' heading with
    warmer 'What we saw in your ads' framing. The section is still
    anchored at #live-ads for the in-page CTA jump."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "What we saw in your ads" in html
    assert 'id="live-ads"' in html


def test_html_contains_concept_board(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Rollback after V7: concept rail is the primary concepts surface
    # again (the short-lived "Other routes we would test next" demotion
    # is gone, along with the featured-route hero that demoted it).
    assert "The routes we" in html
    assert 'id="concepts"' in html


def test_html_contains_how_this_works(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "How simple this is" in html
    assert 'id="process"' in html


def test_html_contains_all_three_prices(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "£90" in html
    assert "£260" in html
    assert "£499" in html


def test_html_contains_open_ad_links_when_urls_exist(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "ads/library/?id=1111111111111111" in html
    assert "ads/library/?id=2222222222222222" in html
    # 'Open ad' button label appears for each ad row.
    assert html.count(">Open ad<") >= 2


def test_html_uses_local_assets_when_provided(tmp_path: Path):
    """Hero photo + product images embed when provided.

    V5: the logo image is intentionally NOT embedded anywhere -
    `hero__monogram` is always text initials, and the wordmark lives
    in the topbar as text. This avoids the previous bug where a 32px
    favicon got stretched into a 76px brand-color circle and read as
    a giant logo block for brands with dark primary colors.
    """
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Hero photo + product images: embedded.
    assert "assets/hero.png" in html
    assert "assets/product_a.png" in html
    assert "assets/product_b.png" in html
    # Logo image: deliberately absent (text initials only).
    assert "assets/logo.png" not in html


def test_html_does_not_use_any_asset_path_more_than_once(tmp_path: Path):
    """Strict 'each image once' rule across the whole deck.

    The logo image is excluded from this check - V5 deliberately
    never embeds it (count == 0 is correct, not a regression).
    """
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    for asset in ("hero.png", "product_a.png", "product_b.png", "ad_1.png"):
        count = html.count(asset)
        assert count == 1, f"asset {asset!r} appears {count} times in the deck"


def test_html_renders_without_any_assets(tmp_path: Path):
    """Asset-less briefs still produce a complete 8-slide deck."""
    brief = _make_synthetic_brief(tmp_path)
    # Strip every image path.
    brief.logo_path = None
    brief.hero_image_path = None
    brief.product_images = []
    for i, ad in enumerate(brief.ads):
        brief.ads[i] = AdProof(
            archive_id=ad.archive_id,
            issue_label=ad.issue_label,
            issue_explainer=ad.issue_explainer,
            body_excerpt=ad.body_excerpt,
            cta_text=ad.cta_text,
            days_active=ad.days_active,
            library_url=ad.library_url,
            screenshot_path=None,
            suggested_route=ad.suggested_route,
        )
    for i, c in enumerate(brief.concepts):
        brief.concepts[i] = ConceptRoute(
            label=c.label, title=c.title, hook=c.hook, cta=c.cta, visual_path=None
        )
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Still has all eight slides + preview + interest.
    for slide in (
        "data-slide=\"1\"",
        "data-slide=\"2\"",
        "data-slide=\"3\"",
        "data-slide=\"4\"",
        "data-slide=\"5\"",
        "data-slide=\"6\"",
        "data-slide=\"7\"",
        "data-slide=\"8\"",
    ):
        assert slide in html, f"slide {slide!r} missing"
    # CSS mock fallbacks for ad card + concept board.
    assert "ad-card__preview--mock" in html
    assert "concept__phone--mock" in html
    # Preview section always renders the simple locked placeholder.
    assert "Your first route lives here" in html


def test_html_does_not_contain_ai_ugc(tmp_path: Path):
    """We explicitly do not promise AI-UGC."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8").lower()
    assert "ai-ugc" not in html
    assert "ai ugc" not in html


def test_html_does_not_leak_debug_markers(tmp_path: Path):
    """Internal field names must never reach the deck."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    for marker in (
        "body_text=",
        "media_type=",
        "ad_archive_id=",
        "{{product.brand}}",
        "{{product.name}}",
    ):
        assert marker not in html, f"deck leaked debug marker {marker!r}"


def test_html_uses_primary_color_in_css(tmp_path: Path):
    """The brand accent must be wired into the stylesheet."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "--accent: #2C4A3E" in html


def test_html_escapes_html_in_text_fields(tmp_path: Path):
    """User-visible strings are HTML-escaped (no XSS via prospect_name etc.)."""
    brief = _make_synthetic_brief(tmp_path)
    brief.prospect_name = "Acme <script>alert(1)</script> & Co"
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------- #
# DeckBrief.from_audit
# --------------------------------------------------------------------------- #


def _seed_audit(tmp_path: Path, *, with_brand_profile: bool = True) -> ProspectStore:
    """Write a minimal audit.json under prospects/test-brand/ and return the store."""
    pid = "test-brand"
    store = ProspectStore(pid, prospects_root=tmp_path)
    audit_dict = {
        "prospect_id": pid,
        "prospect_name": "Test Brand",
        "niche": "skincare",
        "country": "GB",
        "locale": "en-GB",
        "audited_at": "2026-05-14T00:00:00+00:00",
        "competitor_ads": [
            {
                "ad_archive_id": "9999999999999999",
                "page_name": "Test Brand",
                "body_text": "{{product.name}}",
                "cta_text": "Shop now",
                "days_active": 100,
                "is_active": True,
            }
        ],
        "weaknesses": [
            "[HIGH CONFIDENCE] Placeholder copy in all ads. Evidence: 5 of 5 ads.",
            "[MEDIUM CONFIDENCE] No video creative confirmed.",
        ],
        "winning_hooks": [],
        "referral_motions": [],
    }
    if with_brand_profile:
        # Asset files must clear the 4 KB useful-image threshold.
        logo = _make_real_png(store.root / "assets" / "logo.png")
        hero = _make_real_png(store.root / "assets" / "hero.png", width=200, height=200)
        product = _make_real_png(store.root / "assets" / "product.png")
        audit_dict["brand_profile"] = {
            "brand_name": "Test Brand",
            "website_url": "https://test-brand.example",
            "primary_color": "#123456",
            "logo_path": str(logo.relative_to(store.root)).replace("\\", "/"),
            "hero_image_path": str(hero.relative_to(store.root)).replace("\\", "/"),
            "product_images": [str(product.relative_to(store.root)).replace("\\", "/")],
            "brand_tone": "Honest, founder-led",
        }
    store.root.mkdir(parents=True, exist_ok=True)
    store.audit_path.write_text(json.dumps(audit_dict), encoding="utf-8")
    return store


def test_deck_brief_from_audit_reads_audit_json(tmp_path: Path):
    store = _seed_audit(tmp_path)
    brief = DeckBrief.from_audit("test-brand", prospects_root=tmp_path)
    assert brief.prospect_name == "Test Brand"
    assert brief.niche == "skincare"
    assert brief.primary_color == "#123456"
    assert brief.website_url == "https://test-brand.example"
    assert brief.prospect_root == store.root
    assert brief.logo_path is not None
    assert brief.hero_image_path is not None
    assert len(brief.product_images) == 1


def test_deck_brief_from_audit_without_brand_profile(tmp_path: Path):
    _seed_audit(tmp_path, with_brand_profile=False)
    brief = DeckBrief.from_audit("test-brand", prospects_root=tmp_path)
    assert brief.logo_path is None
    assert brief.hero_image_path is None
    assert brief.product_images == []
    # Still produces the 4 standard 45-second cards.
    assert len(brief.forty_five_second_cards) == 4


def test_deck_brief_builds_four_concepts(tmp_path: Path):
    _seed_audit(tmp_path)
    brief = DeckBrief.from_audit("test-brand", prospects_root=tmp_path)
    assert len(brief.concepts) == 4
    # First label is always Route A.
    assert brief.concepts[0].label == "Route A"
    assert brief.concepts[3].label == "Route D"


def test_deck_brief_pricing_is_constant(tmp_path: Path):
    _seed_audit(tmp_path)
    brief = DeckBrief.from_audit("test-brand", prospects_root=tmp_path)
    assert [t.price for t in brief.pricing] == ["£90", "£260", "£499"]
    # The starter trio is the recommended one.
    rec = [t for t in brief.pricing if t.is_recommended]
    assert len(rec) == 1
    assert rec[0].price == "£260"


def test_deck_brief_ads_carry_library_url(tmp_path: Path):
    _seed_audit(tmp_path)
    brief = DeckBrief.from_audit("test-brand", prospects_root=tmp_path)
    assert brief.ads, "expected at least one ad row"
    for ad in brief.ads:
        assert ad.library_url.startswith("https://www.facebook.com/ads/library/?id=")


def test_deck_brief_logo_path_resolved_but_image_not_embedded(tmp_path: Path):
    """V5: the logo_path on the brief is still resolved (so we can
    track which path the brand profile picked) but the renderer
    NEVER embeds the image. Hero monogram is text initials only and
    the wordmark lives in the topbar as text - both treatments work
    for any brand accent colour."""
    store = _seed_audit(tmp_path)
    brief = DeckBrief.from_audit("test-brand", prospects_root=tmp_path)
    assert brief.logo_path is not None  # brief still carries the resolved path
    out = build_html_deck(brief)
    html_text = out.read_text(encoding="utf-8")
    # Logo image is intentionally NOT embedded anywhere.
    assert html_text.count("logo.png") == 0
    # The hero monogram exists and carries text initials.
    cover_start = html_text.find('data-slide="1"')
    cover_end = html_text.find('data-slide="2"')
    cover_chunk = html_text[cover_start:cover_end]
    assert 'class="hero__monogram"' in cover_chunk
    assert store.root.exists()  # sanity


# --------------------------------------------------------------------------- #
# V2 microsite features (scroll reveal, sticky CTA, preview section, mobile)
# --------------------------------------------------------------------------- #


def test_html_has_scroll_reveal_observer(tmp_path: Path):
    """Sections animate in on scroll via IntersectionObserver + data-reveal."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Vanilla JS hook for reveals.
    assert "IntersectionObserver" in html
    # Every non-hero section is marked for reveal.
    assert "data-reveal" in html
    assert html.count("data-reveal") >= 7  # 45-sec, ads, gap, concepts, process, preview, pricing, next-step


def test_html_has_reduced_motion_css(tmp_path: Path):
    """Respect prefers-reduced-motion: reduce."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in html


def test_html_has_smooth_scroll(tmp_path: Path):
    """CSS smooth scrolling for in-page anchor jumps (hero CTA -> preview)."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "scroll-behavior: smooth" in html


def test_html_has_sticky_cta(tmp_path: Path):
    """Fixed pill that anchors to the preview section. V3 copy = 'Show me
    the first route' (was 'Send me' in V2 - warmer + product-shaped)."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert 'class="sticky-cta"' in html
    assert 'href="#preview-video"' in html
    assert "Show me the first route" in html


def test_html_has_scroll_progress_bar(tmp_path: Path):
    """Top scroll-progress indicator visible during scroll."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert 'class="progress-bar"' in html
    assert 'class="progress-bar__fill"' in html


def test_html_has_mobile_media_query(tmp_path: Path):
    """Mobile breakpoint exists so the layout reflows for small screens."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "@media (max-width: 720px)" in html
    # And a tablet breakpoint too.
    assert "@media (max-width: 1100px)" in html


def test_html_always_renders_preview_section_even_without_url(tmp_path: Path):
    """V2/V3: the preview section ALWAYS renders. Without a watermarked
    URL the frame is a premium locked placeholder rather than nothing.

    Rollback after V7: the locked placeholder is the simple lock card
    again ('Your first route lives here' + lock chip) - the busy V7
    storyboard treatment was a bloat addition that's been removed."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")  # no preview_video_url
    assert 'id="preview-video"' in html
    assert "Watermarked route preview" in html  # V3 section meta label
    assert "Your first route lives here" in html
    assert "<video" not in html  # but no <video> tag
    # The V7 storyboard treatment is gone.
    assert "preview__storyboard" not in html


def test_html_embeds_video_when_preview_url_provided(tmp_path: Path):
    """When a watermarked URL is passed, an inline <video> element points at it."""
    brief = _make_synthetic_brief(tmp_path)
    out = build_html_deck(
        brief, preview_video_url="assets/preview_watermarked.mp4"
    )
    html = out.read_text(encoding="utf-8")
    assert "<video" in html
    assert 'src="assets/preview_watermarked.mp4"' in html
    assert "Watermarked preview" in html
    # Placeholder copy is NOT rendered when the real video is present.
    assert "Your first route lives here" not in html


def test_html_preview_unwatermarked_filename_not_embedded(tmp_path: Path):
    """Sanity: the deck builder never auto-discovers an unwatermarked file -
    that is the microsite layer's job. The builder embeds exactly what it
    is given, so we should never see a bare 'preview.mp4' here unless
    explicitly passed."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "preview.mp4" not in html


def test_html_has_ad_sources_footnote(tmp_path: Path):
    """Live-ads section credits the public sources we read from."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "Sources: public Meta Ad Library" in html


def test_html_uses_ad_screenshot_image_when_path_provided(tmp_path: Path):
    """If an ad screenshot path exists locally, the image is used in the
    preview frame rather than the CSS-mock fallback."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # The synthetic brief gives the first ad an ad_1.png screenshot.
    assert "assets/ad_1.png" in html
    # The second ad has no screenshot, so a mock preview renders.
    assert "ad-card__preview--mock" in html


def test_html_has_primary_cta_pointing_to_preview(tmp_path: Path):
    """Hero CTA jumps to the preview section so the page tells a story."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Inside the hero section, the primary button anchors to preview-video.
    hero_start = html.find('data-slide="1"')
    hero_end = html.find('data-slide="2"')
    hero_chunk = html[hero_start:hero_end]
    assert 'href="#preview-video"' in hero_chunk
    assert "btn--primary" in hero_chunk


def test_html_hero_uses_full_bleed_brand_image(tmp_path: Path):
    """Hero promotes the brand's hero image to a full-bleed background."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert 'class="hero__bg"' in html
    # When a real hero exists, the image is the background source.
    assert "assets/hero.png" in html


def test_html_hero_uses_mock_when_no_brand_image(tmp_path: Path):
    """Without a hero image the background gracefully falls back to a
    premium gradient rather than a broken/blank panel."""
    brief = _make_synthetic_brief(tmp_path)
    brief.hero_image_path = None
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "hero__bg--mock" in html


# --------------------------------------------------------------------------- #
# V3 - real ad screenshot integration, scroll storytelling, conversion polish
# --------------------------------------------------------------------------- #


def test_html_uses_real_ad_screenshot_with_overlays(tmp_path: Path):
    """When an ad screenshot path exists, the card uses the real image
    and overlays floating LIVE-AD / days-active chips + 'Open ad' pill on
    top of it, instead of rendering the Meta-shaped fallback mock."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # The synthetic brief gives the first ad a screenshot; that card must
    # NOT carry the mock class on its preview frame.
    assert "assets/ad_1.png" in html
    # Floating overlays render on top of the real screenshot:
    assert "ad-card__overlay-top" in html
    assert "ad-card__chip--live" in html
    assert "Live ad" in html
    # Open-ad pill is also overlaid on the image.
    assert "ad-card__overlay-open" in html


def test_html_fallback_card_uses_meta_shaped_mock(tmp_path: Path):
    """The second ad in the synthetic brief has no screenshot. Its card
    should fall back to the Meta-shaped mock (avatar + sponsored chip +
    body + CTA bar) - not a generic placeholder."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Premium Meta-shaped fallback markers
    assert "ad-card__preview--mock" in html
    assert "ad-card__preview-avatar" in html
    assert "ad-card__preview-sponsored" in html
    assert "ad-card__preview-cta-button" in html


def test_html_live_ads_has_sticky_pin_layout(tmp_path: Path):
    """V3: live-ads section has a sticky-pin layout - left intro panel
    holds while right column scrolls past."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "live-ads-layout" in html
    assert "live-ads__intro" in html
    assert "live-ads__stack" in html
    # And the sticky CSS rule is present.
    assert "position: sticky" in html


def test_html_concepts_use_horizontal_rail(tmp_path: Path):
    """V3: concepts render in a horizontal scroll-snap rail rather than
    a static grid."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "concepts-rail" in html
    assert "scroll-snap-type" in html
    assert "scroll-snap-align" in html
    # And a swipe hint cues the reader.
    assert "concepts-rail-hint" in html


def test_html_has_parallax_hero(tmp_path: Path):
    """V3: hero background image gets parallax via a tiny scroll listener."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # JS reads the hero, attaches a passive scroll listener, and writes a
    # transform on the hero bg image.
    assert "var hero = document.querySelector('.hero')" in html
    assert "translate3d" in html
    assert "will-change: transform" in html


def test_html_concept_cards_use_stagger_index(tmp_path: Path):
    """V3 staggered reveal: concept and ad cards declare a reveal-index
    so the JS can apply a per-card transition-delay (staggered enter).

    V7: the featured concept is hoisted out of the rail (it owns its
    own section), so the secondary rail carries N-1 cards. We assert
    on indices 0..N-2 only."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    secondary_count = len(brief.concepts) - 1
    for i in range(secondary_count):
        assert f'data-reveal-index="{i}"' in html
    # And the JS reads the attribute to compute the transition delay.
    assert "data-reveal-index" in html
    assert "transitionDelay" in html


def test_html_preview_locked_frame_has_lock_chrome(tmp_path: Path):
    """V3 preview placeholder reads as a locked premium preview - a
    framed top bar + lock chip - not a 'coming soon' card."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "preview__frame--locked" in html
    assert "preview__frame-bar" in html
    assert "Reserved" in html
    assert "preview__placeholder-lock" in html


def test_html_preview_filled_frame_has_label_bar(tmp_path: Path):
    """When a real watermarked URL is provided, the same frame bar
    labels the embed as a watermarked route preview."""
    brief = _make_synthetic_brief(tmp_path)
    out = build_html_deck(
        brief, preview_video_url="assets/preview_watermarked.mp4"
    )
    html = out.read_text(encoding="utf-8")
    assert "preview__frame--filled" in html
    assert "preview__frame-bar" in html
    assert "Watermarked route preview" in html


def test_html_hero_cta_says_show_me_the_first_route(tmp_path: Path):
    """V3 conversion polish: hero primary CTA = 'Show me the first route'."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    hero_start = html.find('data-slide="1"')
    hero_end = html.find('data-slide="2"')
    hero_chunk = html[hero_start:hero_end]
    assert "Show me the first route" in hero_chunk
    assert 'href="#preview-video"' in hero_chunk


def test_html_hero_carries_senior_creative_note_explainer(tmp_path: Path):
    """V7 hero explainer reframes the page as a senior creative note,
    not as an audit narration. Pin the new phrasing."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "senior-led creative note" in html
    assert "ready to ship as a watermarked first cut" in html


def test_html_next_step_cta_says_reply_with_send_the_first_route(tmp_path: Path):
    """V3 next-step primary CTA spells out the reply instruction."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    next_start = html.find('data-slide="8"')
    next_chunk = html[next_start:]
    # `&lsquo;` is the HTML entity for the opening curly quote.
    assert "Reply with &lsquo;send the first route&rsquo;" in next_chunk


def test_html_does_not_leak_capture_status_fields(tmp_path: Path):
    """V3 enrichment fields (capture_status, capture_error, image_path_local,
    video_preview_path_local) are operator-side metadata - they must not
    appear in client-facing copy."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    for marker in (
        "capture_status",
        "capture_error",
        "image_path_local",
        "video_preview_path_local",
    ):
        assert marker not in html, f"deck leaked operator marker {marker!r}"


# --------------------------------------------------------------------------- #
# V4 - draft/deployed banner, stronger scroll motion, cream contrast section
# --------------------------------------------------------------------------- #


def test_html_draft_status_banner_renders_when_status_is_draft(tmp_path: Path):
    """V4: passing status='draft' renders a top strip that tells the
    operator the page is NOT deployed yet - so nobody clicks the planned
    public URL before the actual deploy happens."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief, status="draft").read_text(encoding="utf-8")
    # The actual draft strip element (not just a CSS class mention).
    assert '<div class="status-banner status-banner--draft"' in html
    assert "Local draft preview" in html
    assert "not deployed yet" in html
    # No deployed-state strip element.
    assert '<div class="status-banner status-banner--live"' not in html


def test_html_deployed_status_banner_shows_live_link(tmp_path: Path):
    """V4: status='deployed' + public_url renders the Live strip with
    the deployed URL inside it. The draft strip is suppressed."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(
        brief,
        status="deployed",
        public_url="https://yuvo-pitches.pages.dev/p/acme-skincare-abc123/",
    ).read_text(encoding="utf-8")
    assert '<div class="status-banner status-banner--live"' in html
    assert "yuvo-pitches.pages.dev/p/acme-skincare-abc123/" in html
    assert '<div class="status-banner status-banner--draft"' not in html
    assert "Local draft preview" not in html


def test_html_no_banner_when_status_is_none(tmp_path: Path):
    """Deck mode (no manifest, no status passed) renders no banner -
    the deck is a static PDF-style handout, not a live preview.
    (The CSS rules still ship in <style>, but no banner element is
    emitted in the document.)"""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert '<div class="status-banner' not in html


def test_microsite_default_renders_draft_banner(tmp_path: Path):
    """V4: build_microsite() defaults to status='draft' on first build,
    so the local preview HTML always carries the draft warning."""
    from agents.outreach.reporting.microsite_builder import build_microsite

    prospect_root = tmp_path / "prospects" / "acme-skincare"
    prospect_root.mkdir(parents=True, exist_ok=True)
    brief = _make_synthetic_brief(prospect_root)
    build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    assert "Local draft preview" in html
    assert "not deployed yet" in html


def test_microsite_keeps_deployed_status_across_rebuilds(tmp_path: Path):
    """V4: once a manifest carries status='deployed', re-running
    build_microsite respects it instead of silently downgrading the
    banner back to 'draft'. (Otherwise every re-render would erase the
    Live state on the local preview.)"""
    import json

    from agents.outreach.reporting.microsite_builder import build_microsite

    prospect_root = tmp_path / "prospects" / "acme-skincare"
    prospect_root.mkdir(parents=True, exist_ok=True)
    brief = _make_synthetic_brief(prospect_root)
    manifest = build_microsite("acme-skincare", brief=brief)
    # Simulate a successful deploy.
    manifest_path = prospect_root / "site" / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["status"] = "deployed"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    # Re-run.
    build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    assert '<div class="status-banner status-banner--live"' in html
    assert '<div class="status-banner status-banner--draft"' not in html
    assert manifest["public_url"] in html


def test_html_parallax_intensity_v4(tmp_path: Path):
    """V4: parallax was strengthened. Both the bg-image speed multiplier
    and the headline counter-motion should appear in the JS payload."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Hero bg moves at ~55% of scroll (was 35% in V3).
    assert "-rect.top * 0.55" in html
    # Headline counter-motion writes to .hero__body
    assert "hero__body" in html
    assert "-rect.top * 0.15" in html
    # And the resting transform is now scale(1.10) (was 1.04 in V3).
    assert "scale(1.10)" in html


def test_html_gap_map_uses_cream_contrast_section(tmp_path: Path):
    """V4: the gap-map section flips the page from dark to cream, a
    dramatic scroll-contrast moment mid-page."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "section--cream" in html
    # The cream class lives on the gap-map section specifically.
    gap_start = html.find('id="gap-map"')
    assert gap_start > 0
    section_open = html.rfind("<section", 0, gap_start)
    assert "section--cream" in html[section_open:gap_start]


def test_html_live_ads_has_progress_stripe(tmp_path: Path):
    """V4: the sticky live-ads intro has a vertical accent stripe that
    fills as you scroll - obvious visible cue that the pin is working."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "live-ads__progress" in html
    assert "live-ads__progress-fill" in html
    # And the JS drives it via scaleY.
    assert "scaleY" in html


def test_html_concept_focal_spotlight_in_js(tmp_path: Path):
    """V4: concept rail has a focal-card spotlight effect. JS toggles
    .concept--focal on the centred card and .concept--dimmed on the rest."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "concept--focal" in html
    assert "concept--dimmed" in html
    # And the spotlight CSS scales the focal card.
    assert ".concept--focal {" in html


def test_html_reveal_motion_strengthened_v4(tmp_path: Path):
    """V4: the reveal motion went from 28px/700ms to 56px/950ms + scale.
    The fixed assertion is enough to detect any future weakening."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "translateY(56px) scale(0.98)" in html
    assert "950ms" in html


# --------------------------------------------------------------------------- #
# V4 - interest form + Cloudflare endpoint future-readiness
# --------------------------------------------------------------------------- #


def test_interest_section_renders_with_title_and_subtitle(tmp_path: Path):
    """The closing 'Want us to make the first route?' section is the
    conversion surface for the microsite. Its title + subtitle must
    surface verbatim so the prospect understands the ask."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert 'id="interest"' in html
    assert "Want us to make the first route?" in html
    # V7 premium subtitle.
    # Apostrophes are HTML-escaped via html.escape().
    assert "we&#x27;ll send the next step personally" in html or "we'll send the next step personally" in html
    # The form panel renders even without an endpoint configured.
    assert "interest__panel" in html


def test_interest_form_mailto_fallback_is_default(tmp_path: Path):
    """When no form_endpoint is configured, the submit action is a
    mailto: link, not a POST form. This is the MVP path."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "mailto:" in html
    # V7 premium CTA wording: "Email my route request".
    assert "Email my route request" in html
    # No POST form rendered in the interest panel.
    assert 'action="/api/interest"' not in html


def test_interest_mailto_subject_includes_brand_name(tmp_path: Path):
    """Mailto subject auto-names the brand so the operator inbox is
    auto-threaded ('Interested in the first route for <Brand>')."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # `mailto:` URL-encodes spaces as %20; just check for the encoded brand chunk.
    assert "Interested%20in%20the%20first%20route%20for%20Acme%20Skincare" in html


def test_interest_mailto_body_includes_prospect_identifiers(tmp_path: Path):
    """The mailto body carries Brand / Prospect ID / Private slug /
    Public URL labelled lines so the operator can identify the audit
    without parsing the URL by hand."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(
        brief,
        prospect_id="acme-skincare",
        private_slug="acme-skincare-abc123",
        public_url="https://yuvo-pitches.pages.dev/p/acme-skincare-abc123/",
    ).read_text(encoding="utf-8")
    # `%0A` is the URL-encoded newline used to separate body lines.
    assert "Brand%3A%20Acme%20Skincare" in html
    assert "Prospect%20ID%3A%20acme-skincare" in html
    assert "Private%20slug%3A%20acme-skincare-abc123" in html
    assert "Public%20URL%3A%20https%3A//yuvo-pitches.pages.dev/p/acme-skincare-abc123/" in html


def test_interest_mailto_uses_contact_email_when_set(tmp_path: Path):
    """An explicit `contact_email` kwarg flows through to the mailto: URL."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(
        brief, contact_email="route@example.com"
    ).read_text(encoding="utf-8")
    assert "mailto:route%40example.com" in html


def test_interest_default_contact_email_is_yuvostudio(tmp_path: Path):
    """When no contact_email is passed, the fallback is the public
    yuvostudio.com inbox - a public mailbox, safe to ship in HTML."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "mailto:hello%40yuvostudio.com" in html


def test_interest_form_never_contains_api_token(tmp_path: Path):
    """The form never carries a secret. Both modes - mailto and POST -
    must keep credentials out of the HTML. We pin against common
    placeholder names so a future regression is loud."""
    brief = _make_synthetic_brief(tmp_path)
    for endpoint in (None, "/api/interest"):
        html = build_html_deck(
            brief,
            form_endpoint=endpoint,
            contact_email="hello@yuvostudio.com",
        ).read_text(encoding="utf-8")
        for marker in (
            "API_KEY",
            "api_token",
            "Bearer ",
            "RESEND_API_KEY",
            "CLOUDFLARE_API_TOKEN",
        ):
            assert marker not in html, f"deck leaked credential marker {marker!r}"


def test_interest_endpoint_mode_renders_post_form(tmp_path: Path):
    """When form_endpoint is configured, the submit element is a real
    `<form method="POST" action=...>` and the mailto fallback is
    suppressed."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(
        brief, form_endpoint="/api/interest"
    ).read_text(encoding="utf-8")
    assert 'method="POST"' in html
    assert 'action="/api/interest"' in html
    # V7 premium helper note + "reviewed manually" framing.
    assert "reviewed manually by Yuvo Studio" in html
    # Mailto suppressed entirely - mailto: should not appear in the deck.
    assert "mailto:" not in html


def test_interest_endpoint_mode_carries_hidden_prospect_identifiers(tmp_path: Path):
    """In POST mode the hidden fields carry the same identifiers the
    mailto body did, so a future backend can identify the audit."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(
        brief,
        form_endpoint="/api/interest",
        prospect_id="acme-skincare",
        private_slug="acme-skincare-abc123",
        public_url="https://yuvo-pitches.pages.dev/p/acme-skincare-abc123/",
    ).read_text(encoding="utf-8")
    assert '<input type="hidden" name="brand_name" value="Acme Skincare">' in html
    assert '<input type="hidden" name="prospect_id" value="acme-skincare">' in html
    assert '<input type="hidden" name="private_slug" value="acme-skincare-abc123">' in html
    assert '<input type="hidden" name="public_url" value="https://yuvo-pitches.pages.dev/p/acme-skincare-abc123/">' in html


def test_interest_visible_fields_render_in_both_modes(tmp_path: Path):
    """Name / email / availability / message inputs are present in
    mailto mode (visual only) AND in endpoint mode (live)."""
    brief = _make_synthetic_brief(tmp_path)
    for endpoint in (None, "/api/interest"):
        html = build_html_deck(brief, form_endpoint=endpoint).read_text(encoding="utf-8")
        for control_id in (
            'id="interest-name"',
            'id="interest-email"',
            'id="interest-availability"',
            'id="interest-message"',
        ):
            assert control_id in html, f"missing input control {control_id!r} (endpoint={endpoint!r})"


def test_sticky_cta_points_to_interest(tmp_path: Path):
    """V4: the sticky CTA target changed from #preview-video to #interest -
    the deck's conversion surface is the form, not the preview slide."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert 'class="sticky-cta" href="#interest"' in html


def test_preview_section_cta_points_to_interest(tmp_path: Path):
    """V4/V7: the preview-section primary CTA routes to the interest
    form. V7 swapped the label from 'Want the clean version?' to
    'Send the route request' so the CTA points the prospect at the
    actual conversion surface, not back at a question."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    preview_start = html.find('id="preview-video"')
    preview_end = html.find('id="pricing"')
    preview_chunk = html[preview_start:preview_end]
    assert 'href="#interest"' in preview_chunk
    # New CTA label (replaces "Want the clean version?").
    assert "Send the route request" in preview_chunk


def test_next_step_primary_button_points_to_interest(tmp_path: Path):
    """V4: the next-step primary 'Reply with send the first route'
    button now routes to the interest form so the prospect lands on
    the actual submission surface."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    next_start = html.find('data-slide="8"')
    next_chunk = html[next_start:next_start + 5000]
    assert 'href="#interest"' in next_chunk
    assert "Reply with &lsquo;send the first route&rsquo;" in next_chunk


# --------------------------------------------------------------------------- #
# Microsite-level interest-form wiring
# --------------------------------------------------------------------------- #


def test_microsite_uses_pitch_contact_email_env(tmp_path: Path, monkeypatch):
    """`PITCH_CONTACT_EMAIL` in the environment becomes the mailto destination."""
    from agents.outreach.reporting.microsite_builder import build_microsite

    monkeypatch.setenv("PITCH_CONTACT_EMAIL", "founder@example.com")
    monkeypatch.delenv("PITCH_FORM_ENDPOINT", raising=False)

    prospect_root = tmp_path / "prospects" / "acme-skincare"
    prospect_root.mkdir(parents=True, exist_ok=True)
    brief = _make_synthetic_brief(prospect_root)
    build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    assert "mailto:founder%40example.com" in html
    assert 'action="/api/interest"' not in html


def test_microsite_uses_pitch_form_endpoint_env(tmp_path: Path, monkeypatch):
    """`PITCH_FORM_ENDPOINT` flips the microsite into POST mode."""
    from agents.outreach.reporting.microsite_builder import build_microsite

    monkeypatch.setenv("PITCH_FORM_ENDPOINT", "/api/interest")
    monkeypatch.delenv("PITCH_CONTACT_EMAIL", raising=False)

    prospect_root = tmp_path / "prospects" / "acme-skincare"
    prospect_root.mkdir(parents=True, exist_ok=True)
    brief = _make_synthetic_brief(prospect_root)
    build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    assert 'action="/api/interest"' in html
    assert "mailto:" not in html


def test_microsite_passes_prospect_id_and_slug_to_interest_form(tmp_path: Path):
    """The microsite builder threads the prospect_id and the persisted
    private_slug into the interest form so a future backend can identify
    submissions without parsing the URL."""
    from agents.outreach.reporting.microsite_builder import build_microsite

    prospect_root = tmp_path / "prospects" / "acme-skincare"
    prospect_root.mkdir(parents=True, exist_ok=True)
    brief = _make_synthetic_brief(prospect_root)
    manifest = build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    # The mailto body carries the identifiers (URL-encoded).
    assert "Prospect%20ID%3A%20acme-skincare" in html
    assert f"Private%20slug%3A%20{manifest['private_slug']}" in html


# --------------------------------------------------------------------------- #
# V4 concept-rail fix: real images vs designed-fallback frames
# --------------------------------------------------------------------------- #


def _count_concept_cards(html: str) -> dict[str, int]:
    """Helper: count filled vs designed concept cards in the rendered HTML."""
    import re
    cards = re.findall(r'<article class="concept"[^>]*>.*?</article>', html, re.S)
    return {
        "total": len(cards),
        "filled": sum(1 for c in cards if "concept__phone--filled" in c),
        "designed": sum(1 for c in cards if "concept__phone--designed" in c),
        "with_img": sum(1 for c in cards if "<img " in c),
    }


def test_concept_cards_embed_real_image_when_visual_path_present(tmp_path: Path):
    """Regression for the V4 concept-rail bug: every concept whose
    `visual_path` resolves to a usable image must render as
    `concept__phone--filled` with a real <img> inside the phone frame.

    Rollback after V7: the V7 featured-route hoist is gone, so all
    four concepts go to the rail. With 2 concepts that have product
    images, the rail has 2 filled cards + 2 designed fallbacks."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    counts = _count_concept_cards(html)
    assert counts["total"] == 4, (
        f"expected 4 rail cards, got {counts['total']}"
    )
    assert counts["filled"] == 2, (
        f"expected 2 filled rail cards, got {counts['filled']}"
    )
    assert counts["with_img"] == 2
    # And neither the V7 featured-route section nor its class survives.
    assert 'id="featured-route"' not in html
    assert "featured-route" not in html
    # Both product images still render in the rail.
    assert "assets/product_a.png" in html
    assert "assets/product_b.png" in html


def test_concept_designed_fallback_renders_for_concepts_without_visual(tmp_path: Path):
    """Concepts without a `visual_path` must render the premium
    HOOK-LED fallback frame, NOT an empty mock placeholder. The
    fallback carries the hook block, the small corner brand chip,
    the 'OPENING FRAME' label, and the CTA - and explicitly NO
    giant centred monogram."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    counts = _count_concept_cards(html)
    # 2 concepts with images, 2 without -> 2 designed-fallback cards.
    assert counts["designed"] == 2
    # Designed fallback chrome: corner mark + hook block + OPENING FRAME
    assert "concept__phone-corner-mark" in html
    assert "concept__phone-hook-block" in html
    assert "OPENING FRAME" in html
    # No giant centered monogram anywhere on the concept rail (V4 rule).
    import re
    cards = re.findall(r'<article class="concept"[^>]*>.*?</article>', html, re.S)
    for card in cards:
        assert "concept__phone-monogram" not in card, (
            "concept card re-introduced the giant central monogram"
        )


def test_concept_filled_card_uses_bottom_only_gradient(tmp_path: Path):
    """The filled concept overlay must NOT cover the whole product
    image. Only a bottom-half gradient should darken the frame so the
    hook stays legible without blacking out the product."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # The bottom-only gradient CSS rule lives in the stylesheet.
    assert ".concept__phone--filled .concept__overlay" in html
    # Top of the gradient ends well below the top of the frame (75%
    # transparent stop) - if a future regression slips a full-frame
    # `rgba(0,0,0,0.78)` block back in we want a loud failure.
    assert "rgba(8,8,8,0) 75%" in html or "rgba(8,8,8,0) 70%" in html


def test_concept_image_uses_object_fit_cover(tmp_path: Path):
    """The concept phone image must use object-fit: cover so vertical
    crops look right even for horizontal source images."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # CSS rule for the concept image.
    assert ".concept__phone img {" in html
    assert "object-fit: cover" in html


def test_concept_designed_fallback_includes_hook_label_cta(tmp_path: Path):
    """Designed-fallback concept frames must carry the hook line, the
    route label, and the CTA pill - so even without imagery the card
    reads as a finished concept mockup, not a missing asset."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # The two image-less concepts in the synthetic brief.
    for concept in brief.concepts:
        if concept.visual_path is None:
            assert concept.hook in html
            assert concept.cta in html
            # Route label (Route A / Route B / ...) renders on the card.
            assert concept.label in html


def test_concept_board_never_emits_empty_dark_mock(tmp_path: Path):
    """The legacy empty-mock class (`concept__phone--mock` with no
    overlay content) must not appear in concept cards. Any phone frame
    on the rail must be either filled or designed-fallback."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Inside concept cards specifically.
    import re
    cards = re.findall(r'<article class="concept"[^>]*>.*?</article>', html, re.S)
    for card in cards:
        # Either filled (has img) or designed (has monogram).
        if "concept__phone--filled" in card:
            assert "<img " in card
        else:
            assert "concept__phone--designed" in card, (
                "concept fell back to a non-designed phone class - "
                "would render as an empty dark rectangle"
            )


def test_concept_board_does_not_leak_debug_markers(tmp_path: Path):
    """Concept rail content must not surface debug strings such as
    'empty', 'missing image', or capture-status fragments."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    import re
    cards = re.findall(r'<article class="concept"[^>]*>.*?</article>', html, re.S)
    blob = "\n".join(cards).lower()
    for marker in (
        "missing image",
        "no image",
        "capture_status",
        "capture_error",
        "image_path_local",
    ):
        assert marker not in blob, f"concept rail leaked debug marker {marker!r}"


def test_deck_brief_assigns_product_image_to_every_available_concept(tmp_path: Path):
    """V4 concept-rail bug regression: the brief MUST assign a
    product_image to each of the first N concepts (N = number of
    available product images), not pre-claim them all into used_paths
    so the concept builder sees zero candidates."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore

    prospect_root = tmp_path / "prospects" / "p1"
    prospect_root.mkdir(parents=True, exist_ok=True)
    # Create 3 useful product images on disk so DeckBrief.from_audit can
    # resolve their paths.
    p1 = _make_real_png(prospect_root / "assets" / "p_one.png")
    p2 = _make_real_png(prospect_root / "assets" / "p_two.png")
    p3 = _make_real_png(prospect_root / "assets" / "p_three.png")

    store = ProspectStore("p1", prospects_root=tmp_path / "prospects")
    audit = ProspectAudit(
        prospect_id="p1",
        prospect_name="P1 Brand",
        niche="skincare",
        competitor_ads=[],
        weaknesses=[],
        brand_profile={
            "product_images": [
                str(p1.relative_to(prospect_root)),
                str(p2.relative_to(prospect_root)),
                str(p3.relative_to(prospect_root)),
            ],
        },
    )
    store.save_audit(audit)

    brief = DeckBrief.from_audit("p1", prospects_root=tmp_path / "prospects")
    visuals = [c.visual_path for c in brief.concepts[:3]]
    assert all(v is not None for v in visuals), (
        f"expected 3 concept visuals to be assigned, got {visuals}"
    )
    # And they should be the three product images, in order.
    assert visuals[0].name == "p_one.png"
    assert visuals[1].name == "p_two.png"
    assert visuals[2].name == "p_three.png"


def test_microsite_concept_rail_uses_real_images_when_audit_carries_product_images(
    tmp_path: Path,
):
    """End-to-end: build_microsite -> concept rail HTML carries real
    <img> tags pointing at copied site/assets/ files."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore
    from agents.outreach.reporting.microsite_builder import build_microsite

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "rebrand"
    prospect_root.mkdir(parents=True, exist_ok=True)
    p1 = _make_real_png(prospect_root / "assets" / "real_one.png")
    p2 = _make_real_png(prospect_root / "assets" / "real_two.png")

    store = ProspectStore("rebrand", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="rebrand",
            prospect_name="Rebrand Co",
            niche="skincare",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "product_images": [
                    str(p1.relative_to(prospect_root)),
                    str(p2.relative_to(prospect_root)),
                ],
            },
        )
    )
    build_microsite("rebrand", prospects_root=prospects_root)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")

    # Rollback after V7: the featured-route hoist is gone, all 4 concept
    # cards live on the rail. With 2 product images supplied, the rail
    # shows 2 filled + 2 designed-fallback cards.
    counts = _count_concept_cards(html)
    assert counts["total"] == 4
    assert counts["filled"] == 2
    assert counts["with_img"] == 2
    # The copied site/assets/ files exist and the HTML references them.
    site_assets = prospect_root / "site" / "assets"
    assert site_assets.exists()
    site_files = {p.name for p in site_assets.iterdir()}
    assert "real_one.png" in site_files
    assert "real_two.png" in site_files
    assert "real_one.png" in html
    assert "real_two.png" in html


# --------------------------------------------------------------------------- #
# V4 - logo / brand-mark classification + hero fallback polish
# --------------------------------------------------------------------------- #


def _make_logo_shaped_png(path: Path) -> Path:
    """Same byte payload as `_make_real_png` but the filename screams
    'logo' so the classifier can spot it."""
    return _make_real_png(path)


def test_looks_like_logo_path_detects_common_logo_filenames():
    """Filename-based logo classifier catches Shopify/Squarespace
    auto-export filenames the brand-assets collector dumps into
    product_images."""
    from agents.outreach.reporting.deck_brief import _looks_like_logo_path

    assert _looks_like_logo_path(Path("YANA_Logo_black_400x.png"))
    assert _looks_like_logo_path(Path("brand-wordmark.svg.png"))
    assert _looks_like_logo_path(Path("favicon-32.png"))
    assert _looks_like_logo_path(Path("apple-touch-icon-180.png"))

    # Real product / lifestyle photos must NOT match.
    assert not _looks_like_logo_path(Path("IMG_7619_copy.jpg"))
    assert not _looks_like_logo_path(Path("hero-skincare-bath.jpg"))
    assert not _looks_like_logo_path(Path("collection_summer.png"))


def test_deck_brief_filters_logo_shaped_paths_out_of_concept_visuals(tmp_path: Path):
    """A brand_profile that lists a logo-shaped filename in
    `product_images` (which the collector occasionally dumps there
    when it already has a primary logo) must NOT propagate that logo
    onto a concept card.

    V4 policy: the logo is DROPPED ENTIRELY, never auto-promoted to
    logo_path. Those files are usually wide wordmark exports that
    would crop badly in the circular brand badge - the operator
    sets `logo_path` explicitly to a circle-safe asset when one
    exists."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "prosp"
    prospect_root.mkdir(parents=True, exist_ok=True)
    real_product = _make_real_png(prospect_root / "assets" / "IMG_real.png")
    rogue_logo = _make_real_png(prospect_root / "assets" / "BRAND_Logo_black_400x.png")

    store = ProspectStore("prosp", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="prosp",
            prospect_name="Brand X",
            niche="skincare",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "product_images": [
                    str(real_product.relative_to(prospect_root)),
                    str(rogue_logo.relative_to(prospect_root)),
                ],
            },
        )
    )
    brief = DeckBrief.from_audit("prosp", prospects_root=prospects_root)

    # Only the real product image should reach the concept visuals.
    visual_paths = [c.visual_path for c in brief.concepts if c.visual_path is not None]
    assert real_product in visual_paths
    assert rogue_logo not in visual_paths
    # And product_images should no longer carry the logo.
    assert rogue_logo not in brief.product_images
    # The logo is NOT promoted to logo_path (V4 dropped that auto-
    # promote rule). Wordmark exports never make it into the small
    # circular badge.
    assert brief.logo_path is None


def test_deck_brief_skips_logo_shaped_path_when_logo_already_set(tmp_path: Path):
    """If logo_path is already set (a real favicon was collected), a
    second logo-shaped file from product_images is DROPPED entirely.
    The deck must not silently surface it as a product."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "yana"
    prospect_root.mkdir(parents=True, exist_ok=True)
    favicon = _make_real_png(prospect_root / "assets" / "favicon-32.png")
    rogue_logo = _make_real_png(prospect_root / "assets" / "BRAND_Logo_black_400x.png")
    real_product = _make_real_png(prospect_root / "assets" / "IMG_real.png")

    store = ProspectStore("yana", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="yana",
            prospect_name="Yana",
            niche="activewear",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "logo_path": str(favicon.relative_to(prospect_root)),
                "product_images": [
                    str(real_product.relative_to(prospect_root)),
                    str(rogue_logo.relative_to(prospect_root)),
                ],
            },
        )
    )
    brief = DeckBrief.from_audit("yana", prospects_root=prospects_root)
    assert brief.logo_path == favicon  # original logo kept
    assert rogue_logo not in brief.product_images
    visuals = [c.visual_path for c in brief.concepts if c.visual_path]
    assert rogue_logo not in visuals
    assert real_product in visuals


def test_deck_brief_rejects_logo_shaped_hero(tmp_path: Path):
    """A logo-shaped hero (e.g. brand_profile points at the wordmark
    PNG) must NOT be used as the hero background - it would render a
    tiny vector mark on a full-bleed dark frame. The hero falls
    through to the designed mock instead."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "x"
    prospect_root.mkdir(parents=True, exist_ok=True)
    rogue_hero = _make_real_png(prospect_root / "assets" / "site-logo-wide.png")

    store = ProspectStore("x", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="x",
            prospect_name="X Brand",
            niche="fitness",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "hero_image_path": str(rogue_hero.relative_to(prospect_root)),
            },
        )
    )
    brief = DeckBrief.from_audit("x", prospects_root=prospects_root)
    assert brief.hero_image_path is None


def test_hero_uses_real_image_when_available(tmp_path: Path):
    """When hero_image_path resolves to a usable non-logo file, the
    hero section embeds it as the <img> in hero__bg (not the mock)."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert 'class="hero__bg"' in html
    # The mock fallback is NOT used.
    assert "hero__bg--mock" not in html or "hero__bg hero__bg--mock" not in html
    # The hero photo IS embedded.
    assert "hero.png" in html


def test_hero_overlay_is_brighter_than_v3(tmp_path: Path):
    """V4: opacity bumped from 0.55 -> 0.85 so the real photo is
    legible, and the left-to-right wash on hero__gradient was removed
    (it made the right half look like an empty panel)."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # New opacity floor for the hero img.
    assert "opacity: 0.85" in html
    # Old left-right dark wash on hero__gradient is gone.
    # (The css var keeps the bottom fade only.)
    # If a regression slips the old gradient back in we want a loud
    # failure - check for the unique old token.
    assert "rgba(10,10,10,0.55) 0%, rgba(10,10,10,0) 60%" not in html


def test_hero_fallback_renders_designed_collage_not_empty_block(tmp_path: Path):
    """When no hero photo exists, the hero fallback must render a
    layered collage of small premium cards (proof chip + 9:16 route
    frame + caption sliver). No giant centred brand monogram anywhere.
    Reads as an intentional 'first route in progress' preview."""
    brief = _make_synthetic_brief(tmp_path)
    # Wipe the hero so the mock branch fires.
    brief.hero_image_path = None
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "hero__bg hero__bg--mock" in html
    # New collage chrome.
    assert "hero__mock-collage" in html
    assert "hero__mock-card--proof" in html
    assert "hero__mock-card--frame" in html
    assert "hero__mock-card--caption" in html
    # Real labels in the markup, not just CSS classes.
    assert "LIVE AD READ" in html
    assert "9:16 ROUTE FRAME" in html
    assert "FIRST ROUTE" in html
    # Old giant-monogram element is gone for good.
    assert "hero__mock-frame-monogram" not in html


def test_hero_monogram_is_always_text_chip_never_image(tmp_path: Path):
    """V5: hero__monogram is ALWAYS a small text chip - it never
    embeds the logo image, regardless of whether the brand has a logo
    file in their brand_profile. This avoids the 'giant YA block' bug
    where a small favicon was stretched into a 76px brand-color
    circle and dominated the hero panel."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "hero__monogram" in html
    # No <img> inside the monogram even though the brief carries a logo.
    import re
    mono = re.search(r'<div class="hero__monogram">(.*?)</div>', html, re.S)
    assert mono is not None
    assert "<img" not in mono.group(1)
    # The monogram carries text (initials).
    assert mono.group(1).strip()


def test_concept_filled_overlay_does_not_use_full_frame_black(tmp_path: Path):
    """Concept image overlay must be a bottom-only gradient, never a
    full-frame dark wash that blacks out the product."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # The full-frame 0.78 black wash from V3 must not appear inside
    # the concept overlay rule.
    assert ".concept__phone--filled .concept__overlay" in html
    # Check the CSS rule itself: gradient must end transparent in the
    # upper portion of the frame.
    import re
    rule = re.search(
        r"\.concept__phone--filled \.concept__overlay \{[^}]+\}", html
    )
    assert rule is not None
    body = rule.group(0)
    # Top of gradient: 0% must be near-opaque dark, but 75%+ must be
    # fully transparent so the product shows.
    assert "rgba(8,8,8,0) 75%" in body or "rgba(8,8,8,0) 70%" in body


def test_brand_assets_does_not_demote_logo_to_product():
    """Regression: when a 2nd logo-shaped URL is encountered AND a
    primary logo is already set, the collector must NOT relabel it
    as 'product'. (V4 fix - that demote was the source of the rogue
    YANA wordmark sliding onto a concept card.)"""
    # We don't run the real network. Verify the source code by hashing
    # the relevant control flow: the `continue` after the second-logo
    # check must replace the old `label = "product"` re-tag.
    src = Path("agents/outreach/brand_assets.py").read_text(encoding="utf-8")
    # Old buggy line must not re-appear.
    assert 'if label == "logo" and have_logo:\n            label = "product"' not in src
    # New behaviour is `continue` instead.
    assert 'if label == "logo" and have_logo:\n            continue' in src


# --------------------------------------------------------------------------- #
# V4 - no giant logo / monogram as a main visual
# --------------------------------------------------------------------------- #


def _make_wide_wordmark_png(path: Path, width: int = 400, height: int = 200) -> Path:
    """Make a real PNG with horizontal-rectangle dimensions (wide
    wordmark shape)."""
    return _make_real_png(path, width=width, height=height)


def _make_tall_logo_png(path: Path, width: int = 64, height: int = 320) -> Path:
    """Tall non-square logo - should also be rejected from the badge."""
    return _make_real_png(path, width=width, height=height)


def test_logo_is_circle_safe_accepts_small_square():
    """A 32x32 favicon-shaped PNG is the canonical circle-safe logo."""
    import tempfile

    from agents.outreach.reporting.deck_brief import _logo_is_circle_safe
    with tempfile.TemporaryDirectory() as td:
        p = _make_real_png(Path(td) / "favicon.png", width=32, height=32)
        assert _logo_is_circle_safe(p) is True


def test_logo_is_circle_safe_rejects_wide_wordmark(tmp_path: Path):
    """A 400x200 wordmark shape is REJECTED - it would crop badly in
    a circular badge."""
    from agents.outreach.reporting.deck_brief import _logo_is_circle_safe

    p = _make_wide_wordmark_png(tmp_path / "WIDE_logo.png")
    assert _logo_is_circle_safe(p) is False


def test_logo_is_circle_safe_rejects_large_square(tmp_path: Path):
    """Even square logos that are bigger than 256 px on the long edge
    are rejected - those are usually full-bleed marketing exports, not
    a tight brand chip."""
    from agents.outreach.reporting.deck_brief import _logo_is_circle_safe

    p = _make_real_png(tmp_path / "huge.png", width=512, height=512)
    assert _logo_is_circle_safe(p) is False


def test_logo_is_circle_safe_rejects_tall_logo(tmp_path: Path):
    """Tall logos (aspect ratio > 1.4 the other way) are also out."""
    from agents.outreach.reporting.deck_brief import _logo_is_circle_safe

    p = _make_tall_logo_png(tmp_path / "tall_logo.png")
    assert _logo_is_circle_safe(p) is False


def test_deck_brief_drops_wide_wordmark_logo_path(tmp_path: Path):
    """A brand_profile that points logo_path at a wide wordmark PNG
    must end up with `brief.logo_path == None` - the renderer's text
    initials fallback is the only safe option."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "wid"
    prospect_root.mkdir(parents=True, exist_ok=True)
    wide_logo = _make_wide_wordmark_png(prospect_root / "assets" / "BRAND_logo.png")

    store = ProspectStore("wid", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="wid",
            prospect_name="Wide Co",
            niche="skincare",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "logo_path": str(wide_logo.relative_to(prospect_root)),
            },
        )
    )
    brief = DeckBrief.from_audit("wid", prospects_root=prospects_root)
    assert brief.logo_path is None


def test_deck_brief_accepts_small_square_favicon_under_old_threshold(tmp_path: Path):
    """A 32x32 favicon is typically 2-3 KB - well under the 4 KB
    hero/concept threshold. The dedicated `_is_useful_logo` check
    must accept it for the logo slot."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "fav"
    prospect_root.mkdir(parents=True, exist_ok=True)
    # 32x32 PNG. The _make_real_png helper guarantees >= 4 KB via tEXt
    # padding, but we still confirm the path is accepted on the logo
    # path (the path goes through _is_useful_logo, not _is_useful_image).
    favicon = _make_real_png(prospect_root / "assets" / "site-favicon-32.png", width=32, height=32)

    store = ProspectStore("fav", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="fav",
            prospect_name="Fav Co",
            niche="skincare",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "logo_path": str(favicon.relative_to(prospect_root)),
            },
        )
    )
    brief = DeckBrief.from_audit("fav", prospects_root=prospects_root)
    assert brief.logo_path == favicon


def test_concept_fallback_does_not_render_any_giant_monogram(tmp_path: Path):
    """Hard pin: the designed concept fallback must NEVER carry an
    element with the legacy `concept__phone-monogram` class. Catches
    any future regression that tries to slip the giant central YA
    back in.

    V7: one concept is hoisted to the featured-route hero, so the
    rail counts 3 cards (4 concepts - 1 featured)."""
    import dataclasses
    brief = _make_synthetic_brief(tmp_path)
    # Force every concept to fall back. ConceptRoute is frozen, so we
    # replace each instance with a copy whose visual_path is None.
    brief.concepts = [
        dataclasses.replace(c, visual_path=None) for c in brief.concepts
    ]
    html = build_html_deck(brief).read_text(encoding="utf-8")
    import re
    cards = re.findall(r'<article class="concept"[^>]*>.*?</article>', html, re.S)
    # Rollback after V7: full rail of 4 cards.
    assert len(cards) == 4
    for card in cards:
        assert "concept__phone-monogram" not in card
    # The V7 featured-route section is gone.
    assert 'id="featured-route"' not in html


def test_concept_fallback_carries_corner_mark_at_most_two_chars(tmp_path: Path):
    """The brand chip in the corner is text-only, capped to two
    characters. Never a full logo image - the rule is the brand mark
    occupies a small corner area, not the centre of the frame."""
    import dataclasses
    brief = _make_synthetic_brief(tmp_path)
    brief.concepts = [
        dataclasses.replace(c, visual_path=None) for c in brief.concepts
    ]
    html = build_html_deck(brief).read_text(encoding="utf-8")
    import re
    # Find every concept__phone-corner-mark in the document and check
    # its content is short text, not an <img>.
    matches = re.findall(
        r'<span class="concept__phone-corner-mark"[^>]*>(.*?)</span>', html, re.S
    )
    assert matches, "designed fallback missing corner brand mark"
    for content in matches:
        assert "<img" not in content
        assert len(content.strip()) <= 2


def test_concept_fallback_carries_hook_block_and_cta(tmp_path: Path):
    """Each designed-fallback card must carry the hook block (eyebrow
    + hook line) and the CTA row. Pins the hook-led composition."""
    import dataclasses
    brief = _make_synthetic_brief(tmp_path)
    brief.concepts = [
        dataclasses.replace(c, visual_path=None) for c in brief.concepts
    ]
    html = build_html_deck(brief).read_text(encoding="utf-8")
    import re
    cards = re.findall(
        r'<div class="concept__phone concept__phone--designed[^"]*"[^>]*>(.*?)</div>\s*<span class="concept__label"',
        html, re.S,
    )
    assert cards, "no designed fallback phone frames found"
    for card in cards:
        assert "concept__phone-hook-block" in card
        assert "concept__phone-hook-eyebrow" in card
        assert "concept__phone-cta-row" in card
        assert "concept__phone-cta" in card


def test_hero_monogram_uses_text_when_logo_is_wide_wordmark(tmp_path: Path):
    """A wide-wordmark logo_path on the audit must NOT end up embedded
    in the hero monogram circle. The renderer falls back to text
    initials so the small badge stays legible."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore
    from agents.outreach.reporting.microsite_builder import build_microsite

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "wide"
    prospect_root.mkdir(parents=True, exist_ok=True)
    _make_wide_wordmark_png(prospect_root / "assets" / "WIDE_logo.png")
    real_hero = _make_real_png(prospect_root / "assets" / "hero.png", width=128, height=128)

    store = ProspectStore("wide", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="wide",
            prospect_name="Wide Co",
            niche="skincare",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "logo_path": "assets/WIDE_logo.png",
                "hero_image_path": str(real_hero.relative_to(prospect_root)),
            },
        )
    )
    build_microsite("wide", prospects_root=prospects_root)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")

    import re
    mono = re.search(r'<div class="hero__monogram">(.*?)</div>', html, re.S)
    assert mono is not None
    # The wide wordmark must NOT be embedded as an <img> inside the badge.
    assert "<img" not in mono.group(1), (
        f"hero__monogram still embeds a wide-wordmark image: {mono.group(1)!r}"
    )
    # The fallback is the brand initials (W + the second word's first letter
    # or 'W' for single-word names).
    assert mono.group(1).strip()


def test_logo_image_never_used_as_full_concept_image(tmp_path: Path):
    """No concept card's main <img> may ever resolve to a logo-shaped
    file. This is the end-to-end pin for the original YANA bug
    (`YANA_Logo_black_400x.png` rendering full-bleed on a phone)."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore
    from agents.outreach.reporting.microsite_builder import build_microsite

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "y"
    prospect_root.mkdir(parents=True, exist_ok=True)
    p_real = _make_real_png(prospect_root / "assets" / "IMG_real.png")
    p_logo = _make_real_png(prospect_root / "assets" / "BRAND_Logo_400x.png", width=400, height=400)

    store = ProspectStore("y", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="y",
            prospect_name="Y Co",
            niche="skincare",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "product_images": [
                    str(p_real.relative_to(prospect_root)),
                    str(p_logo.relative_to(prospect_root)),
                ],
            },
        )
    )
    build_microsite("y", prospects_root=prospects_root)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")

    import re
    cards = re.findall(r'<article class="concept"[^>]*>.*?</article>', html, re.S)
    for card in cards:
        for img in re.findall(r'<img[^>]+src="([^"]+)"', card):
            assert "logo" not in img.lower(), (
                f"concept card embedded a logo-shaped file: {img!r}"
            )


def test_logo_image_never_used_as_hero_background(tmp_path: Path):
    """No hero__bg <img> may resolve to a logo-shaped file - they
    are explicitly rejected by `from_audit_data`."""
    from agents.outreach.prospect_store import ProspectAudit, ProspectStore
    from agents.outreach.reporting.microsite_builder import build_microsite

    prospects_root = tmp_path / "prospects"
    prospect_root = prospects_root / "z"
    prospect_root.mkdir(parents=True, exist_ok=True)
    _make_real_png(prospect_root / "assets" / "BRAND_logo_4k.png", width=300, height=300)

    store = ProspectStore("z", prospects_root=prospects_root)
    store.save_audit(
        ProspectAudit(
            prospect_id="z",
            prospect_name="Z Co",
            niche="skincare",
            competitor_ads=[],
            weaknesses=[],
            brand_profile={
                "hero_image_path": "assets/BRAND_logo_4k.png",
            },
        )
    )
    build_microsite("z", prospects_root=prospects_root)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")

    import re
    bg = re.search(r'<div class="hero__bg(?: [^"]+)?"[^>]*>(.*?)</div>', html, re.S)
    assert bg is not None
    # The hero embeds NO logo-named image. (The fallback collage renders
    # instead - we already cover that elsewhere.)
    for img in re.findall(r'<img[^>]+src="([^"]+)"', bg.group(1)):
        assert "logo" not in img.lower()


# --------------------------------------------------------------------------- #
# V5 - bright concept-fallback artboard + small hero chip
# --------------------------------------------------------------------------- #


def _full_yana_html(tmp_path: Path) -> str:
    """Helper: rebuild the live YANA microsite under a tmp prospects/
    root and return the rendered HTML.

    The test reads from the canonical audit (the real one in this
    repo) and uses the unmodified microsite_builder pipeline -
    nothing about this test is mocked out. It pins the V5 visual
    rules end-to-end against the real prospect data.

    The `prospects/` tree is gitignored (it holds real prospect
    scraping + audit data that must never reach GitHub). On developer
    machines where the YANA bundle exists locally these tests run as
    full integration checks; on a fresh checkout (including CI) the
    fixture is absent and the tests are skipped — they would
    otherwise raise FileNotFoundError on shutil.copytree.
    """
    import shutil

    import pytest

    from agents.outreach.reporting.microsite_builder import build_microsite

    src_root = Path("prospects") / "yana-active"
    if not src_root.is_dir():
        pytest.skip(
            "prospects/yana-active is not present in this checkout. "
            "The prospects/ tree is gitignored (real-prospect data), "
            "so these end-to-end microsite tests only run when the "
            "operator has the live YANA bundle on disk."
        )
    dst_root = tmp_path / "prospects" / "yana-active"
    dst_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_root, dst_root)
    # Wipe any existing site/ so we render fresh.
    site_dir = dst_root / "site"
    if site_dir.exists():
        shutil.rmtree(site_dir)
    build_microsite("yana-active", prospects_root=tmp_path / "prospects")
    return (dst_root / "site" / "index.html").read_text(encoding="utf-8")


def test_yana_concept_fallback_uses_cream_artboard_not_black(tmp_path: Path):
    """The concept fallback CSS must use the cream artboard floor and
    must NOT use `#0A0A0A` anywhere in the `.concept__phone--designed`
    background. This is the exact bug the user kept hitting: for
    YANA's near-black primary_color the previous gradient collapsed
    to a black phone screen."""
    html = _full_yana_html(tmp_path)
    import re
    css_rule = re.search(
        r'\.concept__phone--designed\s*\{([^}]+)\}', html, re.S
    )
    assert css_rule is not None
    body = css_rule.group(1)
    # Cream artboard floor present.
    assert "#FBF6EE" in body, "concept fallback bg must use cream artboard"
    # No near-black anywhere in this rule.
    assert "#0A0A0A" not in body, (
        "concept fallback bg still contains the dark hex floor "
        "(`#0A0A0A`) - collapses to a black phone screen for brands "
        "with dark primary_color (e.g. YANA #1A1A1A)"
    )


def test_yana_fallback_concept_cards_have_hook_text(tmp_path: Path):
    """Each YANA fallback concept card (Route C/D) must carry the
    actual concept hook text - 'The colour that changes the outfit'
    and 'The one thing I take everywhere' - so the card reads as a
    designed creative concept, not an empty placeholder."""
    html = _full_yana_html(tmp_path)
    # The hook strings come from the activewear concept pack.
    assert "The colour that changes the outfit" in html
    assert "The one thing I take everywhere" in html


def test_yana_fallback_concept_cards_have_cta_pill(tmp_path: Path):
    """Each YANA fallback card must include the CTA pill markup so
    the card has a clear next-step affordance."""
    html = _full_yana_html(tmp_path)
    import re
    cards = re.findall(r'<article class="concept"[^>]*>.*?</article>', html, re.S)
    fallback_cards = [c for c in cards if "concept__phone--designed" in c]
    assert fallback_cards, "expected at least one designed fallback card"
    for c in fallback_cards:
        assert "concept__phone-cta-row" in c
        assert "concept__phone-cta" in c


def test_yana_fallback_concept_cards_use_designed_class(tmp_path: Path):
    """Pin the cream-artboard class name on the fallback frame.
    A future regression that swaps the fallback for an empty mock
    must fail loudly.

    Rollback after V7: featured-route hoist gone. All 4 YANA concepts
    sit on the rail. Routes A+B have real product images (filled);
    Routes C+D fall back to the cream artboard."""
    html = _full_yana_html(tmp_path)
    import re
    cards = re.findall(r'<article class="concept"[^>]*>.*?</article>', html, re.S)
    assert len(cards) == 4
    img_cards = [c for c in cards if "<img " in c]
    fallback_cards = [c for c in cards if "concept__phone--designed" in c]
    assert len(img_cards) == 2
    assert len(fallback_cards) == 2
    # No card uses the legacy mock class.
    for c in cards:
        assert "concept__phone--mock" not in c


def test_yana_html_never_renders_concept_phone_monogram(tmp_path: Path):
    """End-to-end pin against the giant centred monogram. The class
    name `concept__phone-monogram` must NOT appear ANYWHERE in the
    YANA HTML (markup OR stylesheet)."""
    html = _full_yana_html(tmp_path)
    assert "concept__phone-monogram" not in html


def test_yana_hero_monogram_is_small_text_chip_not_giant_block(tmp_path: Path):
    """YANA's hero monogram must:
      * be a small chip (CSS: height 38px, NOT 76px disc)
      * carry text 'YA' as its only content
      * NOT embed the favicon as an image
    This is the exact 'large YA block' bug the user kept reporting."""
    html = _full_yana_html(tmp_path)
    import re
    # Markup check.
    mono = re.search(r'<div class="hero__monogram">(.*?)</div>', html, re.S)
    assert mono is not None
    inner = mono.group(1).strip()
    assert inner == "YA"
    assert "<img" not in mono.group(1)
    # CSS check - small chip, not giant brand-color disc.
    css_rule = re.search(r'\.hero__monogram\s*\{([^}]+)\}', html, re.S)
    assert css_rule is not None
    css_body = css_rule.group(1)
    assert "height: 38px" in css_body
    assert "width: 76px" not in css_body  # no longer a 76px disc
    # Background uses the cream surface, not the brand accent.
    assert "background: var(--accent)" not in css_body


def test_yana_html_no_logo_image_embedded_anywhere(tmp_path: Path):
    """V5 rule: the logo image is never embedded. The wordmark lives
    in the topbar as text ('YANA Active x Yuvo Studio'); the small
    monogram chip carries text initials. No <img src="...logo..."/>
    appears anywhere in the rendered deck."""
    html = _full_yana_html(tmp_path)
    import re
    img_srcs = re.findall(r'<img[^>]+src="([^"]+)"', html)
    for src in img_srcs:
        assert "logo" not in src.lower(), (
            f"logo image leaked into <img>: {src!r}"
        )


def test_yana_concept_fallback_carries_corner_chip_with_initials(tmp_path: Path):
    """The cream artboard fallback carries a small text corner chip
    with the brand initials (top-right). Content is text only, capped
    to 2 characters, never an image - this is the ONLY place the
    brand mark appears on the frame."""
    html = _full_yana_html(tmp_path)
    import re
    chips = re.findall(
        r'<span class="concept__phone-corner-mark"[^>]*>(.*?)</span>', html, re.S
    )
    assert chips, "missing corner brand chip on the fallback frame"
    for chip in chips:
        chip_text = chip.strip()
        assert "<img" not in chip
        assert chip_text == "YA"


def test_concept_fallback_text_uses_dark_ink_not_cream(tmp_path: Path):
    """Cream surface => dark text. The fallback's hook line must
    use the card-ink colour token (not the legacy white-on-black
    `#F5F0E8` text that came with the dark gradient)."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    import re
    hook_rule = re.search(
        r'\.concept__phone--designed \.concept__phone-hook\s*\{([^}]+)\}',
        html, re.S,
    )
    assert hook_rule is not None
    body = hook_rule.group(1)
    assert "color: var(--card-ink)" in body
    # No hard-coded cream-on-dark colour anywhere in the hook rule.
    assert "#F5F0E8" not in body


# --------------------------------------------------------------------------- #
# V6 - giant YA block on Next Step page, regressed; new interest fields + copy
# --------------------------------------------------------------------------- #


def test_yana_next_step_visual_is_not_giant_initials_block(tmp_path: Path):
    """Regression: the Next Step section's right-side visual used to
    be `next-step__visual--mock` - a full-aspect dark block with a
    96 px centred 'YA'. For brands with a dark primary_color (YANA
    #1A1A1A is the worst case) this collapsed to a giant black
    rectangle dominated by the brand initials.

    V6 fix: the visual is now a cream artboard composition (no
    giant initials anywhere). This test rebuilds the live YANA
    microsite and pins the new behaviour."""
    html = _full_yana_html(tmp_path)
    import re

    # The legacy 96px-tall centred initials font-size rule is gone.
    assert "font-size: 96px" not in html, (
        "legacy giant centred-initials CSS still present on the deck"
    )

    # The Next Step section must NOT carry the old `next-step__visual--mock`
    # combo on a markup element.
    next_step = re.search(
        r'<section[^>]*id="next-step"[^>]*>(.*?)</section>', html, re.S,
    )
    assert next_step is not None
    body = next_step.group(1)
    # The markup uses the new visual element with composition shapes,
    # not the legacy mock element.
    assert "next-step__visual-shape" in body
    assert "next-step__visual-label" in body
    # The right-side visual MUST NOT contain a 96px or 80px-class span
    # of initials. The corner mark is allowed (small chip, top-right).
    # Check that within `next-step__visual` there is no element styled
    # to render giant centred text - we verify by the absence of the
    # legacy class.
    assert 'class="next-step__visual next-step__visual--mock"' not in body


def test_yana_no_giant_initials_block_anywhere_in_html(tmp_path: Path):
    """Hard pin: no place in the YANA HTML may render brand initials
    at a giant size. We assert against the specific CSS rules that
    used to drive the giant blocks."""
    html = _full_yana_html(tmp_path)
    # Pin against the legacy CSS rule that produced the 96 px centred
    # initials on the Next Step page.
    assert "font-size: 96px" not in html
    # Pin against any concept centred monogram rule.
    assert "concept__phone-monogram" not in html
    # Pin against the legacy hero mock-frame monogram.
    assert "hero__mock-frame-monogram" not in html


def test_yana_hero_uses_real_image_in_bg(tmp_path: Path):
    """Hero background must reference the real YANA lifestyle photo."""
    html = _full_yana_html(tmp_path)
    import re
    hero = re.search(r'<section class="section hero"[^>]*>(.*?)</section>', html, re.S)
    assert hero is not None
    # Real image referenced in hero__bg.
    assert "assets/IMG_7835copy.jpg" in hero.group(1)


def test_yana_hero_monogram_is_small_text_only(tmp_path: Path):
    """Hero monogram is a small text chip - never a giant block,
    never an embedded image. Pin both the markup (text only) and
    the CSS (small height)."""
    html = _full_yana_html(tmp_path)
    import re
    mono = re.search(r'<div class="hero__monogram">(.*?)</div>', html, re.S)
    assert mono is not None
    inner = mono.group(1).strip()
    assert inner == "YA", f"hero monogram inner was {inner!r}"
    assert "<img" not in mono.group(1)
    css = re.search(r'\.hero__monogram\s*\{([^}]+)\}', html, re.S)
    assert css is not None
    assert "height: 38px" in css.group(1)


def test_yana_next_step_visual_uses_cream_floor_not_dark(tmp_path: Path):
    """The Next Step right-side visual must use the cream artboard,
    not `var(--accent)` as the full background (which collapses to
    near-black for dark brands)."""
    html = _full_yana_html(tmp_path)
    import re
    css_match = re.search(r'\.next-step__visual\s*\{([^}]+)\}', html, re.S)
    assert css_match is not None
    body = css_match.group(1)
    # Cream floor present.
    assert "#FBF6EE" in body
    # Plain `background: var(--accent)` (the legacy full-fill) is gone.
    assert "background: var(--accent)" not in body


# --------------------------------------------------------------------------- #
# V6 - interest form: new fields + premium copy
# --------------------------------------------------------------------------- #


def test_interest_form_includes_country_code_and_phone_fields(tmp_path: Path):
    """The interest form must collect country code AND phone number,
    with proper input semantics so mobile keyboards adapt."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Country code input.
    assert 'id="interest-country_code"' in html
    assert 'autocomplete="tel-country-code"' in html
    # Phone input.
    assert 'id="interest-phone"' in html
    assert 'type="tel"' in html
    assert 'autocomplete="tel-national"' in html


def test_interest_form_includes_preferred_contact_method_field(tmp_path: Path):
    """A 'preferred contact method' field is part of the form so the
    prospect can say email vs WhatsApp vs phone."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert 'id="interest-preferred_contact_method"' in html
    # The label calls out the example options inline.
    assert "Preferred contact method" in html
    assert "WhatsApp" in html


def test_interest_mailto_body_includes_country_code_phone_method(tmp_path: Path):
    """Mailto pre-filled body must carry blank labelled lines for each
    new field so the prospect can fill them in."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # URL-encoded labels in the mailto body.
    assert "Country%20code%3A" in html
    assert "Phone%20number%3A" in html
    assert "Preferred%20contact%20method%3A" in html


def test_interest_endpoint_form_carries_all_new_visible_fields(tmp_path: Path):
    """The POST form must include the new visible inputs."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(
        brief, form_endpoint="/api/interest"
    ).read_text(encoding="utf-8")
    for name in (
        "name", "email", "country_code", "phone",
        "preferred_contact_method", "availability", "message",
    ):
        assert f'name="{name}"' in html, f"endpoint form missing input name={name!r}"


def test_interest_endpoint_form_still_carries_hidden_prospect_identifiers(tmp_path: Path):
    """Hidden fields with brand/prospect identifiers survive the V6
    field expansion."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(
        brief,
        form_endpoint="/api/interest",
        prospect_id="acme-skincare",
        private_slug="acme-skincare-abc123",
        public_url="https://yuvo-pitches.pages.dev/p/acme-skincare-abc123/",
    ).read_text(encoding="utf-8")
    assert '<input type="hidden" name="brand_name" value="Acme Skincare">' in html
    assert '<input type="hidden" name="prospect_id" value="acme-skincare">' in html
    assert '<input type="hidden" name="private_slug" value="acme-skincare-abc123">' in html
    assert '<input type="hidden" name="public_url" value="https://yuvo-pitches.pages.dev/p/acme-skincare-abc123/">' in html


def test_interest_copy_uses_premium_tone_not_cheap_saas(tmp_path: Path):
    """V6 copy rewrite: drop the cheap SaaS contact-form tone.
    The forbidden strings include 'no spam', 'no calendar links'
    and 'zero auto-emails'."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    lower = html.lower()
    for forbidden in (
        "no spam",
        "zero spam",
        "zero auto-emails",
        "no calendar needed",
        "no calendar, no forms to chase",
        "zero calendar links",
    ):
        assert forbidden not in lower, f"old casual interest copy survived: {forbidden!r}"


def test_interest_copy_uses_premium_phrases(tmp_path: Path):
    """V7 premium copy direction: explicit phrases pinning the new
    tone so any future regression is loud."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "Want us to make the first route?" in html
    # V7 subtitle.
    # Apostrophes are HTML-escaped via html.escape().
    assert "we&#x27;ll send the next step personally" in html or "we'll send the next step personally" in html
    # V7 support copy mentions "first watermarked route".
    assert "the first watermarked route" in html
    # V7 premium CTA wording.
    assert "Email my route request" in html


def test_interest_endpoint_cta_says_send_my_route_request(tmp_path: Path):
    """In endpoint mode the submit button is 'Send my route request'
    (V7 premium replacement for the V6 'Send my details' placeholder).
    Pin against any future regression."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief, form_endpoint="/api/interest").read_text(encoding="utf-8")
    assert "Send my route request" in html


# --------------------------------------------------------------------------- #
# V7 - polish pass: no cheap copy, featured route, storyboard preview
# --------------------------------------------------------------------------- #


def test_no_calendar_links_and_no_automated_followups_sentence_removed(tmp_path: Path):
    """Hard pin: the exact V6 sentence is gone everywhere."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "No calendar links and no automated follow-ups" not in html
    assert "no automated follow-ups" not in html.lower()


def test_no_spam_phrase_absent(tmp_path: Path):
    """Hard pin: cheap 'no spam' framing is gone everywhere."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "no spam" not in html.lower()


# --- V7 rollback pins ------------------------------------------------------
# The 5 V7-only featured-route tests + the 2 V7-only preview-storyboard tests
# were removed after the rollback. The replacement test below guards against
# the featured-route section and storyboard treatment ever sneaking back in.


def test_v7_featured_route_and_storyboard_are_rolled_back(tmp_path: Path):
    """Rollback regression: the V7 featured-route hero AND the V7 3-beat
    preview storyboard treatment must not re-appear. The page reads as
    a private microsite, not as an agency strategy report - exactly the
    visual outcome the operator asked for in the V7 rollback brief."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    forbidden = (
        'id="featured-route"',
        'class="section featured-route"',
        "FEATURED ROUTE",
        "The first route we&#x27;d ship for",
        "This is the first route we&#x27;d test for",
        "OPENING &middot; 0:00 - 0:03",
        "PRODUCT PROOF &middot; 0:03 - 0:11",
        "CTA &middot; 0:11 - 0:15",
        "Other routes we would test next",
        "preview__storyboard",
        "preview__storyboard-beat",
    )
    for tok in forbidden:
        assert tok not in html, f"V7 artifact survived rollback: {tok!r}"


def test_interest_form_carries_country_code_and_phone_fields(tmp_path: Path):
    """V7 form retains country code + phone fields from V6."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert 'id="interest-country_code"' in html
    assert 'id="interest-phone"' in html
    assert 'id="interest-preferred_contact_method"' in html


def test_interest_helper_note_reads_premium(tmp_path: Path):
    """V7 helper note reads 'A private route request, reviewed
    manually by Yuvo Studio.' - pins the premium framing."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "A private route request, reviewed manually by Yuvo Studio" in html


def test_html_does_not_leak_debug_markers_after_v7_pass(tmp_path: Path):
    """V7 polish pass must keep the no-leak guarantees from earlier
    versions. AI-UGC / body_text= / media_type= / ad_archive_id= /
    {{product.brand}} must NOT appear anywhere in the rendered HTML."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    forbidden = (
        "AI-UGC", "ai-ugc",
        "body_text=", "body_text =",
        "media_type=", "media_type =",
        "ad_archive_id",
        "{{product.brand}}",
    )
    for marker in forbidden:
        assert marker not in html, f"leaked debug marker {marker!r}"


def test_how_it_works_title_is_not_no_shoot_day(tmp_path: Path):
    """V7 softened the how-it-works heading away from 'No shoot day. No
    retainer.' (one defensive opt-out after another) to 'From your
    inputs to a finished cut.' (process-positive)."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "From your inputs to a finished cut" in html
    # The old defensive headline is gone.
    assert "No shoot day. No retainer. Two-round revisions." not in html
