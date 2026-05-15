"""Tests for the V4 Outreach asset-capture pipeline.

Covers four pieces that the V4 pitch deck depends on:

  1. Apify run_input now sets `scrapeAdDetails=True` so the actor
     returns the per-ad snapshot fields (images, videos, page profile,
     CTA details) the normaliser needs.

  2. `_normalise` correctly preserves every URL/image/video field when
     present AND infers media_type from snapshot contents when the
     actor leaves display_format empty.

  3. `ad_screenshots.capture_ad_screenshots`:
        a. Downloads inline `image_url` directly (path-A).
        b. Skips gracefully when Playwright is not installed (path-B
           no-op) - reports `playwright_available=False`.
        c. Never mutates the input list.

  4. `brand_assets.discover_website_url`:
        a. Returns a normalised homepage URL when a candidate resolves
           with a valid brand-name match.
        b. Returns None when every candidate fails or no candidate
           confirms the brand name.

Tests stub out `requests` so they run offline and deterministically.
"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image  # noqa: I001 (third-party import, kept after stdlib)

# --------------------------------------------------------------------------- #
# Apify actor input + normaliser
# --------------------------------------------------------------------------- #


def test_apify_run_input_enables_scrape_ad_details():
    """The actor's input schema gates the per-ad snapshot behind
    `scrapeAdDetails`. We must always pass `True` so the dataset
    carries images/videos/page-profile URLs."""
    # Build a minimal fake ApifyClient that records the run_input.
    captured: dict[str, Any] = {}

    class FakeDataset:
        def iterate_items(self):
            return iter([])

    class FakeActor:
        def call(self, run_input, timeout_secs):
            captured["run_input"] = run_input
            return {"defaultDatasetId": "DS_FAKE"}

    class FakeClient:
        def __init__(self, token):
            self.token = token

        def actor(self, _id):
            return FakeActor()

        def dataset(self, _id):
            return FakeDataset()

    # Stub the cost ledger so it doesn't try to write to a real DB.
    from agents.strategist.tools import apify_fb_ads as mod

    with patch.dict(os.environ, {"APIFY_API_TOKEN": "fake"}, clear=False):
        with patch.object(mod, "ApifyClient", FakeClient):
            with patch.object(mod.cost_ledger, "record_event", lambda **_kw: None):
                tool = mod.make_fb_ads_search_tool()
                _result = tool.invoke({
                    "competitor_pages": ["Brand X"],
                    "country": "GB",
                    "max_ads_per_page": 10,
                    "active_only": True,
                })

    ri = captured["run_input"]
    assert ri["scrapeAdDetails"] is True, "scrapeAdDetails must be True"
    assert ri["scrapePageAds.countryCode"] == "GB"
    assert ri["scrapePageAds.activeStatus"] == "active"
    assert ri["count"] == 10


def test_normaliser_infers_media_type_video_when_snapshot_has_video():
    """When the actor returns videos but no top-level display_format,
    the normaliser must infer media_type=VIDEO from snapshot contents.
    Without this inference the deck's 'no video presence' gap row
    fires incorrectly on every audit."""
    from agents.strategist.tools.apify_fb_ads import _normalise

    raw = {
        "ad_archive_id": "AD1",
        "page_name": "Brand X",
        "snapshot": {
            "videos": [{"video_hd_url": "https://video/hd.mp4"}],
            "images": [],
        },
    }
    out = _normalise(raw).model_dump()
    assert out["media_type"] == "VIDEO"


def test_normaliser_infers_media_type_image_when_snapshot_has_image_only():
    """Inverse: snapshot.images present, snapshot.videos empty ->
    media_type must be 'IMAGE'."""
    from agents.strategist.tools.apify_fb_ads import _normalise

    raw = {
        "ad_archive_id": "AD2",
        "page_name": "Brand Y",
        "snapshot": {
            "images": [{"original_image_url": "https://img/1.jpg"}],
            "videos": [],
        },
    }
    out = _normalise(raw).model_dump()
    assert out["media_type"] == "IMAGE"


def test_normaliser_prefers_explicit_display_format_over_inference():
    """When the actor DOES return display_format, the normaliser must
    surface it as-is and not silently downgrade it via inference."""
    from agents.strategist.tools.apify_fb_ads import _normalise

    raw = {
        "ad_archive_id": "AD3",
        "page_name": "Brand Z",
        "display_format": "CAROUSEL",
        "snapshot": {
            "images": [{"original_image_url": "https://img/1.jpg"}],
        },
    }
    out = _normalise(raw).model_dump()
    assert out["media_type"] == "CAROUSEL"


# --------------------------------------------------------------------------- #
# ad_screenshots module
# --------------------------------------------------------------------------- #


class _FakeResp:
    """Minimal `requests.Response`-like double for streaming downloads."""

    def __init__(self, status_code: int, body: bytes,
                 headers: dict[str, str] | None = None,
                 url: str = ""):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        yield self._body


def _png_bytes(color=(255, 255, 255), size: int = 256) -> bytes:
    """Generate a real PNG byte string Pillow can decode.
    Uses a 256x256 image so the produced bytes comfortably exceed the
    `_download_image_to` minimum-size guard (which rejects payloads
    under 200 bytes as likely tracking pixels or redirect HTML)."""
    buf = BytesIO()
    Image.new("RGB", (size, size), color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_capture_ad_screenshots_downloads_inline_image_url(tmp_path: Path):
    """Path A: when an ad carries a static `image_url`, the capture
    function downloads it directly to prospects/<id>/assets/ and adds
    the local relative path as `ad_screenshot_path`."""
    from agents.outreach import ad_screenshots

    ads = [
        {
            "ad_archive_id": "111",
            "page_name": "BrandX",
            "image_url": "https://cdn.example/img.png",
        }
    ]

    def fake_get(url, **kwargs):
        assert url == "https://cdn.example/img.png"
        return _FakeResp(200, _png_bytes(), headers={"Content-Type": "image/png"})

    with patch.object(ad_screenshots.requests, "get", fake_get):
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect", ads, prospects_root=tmp_path
        )

    assert result["captured"] == 1
    out_ad = result["ads"][0]
    assert "ad_screenshot_path" in out_ad
    assert out_ad["ad_screenshot_path"].startswith("assets/")
    # Real file written under prospects/<id>/assets/
    written = tmp_path / "test-prospect" / out_ad["ad_screenshot_path"]
    assert written.exists() and written.stat().st_size > 0


def test_capture_ad_screenshots_skips_gracefully_when_playwright_missing(tmp_path: Path):
    """Path B: no inline image_url AND playwright not importable.

    The function must NOT crash; it returns playwright_available=False
    and records an error per skipped ad. The input list is not
    mutated."""
    from agents.outreach import ad_screenshots

    ads = [
        {
            "ad_archive_id": "222",
            "page_name": "BrandY",
            "ad_library_url": "https://www.facebook.com/ads/library/?id=222",
        }
    ]
    snapshot_before = list(ads)

    def force_missing_playwright():
        return False

    with patch.object(ad_screenshots, "_playwright_available", force_missing_playwright):
        # Also block the in-function import by removing the attribute so
        # the inner try/except catches.
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect-2", ads, prospects_root=tmp_path
        )

    assert result["captured"] == 0
    assert result["playwright_available"] is False
    assert any("ad[0]" in e for e in result["errors"])
    # Input not mutated.
    assert ads == snapshot_before


def test_capture_ad_screenshots_does_not_mutate_input_when_capturing(tmp_path: Path):
    """Even on path-A capture the orchestrator returns a shallow copy
    list. The original ad dicts must stay unchanged so the operator's
    audit.json stays the source of truth until they explicitly save it."""
    from agents.outreach import ad_screenshots

    ads = [{"ad_archive_id": "333", "image_url": "https://cdn.example/x.png"}]
    snapshot_before = list(ads)

    def fake_get(url, **kwargs):
        return _FakeResp(200, _png_bytes(), headers={"Content-Type": "image/png"})

    with patch.object(ad_screenshots.requests, "get", fake_get):
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect-3", ads, prospects_root=tmp_path
        )

    assert "ad_screenshot_path" in result["ads"][0]
    assert "ad_screenshot_path" not in ads[0]
    assert ads == snapshot_before


def test_capture_ad_screenshots_falls_back_to_video_preview_when_no_image(tmp_path: Path):
    """When `image_url` is missing but `video_preview_image_url` is
    present, the orchestrator downloads the preview thumbnail instead."""
    from agents.outreach import ad_screenshots

    ads = [
        {
            "ad_archive_id": "444",
            "video_preview_image_url": "https://video/preview.jpg",
        }
    ]

    def fake_get(url, **kwargs):
        assert "preview" in url
        return _FakeResp(200, _png_bytes(), headers={"Content-Type": "image/jpeg"})

    with patch.object(ad_screenshots.requests, "get", fake_get):
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect-4", ads, prospects_root=tmp_path
        )

    assert result["captured"] == 1
    assert "ad_screenshot_path" in result["ads"][0]


# --------------------------------------------------------------------------- #
# brand_assets URL discovery
# --------------------------------------------------------------------------- #


def test_discover_website_url_returns_first_matching_candidate():
    """When a candidate URL returns HTML containing the brand name,
    the discoverer returns the normalised homepage URL."""
    from agents.outreach import brand_assets

    def fake_get(url, headers=None, timeout=None, allow_redirects=False, stream=False):
        # Pretend `https://www.yana-active.co.uk` resolves with HTML
        # containing the brand name; every other candidate 404s.
        if "yana-active.co.uk" in url:
            html = b"<html><head><title>YANA Active - Premium activewear</title></head><body>YANA Active</body></html>"
            return _FakeResp(200, html, headers={"Content-Type": "text/html"}, url=url)
        return _FakeResp(404, b"", headers={"Content-Type": "text/html"})

    with patch.object(brand_assets.requests, "get", fake_get):
        url = brand_assets.discover_website_url("YANA Active", country="GB")

    assert url is not None
    assert "yana-active.co.uk" in url
    # Homepage path only (no deep path).
    assert url.endswith("/")


def test_discover_website_url_returns_none_when_no_candidate_resolves():
    """Every candidate 404s or returns mismatching content -> None.
    The function must never invent a URL or pick a squatter."""
    from agents.outreach import brand_assets

    def always_404(url, headers=None, timeout=None, allow_redirects=False, stream=False):
        return _FakeResp(404, b"", headers={"Content-Type": "text/html"})

    with patch.object(brand_assets.requests, "get", always_404):
        url = brand_assets.discover_website_url("Made Up Brand XYZ", country="GB")

    assert url is None


def test_discover_website_url_rejects_html_that_does_not_mention_brand():
    """A candidate that resolves but doesn't carry the brand name in
    its body should be rejected - this guards against landing on a
    squatter or an unrelated business."""
    from agents.outreach import brand_assets

    def fake_get(url, headers=None, timeout=None, allow_redirects=False, stream=False):
        html = b"<html><body>This is a different company - nothing to do with the prospect.</body></html>"
        return _FakeResp(200, html, headers={"Content-Type": "text/html"}, url=url)

    with patch.object(brand_assets.requests, "get", fake_get):
        url = brand_assets.discover_website_url("YANA Active")

    assert url is None


# --------------------------------------------------------------------------- #
# Sanity: capture wiring back-pressure
# --------------------------------------------------------------------------- #


def test_capture_ad_screenshots_respects_max_screenshots_cap(tmp_path: Path):
    """The orchestrator stops after `max_screenshots` ads even when
    every input ad carries a working image_url - a hard cap is the
    only reason this never blocks the deck build."""
    from agents.outreach import ad_screenshots

    ads = [
        {"ad_archive_id": str(i), "image_url": f"https://cdn.example/{i}.png"}
        for i in range(10)
    ]

    def fake_get(url, **kwargs):
        return _FakeResp(200, _png_bytes(), headers={"Content-Type": "image/png"})

    with patch.object(ad_screenshots.requests, "get", fake_get):
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect-5", ads, prospects_root=tmp_path, max_screenshots=3
        )

    assert result["captured"] == 3
    captured_paths = [
        a.get("ad_screenshot_path") for a in result["ads"] if a.get("ad_screenshot_path")
    ]
    assert len(captured_paths) == 3


# --------------------------------------------------------------------------- #
# V3 - per-ad capture metadata (capture_status, image_path_local, ...)
# --------------------------------------------------------------------------- #


def test_capture_sets_image_path_local_when_image_url_downloaded(tmp_path: Path):
    """V3: when path-A downloads a static `image_url`, both
    `ad_screenshot_path` AND `image_path_local` carry the relative path,
    and `capture_status` reads as `download_image_url`. The deck builder
    only uses ad_screenshot_path; the *_path_local mirrors are operator
    breadcrumbs for distinguishing what kind of asset we have."""
    from agents.outreach import ad_screenshots

    ads = [{"ad_archive_id": "AD-A", "image_url": "https://cdn.example/i.png"}]

    def fake_get(url, **kwargs):
        return _FakeResp(200, _png_bytes(), headers={"Content-Type": "image/png"})

    with patch.object(ad_screenshots.requests, "get", fake_get):
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect-v3a", ads, prospects_root=tmp_path
        )

    out = result["ads"][0]
    assert out["capture_status"] == "download_image_url"
    assert out["image_path_local"] == out["ad_screenshot_path"]
    assert "video_preview_path_local" not in out
    assert "capture_error" not in out


def test_capture_sets_video_preview_path_local_when_video_preview_used(tmp_path: Path):
    """V3: when only `video_preview_image_url` is present, the orchestrator
    downloads it and records `video_preview_path_local` + status
    `download_video_preview`."""
    from agents.outreach import ad_screenshots

    ads = [
        {
            "ad_archive_id": "AD-V",
            "video_preview_image_url": "https://cdn.example/preview.jpg",
        }
    ]

    def fake_get(url, **kwargs):
        return _FakeResp(200, _png_bytes(), headers={"Content-Type": "image/jpeg"})

    with patch.object(ad_screenshots.requests, "get", fake_get):
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect-v3b", ads, prospects_root=tmp_path
        )

    out = result["ads"][0]
    assert out["capture_status"] == "download_video_preview"
    assert out["video_preview_path_local"] == out["ad_screenshot_path"]
    assert "image_path_local" not in out


def test_capture_records_playwright_missing_status(tmp_path: Path):
    """V3: when the ad has only a library URL and Playwright is not
    installed, the orchestrator sets `capture_status='playwright_missing'`
    and `capture_error` with the exact install commands."""
    from agents.outreach import ad_screenshots

    ads = [
        {
            "ad_archive_id": "AD-PW",
            "ad_library_url": "https://www.facebook.com/ads/library/?id=AD-PW",
        }
    ]

    with patch.object(ad_screenshots, "_playwright_available", lambda: False):
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect-v3c", ads, prospects_root=tmp_path
        )

    out = result["ads"][0]
    assert out["capture_status"] == "playwright_missing"
    assert "py -3.11 -m pip install playwright" in out["capture_error"]
    assert "playwright install chromium" in out["capture_error"]


def test_capture_records_no_url_status_when_nothing_to_screenshot(tmp_path: Path):
    """V3: an ad with no image_url, no video preview, and no library URL
    is recorded with `capture_status='no_url'` so the operator can audit
    why nothing was captured."""
    from agents.outreach import ad_screenshots

    # No image_url, no video_preview_image_url, no ad_archive_id => no library URL.
    ads = [{"page_name": "Some Brand"}]

    result = ad_screenshots.capture_ad_screenshots(
        "test-prospect-v3d", ads, prospects_root=tmp_path
    )

    out = result["ads"][0]
    assert out["capture_status"] == "no_url"
    assert "no URL to screenshot" in out["capture_error"]


def test_capture_skipped_cap_status_on_overflow_ads(tmp_path: Path):
    """V3: ads beyond the max_screenshots cap are marked `skipped_cap`
    so it is obvious from audit.json why they don't have screenshots."""
    from agents.outreach import ad_screenshots

    ads = [
        {"ad_archive_id": str(i), "image_url": f"https://cdn.example/{i}.png"}
        for i in range(5)
    ]

    def fake_get(url, **kwargs):
        return _FakeResp(200, _png_bytes(), headers={"Content-Type": "image/png"})

    with patch.object(ad_screenshots.requests, "get", fake_get):
        result = ad_screenshots.capture_ad_screenshots(
            "test-prospect-v3e", ads, prospects_root=tmp_path, max_screenshots=2
        )

    statuses = [a.get("capture_status") for a in result["ads"]]
    # First two captured, remaining three marked `skipped_cap`.
    assert statuses[:2] == ["download_image_url", "download_image_url"]
    assert statuses[2:] == ["skipped_cap", "skipped_cap", "skipped_cap"]
