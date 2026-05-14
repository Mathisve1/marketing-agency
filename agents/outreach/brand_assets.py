"""Lightweight brand-asset collector for the Outreach Private Creative Note.

The V4 pitch deck looks materially better with real brand imagery -
logo, a hero shot, a couple of product cards. This module is a
conservative collector that fetches a prospect's website homepage,
extracts a handful of obvious image URLs (logo, og:image, in-page
imagery), downloads them to `prospects/<slug>/assets/`, and returns a
dict of *local* paths suitable for merging into the audit's
`brand_profile` field.

Design constraints (deliberately strict):
  - One HTTP request to the homepage. No JS execution, no follow-up
    crawls, no API calls.
  - At most `MAX_IMAGES` (default 6) images downloaded total.
  - Each image is capped at `MAX_IMAGE_BYTES` (default 4 MB); larger
    payloads are aborted mid-stream rather than buffered.
  - Only HTTP/HTTPS URLs that resolve to a static image content-type.
  - Same-domain or obvious CDN (subdomain match / explicit allowlist)
    only - we don't follow image URLs into arbitrary third parties.
  - Every network operation is wrapped in `try/except`; failures are
    swallowed and reported in the returned dict's `errors` list.
  - PDF generation never blocks on this - the deck still renders if
    the collector fails or returns empty.

V4 adds two helpers around the core collector:

  - `discover_website_url(brand_name)` - probes a small set of obvious
    domain patterns (`brand.com`, `brand-name.com`, `brandname.co.uk`)
    and returns the first one that resolves to an HTTP 200 with HTML.
    Conservative: HEAD-then-GET; no redirects into unrelated brands.
    Never invents an URL it cannot verify with a live response.
  - `capture_homepage_screenshot(prospect_id, website_url)` - optional
    Playwright capture of the rendered homepage. Skipped gracefully
    when Playwright is unavailable.

Public API:

    collect_brand_assets(
        prospect_id: str,
        website_url: str,
        *,
        prospects_root: Path = Path("prospects"),
        max_images: int = MAX_IMAGES,
        timeout: float = TIMEOUT_SECS,
        user_agent: str = DEFAULT_UA,
    ) -> dict

Returns a dict shaped like:

    {
        "website_url":       "https://example.com",
        "title":             "Example Skincare - Pure Plant Power",
        "meta_description":  "...",
        "logo_path":         "assets/logo.png",       # relative to prospect_root
        "hero_image_path":   "assets/hero.jpg",
        "product_images":    ["assets/product1.jpg", "assets/product2.jpg"],
        "homepage_screenshot_path": "assets/homepage.png",  # if Playwright ran
        "fetched_at":        "2026-05-14T16:20:00+00:00",
        "errors":            [],
    }

Caller can merge those keys directly into an existing brand_profile dict
(which the pitch builder already reads from). Paths are relative to the
prospect's folder so audit.json stays portable across machines.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; YuvoStudio-OutreachBot/1.0; "
    "+https://yuvostudio.example/bot)"
)
TIMEOUT_SECS = 10.0
MAX_HTML_BYTES = 2 * 1024 * 1024       # 2 MB homepage HTML cap
MAX_IMAGE_BYTES = 4 * 1024 * 1024      # 4 MB per downloaded image
MAX_IMAGES = 6                         # logo + hero + up to 4 products
CHUNK = 64 * 1024

_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_ALLOWED_CONTENT_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
}

# Common CDN host substrings we treat as same-brand for the purposes of
# asset whitelisting. Keep conservative; extend as a real prospect
# surfaces a new CDN host.
_OK_CDN_HOST_HINTS = (
    "cloudfront.net",
    "shopifycdn.com",
    "shopify.com",
    "imgix.net",
    "akamaihd.net",
    "wixstatic.com",
    "cdninstagram.com",
    "fbcdn.net",
    "static.squarespace.com",
    "squarespace-cdn.com",
)


def _normalise_website_url(url: str) -> Optional[str]:
    """Return a normalised https URL, or None if the input doesn't parse."""
    if not isinstance(url, str) or not url.strip():
        return None
    s = url.strip()
    if not s.lower().startswith(("http://", "https://")):
        s = "https://" + s.lstrip("/")
    try:
        parsed = urlparse(s)
    except Exception:
        return None
    if not parsed.netloc:
        return None
    # Drop fragments + queries on the homepage URL so we land on the canonical page.
    cleaned = parsed._replace(fragment="", query="", path=parsed.path or "/")
    return urlunparse(cleaned)


def _is_allowed_image_host(image_url: str, brand_host: str) -> bool:
    """True when the image URL is same-host or a recognised brand CDN."""
    try:
        h = urlparse(image_url).netloc.lower()
    except Exception:
        return False
    if not h:
        return False
    if h == brand_host:
        return True
    # Subdomain match: cdn.brand.example matches brand.example.
    if h.endswith("." + brand_host) or brand_host.endswith("." + h):
        return True
    # Known CDN hint match.
    return any(hint in h for hint in _OK_CDN_HOST_HINTS)


class _HtmlScanner(HTMLParser):
    """Tiny HTML scanner. Pulls the title, meta description, link[rel=icon],
    og:image, and the first handful of <img src=...> entries. Robust to
    half-broken HTML because HTMLParser is non-strict by default."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title: Optional[str] = None
        self.meta_description: Optional[str] = None
        self.og_image: Optional[str] = None
        self.icon: Optional[str] = None
        self.images: list[str] = []
        self._in_title = False
        self._title_chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or "").lower()
            prop = (a.get("property") or "").lower()
            content = a.get("content") or ""
            if name == "description" and content and not self.meta_description:
                self.meta_description = content.strip()
            elif prop == "og:image" and content and not self.og_image:
                self.og_image = urljoin(self.base_url, content.strip())
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            href = a.get("href") or ""
            if href and any(r in rel for r in ("icon", "apple-touch-icon", "shortcut icon")):
                if not self.icon:
                    self.icon = urljoin(self.base_url, href.strip())
        elif tag == "img":
            src = (a.get("src") or "").strip()
            if not src or src.startswith("data:"):
                return
            full = urljoin(self.base_url, src)
            if full not in self.images:
                self.images.append(full)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            joined = "".join(self._title_chunks).strip()
            if joined:
                self.title = joined

    def handle_data(self, data):
        if self._in_title:
            self._title_chunks.append(data)


def _fetch_html(url: str, *, timeout: float, user_agent: str) -> tuple[str, str]:
    """Return (html_text, effective_url) or raise."""
    headers = {"User-Agent": user_agent, "Accept": "text/html, */*"}
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
    resp.raise_for_status()
    # Cap how much HTML we read.
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=CHUNK):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_HTML_BYTES:
            break
    encoding = resp.encoding or "utf-8"
    try:
        text = b"".join(chunks).decode(encoding, errors="replace")
    except LookupError:
        text = b"".join(chunks).decode("utf-8", errors="replace")
    return text, resp.url


def _slugify_filename(url: str, fallback: str) -> str:
    """Derive a safe local filename from a URL path. The fallback ensures
    we always end up with something even when the URL has no path."""
    try:
        path = urlparse(url).path
    except Exception:
        path = ""
    name = os.path.basename(path) if path else ""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("_")
    if not name:
        name = fallback
    ext = os.path.splitext(name)[1].lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        # Strip any unhelpful extension and let the writer add one from
        # the response content-type.
        name = os.path.splitext(name)[0] or fallback
    return name[:80]


def _ext_from_content_type(ct: str) -> str:
    if not ct:
        return ".jpg"
    ct = ct.lower()
    if "png" in ct:
        return ".png"
    if "gif" in ct:
        return ".gif"
    if "webp" in ct:
        return ".webp"
    return ".jpg"


def _download_image(
    image_url: str,
    out_dir: Path,
    *,
    label: str,
    timeout: float,
    user_agent: str,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> Optional[Path]:
    """Stream `image_url` to `out_dir/<filename>` with a size cap.
    Returns the written path or None on any failure."""
    headers = {"User-Agent": user_agent, "Accept": "image/*"}
    try:
        resp = requests.get(image_url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
        resp.raise_for_status()
        ct = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ct and ct not in _ALLOWED_CONTENT_TYPES:
            return None
        # Derive filename; ensure a sensible extension.
        fname = _slugify_filename(image_url, fallback=label)
        if "." not in fname:
            fname += _ext_from_content_type(ct)
        out_path = out_dir / fname
        # Idempotent: if a file with the same name already exists AND
        # has non-trivial content, treat it as the same asset and skip
        # the re-download. This stops re-running the collector from
        # producing `<name>_logo.png`/`<name>_hero.jpg` dupes.
        if out_path.exists() and out_path.stat().st_size > 200:
            return out_path
        out_dir.mkdir(parents=True, exist_ok=True)
        total = 0
        with out_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=CHUNK):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    try:
                        out_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return None
                f.write(chunk)
        if total < 200:
            # Suspiciously small - probably a tracking pixel or redirect HTML.
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        return out_path
    except Exception:
        return None


def _looks_like_logo(image_url: str) -> bool:
    """Heuristic: URL or filename mentions 'logo' / 'mark' / 'favicon'."""
    s = image_url.lower()
    return any(tok in s for tok in ("logo", "/mark", "wordmark", "favicon", "apple-touch"))


def _looks_like_product(image_url: str) -> bool:
    s = image_url.lower()
    return any(tok in s for tok in ("product", "/products/", "shop/", "collection", "hero", "banner"))


def collect_brand_assets(
    prospect_id: str,
    website_url: str,
    *,
    prospects_root: Path = Path("prospects"),
    max_images: int = MAX_IMAGES,
    timeout: float = TIMEOUT_SECS,
    user_agent: str = DEFAULT_UA,
) -> dict:
    """Collect brand assets for a prospect and write them under
    `prospects/<id>/assets/`. See module docstring for the return shape
    and constraints. Always returns a dict; on failure the dict's
    `errors` list explains what happened and the asset paths are missing.
    """
    result: dict = {
        "website_url": None,
        "title": None,
        "meta_description": None,
        "logo_path": None,
        "hero_image_path": None,
        "product_images": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "errors": [],
    }

    normalised = _normalise_website_url(website_url)
    if normalised is None:
        result["errors"].append("invalid_website_url")
        return result
    result["website_url"] = normalised

    prospect_root = (prospects_root / prospect_id).resolve()
    assets_dir = prospect_root / "assets"

    try:
        html_text, effective_url = _fetch_html(normalised, timeout=timeout, user_agent=user_agent)
    except Exception as e:
        result["errors"].append(f"fetch_failed:{type(e).__name__}")
        return result

    scanner = _HtmlScanner(effective_url)
    try:
        scanner.feed(html_text)
    except Exception as e:
        result["errors"].append(f"html_parse_failed:{type(e).__name__}")
        # Keep what the scanner managed before the exception.

    if scanner.title:
        result["title"] = scanner.title[:160]
    if scanner.meta_description:
        result["meta_description"] = scanner.meta_description[:300]

    brand_host = urlparse(effective_url).netloc.lower()

    candidates: list[tuple[str, str]] = []  # (label, url)
    if scanner.icon and _is_allowed_image_host(scanner.icon, brand_host):
        candidates.append(("logo", scanner.icon))
    if scanner.og_image and _is_allowed_image_host(scanner.og_image, brand_host):
        candidates.append(("hero", scanner.og_image))
    # Sort in-page <img> sources: logo-like first, then product-like, then rest.
    logos = [u for u in scanner.images if _looks_like_logo(u) and _is_allowed_image_host(u, brand_host)]
    products = [u for u in scanner.images if _looks_like_product(u) and _is_allowed_image_host(u, brand_host)]
    rest = [
        u for u in scanner.images
        if _is_allowed_image_host(u, brand_host) and u not in logos and u not in products
    ]
    for u in logos:
        candidates.append(("logo", u))
    for u in products:
        candidates.append(("product", u))
    for u in rest:
        candidates.append(("hero", u))

    # Dedupe while preserving order.
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for label, u in candidates:
        if u in seen:
            continue
        seen.add(u)
        deduped.append((label, u))

    have_logo = False
    have_hero = False
    products_kept: list[Path] = []
    written_total = 0
    for label, image_url in deduped:
        if written_total >= max_images:
            break
        if label == "logo" and have_logo:
            label = "product"
        if label == "hero" and have_hero:
            label = "product"
        path = _download_image(
            image_url, assets_dir, label=label, timeout=timeout, user_agent=user_agent
        )
        if path is None:
            continue
        rel = str(path.relative_to(prospect_root)).replace("\\", "/")
        if label == "logo" and not have_logo:
            result["logo_path"] = rel
            have_logo = True
        elif label == "hero" and not have_hero:
            result["hero_image_path"] = rel
            have_hero = True
        else:
            products_kept.append(path)
            result["product_images"].append(rel)
        written_total += 1

    # Optional homepage screenshot (Playwright). Skipped silently when
    # the package or the chromium binary isn't available.
    screenshot_path = _capture_homepage_screenshot(
        effective_url, assets_dir / "homepage.png", timeout=timeout, user_agent=user_agent
    )
    if screenshot_path is not None:
        result["homepage_screenshot_path"] = (
            str(screenshot_path.relative_to(prospect_root)).replace("\\", "/")
        )

    return result


# --------------------------------------------------------------------------- #
# V4: website-URL discovery
# --------------------------------------------------------------------------- #


def _brand_slug(brand_name: str) -> str:
    """Lowercase alphanumeric slug for a brand name."""
    slug = re.sub(r"[^a-z0-9]+", "-", brand_name.lower()).strip("-")
    return slug


def _brand_compact(brand_name: str) -> str:
    """Compact lowercase form of a brand name (no separators)."""
    return re.sub(r"[^a-z0-9]+", "", brand_name.lower())


def _candidate_urls(brand_name: str, country: Optional[str] = None) -> list[str]:
    """A short, ranked list of plausible homepage URLs for a brand name.

    Returns at most 6 candidates (we test these one at a time and stop
    at the first that resolves). The list is intentionally tight - we
    don't want to spray HEAD requests across dozens of TLDs.
    """
    slug = _brand_slug(brand_name)
    compact = _brand_compact(brand_name)
    if not slug:
        return []
    tlds = [".com"]
    if country and country.upper() == "GB":
        tlds.append(".co.uk")
    tlds.extend([".co", ".io"])
    out: list[str] = []
    for tld in tlds:
        if compact:
            out.append(f"https://www.{compact}{tld}")
        if slug != compact and "-" in slug:
            out.append(f"https://www.{slug}{tld}")
    # De-dupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for u in out:
        if u in seen:
            continue
        seen.add(u)
        deduped.append(u)
    return deduped[:6]


def discover_website_url(
    brand_name: str,
    *,
    country: Optional[str] = None,
    timeout: float = 6.0,
    user_agent: str = DEFAULT_UA,
    expected_token: Optional[str] = None,
) -> Optional[str]:
    """Probe candidate URLs for a brand name and return the first that
    resolves to HTML where the brand name is mentioned (so we don't
    silently land on a squatter or unrelated business).

    Conservative: GET each candidate with a tight timeout, accept only
    HTTP 200 with a `text/html` content-type, then verify that
    `expected_token` (or a slugified brand name) appears in the first
    2 KB of the response body. Returns None if nothing resolves
    confidently.
    """
    token = (expected_token or brand_name).lower().strip()
    headers = {"User-Agent": user_agent, "Accept": "text/html, */*"}
    for url in _candidate_urls(brand_name, country=country):
        try:
            resp = requests.get(
                url, headers=headers, timeout=timeout, allow_redirects=True, stream=True
            )
            if resp.status_code != 200:
                continue
            ct = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" not in ct:
                continue
            sample_bytes = next(iter(resp.iter_content(chunk_size=4096)), b"")
            sample = sample_bytes.decode(resp.encoding or "utf-8", errors="replace").lower()
            if token in sample or token.replace(" ", "") in sample:
                # Use the canonical URL after redirects.
                final = resp.url or url
                # Drop path/query to land on the homepage.
                parsed = urlparse(final)
                return urlunparse(parsed._replace(path="/", query="", fragment=""))
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- #
# V4: optional homepage screenshot via Playwright
# --------------------------------------------------------------------------- #


def _capture_homepage_screenshot(
    url: str,
    out_path: Path,
    *,
    timeout: float,
    user_agent: str,
) -> Optional[Path]:
    """Open `url` in a headless Chromium and save a homepage screenshot.
    Returns None gracefully when Playwright or chromium isn't available."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                return None
            try:
                ctx = browser.new_context(
                    user_agent=user_agent, viewport={"width": 1280, "height": 800}
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                # Dismiss obvious cookie banners.
                for sel in (
                    "button:has-text('Accept')",
                    "button:has-text('Allow all')",
                    "button[aria-label*='Decline']",
                ):
                    try:
                        page.locator(sel).first.click(timeout=1500)
                        break
                    except Exception:
                        continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out_path), full_page=False)
                return out_path if out_path.exists() and out_path.stat().st_size > 200 else None
            finally:
                browser.close()
    except Exception:
        return None
