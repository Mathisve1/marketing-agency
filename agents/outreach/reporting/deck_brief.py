"""Deck brief layer - structured intermediate between audit data and the deck builder.

The HTML deck builder should NOT have to invent content from raw audit JSON.
It receives a fully-shaped `DeckBrief` describing every slide:

  - which assets to use (already resolved to absolute Paths, with uniqueness
    tracking so no image appears twice in the deck)
  - the cover headline + sub-head
  - the four 45-second-version cards
  - per-ad proof rows (with cleaned copy, public library URL, suggested route)
  - the creative gap-map rows
  - the four concept routes (niche-aware)
  - the standard process steps + pricing tiers
  - the close-out CTA copy

The brief is a pure dataclass tree - no I/O, no rendering. Tests can build
synthetic briefs without touching the filesystem. `DeckBrief.from_audit(...)`
is the one place that touches an audit.json on disk; everything else is
pure.

Reuses helpers from `pitch_builder` (sanitisation, niche-profile concept
synthesis, ad classification) so the HTML deck stays consistent with the
fpdf fallback rather than re-deriving everything from scratch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from agents.outreach.prospect_store import ProspectAudit, ProspectStore
from agents.outreach.reporting.pitch_builder import (
    _category_headline,
    _classify_ad_issue,
    _clean_body_excerpt,
    _concept_pack,
    _niche_profile,
    _niche_short,
    _parse_hex_color,
    _parse_weakness,
    _resolve_asset_path,
    _sanitize,
    _strip_debug_inline,
)

# --------------------------------------------------------------------------- #
# Sub-types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AdProof:
    """One row on the 'From live ad to video route' slide."""
    archive_id: str
    issue_label: str          # 'PLACEHOLDER COPY' / 'DUPLICATE COPY' / 'ACTIVE' ...
    issue_explainer: str      # one-line diagnosis of what's wrong
    body_excerpt: Optional[str]
    cta_text: Optional[str]
    days_active: Optional[int]
    library_url: str          # always present, derived from archive_id if needed
    screenshot_path: Optional[Path]  # absolute, exists; None when no capture
    suggested_route: str      # "What we'd test instead" copy


@dataclass(frozen=True)
class ConceptRoute:
    """One card on the Concept Board slide."""
    label: str                # "Route A", "Route B", ...
    title: str
    hook: str
    cta: str
    visual_path: Optional[Path]  # absolute image to use as the phone-mockup still


@dataclass(frozen=True)
class GapMapRow:
    """One row on the Creative Gap Map slide."""
    current_pattern: str
    why_it_limits_growth: str
    ugc_test: str
    confidence: str           # 'high' / 'medium' / 'low' / ''


@dataclass(frozen=True)
class PricingTier:
    """One card on the Pricing slide."""
    name: str
    price: str                # already formatted, e.g. '£90'
    tagline: str
    bullets: list[str]
    is_recommended: bool = False


@dataclass(frozen=True)
class ProcessStep:
    """One step on the 'How this works' slide."""
    number: str               # "01", "02", ...
    label: str
    description: str


@dataclass(frozen=True)
class FortyFiveSecondCard:
    """One card on the 45-second-version slide."""
    label: str
    body: str


# --------------------------------------------------------------------------- #
# Top-level brief
# --------------------------------------------------------------------------- #


@dataclass
class DeckBrief:
    """Everything the HTML deck builder needs to render a prospect deck.

    Use `DeckBrief.from_audit(prospect_id, prospects_root=...)` for the
    common path - reads audit.json, resolves brand-profile assets, and
    builds every slide-level content list. Tests can also construct
    DeckBriefs directly with synthetic field values.
    """
    # Identity
    prospect_name: str
    niche: str
    agency_name: str = "Yuvo Studio"

    # Brand context
    website_url: Optional[str] = None
    facebook_url: Optional[str] = None
    instagram_url: Optional[str] = None
    brand_tone: Optional[str] = None
    product_category: Optional[str] = None
    audience_assumption: Optional[str] = None

    # Visual identity
    primary_color: str = "#1A1A1A"
    secondary_color: Optional[str] = None
    accent_text_color: str = "#FFFFFF"  # auto-computed contrast for primary
    logo_path: Optional[Path] = None
    hero_image_path: Optional[Path] = None
    product_images: list[Path] = field(default_factory=list)

    # Cover messaging
    cover_headline: str = ""
    cover_subhead: str = ""
    cover_kicker: str = "Private Creative Note"

    # Slide content
    forty_five_second_cards: list[FortyFiveSecondCard] = field(default_factory=list)
    ads: list[AdProof] = field(default_factory=list)
    gap_map_rows: list[GapMapRow] = field(default_factory=list)
    concepts: list[ConceptRoute] = field(default_factory=list)
    process_steps: list[ProcessStep] = field(default_factory=list)
    pricing: list[PricingTier] = field(default_factory=list)
    cta_headline: str = ""
    cta_body: str = ""

    # Source data (kept for the builder to construct correct links)
    prospect_root: Optional[Path] = None

    # ----- constructors ----- #

    @classmethod
    def from_audit(
        cls,
        prospect_id: str,
        *,
        prospects_root: Optional[Path] = None,
        agency_name: str = "Yuvo Studio",
    ) -> "DeckBrief":
        """Build a brief from a prospect's audit.json on disk.

        Resolves brand-profile asset paths to absolute Paths that exist;
        missing/remote/oversized assets are silently dropped. The single
        side-effect-bearing constructor; tests can side-step it by
        constructing a DeckBrief directly.
        """
        store = ProspectStore(prospect_id, prospects_root=prospects_root)
        audit = store.read_audit()
        if audit is None:
            raise FileNotFoundError(
                f"Prospect {prospect_id!r} has no audit.json under "
                f"{store.root}"
            )
        return cls.from_objects(
            audit=audit,
            prospect_root=store.root,
            agency_name=agency_name,
        )

    @classmethod
    def from_objects(
        cls,
        *,
        audit: ProspectAudit,
        prospect_root: Optional[Path],
        agency_name: str = "Yuvo Studio",
    ) -> "DeckBrief":
        """Build a brief from already-loaded audit + prospect_root.

        Pure helper used by `from_audit` and tests that already hold a
        ProspectAudit in memory.
        """
        bp = audit.brand_profile or {}
        prospect_name = audit.prospect_name or "Brand"
        niche = audit.niche or "your category"

        primary_color = _normalize_color(bp.get("primary_color")) or "#1A1A1A"
        secondary_color = _normalize_color(bp.get("secondary_color"))
        accent_text = _contrast_text_color(primary_color)

        # Asset resolution + uniqueness budget.
        used_paths: set[Path] = set()

        # Logo: looser file-size threshold (32x32 favicons are ~2-3 KB)
        # plus a circle-safe check so a wide wordmark export never lands
        # inside the small circular brand badge. When the brand has
        # supplied a logo that fails the circle-safe check, the
        # downstream renderer falls back to text initials - cleaner
        # than a chopped-up wordmark.
        logo = _resolve_asset_path(bp.get("logo_path"), prospect_root)
        if not _is_useful_logo(logo):
            logo = None
        if logo is not None and not _logo_is_circle_safe(logo):
            logo = None
        if logo:
            used_paths.add(logo)

        hero = _resolve_asset_path(
            bp.get("hero_image_path")
            or bp.get("homepage_screenshot_path")
            or bp.get("website_screenshot_path"),
            prospect_root,
        )
        if not _is_useful_image(hero) or (hero is not None and hero in used_paths):
            hero = None
        # Reject a logo-shaped image as the hero - a 32x32 favicon on a
        # full-bleed hero background looks like a missing asset. Better to
        # let the hero fall through to the designed mock.
        if hero is not None and _looks_like_logo_path(hero):
            hero = None
        if hero:
            used_paths.add(hero)

        # Product images are NOT added to `used_paths` at collection
        # time - that pre-claim caused every concept card to fall back
        # to the empty phone mock (the concept builder's "not in
        # used_paths" filter rejected all candidates). Instead, only
        # the path actually consumed by a concept (or by the hero
        # fallback) gets recorded in `used_paths`, so the asset can't
        # be embedded twice across the deck. Within product_images
        # itself we still dedupe so a brand_profile that lists the
        # same URL twice doesn't double-render.
        #
        # V4: also filter out logo-shaped filenames. The brand-asset
        # collector occasionally relabels a second logo file as a
        # "product" image (when logo_path is already set), which then
        # ends up rendered full-bleed on a concept card. That looks
        # weak. When a path is logo-shaped AND the deck has no logo
        # yet, we promote it to logo_path; otherwise we drop it from
        # product_images entirely.
        product_images: list[Path] = []
        product_seen: set[Path] = set()
        for raw in bp.get("product_images") or []:
            p = _resolve_asset_path(raw, prospect_root)
            if not _is_useful_image(p):
                continue
            if p in used_paths:
                # Logo/hero already used this path - skip so we don't
                # surface the same file in the concept rail.
                continue
            if p in product_seen:
                continue
            if _looks_like_logo_path(p):
                # Logo-shaped files dumped into product_images are
                # almost always full-bleed wordmark exports (the YANA
                # 400x457 'YANA_Logo_black' case). They don't crop
                # cleanly into the circular brand badge AND they
                # destroy concept frames when rendered full-bleed.
                # Drop them entirely - operator can configure
                # logo_path explicitly when a real circle-safe logo
                # exists.
                continue
            product_images.append(p)
            product_seen.add(p)

        # Cover headline: niche-aware, brand-led.
        cover_headline = _build_cover_headline(prospect_name, niche)
        cover_subhead = _build_cover_subhead(prospect_name, niche)

        # 45-second cards (4 of them) - derived from weaknesses + niche.
        parsed_weaknesses = [_parse_weakness(w) for w in (audit.weaknesses or [])]
        forty_five = _build_forty_five_second(
            prospect_name=prospect_name,
            niche=niche,
            weaknesses=parsed_weaknesses,
        )

        # Per-ad proof rows.
        ads = _build_ad_proofs(
            audit_ads=audit.competitor_ads or [],
            prospect_root=prospect_root,
            used_paths=used_paths,
            niche=niche,
        )

        # Gap-map rows.
        gap_map_rows = _build_gap_map(weaknesses=parsed_weaknesses, niche=niche)

        # Concepts (one product image per concept, max).
        concepts = _build_concepts(
            prospect_name=prospect_name,
            niche=niche,
            product_images=product_images,
            used_paths=used_paths,
        )

        process_steps = _build_process_steps()
        pricing = _build_pricing(prospect_name=prospect_name)
        cta_headline, cta_body = _build_cta(prospect_name=prospect_name)

        return cls(
            prospect_name=prospect_name,
            niche=niche,
            agency_name=agency_name,
            website_url=_clean_url(bp.get("website_url")),
            facebook_url=_clean_url(bp.get("facebook_url")),
            instagram_url=_clean_url(bp.get("instagram_url")),
            brand_tone=_sanitize(bp.get("brand_tone") or "") or None,
            product_category=_sanitize(bp.get("product_category") or "") or None,
            audience_assumption=_sanitize(bp.get("audience_assumption") or "") or None,
            primary_color=primary_color,
            secondary_color=secondary_color,
            accent_text_color=accent_text,
            logo_path=logo,
            hero_image_path=hero,
            product_images=product_images,
            cover_headline=cover_headline,
            cover_subhead=cover_subhead,
            forty_five_second_cards=forty_five,
            ads=ads,
            gap_map_rows=gap_map_rows,
            concepts=concepts,
            process_steps=process_steps,
            pricing=pricing,
            cta_headline=cta_headline,
            cta_body=cta_body,
            prospect_root=prospect_root,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _normalize_color(raw: Any) -> Optional[str]:
    """Return '#RRGGBB' (always 7 chars, uppercase hex) or None."""
    rgb = _parse_hex_color(raw if isinstance(raw, str) else None)
    if rgb is None:
        return None
    return "#" + "".join(f"{c:02X}" for c in rgb)


def _contrast_text_color(hex_color: str) -> str:
    """Pick a readable text colour for a coloured cover band.

    Uses the classic perceived-brightness formula (Rec.601 weights);
    returns '#0A0A0A' for light backgrounds and '#FFFFFF' for dark ones.
    """
    rgb = _parse_hex_color(hex_color)
    if rgb is None:
        return "#FFFFFF"
    r, g, b = rgb
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return "#0A0A0A" if brightness > 160 else "#FFFFFF"


def _is_useful_image(path: Optional[Path]) -> bool:
    """Filter out images that are too small to be visually useful.

    16x16 favicons live in the assets folder; we don't want them sneaking
    onto the cover. Anything under 4 KB is almost certainly a thumbnail
    or tracking pixel.
    """
    if path is None:
        return False
    suffix = path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 4_000:
        return False
    return True


def _is_useful_logo(path: Optional[Path]) -> bool:
    """Logo / brand-mark variant of `_is_useful_image`.

    Logos sit inside a small (~76 px) circular badge in the topbar.
    A real 32x32 favicon export is often only 2-3 KB - well under the
    4 KB threshold we use for hero/concept photos - but it still
    renders beautifully in that badge. The bar for logos is just:
    a real PNG/JPG/etc with at least a few hundred bytes of payload.
    """
    if path is None:
        return False
    suffix = path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return size >= 500


def _logo_is_circle_safe(path: Optional[Path]) -> bool:
    """True when a logo asset is safe to embed in a small circular
    brand badge without disastrous cropping.

    Rules:
      * approximately square (aspect ratio <= 1.4) so a horizontal
        wordmark like 'YANA Active' doesn't get cropped into 4
        unreadable letters by object-fit: cover.
      * source dimensions <= 256 px on the long edge. Anything bigger
        is almost always a full-bleed wordmark / lockup export
        intended for header strips, not a circular badge.
      * we fall back to True when Pillow isn't importable - the
        downstream renderer's text-initials fallback is the only
        safety net in that environment, and we don't want a missing
        Pillow to silently strip legitimate small favicons from
        every deck.
    """
    if path is None:
        return False
    try:
        from PIL import Image  # noqa: PLC0415
    except Exception:
        # Pillow missing: trust the file. The HTML side can still
        # decide to render text initials instead via its own check.
        return True
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return False
    if w <= 0 or h <= 0:
        return False
    long_edge = max(w, h)
    short_edge = min(w, h)
    if long_edge > 256:
        return False
    if short_edge == 0 or long_edge / short_edge > 1.4:
        return False
    return True


# Tokens that, when present in a filename, mark the file as a brand mark
# rather than a product / lifestyle photograph. We keep the list tight on
# purpose - false positives in either direction (logo->concept, product->
# logo) hurt visual quality more than the strict heuristic could.
_LOGO_FILENAME_TOKENS = (
    "logo",
    "wordmark",
    "word-mark",
    "favicon",
    "brandmark",
    "brand-mark",
    "apple-touch",
    "icon-",
    "_icon",
    "site-icon",
)


def _looks_like_logo_path(path: Optional[Path]) -> bool:
    """True when a Path's filename matches our logo/brand-mark heuristic.

    This is the single source of truth used by:
      * `_select_product_images()` - to keep logo files out of the
        concept rail's main visual.
      * Hero selection - to avoid hero panels that show a brand-mark on
        a dark background instead of a real photograph.

    The check is filename-based only. Image-dimension probing (favicons
    are typically 16-256px square, real logos can be larger) would add
    a Pillow open per asset on every deck build; the filename hint
    catches the cases that actually hurt in practice (Shopify/Squarespace
    auto-export filenames almost always carry 'logo' / 'favicon' /
    'wordmark' in the path).
    """
    if path is None:
        return False
    name = path.name.lower()
    return any(tok in name for tok in _LOGO_FILENAME_TOKENS)


def _is_likely_logo_file(path: Optional[Path]) -> bool:
    """Combined heuristic: filename token AND very small file size (a real
    full-bleed photo is typically >50 KB; a vector logo PNG export is
    usually well under that). The filename check by itself is sometimes
    overzealous - this combined gate is the conservative form used when
    we have to decide whether to PROMOTE a stray asset to logo_path.
    """
    if not _looks_like_logo_path(path):
        return False
    try:
        size = path.stat().st_size  # type: ignore[union-attr]
    except OSError:
        return True
    # Logos are usually flat vectors exported small; > 1 MB is almost
    # certainly a hero photo with the word "logo" mistakenly in its name.
    return size < 1_000_000


def _clean_url(raw: Any) -> Optional[str]:
    """Return a clean http(s) URL or None."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if not s.lower().startswith(("http://", "https://")):
        return None
    return s


def _short_name(name: str) -> str:
    """Up to 24 chars, useful for cover-headline templates."""
    return (name or "your brand").strip()[:24] or "your brand"


def _build_cover_headline(prospect_name: str, niche: str) -> str:
    """Brand-first, niche-aware cover headline.

    Reads as a one-line creative-note opener that puts the prospect (not
    the agency) at the centre. Example for activewear:
      'A sharper video ad route for YANA Active.'
    """
    base = _category_headline(niche)
    return f"{base} for {_short_name(prospect_name)}."


def _build_cover_subhead(prospect_name: str, niche: str) -> str:
    short = _niche_short(niche or "")
    return (
        f"A short, private creative note on what better short-form video "
        f"could do for {_short_name(prospect_name)} in the {short} space."
    )


def _build_forty_five_second(
    *,
    prospect_name: str,
    niche: str,
    weaknesses: Sequence[dict],
) -> list[FortyFiveSecondCard]:
    """Four cards: what we saw / what we'd make / what it costs / why it's low risk.

    'What we saw' draws from the highest-confidence weakness description so
    the diagnosis carries real teeth instead of generic copy.
    """
    name = _short_name(prospect_name)
    _ = niche  # reserved for niche-specific tone polishing later

    # Pull the most-confident weakness as the diagnosis seed.
    sorted_w = sorted(
        weaknesses,
        key=lambda w: {"high": 0, "medium": 1, "low": 2, "": 3}.get(w.get("confidence", ""), 3),
    )
    diagnosis: str
    if sorted_w:
        first = sorted_w[0].get("description") or ""
        diagnosis = _strip_debug_inline(first)[:240]
    else:
        diagnosis = (
            f"{name}'s active library leans on one tone of voice and one "
            f"creative angle - growth is bottlenecked at the hook."
        )

    return [
        FortyFiveSecondCard(
            label="What we saw",
            body=diagnosis,
        ),
        FortyFiveSecondCard(
            label="What we'd make",
            body=(
                "Three short-form video routes built in your brand world - hook, "
                "product proof, founder anchor - cut from assets you already own."
            ),
        ),
        FortyFiveSecondCard(
            label="What it costs to try",
            body=(
                "GBP 90 for one finished route. GBP 260 for the starter trio: "
                "three openings against one product, fixed price, pay per cut."
            ),
        ),
        FortyFiveSecondCard(
            label="How we'd start",
            body=(
                f"We'd pick the strongest opening for {name} and ship one "
                f"finished cut against your current best ad. You keep every "
                f"original asset, every edit decision, every source file."
            ),
        ),
    ]


def _build_ad_proofs(
    *,
    audit_ads: Sequence[dict],
    prospect_root: Optional[Path],
    used_paths: set[Path],
    niche: str,
) -> list[AdProof]:
    """Build at most 4 AdProof rows from sampled competitor_ads."""
    if not audit_ads:
        return []
    ads_list = list(audit_ads)[:4]
    profile = _niche_profile(niche)

    out: list[AdProof] = []
    for ad in ads_list:
        archive_id = str(ad.get("ad_archive_id") or "").strip()
        if not archive_id:
            continue
        issue_label, issue_explainer = _classify_ad_issue(ad, ads_list)
        body_excerpt = _clean_body_excerpt(ad.get("body_text"))
        cta_text = (ad.get("cta_text") or "").strip() or None

        days_raw = ad.get("days_active")
        days_active = int(days_raw) if isinstance(days_raw, (int, float)) and days_raw > 0 else None

        library_url = _ad_library_url(ad, archive_id)
        screenshot = _resolve_asset_path(ad.get("ad_screenshot_path"), prospect_root)
        if screenshot is not None and screenshot in used_paths:
            screenshot = None
        if not _is_useful_image(screenshot):
            screenshot = None
        if screenshot:
            used_paths.add(screenshot)

        out.append(
            AdProof(
                archive_id=archive_id,
                issue_label=issue_label,
                issue_explainer=_sanitize(issue_explainer),
                body_excerpt=body_excerpt,
                cta_text=cta_text,
                days_active=days_active,
                library_url=library_url,
                screenshot_path=screenshot,
                suggested_route=_suggested_route_for_issue(issue_label, profile),
            )
        )
    return out


def _ad_library_url(ad: dict, archive_id: str) -> str:
    """Always-present public Meta Ads Library URL for an ad."""
    raw = ad.get("ad_library_url") or ad.get("snapshot_url")
    if isinstance(raw, str) and raw.lower().startswith(("http://", "https://")):
        return raw.strip()
    return f"https://www.facebook.com/ads/library/?id={archive_id}"


def _suggested_route_for_issue(issue_label: str, profile: str) -> str:
    """One-line UGC route to test against a known ad problem."""
    table = {
        "PLACEHOLDER COPY": "A founder-voiced 9:16 with a hand-held product close and one human sentence about what it is.",
        "DUPLICATE COPY": "Same product, three openings - swap only the first three seconds and let the rest of the cut run identical.",
        "NO COPY CAPTURED": "Open with one spoken line - the problem we solve - then cut to product proof at 03 seconds.",
        "NO CTA CAPTURED": "Hold a one-line CTA on screen for the last two seconds, paired with the spoken version.",
        "LONG-RUNNING SIGNAL": "Borrow the hook structure that's already earned its run; freshen the visual with UGC.",
        "ACTIVE": "Test a UGC re-cut of the same offer against the existing ad - same audience, different first frames.",
    }
    return table.get(issue_label, table["ACTIVE"])


def _build_gap_map(*, weaknesses: Sequence[dict], niche: str) -> list[GapMapRow]:
    """Three columns: current pattern, why it limits growth, UGC test to run.

    Each weakness becomes one row. We keep at most 4 rows so the slide
    stays scannable.
    """
    rows: list[GapMapRow] = []
    profile = _niche_profile(niche)
    for w in (weaknesses or [])[:4]:
        desc = _strip_debug_inline(w.get("description") or "")
        if not desc:
            continue
        confidence = w.get("confidence") or ""
        rows.append(
            GapMapRow(
                current_pattern=desc[:200],
                why_it_limits_growth=_diagnosis_for_weakness(desc, profile),
                ugc_test=_remedy_for_weakness(desc, profile),
                confidence=confidence,
            )
        )
    return rows


def _diagnosis_for_weakness(desc: str, profile: str) -> str:
    """Map a weakness description to a one-line 'why it limits growth'."""
    lower = desc.lower()
    if "video" in lower:
        return "Static creative caps scroll-stop on cold audiences. Video earns watch-time and gives the algorithm signal."
    if "cta" in lower or "call to action" in lower:
        return "Without a clear CTA the audience does not know which next step is the cheap one."
    if "duplicate" in lower or "single copy" in lower or "single angle" in lower:
        return "One angle reaches one slice of the audience. Variants unlock new buyer mindsets cheaply."
    if "placeholder" in lower or "{{" in lower:
        return "Catalogue-templated copy reads as unfinished and erodes brand trust before the first click."
    if "persona" in lower:
        return "Lifestyle-broad copy makes every viewer translate the message themselves - most do not."
    if profile == "skincare":
        return "Skincare buyers want sensorial proof and routine context - missing both flattens conversion."
    return "This pattern caps creative efficiency before paid spend can do its job."


def _remedy_for_weakness(desc: str, profile: str) -> str:
    """Map a weakness description to a one-line UGC test recommendation."""
    lower = desc.lower()
    if "video" in lower:
        return "Two 12-15s founder/UGC cuts shot against existing brand imagery."
    if "cta" in lower or "call to action" in lower:
        return "Add a held-text + voice CTA in the final 2s of each new cut."
    if "duplicate" in lower or "single copy" in lower or "single angle" in lower:
        return "Three openings on the same product - keep the body cut identical."
    if "placeholder" in lower or "{{" in lower:
        return "Replace placeholder copy with one human sentence under each product."
    if "persona" in lower:
        return "Pick one buyer per route: write the first line for them only."
    if profile == "skincare":
        return "One sensorial close-up route + one routine-context route, 12s each."
    return "One short-form UGC route per pattern, tested against the current ads."


def _build_concepts(
    *,
    prospect_name: str,
    niche: str,
    product_images: Sequence[Path],
    used_paths: set[Path],
) -> list[ConceptRoute]:
    """Build up to 4 concept routes, each with at most one product image.

    `product_images` is the already-deduped list from the audit's
    brand_profile (callers must NOT pre-add these paths to
    `used_paths` - doing so caused every concept to fall back to the
    empty phone mock).

    Image assignment policy:
      * Concept 0 gets product_images[0] when available.
      * Concept 1 gets product_images[1] when available.
      * ...etc, up to len(product_images).
      * Concepts past the image count get `visual_path=None` and
        render the designed-fallback frame (premium gradient + brand
        monogram).
      * Each assigned path is added to `used_paths` so the HTML
        renderer's deck-wide dedup check sees them and never double-
        embeds the same file across the deck.
    """
    base = _concept_pack(prospect_name, niche)
    out: list[ConceptRoute] = []
    for idx, c in enumerate(base[:4]):
        label = f"Route {chr(ord('A') + idx)}"
        visual: Optional[Path] = None
        if idx < len(product_images):
            visual = product_images[idx]
            used_paths.add(visual)
        out.append(
            ConceptRoute(
                label=label,
                title=_sanitize(c.get("title") or ""),
                hook=_sanitize(c.get("hook") or ""),
                cta=_sanitize(c.get("cta") or "SHOP NOW"),
                visual_path=visual,
            )
        )
    return out


def _build_process_steps() -> list[ProcessStep]:
    """Seven-step 'how this works' diagram. Static across prospects."""
    return [
        ProcessStep("01", "No shoot day", "We work from imagery, video and brand collateral you already own."),
        ProcessStep("02", "Brand inputs", "30 minutes on your tone, do-not-say list, and current paid creative."),
        ProcessStep("03", "Scene routes", "Three short-form routes drafted - hook, body, CTA, on one page."),
        ProcessStep("04", "Asset assembly", "We cut, grade, sound-design and caption in your brand world."),
        ProcessStep("05", "Two-round revision", "One round of structural notes, one round of polish. Then we ship."),
        ProcessStep("06", "Launch and learn", "Cuts go live against your current best ad - same audience, different opening."),
        ProcessStep("07", "Brand safety", "We never invent product claims. You hold every original file."),
    ]


def _build_pricing(*, prospect_name: str) -> list[PricingTier]:
    """Three pricing tiers - £90 single, £260 starter trio, £499 growth pack."""
    name = _short_name(prospect_name)
    return [
        PricingTier(
            name="Single video",
            price="£90",
            tagline="Try one route against your strongest ad.",
            bullets=[
                "One 12-15s short-form cut",
                "Two rounds of revisions",
                "Paid-social-ready 9:16 + 1:1",
            ],
        ),
        PricingTier(
            name="Starter trio",
            price="£260",
            tagline="Three openings against one product.",
            bullets=[
                "Three 12-15s cuts, one body, three hooks",
                "Two rounds of revisions",
                f"Tested side-by-side with {name}'s current best ad",
                "Per-cut delivery in 9:16 and 1:1",
            ],
            is_recommended=True,
        ),
        PricingTier(
            name="Growth pack",
            price="£499",
            tagline="Build a tested library, not a one-off.",
            bullets=[
                "Six 12-15s cuts across two products",
                "Hook / body / CTA variant matrix",
                "Performance-aware re-cuts on the strongest opener",
                "All cuts delivered in 9:16 and 1:1",
            ],
        ),
    ]


def _build_cta(*, prospect_name: str) -> tuple[str, str]:
    """Headline + body for the closing slide."""
    name = _short_name(prospect_name)
    headline = f"Want to see one {name} video?"
    body = (
        "Reply with 'send the route' and we will draft one short-form cut "
        "against your strongest current ad. Fixed price, two-round "
        "revisions, original files yours."
    )
    return headline, body
