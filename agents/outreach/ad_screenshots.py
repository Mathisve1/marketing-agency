"""Ad screenshot capture pipeline (V4 asset task).

Two capture paths, tried in order per ad:

  1. **Direct image download** - when the Apify normaliser already
     captured a static `image_url` or `video_preview_image_url`, fetch
     that URL with `requests` and save it under
     `prospects/<id>/assets/ad_<n>.<ext>`. No browser needed, cheap and
     reliable.

  2. **Playwright screenshot** - when only the public Ads-Library URL
     is known (the common 'old audit' case), open it in a headless
     Chromium and screenshot the visible ad card. Playwright is
     optional; the function gracefully no-ops with a warning when:
       * The `playwright` Python package is not installed, OR
       * The Chromium browser is not downloaded
         (`playwright install chromium` was never run).

The capture orchestrator merges the resulting local paths back onto
the audit's `competitor_ads` entries as the new `ad_screenshot_path`
field, which the V4 pitch deck looks for when rendering the
'From live ad to video route' page (Page 3).

Public API:

    capture_ad_screenshots(
        prospect_id,
        ads,                # list[dict] - the audit's competitor_ads
        *,
        prospects_root=Path("prospects"),
        max_screenshots=4,
        timeout=15.0,
        user_agent=DEFAULT_UA,
    ) -> CaptureResult

`CaptureResult` is a plain dict shaped like:

    {
        "captured": int,                  # how many ads got a local path
        "playwright_available": bool,
        "errors": list[str],
        "ads": list[dict],                # input ads enriched with
                                          # `ad_screenshot_path` where capture
                                          # succeeded; original list NOT
                                          # mutated.
    }

Failures are non-fatal everywhere - the deck still renders without
screenshots.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; YuvoStudio-OutreachBot/1.0; "
    "+https://yuvostudio.example/bot)"
)
TIMEOUT_SECS = 15.0
MAX_SCREENSHOTS = 4
MAX_IMAGE_BYTES = 4 * 1024 * 1024
CHUNK = 64 * 1024

_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


# --------------------------------------------------------------------------- #
# Path & filename helpers
# --------------------------------------------------------------------------- #


def _safe_ext_from_url(url: str) -> str:
    """Return a safe `.ext` derived from the URL path, or `.jpg`."""
    try:
        path = urlparse(url).path or ""
    except Exception:
        path = ""
    ext = os.path.splitext(path)[1].lower()
    if ext in _ALLOWED_IMAGE_EXTS:
        return ext
    return ".jpg"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Path-A: direct download of a known image URL
# --------------------------------------------------------------------------- #


def _download_image_to(
    url: str,
    out_path: Path,
    *,
    timeout: float,
    user_agent: str,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> Optional[Path]:
    """Stream `url` to `out_path` with a size cap. Returns None on any
    failure (network, content-type, size cap)."""
    headers = {"User-Agent": user_agent, "Accept": "image/*"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
        resp.raise_for_status()
        ct = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ct and not ct.startswith("image/"):
            log.warning("ad_screenshots: %s returned non-image content-type %s", url, ct)
            return None
        total = 0
        chunks: list[bytes] = []
        for chunk in resp.iter_content(chunk_size=CHUNK):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                log.warning("ad_screenshots: %s exceeded %d bytes; aborted", url, max_bytes)
                return None
            chunks.append(chunk)
        if total < 200:
            return None
        _atomic_write(out_path, b"".join(chunks))
        return out_path
    except Exception as e:
        log.warning("ad_screenshots: download failed for %s: %s: %s", url, type(e).__name__, e)
        return None


# --------------------------------------------------------------------------- #
# Path-B: Playwright headless screenshot of the Ads-Library URL
# --------------------------------------------------------------------------- #


def _playwright_available() -> bool:
    """True when the `playwright` Python package can be imported. We
    don't probe browser binaries here (that would launch a process) -
    that check is deferred to the actual screenshot call so the
    in-process detector stays cheap and side-effect free."""
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def _screenshot_via_playwright(
    url: str,
    out_path: Path,
    *,
    timeout: float,
    user_agent: str,
) -> Optional[Path]:
    """Open `url` in a headless Chromium and screenshot the main ad card.
    Returns None on any failure - Playwright not installed, Chromium
    not downloaded, navigation failed, etc."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        log.info("ad_screenshots: playwright not importable (%s); skipping screenshot for %s", e, url)
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                # Chromium binary missing - tell the operator how to fix.
                log.warning(
                    "ad_screenshots: chromium launch failed (%s). "
                    "Run `python -m playwright install chromium` to enable.",
                    e,
                )
                return None
            try:
                context = browser.new_context(user_agent=user_agent, viewport={"width": 800, "height": 1200})
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
                # Try to dismiss a cookies overlay if one is present.
                for sel in (
                    "button[aria-label*='Decline']",
                    "button[aria-label*='Only allow']",
                    "button:has-text('Decline')",
                ):
                    try:
                        page.locator(sel).first.click(timeout=1500)
                        break
                    except Exception:
                        continue
                # Screenshot the first plausible ad card on the page,
                # falling back to the visible viewport when no card
                # locator matches.
                card_locator = page.locator("[role='article'], [data-pagelet='AdsLibrary']").first
                try:
                    card_locator.scroll_into_view_if_needed(timeout=2000)
                    card_locator.screenshot(path=str(out_path), timeout=5000)
                except Exception:
                    page.screenshot(path=str(out_path), full_page=False)
                return out_path if out_path.exists() and out_path.stat().st_size > 200 else None
            finally:
                browser.close()
    except Exception as e:
        log.warning("ad_screenshots: playwright capture failed for %s: %s: %s", url, type(e).__name__, e)
        return None


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


def _prefer_direct_image(ad: dict) -> tuple[Optional[str], Optional[str]]:
    """Return (best_static_url, source_kind) we can download directly.

    `source_kind` is one of:
      - "image_url"          : a static Apify image URL
      - "video_preview"      : the static thumbnail of a video ad
      - "image_urls"         : an entry from the bulk image_urls list

    Returns (None, None) when no inline asset is present.
    """
    v = ad.get("image_url")
    if isinstance(v, str) and v.lower().startswith(("http://", "https://")):
        return v.strip(), "image_url"
    v = ad.get("video_preview_image_url")
    if isinstance(v, str) and v.lower().startswith(("http://", "https://")):
        return v.strip(), "video_preview"
    image_urls = ad.get("image_urls") or []
    if isinstance(image_urls, list):
        for v in image_urls:
            if isinstance(v, str) and v.lower().startswith(("http://", "https://")):
                return v.strip(), "image_urls"
    return None, None


def _ad_library_url(ad: dict) -> Optional[str]:
    """Best public ads-library URL for the ad."""
    for key in ("ad_library_url", "snapshot_url"):
        v = ad.get(key)
        if isinstance(v, str) and v.lower().startswith(("http://", "https://")):
            return v.strip()
    arch_id = ad.get("ad_archive_id")
    if isinstance(arch_id, (int, str)):
        s = str(arch_id).strip()
        if s and re.fullmatch(r"\d+", s):
            return f"https://www.facebook.com/ads/library/?id={s}"
    return None


def capture_ad_screenshots(
    prospect_id: str,
    ads: list[dict],
    *,
    prospects_root: Path = Path("prospects"),
    max_screenshots: int = MAX_SCREENSHOTS,
    timeout: float = TIMEOUT_SECS,
    user_agent: str = DEFAULT_UA,
) -> dict:
    """Capture local screenshots/thumbnails for each ad in `ads`.

    See module docstring for the full contract. Always returns a dict;
    on any per-ad failure the dict carries an `errors` entry and the
    original ad gets no `ad_screenshot_path` field.

    Input ads are NOT mutated - the returned `ads` list is a shallow
    copy with the new field merged onto each entry.
    """
    prospect_root = (prospects_root / prospect_id).resolve()
    assets_dir = prospect_root / "assets"

    out_ads: list[dict] = [dict(a) for a in ads]
    captured = 0
    errors: list[str] = []
    pw_available = _playwright_available()

    for i, ad in enumerate(out_ads):
        if captured >= max_screenshots:
            ad.setdefault("capture_status", "skipped_cap")
            continue
        direct_url, source_kind = _prefer_direct_image(ad)
        target_path = assets_dir / f"ad_{i + 1}{_safe_ext_from_url(direct_url or '')}"

        # Path A: direct image download (cheapest, no browser needed).
        if direct_url:
            result = _download_image_to(direct_url, target_path, timeout=timeout, user_agent=user_agent)
            if result is not None:
                rel = str(result.relative_to(prospect_root)).replace("\\", "/")
                ad["ad_screenshot_path"] = rel
                ad["capture_status"] = f"download_{source_kind}"
                # Mirror the path under a source-named field so callers
                # who want to distinguish "this is a video thumbnail" vs
                # "this is the ad still" can do so without re-deriving.
                if source_kind == "image_url":
                    ad["image_path_local"] = rel
                elif source_kind == "video_preview":
                    ad["video_preview_path_local"] = rel
                else:
                    ad["image_path_local"] = rel  # image_urls bulk-list entry
                captured += 1
                continue
            err = f"ad[{i}]: direct download failed for {direct_url}"
            errors.append(err)
            ad["capture_error"] = err

        # Path B: Playwright screenshot of the public ads-library URL.
        lib_url = _ad_library_url(ad)
        if not lib_url:
            err = f"ad[{i}]: no URL to screenshot"
            errors.append(err)
            ad["capture_status"] = "no_url"
            ad["capture_error"] = err
            continue

        target_path = assets_dir / f"ad_{i + 1}.png"
        if not pw_available:
            err = (
                f"ad[{i}]: playwright not installed; install with "
                "`py -3.11 -m pip install playwright` then "
                "`py -3.11 -m playwright install chromium`"
            )
            errors.append(err)
            ad["capture_status"] = "playwright_missing"
            ad["capture_error"] = err
            continue
        result = _screenshot_via_playwright(
            lib_url, target_path, timeout=timeout, user_agent=user_agent
        )
        if result is not None:
            rel = str(result.relative_to(prospect_root)).replace("\\", "/")
            ad["ad_screenshot_path"] = rel
            ad["capture_status"] = "screenshot_playwright"
            captured += 1
        else:
            err = f"ad[{i}]: playwright capture skipped or failed for {lib_url}"
            errors.append(err)
            ad["capture_status"] = "playwright_failed"
            ad["capture_error"] = err

    return {
        "captured": captured,
        "playwright_available": pw_available,
        "errors": errors,
        "ads": out_ads,
    }
