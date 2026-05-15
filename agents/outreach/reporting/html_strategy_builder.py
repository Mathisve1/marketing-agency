"""Premium HTML strategy / creative-research page renderer.

Takes a `StrategyBrief` and writes a single self-contained HTML file
to `<output_dir>/index.html`. The page is the long-form research
companion to the pitch microsite - shipped at:

    prospects/<id>/strategy/index.html         (local)
    build/pitches/p/<slug>/strategy/index.html (deploy package)

Design philosophy:
  - Dark editorial background (#0A0A0A), warm cream cards (#F5F0E8) -
    matches the pitch microsite so a prospect navigating between the
    two surfaces never feels they've crossed a brand seam.
  - Section hierarchy is deliberately long-form: 9 sections, each with
    its own eyebrow + index. Reads as a private research deliverable,
    not as a stacked-slide pitch deck.
  - Evidence chips on every market-context entry so the prospect can
    tell at a glance which lines are grounded in the audit and which
    are working hypotheses we would validate before scaling.
  - Inline CSS + tiny vanilla JS only. No build step, no external
    scripts. The folder drops into Cloudflare Pages as-is.
  - Each image asset is used at most once across the page (we track a
    deck-wide `used_paths` set; the strategy_brief already dedupes).
  - Print CSS hides interactive chrome so Cmd/Ctrl+P produces a clean
    handout. `prefers-reduced-motion: reduce` disables reveal motion.

Public API:

    build_strategy_html(
        brief: StrategyBrief,
        *,
        output_dir: Optional[Path] = None,
        noindex: bool = True,
        status: Optional[str] = None,
        public_url: Optional[str] = None,
    ) -> Path
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Optional

from agents.outreach.reporting.strategy_brief import (
    AvoidRoute,
    CompetitorAdProof,
    CompetitorIntel,
    CreativePattern,
    HookTerritory,
    MarketSignal,
    OpportunityMap,
    PatternValidationGap,
    RouteIdea,
    SprintRecommendation,
    StrategyAdPattern,
    StrategyBrief,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def build_strategy_html(
    brief: StrategyBrief,
    *,
    output_dir: Optional[Path] = None,
    noindex: bool = True,
    status: Optional[str] = None,
    public_url: Optional[str] = None,
) -> Path:
    """Render `brief` to `<output_dir>/index.html` and return the path.

    Args:
      brief: structured strategy content (built via
        `StrategyBrief.from_audit(prospect_id)` for live prospects).
      output_dir: explicit output folder; defaults to
        `brief.prospect_root / "strategy"`.
      noindex: when True (the default for the private strategy page),
        injects `<meta name="robots" content="noindex,nofollow">`.
      status: optional manifest deploy status; renders the same draft /
        live banner the pitch deck uses.
      public_url: shown alongside the 'Live' banner when status is
        'deployed'.
    """
    if brief.prospect_root is None and output_dir is None:
        raise ValueError(
            "build_strategy_html: brief.prospect_root is None and no "
            "output_dir given; nowhere to write the strategy page."
        )
    if output_dir is None:
        output_dir = brief.prospect_root / "strategy"  # type: ignore[union-attr]
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "index.html"

    asset_dir = brief.prospect_root  # Used by `_url_for()` to compute relative URLs.
    used_paths: set[Path] = set()  # belt-and-braces; brief already dedupes

    html_body = _render(
        brief,
        asset_dir=asset_dir,
        output_dir=output_dir,
        used=used_paths,
        noindex=noindex,
        status=status,
        public_url=public_url,
    )
    out_path.write_text(html_body, encoding="utf-8", newline="\n")
    log.info("html_strategy_builder: wrote %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path


# --------------------------------------------------------------------------- #
# HTML / asset helpers
# --------------------------------------------------------------------------- #


def _e(text: Optional[str]) -> str:
    """HTML-escape. Empty / None becomes empty string."""
    return html.escape(text or "", quote=True)


def _url_for(path: Optional[Path], output_dir: Path, asset_dir: Optional[Path]) -> Optional[str]:
    """Return a URL relative to `output_dir` for a local image path, or
    None when the asset isn't a real file. The strategy/ folder sits
    at `prospects/<id>/strategy/`, the assets at `prospects/<id>/assets/`,
    so the renderer emits `../assets/<file>` for files in that tree;
    `assets/<file>` for files copied inside `strategy/assets/`."""
    if path is None:
        return None
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    try:
        rel = path.resolve().relative_to(output_dir.resolve())
        return rel.as_posix()
    except ValueError:
        pass
    # Try relative to the prospect root (assets sit alongside strategy/).
    if asset_dir is not None:
        try:
            rel_root = path.resolve().relative_to(asset_dir.resolve())
            # output_dir is typically `<prospect_root>/strategy`; we step
            # up one level for the assets folder.
            try:
                up = output_dir.resolve().relative_to(asset_dir.resolve())
                depth = len(up.parts)
            except ValueError:
                depth = 1
            prefix = "/".join([".."] * depth) if depth else ""
            return (prefix + "/" if prefix else "") + rel_root.as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _accent_for_dark_bg(hex_color: Optional[str]) -> str:
    """Pick a brand-accent colour bright enough to read on the dark
    editorial background. Matches the pitch microsite helper of the
    same name; duplicated here so the strategy builder has no hard
    dependency on `html_deck_builder` internals."""
    if not isinstance(hex_color, str) or len(hex_color) < 4:
        return "#F5F0E8"
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return "#F5F0E8"
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return "#F5F0E8"
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    if brightness >= 140:
        return f"#{r:02X}{g:02X}{b:02X}"
    cream = (245, 240, 232)
    for ratio in (0.45, 0.6, 0.75, 0.85):
        mr = int(r * (1 - ratio) + cream[0] * ratio)
        mg = int(g * (1 - ratio) + cream[1] * ratio)
        mb = int(b * (1 - ratio) + cream[2] * ratio)
        if (mr * 299 + mg * 587 + mb * 114) / 1000 >= 140:
            return f"#{mr:02X}{mg:02X}{mb:02X}"
    return "#F5F0E8"


def _confidence_chip(conf: str) -> str:
    """Small uppercase chip for the route-library confidence column."""
    label = (conf or "").lower()
    if label not in {"high", "medium", "low"}:
        return ""
    return (
        f'<span class="route__conf route__conf--{label}">'
        f'{label.upper()}'
        '</span>'
    )


# --------------------------------------------------------------------------- #
# Top-level render
# --------------------------------------------------------------------------- #


def _render(
    brief: StrategyBrief,
    *,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
    noindex: bool,
    status: Optional[str],
    public_url: Optional[str],
) -> str:
    css = _stylesheet(brief)
    js = _javascript()

    sections = [
        _section_cover(brief, asset_dir, output_dir, used),
        _section_executive_summary(brief),
        _section_market_context(brief),
        _section_competitors(brief, asset_dir, output_dir, used),
        _section_creative_patterns(brief, asset_dir, output_dir, used),
        _section_patterns_to_validate(brief),
        _section_ad_patterns(brief, asset_dir, output_dir, used),
        _section_hook_map(brief),
        _section_opportunities(brief),
        _section_route_library(brief),
        _section_avoid_routes(brief),
        _section_sprint(brief),
        _section_next_step(brief),
    ]

    title = f"{_e(brief.brand_name)} - Creative Strategy Map"
    robots_meta = (
        '<meta name="robots" content="noindex,nofollow">' if noindex else ""
    )
    # Strategy is a client deliverable - we deliberately do NOT render
    # the draft/live banner the pitch microsite uses. `status` and
    # `public_url` are kept on the signature for API parity with the
    # pitch `build_html_deck`, but neither reaches the rendered page,
    # so the client never sees "Local draft preview" or "Live" markers.
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{robots_meta}
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Inter+Tight:wght@500;600;700&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body class="strategy">
<div class="progress-bar" aria-hidden="true"><span class="progress-bar__fill"></span></div>
<header class="topbar">
  <span class="topbar__kicker">CREATIVE STRATEGY MAP &middot; CLIENT DELIVERABLE</span>
  <span class="topbar__brand">{_e(brief.brand_name)} &times; {_e(brief.agency_name)}</span>
</header>
<main class="strategy__deck">
{"".join(sections)}
</main>
<footer class="footer">
  <span>{_e(brief.agency_name)} &mdash; private strategy note</span>
  <span>Prepared for {_e(brief.brand_name)}</span>
</footer>
<script>{js}</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# Status banner intentionally OMITTED on the strategy page
# ---------------------------------------------------------------------------
# The pitch microsite renders a draft/live banner via `html_deck_builder.
# _status_banner` for operator review. The strategy page is the client
# deliverable: any operator-facing status chrome (`Local draft preview`,
# `not deployed yet`, `Live`) is suppressed here so the page reads as a
# finished post-purchase document. The `status` and `public_url` kwargs
# on `build_strategy_html` stay accepted (for API parity) but the
# renderer never writes them into the DOM.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


def _section_cover(
    brief: StrategyBrief,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
) -> str:
    """Section 1 - cover. Big title 'Creative Strategy Map', subhead
    naming the brand, brand-mark chip, niche line, optional hero image.

    Brand mark selection:
      1. If `brief.logo_path` resolves to a file >= LOGO_MIN_USABLE_BYTES
         (currently 4 KB), embed it as a small `<img>` on the cover.
         This is the only path that allows a real logo asset onto the
         page; we deliberately reject tiny 32x32 favicons because
         scaling them up reads as broken on a paid deliverable.
      2. Otherwise render a clean lowercase WORDMARK derived from the
         brand's first significant word (e.g. 'pai' from 'Pai
         Skincare'). We never fall back to two-letter initials
         ('PS') - 'PS' looks like a placeholder, 'pai' reads as a
         deliberate wordmark.
    """
    brand_mark_html = _render_brand_mark(brief, asset_dir, output_dir, used)
    hero_url = _url_for(brief.hero_image_path, output_dir, asset_dir)
    if brief.hero_image_path is not None and brief.hero_image_path.is_file():
        used.add(brief.hero_image_path.resolve())
    hero_markup = (
        f'<div class="strategy-cover__hero"><img src="{_e(hero_url)}" alt="" loading="eager"></div>'
        if hero_url
        else '<div class="strategy-cover__hero strategy-cover__hero--mock" aria-hidden="true">'
             '<span class="strategy-cover__hero-frame"></span>'
             '<span class="strategy-cover__hero-orb"></span>'
             '</div>'
    )
    audience = brief.audience_assumption or ""
    category = brief.product_category or brief.niche or ""

    return f"""
<section class="section strategy-cover" id="cover" data-slide="cover" data-reveal>
  <div class="strategy-cover__layout">
    <div class="strategy-cover__copy">
      {brand_mark_html}
      <span class="strategy-cover__eyebrow">CREATIVE STRATEGY MAP &middot; PREPARED AFTER THE FIRST CREATIVE AUDIT</span>
      <h1 class="strategy-cover__title">Creative Strategy Map</h1>
      <p class="strategy-cover__subhead">{_e(brief.cover_subhead)}</p>
      <dl class="strategy-cover__meta">
        <div><dt>Brand</dt><dd>{_e(brief.brand_name)}</dd></div>
        <div><dt>Category</dt><dd>{_e(category)}</dd></div>
        {('<div><dt>Audience</dt><dd>' + _e(audience) + '</dd></div>') if audience else ''}
      </dl>
    </div>
    {hero_markup}
  </div>
</section>
"""


# Minimum file size in bytes before we treat a logo asset as good
# enough to embed on the cover. A 32x32 PNG favicon is typically
# 500-1000 bytes; anything below this floor is rejected so we don't
# scale a tiny favicon into a hero brand mark.
LOGO_MIN_USABLE_BYTES = 4 * 1024


def _render_brand_mark(
    brief: StrategyBrief,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
) -> str:
    """Render the cover brand mark. See `_section_cover` docstring for
    the selection policy. Returns a single inline element.
    """
    logo_url: Optional[str] = None
    logo_path = brief.logo_path
    if logo_path is not None:
        try:
            if logo_path.is_file() and logo_path.stat().st_size >= LOGO_MIN_USABLE_BYTES:
                logo_url = _url_for(logo_path, output_dir, asset_dir)
                if logo_url:
                    used.add(logo_path.resolve())
        except OSError:
            logo_url = None
    if logo_url:
        return (
            '<span class="strategy-cover__brand-mark strategy-cover__brand-mark--logo">'
            f'<img class="strategy-cover__logo-img" src="{_e(logo_url)}" '
            f'alt="{_e(brief.brand_name)} logo" loading="eager">'
            '</span>'
        )
    wordmark = _wordmark_for(brief.brand_name)
    return (
        '<span class="strategy-cover__brand-mark strategy-cover__brand-mark--wordmark">'
        f'<span class="strategy-cover__wordmark">{_e(wordmark)}</span>'
        '</span>'
    )


# Stop-words that should never become the wordmark on their own.
# "By" / "London" / "Skincare" / "Beauty" / "Organic" etc. would
# produce nonsense wordmarks like "London" for "Aurelia London".
_WORDMARK_STOP_WORDS = frozenset({
    "the", "a", "an", "by", "of", "and", "for", "co", "ltd", "inc",
    "uk", "usa", "us", "gb",
    "skincare", "skin", "beauty", "london", "organic", "clean",
    "natural", "studio", "labs",
})


def _wordmark_for(brand_name: str) -> str:
    """Derive a clean lowercase wordmark from a brand name.

    Picks the first non-stop-word token from the name and lowercases
    it. Falls back to the whole name (lowercased) when every token is
    a stop-word - we never collapse to a two-letter monogram.
    """
    import re as _re
    tokens = _re.findall(r"[A-Za-z0-9]+", brand_name or "")
    for tok in tokens:
        if tok.lower() not in _WORDMARK_STOP_WORDS:
            return tok.lower()
    # Fall back to the full lowercased name (rare).
    return (brand_name or "").strip().lower() or "brand"


def _section_executive_summary(brief: StrategyBrief) -> str:
    """Section 2 - three top-of-page summary cards."""
    cards = "".join(
        f"""
<article class="exec-card" data-reveal data-reveal-index="{i}">
  <span class="exec-card__kicker">{_e(c.label)}</span>
  <p class="exec-card__body">{_e(c.body)}</p>
</article>"""
        for i, c in enumerate(brief.executive_cards)
    )
    return f"""
<section class="section" id="exec-summary" data-slide="2" data-reveal>
  <div class="section__meta"><span class="section__index">02</span><span>Executive summary</span></div>
  <h2 class="section__title">Three cards before the long read.</h2>
  <p class="section__lede">What the current ads rely on, where the creative gap is, and what the first production sprint produces. The rest of the page is the working detail behind these three calls.</p>
  <div class="exec-cards">{cards}</div>
</section>
"""


def _section_market_context(brief: StrategyBrief) -> str:
    """Section 3 - market context. Each line is chipped as either
    `PAI AUDIT` (grounded in the prospect brief) or
    `STRATEGY HYPOTHESIS` (strategist's working assumption) so the
    reader can tell at a glance what we would validate before scaling.
    """
    if not brief.market_context:
        body = (
            '<p class="market__empty">No grounded market signals are on file for '
            'this brief - the rest of the page is framed as working strategy.</p>'
        )
    else:
        rows = "".join(_render_market_signal(s, i) for i, s in enumerate(brief.market_context))
        body = f'<div class="market">{rows}</div>'

    return f"""
<section class="section section--cream" id="market" data-slide="3" data-reveal>
  <div class="section__meta"><span class="section__index">03</span><span>Market context</span></div>
  <h2 class="section__title">The shape of the room.</h2>
  <p class="section__lede">Lines chipped <em>PAI AUDIT</em> are grounded in the brief we built with you. Lines chipped <em>STRATEGY HYPOTHESIS</em> are the strategist&rsquo;s working read on the category &mdash; we would validate them before committing budget.</p>
  {body}
</section>
"""


def _render_market_signal(s: MarketSignal, idx: int) -> str:
    chip_label = "PAI AUDIT" if s.evidence == "audit" else "STRATEGY HYPOTHESIS"
    chip_cls = "market__chip--audit" if s.evidence == "audit" else "market__chip--hyp"
    return f"""
<article class="market__row" data-reveal data-reveal-index="{idx}">
  <span class="market__chip {chip_cls}">{chip_label}</span>
  <div class="market__copy">
    <h3 class="market__title">{_e(s.title)}</h3>
    <p class="market__body">{_e(s.body)}</p>
  </div>
</article>"""


# --------------------------------------------------------------------------- #
# Competitor intelligence + creative patterns + avoid routes
# --------------------------------------------------------------------------- #


def _evidence_chip(label: str) -> str:  # noqa: D401
    # Client-facing chip taxonomy. The internal labels stay on the
    # data model; we map them to client-safe strings at render time.
    """Small uppercase chip used across competitor / pattern / avoid
    cards. Maps the brief's internal evidence label to a client-safe
    presentation chip.

    Allowed client-facing labels:
      PAI AUDIT             -> grounded in the prospect's brief
      COMPETITOR ADS        -> backed by live competitor ad creative
      CATEGORY RESEARCH     -> reasoned from public category context
      STRATEGY HYPOTHESIS   -> strategist's working hypothesis
      MONITOR NEXT          -> worth tracking before producing
      COMPLIANCE CAUTION    -> compliance-flagged angle
      SPRINT PRIORITY       -> first-sprint pick
    """
    presets = {
        "audit": ("PAI AUDIT", "ev-chip--audit"),
        "competitor_ad_evidence": ("COMPETITOR ADS", "ev-chip--audit"),
        "web_research": ("CATEGORY RESEARCH", "ev-chip--research"),
        "research": ("CATEGORY RESEARCH", "ev-chip--research"),
        "hypothesis": ("STRATEGY HYPOTHESIS", "ev-chip--hyp"),
        "needs_validation": ("MONITOR NEXT", "ev-chip--avoid"),
        "low_evidence": ("MONITOR NEXT", "ev-chip--avoid"),
        "compliance_risk": ("COMPLIANCE CAUTION", "ev-chip--avoid"),
        "off_brand_fit": ("COMPLIANCE CAUTION", "ev-chip--avoid"),
        "validate_later": ("MONITOR NEXT", "ev-chip--later"),
    }
    text, cls = presets.get(label, (label.upper().replace("_", " "), "ev-chip--hyp"))
    return f'<span class="ev-chip {cls}">{text}</span>'


def _confidence_dot(conf: str) -> str:
    """Small relevance chip inside competitor cards.

    Client framing: we surface a market-relevance read for each
    competitor, not an internal confidence score. Maps the operator
    taxonomy `high/medium/low` to client-safe labels:
      high   -> "DIRECT BENCHMARK"
      medium -> "CATEGORY PEER"
      low    -> "ADJACENT BRAND"
    """
    c = (conf or "").lower()
    label_map = {
        "high":   "DIRECT BENCHMARK",
        "medium": "CATEGORY PEER",
        "low":    "ADJACENT BRAND",
    }
    label = label_map.get(c)
    if label is None:
        return ""
    return f'<span class="conf-chip conf-chip--{c}">{label}</span>'


def _section_competitors(
    brief: StrategyBrief,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
) -> str:
    """Section - competitor intelligence board (proof-only main grid).

    Main grid rule:
      The main competitor intelligence grid renders ONLY competitors
      whose `sampled_ads` is non-empty. Brands the operator listed in
      research but for whom we did not capture a qualifying active
      Meta ad are moved into the secondary "Relevant brands checked
      but not included" note at the bottom of the section.

    Per-card content:
      Each included card carries:
        - the COMPETITOR ADS chip + confidence chip
        - the why-relevant / positioning / creative-pattern / learn dl
        - a 3-5 thumbnail proof rail with per-ad `View sampled ad`
          chips + ID labels
        - an Open website + Open Meta ads chip rail
        - a `+N more sampled ads` overflow note when more captures
          exist than the visible cap.
    """
    competitors_all = list(brief.competitors)
    included = [c for c in competitors_all if len(c.sampled_ads) > 0]
    excluded = [c for c in competitors_all if len(c.sampled_ads) == 0]

    if not included:
        # Defensive: if zero competitors have captured proof, render a
        # quiet empty state instead of leaving the section blank.
        # Excluded brands still surface in the secondary note.
        body = (
            '<p class="competitors__empty">We did not surface paid-social '
            "evidence for any of the relevant category brands this pass. "
            "The secondary list below names the brands we considered.</p>"
        )
    else:
        cards = "".join(
            _render_competitor_card(c, idx, asset_dir, output_dir, used)
            for idx, c in enumerate(included)
        )
        body = f'<div class="competitors">{cards}</div>'

    excluded_block = _render_excluded_competitors_note(excluded)
    n_included = len(included)
    summary = (
        f"{n_included} competitor{'s' if n_included != 1 else ''} with "
        "active paid-social ad creative on file."
    )
    return f"""
<section class="section" id="competitors" data-slide="competitors" data-reveal>
  <div class="section__meta"><span class="section__index">04</span><span>Competitor intelligence</span></div>
  <h2 class="section__title">Who else is fighting for this buyer.</h2>
  <p class="section__lede">{summary} The grid only lists brands whose active Meta ads we have on file and you can verify directly in the Ad Library. Each card surfaces the angle the brand appears to own, the pattern its paid creative leans on, the one thing {_e(brief.brand_name)} can learn from it safely, and a short strip of the live ads.</p>
  {body}
  {excluded_block}
</section>
"""


def _render_excluded_competitors_note(
    excluded: list[CompetitorIntel],
) -> str:
    """Client-facing note for relevant category brands we did NOT
    surface in the main competitor grid.

    The internal taxonomy distinguishes between several capture-side
    outcomes (no active ads, page-name mismatch, screenshot failure,
    block, network error, never attempted). For the client deliverable
    those distinctions read as system noise. We collapse them into two
    client-safe reasons:

      - "Not enough qualifying paid-social proof" - we looked but did
        not surface ad creative for this brand. Covers no_active_ads,
        false_positive, playwright_failed, blocked, scrape_failed.
      - "Kept out of the main grid for evidence discipline" - we have
        research on the brand but no captured ad to show. Covers the
        "never attempted" case (capture_status is None).

    The internal status codes stay on the data model for the audit
    trail; they just never render.
    """
    if not excluded:
        return ""
    rows: list[str] = []
    for c in excluded:
        cs = (c.capture_status or "").strip()
        if cs:
            why = (
                "Relevant category brand. Not enough qualifying "
                "paid-social proof to include in the main grid this pass."
            )
        else:
            why = (
                "Relevant category brand. Kept out of the main grid for "
                "evidence discipline until we have a verifiable ad to show."
            )
        meta_link = (
            f' &middot; <a class="excluded-competitors__link" '
            f'href="{_e(c.meta_ads_url)}" target="_blank" rel="noopener">'
            f'See live ads &rsaquo;</a>'
            if c.meta_ads_url else ''
        )
        rows.append(
            f'<li class="excluded-competitors__row">'
            f'<span class="excluded-competitors__name">{_e(c.name)}</span>'
            f'<span class="excluded-competitors__reason">{_e(why)}</span>'
            f'{meta_link}'
            f'</li>'
        )
    return f"""
<aside class="excluded-competitors" aria-label="Category brands not included in the paid-social comparison">
  <header class="excluded-competitors__head">
    <span class="excluded-competitors__kicker">Also checked</span>
    <span class="excluded-competitors__hint">Relevant category brands kept out of the main grid this pass &mdash; we did not have enough paid-social proof to include them with the same evidence bar.</span>
  </header>
  <ul class="excluded-competitors__list">{''.join(rows)}</ul>
</aside>"""


def _render_competitor_card(
    c: CompetitorIntel,
    idx: int,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
) -> str:
    """Premium competitor card with optional ad-proof strip.

    Layout: brand name + evidence chip + confidence chip + why-relevant
    paragraph + meta dl + (when ad proof exists) a screenshot strip
    with per-ad library links + "Open profile" + "Open Meta ads" chips.
    """
    # Always surface the website link when we have one.
    site_chip = (
        f'<a class="competitor__chip-link" href="{_e(c.website_url)}" '
        f'target="_blank" rel="noopener">Open website &rsaquo;</a>'
        if c.website_url else ''
    )
    # Meta-ads search URL (per-competitor). Only emit when we have one
    # AND the competitor was something we actually looked up - the
    # default-set / hypothesis competitors don't ship a Meta URL.
    meta_chip = (
        f'<a class="competitor__chip-link competitor__chip-link--accent" '
        f'href="{_e(c.meta_ads_url)}" target="_blank" rel="noopener">'
        f'Open Meta ads &rsaquo;</a>'
        if c.meta_ads_url else ''
    )

    # Evidence-chip label - the section-level computed `evidence_level`
    # takes priority over the older `evidence` field so the renderer
    # can show "COMPETITOR ADS" / "NEEDS VALIDATION" when applicable.
    ev_label = c.evidence_level or c.evidence

    # Ad-count chip. The main competitor grid only renders rows with
    # captured ads (`_section_competitors` enforces this), so this chip
    # is always shown.
    n_ads = len(c.sampled_ads)
    ad_count_chip = (
        f'<span class="competitor__ad-count" aria-label="{n_ads} active ads on file">'
        f'<span class="competitor__ad-count-num">{n_ads}</span>'
        f'<span class="competitor__ad-count-label">ACTIVE ADS</span>'
        f'</span>'
    )

    # Sampled ad strip. Cap the visible rail at 5 thumbnails so 5
    # competitor cards × 5 ads remains scannable without dominating
    # the page. Remaining ads stay in the JSON and surface as a
    # `+N more` overflow label.
    visible_cap = 5
    visible_ads = list(c.sampled_ads)[:visible_cap]
    overflow = max(0, n_ads - len(visible_ads))
    proof_block = _render_competitor_ad_strip(
        sampled_ads=visible_ads,
        asset_dir=asset_dir,
        output_dir=output_dir,
        used=used,
        overflow=overflow,
    )

    # Honest "no-proof" affordance. The main grid only renders cards
    # with sampled ads, so we should never reach the no-proof path
    # here - but keep the defensive fallback for tests that build a
    # synthetic brief directly.
    if not proof_block:
        proof_block = _render_no_proof_note(
            capture_status=c.capture_status,
            ev_label=ev_label,
            has_meta_url=bool(c.meta_ads_url),
        )

    # Footer chip rail (always present so the layout stays consistent).
    chip_rail = (
        f'<div class="competitor__chip-rail">{site_chip}{meta_chip}</div>'
        if (site_chip or meta_chip) else ''
    )

    return f"""
<article class="competitor" data-reveal data-reveal-index="{idx}">
  <header class="competitor__head">
    <div class="competitor__title-row">
      <span class="competitor__idx">{idx + 1:02d}</span>
      <h3 class="competitor__name">{_e(c.name)}</h3>
    </div>
    <div class="competitor__chips">
      {_evidence_chip(ev_label)}
      {ad_count_chip}
      {_confidence_dot(c.confidence)}
    </div>
  </header>
  <p class="competitor__why">{_e(c.why_relevant)}</p>
  <dl class="competitor__meta">
    <div><dt>Positioning angle</dt><dd>{_e(c.positioning_angle)}</dd></div>
    <div><dt>Creative pattern</dt><dd>{_e(c.creative_pattern)}</dd></div>
    <div><dt>What we&apos;d learn</dt><dd>{_e(c.pai_can_learn)}</dd></div>
  </dl>
  {proof_block}
  {chip_rail}
</article>"""


def _render_no_proof_note(
    *,
    capture_status: Optional[str],
    ev_label: Optional[str],
    has_meta_url: bool,
) -> str:
    """Quiet inline note shown on competitor cards that have no
    paid-social proof to attach yet.

    Client framing: we never speak about scrape failures, screenshot
    timeouts, or page-name mismatches. The card explains the gap as a
    deliberate evidence choice and points to the live library so the
    client can see for themselves.
    """
    # `ev_label` is informational only - the wording does not branch
    # on it any more.
    _ = capture_status, ev_label
    note = (
        "Relevant category brand. We are not surfacing paid-social "
        "evidence for this competitor in this pass, to keep the main "
        "grid to brands with verifiable active ad creative."
    )
    cta = (
        "See live ads for an independent view of what they are running."
        if has_meta_url else
        "Worth a follow-up in the next evidence pass."
    )
    return (
        '<div class="competitor__no-proof" role="note">'
        '<span class="competitor__no-proof-label">Paid-social proof not surfaced this pass</span>'
        f'<p>{_e(note)} {_e(cta)}</p>'
        '</div>'
    )


def _render_competitor_ad_strip(
    *,
    sampled_ads: list[CompetitorAdProof],
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
    overflow: int = 0,
) -> str:
    """Inline mini-strip of up to 5 active ads. Returns empty string
    when no captured ad carries a screenshot or library URL.

    Each tile shows a thumbnail (when available) and a `Open in Ad
    Library` chip that links to the public Meta Ads Library URL. When
    only the library URL is known, a designed cream placeholder tile
    keeps the strip layout consistent.

    `overflow` is the number of additional active ads on file beyond
    the visible cap. When > 0 the strip appends a small `+N more
    active ads` chip so the reader can see the evidence base is
    deeper than the thumbnails.

    Note: the underlying `ad_archive_id` is NOT rendered as a visible
    label - it would read as a system identifier. Each tile is
    identified by competitor and a sequential number instead.
    """
    visible = [
        ad for ad in sampled_ads
        if ad.screenshot_path is not None or ad.ad_library_url
    ]
    if not visible:
        return ''
    tiles: list[str] = []
    for tile_idx, ad in enumerate(visible, start=1):
        if ad.screenshot_path is not None:
            url = _url_for(ad.screenshot_path, output_dir, asset_dir)
            if url:
                used.add(Path(ad.screenshot_path))
                thumb = (
                    f'<img class="ad-proof__img" src="{_e(url)}" '
                    f'alt="Live ad creative for {_e(ad.competitor_name)}" '
                    f'loading="lazy">'
                )
            else:
                thumb = '<div class="ad-proof__placeholder" aria-hidden="true">AD</div>'
        else:
            thumb = '<div class="ad-proof__placeholder" aria-hidden="true">AD</div>'
        open_ad = (
            f'<a class="ad-proof__open" href="{_e(ad.ad_library_url)}" '
            f'target="_blank" rel="noopener">Open in Ad Library &rsaquo;</a>'
            if ad.ad_library_url else ''
        )
        tiles.append(f"""
<div class="ad-proof__tile">
  <div class="ad-proof__media">{thumb}</div>
  <div class="ad-proof__meta">
    <span class="ad-proof__id">Ad {tile_idx:02d}</span>
    {open_ad}
  </div>
</div>""")
    body = "".join(tiles)
    total = len(visible) + max(0, overflow)
    count_label = (
        f'{total} active ad' + ('' if total == 1 else 's')
    )
    overflow_tile = (
        f'<div class="ad-proof__more" aria-label="{overflow} more active ads on file">'
        f'<span class="ad-proof__more-num">+{overflow}</span>'
        f'<span class="ad-proof__more-label">more active ads</span>'
        '</div>'
    ) if overflow > 0 else ''
    return f"""
<div class="ad-proof">
  <div class="ad-proof__head">
    <span class="ad-proof__label">Live ads on file</span>
    <span class="ad-proof__count">{count_label}</span>
  </div>
  <div class="ad-proof__strip">{body}{overflow_tile}</div>
</div>"""


def _section_creative_patterns(
    brief: StrategyBrief,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
) -> str:
    """Section - competitor creative-pattern board (evidence-first).

    Only patterns that cleared the Section-05 bar render here:
      - at least 2 unique sampled ads after de-duplication
      - no ad_archive_id duplicated across multiple cards
    Patterns below the bar surface in the sibling
    `_section_patterns_to_validate()` instead.
    """
    validated = list(brief.validated_patterns or [])
    if not validated:
        body = (
            '<p class="patterns__empty">We are not surfacing a paid-social '
            "pattern board this pass. The signals worth monitoring before "
            "production are listed in the next section.</p>"
        )
    else:
        cards = "".join(
            _render_pattern_card(p, idx, asset_dir, output_dir, used)
            for idx, p in enumerate(validated)
        )
        body = f'<div class="patterns">{cards}</div>'
    n_total = len(brief.creative_patterns)
    n_validated = len(validated)
    summary = (
        f"{n_validated} of {n_total} pattern{'s' if n_total != 1 else ''} "
        "are backed by at least two distinct live ads."
    )
    return f"""
<section class="section section--cream" id="creative-patterns" data-slide="patterns" data-reveal>
  <div class="section__meta"><span class="section__index">05</span><span>Competitor creative patterns</span></div>
  <h2 class="section__title">The patterns the market is running on.</h2>
  <p class="section__lede">Each card carries live ad creative you can verify directly. {summary} Patterns with thinner evidence sit in the next section, marked for the production team to monitor before we commit budget.</p>
  {body}
</section>
"""


def _section_patterns_to_validate(brief: StrategyBrief) -> str:
    """Section 05B - "Patterns to validate next".

    Houses every pattern that did NOT clear the Section-05 two-ad bar,
    each wrapped with a `PatternValidationGap` carrying the diagnostic
    (why not promoted) and the recommended action. Three buckets:
      - `no_evidence`   -> never captured an ad backing this pattern
      - `single_ad`     -> one supporting ad, below the two-ad minimum
      - `lost_to_dedup` -> supporting ads were claimed by a more-specific
                           pattern card to avoid duplicate proof

    When every pattern clears the bar (the happy case) the section is
    omitted entirely.
    """
    gaps = list(brief.unvalidated_patterns or [])
    if not gaps:
        return ""

    cards: list[str] = []
    for idx, gap in enumerate(gaps):
        p = gap.pattern
        next_competitor = _pick_validation_lead(p, brief.competitors)

        if next_competitor is None:
            looking_at = ""
        elif next_competitor.meta_ads_url:
            looking_at = (
                f'<div><dt>Lead competitor to validate</dt>'
                f'<dd><a class="patterns-validate__link" '
                f'href="{_e(next_competitor.meta_ads_url)}" target="_blank" '
                f'rel="noopener">{_e(next_competitor.name)} '
                f'&middot; Open Meta ads &rsaquo;</a></dd></div>'
            )
        else:
            looking_at = (
                f'<div><dt>Lead competitor to validate</dt>'
                f'<dd>{_e(next_competitor.name)}</dd></div>'
            )

        what_we_need = (
            "A live ad whose copy or framing leads clearly with this "
            "pattern, ideally from more than one brand in the category."
        )

        # Reason-code chip + current evidence count chip.
        reason_chip_label, reason_chip_class = _reason_chip_for_gap(gap)
        ev_count_chip = (
            f'<span class="patterns-validate__count">'
            f'{gap.current_ad_count} live ad{"s" if gap.current_ad_count != 1 else ""} '
            f'/ {gap.current_competitor_count} '
            f'brand{"s" if gap.current_competitor_count != 1 else ""}'
            f'</span>'
        )

        # If the pattern was superseded by a more-specific card, name it.
        supersede_row = ""
        if gap.reason_code == "lost_to_dedup" and gap.superseded_by:
            supersede_row = (
                f'<div><dt>Already represented under</dt>'
                f'<dd>&laquo;{_e(gap.superseded_by)}&raquo; &mdash; the '
                f'live ads we have on file fit that card more cleanly.</dd></div>'
            )

        cards.append(f"""
<article class="patterns-validate__card" data-reveal data-reveal-index="{idx}">
  <header class="patterns-validate__head">
    <span class="patterns-validate__idx">{idx + 1:02d}</span>
    <h3 class="patterns-validate__name">{_e(p.name)}</h3>
    <span class="patterns-validate__reason {reason_chip_class}">{reason_chip_label}</span>
    {ev_count_chip}
  </header>
  <dl class="patterns-validate__meta">
    <div><dt>Why it is interesting</dt><dd>{_e(p.why_works)}</dd></div>
    <div><dt>Why not in sprint one</dt><dd>{_e(gap.reason_text)}</dd></div>
    {supersede_row}
    <div><dt>What would make it eligible</dt><dd>{_e(_missing_evidence_text(gap))}</dd></div>
    {looking_at}
    <div><dt>Proof we would want to see</dt><dd>{what_we_need}</dd></div>
    <div><dt>What we would do instead now</dt><dd><span class="patterns-validate__action patterns-validate__action--{gap.recommended_action}">{_action_chip_label(gap.recommended_action)}</span> &mdash; {_e(gap.action_text)}</dd></div>
  </dl>
</article>""")
    body = "".join(cards)
    return f"""
<section class="section" id="patterns-to-validate" data-slide="patterns-validate" data-reveal>
  <div class="section__meta"><span class="section__index">05B</span><span>Signals to monitor before production</span></div>
  <h2 class="section__title">Signals worth tracking, not worth producing against yet.</h2>
  <p class="section__lede">A strategist&rsquo;s queue: patterns we see in the market but would not commit budget to in sprint one. Each card explains why it is interesting, why it is not in sprint one, what proof would make it eligible later, and what we would do instead now.</p>
  <div class="patterns-validate">{body}</div>
</section>
"""


def _reason_chip_for_gap(gap: PatternValidationGap) -> tuple[str, str]:
    """Return (chip label, css class) for the Section 05B reason chip.

    Client-facing taxonomy:
      no_evidence   -> "FURTHER EVIDENCE TO COLLECT"
      single_ad     -> "EARLY SIGNAL"
      lost_to_dedup -> "ALREADY REPRESENTED"
    """
    code = gap.reason_code
    if code == "no_evidence":
        return ("FURTHER EVIDENCE TO COLLECT", "patterns-validate__reason--none")
    if code == "single_ad":
        return ("EARLY SIGNAL", "patterns-validate__reason--single")
    if code == "lost_to_dedup":
        return ("ALREADY REPRESENTED", "patterns-validate__reason--moved")
    return ("MONITOR NEXT", "patterns-validate__reason--none")


def _action_chip_label(action_code: str) -> str:
    """Client-safe action chip label. The internal codes are
    `scrape_more` / `validate_next` / `avoid_for_now`; we never show
    those words on the client page."""
    if action_code == "scrape_more":
        return "MONITOR NEXT"
    if action_code == "validate_next":
        return "MONITOR NEXT"
    if action_code == "avoid_for_now":
        return "COMPLIANCE CAUTION"
    return action_code.replace("_", " ").upper()


def _missing_evidence_text(gap: PatternValidationGap) -> str:
    """Concrete text for the 'what would make it eligible' row."""
    code = gap.reason_code
    if code == "no_evidence":
        return (
            "We would want at least two live ads in the category that lead "
            "clearly with this pattern before producing against it. Worth "
            "monitoring next pass."
        )
    if code == "single_ad":
        return (
            "We have an early signal but only one live ad to point at. A "
            "second ad - ideally from a different brand - would move this "
            "into the main board."
        )
    if code == "lost_to_dedup":
        return (
            "Every live ad that touches this pattern fits a more specific "
            "card above more cleanly, so we promoted the more specific "
            "card instead. A live ad that leads clearly with this pattern "
            "(not as a side note) would put it on the main board."
        )
    return (
        "Not enough live ad evidence in the category to prioritize this "
        "in sprint one."
    )


def _pick_validation_lead(
    pattern: CreativePattern,
    competitors,
) -> Optional[CompetitorIntel]:
    """Return the first `CompetitorIntel` in `pattern.who_uses` that we
    actually carry intelligence for. None when no match - the renderer
    omits the "look at" row.
    """
    by_name = {c.name: c for c in competitors}
    for candidate in pattern.who_uses:
        c = by_name.get(candidate)
        if c is not None:
            return c
    return None


def _pick_validation_action(pattern: CreativePattern) -> tuple[str, str]:
    """Return `(label, body)` for the recommended-action chip.

    Labels:
      "scrape_more"     -> the pattern is plausible but we need more data
      "validate_next"   -> the pattern is audit-grounded; capture a
                           proof ad in the next sprint
      "avoid_for_now"   -> compliance / off-brand risk; not sprint-one
    """
    name_lc = (pattern.name or "").lower()
    if "before" in name_lc and "after" in name_lc:
        return (
            "avoid_for_now",
            "Compliance bar on transformation claims is high - "
            "park until a documented case study and consent are in hand.",
        )
    if "discount" in name_lc or "transformation" in name_lc:
        return (
            "avoid_for_now",
            "Off-brand fit on cold prospecting. Reserve for retargeting "
            "tests, not for first-sprint cold creative.",
        )
    if pattern.evidence == "audit":
        return (
            "validate_next",
            "Audit-grounded hypothesis - capture one sampled competitor "
            "ad in the next outreach pass and promote into the main board.",
        )
    return (
        "scrape_more",
        "Plausible market angle - run a focused Meta Ads Library "
        "sweep on the named competitors before sprint two.",
    )


def _render_pattern_card(
    p: CreativePattern,
    idx: int,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
) -> str:
    """Pattern card with explicit ad-evidence area (proof-only chips).

    Decisioning:
      * If `ad_evidence` is non-empty, the pattern is verified - the
        chip is upgraded to COMPETITOR ADS and the user chips list ONLY
        the brands that actually have a captured ad for this pattern.
        Unverified `who_uses` names are deliberately NOT rendered so
        the card never names a brand we cannot point at with a sampled
        ad.
      * Pattern cards with no `ad_evidence` are filtered out of the
        main board by `_section_creative_patterns` and surface in the
        "Patterns to validate next" section instead; this renderer
        therefore expects `ad_evidence` to be non-empty in practice.
    """
    has_ads = len(p.ad_evidence) > 0

    # Verified brand names (those that actually have a sampled ad).
    verified_brands: list[str] = []
    seen_brand: set[str] = set()
    for ad in p.ad_evidence:
        if ad.competitor_name not in seen_brand:
            seen_brand.add(ad.competitor_name)
            verified_brands.append(ad.competitor_name)

    if has_ads:
        # Verified-only chips: only brands with a real sampled ad for
        # this pattern render here. Drops the unverified hardcoded
        # `who_uses` candidates so the card never names a brand we
        # cannot back up.
        users_html = "".join(
            f'<span class="pattern__user pattern__user--verified">'
            f'<span class="pattern__user-dot" aria-hidden="true"></span>{_e(name)}</span>'
            for name in verified_brands
        )
    else:
        # Defensive fallback for synthetic tests / unfiltered renders.
        users_html = "".join(
            f'<span class="pattern__user pattern__user--candidate">{_e(name)}</span>'
            for name in p.who_uses
        )

    # Chip selection: upgrade to COMPETITOR ADS if proof exists,
    # otherwise tone down a "hypothesis" pattern to NEEDS VALIDATION
    # so the language stays honest.
    if has_ads:
        chip_label = "competitor_ad_evidence"
    elif p.evidence == "hypothesis":
        chip_label = "needs_validation"
    else:
        chip_label = p.evidence

    # Ad evidence strip (only when proof exists). Up to 6 visible tiles
    # so a single pattern can carry meaningful proof depth; the rest
    # surface as a `+N more sampled ads` overflow chip.
    if has_ads:
        ads_all = list(p.ad_evidence)
        visible_cap = 6
        visible = ads_all[:visible_cap]
        overflow = max(0, len(ads_all) - len(visible))
        evidence_block = _render_pattern_evidence_strip(
            ads=visible,
            asset_dir=asset_dir,
            output_dir=output_dir,
            used=used,
            total_count=len(ads_all),
            overflow=overflow,
        )
    else:
        evidence_block = (
            '<div class="pattern__no-evidence">'
            '<span class="pattern__no-evidence-label">No sampled ads matched yet.</span>'
            '<p>This pattern is a working hypothesis. Cards above with verified '
            'ad screenshots carry the evidence we can point at.</p>'
            '</div>'
        )

    # Section-05 chips: unique competitor count + sampled ad count so
    # the reader can see proof depth at a glance.
    unique_comp_count = len({ad.competitor_name for ad in p.ad_evidence})
    unique_ad_count = len(p.ad_evidence)
    proof_meta = (
        f'<div class="pattern__proof-meta">'
        f'<span class="pattern__proof-chip">'
        f'<span class="pattern__proof-num">{unique_comp_count}</span>'
        f'<span class="pattern__proof-label">'
        f'brand{"s" if unique_comp_count != 1 else ""} running it</span>'
        f'</span>'
        f'<span class="pattern__proof-chip">'
        f'<span class="pattern__proof-num">{unique_ad_count}</span>'
        f'<span class="pattern__proof-label">live '
        f'ad{"s" if unique_ad_count != 1 else ""}</span>'
        f'</span>'
        f'</div>'
    ) if has_ads else ''

    return f"""
<article class="pattern" data-reveal data-reveal-index="{idx}">
  <header class="pattern__head">
    <span class="pattern__idx">{idx + 1:02d}</span>
    <h3 class="pattern__name">{_e(p.name)}</h3>
    {_evidence_chip(chip_label)}
  </header>
  {proof_meta}
  <div class="pattern__users">{users_html}</div>
  <dl class="pattern__meta">
    <div><dt>Why it works</dt><dd>{_e(p.why_works)}</dd></div>
    <div><dt>What the brand does today</dt><dd>{_e(p.brand_status)}</dd></div>
    <div><dt>Safe adaptation</dt><dd>{_e(p.safe_adaptation)}</dd></div>
    <div><dt>Risk to manage</dt><dd>{_e(p.risk)}</dd></div>
  </dl>
  {evidence_block}
</article>"""


def _render_pattern_evidence_strip(
    *,
    ads: list[CompetitorAdProof],
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
    total_count: Optional[int] = None,
    overflow: int = 0,
) -> str:
    """Pattern-evidence block. Tile per matching ad, grouped by
    competitor so a card that pulls from 2 brands reads as 'REN +
    Aurelia' not 'four random ads'.

    `total_count` (when set) is the underlying count of matching ads
    BEFORE the visible cap was applied; the count label uses it so the
    reader sees the full evidence pool. `overflow` is the count of
    matching ads beyond the visible cap; when > 0 the strip appends a
    `+N more sampled ads` chip so the reader knows the proof base is
    deeper than the visible tiles.
    """
    if not ads:
        return ''
    tiles: list[str] = []
    for ad in ads:
        if ad.screenshot_path is not None:
            url = _url_for(ad.screenshot_path, output_dir, asset_dir)
            if url:
                used.add(Path(ad.screenshot_path))
                thumb = (
                    f'<img class="pattern-evidence__img" src="{_e(url)}" '
                    f'alt="Live ad creative for {_e(ad.competitor_name)}" '
                    f'loading="lazy">'
                )
            else:
                thumb = (
                    '<div class="pattern-evidence__placeholder" aria-hidden="true">AD</div>'
                )
        else:
            thumb = '<div class="pattern-evidence__placeholder" aria-hidden="true">AD</div>'
        open_ad = (
            f'<a class="pattern-evidence__open" href="{_e(ad.ad_library_url)}" '
            f'target="_blank" rel="noopener">Open in Ad Library &rsaquo;</a>'
            if ad.ad_library_url else ''
        )
        tiles.append(f"""
<figure class="pattern-evidence__tile">
  <div class="pattern-evidence__media">{thumb}</div>
  <figcaption class="pattern-evidence__caption">
    <span class="pattern-evidence__brand">{_e(ad.competitor_name)}</span>
    {open_ad}
  </figcaption>
</figure>""")
    n_total = total_count if total_count is not None else len(ads)
    overflow_tile = (
        f'<div class="pattern-evidence__more" '
        f'aria-label="{overflow} more active ads back this pattern">'
        f'<span class="pattern-evidence__more-num">+{overflow}</span>'
        f'<span class="pattern-evidence__more-label">more active ads</span>'
        '</div>'
    ) if overflow > 0 else ''
    return f"""
<div class="pattern-evidence">
  <div class="pattern-evidence__head">
    <span class="pattern-evidence__label">Live ads backing this pattern</span>
    <span class="pattern-evidence__count">{n_total} active ad{'s' if n_total != 1 else ''}</span>
  </div>
  <div class="pattern-evidence__strip">{"".join(tiles)}{overflow_tile}</div>
</div>"""


def _section_ad_patterns(
    brief: StrategyBrief,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
) -> str:
    """Section 4 - competitor / ad pattern board.

    One row per sampled ad. Real screenshot when present; a tasteful
    Meta-shaped mock when not. Each row carries pattern + weakness +
    opportunity columns and an 'Open ad' chip linking to the Meta Ads
    Library.
    """
    if not brief.ad_patterns:
        body = (
            f'<p class="ad-board__empty">No live ads for {_e(brief.brand_name)} '
            "are on file in this pass. The hook map and route library below "
            "carry the rest of the recommendation.</p>"
        )
    else:
        rows = "".join(
            _render_ad_pattern(p, idx, asset_dir, output_dir, used, brief.brand_name)
            for idx, p in enumerate(brief.ad_patterns)
        )
        body = f'<div class="ad-board">{rows}</div>'
    return f"""
<section class="section" id="ad-board" data-slide="4" data-reveal>
  <div class="section__meta"><span class="section__index">06</span><span>Own-ad pattern board</span></div>
  <h2 class="section__title">What {_e(brief.brand_name)}&rsquo;s live library is doing.</h2>
  <p class="section__lede">One row per live ad with the pattern we observed, the weakness limiting it, and the opportunity we would test against it. This is the audit-grounded read that drives the sprint plan further down.</p>
  {body}
</section>
"""


def _render_ad_pattern(
    p: StrategyAdPattern,
    idx: int,
    asset_dir: Optional[Path],
    output_dir: Path,
    used: set[Path],
    brand_name: str,
) -> str:
    shot_url = _url_for(p.screenshot_path, output_dir, asset_dir)
    if p.screenshot_path is not None and p.screenshot_path.is_file():
        used.add(p.screenshot_path.resolve())
    if shot_url:
        preview = (
            f'<div class="ad-board__preview ad-board__preview--filled">'
            f'<img src="{_e(shot_url)}" alt="Live ad creative preview" loading="lazy">'
            f'</div>'
        )
    else:
        # We deliberately do NOT render the brand monogram here -
        # showing 'PS' as a fake-ad preview reads as broken to clients.
        # Instead we render a quiet text block with an explicit
        # Open-live-ad CTA when we have an ads-library URL.
        _ = brand_name  # kept on signature for back-compat with tests
        if p.library_url:
            preview_cta = (
                f'<a class="ad-board__preview-open" href="{_e(p.library_url)}" '
                'target="_blank" rel="noopener">Open the live ad &rsaquo;</a>'
            )
        else:
            preview_cta = ''
        preview = (
            '<div class="ad-board__preview ad-board__preview--unavailable" role="note">'
            '<span class="ad-board__preview-label">Preview held back this pass</span>'
            '<span class="ad-board__preview-sub">Verify directly on Meta Ads Library</span>'
            f'{preview_cta}'
            '</div>'
        )
    body_excerpt_block = (
        f'<p class="ad-board__excerpt">{_e(p.body_excerpt)}</p>'
        if p.body_excerpt else ''
    )
    open_btn = (
        f'<a class="ad-board__open" href="{_e(p.library_url)}" target="_blank" rel="noopener">Open in Ad Library</a>'
        if p.library_url else ''
    )
    days_chip = (
        f'<span class="ad-board__days">{int(p.days_active)} DAYS ACTIVE</span>'
        if p.days_active else ''
    )
    return f"""
<article class="ad-board__row" data-reveal data-reveal-index="{idx}">
  {preview}
  <div class="ad-board__cols">
    <div class="ad-board__col">
      <span class="ad-board__col-label">Pattern</span>
      <p class="ad-board__col-body">{_e(p.pattern)}</p>
      {body_excerpt_block}
    </div>
    <div class="ad-board__col">
      <span class="ad-board__col-label">Weakness</span>
      <p class="ad-board__col-body">{_e(p.weakness)}</p>
    </div>
    <div class="ad-board__col">
      <span class="ad-board__col-label">Opportunity</span>
      <p class="ad-board__col-body">{_e(p.opportunity)}</p>
    </div>
  </div>
  <div class="ad-board__actions">
    {days_chip}
    {open_btn}
  </div>
</article>"""


def _section_hook_map(brief: StrategyBrief) -> str:
    """Section - hook map, grouped into three lanes:

      - HOOKS TO PRIORITIZE   (priority)
      - HOOKS TO TEST LATER   (test_later)
      - HOOKS TO AVOID         (avoid)

    The grouping is what makes the page read as a strategist's call,
    not a brain dump. Each lane carries its own visual tone (the
    'avoid' lane is muted, never alarming).
    """
    lanes: dict[str, list[HookTerritory]] = {
        "priority": [], "test_later": [], "avoid": [],
    }
    for h in brief.hook_territories:
        lanes.setdefault(h.priority, lanes["priority"]).append(h)
    # Defensive: anything with an unknown priority lands in 'priority'.

    def render_lane(label: str, hint: str, key: str, lane_cls: str) -> str:
        items = lanes.get(key) or []
        if not items:
            return ""
        cards = "".join(
            _render_hook_territory(h, idx, lane_cls)
            for idx, h in enumerate(items)
        )
        return f"""
<div class="hook-lane hook-lane--{lane_cls}">
  <header class="hook-lane__head">
    <span class="hook-lane__kicker">{label}</span>
    <span class="hook-lane__hint">{hint}</span>
  </header>
  <div class="hook-lane__grid">{cards}</div>
</div>"""

    body = "".join([
        render_lane(
            "Hooks to prioritize",
            "tested first; existing-asset friendly",
            "priority", "priority",
        ),
        render_lane(
            "Hooks to test later",
            "second-sprint candidates; medium evidence fit",
            "test_later", "later",
        ),
        render_lane(
            "Hooks to avoid for now",
            "lower-evidence fit or compliance risk; validate later, not first",
            "avoid", "avoid",
        ),
    ])
    return f"""
<section class="section" id="hook-map" data-slide="hooks" data-reveal>
  <div class="section__meta"><span class="section__index">07</span><span>Hook map</span></div>
  <h2 class="section__title">The territories worth testing first - and the ones to park.</h2>
  <p class="section__lede">A strategist&rsquo;s working set of hook territories, sorted into priority, test-later and avoid lanes. Each card carries the reason it could work, the risk to manage, and a sample opening line.</p>
  {body}
</section>
"""


def _render_hook_territory(h: HookTerritory, idx: int, lane_cls: str) -> str:
    sample_block = (
        f'<blockquote class="hook__line">{_e(h.sample_line)}</blockquote>'
        if h.sample_line and not h.sample_line.startswith("(") else ''
    )
    return f"""
<article class="hook hook--{lane_cls}" data-reveal data-reveal-index="{idx}">
  <div class="hook__head">
    <span class="hook__index">{idx + 1:02d}</span>
    <h3 class="hook__name">{_e(h.name)}</h3>
  </div>
  <p class="hook__rationale">{_e(h.rationale)}</p>
  <p class="hook__risk"><strong>Risk to manage</strong> {_e(h.risk)}</p>
  {sample_block}
</article>"""


def _section_opportunities(brief: StrategyBrief) -> str:
    """Section 6 - 3-5 strategic creative opportunities."""
    rows = "".join(
        _render_opportunity(o, idx) for idx, o in enumerate(brief.opportunities)
    )
    return f"""
<section class="section section--cream" id="opportunities" data-slide="opps" data-reveal>
  <div class="section__meta"><span class="section__index">08</span><span>Creative opportunity map</span></div>
  <h2 class="section__title">What the brand can own.</h2>
  <p class="section__lede">Bigger strategic positions worth a deliberate test &mdash; what {_e(brief.brand_name)} can own, why most competitors don&rsquo;t, and the kind of video that proves it.</p>
  <div class="opps">{rows}</div>
</section>
"""


def _render_opportunity(o: OpportunityMap, idx: int) -> str:
    return f"""
<article class="opps__row" data-reveal data-reveal-index="{idx}">
  <span class="opps__idx">{idx + 1:02d}</span>
  <div class="opps__cols">
    <div class="opps__col">
      <span class="opps__col-label">Can own</span>
      <p class="opps__col-body">{_e(o.can_own)}</p>
    </div>
    <div class="opps__col">
      <span class="opps__col-label">Why others don't</span>
      <p class="opps__col-body">{_e(o.why_others_dont)}</p>
    </div>
    <div class="opps__col">
      <span class="opps__col-label">What proves it</span>
      <p class="opps__col-body">{_e(o.proof_video)}</p>
    </div>
  </div>
</article>"""


def _section_route_library(brief: StrategyBrief) -> str:
    """Section 7 - route library. 10-15 short-form video ideas."""
    rows = "".join(
        _render_route(r, idx) for idx, r in enumerate(brief.routes)
    )
    return f"""
<section class="section" id="routes" data-slide="routes" data-reveal>
  <div class="section__meta"><span class="section__index">09</span><span>Routes to produce</span></div>
  <h2 class="section__title">A long list &mdash; we produce from the top.</h2>
  <p class="section__lede">{len(brief.routes)} short-form video routes &mdash; title, hook, opening shot, proof point, CTA and confidence. The sprint section below picks the first four to produce.</p>
  <div class="routes">{rows}</div>
</section>
"""


def _render_route(r: RouteIdea, idx: int) -> str:
    return f"""
<article class="route" data-reveal data-reveal-index="{idx}">
  <header class="route__head">
    <span class="route__idx">{idx + 1:02d}</span>
    <h3 class="route__title">{_e(r.title)}</h3>
    {_confidence_chip(r.confidence)}
  </header>
  <p class="route__hook">&ldquo;{_e(r.hook)}&rdquo;</p>
  <dl class="route__meta">
    <div><dt>Opening shot</dt><dd>{_e(r.opening_shot)}</dd></div>
    <div><dt>Proof point</dt><dd>{_e(r.proof_point)}</dd></div>
    <div><dt>CTA</dt><dd>{_e(r.cta)}</dd></div>
    <div><dt>Assets needed</dt><dd>{_e(r.asset_requirement)}</dd></div>
  </dl>
</article>"""


def _section_avoid_routes(brief: StrategyBrief) -> str:
    """Section - 'Routes we would avoid for now'.

    Five to eight angles we are deliberately NOT prioritising in
    sprint one. Cards are premium, never alarming: each row gives the
    reason it is tempting, the reason we are parking it, the evidence
    label, and what to test instead.
    """
    if not brief.avoid_routes:
        body = (
            '<p class="avoid__empty">No deliberate exclusions captured for '
            'this niche yet.</p>'
        )
    else:
        rows = "".join(
            _render_avoid_card(a, idx) for idx, a in enumerate(brief.avoid_routes)
        )
        body = f'<div class="avoid-grid">{rows}</div>'
    return f"""
<section class="section" id="avoid" data-slide="avoid" data-reveal>
  <div class="section__meta"><span class="section__index">10</span><span>Routes we would avoid for now</span></div>
  <h2 class="section__title">Angles we&rsquo;re deliberately not testing first.</h2>
  <p class="section__lede">Each row names a route that is tempting, the reason we&rsquo;re parking it, and what to test instead. None of these are dismissed forever &mdash; we say lower-evidence fit, higher compliance risk, or validate later, not first.</p>
  {body}
</section>
"""


def _render_avoid_card(a: AvoidRoute, idx: int) -> str:
    return f"""
<article class="avoid" data-reveal data-reveal-index="{idx}">
  <header class="avoid__head">
    <span class="avoid__idx">{idx + 1:02d}</span>
    <h3 class="avoid__name">{_e(a.name)}</h3>
    {_evidence_chip(a.evidence)}
  </header>
  <dl class="avoid__meta">
    <div><dt>Why it&rsquo;s tempting</dt><dd>{_e(a.why_tempting)}</dd></div>
    <div><dt>Why we&rsquo;d avoid for now</dt><dd>{_e(a.why_avoid)}</dd></div>
    <div><dt>Test instead</dt><dd>{_e(a.test_instead)}</dd></div>
  </dl>
</article>"""


def _section_sprint(brief: StrategyBrief) -> str:
    """Section - recommended first production sprint.

    Two parts:
      1. 3-5 routes to produce first, each with a reason.
      2. 'Not in sprint one' - parked routes with their postponement
         reason.

    Framing is operational: this is the production plan the client is
    being asked to approve, not a pitch for the gig.
    """
    if not brief.sprint:
        primary = (
            '<p class="sprint__empty">No sprint picks yet - work the hook map '
            'and route library first.</p>'
        )
    else:
        rows = "".join(
            _render_sprint_row(s, idx) for idx, s in enumerate(brief.sprint)
        )
        primary = f'<ol class="sprint">{rows}</ol>'

    if brief.not_in_sprint_one:
        parked_rows = "".join(
            _render_parked_row(s, idx)
            for idx, s in enumerate(brief.not_in_sprint_one)
        )
        parked_block = f"""
<div class="sprint-parked">
  <header class="sprint-parked__head">
    <span class="sprint-parked__kicker">Not in sprint one</span>
    <span class="sprint-parked__hint">Strong second-sprint candidates and routes parked pending consent / footage</span>
  </header>
  <ol class="sprint-parked__list">{parked_rows}</ol>
</div>"""
    else:
        parked_block = ""

    return f"""
<section class="section section--cream" id="sprint" data-slide="sprint" data-reveal>
  <div class="section__meta"><span class="section__index">11</span><span>Recommended first production sprint</span></div>
  <h2 class="section__title">The first batch of videos to produce.</h2>
  <p class="section__lede">The cheapest learning loop &mdash; existing-asset-friendly, evidence-led, paired against the strongest ad in the current library. Approve the four below and the rest of the route library becomes the queue for sprints two and three.</p>
  {primary}
  {parked_block}
</section>
"""


def _render_sprint_row(s: SprintRecommendation, idx: int) -> str:
    return f"""
<li class="sprint__row" data-reveal data-reveal-index="{idx}">
  <span class="sprint__idx">{idx + 1:02d}</span>
  <div class="sprint__copy">
    <h3 class="sprint__title">{_e(s.route_title)}</h3>
    <p class="sprint__reason">{_e(s.reason)}</p>
  </div>
</li>"""


def _render_parked_row(s: SprintRecommendation, idx: int) -> str:
    return f"""
<li class="sprint-parked__row" data-reveal data-reveal-index="{idx}">
  <span class="sprint-parked__idx">{idx + 1:02d}</span>
  <div class="sprint-parked__copy">
    <h4 class="sprint-parked__title">{_e(s.route_title)}</h4>
    <p class="sprint-parked__reason">{_e(s.reason)}</p>
  </div>
</li>"""


def _section_next_step(brief: StrategyBrief) -> str:
    """Section - close-out call to action.

    This page is sent AFTER the client has bought, so the CTA is NOT
    a lead capture and does NOT point back at the pitch microsite. It
    asks the client to approve the first production sprint and gives
    them a single internal anchor link back to the sprint section.
    """
    _ = brief  # reserved for future per-brand microcopy
    return """
<section class="section" id="next-step" data-slide="next-step" data-reveal>
  <div class="section__meta"><span class="section__index">12</span><span>Recommended next action</span></div>
  <div class="strategy-next">
    <div class="strategy-next__copy">
      <h2 class="strategy-next__head">Approve the first production sprint.</h2>
      <p class="strategy-next__body">Confirm which three routes we should produce first. Once approved, we book the founder / customer time, lock the asset list, and start filming inside the week.</p>
      <a class="btn btn--primary" href="#sprint">Review the sprint plan</a>
    </div>
  </div>
</section>
"""


# --------------------------------------------------------------------------- #
# Stylesheet
# --------------------------------------------------------------------------- #


def _stylesheet(brief: StrategyBrief) -> str:
    accent = _accent_for_dark_bg(brief.primary_color or "#F5F0E8")
    primary = brief.primary_color or "#F5F0E8"
    return f"""
:root {{
  --bg: #0A0A0A;
  --bg-2: #131313;
  --ink: #F5F0E8;
  --ink-muted: rgba(245, 240, 232, 0.72);
  --ink-faint: rgba(245, 240, 232, 0.45);
  --card: #F5F0E8;
  --card-ink: #0A0A0A;
  --card-ink-muted: rgba(10, 10, 10, 0.68);
  --hairline: rgba(245, 240, 232, 0.12);
  --hairline-strong: rgba(245, 240, 232, 0.32);
  --accent: {primary};
  --accent-on-dark: {accent};
  --accent-ink: #0A0A0A;
  --radius-md: 14px;
  --radius-lg: 22px;
}}
* {{ box-sizing: border-box; }}
html, body {{ background: var(--bg); color: var(--ink); }}
body {{
  margin: 0;
  font-family: Inter, system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  line-height: 1.55;
  font-size: 16px;
  letter-spacing: -0.003em;
}}

/* Progress bar + status banner share styling with the pitch microsite
   so navigating between the two surfaces feels continuous. */
.progress-bar {{
  position: fixed;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: transparent;
  z-index: 50;
}}
.progress-bar__fill {{
  display: block; height: 100%;
  background: var(--accent-on-dark);
  width: 0%;
  transition: width 80ms linear;
}}
/* (Strategy page intentionally has no draft/live status banner -
   the client deliverable should not carry operator-facing chrome.) */

.topbar {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 22px 36px;
  font-size: 11px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 600;
  color: var(--ink-muted);
  border-bottom: 1px solid var(--hairline);
}}
.topbar__brand {{ color: var(--ink); }}

.strategy__deck {{ padding: 0 36px 96px; }}

.section {{
  padding: 96px 0 96px;
  border-bottom: 1px solid var(--hairline);
}}
.section:last-of-type {{ border-bottom: none; }}
.section__meta {{
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--ink-muted);
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  margin-bottom: 24px;
}}
.section__index {{ color: var(--accent-on-dark); }}
.section__title {{
  font-family: "Inter Tight", sans-serif;
  font-size: clamp(36px, 4vw, 56px);
  line-height: 1.04;
  letter-spacing: -0.02em;
  font-weight: 700;
  margin: 0 0 24px;
  max-width: 24ch;
}}
.section__lede {{
  font-size: 18px;
  line-height: 1.55;
  color: var(--ink-muted);
  max-width: 62ch;
  margin-bottom: 56px;
}}
.section--cream {{
  background: var(--card);
  color: var(--card-ink);
  margin: 0 -36px;
  padding-left: 36px;
  padding-right: 36px;
  border-bottom: none;
}}
.section--cream .section__meta {{ color: var(--card-ink-muted); }}
.section--cream .section__index {{ color: var(--accent); }}
.section--cream .section__title {{ color: var(--card-ink); }}
.section--cream .section__lede {{ color: var(--card-ink-muted); }}

/* ------------------------------------------------------------ cover */
.strategy-cover {{ padding-top: 80px; }}
.strategy-cover__layout {{
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 56px;
  align-items: center;
}}
.strategy-cover__copy {{ display: flex; flex-direction: column; gap: 18px; }}
.strategy-cover__monogram {{
  /* Kept for legacy callers; the cover now uses .strategy-cover__brand-mark. */
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px; height: 44px;
  border-radius: 999px;
  background: rgba(245, 240, 232, 0.95);
  color: #0A0A0A;
  font-family: "Inter Tight", sans-serif;
  font-weight: 700;
  font-size: 14px;
  border: 1px solid color-mix(in srgb, var(--accent) 45%, rgba(0,0,0,0.18));
}}
/* Cover brand mark - either a real logo image or a clean text wordmark. */
.strategy-cover__brand-mark {{
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
}}
.strategy-cover__brand-mark--logo {{
  height: 38px;
  padding: 4px 10px;
  background: rgba(245, 240, 232, 0.92);
  border-radius: 8px;
}}
.strategy-cover__logo-img {{
  height: 100%;
  width: auto;
  object-fit: contain;
  display: block;
}}
.strategy-cover__brand-mark--wordmark {{
  align-items: baseline;
  gap: 0;
  padding: 0;
  background: transparent;
}}
.strategy-cover__wordmark {{
  font-family: "Inter Tight", sans-serif;
  font-weight: 700;
  font-size: 26px;
  letter-spacing: -0.02em;
  color: rgba(245, 240, 232, 0.95);
  text-transform: lowercase;
}}
.strategy-cover__eyebrow {{
  font-size: 11px;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-on-dark);
}}
.strategy-cover__title {{
  font-family: "Inter Tight", sans-serif;
  font-size: clamp(48px, 6vw, 76px);
  line-height: 0.98;
  letter-spacing: -0.025em;
  margin: 0;
  font-weight: 700;
}}
.strategy-cover__subhead {{
  font-size: 18.5px;
  color: var(--ink-muted);
  max-width: 32ch;
  margin: 0 0 6px;
}}
.strategy-cover__meta {{
  margin: 16px 0 0;
  display: grid;
  gap: 10px;
  max-width: 38ch;
}}
.strategy-cover__meta > div {{
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  align-items: start;
}}
.strategy-cover__meta dt {{
  font-size: 10.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
}}
.strategy-cover__meta dd {{
  margin: 0;
  font-size: 14.5px;
  line-height: 1.5;
  color: var(--ink);
}}
.strategy-cover__hero {{
  position: relative;
  aspect-ratio: 4 / 5;
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--bg-2);
  box-shadow: 0 30px 80px rgba(0,0,0,0.5);
}}
.strategy-cover__hero img {{
  width: 100%; height: 100%; object-fit: cover; display: block;
}}
.strategy-cover__hero--mock {{
  background:
    radial-gradient(120% 80% at 18% 12%, color-mix(in srgb, var(--accent) 55%, transparent) 0%, transparent 55%),
    radial-gradient(90% 60% at 82% 88%, color-mix(in srgb, var(--accent) 30%, transparent) 0%, transparent 60%),
    linear-gradient(160deg, color-mix(in srgb, var(--accent) 18%, #141414) 0%, #0A0A0A 80%);
}}
.strategy-cover__hero-frame, .strategy-cover__hero-orb {{
  position: absolute;
  pointer-events: none;
}}
.strategy-cover__hero-frame {{
  inset: 18% 22% auto auto;
  width: 38%;
  aspect-ratio: 9/16;
  border-radius: 14px;
  border: 1px solid rgba(245,240,232,0.18);
  background: linear-gradient(155deg, color-mix(in srgb, var(--accent) 70%, transparent) 0%, transparent 80%);
  box-shadow: 0 22px 50px rgba(0,0,0,0.55);
}}
.strategy-cover__hero-orb {{
  inset: auto 12% 12% auto;
  width: 30%;
  aspect-ratio: 1;
  border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, rgba(245,240,232,0.35), rgba(0,0,0,0) 70%);
  filter: blur(8px);
  opacity: 0.5;
}}

/* ----------------------------------------------------- exec summary */
.exec-cards {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
}}
.exec-card {{
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  padding: 26px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}}
.exec-card__kicker {{
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-on-dark);
}}
.exec-card__body {{
  margin: 0;
  font-size: 15.5px;
  line-height: 1.55;
  color: var(--ink);
}}

/* ----------------------------------------------------- market signals */
.market {{ display: grid; gap: 14px; }}
.market__row {{
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 24px;
  padding: 22px 24px;
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: var(--radius-md);
  background: #FBF8F0;
}}
.market__chip {{
  display: inline-flex;
  align-items: center;
  align-self: start;
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  padding: 6px 9px;
  border-radius: 6px;
  white-space: nowrap;
}}
.market__chip--audit {{ background: var(--accent); color: var(--accent-ink); }}
.market__chip--hyp {{ background: rgba(0,0,0,0.06); color: rgba(0,0,0,0.7); }}
.market__copy {{ display: flex; flex-direction: column; gap: 4px; }}
.market__title {{
  font-family: "Inter Tight", sans-serif;
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--card-ink);
}}
.market__body {{
  margin: 0;
  font-size: 14.5px;
  line-height: 1.55;
  color: var(--card-ink-muted);
}}
.market__empty {{ color: var(--card-ink-muted); font-size: 14.5px; }}

/* --------------------------------------------------------- ad board */
.ad-board {{ display: grid; gap: 18px; }}
.ad-board__row {{
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr) auto;
  gap: 22px;
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  padding: 22px;
  align-items: stretch;
}}
.ad-board__preview {{
  position: relative;
  aspect-ratio: 9 / 14;
  border-radius: 14px;
  overflow: hidden;
  background: #131313;
  border: 1px solid var(--hairline);
}}
.ad-board__preview img {{
  width: 100%; height: 100%; object-fit: cover; display: block;
}}
.ad-board__preview--mock {{
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 6px;
  background:
    radial-gradient(120% 80% at 20% 10%, rgba(245,240,232,0.16) 0%, transparent 65%),
    linear-gradient(160deg, color-mix(in srgb, var(--accent) 30%, #131313) 0%, #0A0A0A 100%);
}}
.ad-board__preview-monogram {{
  font-family: "Inter Tight", sans-serif;
  font-size: 28px;
  font-weight: 700;
  color: rgba(245,240,232,0.85);
  letter-spacing: -0.02em;
}}
.ad-board__preview-chip {{
  font-size: 9.5px;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  font-weight: 700;
  color: rgba(245,240,232,0.65);
}}
.ad-board__preview--unavailable {{
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 8px;
  padding: 18px;
  background:
    radial-gradient(120% 80% at 20% 10%, rgba(245,240,232,0.05) 0%, transparent 70%),
    linear-gradient(160deg, #131313 0%, #0A0A0A 100%);
  border: 1px dashed var(--hairline-strong);
}}
.ad-board__preview-label {{
  font-size: 11px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-muted);
  text-align: center;
}}
.ad-board__preview-sub {{
  font-size: 10.5px;
  letter-spacing: 0.12em;
  color: var(--ink-faint);
  text-align: center;
}}
.ad-board__preview-open {{
  margin-top: 4px;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--accent-on-dark);
  text-decoration: none;
  padding: 5px 12px;
  border: 1px solid var(--hairline-strong);
  border-radius: 999px;
}}
.ad-board__preview-open:hover {{
  background: rgba(245,240,232,0.06);
}}
.ad-board__cols {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}}
.ad-board__col {{ display: flex; flex-direction: column; gap: 6px; }}
.ad-board__col-label {{
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
}}
.ad-board__col-body {{
  margin: 0;
  font-size: 14.5px;
  line-height: 1.5;
  color: var(--ink);
}}
.ad-board__excerpt {{
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--ink-muted);
  border-left: 2px solid var(--accent-on-dark);
  padding-left: 10px;
  font-style: italic;
}}
.ad-board__actions {{
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}}
.ad-board__days {{
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
}}
.ad-board__open {{
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-ink);
  background: var(--accent-on-dark);
  padding: 8px 14px;
  border-radius: 999px;
  text-decoration: none;
}}
.ad-board__empty {{ color: var(--ink-muted); font-size: 14.5px; }}

/* ----------------------------------------------------------- hook map */
.hook-map {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}}
.hook {{
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}}
.hook__head {{ display: flex; align-items: center; gap: 14px; }}
.hook__index {{
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-on-dark);
}}
.hook__name {{
  font-family: "Inter Tight", sans-serif;
  margin: 0;
  font-size: 22px;
  letter-spacing: -0.01em;
  font-weight: 700;
}}
.hook__rationale {{
  margin: 0;
  font-size: 14.5px;
  line-height: 1.55;
  color: var(--ink-muted);
}}
.hook__risk {{
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--ink-faint);
}}
.hook__risk strong {{
  display: inline-block;
  font-size: 10px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--accent-on-dark);
  font-weight: 700;
  margin-right: 6px;
}}
.hook__line {{
  margin: 6px 0 0;
  padding: 14px 16px;
  border-left: 3px solid var(--accent-on-dark);
  background: rgba(245, 240, 232, 0.04);
  font-family: "Inter Tight", sans-serif;
  font-size: 16px;
  line-height: 1.4;
  font-style: italic;
  color: var(--ink);
}}

/* ------------------------------------------------------- opportunities */
.opps {{ display: grid; gap: 16px; }}
.opps__row {{
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr);
  gap: 22px;
  padding: 24px;
  background: #FFFFFF;
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: var(--radius-md);
}}
.opps__idx {{
  font-family: "Inter Tight", sans-serif;
  font-size: 22px;
  font-weight: 700;
  color: var(--accent);
  align-self: start;
}}
.opps__cols {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
}}
.opps__col {{ display: flex; flex-direction: column; gap: 6px; }}
.opps__col-label {{
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: rgba(0,0,0,0.55);
}}
.opps__col-body {{
  margin: 0;
  font-size: 14.5px;
  line-height: 1.55;
  color: var(--card-ink);
}}

/* ------------------------------------------------------------ routes */
.routes {{ display: grid; gap: 14px; }}
.route {{
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  padding: 22px 24px;
}}
.route__head {{
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 12px;
}}
.route__idx {{
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-on-dark);
  min-width: 28px;
}}
.route__title {{
  font-family: "Inter Tight", sans-serif;
  margin: 0;
  font-size: 19px;
  letter-spacing: -0.01em;
  font-weight: 700;
  flex: 1;
}}
.route__conf {{
  font-size: 9.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  padding: 5px 9px;
  border-radius: 6px;
}}
.route__conf--high {{ background: var(--accent); color: var(--accent-ink); }}
.route__conf--medium {{ background: rgba(245,240,232,0.16); color: var(--ink); }}
.route__conf--low {{ background: rgba(245,240,232,0.06); color: var(--ink-faint); }}
.route__hook {{
  margin: 0 0 14px;
  font-family: "Inter Tight", sans-serif;
  font-style: italic;
  font-size: 16.5px;
  line-height: 1.4;
  color: var(--ink);
  border-left: 3px solid var(--accent-on-dark);
  padding-left: 14px;
}}
.route__meta {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 24px;
  margin: 0;
}}
.route__meta > div {{
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  align-items: baseline;
}}
.route__meta dt {{
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
}}
.route__meta dd {{
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--ink-muted);
}}

/* ------------------------------------------------------------- sprint */
.sprint {{
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 14px;
}}
.sprint__row {{
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr);
  gap: 20px;
  padding: 22px 24px;
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
}}
.sprint__idx {{
  font-family: "Inter Tight", sans-serif;
  font-size: 22px;
  font-weight: 700;
  color: var(--accent-on-dark);
  align-self: start;
}}
.sprint__title {{
  font-family: "Inter Tight", sans-serif;
  margin: 0 0 6px;
  font-size: 18px;
  letter-spacing: -0.01em;
  font-weight: 700;
}}
.sprint__reason {{
  margin: 0;
  font-size: 14.5px;
  line-height: 1.55;
  color: var(--ink-muted);
}}
.sprint__empty {{ color: var(--ink-muted); font-size: 14.5px; }}

/* ----------------------------------------------------------- next step */
.strategy-next {{
  background: #FFFFFF;
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: var(--radius-lg);
  padding: 36px 32px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 18px;
  color: var(--card-ink);
}}
.strategy-next__head {{
  font-family: "Inter Tight", sans-serif;
  font-size: clamp(28px, 3vw, 36px);
  margin: 0;
  letter-spacing: -0.018em;
  font-weight: 700;
}}
.strategy-next__body {{
  margin: 0;
  color: var(--card-ink-muted);
  font-size: 16.5px;
  line-height: 1.55;
  max-width: 56ch;
}}

/* CTA buttons. */
.btn {{
  display: inline-flex;
  align-items: center;
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  letter-spacing: 0.05em;
  font-weight: 700;
  padding: 14px 22px;
  border-radius: 999px;
  text-decoration: none;
  transition: transform 180ms ease, opacity 180ms ease;
}}
.btn--primary {{ background: var(--accent-on-dark); color: var(--accent-ink); }}
.btn--primary:hover {{ transform: translateY(-1px); }}

/* Footer. */
.footer {{
  display: flex;
  justify-content: space-between;
  padding: 22px 36px;
  font-size: 12px;
  color: var(--ink-faint);
  border-top: 1px solid var(--hairline);
}}

/* --------------------------------------------------------- evidence chips */
.ev-chip {{
  display: inline-flex;
  align-items: center;
  font-size: 9.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  padding: 5px 9px;
  border-radius: 6px;
  white-space: nowrap;
}}
.ev-chip--audit {{ background: var(--accent); color: var(--accent-ink); }}
.ev-chip--research {{ background: rgba(245, 240, 232, 0.18); color: var(--ink); }}
.ev-chip--hyp {{ background: rgba(245, 240, 232, 0.06); color: var(--ink-muted); }}
.ev-chip--avoid {{ background: rgba(255, 188, 110, 0.14); color: #FFC97A; }}
.ev-chip--later {{ background: rgba(155, 200, 230, 0.14); color: #B4DCEF; }}
.section--cream .ev-chip--research {{ background: rgba(0,0,0,0.08); color: rgba(0,0,0,0.74); }}
.section--cream .ev-chip--hyp {{ background: rgba(0,0,0,0.05); color: rgba(0,0,0,0.55); }}
.section--cream .ev-chip--avoid {{ background: rgba(176, 96, 16, 0.12); color: #8C4A0F; }}
.section--cream .ev-chip--later {{ background: rgba(40, 100, 140, 0.12); color: #2F5E80; }}

.conf-chip {{
  display: inline-flex;
  align-items: center;
  font-size: 9.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  padding: 5px 9px;
  border-radius: 6px;
  border: 1px solid var(--hairline);
  color: var(--ink-muted);
  background: transparent;
}}
.conf-chip--high {{ color: var(--accent-on-dark); border-color: var(--hairline-strong); }}
.conf-chip--medium {{ color: var(--ink-muted); }}
.conf-chip--low {{ color: var(--ink-faint); }}

/* ---------------------------------------------------- competitor intel */
.competitors {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}}
.competitor {{
  position: relative;
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-left: 3px solid var(--accent-on-dark);
  border-radius: var(--radius-md);
  padding: 22px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}}
.competitor__head {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}}
.competitor__title-row {{ display: flex; align-items: baseline; gap: 12px; }}
.competitor__idx {{
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-on-dark);
}}
.competitor__name {{
  font-family: "Inter Tight", sans-serif;
  margin: 0;
  font-size: 21px;
  letter-spacing: -0.01em;
  font-weight: 700;
}}
.competitor__chips {{ display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }}
.competitor__why {{
  margin: 0;
  font-size: 14.5px;
  line-height: 1.55;
  color: var(--ink-muted);
}}
.competitor__meta {{ display: grid; gap: 10px; margin: 0; }}
.competitor__meta > div {{
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  align-items: start;
  gap: 14px;
}}
.competitor__meta dt {{
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
}}
.competitor__meta dd {{
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--ink);
}}
.competitor__site {{
  align-self: flex-start;
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-on-dark);
  text-decoration: none;
}}
.competitor__site:hover {{ text-decoration: underline; }}

/* Competitor chip rail - "Open website" + "Open Meta ads" pair. */
.competitor__chip-rail {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 2px;
}}
.competitor__chip-link {{
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  padding: 7px 12px;
  border-radius: 999px;
  border: 1px solid var(--hairline-strong);
  color: var(--ink);
  text-decoration: none;
  transition: background 0.18s ease, transform 0.18s ease;
}}
.competitor__chip-link:hover {{
  background: rgba(245, 240, 232, 0.06);
  transform: translateY(-1px);
}}
.competitor__chip-link--accent {{
  border-color: var(--accent-on-dark);
  color: var(--accent-on-dark);
}}

/* Competitor ad-proof strip */
.ad-proof {{
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
}}
.ad-proof__head {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}}
.ad-proof__label {{
  font-size: 10.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-on-dark);
}}
.ad-proof__count {{
  font-size: 11px;
  color: var(--ink-muted);
}}
.ad-proof__strip {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}}
.ad-proof__tile {{
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: 10px;
  overflow: hidden;
}}
.ad-proof__media {{
  aspect-ratio: 4 / 5;
  overflow: hidden;
  background: rgba(245, 240, 232, 0.06);
  display: flex;
  align-items: center;
  justify-content: center;
}}
.ad-proof__img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.ad-proof__placeholder {{
  font-size: 11px;
  letter-spacing: 0.22em;
  color: var(--ink-faint);
  font-weight: 700;
}}
.ad-proof__meta {{
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px 10px;
}}
.ad-proof__id {{
  font-size: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--ink-faint);
}}
.ad-proof__open {{
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent-on-dark);
  text-decoration: none;
}}
.ad-proof__open:hover {{ text-decoration: underline; }}

/* Ad-count chip on the competitor card header. Distinct from the
   evidence chip so the reader can scan total proof depth at a glance. */
.competitor__ad-count {{
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(245, 240, 232, 0.06);
  border: 1px solid var(--hairline);
  font-family: "Inter Tight", sans-serif;
}}
.competitor__ad-count-num {{
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  line-height: 1;
}}
.competitor__ad-count-label {{
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
}}

/* Overflow tile on the competitor ad strip. Compact, premium, not a
   placeholder - reads as "more proof in the JSON" not "missing ad". */
.ad-proof__more {{
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 110px;
  padding: 14px 12px;
  border-radius: 10px;
  background: rgba(245, 240, 232, 0.04);
  border: 1px dashed var(--hairline-strong);
  color: var(--ink-muted);
}}
.ad-proof__more-num {{
  font-family: "Inter Tight", sans-serif;
  font-size: 18px;
  font-weight: 700;
  color: var(--ink);
}}
.ad-proof__more-label {{
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
  text-align: center;
}}

/* Excluded-competitors secondary note - small, quiet, never the main
   focus of the section. */
.excluded-competitors {{
  margin-top: 36px;
  padding: 20px 22px;
  border: 1px dashed var(--hairline);
  border-radius: 14px;
  background: rgba(245, 240, 232, 0.02);
}}
.excluded-competitors__head {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 10px;
}}
.excluded-competitors__kicker {{
  font-family: "Inter Tight", sans-serif;
  font-size: 11.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-muted);
}}
.excluded-competitors__hint {{
  font-size: 11.5px;
  color: var(--ink-faint);
}}
.excluded-competitors__list {{
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 6px;
}}
.excluded-competitors__row {{
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--ink-muted);
}}
.excluded-competitors__name {{
  font-weight: 700;
  color: var(--ink);
}}
.excluded-competitors__reason {{
  color: var(--ink-muted);
}}
.excluded-competitors__link {{
  color: var(--accent-on-dark);
  text-decoration: none;
  letter-spacing: 0.04em;
}}
.excluded-competitors__link:hover {{ text-decoration: underline; }}

/* Quiet no-proof affordance for competitors without captured ads */
.competitor__no-proof {{
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  background: rgba(245, 240, 232, 0.03);
  border: 1px dashed rgba(255, 188, 110, 0.35);
  border-radius: 10px;
}}
.competitor__no-proof-label {{
  font-size: 10.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: rgba(255, 201, 122, 0.95);
}}
.competitor__no-proof p {{
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--ink-muted);
}}

/* ----------------------------------------------- creative pattern board */
.patterns {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}}
.pattern {{
  background: #FFFFFF;
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: var(--radius-md);
  padding: 22px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: var(--card-ink);
}}
.pattern__head {{
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}}
.pattern__idx {{
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
}}
.pattern__name {{
  font-family: "Inter Tight", sans-serif;
  margin: 0;
  font-size: 19px;
  letter-spacing: -0.01em;
  font-weight: 700;
  flex: 1;
}}
.pattern__proof-meta {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 2px;
}}
.pattern__proof-chip {{
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 10px 4px 8px;
  border-radius: 999px;
  background: rgba(60, 74, 59, 0.08);
  border: 1px solid rgba(60, 74, 59, 0.18);
  font-family: "Inter Tight", sans-serif;
}}
.pattern__proof-num {{
  font-size: 14px;
  font-weight: 800;
  color: #2C3A2C;
  letter-spacing: -0.01em;
}}
.pattern__proof-label {{
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(0,0,0,0.6);
}}
.pattern__users {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.pattern__user {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid rgba(0,0,0,0.1);
  background: rgba(0,0,0,0.03);
  color: rgba(0,0,0,0.72);
}}
.pattern__user--verified {{
  border-color: rgba(60, 74, 59, 0.45);
  background: rgba(60, 74, 59, 0.08);
  color: #2C3A2C;
}}
.pattern__user-dot {{
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #2C3A2C;
}}
.pattern__user--candidate {{
  border-style: dashed;
  border-color: rgba(0,0,0,0.18);
  color: rgba(0,0,0,0.55);
  background: transparent;
}}

/* Pattern ad-evidence strip */
.pattern-evidence {{
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 4px;
  padding-top: 14px;
  border-top: 1px dashed rgba(0,0,0,0.12);
}}
.pattern-evidence__head {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}}
.pattern-evidence__label {{
  font-size: 10.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent);
}}
.pattern-evidence__count {{
  font-size: 11px;
  color: rgba(0,0,0,0.55);
}}
.pattern-evidence__strip {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}}
.pattern-evidence__tile {{
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  background: #FAF7F1;
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 10px;
  overflow: hidden;
}}
.pattern-evidence__media {{
  aspect-ratio: 4 / 5;
  overflow: hidden;
  background: rgba(0,0,0,0.03);
  display: flex;
  align-items: center;
  justify-content: center;
}}
.pattern-evidence__img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.pattern-evidence__placeholder {{
  font-size: 11px;
  letter-spacing: 0.22em;
  color: rgba(0,0,0,0.4);
  font-weight: 700;
}}
.pattern-evidence__caption {{
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px 10px;
}}
.pattern-evidence__brand {{
  font-size: 12.5px;
  font-weight: 700;
  color: var(--card-ink);
}}
.pattern-evidence__open {{
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent);
  text-decoration: none;
}}
.pattern-evidence__open:hover {{ text-decoration: underline; }}

/* Overflow tile inside the pattern-evidence strip (cream section). */
.pattern-evidence__more {{
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 18px 10px;
  border: 1px dashed rgba(0,0,0,0.18);
  border-radius: 10px;
  background: rgba(0,0,0,0.02);
}}
.pattern-evidence__more-num {{
  font-family: "Inter Tight", sans-serif;
  font-size: 18px;
  font-weight: 700;
  color: var(--card-ink);
}}
.pattern-evidence__more-label {{
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: rgba(0,0,0,0.5);
  text-align: center;
}}

.pattern__no-evidence {{
  margin-top: 4px;
  padding-top: 14px;
  border-top: 1px dashed rgba(0,0,0,0.12);
  display: flex;
  flex-direction: column;
  gap: 6px;
}}
.pattern__no-evidence-label {{
  font-size: 10.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: rgba(176, 96, 16, 0.85);
}}
.pattern__no-evidence p {{
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: rgba(0,0,0,0.6);
}}

/* ----------------------------------------------- patterns to validate next */
.patterns-validate {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}}
.patterns-validate__card {{
  position: relative;
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-left: 3px dashed rgba(255, 188, 110, 0.55);
  border-radius: var(--radius-md);
  padding: 22px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}}
.patterns-validate__head {{
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}}
.patterns-validate__idx {{
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--ink-faint);
}}
.patterns-validate__name {{
  font-family: "Inter Tight", sans-serif;
  margin: 0;
  font-size: 19px;
  letter-spacing: -0.01em;
  font-weight: 700;
  flex: 1;
}}
.patterns-validate__meta {{ display: grid; gap: 8px; margin: 0; }}
.patterns-validate__meta > div {{
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  align-items: start;
  gap: 14px;
}}
.patterns-validate__meta dt {{
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
}}
.patterns-validate__meta dd {{
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--ink);
}}
.patterns-validate__meta code {{
  font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(245, 240, 232, 0.1);
  color: var(--ink-muted);
}}
.patterns-validate__link {{
  color: var(--accent-on-dark);
  text-decoration: none;
  font-weight: 600;
}}
.patterns-validate__link:hover {{ text-decoration: underline; }}
.patterns-validate__action {{
  display: inline-flex;
  align-items: center;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  padding: 3px 9px;
  border-radius: 999px;
  margin-right: 4px;
}}
.patterns-validate__action--scrape_more {{
  background: rgba(155, 200, 230, 0.16);
  color: #B4DCEF;
}}
.patterns-validate__action--validate_next {{
  background: rgba(60, 74, 59, 0.2);
  color: var(--accent-on-dark);
}}
.patterns-validate__action--avoid_for_now {{
  background: rgba(255, 188, 110, 0.16);
  color: #FFC97A;
}}
.patterns-validate__reason {{
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  padding: 3px 9px;
  border-radius: 999px;
}}
.patterns-validate__reason--none {{
  background: rgba(255, 188, 110, 0.14);
  color: #FFC97A;
}}
.patterns-validate__reason--single {{
  background: rgba(155, 200, 230, 0.18);
  color: #B4DCEF;
}}
.patterns-validate__reason--moved {{
  background: rgba(180, 220, 239, 0.14);
  color: rgba(245, 240, 232, 0.85);
}}
.patterns-validate__count {{
  display: inline-flex;
  align-items: center;
  font-family: "Inter Tight", sans-serif;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ink-faint);
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid rgba(245, 240, 232, 0.12);
}}
.pattern__meta {{ display: grid; gap: 8px; margin: 0; }}
.pattern__meta > div {{
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  align-items: start;
  gap: 14px;
}}
.pattern__meta dt {{
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: rgba(0,0,0,0.55);
}}
.pattern__meta dd {{
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--card-ink);
}}

/* --------------------------------------------------------- hook lanes */
.hook-lane {{ margin-bottom: 36px; }}
.hook-lane:last-child {{ margin-bottom: 0; }}
.hook-lane__head {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 14px;
  border-bottom: 1px solid var(--hairline);
  padding-bottom: 10px;
  margin-bottom: 18px;
}}
.hook-lane__kicker {{
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-on-dark);
}}
.hook-lane__hint {{
  font-size: 12px;
  color: var(--ink-faint);
  letter-spacing: 0.02em;
}}
.hook-lane__grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}}
.hook-lane--avoid .hook-lane__kicker {{ color: #FFC97A; }}
.hook-lane--later .hook-lane__kicker {{ color: #B4DCEF; }}
.hook--avoid {{ opacity: 0.92; border-style: dashed; }}
.hook--later {{ background: rgba(155, 200, 230, 0.05); }}

/* -------------------------------------------------- routes to avoid */
.avoid-grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}}
.avoid {{
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  padding: 22px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}}
.avoid__head {{
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}}
.avoid__idx {{
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--ink-faint);
}}
.avoid__name {{
  font-family: "Inter Tight", sans-serif;
  margin: 0;
  font-size: 18px;
  letter-spacing: -0.01em;
  font-weight: 700;
  flex: 1;
}}
.avoid__meta {{ display: grid; gap: 8px; margin: 0; }}
.avoid__meta > div {{
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  align-items: start;
  gap: 14px;
}}
.avoid__meta dt {{
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--ink-faint);
}}
.avoid__meta dd {{
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--ink-muted);
}}

/* -------------------------------------------- sprint parked / not in s1 */
.sprint-parked {{
  margin-top: 28px;
  padding-top: 24px;
  border-top: 1px dashed rgba(0,0,0,0.1);
}}
.sprint-parked__head {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 14px;
}}
.sprint-parked__kicker {{
  font-family: "Inter Tight", sans-serif;
  font-size: 13px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent);
}}
.sprint-parked__hint {{
  font-size: 11.5px;
  letter-spacing: 0.02em;
  color: rgba(0,0,0,0.55);
}}
.sprint-parked__list {{
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 10px;
}}
.sprint-parked__row {{
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  gap: 14px;
  padding: 12px 16px;
  background: rgba(0,0,0,0.04);
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: 10px;
}}
.sprint-parked__idx {{
  font-family: "Inter Tight", sans-serif;
  font-size: 14px;
  font-weight: 700;
  color: rgba(0,0,0,0.55);
}}
.sprint-parked__title {{
  font-family: "Inter Tight", sans-serif;
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 700;
  color: var(--card-ink);
}}
.sprint-parked__reason {{
  margin: 0;
  font-size: 13.5px;
  line-height: 1.5;
  color: rgba(0,0,0,0.65);
}}

/* Reveal motion. */
[data-reveal] {{
  opacity: 0;
  transform: translateY(36px);
  transition: opacity 700ms cubic-bezier(.2,.6,.2,1),
              transform 700ms cubic-bezier(.2,.6,.2,1);
}}
[data-reveal].is-in {{ opacity: 1; transform: none; }}
@media (prefers-reduced-motion: reduce) {{
  [data-reveal] {{ opacity: 1; transform: none; transition: none; }}
}}

/* Tablet. */
@media (max-width: 1100px) {{
  .strategy-cover__layout {{ grid-template-columns: 1fr; gap: 36px; }}
  .ad-board__row {{ grid-template-columns: 160px minmax(0, 1fr); }}
  .ad-board__actions {{ grid-column: 1 / -1; flex-direction: row; align-items: center; justify-content: flex-start; }}
  .ad-board__cols {{ grid-template-columns: 1fr; }}
  .opps__cols {{ grid-template-columns: 1fr; }}
  .route__meta {{ grid-template-columns: 1fr; }}
  .exec-cards {{ grid-template-columns: 1fr; }}
  .hook-lane__grid {{ grid-template-columns: 1fr; }}
  .competitors {{ grid-template-columns: 1fr; }}
  .patterns {{ grid-template-columns: 1fr; }}
  .avoid-grid {{ grid-template-columns: 1fr; }}
  .competitor__meta > div {{ grid-template-columns: 1fr; }}
  .pattern__meta > div {{ grid-template-columns: 1fr; }}
  .avoid__meta > div {{ grid-template-columns: 1fr; }}
}}

/* Mobile. */
@media (max-width: 720px) {{
  .strategy__deck {{ padding: 0 20px 96px; }}
  .topbar, .footer {{ padding: 14px 20px; font-size: 11px; }}
  .section {{ padding: 64px 0; }}
  .section__title {{ font-size: 32px; max-width: none; }}
  .section__lede {{ font-size: 16px; margin-bottom: 36px; }}
  .strategy-cover__title {{ font-size: clamp(36px, 12vw, 56px); }}
  .ad-board__row {{ grid-template-columns: 1fr; }}
  .market__row {{ grid-template-columns: 1fr; }}
}}

/* Print. */
@media print {{
  .progress-bar {{ display: none !important; }}
  body {{ background: #fff; color: #0A0A0A; }}
  .section {{ page-break-inside: avoid; }}
}}
"""


# --------------------------------------------------------------------------- #
# JavaScript (reveal-on-scroll + progress bar)
# --------------------------------------------------------------------------- #


def _javascript() -> str:
    return """
(function(){
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Progress bar.
  var bar = document.querySelector('.progress-bar__fill');
  function onScroll() {
    if (!bar) return;
    var doc = document.documentElement;
    var max = (doc.scrollHeight - doc.clientHeight) || 1;
    var pct = Math.min(100, Math.max(0, (window.scrollY / max) * 100));
    bar.style.width = pct.toFixed(2) + '%';
  }
  window.addEventListener('scroll', onScroll, {passive: true});
  onScroll();
  // Reveal-on-scroll.
  if (reduceMotion || !('IntersectionObserver' in window)) {
    document.querySelectorAll('[data-reveal]').forEach(function(el){ el.classList.add('is-in'); });
    return;
  }
  var io = new IntersectionObserver(function(entries) {
    entries.forEach(function(entry) {
      if (!entry.isIntersecting) return;
      var idx = parseInt(entry.target.getAttribute('data-reveal-index') || '0', 10);
      entry.target.style.transitionDelay = (idx * 60) + 'ms';
      entry.target.classList.add('is-in');
      io.unobserve(entry.target);
    });
  }, {rootMargin: '-10% 0px -5% 0px'});
  document.querySelectorAll('[data-reveal]').forEach(function(el){ io.observe(el); });
})();
"""
