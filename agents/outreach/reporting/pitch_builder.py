"""Yuvo Studio - Private Creative Note (V4) pitch deck generator.

V4 ("UGC Opportunity") replaces the V3 "Creative Growth Audit" report
with a warmer, more visual outreach deck that reads like a bespoke
creative note rather than an AI-generated audit. The structure mirrors
the editorial reference deck the team approved: black editorial pages,
cream cards, brand wordmark as a quiet visual anchor, large
prospect-first headlines, and an opinionated 7-8 page flow.

Deck flow (7-8 pages, dynamic per prospect):

  1. Private creative note cover   - black page, vertical brand wordmark,
                                     "PRIVATE CREATIVE NOTE" eyebrow,
                                     headline tailored to the niche
                                     ("A sharper video ad route for ..."),
                                     opening paragraph, summary card.
  2. The 45-second version         - compact sales page: 3 cards (what
                                     we saw / what we would make / what
                                     it costs to try) + "FROM L90".
  3. From live ad to video route   - up to 4 sampled ads turned into
                                     route cards (LIVE AD opening + route
                                     name + video idea + "OPEN AD"
                                     clickable link when an ad URL
                                     exists).
  4. Creative gap map              - 3-column matrix: current pattern,
                                     why it limits growth, UGC-style
                                     test to run.
  5. Concept board                 - 4 concept cards built from the
                                     niche-aware concept pack (titles,
                                     hooks, CTA pills, optional product
                                     image when one is supplied).
  6. How this works                - 4-step asset-based production system
                                     (brand inputs / scene routes / video
                                     edit / launch + learn) + brand
                                     safety guardrail line.
  7. Pricing and first test        - 3 tiers (Single video L90, Starter
                                     trio L260, Growth pack L499).
  8. Next step                     - warm CTA, "Want to see one <Brand>
                                     video?", OPEN WEBSITE / OPEN AD 01
                                     links when available.

Public API:

  build_pitch_pdf(...)             - unchanged signature (the existing
                                     Outreach `generate_pitch_pdf` tool
                                     and any operator scripts keep
                                     working). Accepts the V3 optional
                                     brand_profile + prospect_root.
  DEFAULT_FRAMEWORK_STRENGTHS      - retained for back-compat; the V4
                                     layout does not render them
                                     directly (the deck has its own
                                     pricing-and-concepts pages).

Image embedding policy (unchanged from V3):
  Only LOCAL image paths are embedded. Remote HTTP/HTTPS URLs surface as
  subtle text proof links instead of being fetched at PDF time. The
  optional `agents.outreach.brand_assets` collector is the canonical
  way to download a brand's logo / hero image to a local path BEFORE
  the deck is rendered.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from fpdf import FPDF

# --------------------------------------------------------------------------- #
# Layout + palette
# --------------------------------------------------------------------------- #

PAGE_W = 210.0
PAGE_H = 297.0
LEFT = 18.0
RIGHT = 18.0
TOP = 18.0
BOTTOM = 18.0
CONTENT_W = PAGE_W - LEFT - RIGHT          # 174mm
CONTENT_RIGHT = PAGE_W - RIGHT             # 192mm

# V4 editorial-black palette. Pages are filled near-black; cards are
# cream-on-black; brand accent colour falls back to a warm cream when
# the brand profile doesn't carry one.
PAGE_BG = (10, 10, 10)            # near-black page fill
INK = (18, 18, 18)                # text on cream cards
WHITE = (255, 255, 255)
CREAM = (243, 240, 232)           # warm card background
CREAM_INK = (24, 24, 24)
MID_LIGHT = (170, 170, 170)       # secondary text on black pages
MID = (95, 95, 95)
HAIRLINE_DARK = (45, 45, 45)
HAIRLINE_LIGHT = (210, 210, 210)
ACCENT_DEFAULT = (215, 195, 140)  # warm tan / cream-gold fallback accent

# Pricing tiers. Single source of truth — every place that mentions
# pricing reads from this tuple so changing one price ripples through
# the deck and the tests.
PRICING_TIERS = (
    {
        "label": "Single video",
        "eyebrow": "START HERE",
        "price": "£90",
        "bullets": (
            "1 finished 9:16 UGC-style video",
            "1 product/route",
            "1 hook direction",
            "Caption + CTA overlay",
            "One revision round",
        ),
        "tagline": "Lowest test barrier",
    },
    {
        "label": "Starter trio",
        "eyebrow": "SMALL PACK",
        "price": "£260",
        "bullets": (
            "3 finished videos to test angles",
            "3 separate hooks",
            "Same product or collection",
            "Ad-ready exports",
        ),
        "tagline": "Better for first read",
    },
    {
        "label": "Growth pack",
        "eyebrow": "AFTER PROOF",
        "price": "£499",
        "bullets": (
            "6 finished videos after fit is proven",
            "Scale winning route",
            "Multiple CTA angles",
            "More edit variety",
        ),
        "tagline": "Best for paid testing",
    },
)

DEFAULT_AGENCY_NAME = "Yuvo Studio"

# Image embedding policy: keep PDF generation fast and offline-safe.
_MAX_EMBED_IMAGE_BYTES = 6 * 1024 * 1024  # 6 MB
_SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif"}


# --------------------------------------------------------------------------- #
# Brand profile helpers
# --------------------------------------------------------------------------- #


def _bp(brand_profile: Optional[dict], *keys: str) -> Optional[str]:
    """First non-empty string field from brand_profile under any candidate key."""
    if not brand_profile:
        return None
    for k in keys:
        v = brand_profile.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _bp_list(brand_profile: Optional[dict], key: str) -> list[str]:
    """List-valued brand_profile field (e.g. product_images). Returns []
    when absent, malformed, or non-string entries are present."""
    if not brand_profile:
        return []
    v = brand_profile.get(key)
    if not isinstance(v, list):
        return []
    return [s.strip() for s in v if isinstance(s, str) and s.strip()]


def _parse_hex_color(value: Optional[str]) -> Optional[tuple[int, int, int]]:
    """Parse '#1A1A1A' / '1a1a1a' / '#fff' into an (r, g, b) tuple."""
    if not isinstance(value, str):
        return None
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return None
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return None


def _resolve_asset_path(
    raw_path: Optional[str], prospect_root: Optional[Path]
) -> Optional[Path]:
    """Turn a raw asset path into an absolute Path that exists, or None.

    Strategy:
      - Reject HTTP/HTTPS URLs (offline-safe: never fetch at PDF time).
      - Absolute path: use it if it exists.
      - Relative path: resolve against prospect_root.
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    s = raw_path.strip()
    if s.lower().startswith(("http://", "https://")):
        return None
    candidate = Path(s)
    if not candidate.is_absolute() and prospect_root is not None:
        candidate = (prospect_root / candidate).resolve()
    try:
        if candidate.exists() and candidate.is_file():
            return candidate
    except OSError:
        return None
    return None


def _is_embeddable_image(path: Optional[Path]) -> bool:
    """Whether `path` is a local image fpdf2 can safely embed."""
    if path is None:
        return False
    if path.suffix.lower() not in _SUPPORTED_IMAGE_EXTS:
        return False
    try:
        if path.stat().st_size > _MAX_EMBED_IMAGE_BYTES:
            return False
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Text sanitisation (latin-1 safe, debug-leak filtering)
# --------------------------------------------------------------------------- #


_PUNCT_MAP = {
    "—": " - ",   # em-dash
    "–": " - ",   # en-dash
    "−": "-",     # minus sign
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "…": "...",
    " ": " ",
    "•": "-",
    "·": "-",
    "→": "->",
    "←": "<-",
}


def _sanitize(text: str) -> str:
    """Force a string into latin-1-safe characters for FPDF core fonts."""
    if not text:
        return ""
    text = str(text)
    for src, dst in _PUNCT_MAP.items():
        text = text.replace(src, dst)
    text = text.encode("latin-1", errors="ignore").decode("latin-1")
    return text


_VALID_CONFIDENCES = ("high", "medium", "low")
_LEGACY_CONF_RE = re.compile(
    r"^\s*\[\s*(HIGH|MEDIUM|LOW)\s*CONFIDENCE[^\]]*\]\s*",
    re.IGNORECASE,
)

_DEBUG_MARKERS = (
    "body_text",
    "{{",
    "}}",
    "media_type=",
    "media_type =",
    "ad_archive_id",
    "cta_text",
    "start_date",
)


def _looks_like_debug(text: str) -> bool:
    """True when a string carries internal-only debug markers."""
    if not text:
        return False
    lower = text.lower()
    return any(m in lower for m in _DEBUG_MARKERS)


def _strip_debug_inline(text: str) -> str:
    """Scrub inline debug fragments from a string we still want to render."""
    if not text:
        return ""
    text = re.sub(r"body_text\s*=\s*['\"][^'\"]*['\"]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"\bad[_ ]?archive[_ ]?id[s]?\s*\d+", "an ad reference", text, flags=re.IGNORECASE)
    text = re.sub(r"\bad\s+\d{6,}\b", "an ad reference", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*;\s*;\s*", "; ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ;,.-")


def _parse_weakness(w) -> dict:
    """Normalise bare-string OR dict-shape weakness into a common dict."""
    if isinstance(w, dict):
        confidence = (w.get("confidence") or "").strip().lower()
        if confidence not in _VALID_CONFIDENCES:
            confidence = ""
        return {
            "description": (w.get("description") or w.get("desc") or "").strip(),
            "confidence": confidence,
            "evidence": [str(e) for e in (w.get("evidence") or [])],
            "raw": None,
        }
    if isinstance(w, str):
        confidence = ""
        text = w
        m = _LEGACY_CONF_RE.match(text)
        if m:
            confidence = m.group(1).lower()
            text = text[m.end():]
        if " Evidence:" in text:
            description, evidence_blob = text.split(" Evidence:", 1)
        elif "Evidence:" in text:
            description, evidence_blob = text.split("Evidence:", 1)
        else:
            description, evidence_blob = text, ""
        return {
            "description": description.strip(),
            "confidence": confidence,
            "evidence": [evidence_blob.strip()] if evidence_blob.strip() else [],
            "raw": w,
        }
    return {
        "description": repr(w),
        "confidence": "",
        "evidence": [],
        "raw": w,
    }


# --------------------------------------------------------------------------- #
# Niche-aware concept synthesis
# --------------------------------------------------------------------------- #


def _niche_short(niche: str) -> str:
    """Short, inline-friendly form of the niche string."""
    if not niche:
        return "your category"
    parts = re.split(r"[/,;]", niche)
    short = parts[0].strip().lower()
    return short or "your category"


def _niche_profile(niche: str) -> str:
    """Map a raw niche string onto a coarse category key the rest of the
    builder can switch on. Keys are stable so concept packs and headline
    synthesis stay in sync."""
    n = (niche or "").lower()
    if any(k in n for k in ("supplement", "nutrition", "energy", "endurance", "fuel")):
        return "endurance-nutrition"
    if any(k in n for k in ("sports bra", "lingerie", "intimate")):
        return "intimates"
    if any(k in n for k in ("activewear", "gymwear", "leggings")):
        return "activewear"
    if any(k in n for k in ("running", "runner")) and any(k in n for k in ("apparel", "underwear", "shorts", "kit")):
        return "running-apparel"
    if any(k in n for k in ("coach", "online fitness", "personal", "programme", "program")):
        return "online-coaching"
    if any(k in n for k in ("skincare", "skin care", "beauty", "cosmetic")):
        return "skincare"
    if any(k in n for k in ("restaurant", "takeaway", "food delivery", "cafe", "kitchen")):
        return "restaurant"
    if any(k in n for k in ("snack", "food", "drink", "beverage")):
        return "food"
    if any(k in n for k in ("fashion", "apparel", "clothing", "outfit")):
        return "fashion"
    if any(k in n for k in ("fitness", "gym", "training", "wellness")):
        return "fitness"
    return "default"


def _category_headline(niche: str) -> str:
    """Niche-aware cover headline form. The reference deck uses
    'A sharper video ad route for YANA Active.'; we keep that shape and
    swap one adjective per category profile so the cover feels native
    to skincare/fitness/food/etc."""
    profile = _niche_profile(niche)
    if profile == "skincare":
        return "A cleaner skincare video route"
    if profile in ("food", "restaurant"):
        return "A warmer food-led video route"
    if profile == "endurance-nutrition":
        return "A clearer endurance-fuel video route"
    if profile == "intimates":
        return "A more honest fit-led video route"
    if profile == "activewear":
        return "A sharper video ad route"
    if profile == "running-apparel":
        return "A more grounded running-kit video route"
    if profile == "online-coaching":
        return "A more honest coaching-led video route"
    if profile == "fashion":
        return "A more grounded fashion video route"
    if profile == "fitness":
        return "A stronger short-form ad route"
    return "A stronger short-form ad route"


def _concept_pack(prospect_name: str, niche: str) -> list[dict]:
    """Return up to 4 short-form video concept ideas tailored to the niche.

    The deck shows 4 cards on the Concept Board page; if a niche profile
    only carries 3 we extend with a generic 'open route' fallback.
    """
    profile = _niche_profile(niche)
    name = prospect_name or "your brand"

    if profile == "endurance-nutrition":
        base = [
            {"title": "The long-run stomach test", "hook": "What I take before a Sunday long run", "cta": "SHOP THE BUNDLE"},
            {"title": "Energy without the gel feeling", "hook": "Chews that don't make your stomach turn", "cta": "TRY THE CHEWS"},
            {"title": "What's in my fuel kit", "hook": "The three things I carry on race day", "cta": "BUILD THE KIT"},
            {"title": "Mid-run taste test", "hook": "The one I keep coming back to", "cta": "TASTE FOR YOURSELF"},
        ]
    elif profile == "intimates":
        base = [
            {"title": "The support test", "hook": "One sports bra, three workouts", "cta": "SHOP THE FIT"},
            {"title": "What changes when fit is right", "hook": "I didn't realise this was missing", "cta": "FIND YOUR SIZE"},
            {"title": "By cup size, not by guess", "hook": "The fitting question no one asks online", "cta": "SHOP BY CUP"},
            {"title": "All-day comfort", "hook": "Six hours in, still forgot it was on", "cta": "SHOP THE LINE"},
        ]
    elif profile == "activewear":
        base = [
            {"title": "Class to coffee", "hook": "I changed once today", "cta": "SHOP BACK TO BASICS"},
            {"title": "Proof before promise", "hook": "What luxury has to do", "cta": "SEE THE LEGGING"},
            {"title": "Lover Girl edit", "hook": "The colour that changes the outfit", "cta": "SHOP LOVER GIRL"},
            {"title": "Gym-to-life bag", "hook": "The one thing I take everywhere", "cta": "VIEW ACCESSORY"},
        ]
    elif profile == "running-apparel":
        base = [
            {"title": "After 50 kilometres", "hook": "What survives 50km without a complaint", "cta": "SHOP THE KIT"},
            {"title": "Wet kilometre review", "hook": "Tested in the rain, on purpose", "cta": "SHOP THE SHORTS"},
            {"title": "Two sizes too sceptical", "hook": "Why I expected it to chafe", "cta": "FIND YOUR SIZE"},
            {"title": "Race-day kit run", "hook": "Everything I packed for race day", "cta": "BUILD THE KIT"},
        ]
    elif profile == "skincare":
        base = [
            {"title": "Morning skin reset", "hook": "What three minutes actually changes", "cta": "SHOP THE ROUTINE"},
            {"title": "Texture proof close-up", "hook": "What it looks like one week in", "cta": "SHOP THE SERUM"},
            {"title": "Sensitive-skin routine", "hook": "What I use when my skin reacts to everything", "cta": "SHOP SENSITIVE"},
            {"title": "Founder explains the formula", "hook": "Why we left this ingredient out", "cta": "READ THE FORMULA"},
        ]
    elif profile == "restaurant":
        base = [
            {"title": "Tonight's order, on camera", "hook": "What we actually serve at 7pm", "cta": "ORDER NOW"},
            {"title": "Behind the kitchen pass", "hook": "Two minutes from order to plate", "cta": "ORDER ONLINE"},
            {"title": "Family-table moment", "hook": "The one we send out the most", "cta": "BOOK A TABLE"},
            {"title": "First-time order", "hook": "What to try if it's your first time", "cta": "ORDER ONLINE"},
        ]
    elif profile == "food":
        base = [
            {"title": "First taste, on camera", "hook": "What it tastes like the first time", "cta": "TRY THE PACK"},
            {"title": "What's in the box", "hook": "Top down, no music, real reactions", "cta": "SHOP THE BOX"},
            {"title": "Snack-break routine", "hook": "The 3pm fix that doesn't crash you", "cta": "SHOP THE PACK"},
            {"title": "Founder's favourite", "hook": "The one I keep eating myself", "cta": "SHOP THE LINE"},
        ]
    elif profile == "fashion":
        base = [
            {"title": "Outfit, three ways", "hook": "One piece, three days", "cta": "SHOP THE PIECE"},
            {"title": "Quiet quality close-up", "hook": "Why the stitching matters", "cta": "SHOP THE LINE"},
            {"title": "Day-to-night switch", "hook": "Office to dinner in one change", "cta": "SHOP THE LOOK"},
            {"title": "The piece I wear most", "hook": "The one I keep reaching for", "cta": "SHOP THE PIECE"},
        ]
    elif profile == "online-coaching":
        base = [
            {"title": "Day 1 vs Day 90", "hook": "What changed and what I'd skip", "cta": "BOOK A CALL"},
            {"title": "The Sunday review", "hook": "What I track every week", "cta": "SEE THE PROCESS"},
            {"title": "Three questions before we start", "hook": "What a real intake call sounds like", "cta": "BOOK INTRO CALL"},
            {"title": "Behind a real check-in", "hook": "The two minutes that keep clients on track", "cta": "JOIN THE LIST"},
        ]
    elif profile == "fitness":
        base = [
            {"title": "The honest thirty days", "hook": "What I tracked, what I didn't", "cta": f"TRY {name.upper()}"},
            {"title": "Before I bought this", "hook": "What I almost bought instead", "cta": "SHOP THE OFFER"},
            {"title": "Same product, three openings", "hook": "Different first three seconds", "cta": "SHOP NOW"},
            {"title": "What the first week looks like", "hook": "Day one through day seven", "cta": "START HERE"},
        ]
    else:
        base = [
            {"title": "Why I almost didn't", "hook": "What stopped me, and what changed it", "cta": "SHOP THE OFFER"},
            {"title": "Three different openings", "hook": "Same product. Different first 3 seconds.", "cta": "TRY THE PRODUCT"},
            {"title": "What it actually looks like", "hook": "Quiet, honest review", "cta": "SHOP NOW"},
            {"title": "The one I keep using", "hook": "What I went back to after testing", "cta": "SHOP THE LINE"},
        ]
    # Annotate each concept with the prospect name so consumers downstream
    # (tests, future image-gen, future analytics) can carry context.
    for c in base:
        c.setdefault("prospect_name", name)
        c.setdefault("concept_frame_path", None)  # reserved for future image-gen
    return base


# --------------------------------------------------------------------------- #
# Ad classification + route synthesis
# --------------------------------------------------------------------------- #


_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")


def _ad_brand_initial(page_name: Optional[str]) -> str:
    """Up to 2 uppercase letters to render inside a mock ad card."""
    name = (page_name or "").strip()
    if not name:
        return "AD"
    parts = re.split(r"[\s\-_]+", name)
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    return name[:2].upper()


def _clean_body_excerpt(body: Optional[str], max_chars: int = 160) -> Optional[str]:
    """Clean + truncate ad body text. Returns None when there's no real content."""
    if not body:
        return None
    cleaned = _sanitize(body)
    cleaned = _PLACEHOLDER_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 3].rstrip() + "..."
    return cleaned


def _classify_ad_issue(ad: dict, all_ads: Sequence[dict]) -> tuple[str, str]:
    """(issue_tag, one-line diagnosis) for a single sampled ad.

    Kept from V3 because the same five buckets drive the V4 route cards;
    only the surfacing changed.
    """
    body = (ad.get("body_text") or "").strip()
    has_placeholder = bool(_PLACEHOLDER_RE.search(body))
    body_no_placeholder = _PLACEHOLDER_RE.sub("", body).strip()

    duplicates = sum(
        1
        for other in all_ads
        if other is not ad and (other.get("body_text") or "").strip() == body and body
    )

    cta_present = bool((ad.get("cta_text") or "").strip())
    days_raw = ad.get("days_active")
    days = int(days_raw) if isinstance(days_raw, (int, float)) and days_raw > 0 else 0

    if has_placeholder and not body_no_placeholder:
        if duplicates:
            return (
                "PLACEHOLDER COPY",
                f"This ad and {duplicates} other sampled ad(s) appear to show "
                f"an unfilled product placeholder - paid spend running against "
                f"creative that looks unfinished.",
            )
        return (
            "PLACEHOLDER COPY",
            "This ad appears to show an unfilled product placeholder, "
            "which can make paid creatives look unfinished.",
        )

    if duplicates:
        return (
            "DUPLICATE COPY",
            f"This copy block runs across {duplicates + 1} sampled ads with "
            f"no variation - one angle is doing the work.",
        )

    if not body_no_placeholder:
        return (
            "NO COPY CAPTURED",
            "No body text was captured for this ad - the creative leans "
            "entirely on the visual.",
        )

    if not cta_present:
        if days >= 60:
            return (
                "NO CTA CAPTURED",
                f"This ad has been live for {days} days with no clear "
                f"call-to-action layer captured.",
            )
        return (
            "NO CTA CAPTURED",
            "No clear call-to-action layer captured - the buyer is left "
            "to find the next step on their own.",
        )

    if days >= 80:
        return (
            "LONG-RUNNING SIGNAL",
            f"This ad has been active for {days} days. Worth treating as "
            f"a creative anchor and testing variants from.",
        )

    return ("ACTIVE", "Currently live in the Meta Ads Library.")


def _route_from_ad(ad: dict, niche: str, concept_pack: list[dict],
                   *, route_index: int = 0,
                   prospect_root: Optional[Path] = None) -> dict:
    """Turn one sampled ad into a 'route card': route name, video idea,
    short excerpt of the live ad, public ads-library URL.

    Route names are taken from the niche's concept pack in order using
    `route_index` so the Page 3 ad cards mirror the Page 5 concept board
    1-1 and never repeat a route name on the same page.

    V4 asset pipeline: when the ad carries a local screenshot path
    (`ad_screenshot_path` / `image_path` / `snapshot_path` /
    `thumbnail_path`), the renderer embeds it on the route card. We
    resolve the path here so the renderer can check whether the asset
    is usable before laying out the card.
    """
    excerpt = _clean_body_excerpt(ad.get("body_text"), max_chars=110) or "(no body copy captured)"
    issue_tag, _ = _classify_ad_issue(ad, [ad])
    n = _niche_short(niche)

    route_titles = [c.get("title") for c in concept_pack if c.get("title")]
    if route_titles:
        route_name = route_titles[route_index % len(route_titles)]
    else:
        route_name = "Open creative route"
    days = ad.get("days_active") or 0

    # Video-idea seed. A small set of templates keyed by issue tag - keeps
    # the page from reading like an audit and still flags the gap to the
    # operator who reviews the deck.
    if issue_tag == "PLACEHOLDER COPY":
        idea = (
            "Replace the dynamic placeholder with a single product hero and "
            f"a real opening line about {n}."
        )
    elif issue_tag == "DUPLICATE COPY":
        idea = (
            "Take the same product and shoot it under three different first "
            "lines - lived-experience, founder voice, and proof close-up."
        )
    elif issue_tag == "NO CTA CAPTURED":
        idea = (
            "Add a one-line CTA pinned to the closing frame and a matching "
            "caption that points to the right product page."
        )
    elif issue_tag == "NO COPY CAPTURED":
        idea = (
            "Layer a single hook line over the existing visual and add a "
            "spoken voiceover that names the specific buying moment."
        )
    elif issue_tag == "LONG-RUNNING SIGNAL":
        idea = (
            "Treat this ad as the creative anchor and shoot three video "
            "variants that test a new opening line against the same offer."
        )
    else:
        idea = (
            f"Turn the existing brand moment into a short UGC-style ad "
            f"built for {n}."
        )

    return {
        "live_ad_excerpt": excerpt,
        "route_name": route_name,
        "video_idea": idea,
        "ad_library_url": _ad_proof_link(ad),
        "snapshot_url": ad.get("snapshot_url") if isinstance(ad.get("snapshot_url"), str) else None,
        "days_active": int(days) if isinstance(days, (int, float)) else 0,
        "ad_archive_id": (ad.get("ad_archive_id") or "").strip(),
        "page_name": (ad.get("page_name") or "").strip(),
        "media_type": ad.get("media_type"),
        "image_url": ad.get("image_url"),
        "image_path_local": _resolve_asset_path(
            (
                ad.get("ad_screenshot_path")
                or ad.get("image_path")
                or ad.get("snapshot_path")
                or ad.get("thumbnail_path")
            ),
            prospect_root,
        ),
    }


def _ad_proof_link(ad: dict) -> Optional[str]:
    """Public, clickable proof link for one ad. We prefer the Meta Ads
    Library URL (long-lived); fall back to snapshot/link URL or derive a
    Meta Ads Library URL from the ad_archive_id when no remote URL is
    present at all.  Returns None when nothing usable exists."""
    for key in ("ad_library_url", "snapshot_url", "link_url"):
        v = ad.get(key)
        if isinstance(v, str) and v.lower().startswith(("http://", "https://")):
            return v.strip()
    archive_id = ad.get("ad_archive_id")
    if isinstance(archive_id, (str, int)):
        s = str(archive_id).strip()
        if s and s.isdigit():
            return f"https://www.facebook.com/ads/library/?id={s}"
    return None


def _trim_url_for_display(url: str, max_chars: int = 56) -> str:
    """Display-form URL (drop https://, ellipsis if too long)."""
    if not url:
        return ""
    s = url
    for prefix in ("https://", "http://"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
    if len(s) > max_chars:
        s = s[: max_chars - 1] + "..."
    return s


# --------------------------------------------------------------------------- #
# Gap map row generation
# --------------------------------------------------------------------------- #


def _gap_map_rows(weaknesses: list[dict], ads: Sequence[dict]) -> list[dict]:
    """3-column gap map rows derived from observed weaknesses + ad bodies."""
    blob = " ".join((w.get("description") or "").lower() for w in weaknesses)
    blob += " " + " ".join(
        str(w.get("raw") or "").lower() for w in weaknesses if w.get("raw")
    )
    body_blob = " ".join((a.get("body_text") or "").lower() for a in ads)

    rows: list[dict] = []

    if "placeholder" in blob or "unfilled" in blob or "{{product" in body_blob:
        rows.append({
            "current": "Unfilled product placeholders running on live ads",
            "limit": "Paid spend going against creative that looks unfinished",
            "test": "Refresh the ads so every dynamic field is named or removed",
        })
    if any(k in blob for k in ("identical", "same body", "single copy", "duplicat", "verbatim")):
        rows.append({
            "current": "One copy angle carrying most of the active ads",
            "limit": "Audience fatigue caps performance before bidding adapts",
            "test": "A three-hook short-form video pack rotated against the current top ad",
        })
    if "no cta" in blob or "missing cta" in blob or "null cta" in blob:
        rows.append({
            "current": "No clear call-to-action layer on the sampled ads",
            "limit": "Click-through stays flat even when the hook is strong",
            "test": "One specific CTA per concept, paired with the right landing page",
        })
    if "lifestyle" in blob or "generic" in blob or "broad sweep" in blob or "no persona" in blob:
        rows.append({
            "current": "Broad lifestyle language with no clear audience",
            "limit": "Speaks to no one in particular - cohort acquisition stalls",
            "test": "One concept per cohort, written in their actual language",
        })
    if "no video" in blob or "image-only" in blob or "static" in blob:
        rows.append({
            "current": "Ad library leans heavily on static creative",
            "limit": "Static caps view-time; short-form video extends it",
            "test": "Three short-form video variants running alongside the static pack",
        })
    if any(k in blob for k in ("transformation", "result", "credential", "award", "launch")):
        rows.append({
            "current": "Product launches lead the story",
            "limit": "Launch posts don't always answer the cold buyer's questions",
            "test": "Turn every launch into 3 situations: before, during, day-to-day",
        })

    fallbacks = [
        {
            "current": "Same-angle repeat creative carrying the spend",
            "limit": "Slow CTR decay before bidding can detect it",
            "test": "A small rotation of tested short-form variants",
        },
        {
            "current": "Product-launch announcement format only",
            "limit": "Misses the consideration-stage buyer entirely",
            "test": "Lived-experience UGC-style video on the first-purchase moment",
        },
        {
            "current": "Brand voice fully owns the message",
            "limit": "Reduces credibility on cold audiences",
            "test": "Customer-voice testimonial in short-form video",
        },
        {
            "current": "Premium positioning stated more than shown",
            "limit": "Stated claims do less work than visible proof",
            "test": "Proof-led scenes that show the reason in motion",
        },
    ]
    fb_iter = iter(fallbacks)
    while len(rows) < 4:
        rows.append(next(fb_iter))

    return rows[:4]


# --------------------------------------------------------------------------- #
# Headline + 45-second summary synthesis
# --------------------------------------------------------------------------- #


def _summary_cards(
    weaknesses: list[dict],
    ads: Sequence[dict],
    niche: str,
    prospect_name: str,
) -> list[dict]:
    """Three cards for the '45-second version' page: what we saw / what we
    would make / what it costs to try. Body copy adapts to the dominant
    weakness signal so the page doesn't read as boilerplate."""
    blob = " ".join((w.get("description") or "").lower() for w in weaknesses)
    has_no_video = "no video" in blob or "image-only" in blob or "static" in blob
    has_single = any(k in blob for k in ("single copy", "identical", "duplicat", "verbatim"))
    has_placeholder = "placeholder" in blob or "unfilled" in blob
    has_launch_only = "launch" in blob or "product launch" in blob

    if has_placeholder:
        saw = (
            "Long-running ads in the public library still show an unfilled "
            "product placeholder. The paid spend is moving; the creative is "
            "not finished."
        )
    elif has_single:
        saw = (
            "A single copy angle is doing most of the work across the sampled "
            "ads. Strong brand, but not enough buyer-specific situations."
        )
    elif has_no_video:
        saw = (
            "The current ad library leans heavily on static creative. Strong "
            "product, but no short-form video pack to test alongside it."
        )
    elif has_launch_only:
        saw = (
            "Launch-led openings and broad lifestyle language are carrying "
            "the message. Strong brand, but not enough buyer-specific "
            "situations on camera."
        )
    else:
        saw = (
            "The sampled ads show a working brand voice but a small number "
            "of creative angles - more proof-led variants would compound."
        )

    n = _niche_short(niche)
    name = prospect_name or "your brand"
    make = (
        f"One polished UGC-style video route around a specific {n} buying "
        f"moment - built to match the {name} world."
    )
    cost = (
        "Start with one finished video from £90. If the first direction "
        "feels right, scale into packs only after you have seen the quality."
    )

    return [
        {"label": "CLEARER BUYER MOMENT", "title": "What we saw", "body": saw},
        {"label": "VIDEO ROUTE", "title": "What we would make", "body": make},
        {"label": "FROM £90", "title": "What it costs to try", "body": cost},
    ]


def _cover_paragraph(niche: str, prospect_name: str) -> str:
    """Cover paragraph - 1-2 sentences naming the prospect, the niche
    and the moment we'd build a creative test around."""
    n = _niche_short(niche)
    name = prospect_name or "your brand"
    return (
        f"A focused paid-social opportunity built around {name}'s current "
        f"ads, product world and the moments your {n} buyer already "
        f"recognises."
    )


def _next_step_lines(prospect_name: str, cta: Optional[str]) -> tuple[str, str]:
    """Heading + body for the Next step page. Falls back to a brand-named
    question + a one-line invite when no CTA was passed."""
    name = prospect_name or "your brand"
    heading = f"Want to see one {name} video?"
    if cta and cta.strip():
        body = cta.strip()
        if not body.lower().startswith(("reply", "want to", "see ", "open ")):
            body = (
                f"{body} Reply with 'send the first route' and we will outline "
                f"the first £90 video concept around one current {name} product page."
            )
    else:
        body = (
            f"Reply with 'send the first route' and we will outline the first "
            f"£90 video concept around one current {name} product page."
        )
    return heading, body


# --------------------------------------------------------------------------- #
# Drawing primitives - editorial dark mode
# --------------------------------------------------------------------------- #


def _fill_page(pdf: FPDF, color: tuple[int, int, int]) -> None:
    """Fill the entire current page with a solid colour."""
    pdf.set_fill_color(*color)
    pdf.set_draw_color(*color)
    pdf.rect(0, 0, PAGE_W, PAGE_H, style="F")


def _filled(pdf: FPDF, x: float, y: float, w: float, h: float, color: tuple[int, int, int]) -> None:
    pdf.set_fill_color(*color)
    pdf.set_draw_color(*color)
    pdf.rect(x, y, w, h, style="F")


def _stroke_rect(pdf: FPDF, x: float, y: float, w: float, h: float,
                 color: tuple[int, int, int], line_w: float = 0.3) -> None:
    pdf.set_draw_color(*color)
    pdf.set_line_width(line_w)
    pdf.rect(x, y, w, h, style="D")


def _card(pdf: FPDF, x: float, y: float, w: float, h: float,
          bg: tuple[int, int, int] = CREAM) -> None:
    """Cream-on-black card rectangle. Optional inner stroke is omitted -
    cards rely on the contrast with the page background."""
    _filled(pdf, x, y, w, h, bg)


def _hairline(pdf: FPDF, y: float, color: tuple[int, int, int] = HAIRLINE_DARK,
              x1: Optional[float] = None, x2: Optional[float] = None) -> None:
    pdf.set_draw_color(*color)
    pdf.set_line_width(0.2)
    pdf.line(x1 if x1 is not None else LEFT, y, x2 if x2 is not None else CONTENT_RIGHT, y)


def _vertical_wordmark(pdf: FPDF, brand_name: str, accent: tuple[int, int, int]) -> None:
    """Decorative vertical letter stack along the left margin of interior
    pages. Mirrors the editorial reference deck ('Y / A / N / A')."""
    first_word = (brand_name or "").strip().split(" ")[0][:6].upper()
    if not first_word:
        return
    pdf.set_text_color(*accent)
    pdf.set_font("Helvetica", "B", 14)
    n = len(first_word)
    block_h = 50.0
    start_y = 50.0
    step = block_h / max(1, n)
    x = 6.0
    for i, ch in enumerate(first_word):
        pdf.set_xy(x, start_y + i * step)
        pdf.cell(8, step, _sanitize(ch), align="C")


def _page_number(pdf: FPDF) -> None:
    """Top-right page number for interior pages ('02', '03', ...)."""
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(CONTENT_RIGHT - 12, 12)
    pdf.cell(12, 5, _sanitize(f"{pdf.page_no():02d}"), align="R")


def _section_eyebrow(pdf: FPDF, number: str, title: str) -> None:
    """Standard interior-page section eyebrow + big headline."""
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(LEFT, 12)
    pdf.cell(CONTENT_W / 2, 5, _sanitize(f"{number} / {title.upper()}"))


def _accent_color(brand_profile: Optional[dict]) -> tuple[int, int, int]:
    """Brand accent color or the cream-gold fallback."""
    c = _parse_hex_color(_bp(brand_profile, "primary_color"))
    return c if c is not None else ACCENT_DEFAULT


def _measure(pdf: FPDF, w: float, line_h: float, text: str) -> float:
    """Wrapped height of `text` with the currently-set font."""
    if not text:
        return 0.0
    return float(pdf.multi_cell(w, line_h, text=_sanitize(text), dry_run=True, output="HEIGHT"))


def _embed_image_or_fallback(
    pdf: FPDF,
    path: Optional[Path],
    x: float,
    y: float,
    w: float,
    h: float,
    fallback_initial: str,
    accent: tuple[int, int, int],
) -> None:
    """Embed a local image into the rect, or draw a tasteful cream box
    with a centred monogram when no usable image is available."""
    if _is_embeddable_image(path):
        try:
            pdf.image(str(path), x=x, y=y, w=w, h=h)
            return
        except Exception:
            pass
    # Fallback: cream rectangle with a centred big initial.
    _filled(pdf, x, y, w, h, CREAM)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", max(16, int(min(w, h) * 0.4)))
    initial_w = pdf.get_string_width(fallback_initial)
    pdf.set_xy(x + (w - initial_w) / 2, y + h / 2 - min(w, h) * 0.18)
    pdf.cell(initial_w, 8, _sanitize(fallback_initial))
    # Tiny accent dot in the corner so the fallback still looks designed.
    _filled(pdf, x + w - 4, y + 2, 2, 2, accent)


# --------------------------------------------------------------------------- #
# _Deck class - black editorial mode
# --------------------------------------------------------------------------- #


class _Deck(FPDF):
    """FPDF subclass that fills every page with the editorial-black
    background and (for interior pages only) lays down the vertical
    wordmark + page number. The cover is custom-rendered."""

    def __init__(self, agency_name: str, prospect_name: str,
                 accent: tuple[int, int, int]):
        super().__init__()
        self.agency_name = agency_name
        self.prospect_name = prospect_name
        self.accent = accent
        self.set_margins(LEFT, TOP, RIGHT)
        # We control vertical layout manually; disable auto page breaks so
        # mid-card overflows don't drop a stray blank page.
        self.set_auto_page_break(auto=False)

    def header(self):
        # Fill the page with PAGE_BG on every page. Cover and interior
        # share the same dark base; section eyebrow / wordmark are drawn
        # per-page by the renderers.
        _fill_page(self, PAGE_BG)
        if self.page_no() == 1:
            return
        _vertical_wordmark(self, self.prospect_name, self.accent)
        _page_number(self)

    def footer(self):
        return


# --------------------------------------------------------------------------- #
# Page 1 - Cover (Private Creative Note)
# --------------------------------------------------------------------------- #


def _render_cover(
    pdf: _Deck,
    prospect_name: str,
    niche: str,
    headline: str,
    paragraph: str,
    summary_card_text: str,
    agency_name: str,
    accent: tuple[int, int, int],
    brand_profile: Optional[dict],
    prospect_root: Optional[Path],
    used_paths: set[Path],
) -> None:
    pdf.add_page()

    # Decorative vertical wordmark on the cover too (the reference deck
    # carries it across every page including the cover).
    _vertical_wordmark(pdf, prospect_name, accent)

    # Eyebrow: "PRIVATE CREATIVE NOTE"
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(LEFT, 26)
    pdf.cell(CONTENT_W, 5, _sanitize("PRIVATE CREATIVE NOTE"))

    # Optional hero image on the right side - tries explicit hero, then
    # the homepage screenshot, then product[0]. A real logo is allowed
    # to overlay the bottom even when hero is present.
    hero_path = _resolve_asset_path(
        _bp(brand_profile, "hero_image_path", "homepage_screenshot_path", "website_screenshot_path"),
        prospect_root,
    )
    if not _is_embeddable_image(hero_path):
        # Fall back to the first product image.
        for raw in _bp_list(brand_profile, "product_images"):
            cand = _resolve_asset_path(raw, prospect_root)
            if _is_embeddable_image(cand) and cand not in used_paths:
                hero_path = cand
                break

    logo_path = _resolve_asset_path(_bp(brand_profile, "logo_path"), prospect_root)

    hero_x = LEFT + CONTENT_W * 0.55
    hero_y = 44
    hero_w = CONTENT_W * 0.45
    hero_h = 70

    if _is_embeddable_image(hero_path) and hero_path not in used_paths:
        try:
            pdf.image(str(hero_path), x=hero_x, y=hero_y, w=hero_w, h=hero_h)
            used_paths.add(hero_path)
        except Exception:
            _embed_image_or_fallback(
                pdf, logo_path, hero_x, hero_y, hero_w, hero_h,
                _ad_brand_initial(prospect_name), accent,
            )
    elif _is_embeddable_image(logo_path) and logo_path not in used_paths:
        try:
            pdf.image(str(logo_path), x=hero_x + hero_w * 0.2, y=hero_y + 15,
                      w=hero_w * 0.6, h=hero_h - 30)
            used_paths.add(logo_path)
        except Exception:
            pass

    # Hero headline (left half)
    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "B", 30)
    pdf.set_xy(LEFT, 50)
    text_w = CONTENT_W * 0.52
    pdf.multi_cell(text_w, 11, _sanitize(f"{headline} for"))
    pdf.set_x(LEFT)
    pdf.set_font("Helvetica", "B", 30)
    pdf.multi_cell(text_w, 11, _sanitize(f"{prospect_name}."))

    # Niche line under the headline
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "I", 11)
    pdf.set_xy(LEFT, pdf.get_y() + 2)
    pdf.multi_cell(text_w, 6, _sanitize(niche or ""))

    # Cover paragraph
    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_xy(LEFT, 130)
    pdf.multi_cell(CONTENT_W, 5.5, _sanitize(paragraph))

    # Summary card near the bottom
    card_y = 175
    card_h = 36
    _card(pdf, LEFT, card_y, CONTENT_W, card_h, CREAM)
    # Accent bar on the left edge of the card
    _filled(pdf, LEFT, card_y, 2.4, card_h, accent)

    pdf.set_text_color(*MID)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(LEFT + 8, card_y + 6)
    pdf.cell(CONTENT_W - 12, 4, _sanitize("ONE CLEAR FIRST ROUTE"))

    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(LEFT + 8, card_y + 12)
    pdf.multi_cell(CONTENT_W - 12, 6, _sanitize(summary_card_text))

    # Bottom strip: agency + date in muted ink (subtle, not dominant)
    _hairline(pdf, PAGE_H - 22, HAIRLINE_DARK)
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_xy(LEFT, PAGE_H - 18)
    pdf.cell(CONTENT_W / 2, 4, _sanitize(f"Prepared for {prospect_name} by {agency_name}"))
    pdf.set_xy(LEFT + CONTENT_W / 2, PAGE_H - 18)
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    pdf.cell(CONTENT_W / 2, 4, _sanitize(date_str), align="R")


# --------------------------------------------------------------------------- #
# Page 2 - The 45-second version
# --------------------------------------------------------------------------- #


def _render_45_second_version(
    pdf: _Deck,
    prospect_name: str,
    niche: str,
    cards: list[dict],
    accent: tuple[int, int, int],
) -> None:
    pdf.add_page()
    _section_eyebrow(pdf, "01", "The 45-second version")

    # Big two-line headline
    name = prospect_name or "Your brand"
    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_xy(LEFT, 36)
    pdf.multi_cell(CONTENT_W, 9, _sanitize(f"{name} does not need louder ads."))
    pdf.set_x(LEFT)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("It needs faster proof-led creative tests."))

    # Sub-paragraph
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(LEFT, pdf.get_y() + 2)
    pdf.multi_cell(
        CONTENT_W,
        5,
        _sanitize(
            f"The public ad pattern already shows the creative starting points "
            f"for {_niche_short(niche)}. The opportunity is to turn those signals "
            f"into short video ads that feel specific from the first second."
        ),
    )

    # Three cards across the page
    card_top = 110
    card_h = 110
    gap = 4.0
    card_w = (CONTENT_W - gap * 2) / 3
    for i, c in enumerate(cards[:3]):
        x = LEFT + i * (card_w + gap)
        _card(pdf, x, card_top, card_w, card_h, CREAM)
        # Eyebrow label
        pdf.set_text_color(*MID)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_xy(x + 6, card_top + 6)
        pdf.cell(card_w - 12, 4, _sanitize(c.get("label", "")))
        # Title
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_xy(x + 6, card_top + 14)
        pdf.multi_cell(card_w - 12, 6, _sanitize(c.get("title", "")))
        # Body
        pdf.set_text_color(*CREAM_INK)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_xy(x + 6, pdf.get_y() + 1)
        pdf.multi_cell(card_w - 12, 4.5, _sanitize(c.get("body", "")))
        # Accent bottom rule
        _filled(pdf, x + 6, card_top + card_h - 6, 14, 1.0, accent)

    # Footer strip card
    closer_y = card_top + card_h + 10
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_xy(LEFT, closer_y)
    pdf.multi_cell(
        CONTENT_W, 5.5,
        _sanitize(
            f"A focused first route, grounded in {name}'s current ads "
            f"and brand world - so the next creative step feels obvious, "
            f"not generic."
        ),
    )


# --------------------------------------------------------------------------- #
# Page 3 - From live ad to video route
# --------------------------------------------------------------------------- #


def _render_ad_routes(
    pdf: _Deck,
    prospect_name: str,
    routes: list[dict],
    accent: tuple[int, int, int],
    used_paths: set[Path],
) -> None:
    pdf.add_page()
    _section_eyebrow(pdf, "02", "From live ad to video route")

    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(LEFT, 36)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("The first test is already hiding"))
    pdf.set_x(LEFT)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("inside your current ads."))

    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(LEFT, pdf.get_y() + 2)
    pdf.multi_cell(
        CONTENT_W, 5,
        _sanitize(
            "Below are live ad openings from the sampled Meta audit and the "
            "route we would turn each one into. Each link points back to the "
            "exact live ad source."
        ),
    )

    # 2x2 grid of route cards
    grid_top = 95
    grid_h = 170
    gap = 4.0
    rows = 2
    cols = 2
    card_w = (CONTENT_W - gap) / cols
    card_h = (grid_h - gap) / rows

    rendered_routes = routes[:4]
    if not rendered_routes:
        # Empty-state card
        _card(pdf, LEFT, grid_top, CONTENT_W, 40, CREAM)
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_xy(LEFT + 6, grid_top + 14)
        pdf.cell(CONTENT_W - 12, 6, _sanitize("Ad library not sampled yet."))
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*CREAM_INK)
        pdf.set_xy(LEFT + 6, grid_top + 22)
        pdf.cell(CONTENT_W - 12, 4, _sanitize("We will pull the Meta Ads Library before sending the next route."))
        return

    for idx, r in enumerate(rendered_routes):
        row = idx // cols
        col = idx % cols
        x = LEFT + col * (card_w + gap)
        y = grid_top + row * (card_h + gap)
        _render_route_card(pdf, r, x, y, card_w, card_h, prospect_name, accent, used_paths)


def _render_route_card(
    pdf: _Deck,
    route: dict,
    x: float,
    y: float,
    w: float,
    h: float,
    prospect_name: str,
    accent: tuple[int, int, int],
    used_paths: set[Path],
) -> None:
    """Single ad->route card.

    Layout adapts to whether the ad has a local image:
      * With image: image strip across the top ~32mm, then a smaller
        text block below (LIVE AD eyebrow + excerpt + ROUTE + idea +
        OPEN AD pill).
      * Without image: original V4 layout (LIVE AD eyebrow + larger
        excerpt up top, ROUTE block below the mid divider).

    `used_paths` is the deck-wide set of image Paths already embedded;
    any path already in the set is dropped to the unimaged layout so
    no single asset is shown twice."""
    _card(pdf, x, y, w, h, CREAM)

    pad = 6.0
    inner_w = w - pad * 2

    image_path = route.get("image_path_local")
    if isinstance(image_path, Path) and image_path in used_paths:
        image_path = None
    has_image = _is_embeddable_image(image_path) if isinstance(image_path, Path) else False

    if has_image:
        # Image strip at the top.
        img_h = 32.0
        try:
            pdf.image(str(image_path), x=x + pad, y=y + pad, w=inner_w, h=img_h)
            used_paths.add(image_path)
        except Exception:
            has_image = False

    # LIVE AD eyebrow + brand chip
    eyebrow_y = (y + pad + 34.0) if has_image else (y + pad)
    pdf.set_text_color(*MID)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(x + pad, eyebrow_y)
    pdf.cell(inner_w, 4, _sanitize("LIVE AD"))

    initial = _ad_brand_initial(route.get("page_name") or prospect_name)
    chip_w = 10
    chip_y = eyebrow_y - 1
    _filled(pdf, x + w - pad - chip_w, chip_y, chip_w, 6.5, INK)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(x + w - pad - chip_w, chip_y + 0.5)
    pdf.cell(chip_w, 6, _sanitize(initial), align="C")

    # Excerpt
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 10 if has_image else 11)
    pdf.set_xy(x + pad, eyebrow_y + 6)
    excerpt_lines = (90 if has_image else 110)
    pdf.multi_cell(
        inner_w, 4.4 if has_image else 5,
        _sanitize(_clean_body_excerpt(route.get("live_ad_excerpt"), max_chars=excerpt_lines) or ""),
    )

    # Days-active footnote
    days = route.get("days_active") or 0
    if days:
        pdf.set_text_color(*MID)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_xy(x + pad, pdf.get_y() + 0.5)
        pdf.cell(inner_w, 3, _sanitize(f"{days} DAYS ACTIVE"))

    # Mid divider
    mid_y = y + h * (0.72 if has_image else 0.55)
    _filled(pdf, x + pad, mid_y, inner_w, 0.4, HAIRLINE_LIGHT)

    # Route eyebrow + name
    pdf.set_text_color(*MID)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(x + pad, mid_y + 3)
    pdf.cell(inner_w, 3, _sanitize("ROUTE"))

    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 11 if has_image else 12)
    pdf.set_xy(x + pad, mid_y + 7)
    pdf.multi_cell(inner_w, 4.6, _sanitize(route.get("route_name") or "Open creative route"))

    # Video idea (with image: compact; without: full).
    pdf.set_text_color(*CREAM_INK)
    pdf.set_font("Helvetica", "", 8.5 if has_image else 9)
    pdf.set_xy(x + pad, pdf.get_y() + 0.5)
    pdf.multi_cell(
        inner_w, 4.0 if has_image else 4.2,
        _sanitize(route.get("video_idea") or ""),
    )

    # OPEN AD pill - clickable when we have a real URL.
    pill_w = 26
    pill_h = 7
    pill_x = x + w - pad - pill_w
    pill_y = y + h - pad - pill_h
    ad_url = route.get("ad_library_url")
    _filled(pdf, pill_x, pill_y, pill_w, pill_h, INK)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(pill_x, pill_y + 1)
    pdf.cell(pill_w, pill_h - 2, _sanitize("OPEN AD" if ad_url else "AD REFERENCE"), align="C")
    if ad_url:
        try:
            pdf.link(pill_x, pill_y, pill_w, pill_h, ad_url)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Page 4 - Creative gap map
# --------------------------------------------------------------------------- #


def _render_gap_map(
    pdf: _Deck,
    rows: list[dict],
    accent: tuple[int, int, int],
) -> None:
    pdf.add_page()
    _section_eyebrow(pdf, "03", "Creative gap map")

    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(LEFT, 36)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("The gap is not more content."))
    pdf.set_x(LEFT)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("It is more testable situations."))

    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(LEFT, pdf.get_y() + 2)
    pdf.multi_cell(
        CONTENT_W, 5,
        _sanitize(
            "The brand pillars are already valuable. The creative system should "
            "convert them into specific buying moments that can be versioned and "
            "tested without waiting on creator availability."
        ),
    )

    # Column headers
    table_top = 92
    col_gap = 3.0
    col_w = (CONTENT_W - col_gap * 2) / 3
    headers = ("CURRENT PATTERN", "WHY IT LIMITS GROWTH", "UGC-STYLE TEST TO RUN")
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "B", 8)
    for i, h in enumerate(headers):
        x = LEFT + i * (col_w + col_gap)
        pdf.set_xy(x, table_top)
        pdf.cell(col_w, 4, _sanitize(h))
    _hairline(pdf, table_top + 6, HAIRLINE_DARK)

    # Rows - each row is a 3-card strip on cream cards
    y = table_top + 12
    row_h = 38
    row_gap = 4.0
    for row in rows[:4]:
        for i, key in enumerate(("current", "limit", "test")):
            x = LEFT + i * (col_w + col_gap)
            _card(pdf, x, y, col_w, row_h, CREAM)
            if i == 2:
                # Last column gets a small accent rule top-left
                _filled(pdf, x, y, 4, 1.4, accent)
            pdf.set_text_color(*INK if i != 2 else CREAM_INK)
            pdf.set_font("Helvetica", "B" if i == 0 else "", 10)
            pdf.set_xy(x + 4, y + 4)
            pdf.multi_cell(col_w - 8, 4.4, _sanitize(row.get(key, "")))
        y += row_h + row_gap
        if y > PAGE_H - 30:
            break


# --------------------------------------------------------------------------- #
# Page 5 - Concept board
# --------------------------------------------------------------------------- #


def _render_concept_board(
    pdf: _Deck,
    concepts: list[dict],
    accent: tuple[int, int, int],
    brand_profile: Optional[dict],
    prospect_root: Optional[Path],
    used_paths: set[Path],
) -> None:
    pdf.add_page()
    _section_eyebrow(pdf, "04", "Concept board")

    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(LEFT, 36)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("Four routes built from existing assets"))

    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(LEFT, pdf.get_y() + 2)
    pdf.multi_cell(
        CONTENT_W, 5,
        _sanitize(
            "These are the kinds of UGC-style routes we would create from "
            "brand assets, product imagery and approved product-page claims. "
            "No product shipping, no creator booking, no live shoot."
        ),
    )

    # Build a pool of unique product images, skipping anything the
    # cover or ad-route cards already consumed. Each concept card pops
    # the next available image.
    product_paths: list[Path] = []
    for raw in _bp_list(brand_profile, "product_images"):
        p = _resolve_asset_path(raw, prospect_root)
        if (
            _is_embeddable_image(p)
            and p not in product_paths
            and p not in used_paths
        ):
            product_paths.append(p)

    grid_top = 95
    grid_h = 175
    gap = 4.0
    card_w = (CONTENT_W - gap) / 2
    card_h = (grid_h - gap) / 2

    for idx, concept in enumerate(concepts[:4]):
        row = idx // 2
        col = idx % 2
        x = LEFT + col * (card_w + gap)
        y = grid_top + row * (card_h + gap)
        product_path = product_paths.pop(0) if product_paths else None
        _render_concept_card(
            pdf, concept, x, y, card_w, card_h,
            product_path=product_path,
            accent=accent,
            number=idx + 1,
        )
        if product_path is not None:
            used_paths.add(product_path)


def _render_concept_card(
    pdf: _Deck,
    concept: dict,
    x: float,
    y: float,
    w: float,
    h: float,
    product_path: Optional[Path],
    accent: tuple[int, int, int],
    number: int,
) -> None:
    _card(pdf, x, y, w, h, CREAM)
    pad = 6.0

    # Phone/mockup column on the left (image or accent block)
    mock_w = w * 0.38
    mock_h = h - pad * 2
    mock_x = x + pad
    mock_y = y + pad
    if product_path is not None:
        _embed_image_or_fallback(
            pdf, product_path, mock_x, mock_y, mock_w, mock_h,
            _ad_brand_initial(concept.get("prospect_name") or ""),
            accent,
        )
    else:
        # Tasteful tinted block with a tiny phone-frame look
        _filled(pdf, mock_x, mock_y, mock_w, mock_h, (28, 28, 28))
        # Accent vertical bar
        _filled(pdf, mock_x + 2, mock_y + 6, 2, mock_h - 12, accent)
        # Tiny ink camera notch
        _filled(pdf, mock_x + mock_w / 2 - 4, mock_y + 4, 8, 2, accent)
        # Centred initial
        pdf.set_text_color(*CREAM)
        pdf.set_font("Helvetica", "B", 28)
        initial = _ad_brand_initial(concept.get("prospect_name") or "")
        init_w = pdf.get_string_width(initial)
        pdf.set_xy(mock_x + (mock_w - init_w) / 2, mock_y + mock_h / 2 - 8)
        pdf.cell(init_w, 12, _sanitize(initial))

    # Right-hand text column
    text_x = mock_x + mock_w + 5
    text_w = w - (mock_w + pad * 2 + 5)
    pdf.set_text_color(*MID)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(text_x, y + pad + 2)
    pdf.cell(text_w, 4, _sanitize(f"CONCEPT {number:02d}"))

    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(text_x, y + pad + 9)
    pdf.multi_cell(text_w, 5.5, _sanitize(concept.get("title", "")))

    pdf.set_text_color(*MID)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(text_x, pdf.get_y() + 1)
    pdf.cell(text_w, 4, _sanitize("HOOK"))

    pdf.set_text_color(*CREAM_INK)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_xy(text_x, pdf.get_y() + 5)
    pdf.multi_cell(text_w, 4.6, _sanitize(concept.get("hook", "")))

    # CTA pill at the bottom of the card
    pill_h = 7
    pill_w = min(text_w - 2, 50)
    pill_x = text_x
    pill_y = y + h - pad - pill_h
    _filled(pdf, pill_x, pill_y, pill_w, pill_h, INK)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(pill_x, pill_y + 1.2)
    pdf.cell(pill_w, 4.4, _sanitize(concept.get("cta", "OPEN ROUTE")), align="C")


# --------------------------------------------------------------------------- #
# Page 6 - How this works
# --------------------------------------------------------------------------- #


def _render_how_it_works(pdf: _Deck, accent: tuple[int, int, int]) -> None:
    pdf.add_page()
    _section_eyebrow(pdf, "05", "How this works")

    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(LEFT, 36)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("The whole point: no shoot day."))
    pdf.set_x(LEFT)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("More routes from assets you already own."))

    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(LEFT, pdf.get_y() + 2)
    pdf.multi_cell(
        CONTENT_W, 5,
        _sanitize(
            "The offer is simple: UGC-style paid social built from approved "
            "brand inputs and product claims. More angles to test without "
            "organising a creator pipeline."
        ),
    )

    # Eyebrow above the 4 steps
    eb_y = 96
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(LEFT, eb_y)
    pdf.cell(CONTENT_W, 4, _sanitize("ASSET-BASED PRODUCTION"))

    # 4 step cards in a row
    cards = [
        ("01", "Brand inputs", "Logo, colours, product URLs, product images, current ads and brand do/don'ts."),
        ("02", "Scene routes", "Lifestyle scenes, product proof moments and UGC-style visual hooks matched to the brand."),
        ("03", "Video edit", "9:16 video, hook text, captions, CTA overlay and collection link direction."),
        ("04", "Launch + learn", "Run against current ads and read 3-second hold, CTR, saves, comments and CPA direction."),
    ]
    top = 105
    gap = 3.5
    card_w = (CONTENT_W - gap * 3) / 4
    card_h = 80
    for i, (num, title, body) in enumerate(cards):
        x = LEFT + i * (card_w + gap)
        _card(pdf, x, top, card_w, card_h, CREAM)
        pdf.set_text_color(*MID)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_xy(x + 5, top + 5)
        pdf.cell(card_w - 10, 4, _sanitize(num))
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_xy(x + 5, top + 12)
        pdf.multi_cell(card_w - 10, 5, _sanitize(title))
        pdf.set_text_color(*CREAM_INK)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_xy(x + 5, pdf.get_y() + 1)
        pdf.multi_cell(card_w - 10, 4.3, _sanitize(body))
        # Accent dot
        _filled(pdf, x + 5, top + card_h - 6, 4, 1.0, accent)

    # Brand-safety guardrail strip
    strip_y = top + card_h + 10
    _filled(pdf, LEFT, strip_y, CONTENT_W, 18, (22, 22, 22))
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(LEFT + 5, strip_y + 3)
    pdf.cell(CONTENT_W - 10, 4, _sanitize("BRAND SAFETY GUARDRAIL"))
    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(LEFT + 5, strip_y + 8)
    pdf.multi_cell(
        CONTENT_W - 10, 4.5,
        _sanitize(
            "No fake testimonials, no fabricated results, no unsourced claims. "
            "Product claims stay tied to brand-approved pages; medical or "
            "health language stays out of paid creative unless the brand supports it."
        ),
    )


# --------------------------------------------------------------------------- #
# Page 7 - Pricing and first test
# --------------------------------------------------------------------------- #


def _render_pricing(pdf: _Deck, accent: tuple[int, int, int]) -> None:
    pdf.add_page()
    _section_eyebrow(pdf, "06", "Pricing and first test")

    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(LEFT, 36)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("Start small. Prove the route."))
    pdf.set_x(LEFT)
    pdf.multi_cell(CONTENT_W, 9, _sanitize("Then scale only what works."))

    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(LEFT, pdf.get_y() + 2)
    pdf.multi_cell(
        CONTENT_W, 5,
        _sanitize(
            "Start with one finished video, or pick a small pack to compare "
            "angles from day one."
        ),
    )

    top = 110
    gap = 4.0
    card_w = (CONTENT_W - gap * 2) / 3
    card_h = 130
    for i, tier in enumerate(PRICING_TIERS):
        x = LEFT + i * (card_w + gap)
        _card(pdf, x, top, card_w, card_h, CREAM)

        pdf.set_text_color(*MID)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_xy(x + 6, top + 6)
        pdf.cell(card_w - 12, 4, _sanitize(tier["eyebrow"]))

        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_xy(x + 6, top + 14)
        pdf.multi_cell(card_w - 12, 6, _sanitize(tier["label"]))

        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "B", 28)
        pdf.set_xy(x + 6, pdf.get_y() + 1)
        pdf.cell(card_w - 12, 10, _sanitize(tier["price"]))

        # Bullet list
        bullet_y = pdf.get_y() + 14
        pdf.set_text_color(*CREAM_INK)
        pdf.set_font("Helvetica", "", 9)
        for bullet in tier["bullets"]:
            pdf.set_xy(x + 6, bullet_y)
            pdf.cell(2, 4, _sanitize("-"))
            pdf.set_xy(x + 10, bullet_y)
            pdf.multi_cell(card_w - 16, 4.3, _sanitize(bullet))
            bullet_y = pdf.get_y() + 1.0

        # Bottom accent + tagline
        tag_y = top + card_h - 14
        _filled(pdf, x + 6, tag_y, 16, 1.0, accent)
        pdf.set_text_color(*MID)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_xy(x + 6, tag_y + 3)
        pdf.cell(card_w - 12, 4, _sanitize(tier["tagline"]))

    # Foot note
    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_xy(LEFT, top + card_h + 8)
    pdf.multi_cell(
        CONTENT_W, 4.5,
        _sanitize("Most brands start with one route, then expand once the creative direction feels right."),
    )


# --------------------------------------------------------------------------- #
# Page 8 - Next step
# --------------------------------------------------------------------------- #


def _render_next_step(
    pdf: _Deck,
    prospect_name: str,
    heading: str,
    body: str,
    accent: tuple[int, int, int],
    brand_profile: Optional[dict],
    first_route: Optional[dict],
) -> None:
    pdf.add_page()
    _section_eyebrow(pdf, "07", "Next step")

    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(LEFT, 36)
    pdf.cell(CONTENT_W, 5, _sanitize(f"FOR THE {prospect_name.upper()} TEAM"))

    pdf.set_text_color(*CREAM)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_xy(LEFT, 50)
    pdf.multi_cell(CONTENT_W, 10, _sanitize("Your brand should not"))
    pdf.set_x(LEFT)
    pdf.multi_cell(CONTENT_W, 10, _sanitize("become louder to convert."))

    pdf.set_text_color(*MID_LIGHT)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_xy(LEFT, pdf.get_y() + 2)
    pdf.multi_cell(
        CONTENT_W, 5.5,
        _sanitize(
            "It should become more specific. More situations. More proof. "
            "More reasons to click - all built from assets you already own, "
            "with no shoot day and no product logistics."
        ),
    )

    # Centre invite card
    card_y = 145
    card_h = 60
    _card(pdf, LEFT, card_y, CONTENT_W, card_h, CREAM)
    _filled(pdf, LEFT, card_y, 2.4, card_h, accent)

    pdf.set_text_color(*MID)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(LEFT + 8, card_y + 6)
    pdf.cell(CONTENT_W - 12, 4, _sanitize("NEXT STEP"))

    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_xy(LEFT + 8, card_y + 12)
    pdf.multi_cell(CONTENT_W - 12, 7, _sanitize(heading))

    pdf.set_text_color(*CREAM_INK)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(LEFT + 8, pdf.get_y() + 1)
    pdf.multi_cell(CONTENT_W - 16, 4.6, _sanitize(body))

    # Action pills row
    pill_y = card_y + card_h + 12
    pill_h = 9
    actions: list[tuple[str, Optional[str]]] = []
    website = _bp(brand_profile, "website_url")
    if website:
        actions.append(("OPEN WEBSITE", website if website.lower().startswith(("http://", "https://")) else f"https://{website}"))
    first_url = first_route.get("ad_library_url") if first_route else None
    if first_url:
        actions.append(("OPEN AD 01", first_url))
    instagram = _bp(brand_profile, "instagram_url")
    if instagram and len(actions) < 2:
        actions.append(("OPEN INSTAGRAM", instagram))

    pill_w = 50
    pill_gap = 6
    if actions:
        for i, (label, url) in enumerate(actions[:2]):
            x = LEFT + i * (pill_w + pill_gap)
            _filled(pdf, x, pill_y, pill_w, pill_h, CREAM)
            pdf.set_text_color(*INK)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_xy(x, pill_y + 2)
            pdf.cell(pill_w, 5, _sanitize(label), align="C")
            if url:
                try:
                    pdf.link(x, pill_y, pill_w, pill_h, url)
                except Exception:
                    pass

    # Sources footer
    pdf.set_text_color(*MID)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_xy(LEFT, PAGE_H - 22)
    sources_bits = [f"Sources inside this sample: {prospect_name} website/product pages"]
    if first_route and first_route.get("ad_library_url"):
        sources_bits.append("the provided Meta ad audit")
    sources_bits.append("each website asset used once")
    pdf.multi_cell(
        CONTENT_W, 4,
        _sanitize(" + ".join(sources_bits) + "; all other visuals are unique concept frames."),
    )


# --------------------------------------------------------------------------- #
# Back-compat surface: framework strengths string
# --------------------------------------------------------------------------- #


DEFAULT_FRAMEWORK_STRENGTHS = (
    "Hooks informed by competitor-ad longevity signals (>=14 days of "
    "sustained paid spend in your market is a strong candidate worth "
    "testing - we treat it as evidence to study, not proof of victory).",
    "Premium short-form video produced from your brand-safe references "
    "with motion patterns observed in long-running competitor ads.",
    "Built-in performance feedback loop: every Meta campaign's ROAS/CTR "
    "feeds back into our hook library, retiring underperformers and "
    "compounding the angles your real audience actually responds to.",
    "Per-client isolation: your brand voice, forbidden terms, and audience "
    "data never bleed into another client's creative.",
)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def build_pitch_pdf(
    output_path: Path,
    prospect_name: str,
    niche: str,
    weaknesses: Sequence,
    competitor_ad_summary: Sequence[dict],
    our_framework_strengths: Sequence[str] = DEFAULT_FRAMEWORK_STRENGTHS,
    cta: Optional[str] = None,
    agency_name: str = DEFAULT_AGENCY_NAME,
    brand_profile: Optional[dict] = None,
    prospect_root: Optional[Path] = None,
) -> Path:
    """Render the V4 Private Creative Note deck and return the output path.

    Signature preserved from V3 for back-compat. New parameters
    `brand_profile` and `prospect_root` are still optional; the deck
    degrades gracefully when neither is supplied.

    Parameters:
      our_framework_strengths : retained but not surfaced in V4 (the deck
                                has its own pricing-and-concepts pages).
      cta                     : merged into the Next step page body. If
                                None, a brand-named default is used.
      brand_profile           : dict carrying optional brand assets
                                (logo_path, website_url, primary_color,
                                product_images: list[str], etc.). All
                                fields optional.
      prospect_root           : folder used to resolve relative asset
                                paths (prospects/<id>/ by convention).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    agency_name = (agency_name or DEFAULT_AGENCY_NAME).strip() or DEFAULT_AGENCY_NAME

    if prospect_root is None:
        prospect_root = output_path.parent

    parsed_weaknesses = [_parse_weakness(w) for w in weaknesses]
    accent = _accent_color(brand_profile)
    concepts = _concept_pack(prospect_name or "", niche or "")
    cards = _summary_cards(parsed_weaknesses, competitor_ad_summary, niche or "", prospect_name or "")
    gap_rows = _gap_map_rows(parsed_weaknesses, competitor_ad_summary)
    routes = [
        _route_from_ad(a, niche or "", concepts, route_index=i, prospect_root=prospect_root)
        for i, a in enumerate(competitor_ad_summary[:4])
    ]

    headline = _category_headline(niche or "")
    paragraph = _cover_paragraph(niche or "", prospect_name or "")
    summary_card = "One clear first route. Four visual angles."
    next_heading, next_body = _next_step_lines(prospect_name or "", cta)

    # Deck-wide tracker so no single image is embedded twice across
    # cover / ad-route cards / concept board.
    used_paths: set[Path] = set()

    pdf = _Deck(agency_name=agency_name, prospect_name=prospect_name or "", accent=accent)

    _render_cover(
        pdf,
        prospect_name=prospect_name or "",
        niche=niche or "",
        headline=headline,
        paragraph=paragraph,
        summary_card_text=summary_card,
        agency_name=agency_name,
        accent=accent,
        brand_profile=brand_profile,
        prospect_root=prospect_root,
        used_paths=used_paths,
    )
    _render_45_second_version(
        pdf,
        prospect_name=prospect_name or "",
        niche=niche or "",
        cards=cards,
        accent=accent,
    )
    _render_ad_routes(
        pdf,
        prospect_name=prospect_name or "",
        routes=routes,
        accent=accent,
        used_paths=used_paths,
    )
    _render_gap_map(pdf, gap_rows, accent)
    _render_concept_board(pdf, concepts, accent, brand_profile, prospect_root, used_paths)
    _render_how_it_works(pdf, accent)
    _render_pricing(pdf, accent)
    _render_next_step(
        pdf,
        prospect_name=prospect_name or "",
        heading=next_heading,
        body=next_body,
        accent=accent,
        brand_profile=brand_profile,
        first_route=routes[0] if routes else None,
    )

    pdf.output(str(output_path))
    return output_path
