"""Tests for the AI vision ad-pattern classifier.

Covers:
  - schema normalisation (strict closed set)
  - graceful JSON-parse fallback
  - missing API key -> skipped status (never crashes)
  - cached AI classifications are reused unless force=True
  - --force-ai-classify overwrites cached results
  - merge policy: high/medium AI tags win, low-confidence falls back to regex
  - end-to-end: StrategyBrief honours AI-promoted tags and Section 05
    still requires >=2 unique ads with no duplicate ad_archive_id
  - debug markers / raw model JSON never leak to the rendered HTML

The classifier is mocked - no network calls in the test suite.
"""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any

from agents.outreach.prospect_store import ProspectAudit, ProspectStore
from agents.outreach.reporting.ad_pattern_classifier import (
    AI_ALLOWED_PATTERNS,
    AI_CLASSIFIER_VERSION,
    classify_competitor_ad_with_ai,
    classify_competitor_ads_batch,
    is_ai_classification_available,
    merge_ai_tags_with_regex,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data)
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _make_real_png(path: Path, *, width: int = 64, height: int = 64) -> Path:
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


class _FakeContentBlock:
    """Mimics anthropic SDK's response content block."""
    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class _FakeMessage:
    """Mimics anthropic SDK's `messages.create()` return value."""
    def __init__(self, text: str):
        self.content = [_FakeContentBlock(text)]


class _FakeClient:
    """Mimics anthropic.Anthropic(). Returns a queue of pre-canned
    JSON responses, one per `messages.create()` call.
    """
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        if not self._responses:
            return _FakeMessage('{"primary_pattern": null}')
        return _FakeMessage(self._responses.pop(0))


def _make_ad(
    *,
    ad_id: str = "ABC123",
    competitor: str = "UpCircle Beauty",
    body: str = "Vitamin C and rosehip in our morning routine.",
    cta: str = "Shop now",
    media: str = "VIDEO",
    screenshot: Path | None = None,
) -> dict:
    return {
        "ad_archive_id": ad_id,
        "ad_library_url": f"https://www.facebook.com/ads/library/?id={ad_id}",
        "competitor_name": competitor,
        "body_excerpt": body,
        "cta_text": cta,
        "media_type": media,
        "screenshot_path": str(screenshot) if screenshot else None,
        "pattern_tags": [],
    }


# --------------------------------------------------------------------------- #
# 1. Schema normalisation
# --------------------------------------------------------------------------- #


def test_classifier_returns_strict_schema(tmp_path: Path, monkeypatch):
    """A valid model response is normalised into the documented schema."""
    shot = _make_real_png(tmp_path / "shot.png")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    fake = _FakeClient([json.dumps({
        "primary_pattern": "ingredient_proof",
        "secondary_patterns": ["editorial_luxury"],
        "rejected_patterns": ["before_after_claim"],
        "confidence": "high",
        "evidence_notes": ["Ad copy names rosehip and vitamin C."],
        "visual_evidence": ["Product still life with ingredient labels."],
        "text_evidence": ["Vitamin C and rosehip"],
        "why_primary_pattern": "Body copy leads with two named actives.",
        "should_use_for_strategy": True,
        "caution": "Avoid clinical-percentage claims.",
    })])
    res = classify_competitor_ad_with_ai(
        _make_ad(body="Vitamin C and rosehip in our morning routine."),
        screenshot_path=shot,
        _client=fake,
    )
    assert res["ai_classification_status"] == "ok"
    assert res["ai_primary_pattern"] == "ingredient_proof"
    assert res["ai_pattern_tags"] == ["ingredient_proof", "editorial_luxury"]
    assert res["ai_confidence"] == "high"
    assert res["ai_should_use_for_strategy"] is True
    assert res["ai_evidence_notes"][0].startswith("Ad copy names")
    assert res["ai_classifier_version"] == AI_CLASSIFIER_VERSION
    assert res["ai_model"] is not None
    assert res["ai_caution"] is not None


def test_classifier_drops_out_of_set_primary(tmp_path: Path, monkeypatch):
    """When the model invents a tag outside `AI_ALLOWED_PATTERNS`, the
    primary is dropped to None and out-of-set secondaries are filtered."""
    shot = _make_real_png(tmp_path / "shot.png")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    fake = _FakeClient([json.dumps({
        "primary_pattern": "MADE_UP_TAG",
        "secondary_patterns": ["another_fake", "ingredient_proof"],
        "rejected_patterns": [],
        "confidence": "medium",
        "evidence_notes": [],
        "visual_evidence": [],
        "text_evidence": [],
        "why_primary_pattern": "fake reason",
        "should_use_for_strategy": True,
        "caution": None,
    })])
    res = classify_competitor_ad_with_ai(
        _make_ad(), screenshot_path=shot, _client=fake,
    )
    assert res["ai_primary_pattern"] is None
    assert "ingredient_proof" in res["ai_pattern_tags"]
    assert "MADE_UP_TAG" not in res["ai_pattern_tags"]
    assert "another_fake" not in res["ai_pattern_tags"]


def test_classifier_handles_invalid_confidence(tmp_path: Path, monkeypatch):
    """An unexpected confidence value is reported as a schema_error
    and never silently rounded - we'd rather log + fall back to regex."""
    shot = _make_real_png(tmp_path / "shot.png")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    fake = _FakeClient([json.dumps({
        "primary_pattern": "ingredient_proof",
        "confidence": "MAYBE",
        "secondary_patterns": [], "rejected_patterns": [],
        "evidence_notes": [], "visual_evidence": [], "text_evidence": [],
        "why_primary_pattern": "", "should_use_for_strategy": True, "caution": None,
    })])
    res = classify_competitor_ad_with_ai(
        _make_ad(), screenshot_path=shot, _client=fake,
    )
    assert res["ai_classification_status"] == "schema_error"
    assert res["ai_error"] and "confidence" in res["ai_error"].lower()


# --------------------------------------------------------------------------- #
# 2. JSON parse robustness
# --------------------------------------------------------------------------- #


def test_classifier_handles_invalid_json_gracefully(tmp_path: Path, monkeypatch):
    """A model that returns prose / unparseable text yields
    `json_parse_error`, not an uncaught exception."""
    shot = _make_real_png(tmp_path / "shot.png")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    fake = _FakeClient(["I cannot classify this ad, sorry."])
    res = classify_competitor_ad_with_ai(
        _make_ad(), screenshot_path=shot, _client=fake,
    )
    assert res["ai_classification_status"] == "json_parse_error"
    assert res["ai_pattern_tags"] == []


def test_classifier_extracts_json_from_prose(tmp_path: Path, monkeypatch):
    """Models sometimes wrap JSON in markdown fences or prefix prose -
    the parser must extract the first {...} block."""
    shot = _make_real_png(tmp_path / "shot.png")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    payload = {
        "primary_pattern": "review_social_proof",
        "secondary_patterns": [], "rejected_patterns": [],
        "confidence": "medium",
        "evidence_notes": [], "visual_evidence": [], "text_evidence": [],
        "why_primary_pattern": "review overlay visible",
        "should_use_for_strategy": True, "caution": None,
    }
    wrapped = (
        "Sure, here is my classification:\n```json\n"
        + json.dumps(payload)
        + "\n```\nLet me know if you'd like a deeper read."
    )
    fake = _FakeClient([wrapped])
    res = classify_competitor_ad_with_ai(
        _make_ad(), screenshot_path=shot, _client=fake,
    )
    assert res["ai_classification_status"] == "ok"
    assert res["ai_primary_pattern"] == "review_social_proof"


# --------------------------------------------------------------------------- #
# 3. Missing API key / no-vision-model -> skipped
# --------------------------------------------------------------------------- #


def test_classifier_skipped_when_api_key_missing(monkeypatch):
    """No ANTHROPIC_API_KEY -> returns a `skipped_no_key` result and
    NEVER raises. The caller can keep using regex tags."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = classify_competitor_ad_with_ai(
        _make_ad(), screenshot_path=None,
    )
    assert res["ai_classification_status"] == "skipped_no_key"
    assert res["ai_pattern_tags"] == []
    assert res["ai_primary_pattern"] is None


def test_is_ai_classification_available_reports_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ok, why = is_ai_classification_available()
    assert ok is False
    assert "ANTHROPIC_API_KEY" in (why or "")


def test_classifier_skipped_when_no_screenshot_and_no_body(monkeypatch):
    """A DCO ad with no body and no screenshot has nothing to classify;
    skipped is the correct behaviour."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    ad = {"ad_archive_id": "X", "body_excerpt": None, "screenshot_path": None}
    res = classify_competitor_ad_with_ai(ad, screenshot_path=None)
    assert res["ai_classification_status"] == "skipped_no_screenshot_no_body"


# --------------------------------------------------------------------------- #
# 4 + 5. Caching + --force-ai-classify
# --------------------------------------------------------------------------- #


def _seed_batch_payload(tmp_path: Path) -> tuple[Path, dict]:
    prospect_root = tmp_path / "prospects" / "demo"
    prospect_root.mkdir(parents=True, exist_ok=True)
    shot = _make_real_png(
        prospect_root / "assets" / "competitors" / "x" / "ad_1.png",
    )
    payload = {
        "competitors": {
            "Brand X": {
                "meta_ads_url": "https://example.com/ads",
                "sampled_ads": [
                    {
                        "ad_archive_id": "AD-1",
                        "ad_library_url": "https://example.com/ads/?id=AD-1",
                        "screenshot_path": str(shot.relative_to(prospect_root)),
                        "body_excerpt": "Buy our routine bundle.",
                        "cta_text": "Shop now",
                        "media_type": "IMAGE",
                        "pattern_tags": [],
                    },
                ],
            },
        },
        "generated_at": "2026-05-15T20:00:00+00:00",
    }
    return prospect_root, payload


def test_classify_batch_uses_cache(tmp_path: Path, monkeypatch):
    """A second run on a payload that already carries
    `ai_classification_status == 'ok'` must reuse the cached result,
    not re-call the model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    prospect_root, payload = _seed_batch_payload(tmp_path)
    good_json = json.dumps({
        "primary_pattern": "offer_bundle",
        "secondary_patterns": [], "rejected_patterns": [],
        "confidence": "high",
        "evidence_notes": [], "visual_evidence": [], "text_evidence": [],
        "why_primary_pattern": "bundle copy", "should_use_for_strategy": True,
        "caution": None,
    })
    fake = _FakeClient([good_json])
    first = classify_competitor_ads_batch(
        payload, prospect_root=prospect_root, _client=fake,
    )
    assert first["ads_classified"] == 1
    assert first["ads_cached"] == 0
    assert len(fake.calls) == 1

    # Second run: the ad is cached - the model must NOT be called again.
    fake2 = _FakeClient([])
    second = classify_competitor_ads_batch(
        payload, prospect_root=prospect_root, _client=fake2,
    )
    assert second["ads_classified"] == 0
    assert second["ads_cached"] == 1
    assert len(fake2.calls) == 0


def test_force_ai_classify_bypasses_cache(tmp_path: Path, monkeypatch):
    """With `force=True`, even a cached result is re-classified."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    prospect_root, payload = _seed_batch_payload(tmp_path)
    good_json = json.dumps({
        "primary_pattern": "offer_bundle",
        "secondary_patterns": [], "rejected_patterns": [],
        "confidence": "high",
        "evidence_notes": [], "visual_evidence": [], "text_evidence": [],
        "why_primary_pattern": "bundle copy", "should_use_for_strategy": True,
        "caution": None,
    })
    fake = _FakeClient([good_json])
    classify_competitor_ads_batch(
        payload, prospect_root=prospect_root, _client=fake,
    )
    # Force a re-run: a new model call must happen.
    fake2 = _FakeClient([good_json])
    second = classify_competitor_ads_batch(
        payload, prospect_root=prospect_root, _client=fake2, force=True,
    )
    assert second["ads_classified"] == 1
    assert second["ads_cached"] == 0
    assert len(fake2.calls) == 1


def test_classifier_version_bump_invalidates_cache(tmp_path: Path, monkeypatch):
    """A cached result whose `ai_classifier_version` doesn't match the
    current version must be re-classified - so a prompt/schema change
    doesn't silently leave stale tags in the JSON.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-fake-key")
    prospect_root, payload = _seed_batch_payload(tmp_path)
    ad = payload["competitors"]["Brand X"]["sampled_ads"][0]
    ad.update({
        "ai_classification_status": "ok",
        "ai_classifier_version": "0.0.0-old",
        "ai_primary_pattern": "ingredient_proof",
    })
    good_json = json.dumps({
        "primary_pattern": "offer_bundle",
        "secondary_patterns": [], "rejected_patterns": [],
        "confidence": "high",
        "evidence_notes": [], "visual_evidence": [], "text_evidence": [],
        "why_primary_pattern": "bundle copy", "should_use_for_strategy": True,
        "caution": None,
    })
    fake = _FakeClient([good_json])
    res = classify_competitor_ads_batch(
        payload, prospect_root=prospect_root, _client=fake,
    )
    assert res["ads_classified"] == 1
    assert res["ads_cached"] == 0


# --------------------------------------------------------------------------- #
# 6 + 7. Merge policy: high/medium AI wins, low falls back to regex
# --------------------------------------------------------------------------- #


def test_merge_high_confidence_ai_tags_win():
    ai_result = {
        "ai_classification_status": "ok",
        "ai_primary_pattern": "review_social_proof",
        "ai_pattern_tags": ["review_social_proof", "routine_simplification"],
        "ai_confidence": "high",
        "ai_should_use_for_strategy": True,
    }
    merged = merge_ai_tags_with_regex(["ingredient_proof"], ai_result)
    assert merged["tag_source"] == "ai"
    assert merged["pattern_tags"] == ["review_social_proof", "routine_simplification"]
    assert merged["raw_regex_tags"] == ["ingredient_proof"]


def test_merge_medium_confidence_ai_tags_win():
    ai_result = {
        "ai_classification_status": "ok",
        "ai_primary_pattern": "founder_expert_credibility",
        "ai_pattern_tags": ["founder_expert_credibility"],
        "ai_confidence": "medium",
        "ai_should_use_for_strategy": True,
    }
    merged = merge_ai_tags_with_regex([], ai_result)
    assert merged["tag_source"] == "ai"
    assert merged["pattern_tags"] == ["founder_expert_credibility"]


def test_merge_low_confidence_ai_falls_back_to_regex():
    ai_result = {
        "ai_classification_status": "ok",
        "ai_primary_pattern": "ingredient_proof",
        "ai_pattern_tags": ["ingredient_proof"],
        "ai_confidence": "low",
        "ai_should_use_for_strategy": False,
    }
    merged = merge_ai_tags_with_regex(["routine_simplification"], ai_result)
    assert merged["tag_source"] == "regex_fallback_low_confidence"
    assert merged["pattern_tags"] == ["routine_simplification"]
    assert merged["raw_regex_tags"] == ["routine_simplification"]


def test_merge_should_use_false_falls_back_even_if_high_confidence():
    """Model says high-confidence but flags `should_use_for_strategy=False`
    (e.g. the pattern is on the closed list but the ad is too weak to
    drive sprint planning). Regex still wins."""
    ai_result = {
        "ai_classification_status": "ok",
        "ai_primary_pattern": "ingredient_proof",
        "ai_pattern_tags": ["ingredient_proof"],
        "ai_confidence": "high",
        "ai_should_use_for_strategy": False,
    }
    merged = merge_ai_tags_with_regex(["editorial_luxury"], ai_result)
    assert merged["tag_source"] == "regex"
    assert merged["pattern_tags"] == ["editorial_luxury"]


def test_merge_skipped_status_falls_back_to_regex():
    ai_result = {"ai_classification_status": "skipped_no_key"}
    merged = merge_ai_tags_with_regex(["ingredient_proof"], ai_result)
    assert merged["tag_source"] == "regex"
    assert merged["pattern_tags"] == ["ingredient_proof"]


# --------------------------------------------------------------------------- #
# 8 + 9. StrategyBrief honours AI tags; Section 05 still dedups + ≥2 ads
# --------------------------------------------------------------------------- #


def _save_pai_audit(prospect_root: Path) -> Path:
    """Minimal Pai-shaped audit on disk. The prospect_id is the
    directory name of `prospect_root`."""
    prospect_id = prospect_root.name
    prospects_root = prospect_root.parent
    prospect_root.mkdir(parents=True, exist_ok=True)
    assets = prospect_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    favicon = _make_real_png(assets / "favicon.png", width=32, height=32)
    homepage = _make_real_png(assets / "home.png", width=160, height=120)
    store = ProspectStore(prospect_id, prospects_root=prospects_root)
    audit = ProspectAudit(
        prospect_id=prospect_id,
        prospect_name="Pai Skincare",
        niche="premium organic skincare",
        country="GB",
        locale="en-GB",
        competitor_ads=[],
        weaknesses=[{"description": "DCO placeholder library.", "confidence": "high"}],
        brand_profile={
            "website_url": "https://example.test",
            "product_category": "Skincare",
            "brand_tone": "calm",
            "audience_assumption": "sensitive skin",
            "primary_color": "#2C4A3E",
            "logo_path": str(favicon.relative_to(prospect_root)),
            "hero_image_path": str(homepage.relative_to(prospect_root)),
            "product_images": [],
        },
    )
    store.save_audit(audit)
    return prospects_root


def _write_ai_seeded_competitor_ads(prospect_root: Path) -> None:
    """Seed a competitor_ads.json where:
      - UpCircle Beauty has 2 ads with AI primary=offer_bundle, high confidence
      - Aurelia London has 1 ad with AI primary=offer_bundle, medium confidence
      - Evolve Organic Beauty has 1 ad with AI primary=ingredient_proof, low
        confidence and regex tags=[] -> falls back to regex (empty) -> 05B
    The expected outcome:
      - `offer_bundle` clears the ≥2-ads bar with 3 ads / 2 competitors
      - `ingredient_proof` ends up in 05B (no_evidence or lost_to_dedup)
    We use real catalog names because `_build_competitors_base` only
    iterates over its hardcoded skincare set; competitors outside the
    catalog never get their ads bound to a pattern.
    """
    strat = prospect_root / "strategy"
    strat.mkdir(parents=True, exist_ok=True)
    for slug in ("upcircle-beauty", "aurelia-london", "evolve-organic-beauty"):
        _make_real_png(
            prospect_root / "assets" / "competitors" / slug / "ad_1.png",
        )
    _make_real_png(
        prospect_root / "assets" / "competitors" / "upcircle-beauty" / "ad_2.png",
    )
    payload = {
        "competitors": {
            "UpCircle Beauty": {
                "meta_ads_url": "https://example.com/a",
                "sampled_ads": [
                    {
                        "ad_archive_id": "A-1",
                        "ad_library_url": "https://example.com/?id=A-1",
                        "screenshot_path": "assets/competitors/upcircle-beauty/ad_1.png",
                        "body_excerpt": "Try the starter kit.",
                        "cta_text": "Shop now",
                        "media_type": "IMAGE",
                        "pattern_tags": ["offer_bundle"],
                        "raw_regex_tags": ["offer_bundle"],
                        "ai_classification_status": "ok",
                        "ai_classifier_version": AI_CLASSIFIER_VERSION,
                        "ai_primary_pattern": "offer_bundle",
                        "ai_pattern_tags": ["offer_bundle"],
                        "ai_confidence": "high",
                        "ai_should_use_for_strategy": True,
                        "tag_source": "ai",
                        "tag_source_reason": "AI high preferred",
                    },
                    {
                        "ad_archive_id": "A-2",
                        "ad_library_url": "https://example.com/?id=A-2",
                        "screenshot_path": "assets/competitors/upcircle-beauty/ad_2.png",
                        "body_excerpt": "Bundle deal this weekend.",
                        "cta_text": "Shop now",
                        "media_type": "IMAGE",
                        "pattern_tags": ["offer_bundle"],
                        "raw_regex_tags": [],
                        "ai_classification_status": "ok",
                        "ai_classifier_version": AI_CLASSIFIER_VERSION,
                        "ai_primary_pattern": "offer_bundle",
                        "ai_pattern_tags": ["offer_bundle"],
                        "ai_confidence": "high",
                        "ai_should_use_for_strategy": True,
                        "tag_source": "ai",
                        "tag_source_reason": "AI high preferred",
                    },
                ],
            },
            "Aurelia London": {
                "meta_ads_url": "https://example.com/b",
                "sampled_ads": [
                    {
                        "ad_archive_id": "B-1",
                        "ad_library_url": "https://example.com/?id=B-1",
                        "screenshot_path": "assets/competitors/aurelia-london/ad_1.png",
                        "body_excerpt": "Trio gift bundle.",
                        "cta_text": "Shop now",
                        "media_type": "IMAGE",
                        "pattern_tags": ["offer_bundle"],
                        "raw_regex_tags": [],
                        "ai_classification_status": "ok",
                        "ai_classifier_version": AI_CLASSIFIER_VERSION,
                        "ai_primary_pattern": "offer_bundle",
                        "ai_pattern_tags": ["offer_bundle"],
                        "ai_confidence": "medium",
                        "ai_should_use_for_strategy": True,
                        "tag_source": "ai",
                        "tag_source_reason": "AI medium preferred",
                    },
                ],
            },
            "Evolve Organic Beauty": {
                "meta_ads_url": "https://example.com/c",
                "sampled_ads": [
                    {
                        "ad_archive_id": "C-1",
                        "ad_library_url": "https://example.com/?id=C-1",
                        "screenshot_path": "assets/competitors/evolve-organic-beauty/ad_1.png",
                        "body_excerpt": "Generic ad body.",
                        "cta_text": "Shop now",
                        "media_type": "IMAGE",
                        "pattern_tags": [],
                        "raw_regex_tags": [],
                        "ai_classification_status": "ok",
                        "ai_classifier_version": AI_CLASSIFIER_VERSION,
                        "ai_primary_pattern": "ingredient_proof",
                        "ai_pattern_tags": ["ingredient_proof"],
                        "ai_confidence": "low",
                        "ai_should_use_for_strategy": False,
                        "tag_source": "regex_fallback_low_confidence",
                        "tag_source_reason": "low confidence",
                    },
                ],
            },
        },
        "generated_at": "2026-05-15T20:30:00+00:00",
    }
    (strat / "competitor_ads.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def test_strategy_brief_prefers_high_medium_ai_tags(tmp_path: Path):
    """High and medium AI tags drive the pattern_tags surface that
    Section 05 consumes. Low-confidence AI tags do NOT - they fall
    back to whatever the regex saw (empty in this fixture)."""
    from agents.outreach.reporting.strategy_brief import StrategyBrief

    prospects_root = _save_pai_audit(tmp_path / "prospects" / "pai-like")
    _write_ai_seeded_competitor_ads(prospects_root / "pai-like")
    brief = StrategyBrief.from_audit("pai-like", prospects_root=prospects_root)

    # The high-confidence pattern `offer_bundle` must have its ads
    # bound to the pattern card.
    by_name = {p.name: p for p in brief.creative_patterns}
    bundle = by_name["Offer / bundle ads"]
    assert len(bundle.ad_evidence) == 3, (
        f"offer_bundle should carry 3 AI-promoted ads; got {len(bundle.ad_evidence)}"
    )
    # The low-confidence ingredient_proof tag must NOT promote into
    # the pattern card's ad_evidence.
    ingredient = by_name["Ingredient-proof close-up"]
    ad_ids = {a.ad_archive_id for a in ingredient.ad_evidence}
    assert "C-1" not in ad_ids


def test_strategy_brief_low_confidence_ai_falls_back_to_regex(tmp_path: Path):
    """A low-confidence AI call must NOT override an existing regex
    tag - the original regex decision wins."""
    from agents.outreach.reporting.strategy_brief import StrategyBrief
    prospects_root = _save_pai_audit(tmp_path / "prospects" / "pai-fb")
    prospect_root = prospects_root / "pai-fb"
    strat = prospect_root / "strategy"
    strat.mkdir(parents=True, exist_ok=True)
    _make_real_png(prospect_root / "assets" / "competitors" / "upcircle-beauty" / "ad_1.png")
    payload = {
        "competitors": {
            "UpCircle Beauty": {
                "sampled_ads": [{
                    "ad_archive_id": "X-1",
                    "ad_library_url": "https://example.com/?id=X-1",
                    "screenshot_path": "assets/competitors/upcircle-beauty/ad_1.png",
                    "body_excerpt": "Routine moment captured.",
                    "cta_text": "Shop now",
                    "media_type": "IMAGE",
                    # Regex called it routine_simplification; AI says
                    # editorial_luxury but at LOW confidence.
                    "pattern_tags": ["routine_simplification"],
                    "raw_regex_tags": ["routine_simplification"],
                    "ai_classification_status": "ok",
                    "ai_classifier_version": AI_CLASSIFIER_VERSION,
                    "ai_primary_pattern": "editorial_luxury",
                    "ai_pattern_tags": ["editorial_luxury"],
                    "ai_confidence": "low",
                    "ai_should_use_for_strategy": False,
                }],
            },
        },
        "generated_at": "2026-05-15T20:30:00+00:00",
    }
    (strat / "competitor_ads.json").write_text(json.dumps(payload), encoding="utf-8")
    brief = StrategyBrief.from_audit("pai-fb", prospects_root=prospects_root)
    by_name = {p.name: p for p in brief.creative_patterns}
    # The ad should bind to routine_simplification (regex), NOT
    # editorial_luxury (low-confidence AI).
    routine = by_name["Routine simplification"]
    editorial = by_name["Editorial / heritage tone"]
    assert any(a.ad_archive_id == "X-1" for a in routine.ad_evidence)
    assert not any(a.ad_archive_id == "X-1" for a in editorial.ad_evidence)


def test_section05_no_duplicate_ad_after_ai_promotion(tmp_path: Path):
    """Even with AI promotion across multiple patterns, Section 05
    must not render the same ad_archive_id under two pattern cards."""
    from agents.outreach.reporting.strategy_brief import StrategyBrief

    prospects_root = _save_pai_audit(tmp_path / "prospects" / "pai-dup")
    _write_ai_seeded_competitor_ads(prospects_root / "pai-dup")
    brief = StrategyBrief.from_audit("pai-dup", prospects_root=prospects_root)
    seen: dict[tuple[str, str], str] = {}
    for p in brief.validated_patterns:
        for a in p.ad_evidence:
            key = (a.competitor_name, a.ad_archive_id)
            assert key not in seen, (
                f"AI promotion produced duplicate ad {key} across "
                f"{seen[key]!r} and {p.name!r}"
            )
            seen[key] = p.name


def test_section05_still_requires_two_unique_ads_with_ai(tmp_path: Path):
    """A pattern whose only AI-tagged ad is a single one must still
    move to Section 05B - the ≥2-ads rule is independent of AI."""
    from agents.outreach.reporting.strategy_brief import StrategyBrief

    prospects_root = _save_pai_audit(tmp_path / "prospects" / "pai-single")
    prospect_root = prospects_root / "pai-single"
    strat = prospect_root / "strategy"
    strat.mkdir(parents=True, exist_ok=True)
    _make_real_png(prospect_root / "assets" / "competitors" / "upcircle-beauty" / "ad_1.png")
    payload = {
        "competitors": {
            "UpCircle Beauty": {
                "sampled_ads": [{
                    "ad_archive_id": "ONLY-1",
                    "ad_library_url": "https://example.com/?id=ONLY-1",
                    "screenshot_path": "assets/competitors/upcircle-beauty/ad_1.png",
                    "body_excerpt": "founder talks about her formulation.",
                    "cta_text": "Shop now",
                    "media_type": "VIDEO",
                    "pattern_tags": ["founder_expert_credibility"],
                    "raw_regex_tags": [],
                    "ai_classification_status": "ok",
                    "ai_classifier_version": AI_CLASSIFIER_VERSION,
                    "ai_primary_pattern": "founder_expert_credibility",
                    "ai_pattern_tags": ["founder_expert_credibility"],
                    "ai_confidence": "high",
                    "ai_should_use_for_strategy": True,
                }],
            },
        },
        "generated_at": "2026-05-15T20:30:00+00:00",
    }
    (strat / "competitor_ads.json").write_text(json.dumps(payload), encoding="utf-8")
    brief = StrategyBrief.from_audit("pai-single", prospects_root=prospects_root)
    validated_names = {p.name for p in brief.validated_patterns}
    assert "Founder / expert credibility" not in validated_names, (
        "A pattern with only 1 AI-tagged ad must still move to 05B"
    )
    unvalidated_names = {g.pattern.name for g in brief.unvalidated_patterns}
    assert "Founder / expert credibility" in unvalidated_names


# --------------------------------------------------------------------------- #
# 10. Rendered HTML must not leak AI debug data
# --------------------------------------------------------------------------- #


def test_ai_debug_fields_do_not_leak_into_rendered_html(tmp_path: Path):
    """Internal AI fields like `ai_raw_response`, `ai_error`,
    `ai_classification_status` are operator-audit only - they must
    never reach the rendered strategy page."""
    from agents.outreach.reporting.html_strategy_builder import build_strategy_html
    from agents.outreach.reporting.strategy_brief import StrategyBrief

    prospects_root = _save_pai_audit(tmp_path / "prospects" / "pai-render")
    _write_ai_seeded_competitor_ads(prospects_root / "pai-render")
    brief = StrategyBrief.from_audit("pai-render", prospects_root=prospects_root)
    out_dir = tmp_path / "out"
    build_strategy_html(brief, output_dir=out_dir, noindex=True)
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    for marker in (
        "ai_classification_status",
        "ai_raw_response",
        "ai_error",
        "ai_should_use_for_strategy",
        "ai_classifier_version",
        '"primary_pattern"',
        '"secondary_patterns"',
    ):
        assert marker not in html, (
            f"AI internal field {marker!r} leaked into the rendered HTML"
        )


# --------------------------------------------------------------------------- #
# 11. CLI / live entry point smoke
# --------------------------------------------------------------------------- #


def test_capture_script_exposes_ai_classify_flag():
    """The capture script's argparse must accept --ai-classify,
    --force-ai-classify, --no-ai-classify, --ai-model."""
    import importlib
    cap = importlib.import_module("scripts.capture_competitor_ads")
    # We can't reach the locally-built parser inside main() without
    # invoking it, so read the source and assert each flag is wired.
    import inspect
    src = inspect.getsource(cap.main)
    for flag in (
        '"--ai-classify"',
        '"--force-ai-classify"',
        '"--no-ai-classify"',
        '"--ai-model"',
    ):
        assert flag in src, f"capture script missing CLI flag {flag}"


def test_ai_module_exports_expected_public_api():
    """The classifier module must expose the public helpers the capture
    script + tests import. Guards against accidental rename."""
    from agents.outreach.reporting import ad_pattern_classifier as mod
    for name in (
        "AI_ALLOWED_PATTERNS",
        "AI_CLASSIFIER_VERSION",
        "DEFAULT_AI_MODEL",
        "classify_competitor_ad_with_ai",
        "classify_competitor_ads_batch",
        "is_ai_classification_available",
        "merge_ai_tags_with_regex",
    ):
        assert hasattr(mod, name), f"missing public export: {name}"


def test_allowed_patterns_match_strategy_taxonomy():
    """The classifier's closed pattern set must match the regex
    classifier's `_PATTERN_RULES` taxonomy - otherwise the merge step
    would emit AI tags that Section 05 cannot bind."""
    from scripts.capture_competitor_ads import _PATTERN_RULES
    regex_tags = {tag for tag, _rx, _why in _PATTERN_RULES}
    assert set(AI_ALLOWED_PATTERNS) == regex_tags, (
        f"AI taxonomy {set(AI_ALLOWED_PATTERNS)} drift from regex "
        f"taxonomy {regex_tags}"
    )


# --------------------------------------------------------------------------- #
# Misc - secret redaction
# --------------------------------------------------------------------------- #


def test_redact_secrets_strips_api_key_shapes():
    from agents.outreach.reporting.ad_pattern_classifier import _redact_secrets
    text = (
        "Error: AuthError: bad key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890 "
        "in the headers"
    )
    out = _redact_secrets(text)
    assert "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890" not in out
    assert "<redacted>" in out
