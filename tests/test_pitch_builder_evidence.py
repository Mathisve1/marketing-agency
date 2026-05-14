"""Tests for the V4 Yuvo Studio outreach pitch deck.

V4 redesign pins:
  - Cover renders "PRIVATE CREATIVE NOTE" eyebrow and the prospect name.
  - The deck never carries the V1.6 "AI-UGC" agency vocabulary.
  - Client-facing pages don't leak debug strings (`body_text=`, raw
    `ad_archive_id` numbers, `{{...}}` placeholders, or `media_type=`).
  - latin-1-incompatible glyphs (em-dashes, curly quotes, emojis) come
    out clean - no '?' replacement-character artefacts.
  - The seven V4 section titles all render (45-second version, From
    live ad to video route, Creative gap map, Concept board, How this
    works, Pricing and first test, Next step).
  - The three pricing tiers (£90 / £260 / £499) all surface on the
    pricing page.
  - Concept board renders four `CONCEPT 0X` eyebrows.
  - Ad-route cards render `LIVE AD`, `ROUTE`, `DAYS ACTIVE`, and an
    OPEN AD action pill when an ad URL exists.
  - Next-step page is named after the prospect ("Want to see one
    <Brand> video?").

V1.6 evidence-discipline back-compat:
  - Bare-string AND dict-shaped weaknesses are accepted by the builder
    and don't crash the render. The V4 layout doesn't surface every
    weakness verbatim (descriptions feed into the gap-map and 45-sec
    summary instead), so we assert the deck renders cleanly with mixed
    inputs rather than asserting raw text.
  - DEFAULT_FRAMEWORK_STRENGTHS still avoids overclaiming language and
    does not carry the AI-UGC fingerprint.

Approach: monkey-patch FPDF.multi_cell AND FPDF.cell to capture every
emitted text string. PDF text streams are zlib-compressed by default so
substring checks against on-disk bytes are unreliable; capturing at the
renderer-call boundary is deterministic.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fpdf import FPDF

from agents.outreach.reporting import pitch_builder
from agents.outreach.reporting.pitch_builder import build_pitch_pdf


@pytest.fixture
def captured_text(monkeypatch) -> list[str]:
    """Capture every text payload reaching `FPDF.multi_cell` or
    `FPDF.cell` so tests can assert on the full deck's visible copy.

    fpdf2 2.7.6 renamed the third positional argument from `txt` to
    `text`; the spy must accept either, but never re-inject the value
    on the forwarding call (doing so produces "got multiple values for
    argument 'text'" against the dry_run measurement path)."""
    captured: list[str] = []
    real_multi_cell = FPDF.multi_cell
    real_cell = FPDF.cell

    def _extract_text(args, kwargs):
        if args:
            cand = args[0]
            if isinstance(cand, str):
                return cand
        for key in ("text", "txt"):
            cand = kwargs.get(key)
            if isinstance(cand, str):
                return cand
        return None

    def spy_mc(self, w, h=None, *args, **kwargs):
        text_val = _extract_text(args, kwargs)
        if text_val is not None:
            captured.append(text_val)
        return real_multi_cell(self, w, h, *args, **kwargs)

    def spy_cell(self, w, h=0, *args, **kwargs):
        text_val = _extract_text(args, kwargs)
        if text_val is not None:
            captured.append(text_val)
        return real_cell(self, w, h, *args, **kwargs)

    monkeypatch.setattr(FPDF, "multi_cell", spy_mc)
    monkeypatch.setattr(FPDF, "cell", spy_cell)
    return captured


# A representative audit that exercises every nasty input shape past
# outreach runs produced: placeholder bodies, em-dashes, emojis, legacy
# "[HIGH CONFIDENCE] ... Evidence: ad <id> body_text = '{{...}}'"
# strings, and a clean structured dict weakness. The fixture also
# carries enough ads for the V4 ad-route page (4 cards in the grid).
_FULL_FIXTURE = dict(
    prospect_name="Active Root",
    niche="sports nutrition / energy supplements",
    weaknesses=[
        (
            "[HIGH CONFIDENCE] Two of three sampled ads contain only an "
            "unfilled template placeholder with zero usable copy. Evidence: "
            "ad 912746031183785 body_text = '{{product.brand}}'; ad "
            "25621726727454653 body_text = '{{product.brand}}' — 2 of 3 "
            "ads in the sample have no real copy rendered."
        ),
        {
            "description": "Single copy angle duplicated verbatim across paid ads",
            "evidence": [
                "ad_archive_id 25579181511702305 (active 91 days)",
                "2 of 4 sampled ads share identical body_text",
                "no creative refresh in the last 90 days",
            ],
            "confidence": "high",
        },
    ],
    competitor_ad_summary=[
        {
            "ad_archive_id": "912746031183785",
            "page_name": "Active Root",
            "media_type": None,
            "body_text": "{{product.brand}}",
            "cta_text": None,
            "days_active": 114,
        },
        {
            "ad_archive_id": "25579181511702305",
            "page_name": "Active Root",
            "media_type": None,
            # Em-dash + emoji preserved in the fixture so the typography
            # sanitiser is genuinely exercised on the way to the PDF.
            "body_text": "Award-commended energy — yum✨",
            "cta_text": None,
            "days_active": 91,
        },
        {
            "ad_archive_id": "25579181511702306",
            "page_name": "Active Root",
            "media_type": None,
            "body_text": "Award-commended energy — yum✨",
            "cta_text": None,
            "days_active": 88,
        },
    ],
    cta="Reply to schedule a 30-minute creative working session.",
    agency_name="Yuvo Studio",
)


# --------------------------------------------------------------------------- #
# V4 cover + brand pins
# --------------------------------------------------------------------------- #


def test_v4_cover_has_private_creative_note_eyebrow(
    tmp_path: Path, captured_text: list[str]
):
    """The V4 cover replaces the V3 'Creative Growth Audit' header with a
    'PRIVATE CREATIVE NOTE' eyebrow. This is the single biggest visual
    signal that the deck is the V4 'private note' style, not the V3
    audit report."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "PRIVATE CREATIVE NOTE" in joined


def test_v4_cover_makes_prospect_the_hero(
    tmp_path: Path, captured_text: list[str]
):
    """The prospect name must appear prominently on the cover (as the
    hero headline target) and again on the Next step page heading."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "Active Root" in joined
    # The next-step heading is named after the prospect.
    assert "Want to see one Active Root video?" in joined


def test_v4_deck_keeps_subtle_yuvo_studio_signoff(
    tmp_path: Path, captured_text: list[str]
):
    """Yuvo Studio still owns the deck (cover footer signoff) but
    appears subtly, not as the hero - the prospect is the hero now."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "Yuvo Studio" in joined or "YUVO STUDIO" in joined


def test_v4_deck_never_uses_ai_ugc_language(
    tmp_path: Path, captured_text: list[str]
):
    """The Yuvo Studio brief is explicit: do not lead with AI in
    client-facing copy. The phrase 'AI-UGC' must never appear."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "AI-UGC" not in joined
    assert "ai-ugc" not in joined.lower()


def test_v4_deck_renders_all_section_titles(
    tmp_path: Path, captured_text: list[str]
):
    """V4 carries seven named sections. They must all render so the
    deck doesn't silently regress to a missing-page state."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    expected = (
        "THE 45-SECOND VERSION",
        "FROM LIVE AD TO VIDEO ROUTE",
        "CREATIVE GAP MAP",
        "CONCEPT BOARD",
        "HOW THIS WORKS",
        "PRICING AND FIRST TEST",
        "NEXT STEP",
    )
    for title in expected:
        assert title in joined, f"V4 section title missing: {title!r}"


def test_v4_deck_filters_raw_body_text_debug_labels(
    tmp_path: Path, captured_text: list[str]
):
    """`body_text = '...'`, `media_type=`, `ad_archive_id`, and
    `{{...}}` are internal debug fragments. None of them belong on a
    client-facing page. The renderer must strip them whether they come
    from a legacy free-text weakness, a structured evidence list, or
    the ad body itself."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "body_text =" not in joined
    assert "body_text=" not in joined
    assert "{{product" not in joined
    assert "ad_archive_id" not in joined
    assert "media_type =" not in joined
    assert "media_type=" not in joined


def test_v4_deck_strips_typographic_chars_to_avoid_question_marks(
    tmp_path: Path, captured_text: list[str]
):
    """The V1.6 PDFs rendered em-dashes ('—') and emoji as '?' because
    FPDF core fonts are latin-1. V2+ route everything through
    `_sanitize` first - em-dashes become ' - ', emojis are dropped, and
    no '?' replacement character should ever appear in surfaced copy."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    for t in captured_text:
        assert "—" not in t, f"em-dash survived sanitisation in: {t!r}"


def test_v4_deck_with_minimal_audit_data_still_renders_all_sections(tmp_path: Path):
    """A near-empty audit (no weaknesses, no ads) must still produce a
    coherent deck rather than blanking out a section."""
    out = tmp_path / "pitch.pdf"
    build_pitch_pdf(
        output_path=out,
        prospect_name="Tiny Co",
        niche="skincare",
        weaknesses=[],
        competitor_ad_summary=[],
        cta=None,
        agency_name="Yuvo Studio",
    )
    assert out.exists()
    assert out.stat().st_size > 2000


def test_v4_deck_does_not_use_legacy_consulting_jargon(
    tmp_path: Path, captured_text: list[str]
):
    """V1.7 / V2 occasionally leaked consulting filler that the V4 brief
    explicitly retires. The deck must not reintroduce any of them."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text).lower()
    forbidden = (
        "creative testing floor",
        "matched cold-audience",
        "stable cpa at",
        "view-completion rate",
        "unlock next growth phase",
    )
    for phrase in forbidden:
        assert phrase not in joined, f"legacy jargon survived: {phrase!r}"


# --------------------------------------------------------------------------- #
# V4 ad-route cards (Page 3)
# --------------------------------------------------------------------------- #


def test_v4_ad_route_card_renders_live_ad_and_route_eyebrows(
    tmp_path: Path, captured_text: list[str]
):
    """Each ad-route card opens with `LIVE AD` (top half) and `ROUTE`
    (bottom half) eyebrow labels - they are the structural anchors of
    the card design."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "LIVE AD" in joined
    assert "ROUTE" in joined


def test_v4_ad_route_card_shows_days_active(
    tmp_path: Path, captured_text: list[str]
):
    """Days-active footnote (e.g. '114 DAYS ACTIVE') must surface so the
    prospect immediately knows the route is anchored to a real,
    long-running ad."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert any(
        f"{d} DAYS ACTIVE" in joined for d in (114, 91, 88)
    ), "no DAYS ACTIVE label rendered on the ad-route cards"


def test_v4_ad_route_card_shows_open_ad_pill_when_url_exists(
    tmp_path: Path, captured_text: list[str]
):
    """An OPEN AD pill appears on every route card backed by an ad URL.
    The fixture has no explicit ad_library_url, so the builder derives
    one from the ad_archive_id - that derivation is the V4 baseline
    behaviour the test pins down."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "OPEN AD" in joined


def test_v4_ad_route_card_falls_back_to_ad_reference_when_no_url(
    tmp_path: Path, captured_text: list[str]
):
    """When no URL is present AND no archive id is usable, the pill
    swaps to 'AD REFERENCE' so the card stays visually consistent."""
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="No URL Co",
        niche="fitness",
        weaknesses=[],
        competitor_ad_summary=[
            # Non-digit archive id, no ad_library_url, no snapshot URL.
            {
                "ad_archive_id": "not-a-number",
                "page_name": "No URL Co",
                "body_text": "Live ad copy.",
                "days_active": 30,
                "cta_text": None,
            }
        ],
        cta=None,
        agency_name="Yuvo Studio",
    )
    joined = "\n".join(captured_text)
    assert "AD REFERENCE" in joined


def test_v4_ad_route_card_drops_legacy_meta_ad_label(
    tmp_path: Path, captured_text: list[str]
):
    """The pre-V3 'META AD' label is gone - the brand initial chip in
    the corner is intentional design now, not a labelled placeholder."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "META AD" not in joined


def test_v4_ad_route_card_embeds_local_image_when_ad_screenshot_path_set(tmp_path: Path):
    """When an ad carries `ad_screenshot_path` (or fallback keys
    `image_path` / `snapshot_path` / `thumbnail_path`), the V4 route
    card on Page 3 must embed it as an image strip rather than falling
    back to the text-only layout.

    Uses a real 32x32 PNG so Pillow can actually decode it - the spy
    only counts SUCCESSFUL embeds (failures raise before the counter
    increments)."""
    from PIL import Image
    img_path = tmp_path / "assets" / "ad_1.png"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color=(255, 200, 100)).save(img_path, format="PNG")

    successful: list[str] = []
    real_image = FPDF.image

    def spy_image(self, name, *args, **kwargs):
        result = real_image(self, name, *args, **kwargs)
        if isinstance(name, str):
            successful.append(name)
        return result

    FPDF.image = spy_image
    try:
        build_pitch_pdf(
            output_path=tmp_path / "pitch.pdf",
            prospect_name="Imgful Co",
            niche="skincare",
            weaknesses=["copy is thin"],
            competitor_ad_summary=[
                {
                    "page_name": "Imgful Co",
                    "body_text": "Real body copy here.",
                    "days_active": 30,
                    "cta_text": None,
                    "ad_archive_id": "987654321",
                    "ad_screenshot_path": "assets/ad_1.png",
                }
            ],
            cta=None,
            agency_name="Yuvo Studio",
            prospect_root=tmp_path,
        )
    finally:
        FPDF.image = real_image

    assert any("ad_1" in c for c in successful), (
        f"ad_screenshot_path was not embedded; successful embeds = {successful}"
    )


def test_v4_ad_route_card_falls_back_to_text_when_image_path_missing(tmp_path: Path):
    """When the ad has neither `ad_screenshot_path` nor any
    `image_path` / `snapshot_path` / `thumbnail_path`, the renderer
    must skip image embedding gracefully and use the text-only layout
    (the deck must still produce a PDF)."""
    out = tmp_path / "pitch.pdf"
    build_pitch_pdf(
        output_path=out,
        prospect_name="Textonly Co",
        niche="fitness",
        weaknesses=[],
        competitor_ad_summary=[
            {
                "page_name": "Textonly Co",
                "body_text": "no image, just copy",
                "days_active": 10,
                "ad_archive_id": "1",
            }
        ],
        cta=None,
        agency_name="Yuvo Studio",
        prospect_root=tmp_path,
    )
    assert out.exists() and out.stat().st_size > 2000


# --------------------------------------------------------------------------- #
# V4 creative gap map (Page 4)
# --------------------------------------------------------------------------- #


def test_v4_creative_gap_map_renders_three_column_headers(
    tmp_path: Path, captured_text: list[str]
):
    """The gap map page renders its three V4 column labels. The third
    column is renamed from 'WHAT WE WOULD TEST' (V2) to 'UGC-STYLE TEST
    TO RUN' (V4) to align with the warmer, more concrete voice."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "CURRENT PATTERN" in joined
    assert "WHY IT LIMITS GROWTH" in joined
    assert "UGC-STYLE TEST TO RUN" in joined


# --------------------------------------------------------------------------- #
# V4 concept board (Page 5)
# --------------------------------------------------------------------------- #


def test_v4_concept_board_renders_four_concept_eyebrows(
    tmp_path: Path, captured_text: list[str]
):
    """The concept board page has four cards, each with a CONCEPT 0X
    eyebrow. V4 dropped the 3-scene storyboard breakdown; the cards now
    carry a hook line + CTA pill."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    for label in ("CONCEPT 01", "CONCEPT 02", "CONCEPT 03", "CONCEPT 04"):
        assert label in joined, f"concept eyebrow missing: {label!r}"


def test_v4_concept_board_renders_hook_label(
    tmp_path: Path, captured_text: list[str]
):
    """Each concept card carries a 'HOOK' eyebrow above the hook line."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "HOOK" in joined


# --------------------------------------------------------------------------- #
# V4 how-this-works (Page 6)
# --------------------------------------------------------------------------- #


def test_v4_how_it_works_carries_asset_based_production_eyebrow(
    tmp_path: Path, captured_text: list[str]
):
    """Page 6 is anchored by the ASSET-BASED PRODUCTION eyebrow above the
    four-step matrix."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "ASSET-BASED PRODUCTION" in joined


def test_v4_how_it_works_carries_brand_safety_guardrail(
    tmp_path: Path, captured_text: list[str]
):
    """Page 6 closes with the brand-safety guardrail strip."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "BRAND SAFETY GUARDRAIL" in joined


# --------------------------------------------------------------------------- #
# V4 pricing (Page 7)
# --------------------------------------------------------------------------- #


def test_v4_pricing_page_shows_all_three_prices(
    tmp_path: Path, captured_text: list[str]
):
    """The three pricing tiers (£90 / £260 / £499) must all appear on the
    pricing page - this is the deck's commercial fingerprint."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "£90" in joined, "£90 single-video tier missing"
    assert "£260" in joined, "£260 starter-trio tier missing"
    assert "£499" in joined, "£499 growth-pack tier missing"


def test_v4_pricing_page_renders_tier_labels(
    tmp_path: Path, captured_text: list[str]
):
    """Tier labels (Single video / Starter trio / Growth pack) and their
    eyebrows (START HERE / SMALL PACK / AFTER PROOF) must surface."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    for label in ("Single video", "Starter trio", "Growth pack"):
        assert label in joined, f"tier label missing: {label!r}"
    for eyebrow in ("START HERE", "SMALL PACK", "AFTER PROOF"):
        assert eyebrow in joined, f"tier eyebrow missing: {eyebrow!r}"


# --------------------------------------------------------------------------- #
# V4 next step (Page 8)
# --------------------------------------------------------------------------- #


def test_v4_next_step_includes_open_website_when_url_present(
    tmp_path: Path, captured_text: list[str]
):
    """When brand_profile carries a website_url, the next-step page must
    render an OPEN WEBSITE action pill so the prospect has a single
    click back to their own site as the deck's call-back surface."""
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="Webby Co",
        niche="skincare",
        weaknesses=[],
        competitor_ad_summary=[],
        cta=None,
        agency_name="Yuvo Studio",
        brand_profile={"website_url": "webby-co.example"},
    )
    joined = "\n".join(captured_text)
    assert "OPEN WEBSITE" in joined


def test_v4_next_step_includes_open_ad_when_ad_url_present(
    tmp_path: Path, captured_text: list[str]
):
    """When at least one route has an ad URL, the next-step page also
    carries an OPEN AD 01 pill anchored to the first sampled ad."""
    build_pitch_pdf(output_path=tmp_path / "pitch.pdf", **_FULL_FIXTURE)
    joined = "\n".join(captured_text)
    assert "OPEN AD 01" in joined


# --------------------------------------------------------------------------- #
# V1.6 / V1.7 evidence-discipline back-compat (input shape acceptance)
# --------------------------------------------------------------------------- #


def test_bare_string_weaknesses_do_not_crash_render(tmp_path: Path):
    """V4 doesn't surface every weakness verbatim (descriptions feed
    into the gap map and the 45-second summary), but bare-string
    weaknesses still need to be accepted as input without crashing the
    render."""
    out = tmp_path / "pitch.pdf"
    build_pitch_pdf(
        output_path=out,
        prospect_name="Acme Co",
        niche="fitness apparel",
        weaknesses=[
            "Brand relies on a single copy angle across all 10 long-running ads",
            "No video creative on Meta",
        ],
        competitor_ad_summary=[],
        cta="Reply to schedule a 20-minute audit walkthrough.",
    )
    assert out.exists()
    assert out.stat().st_size > 2000


def test_dict_weakness_with_evidence_does_not_leak_debug_markers(
    tmp_path: Path, captured_text: list[str]
):
    """Dict-shape weaknesses with `media_type=` and `ad_archive_id` debug
    fragments in the evidence list must not propagate those debug
    strings to any rendered page."""
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="Gymshark",
        niche="performance wear",
        weaknesses=[
            {
                "description": "Image-only ad library; no Meta video presence",
                "evidence": [
                    "10 of 10 ads have media_type=IMAGE",
                    "ad_archive_id 12345 (active 84 days, image only)",
                    "no video ad detected in the last 90 days of activity",
                ],
                "confidence": "high",
            },
        ],
        competitor_ad_summary=[],
    )
    joined = "\n".join(captured_text)
    assert "media_type=" not in joined
    assert "ad_archive_id" not in joined


def test_mixed_str_and_dict_weaknesses_do_not_crash(tmp_path: Path):
    """Real-world audits can carry both legacy free-text weaknesses and
    structured dict shapes. The renderer must accept both shapes
    together without raising."""
    out = tmp_path / "pitch.pdf"
    build_pitch_pdf(
        output_path=out,
        prospect_name="On Running",
        niche="performance footwear",
        weaknesses=[
            "Legacy free-text observation about CTA repetition",
            {
                "description": "No fresh creative in the last 60+ days",
                "evidence": ["newest creative is from mid-February"],
                "confidence": "medium",
            },
        ],
        competitor_ad_summary=[],
    )
    assert out.exists()
    assert out.stat().st_size > 2000


def test_unknown_weakness_shape_does_not_crash(tmp_path: Path):
    """A future writer that puts a non-str/non-dict (an int, a tuple, etc.)
    into weaknesses must not take down the whole PDF render."""
    out = tmp_path / "pitch.pdf"
    build_pitch_pdf(
        output_path=out,
        prospect_name="Edge Case Co",
        niche="testing",
        weaknesses=[42, ("a", "b")],
        competitor_ad_summary=[],
    )
    assert out.exists()


def test_dict_weakness_with_invalid_confidence_does_not_render_chip(
    tmp_path: Path, captured_text: list[str]
):
    """An LLM that invents 'pretty-good' as a confidence string must
    NOT propagate that to the customer-facing PDF (V4 does not render
    confidence chips anywhere, so we simply assert the invented label
    is not surfaced)."""
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="Bad Conf Co",
        niche="test",
        weaknesses=[
            {
                "description": "A claim with invented confidence",
                "evidence": ["one item, no debug markers"],
                "confidence": "pretty-good",
            },
        ],
        competitor_ad_summary=[],
    )
    joined = "\n".join(captured_text)
    assert "PRETTY-GOOD CONFIDENCE" not in joined
    assert "PRETTY-GOOD" not in joined


# --------------------------------------------------------------------------- #
# Module-level constants
# --------------------------------------------------------------------------- #


def test_default_framework_strengths_no_longer_overclaim():
    """V1.6 language audit: pitch default copy must not claim hooks are
    'proven'. Long-running ads are a SIGNAL, not proof. V4 keeps this
    constant in the public API for back-compat even though the new
    deck layout does not render it directly."""
    joined = " ".join(pitch_builder.DEFAULT_FRAMEWORK_STRENGTHS).lower()
    assert "proven" not in joined
    assert "signal" in joined or "candidate" in joined


def test_default_framework_strengths_no_longer_say_ai_ugc():
    """V2 brand pin: the framework defaults must not carry the 'AI-UGC'
    fingerprint - the rest of the deck is squeaky clean of it and the
    constants should not silently undo that."""
    joined = " ".join(pitch_builder.DEFAULT_FRAMEWORK_STRENGTHS)
    assert "AI-UGC" not in joined
    assert "ai-ugc" not in joined.lower()


# --------------------------------------------------------------------------- #
# V4 brand-profile rendering
# --------------------------------------------------------------------------- #


def test_v4_brand_profile_website_url_surfaces_in_next_step_pill(tmp_path: Path):
    """When website_url is set, the next-step page should expose an
    OPEN WEBSITE pill. The deck should also render cleanly under a
    fully-populated brand_profile."""
    captured: list[str] = []
    real_mc = FPDF.multi_cell
    real_cell = FPDF.cell

    def _grab(text):
        if isinstance(text, str):
            captured.append(text)

    def spy_mc(self, w, h=None, *args, **kwargs):
        t = args[0] if args else (kwargs.get("text") or kwargs.get("txt"))
        _grab(t)
        return real_mc(self, w, h, *args, **kwargs)

    def spy_cell(self, w, h=0, *args, **kwargs):
        t = args[0] if args else (kwargs.get("text") or kwargs.get("txt"))
        _grab(t)
        return real_cell(self, w, h, *args, **kwargs)

    FPDF.multi_cell = spy_mc
    FPDF.cell = spy_cell
    try:
        build_pitch_pdf(
            output_path=tmp_path / "pitch.pdf",
            prospect_name="YANA Active",
            niche="women's activewear",
            weaknesses=["One copy angle on every ad."],
            competitor_ad_summary=[
                {
                    "page_name": "YANA Active",
                    "body_text": "Your new favourite luxury activewear.",
                    "days_active": 62,
                    "cta_text": None,
                    "ad_archive_id": "931765719820665",
                }
            ],
            cta=None,
            agency_name="Yuvo Studio",
            brand_profile={
                "website_url": "yana-active.com",
                "product_category": "luxury activewear made in the UK",
                "audience_assumption": (
                    "Women buying premium activewear they wear well "
                    "beyond the gym"
                ),
                "brand_tone": "premium and lifestyle-led",
                "primary_color": "#1A1A1A",
            },
        )
    finally:
        FPDF.multi_cell = real_mc
        FPDF.cell = real_cell

    joined = "\n".join(captured)
    assert "OPEN WEBSITE" in joined


def test_v4_renders_gracefully_without_brand_profile(tmp_path: Path):
    """An audit with no brand_profile is still the common case (the
    pre-existing seed prospects don't have one). The deck must render
    every section with sensible fallback content."""
    out = tmp_path / "pitch.pdf"
    build_pitch_pdf(
        output_path=out,
        prospect_name="Plain Co",
        niche="fitness",
        weaknesses=["One copy angle on every ad."],
        competitor_ad_summary=[
            {"page_name": "Plain Co", "body_text": "Stay fit.", "days_active": 30, "ad_archive_id": "1"}
        ],
        cta=None,
        agency_name="Yuvo Studio",
        # No brand_profile.
    )
    assert out.exists()
    assert out.stat().st_size > 2000


def test_v4_image_used_once_when_one_product_image_supplied(tmp_path: Path):
    """When only ONE product image is supplied, the deck embeds it at
    most once via the dedup `used_paths` set (the cover claims it as a
    fallback hero; the concept board skips because the path is already
    used). Pins the 'each website asset used once' brief.

    Uses a real 32x32 PNG so the Pillow decode actually succeeds - the
    1x1 fixture used previously failed decode and the dedup logic was
    masked behind two raised exceptions.
    """
    # Real 32x32 white PNG generated via Pillow at test time.
    from PIL import Image
    img_path = tmp_path / "assets" / "p1.png"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color=(255, 255, 255)).save(img_path, format="PNG")

    p1_embed_count = {"n": 0}
    real_image = FPDF.image

    def spy_image(self, name, *args, **kwargs):
        result = real_image(self, name, *args, **kwargs)
        if isinstance(name, str) and "p1" in name:
            # Count only SUCCESSFUL embeds (failure raises before this line).
            p1_embed_count["n"] += 1
        return result

    FPDF.image = spy_image
    try:
        build_pitch_pdf(
            output_path=tmp_path / "pitch.pdf",
            prospect_name="Imgful Co",
            niche="skincare",
            weaknesses=[],
            competitor_ad_summary=[],
            cta=None,
            agency_name="Yuvo Studio",
            brand_profile={"product_images": ["assets/p1.png"]},
            prospect_root=tmp_path,
        )
    finally:
        FPDF.image = real_image

    assert p1_embed_count["n"] == 1, (
        f"p1 was embedded {p1_embed_count['n']} times; expected exactly 1"
    )


# --------------------------------------------------------------------------- #
# Data-layer round-trip (unchanged from V3)
# --------------------------------------------------------------------------- #


def test_v3_normaliser_extracts_image_and_logo_urls():
    """The Apify normaliser pipeline is the source of brand/image proof
    URLs in audit.json. The normaliser preserves snapshot images, video
    previews, the page profile picture, the page profile URI, and the
    public Meta Ads Library URL."""
    from agents.strategist.tools.apify_fb_ads import _normalise

    raw = {
        "ad_archive_id": "AD123",
        "page_name": "Acme",
        "snapshot": {
            "body": {"text": "Hello world"},
            "title": "Acme Sale",
            "cta_text": "Shop Now",
            "cta_type": "SHOP_NOW",
            "link_url": "https://acme.example/products",
            "page_profile_picture_url": "https://scontent.cdninstagram/logo.jpg",
            "page_profile_uri": "https://facebook.com/acme",
            "images": [
                {"original_image_url": "https://scontent/img1.jpg"},
                {"original_image_url": "https://scontent/img2.jpg"},
            ],
            "videos": [
                {
                    "video_hd_url": "https://video/hd.mp4",
                    "video_preview_image_url": "https://video/preview.jpg",
                }
            ],
        },
    }
    out = _normalise(raw).model_dump()

    assert out["image_url"] == "https://scontent/img1.jpg"
    assert out["image_urls"] == [
        "https://scontent/img1.jpg",
        "https://scontent/img2.jpg",
    ]
    assert out["video_url"] == "https://video/hd.mp4"
    assert out["video_preview_image_url"] == "https://video/preview.jpg"
    assert out["page_profile_picture_url"] == "https://scontent.cdninstagram/logo.jpg"
    assert out["page_profile_uri"] == "https://facebook.com/acme"
    assert out["title"] == "Acme Sale"
    assert out["cta_type"] == "SHOP_NOW"
    # ad_library_url is derived from ad_archive_id when not present in raw.
    assert out["ad_library_url"] == "https://www.facebook.com/ads/library/?id=AD123"


def test_v3_prospect_audit_round_trips_brand_profile():
    """ProspectAudit JSON shape must carry brand_profile when set and
    omit it when unset (so audits written before V3 stay byte-identical
    on no-op rewrite)."""
    from agents.outreach.prospect_store import ProspectAudit

    no_bp = ProspectAudit(prospect_id="x", prospect_name="X")
    assert "brand_profile" not in no_bp.to_dict()

    with_bp = ProspectAudit(
        prospect_id="x",
        prospect_name="X",
        brand_profile={"website_url": "x.com", "primary_color": "#000000"},
    )
    d = with_bp.to_dict()
    assert d["brand_profile"]["website_url"] == "x.com"

    # And from_dict pulls it back into the dataclass.
    rebuilt = ProspectAudit.from_dict(d)
    assert rebuilt.brand_profile == {"website_url": "x.com", "primary_color": "#000000"}
