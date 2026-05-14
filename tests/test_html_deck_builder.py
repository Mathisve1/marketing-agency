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


def test_html_contains_from_live_ad_to_video_route(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "From live ad to video route" in html


def test_html_contains_concept_board(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "Concept board" in html


def test_html_contains_how_this_works(tmp_path: Path):
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    assert "How this works" in html


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
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    # Logo and hero referenced via `../assets/...` (relative path).
    assert "assets/logo.png" in html
    assert "assets/hero.png" in html
    assert "assets/product_a.png" in html
    assert "assets/product_b.png" in html


def test_html_does_not_use_any_asset_path_more_than_once(tmp_path: Path):
    """Strict 'each image once' rule across the whole deck."""
    brief = _make_synthetic_brief(tmp_path)
    html = build_html_deck(brief).read_text(encoding="utf-8")
    for asset in ("logo.png", "hero.png", "product_a.png", "product_b.png", "ad_1.png"):
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
    # Still has all eight slides.
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
        assert slide in html
    # CSS mock fallbacks for ad row + concept board.
    assert "ad-row__preview--mock" in html
    assert "concept__phone--mock" in html


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


def test_deck_brief_logo_used_only_on_cover(tmp_path: Path):
    """The logo path must be in the brief once and rendered once."""
    store = _seed_audit(tmp_path)
    brief = DeckBrief.from_audit("test-brand", prospects_root=tmp_path)
    assert brief.logo_path is not None
    out = build_html_deck(brief)
    html_text = out.read_text(encoding="utf-8")
    # Logo path appears exactly once in the rendered deck.
    assert html_text.count("logo.png") == 1
    # And it lives in the cover monogram (the first slide).
    cover_start = html_text.find('data-slide="1"')
    cover_end = html_text.find('data-slide="2"')
    assert "logo.png" in html_text[cover_start:cover_end]
    assert store.root.exists()  # sanity
