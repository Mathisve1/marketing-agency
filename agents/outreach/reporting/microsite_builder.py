"""Private microsite builder - hosted private creative-note distribution.

Wraps `html_deck_builder` and gives each prospect a self-contained,
deployable microsite under `prospects/<id>/site/`:

  prospects/<id>/site/
    index.html        # the deck, with <meta name=robots noindex>
    assets/           # copies of every image / video the deck references
    manifest.json     # routing + provenance metadata (private slug, etc.)

The `private_slug` includes a stable random 6-char token so URLs like
`https://pitch.yuvostudio.com/p/haeckels-k7x4q9/` cannot be guessed by
walking the prospect index. Token generation is idempotent: re-running
the builder reuses the existing token from `manifest.json` instead of
churning the public URL on every rebuild.

`build_deploy_package()` produces an upload-ready folder under
`build/pitches/p/<private_slug>/` - same layout as the live microsite,
ready to drop into a Cloudflare Pages project (see
`docs/microsite_deployment.md`).

Watermark safety:
  We never auto-embed an unwatermarked preview video. The auto-detector
  only matches `assets/preview_watermarked.mp4`. A bare `preview.mp4`
  in the assets folder is logged and ignored - the operator must
  watermark it explicitly. A future ffmpeg watermark helper will live
  in this module (TODO).
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import string
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agents.outreach.reporting.deck_brief import (
    AdProof,
    ConceptRoute,
    DeckBrief,
)
from agents.outreach.reporting.html_deck_builder import build_html_deck

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_PUBLIC_BASE_URL = "https://yuvo-pitches.pages.dev"
"""Default public-URL base for the rendered manifest.

MVP uses the free Cloudflare Pages domain. When the operator wires up a
custom domain, override this via the `PITCH_BASE_URL` env var or the
`public_base_url` keyword on `build_microsite()`. The `/p/<slug>/` path
suffix is added by the builder, not by this constant - pass only the
scheme + host (no trailing `/p`)."""

PITCH_BASE_URL_ENV = "PITCH_BASE_URL"
"""Name of the env var that overrides DEFAULT_PUBLIC_BASE_URL at runtime."""

PITCH_CONTACT_EMAIL_ENV = "PITCH_CONTACT_EMAIL"
"""Env var holding the mailto destination for the interest-form fallback.
Defaults to `hello@yuvostudio.com` when unset. Public; safe in HTML."""

PITCH_FORM_ENDPOINT_ENV = "PITCH_FORM_ENDPOINT"
"""Env var holding the optional Cloudflare Pages Function / Worker URL
(typically `/api/interest`). When set, the interest form renders a
real `<form method="POST">` instead of the mailto fallback. The
frontend carries no secret; the endpoint reads its own bindings."""

TOKEN_ALPHABET = string.ascii_lowercase + string.digits
"""URL-safe alphabet for the private-slug token. ~36^6 = 2.2 B combinations."""

TOKEN_LENGTH = 6
"""Length of the random token appended to the prospect slug."""

WATERMARKED_PREVIEW_FILENAME = "preview_watermarked.mp4"
"""Only files matching this exact name are auto-embedded. `preview.mp4` is
deliberately rejected to enforce the watermark-safety contract."""

DEPLOY_PACKAGE_ROOT = Path("build") / "pitches" / "p"
"""Root of the upload-ready deploy package, sibling to the repo root."""

CLOUDFLARE_MAX_SAFE_ASSET_BYTES = 25 * 1024 * 1024
"""Cloudflare Pages serves static assets up to 25 MB safely; bigger files
trigger a soft warning so the operator can compress before upload."""


# --------------------------------------------------------------------------- #
# Slug + token
# --------------------------------------------------------------------------- #


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _brand_slug(brand_name: str, fallback: str) -> str:
    """Lowercase, hyphen-separated, alphanumeric slug from a brand name."""
    raw = (brand_name or "").lower()
    slug = _SLUG_RE.sub("-", raw).strip("-")
    if not slug:
        slug = (fallback or "brand").lower()
    return slug[:40]


def _generate_token(length: int = TOKEN_LENGTH) -> str:
    """Cryptographically random URL-safe token. Re-used across rebuilds."""
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))


def _compose_private_slug(brand_slug: str, token: str) -> str:
    """`<brand-slug>-<token>` - the user-facing route key."""
    return f"{brand_slug}-{token}"


def _read_existing_status(manifest_path: Path) -> Optional[str]:
    """Best-effort: return the `status` field of an existing manifest, or
    None if the manifest is missing/unreadable. Used so that re-running
    `build_microsite` after a deploy keeps the 'Live' banner instead of
    silently downgrading the page back to 'draft'."""
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    s = data.get("status")
    return s if isinstance(s, str) else None


def _existing_token(manifest_path: Path) -> Optional[str]:
    """Read the persisted token from an existing manifest, if any.

    Returns None when the manifest is missing, malformed, or carries a
    token that does not match the expected alphabet/length. This means a
    hand-edited or corrupted manifest cannot pin a bad token forever -
    we will regenerate one and overwrite.
    """
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    slug = data.get("private_slug")
    if not isinstance(slug, str) or "-" not in slug:
        return None
    candidate = slug.rsplit("-", 1)[-1]
    if len(candidate) != TOKEN_LENGTH:
        return None
    if not all(c in TOKEN_ALPHABET for c in candidate):
        return None
    return candidate


# --------------------------------------------------------------------------- #
# Watermark guard
# --------------------------------------------------------------------------- #


def _resolve_preview_video(prospect_root: Path) -> Optional[Path]:
    """Return the watermarked preview MP4 if it exists, else None.

    Rejects `preview.mp4` (unwatermarked) on purpose - the operator
    must produce a watermarked copy before the microsite will surface
    a preview slide. A future ffmpeg helper will produce that copy
    in-process; for now the rejection is logged so the operator knows
    why no video is showing.
    """
    assets_dir = prospect_root / "assets"
    watermarked = assets_dir / WATERMARKED_PREVIEW_FILENAME
    if watermarked.is_file():
        return watermarked
    bare = assets_dir / "preview.mp4"
    if bare.is_file():
        log.warning(
            "microsite_builder: %s found but no %s; refusing to embed an "
            "unwatermarked preview. Watermark it first.",
            bare, WATERMARKED_PREVIEW_FILENAME,
        )
    return None


# TODO(yuvo): add `watermark_preview_video(src, dst, brand)` using ffmpeg
# so the operator can produce assets/preview_watermarked.mp4 from
# assets/preview.mp4 without leaving Python. Until then, watermarking is
# manual (Premiere / CapCut / ffmpeg one-liner).


# --------------------------------------------------------------------------- #
# Asset copying
# --------------------------------------------------------------------------- #


def _gather_used_paths(brief: DeckBrief) -> list[Path]:
    """All asset Paths the deck will actually reference, deduped, in order."""
    seen: set[Path] = set()
    out: list[Path] = []
    for p in [brief.logo_path, brief.hero_image_path] + list(brief.product_images):
        if p is None or p in seen:
            continue
        seen.add(p)
        out.append(p)
    for ad in brief.ads:
        if ad.screenshot_path is None or ad.screenshot_path in seen:
            continue
        seen.add(ad.screenshot_path)
        out.append(ad.screenshot_path)
    for c in brief.concepts:
        if c.visual_path is None or c.visual_path in seen:
            continue
        seen.add(c.visual_path)
        out.append(c.visual_path)
    return out


def _safe_assets_name(path: Path) -> str:
    """Filesystem-safe filename used inside `site/assets/`."""
    name = path.name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("_")
    return cleaned or "asset"


def _copy_assets_into_site(
    brief: DeckBrief,
    site_dir: Path,
    preview_src: Optional[Path],
) -> tuple[DeckBrief, list[str], Optional[str], Optional[int]]:
    """Copy every brief-referenced asset (plus optional preview video) into
    `<site_dir>/assets/` and return a new brief whose Paths point at the
    copies.

    Returns:
      rewritten_brief, list[str] of relative asset URLs, optional preview
      video URL, optional preview video size in bytes.
    """
    assets_dir = site_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    mapping: dict[Path, Path] = {}
    used_rel: list[str] = []
    used_names: set[str] = set()

    for src in _gather_used_paths(brief):
        # Avoid clobbering when two paths share a basename.
        base = _safe_assets_name(src)
        candidate = base
        i = 1
        while candidate in used_names:
            stem, ext = os.path.splitext(base)
            candidate = f"{stem}_{i}{ext}"
            i += 1
        dst = assets_dir / candidate
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            log.warning("microsite_builder: failed to copy %s -> %s: %s", src, dst, e)
            continue
        mapping[src] = dst
        used_names.add(candidate)
        used_rel.append(f"assets/{candidate}")

    preview_rel: Optional[str] = None
    preview_size: Optional[int] = None
    if preview_src is not None:
        preview_dst = assets_dir / WATERMARKED_PREVIEW_FILENAME
        try:
            shutil.copy2(preview_src, preview_dst)
            preview_rel = f"assets/{WATERMARKED_PREVIEW_FILENAME}"
            preview_size = preview_dst.stat().st_size
        except OSError as e:
            log.warning("microsite_builder: failed to copy preview video: %s", e)

    rewritten = _rewrite_brief(brief, mapping, site_dir)
    return rewritten, used_rel, preview_rel, preview_size


def _rewrite_brief(brief: DeckBrief, mapping: dict[Path, Path], site_dir: Path) -> DeckBrief:
    """Return a shallow-copied DeckBrief whose Path fields point at
    copies under `site_dir/assets/` instead of the original prospect
    assets folder."""

    def remap(p: Optional[Path]) -> Optional[Path]:
        return mapping.get(p, p) if p is not None else None

    new_ads = [
        AdProof(
            archive_id=a.archive_id,
            issue_label=a.issue_label,
            issue_explainer=a.issue_explainer,
            body_excerpt=a.body_excerpt,
            cta_text=a.cta_text,
            days_active=a.days_active,
            library_url=a.library_url,
            screenshot_path=remap(a.screenshot_path),
            suggested_route=a.suggested_route,
        )
        for a in brief.ads
    ]
    new_concepts = [
        ConceptRoute(
            label=c.label,
            title=c.title,
            hook=c.hook,
            cta=c.cta,
            visual_path=remap(c.visual_path),
        )
        for c in brief.concepts
    ]
    # asdict() on the parent DeckBrief is hostile to non-serialisable
    # Path objects in some edge cases; copy attribute-wise instead.
    return DeckBrief(
        prospect_name=brief.prospect_name,
        niche=brief.niche,
        agency_name=brief.agency_name,
        website_url=brief.website_url,
        facebook_url=brief.facebook_url,
        instagram_url=brief.instagram_url,
        brand_tone=brief.brand_tone,
        product_category=brief.product_category,
        audience_assumption=brief.audience_assumption,
        primary_color=brief.primary_color,
        secondary_color=brief.secondary_color,
        accent_text_color=brief.accent_text_color,
        logo_path=remap(brief.logo_path),
        hero_image_path=remap(brief.hero_image_path),
        product_images=[remap(p) for p in brief.product_images if p is not None],
        cover_headline=brief.cover_headline,
        cover_subhead=brief.cover_subhead,
        cover_kicker=brief.cover_kicker,
        forty_five_second_cards=list(brief.forty_five_second_cards),
        ads=new_ads,
        gap_map_rows=list(brief.gap_map_rows),
        concepts=new_concepts,
        process_steps=list(brief.process_steps),
        pricing=list(brief.pricing),
        cta_headline=brief.cta_headline,
        cta_body=brief.cta_body,
        prospect_root=site_dir,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def resolve_public_base_url(override: Optional[str] = None) -> str:
    """Pick the public-URL base in priority order:

      1. Explicit `override` argument (used by tests + scripts).
      2. `PITCH_BASE_URL` env var (set in `.env` for the operator).
      3. `DEFAULT_PUBLIC_BASE_URL` (the free Cloudflare Pages domain).

    The returned URL has no trailing slash and no `/p` suffix; the
    builder appends `/p/<slug>/` itself.
    """
    raw = override if override is not None else os.environ.get(PITCH_BASE_URL_ENV)
    if not raw or not raw.strip():
        raw = DEFAULT_PUBLIC_BASE_URL
    return raw.strip().rstrip("/")


def resolve_contact_email(override: Optional[str] = None) -> Optional[str]:
    """Pick the interest-form mailto destination.

    Priority:
      1. Explicit `override` argument.
      2. `PITCH_CONTACT_EMAIL` env var.
      3. None - `build_html_deck` substitutes its own DEFAULT_CONTACT_EMAIL.
    """
    raw = override if override is not None else os.environ.get(PITCH_CONTACT_EMAIL_ENV)
    if not raw or not raw.strip():
        return None
    return raw.strip()


def resolve_form_endpoint(override: Optional[str] = None) -> Optional[str]:
    """Pick the optional POST endpoint for the interest form.

    Priority:
      1. Explicit `override` argument.
      2. `PITCH_FORM_ENDPOINT` env var.
      3. None - the deck falls back to the mailto path.
    """
    raw = override if override is not None else os.environ.get(PITCH_FORM_ENDPOINT_ENV)
    if not raw or not raw.strip():
        return None
    return raw.strip()


def build_microsite(
    prospect_id: str,
    *,
    prospects_root: Optional[Path] = None,
    public_base_url: Optional[str] = None,
    brief: Optional[DeckBrief] = None,
    output_dir: Optional[Path] = None,
    contact_email: Optional[str] = None,
    form_endpoint: Optional[str] = None,
    intended_status: Optional[str] = None,
) -> dict:
    """Build the private microsite for `prospect_id` and return its manifest.

    Steps:
      1. Build (or accept) a DeckBrief.
      2. Read the existing manifest's token (if any) so the public URL
         stays stable across rebuilds.
      3. Copy every used asset into `<site_dir>/assets/` (including the
         watermarked preview video, when present).
      4. Render the HTML deck with `noindex` and the preview-video URL.
      5. Write `manifest.json`.

    Returns the manifest dict (also persisted at site/manifest.json).

    Interest-form configuration:
      `contact_email` - mailto destination for the MVP fallback. When
        None, reads `PITCH_CONTACT_EMAIL` from the env, else defaults
        to the public `hello@yuvostudio.com` baked into the renderer.
      `form_endpoint` - optional Cloudflare Pages Function / Worker URL
        (typically `/api/interest`). When set, the interest form posts
        to it instead of opening mailto. Read from `PITCH_FORM_ENDPOINT`
        when the kwarg is None.
      `intended_status` - when set to `"draft"` or `"deployed"`,
        overrides the rendered status banner. The deploy script passes
        `"deployed"` for a real (non-dry-run) deploy so the uploaded
        HTML already carries the Live banner instead of the amber
        Draft banner. The manifest's `status` field stays `"draft"`
        until `mark_manifest_deployed()` runs after a successful
        upload - so a deploy that fails leaves the file-on-disk
        manifest honest about the public URL not being live yet.
        When `None`, falls back to whatever status the existing
        manifest carries (so re-running `build_microsite()` after a
        successful deploy preserves the Live banner across rebuilds).
    """
    if brief is None:
        brief = DeckBrief.from_audit(prospect_id, prospects_root=prospects_root)
    prospect_root = brief.prospect_root
    if prospect_root is None:
        raise ValueError(
            "build_microsite: brief.prospect_root is None; cannot decide "
            "where to write site/."
        )

    site_dir = Path(output_dir) if output_dir else (prospect_root / "site")
    site_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = site_dir / "manifest.json"

    # 1. Slug + token (stable across rebuilds).
    brand_slug = _brand_slug(brief.prospect_name, fallback=prospect_id)
    token = _existing_token(manifest_path) or _generate_token()
    private_slug = _compose_private_slug(brand_slug, token)

    # 2. Watermarked preview check.
    preview_src = _resolve_preview_video(prospect_root)

    # 3. Copy assets + preview video into the site/ folder.
    site_brief, assets_used, preview_rel, preview_size = _copy_assets_into_site(
        brief, site_dir, preview_src
    )

    # 4. Render HTML deck with noindex + optional preview embed.
    # The 'public_url' here is the PLANNED URL (where the page will live
    # once deployed). The HTML carries a draft banner that explicitly
    # states the page is not deployed yet, so reviewers do not click an
    # un-deployed link and hit a 404.
    base = resolve_public_base_url(public_base_url)
    planned_public_url = f"{base}/p/{private_slug}/"
    # `intended_status` (from the deploy script's real-deploy path)
    # wins over the existing manifest state, so the first deploy can
    # upload Live-banner HTML without needing a second deploy. When
    # the caller passes nothing, fall back to whatever the existing
    # manifest claims so rebuilds after a successful deploy keep the
    # Live banner across regenerations.
    if intended_status in ("draft", "deployed"):
        render_status = intended_status
    else:
        existing_status = _read_existing_status(manifest_path)
        render_status = existing_status if existing_status in ("draft", "deployed") else "draft"
    out_html = build_html_deck(
        site_brief,
        output_dir=site_dir,
        noindex=True,
        preview_video_url=preview_rel,
        status=render_status,
        public_url=planned_public_url if render_status == "deployed" else None,
        contact_email=resolve_contact_email(contact_email),
        form_endpoint=resolve_form_endpoint(form_endpoint),
        prospect_id=prospect_id,
        private_slug=private_slug,
    )

    # 5. Open-ad links list (one per ad in the brief).
    open_ad_links = [a.library_url for a in brief.ads if a.library_url]

    # 6. Manifest. `created_at` is sticky: if the manifest already
    # carried one, keep it. The token-reuse logic guarantees identity
    # consistency across rebuilds.
    created_at: str
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}
    created_at = existing.get("created_at") or datetime.now(timezone.utc).isoformat()

    public_url = planned_public_url

    manifest: dict = {
        "prospect_id": prospect_id,
        "brand_name": brief.prospect_name,
        "private_slug": private_slug,
        "local_path": str(out_html.resolve()),
        "public_url": public_url,
        "status": "draft",
        "review_required": True,
        "created_at": created_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "assets_used": assets_used,
        "open_ad_links": open_ad_links,
        "website_url": brief.website_url,
    }
    if preview_rel is not None:
        manifest["preview_video_path"] = preview_rel
        manifest["watermarked_video"] = True
        manifest["preview_video_bytes"] = preview_size
        if preview_size and preview_size > CLOUDFLARE_MAX_SAFE_ASSET_BYTES:
            manifest.setdefault("warnings", []).append(
                f"preview_video_bytes={preview_size} exceeds Cloudflare Pages "
                f"safe-asset budget ({CLOUDFLARE_MAX_SAFE_ASSET_BYTES} bytes); "
                "compress before deploying."
            )

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    log.info(
        "microsite_builder: wrote %s (slug=%s, assets=%d, preview=%s)",
        manifest_path, private_slug, len(assets_used), bool(preview_rel),
    )
    return manifest


def build_deploy_package(
    prospect_id: str,
    *,
    prospects_root: Optional[Path] = None,
    build_root: Optional[Path] = None,
) -> Path:
    """Copy `prospects/<id>/site/` into `<build_root>/pitches/p/<slug>/`.

    Reads the existing manifest to discover the private slug - so
    `build_microsite()` must have run at least once for this prospect.
    The deploy folder is upload-ready: drop it into a Cloudflare Pages
    project (or any static host) and the prospect link works.

    When a sibling `prospects/<id>/strategy/` folder exists (written by
    `build_strategy_microsite()`), it is copied into the package as
    `<slug>/strategy/`, so the deployed Cloudflare URL serves both:

        https://<host>/p/<slug>/            -> pitch microsite
        https://<host>/p/<slug>/strategy/   -> strategy page

    Returns the path to the generated deploy folder.
    """
    root = Path(prospects_root) if prospects_root else Path("prospects")
    site_dir = (root / prospect_id / "site").resolve()
    manifest_path = site_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"build_deploy_package: {manifest_path} missing - run "
            "build_microsite() first."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    private_slug = manifest.get("private_slug")
    if not private_slug:
        raise ValueError("build_deploy_package: manifest has no private_slug")

    build_base = Path(build_root) if build_root else Path("build")
    target = (build_base / "pitches" / "p" / private_slug).resolve()
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(site_dir, target)

    # Strategy page is optional: only copy if the operator has run
    # `build_strategy_microsite()` for this prospect. Lives at
    # `<slug>/strategy/` so the pitch and strategy URLs share the same
    # private-slug namespace but live at different paths.
    strategy_dir = (root / prospect_id / "strategy").resolve()
    if strategy_dir.is_dir():
        shutil.copytree(strategy_dir, target / "strategy")
        log.info(
            "microsite_builder: strategy page packaged at %s/strategy/",
            target,
        )

    log.info("microsite_builder: deploy package ready at %s", target)
    return target


# --------------------------------------------------------------------------- #
# Strategy page builder (sibling of the pitch microsite)
# --------------------------------------------------------------------------- #


# These imports are deliberately lazy at module-level - they pull in the
# strategy renderer + brief layer only when the operator actually builds
# a strategy page, keeping `build_microsite()`'s import graph tight.
from agents.outreach.reporting.html_strategy_builder import build_strategy_html  # noqa: E402
from agents.outreach.reporting.strategy_brief import StrategyBrief  # noqa: E402


def _gather_strategy_asset_paths(brief: StrategyBrief) -> list[Path]:
    """All asset Paths the strategy page actually references, deduped,
    in order. Used by `_copy_strategy_assets()` to populate the local
    `strategy/assets/` folder so the page is self-contained when
    served from any path."""
    seen: set[Path] = set()
    out: list[Path] = []

    def _push(p: Optional[Path]) -> None:
        if p is None or p in seen:
            return
        try:
            if not p.is_file():
                return
        except OSError:
            return
        seen.add(p)
        out.append(p)

    _push(brief.logo_path)
    _push(brief.hero_image_path)
    for p in brief.product_images:
        _push(p)
    for ad in brief.ad_patterns:
        _push(ad.screenshot_path)
    # Competitor ad proof - screenshots referenced by section 04
    # competitor cards AND by section 05 pattern cards.
    for comp in brief.competitors:
        for sampled_ad in comp.sampled_ads:
            _push(sampled_ad.screenshot_path)
    for pat in brief.creative_patterns:
        for ev_ad in pat.ad_evidence:
            _push(ev_ad.screenshot_path)
    return out


def _copy_strategy_assets(
    brief: StrategyBrief,
    strategy_dir: Path,
) -> StrategyBrief:
    """Copy every asset the strategy page references into
    `<strategy_dir>/assets/` and return a brief whose Paths point at
    the copies. Keeps the strategy page self-contained for the deploy
    package - paths inside the rendered HTML become `assets/<name>`.
    """
    from dataclasses import replace

    from agents.outreach.reporting.strategy_brief import (
        CompetitorAdProof,
        CreativePattern,
        StrategyAdPattern,
    )

    assets_dir = strategy_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    mapping: dict[Path, Path] = {}
    used_names: set[str] = set()
    for src in _gather_strategy_asset_paths(brief):
        base = _safe_assets_name(src)
        candidate = base
        i = 1
        while candidate in used_names:
            stem, ext = os.path.splitext(base)
            candidate = f"{stem}_{i}{ext}"
            i += 1
        dst = assets_dir / candidate
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            log.warning(
                "microsite_builder: failed to copy strategy asset %s -> %s: %s",
                src, dst, e,
            )
            continue
        mapping[src] = dst
        used_names.add(candidate)

    def remap(p: Optional[Path]) -> Optional[Path]:
        return mapping.get(p, p) if p is not None else None

    def remap_ad(ad: CompetitorAdProof) -> CompetitorAdProof:
        return replace(ad, screenshot_path=remap(ad.screenshot_path))

    new_ad_patterns = [
        StrategyAdPattern(
            archive_id=p.archive_id,
            pattern=p.pattern,
            weakness=p.weakness,
            opportunity=p.opportunity,
            library_url=p.library_url,
            screenshot_path=remap(p.screenshot_path),
            body_excerpt=p.body_excerpt,
            days_active=p.days_active,
        )
        for p in brief.ad_patterns
    ]

    new_competitors = [
        replace(
            c,
            sampled_ads=tuple(remap_ad(ad) for ad in c.sampled_ads),
        )
        for c in brief.competitors
    ]
    # The CreativePattern.ad_evidence list references the SAME
    # CompetitorAdProof objects we just remapped on the competitors
    # list - but `frozen=True` means we can't rely on identity to
    # share them. Re-derive by competitor-name + ad_archive_id so
    # both sides see the file in `strategy/assets/`.
    _remapped_by_key: dict[tuple[str, str], CompetitorAdProof] = {}
    for c in new_competitors:
        for ad in c.sampled_ads:
            _remapped_by_key[(c.name, ad.ad_archive_id)] = ad

    new_creative_patterns: list[CreativePattern] = []
    for pat in brief.creative_patterns:
        new_evidence: list[CompetitorAdProof] = []
        for ad in pat.ad_evidence:
            key = (ad.competitor_name, ad.ad_archive_id)
            new_evidence.append(_remapped_by_key.get(key, remap_ad(ad)))
        new_creative_patterns.append(replace(pat, ad_evidence=tuple(new_evidence)))

    return replace(
        brief,
        logo_path=remap(brief.logo_path),
        hero_image_path=remap(brief.hero_image_path),
        product_images=[remap(p) for p in brief.product_images if p is not None],
        ad_patterns=new_ad_patterns,
        competitors=new_competitors,
        creative_patterns=new_creative_patterns,
        prospect_root=strategy_dir,  # so the renderer's `_url_for` walks the right tree
    )


def build_strategy_microsite(
    prospect_id: str,
    *,
    prospects_root: Optional[Path] = None,
    public_base_url: Optional[str] = None,
    public_pitch_url: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> dict:
    """Build the private strategy / creative-research page for
    `prospect_id` and return a small manifest.

    Steps:
      1. Build a StrategyBrief from the prospect's audit.json.
      2. Resolve the private slug from the existing pitch
         `site/manifest.json` (the strategy page deploys to
         `<slug>/strategy/`, so we want both surfaces to share the
         same slug).
      3. Copy every used asset into `<strategy_dir>/assets/` so the
         page is self-contained.
      4. Render the strategy HTML.
      5. Write a small `manifest.json` next to it.

    Returns the manifest dict (also persisted at
    `prospects/<id>/strategy/manifest.json`).
    """
    root = Path(prospects_root) if prospects_root else Path("prospects")
    prospect_root = (root / prospect_id).resolve()

    # Pitch manifest tells us the slug + public URL base; if the pitch
    # microsite hasn't been built yet, we can still write the strategy
    # page locally - we just don't have a private_slug to record.
    pitch_manifest_path = prospect_root / "site" / "manifest.json"
    pitch_manifest: dict = {}
    if pitch_manifest_path.exists():
        try:
            pitch_manifest = json.loads(
                pitch_manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            pitch_manifest = {}
    private_slug = pitch_manifest.get("private_slug")
    pitch_public_url = pitch_manifest.get("public_url")

    # Resolve target public URL for the strategy page.
    base = resolve_public_base_url(public_base_url)
    strategy_public_url: Optional[str] = None
    if private_slug:
        strategy_public_url = f"{base}/p/{private_slug}/strategy/"

    # Build the brief.
    brief = StrategyBrief.from_audit(
        prospect_id,
        prospects_root=prospects_root,
        public_pitch_url=public_pitch_url or pitch_public_url or "../",
    )

    # Decide output dir + copy assets locally for self-containment.
    strategy_dir = Path(output_dir) if output_dir else (prospect_root / "strategy")
    strategy_dir.mkdir(parents=True, exist_ok=True)
    site_brief = _copy_strategy_assets(brief, strategy_dir)

    # Render the HTML.
    out_html = build_strategy_html(
        site_brief,
        output_dir=strategy_dir,
        noindex=True,
        status="draft",
        public_url=strategy_public_url,
    )

    # Manifest.
    manifest: dict = {
        "prospect_id": prospect_id,
        "brand_name": brief.brand_name,
        "private_slug": private_slug,
        "kind": "strategy",
        "local_path": str(out_html.resolve()),
        "public_url": strategy_public_url,
        "status": "draft",
        "review_required": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = strategy_dir / "manifest.json"
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("created_at"):
                manifest["created_at"] = existing["created_at"]
        except (OSError, json.JSONDecodeError):
            pass
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    log.info(
        "microsite_builder: strategy page written to %s (slug=%s)",
        manifest_path, private_slug,
    )
    return manifest


# --------------------------------------------------------------------------- #
# CLI helper (manual ergonomics; not auto-wired into anything)
# --------------------------------------------------------------------------- #


def _main(argv: Optional[list[str]] = None) -> int:
    """`python -m agents.outreach.reporting.microsite_builder <prospect_id>`."""
    import argparse
    ap = argparse.ArgumentParser(
        description="Build a private microsite for an audited prospect."
    )
    ap.add_argument("prospect_id")
    ap.add_argument("--prospects-root", type=Path, default=None)
    ap.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    ap.add_argument(
        "--deploy",
        action="store_true",
        help="Also copy site/ into build/pitches/p/<slug>/ for upload.",
    )
    args = ap.parse_args(argv)
    manifest = build_microsite(
        args.prospect_id,
        prospects_root=args.prospects_root,
        public_base_url=args.public_base_url,
    )
    print(f"private_slug:  {manifest['private_slug']}")
    print(f"public_url:    {manifest['public_url']}")
    print(f"local_path:    {manifest['local_path']}")
    if args.deploy:
        deploy = build_deploy_package(
            args.prospect_id, prospects_root=args.prospects_root
        )
        print(f"deploy_dir:    {deploy}")
    # Suppress lint warning on the unused asdict import while keeping the
    # dependency obvious for future contributors who want to dump a brief.
    _ = asdict
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
