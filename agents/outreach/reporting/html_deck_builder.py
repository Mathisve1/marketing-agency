"""HTML/CSS microsite builder - the client-facing route.

Takes a `DeckBrief` and writes a single self-contained HTML file to
`prospects/<id>/deck/index.html` (deck mode) or `prospects/<id>/site/index.html`
(microsite mode, via `microsite_builder`).

Design philosophy (V2):
  - Black editorial background (#0A0A0A), warm cream cards (#F5F0E8).
  - Prospect brand is the hero - their primary_color drives the accent,
    their hero image is full-bleed above the fold.
  - Premium interactive microsite, not a stacked-slide PDF: smooth scroll,
    sticky scroll-progress bar, fade-and-rise reveal on every section,
    a fixed "Send me the first route" CTA pill, and a dedicated
    watermarked-preview section that always renders (real video when
    provided, premium placeholder when not).
  - Inline CSS + tiny vanilla JS only. No React, no build step, no
    external scripts. Drop the folder into Cloudflare Pages and it works.
  - Each image asset is used at most once across the whole page (the
    `DeckBrief` already enforces this; the builder tracks `used_paths`).
  - Print CSS hides interactive chrome (sticky CTA, progress bar) and
    lays one section per A4 page so Cmd/Ctrl+P still produces a clean
    handout.
  - `prefers-reduced-motion: reduce` disables all reveal/scroll motion.

Optional helper `export_to_pdf(html_path, pdf_path)` tries `weasyprint`
when available and falls back to a documented "open in browser, print
to PDF" workflow. We never add a hard dependency on heavy print stacks.
"""
from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.outreach.reporting.deck_brief import (
    AdProof,
    ConceptRoute,
    DeckBrief,
    GapMapRow,
    PricingTier,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


DEFAULT_CONTACT_EMAIL = "hello@yuvostudio.com"
"""Mailto fallback recipient when no `contact_email` is configured.

Public-facing mailto: addresses are intentionally fine to ship in HTML
- they expose only the destination address, no API token or secret.
"""


def build_html_deck(
    brief: DeckBrief,
    *,
    output_dir: Optional[Path] = None,
    noindex: bool = False,
    preview_video_url: Optional[str] = None,
    status: Optional[str] = None,
    public_url: Optional[str] = None,
    contact_email: Optional[str] = None,
    form_endpoint: Optional[str] = None,
    prospect_id: Optional[str] = None,
    private_slug: Optional[str] = None,
) -> Path:
    """Render `brief` to `<output_dir>/index.html` and return the path.

    Asset paths are computed relative to `output_dir` so the same builder
    works for:
      - deck mode: prospects/<id>/deck/  (assets at ../assets/)
      - microsite mode: prospects/<id>/site/  (assets copied to assets/)

    Args:
      brief: deck content.
      output_dir: explicit output folder; defaults to
        `brief.prospect_root / "deck"`.
      noindex: when True, injects `<meta name="robots" content="noindex,
        nofollow">` so the page does not get indexed by search engines.
        Mandatory for the private microsite path.
      preview_video_url: relative or absolute URL of a watermarked preview
        MP4. When set, the dedicated "Your first route preview" section
        embeds an inline video player. When None, that same section
        renders a premium phone-mockup placeholder explaining that the
        first watermarked route will sit there. The caller is responsible
        for the watermark guarantee; this layer just embeds what it is
        told.
      status: optional deploy status taken from the prospect manifest:
        `"draft"` -> render a top strip reading 'Local draft preview - not
        deployed yet'; `"deployed"` -> render 'Live - <public_url>'; None
        suppresses the strip entirely (used by the plain deck mode and
        by tests that don't simulate a manifest).
      public_url: when `status == "deployed"`, this URL is shown beside
        the 'Live' badge so reviewers can copy it. Ignored when status
        is not 'deployed'.
      contact_email: mailto destination when `form_endpoint` is None
        (the MVP path). Falls back to `DEFAULT_CONTACT_EMAIL` when not
        supplied. Public; safe to ship in HTML.
      form_endpoint: when set, the interest form renders as a real
        `<form method="POST" action="<endpoint>">` and the mailto
        fallback is suppressed. Use this once a Cloudflare Pages
        Function / Worker at `/api/interest` is live. The frontend
        carries no secret; the endpoint reads its own bindings.
      prospect_id, private_slug: optional hidden-field values passed
        through to the interest form so a future backend can identify
        which prospect a submission belongs to without parsing the URL.
        Both fields are surfaced as `<input type="hidden">` and prefilled
        in the mailto subject/body.
    """
    if brief.prospect_root is None and output_dir is None:
        raise ValueError(
            "build_html_deck: brief.prospect_root is None and no output_dir "
            "given; nowhere to write the deck."
        )

    if output_dir is None:
        output_dir = brief.prospect_root / "deck"  # type: ignore[union-attr]
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "index.html"

    asset_resolver = _AssetResolver(
        deck_dir=output_dir,
        prospect_root=brief.prospect_root,
    )

    used_paths: set[Path] = set()  # belt-and-braces; brief already dedupes

    html_body = _render(
        brief,
        asset_resolver,
        used_paths,
        noindex=noindex,
        preview_video_url=preview_video_url,
        status=status,
        public_url=public_url,
        contact_email=contact_email or DEFAULT_CONTACT_EMAIL,
        form_endpoint=form_endpoint,
        prospect_id=prospect_id,
        private_slug=private_slug,
    )
    out_path.write_text(html_body, encoding="utf-8", newline="\n")
    log.info("html_deck_builder: wrote %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path


def export_to_pdf(html_path: Path, pdf_path: Path) -> Optional[Path]:
    """Try to write `pdf_path` from `html_path` using weasyprint.

    Returns the PDF path on success, None when weasyprint is unavailable
    or fails. The deck builder never depends on this working - the
    canonical 'export to PDF' path is the user opening the HTML in
    Chrome / Edge / Safari and using File -> Print -> Save as PDF.
    """
    try:
        from weasyprint import HTML  # type: ignore[import-not-found]
    except Exception as e:
        log.info("export_to_pdf: weasyprint unavailable (%s); skipping", e)
        return None
    try:
        HTML(filename=str(html_path)).write_pdf(str(pdf_path))
    except Exception as e:
        log.warning("export_to_pdf: weasyprint failed: %s", e)
        return None
    return pdf_path


# --------------------------------------------------------------------------- #
# Asset resolution
# --------------------------------------------------------------------------- #


@dataclass
class _AssetResolver:
    """Resolve an absolute asset Path to a URL relative to the HTML output dir.

    Works for any layout:
      - Deck mode: HTML at prospects/<id>/deck/, assets at
        prospects/<id>/assets/  -> emits `../assets/<file>`
      - Microsite mode: HTML at prospects/<id>/site/, assets at
        prospects/<id>/site/assets/  -> emits `assets/<file>`
      - Deploy package: same as microsite - the resolver is purely
        relative.

    Cross-drive paths (rare on Windows when assets are imported from
    another disk) fall back to an absolute `file:///` URI so the browser
    can still render them locally.
    """
    deck_dir: Path
    prospect_root: Optional[Path]

    def url_for(self, path: Optional[Path]) -> Optional[str]:
        """Return a relative or file:/// URL for `path`, or None."""
        if path is None:
            return None
        try:
            abs_target = Path(path).resolve()
            abs_dir = self.deck_dir.resolve()
            rel = os.path.relpath(abs_target, abs_dir)
            return Path(rel).as_posix()
        except ValueError:
            return Path(path).resolve().as_uri()
        except Exception:
            return Path(path).resolve().as_uri()


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _e(text: Optional[str]) -> str:
    """HTML-escape; None becomes empty string."""
    return html.escape(text or "", quote=True)


def _render(
    brief: DeckBrief,
    assets: _AssetResolver,
    used: set[Path],
    *,
    noindex: bool = False,
    preview_video_url: Optional[str] = None,
    status: Optional[str] = None,
    public_url: Optional[str] = None,
    contact_email: str = DEFAULT_CONTACT_EMAIL,
    form_endpoint: Optional[str] = None,
    prospect_id: Optional[str] = None,
    private_slug: Optional[str] = None,
) -> str:
    """Top-level HTML render. Each section is composed by a private helper.

    Section order follows the commercial story:
      1. Hero            - brand is the hero, above-the-fold CTA
      2. 45-second       - four cards: what we saw / would make / costs / risk
      3. Live ads        - what we saw in the prospect's ads
      4. Gap map         - what's missing
      5. Concept board   - the routes we'd ship
      6. Process         - how simple this is
      preview            - your first route preview (always)
      7. Pricing         - the price
      8. Next step       - what to do next
      9. Interest        - send us your preferred call times
    """
    css = _stylesheet(brief)
    js = _javascript()

    sections = [
        _section_hero(brief, assets, used),
        _section_45_second(brief),
        _section_live_ads(brief, assets, used),
        _section_gap_map(brief),
        _section_concept_board(brief, assets, used),
        _section_how_this_works(brief),
        _section_preview_video(brief, preview_video_url),
        _section_pricing(brief),
        _section_next_step(brief),
        _section_interest(
            brief,
            contact_email=contact_email,
            form_endpoint=form_endpoint,
            prospect_id=prospect_id,
            private_slug=private_slug,
            public_url=public_url,
        ),
    ]

    title = f"{_e(brief.prospect_name)} - Private Creative Note"
    robots_meta = (
        '<meta name="robots" content="noindex,nofollow">' if noindex else ""
    )
    sticky_cta = _sticky_cta(brief)
    status_banner = _status_banner(status, public_url)
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
<body>
<div class="progress-bar" aria-hidden="true"><span class="progress-bar__fill"></span></div>
{status_banner}
<header class="topbar">
  <span class="topbar__kicker">{_e(brief.cover_kicker)}</span>
  <span class="topbar__brand">{_e(brief.prospect_name)} &times; {_e(brief.agency_name)}</span>
</header>
<main class="deck">
{"".join(sections)}
</main>
{sticky_cta}
<footer class="footer">
  <span>{_e(brief.agency_name)} &mdash; private creative note</span>
  <span>Prepared for {_e(brief.prospect_name)}</span>
</footer>
<script>{js}</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# Status banner (draft / deployed)
# --------------------------------------------------------------------------- #


def _status_banner(status: Optional[str], public_url: Optional[str]) -> str:
    """Top-of-page strip that prevents the local HTML from implying the
    page is live before a deploy actually happened.

    States:
      - status == "draft"     -> 'LOCAL DRAFT PREVIEW - NOT DEPLOYED YET'
      - status == "deployed"  -> 'LIVE - <public_url>' (when known)
      - None / other          -> nothing (e.g. deck mode, tests)

    Hidden in print.
    """
    if status == "draft":
        return (
            '<div class="status-banner status-banner--draft" role="status">'
            '<span class="status-banner__dot" aria-hidden="true"></span>'
            '<span class="status-banner__label">Local draft preview</span>'
            '<span class="status-banner__sep">&middot;</span>'
            '<span class="status-banner__detail">not deployed yet &mdash; '
            'public URL is reserved but the page is not live</span>'
            '</div>'
        )
    if status == "deployed":
        if public_url:
            url_html = (
                f'<a class="status-banner__link" href="{_e(public_url)}" '
                f'target="_blank" rel="noopener">{_e(public_url)}</a>'
            )
            tail = (
                '<span class="status-banner__sep">&middot;</span>' + url_html
            )
        else:
            tail = ""
        return (
            '<div class="status-banner status-banner--live" role="status">'
            '<span class="status-banner__dot status-banner__dot--live" aria-hidden="true"></span>'
            '<span class="status-banner__label">Live</span>'
            f'{tail}'
            '</div>'
        )
    return ""


# --------------------------------------------------------------------------- #
# Sticky CTA
# --------------------------------------------------------------------------- #


def _sticky_cta(brief: DeckBrief) -> str:
    """A fixed pill, bottom-right on desktop / full-width strip on mobile.

    Anchors to the interest form section (the conversion surface). Hidden
    in print. The label stays warm and product-shaped (`Show me the
    first route`), not a generic "Contact us".
    """
    _ = brief  # reserved: future brand-specific microcopy
    return (
        '<a class="sticky-cta" href="#interest" aria-label="Show me the first route">'
        '<span class="sticky-cta__label">Show me the first route</span>'
        '<span class="sticky-cta__arrow" aria-hidden="true">&#8599;</span>'
        '</a>'
    )


# --------------------------------------------------------------------------- #
# Tiny vanilla JS - scroll progress + reveal-on-scroll
# --------------------------------------------------------------------------- #


def _javascript() -> str:
    """V4 vanilla JS pipeline.

    Five behaviours, all guarded by `prefers-reduced-motion: reduce`:

      1. Scroll-progress bar at the top of the page.
      2. Hero parallax: bg image translates at ~55% of scroll, headline
         block counter-translates ~15% so depth feels real.
      3. IntersectionObserver reveals with per-item stagger.
      4. Concept rail focal-card spotlight: scales the card closest to
         horizontal centre, dims the rest.
      5. Section-progress accent stripe on the live-ads sticky panel
         (fills as you scroll through that section).

    The CSS shows reveal targets and the parallax base transform
    immediately if motion is suppressed, so functionality never depends
    on the JS path.
    """
    return """
(function(){
  var reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // 1. Scroll progress bar
  var bar = document.querySelector('.progress-bar__fill');
  if (bar) {
    var updateBar = function(){
      var h = document.documentElement;
      var b = document.body;
      var total = (h.scrollHeight || b.scrollHeight) - h.clientHeight;
      var pct = total > 0 ? (h.scrollTop || b.scrollTop) / total : 0;
      bar.style.width = (Math.max(0, Math.min(1, pct)) * 100).toFixed(2) + '%';
    };
    window.addEventListener('scroll', updateBar, { passive: true });
    window.addEventListener('resize', updateBar);
    updateBar();
  }

  // 2. Hero parallax + headline counter-motion
  var hero = document.querySelector('.hero');
  var heroBg = hero ? hero.querySelector('.hero__bg img, .hero__bg--mock') : null;
  var heroBody = hero ? hero.querySelector('.hero__body') : null;
  if (hero && heroBg && !reduceMotion) {
    var heroFrame = false;
    var updateHero = function(){
      var rect = hero.getBoundingClientRect();
      if (rect.bottom < -200 || rect.top > window.innerHeight) return;
      var y = -rect.top * 0.55;
      heroBg.style.transform = 'translate3d(0,' + y.toFixed(1) + 'px,0) scale(1.10)';
      if (heroBody) {
        var bodyY = -rect.top * 0.15;
        var fade = Math.max(0, 1 - (Math.max(0, -rect.top) / (rect.height || 1)));
        heroBody.style.transform = 'translate3d(0,' + bodyY.toFixed(1) + 'px,0)';
        heroBody.style.opacity = fade.toFixed(3);
      }
    };
    var onHeroScroll = function(){
      if (heroFrame) return;
      heroFrame = true;
      requestAnimationFrame(function(){
        heroFrame = false;
        updateHero();
      });
    };
    window.addEventListener('scroll', onHeroScroll, { passive: true });
    updateHero();
  }

  // 3. Reveal-on-scroll, with stagger via data-reveal-index
  var targets = document.querySelectorAll('[data-reveal]');
  if (reduceMotion || !('IntersectionObserver' in window)) {
    for (var i = 0; i < targets.length; i++) {
      targets[i].classList.add('is-revealed');
    }
  } else {
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(entry){
        if (entry.isIntersecting) {
          var idx = entry.target.getAttribute('data-reveal-index');
          if (idx) {
            entry.target.style.transitionDelay = (parseInt(idx, 10) * 110) + 'ms';
          }
          entry.target.classList.add('is-revealed');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -60px 0px' });
    for (var j = 0; j < targets.length; j++) { io.observe(targets[j]); }
  }

  // 4. Concept rail focal-card spotlight
  var rail = document.querySelector('.concepts-rail');
  if (rail && !reduceMotion) {
    var concepts = rail.querySelectorAll('.concept');
    var focalFrame = false;
    var updateFocal = function(){
      var railRect = rail.getBoundingClientRect();
      var railCenter = railRect.left + railRect.width / 2;
      var bestIdx = -1;
      var bestDelta = Infinity;
      for (var k = 0; k < concepts.length; k++) {
        var r = concepts[k].getBoundingClientRect();
        var c = r.left + r.width / 2;
        var d = Math.abs(c - railCenter);
        if (d < bestDelta) { bestDelta = d; bestIdx = k; }
      }
      for (var m = 0; m < concepts.length; m++) {
        if (m === bestIdx) {
          concepts[m].classList.add('concept--focal');
          concepts[m].classList.remove('concept--dimmed');
        } else {
          concepts[m].classList.remove('concept--focal');
          concepts[m].classList.add('concept--dimmed');
        }
      }
    };
    var onFocalScroll = function(){
      if (focalFrame) return;
      focalFrame = true;
      requestAnimationFrame(function(){
        focalFrame = false;
        updateFocal();
      });
    };
    rail.addEventListener('scroll', onFocalScroll, { passive: true });
    window.addEventListener('scroll', onFocalScroll, { passive: true });
    window.addEventListener('resize', onFocalScroll);
    updateFocal();
  }

  // 5. Section-progress accent stripe on the live-ads sticky intro panel
  var liveAds = document.getElementById('live-ads');
  var stripe = liveAds && liveAds.querySelector('.live-ads__progress-fill');
  if (liveAds && stripe && !reduceMotion) {
    var stripeFrame = false;
    var updateStripe = function(){
      var rect = liveAds.getBoundingClientRect();
      var vh = window.innerHeight;
      var travelled = Math.min(Math.max((vh - rect.top) / (rect.height + vh), 0), 1);
      stripe.style.transform = 'scaleY(' + travelled.toFixed(3) + ')';
    };
    var onStripeScroll = function(){
      if (stripeFrame) return;
      stripeFrame = true;
      requestAnimationFrame(function(){
        stripeFrame = false;
        updateStripe();
      });
    };
    window.addEventListener('scroll', onStripeScroll, { passive: true });
    window.addEventListener('resize', onStripeScroll);
    updateStripe();
  }
})();
""".strip()


# --------------------------------------------------------------------------- #
# Stylesheet
# --------------------------------------------------------------------------- #


def _stylesheet(brief: DeckBrief) -> str:
    """All deck CSS, brand-coloured. Inline so the HTML stays portable."""
    accent = brief.primary_color
    accent_text = brief.accent_text_color
    accent_on_dark = _accent_for_dark_bg(accent)
    return f"""
:root {{
  --bg: #0A0A0A;
  --bg-soft: #111111;
  --ink: #F5F0E8;
  --ink-muted: rgba(245, 240, 232, 0.62);
  --ink-faint: rgba(245, 240, 232, 0.36);
  --card: #F5F0E8;
  --card-ink: #131210;
  --card-ink-muted: rgba(19, 18, 16, 0.62);
  --hairline: rgba(245, 240, 232, 0.10);
  --hairline-strong: rgba(245, 240, 232, 0.18);
  --accent: {accent};
  --accent-on-dark: {accent_on_dark};
  --accent-ink: {accent_text};
  --radius-lg: 24px;
  --radius-md: 18px;
  --radius-sm: 12px;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
html, body {{
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI",
               Roboto, Helvetica, Arial, sans-serif;
  font-size: 16px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}}
body {{ overflow-x: hidden; }}
h1, h2, h3, h4 {{
  font-family: "Inter Tight", "Inter", -apple-system, "Segoe UI", sans-serif;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0;
}}
a {{ color: inherit; text-decoration: none; }}

/* Scroll progress bar (fixed top) */
.progress-bar {{
  position: fixed;
  inset: 0 0 auto 0;
  height: 3px;
  background: rgba(245, 240, 232, 0.06);
  z-index: 60;
  pointer-events: none;
}}
.progress-bar__fill {{
  display: block;
  height: 100%;
  width: 0;
  background: var(--accent-on-dark);
  transition: width 80ms linear;
}}

/* Top + bottom chrome (subtle, never dominant) */
.topbar, .footer {{
  position: sticky;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 36px;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--ink-faint);
  border-bottom: 1px solid var(--hairline);
  background: rgba(10, 10, 10, 0.85);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  z-index: 50;
}}
.topbar {{ top: 3px; }}  /* under the progress bar */

/* Status banner: draft / deployed strip. Sits between progress bar
   and topbar so the operator always sees deploy state at a glance. */
.status-banner {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 36px;
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
}}
.status-banner__dot {{
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #B8731F;
  box-shadow: 0 0 0 3px rgba(184,115,31,0.18);
}}
.status-banner__dot--live {{
  background: #2C7A3D;
  box-shadow: 0 0 0 3px rgba(44,122,61,0.18);
}}
.status-banner__sep {{ opacity: 0.6; font-weight: 400; }}
.status-banner__detail {{
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: none;
  font-size: 12.5px;
}}
.status-banner__link {{
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 2px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: none;
  font-size: 12.5px;
}}
.status-banner--draft {{
  background: #2E1F0F;
  color: #F8D6A4;
  border-bottom: 1px solid rgba(248,214,164,0.18);
}}
.status-banner--live {{
  background: #0F2A1A;
  color: #C9EAD2;
  border-bottom: 1px solid rgba(201,234,210,0.18);
}}
.footer {{
  position: relative;
  border-top: 1px solid var(--hairline);
  border-bottom: none;
  bottom: 0;
  padding-bottom: 96px;  /* leave room above sticky CTA on desktop */
}}
.topbar__kicker {{ color: var(--accent-on-dark); font-weight: 600; }}

/* Section structure */
.deck {{ padding: 0 36px 16px; max-width: 1280px; margin: 0 auto; }}
.section {{
  padding: 96px 0 112px;
  border-bottom: 1px solid var(--hairline);
  position: relative;
}}
.section:last-of-type {{ border-bottom: none; }}
.section__meta {{
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-bottom: 28px;
}}
.section__index {{ color: var(--accent-on-dark); font-weight: 600; }}
.section__title {{
  font-size: 56px;
  line-height: 1.04;
  letter-spacing: -0.02em;
  max-width: 20ch;
  margin-bottom: 20px;
}}
.section__lede {{
  font-size: 19px;
  line-height: 1.5;
  color: var(--ink-muted);
  max-width: 62ch;
  margin-bottom: 56px;
}}
.section__footnote {{
  margin-top: 32px;
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-faint);
  display: inline-flex;
  align-items: center;
  gap: 10px;
}}
.section__footnote::before {{
  content: '';
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent-on-dark);
}}

/* Cream-section variant. Used for one mid-page section so the scroll
   journey hits an obvious dark -> cream -> dark contrast moment. */
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
.section--cream .section__footnote {{ color: var(--card-ink-muted); }}
.section--cream .section__footnote::before {{ background: var(--accent); }}
.section--cream .gap-map {{ box-shadow: none; }}
.section--cream .gap-map__head {{ background: rgba(0,0,0,0.04); }}

/* Reveal-on-scroll - V4 bigger lift + scale for premium feel */
[data-reveal] {{
  opacity: 0;
  transform: translateY(56px) scale(0.98);
  transition: opacity 950ms cubic-bezier(.2,.6,.2,1),
              transform 950ms cubic-bezier(.2,.6,.2,1);
  will-change: opacity, transform;
}}
[data-reveal].is-revealed {{
  opacity: 1;
  transform: translateY(0) scale(1);
}}
@media (prefers-reduced-motion: reduce) {{
  html {{ scroll-behavior: auto; }}
  [data-reveal] {{ opacity: 1; transform: none; transition: none; }}
  .sticky-cta {{ animation: none; }}
  .ad-card__chip--live::before {{ animation: none; }}
  .concepts-rail-hint::after {{ animation: none; }}
  .hero__bg img {{ transform: scale(1.10); }}
  .concept--focal {{ transform: none; }}
}}

/* Hero (above the fold) */
.hero {{
  position: relative;
  isolation: isolate;
  min-height: min(92vh, 880px);
  padding: 56px 0 96px;
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 36px;
  overflow: hidden;
}}
.hero__bg {{
  position: absolute;
  inset: -10% -36px -6% -36px;
  background: var(--bg);
  z-index: -2;
}}
.hero__bg img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  /* V4: lifted the photo from 0.55 -> 0.85 so the real brand image is
     visible, not buried under a black wash. The hero__gradient below
     handles legibility for the body copy via a bottom-only fade. */
  opacity: 0.85;
  filter: saturate(1.08) contrast(1.05);
  will-change: transform;
  transform-origin: center 30%;
  transform: scale(1.04);
}}
/* Premium designed fallback for prospects with no usable hero photo.
   Three layered radial highlights + a tinted brand-accent floor +
   a floating "PRIVATE ROUTE PREVIEW" frame so the hero never reads as
   an empty dark rectangle. */
.hero__bg--mock {{
  background:
    radial-gradient(120% 80% at 18% 12%, color-mix(in srgb, var(--accent) 55%, transparent) 0%, transparent 55%),
    radial-gradient(90% 60% at 82% 88%, color-mix(in srgb, var(--accent) 30%, transparent) 0%, transparent 60%),
    radial-gradient(140% 70% at 50% 110%, rgba(0,0,0,0.55) 0%, transparent 70%),
    linear-gradient(160deg, color-mix(in srgb, var(--accent) 18%, #141414) 0%, #0A0A0A 80%);
}}
.hero__bg--mock::before,
.hero__bg--mock::after {{
  content: "";
  position: absolute;
  pointer-events: none;
}}
.hero__bg--mock::before {{
  inset: 12% auto auto 6%;
  width: clamp(140px, 22vw, 260px);
  aspect-ratio: 9 / 16;
  background:
    linear-gradient(155deg, color-mix(in srgb, var(--accent) 70%, transparent) 0%, transparent 80%),
    linear-gradient(0deg, rgba(0,0,0,0.6), rgba(0,0,0,0));
  border: 1px solid rgba(245,240,232,0.12);
  border-radius: 18px;
  box-shadow: 0 24px 60px rgba(0,0,0,0.55);
  opacity: 0.55;
}}
.hero__bg--mock::after {{
  inset: auto 8% 14% auto;
  width: clamp(120px, 18vw, 220px);
  aspect-ratio: 1;
  background:
    radial-gradient(circle at 30% 30%, rgba(245,240,232,0.35), rgba(0,0,0,0) 70%);
  filter: blur(8px);
  opacity: 0.4;
}}
/* Designed hero fallback - layered collage of small premium cards.
   No giant centred monogram anywhere. The composition reads as a
   designed "first route in progress" preview, not a logo placeholder. */
.hero__mock-collage {{
  position: absolute;
  inset: auto 0 0 0;
  height: 70%;
  pointer-events: none;
}}
.hero__mock-card {{
  position: absolute;
  border-radius: 16px;
  border: 1px solid rgba(245,240,232,0.16);
  background:
    radial-gradient(120% 80% at 20% 10%, rgba(245,240,232,0.18) 0%, transparent 65%),
    linear-gradient(155deg, color-mix(in srgb, var(--accent) 50%, #0A0A0A) 0%, #0A0A0A 100%);
  box-shadow: 0 22px 50px rgba(0,0,0,0.55);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  z-index: 0;
}}
.hero__mock-card--proof {{
  inset: 38% auto auto 8%;
  width: clamp(140px, 18vw, 220px);
  height: 80px;
  transform: rotate(-3deg);
}}
.hero__mock-card--frame {{
  inset: 12% 6% auto auto;
  width: clamp(150px, 20vw, 240px);
  aspect-ratio: 9 / 12;
  transform: rotate(2deg);
}}
.hero__mock-card--caption {{
  inset: auto 14% 8% auto;
  width: clamp(160px, 22vw, 260px);
  height: 92px;
  transform: rotate(-1deg);
}}
.hero__mock-card-label {{
  font-size: 9.5px;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  font-weight: 700;
  color: rgba(245,240,232,0.85);
}}
.hero__mock-card-meta {{
  font-size: 11px;
  color: rgba(245,240,232,0.7);
  font-weight: 500;
}}
.hero__mock-card-text {{
  color: rgba(245,240,232,0.92);
  font-family: "Inter Tight", sans-serif;
  font-size: 14px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -0.005em;
}}
.hero__mock-card-bars {{
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 8px;
}}
.hero__mock-card-bars span {{
  display: block;
  height: 6px;
  border-radius: 4px;
  background: linear-gradient(90deg, var(--accent) 0%, rgba(245,240,232,0.25) 80%);
  opacity: 0.55;
}}
.hero__mock-card-bars span:nth-child(2) {{ width: 78%; opacity: 0.4; }}
.hero__mock-card-bars span:nth-child(3) {{ width: 56%; opacity: 0.3; }}
.hero__mock-card-corner {{
  position: absolute;
  top: 12px;
  right: 12px;
  font-size: 9.5px;
  letter-spacing: 0.18em;
  font-weight: 700;
  color: rgba(10,10,10,0.85);
  background: rgba(245,240,232,0.92);
  padding: 4px 7px;
  border-radius: 6px;
}}
.hero__gradient {{
  position: absolute;
  inset: 0;
  z-index: -1;
  /* V4: lighter top, dark only at the bottom (where the body copy sits).
     Left-to-right wash is gone - it was making the right half of the
     hero look like an unfilled panel. */
  background:
    linear-gradient(180deg, rgba(10,10,10,0.20) 0%, rgba(10,10,10,0.30) 35%, rgba(10,10,10,0.78) 78%, #0A0A0A 100%);
  pointer-events: none;
}}
.hero__head {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
}}
/* V5: small text chip, not a giant brand-color circle.
   The previous 76px circle filled with `background: var(--accent)`
   collapsed to a dark blob for brands whose primary color is dark
   (e.g. YANA's #1A1A1A). Even a logo image dropped inside looked
   like a "giant YA" because the surrounding 76px disc dominated.
   New treatment: small pill chip with cream surface + accent border,
   text initials only. Never embeds an image. The brand wordmark
   lives in the topbar where it belongs. */
.hero__monogram {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 38px;
  min-width: 44px;
  padding: 0 12px;
  border-radius: 999px;
  background: rgba(245, 240, 232, 0.95);
  color: var(--ink);
  border: 1px solid color-mix(in srgb, var(--accent) 45%, rgba(0,0,0,0.18));
  font-family: "Inter Tight", sans-serif;
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.06em;
  flex-shrink: 0;
  box-shadow: 0 6px 18px rgba(0,0,0,0.18);
}}
.hero__badge {{
  font-size: 11px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--ink);
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border: 1px solid var(--hairline-strong);
  border-radius: 999px;
  background: rgba(10,10,10,0.5);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
}}
.hero__badge::before {{
  content: '';
  width: 8px;
  height: 8px;
  background: var(--accent-on-dark);
  border-radius: 50%;
  box-shadow: 0 0 0 4px rgba(245,240,232,0.06);
}}
.hero__body {{
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  max-width: 24ch;
}}
.hero__headline {{
  font-size: clamp(46px, 7vw, 92px);
  line-height: 0.97;
  letter-spacing: -0.025em;
  margin: 0 0 24px;
}}
.hero__subhead {{
  font-size: 20px;
  line-height: 1.45;
  color: var(--ink-muted);
  max-width: 56ch;
  margin: 0 0 18px;
}}
.hero__explainer {{
  font-size: 13.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin: 0 0 28px;
  max-width: 60ch;
}}
.hero__actions {{
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
}}
.hero__byline {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-faint);
  border-top: 1px solid var(--hairline);
  padding-top: 20px;
}}

/* Buttons */
.btn {{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 15px 22px;
  border-radius: 999px;
  font-size: 13px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-weight: 700;
  border: 1px solid var(--ink);
  color: var(--ink);
  background: transparent;
  transition: transform 180ms ease, background 180ms ease, color 180ms ease;
}}
.btn:hover {{ transform: translateY(-1px); }}
.btn--primary {{
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--accent);
  box-shadow: 0 14px 40px rgba(0,0,0,0.45);
}}
.btn--primary:hover {{ filter: brightness(1.08); }}
.btn--ghost {{ background: rgba(245,240,232,0.04); }}
.btn::after {{ content: '\\2197'; font-size: 16px; }}

/* Sticky CTA pill */
.sticky-cta {{
  position: fixed;
  right: 28px;
  bottom: 28px;
  display: inline-flex;
  align-items: center;
  gap: 12px;
  padding: 16px 24px;
  border-radius: 999px;
  background: var(--accent);
  color: var(--accent-ink);
  font-size: 13px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-weight: 700;
  box-shadow: 0 18px 45px rgba(0,0,0,0.6), 0 0 0 1px rgba(245,240,232,0.06);
  z-index: 55;
  animation: pulseGlow 2.4s ease-in-out infinite;
}}
.sticky-cta__arrow {{
  font-size: 16px;
  transform: translateY(-1px);
}}
@keyframes pulseGlow {{
  0%, 100% {{ box-shadow: 0 18px 45px rgba(0,0,0,0.6), 0 0 0 1px rgba(245,240,232,0.06); }}
  50% {{ box-shadow: 0 18px 45px rgba(0,0,0,0.6), 0 0 0 6px rgba(245,240,232,0.04); }}
}}

/* 45-second cards */
.cards {{ display: grid; gap: 20px; }}
.cards--2x2 {{ grid-template-columns: 1fr 1fr; }}
.cards--3up {{ grid-template-columns: repeat(3, 1fr); }}
.cards--4up {{ grid-template-columns: repeat(4, 1fr); }}
.card {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: var(--radius-lg);
  padding: 32px;
  display: flex;
  flex-direction: column;
  min-height: 220px;
  position: relative;
  overflow: hidden;
}}
.card::before {{
  content: '';
  position: absolute;
  inset: auto auto 0 0;
  width: 56px;
  height: 4px;
  background: var(--accent);
  border-radius: 0 4px 0 0;
}}
.card__kicker {{
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  font-weight: 700;
  margin-bottom: 16px;
}}
.card__body {{
  font-size: 15.5px;
  line-height: 1.55;
  color: var(--card-ink-muted);
}}

/* Live-ads section: sticky-pin intro on the left, ad-card stack on the right.
   V4 adds a vertical accent stripe along the intro's right edge that
   fills as the operator scrolls through the section - obvious motion
   cue that the pin is doing real work. */
.live-ads-layout {{
  display: grid;
  grid-template-columns: minmax(280px, 0.85fr) 2fr;
  gap: 56px;
  align-items: start;
}}
.live-ads__intro {{
  position: sticky;
  top: 96px;  /* clear the topbar + progress bar */
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding-right: 28px;
  position: -webkit-sticky;
  position: sticky;
}}
.live-ads__intro::after {{
  content: '';
  position: absolute;
  inset: 0 0 0 auto;
  width: 2px;
  background: var(--hairline);
  border-radius: 2px;
}}
.live-ads__progress {{
  position: absolute;
  inset: 0 0 0 auto;
  width: 2px;
  border-radius: 2px;
  background: linear-gradient(180deg, var(--accent-on-dark) 0%, var(--accent) 100%);
  transform-origin: top;
  transform: scaleY(0);
  transition: transform 80ms linear;
  pointer-events: none;
  z-index: 1;
}}
.live-ads__progress-fill {{
  display: block;
  width: 100%;
  height: 100%;
}}
.live-ads__intro h2 {{ margin: 0; }}
.live-ads__intro p {{ margin: 0; }}
.live-ads__stack {{
  display: flex;
  flex-direction: column;
  gap: 24px;
}}

/* Ad proof cards */
.ad-card {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: var(--radius-lg);
  padding: 26px;
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 26px;
  min-height: 320px;
  position: relative;
  box-shadow: 0 22px 60px rgba(0,0,0,0.35);
}}
.ad-card__preview {{
  position: relative;
  border-radius: var(--radius-md);
  background: #0F0F0F;
  overflow: hidden;
  aspect-ratio: 9 / 16;
}}
.ad-card__preview img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
/* Floating overlays drawn on top of a real screenshot */
.ad-card__overlay-top {{
  position: absolute;
  inset: 10px 10px auto 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  pointer-events: none;
}}
.ad-card__overlay-bottom {{
  position: absolute;
  inset: auto 10px 10px 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  pointer-events: auto;
}}
.ad-card__chip {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: #F5F0E8;
  background: rgba(10,10,10,0.72);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  padding: 6px 9px;
  border-radius: 999px;
}}
.ad-card__chip--live::before {{
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #E94545;
  box-shadow: 0 0 0 3px rgba(233,69,69,0.22);
  animation: livePulse 2.4s ease-in-out infinite;
}}
@keyframes livePulse {{
  0%, 100% {{ box-shadow: 0 0 0 3px rgba(233,69,69,0.22); }}
  50%      {{ box-shadow: 0 0 0 7px rgba(233,69,69,0.10); }}
}}
.ad-card__overlay-open {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: #F5F0E8;
  background: rgba(10,10,10,0.78);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  padding: 7px 11px;
  border-radius: 999px;
  pointer-events: auto;
}}
.ad-card__overlay-open::after {{ content: '\\2197'; font-size: 12px; }}

/* Premium Meta-shaped fallback when no screenshot is available */
.ad-card__preview--mock {{
  display: grid;
  grid-template-rows: 46px 1fr 56px;
  background: #F5F0E8;
  color: #131210;
}}
.ad-card__preview-head {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 12px;
  border-bottom: 1px solid rgba(0,0,0,0.08);
  font-size: 11px;
  font-weight: 600;
  color: #131210;
}}
.ad-card__preview-avatar {{
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--accent);
  color: var(--accent-ink);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: "Inter Tight", sans-serif;
  font-weight: 700;
  font-size: 11px;
  letter-spacing: -0.01em;
}}
.ad-card__preview-meta {{
  display: flex;
  flex-direction: column;
  line-height: 1.05;
  gap: 2px;
}}
.ad-card__preview-meta strong {{ font-size: 11.5px; font-weight: 700; }}
.ad-card__preview-meta span {{ font-size: 9.5px; color: rgba(19,18,16,0.55); letter-spacing: 0.04em; font-weight: 500; }}
.ad-card__preview-sponsored {{
  margin-left: auto;
  font-size: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: rgba(19,18,16,0.42);
  font-weight: 700;
}}
.ad-card__preview-body {{
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 18px 16px;
  text-align: center;
  background: linear-gradient(180deg, var(--accent) 0%, #131210 130%);
  color: #F5F0E8;
}}
.ad-card__preview-body span {{
  font-family: "Inter Tight", sans-serif;
  font-weight: 600;
  font-size: 21px;
  line-height: 1.18;
  letter-spacing: -0.01em;
}}
.ad-card__preview-cta {{
  background: rgba(19,18,16,0.06);
  color: #131210;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11.5px;
  font-weight: 700;
  padding: 0 14px;
  border-top: 1px solid rgba(0,0,0,0.06);
}}
.ad-card__preview-cta-button {{
  background: rgba(19,18,16,0.92);
  color: #F5F0E8;
  padding: 7px 12px;
  border-radius: 6px;
  font-size: 11px;
  letter-spacing: 0.08em;
  font-weight: 700;
}}
.ad-card__content {{ display: flex; flex-direction: column; gap: 14px; }}
.ad-card__header {{
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  font-weight: 700;
}}
.ad-card__brandname {{ color: var(--card-ink); }}
.ad-card__tag {{
  display: inline-flex;
  align-self: flex-start;
  font-size: 10px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent);
  background: rgba(0,0,0,0.05);
  padding: 6px 10px;
  border-radius: 999px;
}}
.ad-card__excerpt {{
  font-family: "Inter Tight", sans-serif;
  font-size: 20px;
  line-height: 1.32;
  letter-spacing: -0.005em;
  margin: 0;
}}
.ad-card__diag {{
  font-size: 14px;
  color: var(--card-ink-muted);
  line-height: 1.55;
  margin: 0;
}}
.ad-card__route {{
  font-size: 14.5px;
  font-weight: 500;
  color: var(--card-ink);
  padding: 14px 16px;
  background: rgba(0,0,0,0.04);
  border-radius: var(--radius-sm);
  border-left: 3px solid var(--accent);
}}
.ad-card__route strong {{
  display: block;
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  margin-bottom: 6px;
  font-weight: 700;
}}
.ad-card__footer {{
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}}
.ad-card__meta {{
  font-size: 12px;
  color: var(--card-ink-muted);
}}
.ad-card__open {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--card-ink);
  border: 1px solid var(--card-ink);
  padding: 9px 14px;
  border-radius: 999px;
  transition: background 180ms ease, color 180ms ease;
}}
.ad-card__open::after {{ content: '\\2197'; font-size: 14px; }}
.ad-card__open:hover {{ background: var(--card-ink); color: var(--card); }}

/* Gap map */
.gap-map {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: 0 22px 60px rgba(0,0,0,0.35);
}}
.gap-map__head, .gap-map__row {{
  display: grid;
  grid-template-columns: 1.1fr 1.2fr 1.2fr 110px;
  gap: 24px;
  padding: 22px 28px;
  align-items: start;
}}
.gap-map__head {{
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  font-weight: 700;
  border-bottom: 1px solid rgba(0,0,0,0.08);
  background: rgba(0,0,0,0.02);
}}
.gap-map__row + .gap-map__row {{ border-top: 1px solid rgba(0,0,0,0.06); }}
.gap-map__pattern {{
  font-family: "Inter Tight", sans-serif;
  font-size: 17px;
  line-height: 1.35;
  font-weight: 600;
}}
.gap-map__diag {{ font-size: 14.5px; color: var(--card-ink-muted); }}
.gap-map__test {{
  font-size: 14.5px;
  border-left: 3px solid var(--accent);
  padding-left: 14px;
}}
.gap-map__conf {{
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
}}
.gap-map__conf--high {{ color: #2C7A3D; }}
.gap-map__conf--medium {{ color: #B8731F; }}
.gap-map__conf--low {{ color: var(--card-ink-muted); }}

/* Concept board: horizontal route rail (scroll-snap) on desktop,
   stack on mobile. The rail bleeds out to the deck edges so you
   can sense more cards waiting just off-screen. */
.concepts-rail {{
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(280px, 1fr);
  gap: 22px;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  scroll-padding-left: 4px;
  padding: 8px 4px 32px;
  margin: 0 -4px;
  scrollbar-color: var(--accent-on-dark) rgba(245,240,232,0.06);
  scrollbar-width: thin;
}}
.concepts-rail::-webkit-scrollbar {{ height: 8px; }}
.concepts-rail::-webkit-scrollbar-track {{
  background: rgba(245,240,232,0.06);
  border-radius: 4px;
}}
.concepts-rail::-webkit-scrollbar-thumb {{
  background: var(--accent-on-dark);
  border-radius: 4px;
}}
.concepts-rail-hint {{
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-bottom: 14px;
}}
.concepts-rail-hint::after {{
  content: '\\2192';
  font-size: 14px;
  letter-spacing: 0;
  color: var(--accent-on-dark);
  animation: railArrow 1.6s ease-in-out infinite;
}}
@keyframes railArrow {{
  0%, 100% {{ transform: translateX(0); opacity: 0.6; }}
  50%      {{ transform: translateX(6px); opacity: 1; }}
}}
/* Legacy class kept for the 4-up grid fallback if anyone ever wants it. */
.concepts {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
}}
.concept {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: var(--radius-lg);
  padding: 20px;
  display: flex;
  flex-direction: column;
  position: relative;
  box-shadow: 0 22px 60px rgba(0,0,0,0.35);
  transition: transform 220ms cubic-bezier(.2,.6,.2,1),
              opacity 700ms cubic-bezier(.2,.6,.2,1);
  scroll-snap-align: start;
}}
.concept:hover {{ transform: translateY(-4px); }}
/* V4 focal spotlight: JS toggles .concept--focal on the card closest
   to horizontal centre, and .concept--dimmed on the rest. Pure CSS
   fallback (no JS / reduce-motion) leaves all cards at full opacity. */
.concept--focal {{
  transform: scale(1.04);
  box-shadow: 0 32px 80px rgba(0,0,0,0.55);
}}
.concept--dimmed {{ opacity: 0.62; }}
.concepts-rail:hover .concept--dimmed {{ opacity: 1; }}
.concept__phone {{
  position: relative;
  width: 100%;
  aspect-ratio: 9 / 16;
  border-radius: 22px;
  border: 1px solid rgba(0,0,0,0.10);
  overflow: hidden;
  background: #111;
  margin-bottom: 18px;
  box-shadow: 0 8px 30px rgba(0,0,0,0.25);
  isolation: isolate;
}}
.concept__phone img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
/* Shared frame chrome - meta label at the top of every phone frame. */
.concept__phone-meta {{
  position: absolute;
  top: 12px;
  left: 12px;
  right: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 9.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: rgba(245, 240, 232, 0.85);
  z-index: 3;
  text-shadow: 0 1px 2px rgba(0,0,0,0.55);
}}
.concept__phone-meta-dot {{
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px rgba(245, 240, 232, 0.18);
  flex: none;
}}

/* Filled state: real product image.
   Bottom-only gradient keeps the hook legible without darkening the
   product. The hook + CTA sit inside `.concept__overlay`. */
.concept__phone--filled .concept__overlay {{
  background:
    linear-gradient(0deg, rgba(8,8,8,0.85) 0%, rgba(8,8,8,0.55) 35%, rgba(8,8,8,0) 75%);
}}

/* Designed-fallback state: brand-gradient frame.
   Layered radial highlights, a faint film-grain noise sheet (pure CSS,
   no images), a tasteful brand monogram, and an explicit "9:16 route
   frame" label - reads as intentional, not as a missing image. */
/* Designed-fallback concept frame.

   V5 redesign: the frame is now a CREAM artboard - never dark, never
   black. Earlier versions used `linear-gradient(..., var(--accent),
   #0A0A0A)` as the floor which collapsed to near-black for brands
   whose primary_color is dark (e.g. YANA's #1A1A1A), leaving the
   user with an empty-looking black phone card. The brand accent now
   only appears as decorative border + corner shapes + CTA pill, not
   as the full background. */
.concept__phone--designed {{
  background:
    radial-gradient(115% 75% at 8% 4%, color-mix(in srgb, var(--accent) 22%, transparent) 0%, transparent 55%),
    radial-gradient(120% 85% at 92% 96%, color-mix(in srgb, var(--accent) 12%, transparent) 0%, transparent 60%),
    linear-gradient(165deg, #FBF6EE 0%, #F1E8D8 100%);
  color: var(--card-ink);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--accent) 26%, transparent);
}}
.concept__phone--designed .concept__phone-noise {{
  position: absolute;
  inset: 0;
  background-image:
    repeating-linear-gradient(0deg, rgba(0,0,0,0.012) 0 1px, transparent 1px 3px),
    repeating-linear-gradient(90deg, rgba(0,0,0,0.012) 0 1px, transparent 1px 3px);
  pointer-events: none;
  z-index: 1;
}}
/* Three abstract decorative shapes - layered ovals + accent stripe.
   Cream-surface variant: soft brand-tinted shapes that read as ad
   concept layout, not as bg-fill. Positions vary by `--variant-N`. */
.concept__phone--designed .concept__phone-shape {{
  position: absolute;
  pointer-events: none;
  z-index: 1;
}}
.concept__phone--designed .concept__phone-shape--a {{
  width: 62%;
  aspect-ratio: 1;
  border-radius: 50%;
  top: -18%;
  left: -16%;
  background:
    radial-gradient(circle at 35% 35%, color-mix(in srgb, var(--accent) 40%, transparent), transparent 70%);
  filter: blur(22px);
  opacity: 0.65;
}}
.concept__phone--designed .concept__phone-shape--b {{
  width: 84%;
  aspect-ratio: 1;
  border-radius: 50%;
  bottom: -26%;
  right: -22%;
  background:
    radial-gradient(circle at 35% 35%, color-mix(in srgb, var(--accent) 28%, transparent), transparent 70%);
  filter: blur(26px);
  opacity: 0.7;
}}
.concept__phone--designed .concept__phone-shape--accent {{
  width: 55%;
  height: 22%;
  top: 36%;
  left: 22%;
  border-radius: 18px;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--accent) 55%, transparent) 0%, transparent 80%);
  transform: rotate(-6deg);
  opacity: 0.55;
}}
/* Variant tweaks so the rail doesn't look like four identical cards. */
.concept__phone--variant-2 .concept__phone-shape--a {{ top: auto; bottom: -18%; left: auto; right: -10%; }}
.concept__phone--variant-2 .concept__phone-shape--b {{ bottom: auto; top: -28%; right: auto; left: -22%; }}
.concept__phone--variant-2 .concept__phone-shape--accent {{ transform: rotate(8deg); top: 28%; left: 16%; }}
.concept__phone--variant-3 .concept__phone-shape--accent {{ width: 38%; height: 38%; border-radius: 50%; transform: none; top: 28%; left: 32%; opacity: 0.45; }}
.concept__phone--variant-4 .concept__phone-shape--a {{ width: 80%; aspect-ratio: 1; top: -28%; left: -34%; }}
.concept__phone--variant-4 .concept__phone-shape--accent {{ height: 14%; transform: rotate(-3deg); }}

/* Small frame-label chip at the top. Cream surface variant - dark
   text on the cream artboard so it stays readable. */
.concept__phone--designed .concept__phone-frame-label {{
  position: absolute;
  top: 14px;
  left: 14px;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 9.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: color-mix(in srgb, var(--card-ink) 70%, transparent);
  z-index: 3;
}}
.concept__phone--designed .concept__phone-frame-dot {{
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}}

/* Small corner brand chip (top-right). Text only, capped to 2 chars.
   Cream surface variant - solid ink-on-cream with a thin accent
   border. The ONLY place brand initials appear on the frame; no
   centred giant monogram exists anywhere in the designed fallback. */
.concept__phone--designed .concept__phone-corner-mark {{
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 3;
  font-family: "Inter Tight", sans-serif;
  font-size: 10px;
  letter-spacing: 0.18em;
  font-weight: 700;
  color: var(--card-ink);
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid color-mix(in srgb, var(--accent) 38%, transparent);
  padding: 5px 8px;
  min-width: 22px;
  text-align: center;
  border-radius: 6px;
}}

/* Hook block - the visual centrepiece. Cream surface variant: large
   dark hook line + small eyebrow above. The hook is the focal element
   of the card; the brand mark and decorative shapes are deliberately
   small. */
.concept__phone--designed .concept__phone-hook-block {{
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 64px;
  z-index: 3;
  display: flex;
  flex-direction: column;
  gap: 6px;
}}
.concept__phone--designed .concept__phone-hook-eyebrow {{
  font-size: 9.5px;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  font-weight: 700;
  color: color-mix(in srgb, var(--card-ink) 55%, transparent);
}}
.concept__phone--designed .concept__phone-hook {{
  color: var(--card-ink);
  font-family: "Inter Tight", sans-serif;
  font-size: clamp(20px, 7cqw, 26px);
  line-height: 1.18;
  font-weight: 700;
  letter-spacing: -0.012em;
  container-type: inline-size;
}}

/* CTA row sits at the bottom. CTA pill on the left, '01 / SCENE' meta
   chip on the right - reinforces that this is a video concept frame. */
.concept__phone--designed .concept__phone-cta-row {{
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 16px;
  z-index: 3;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}}
.concept__phone--designed .concept__phone-cta {{
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-ink);
  background: var(--accent);
  padding: 7px 12px;
  border-radius: 999px;
  box-shadow: 0 6px 18px rgba(0,0,0,0.18);
}}
.concept__phone--designed .concept__phone-scene-tag {{
  font-size: 9.5px;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  font-weight: 700;
  color: color-mix(in srgb, var(--card-ink) 55%, transparent);
}}

/* Legacy mock class - kept for back-compat with any test/external
   consumer pinning on it. The designed fallback supersedes it. */
.concept__phone--mock {{
  background:
    radial-gradient(at 30% 20%, rgba(245,240,232,0.18) 0%, transparent 50%),
    linear-gradient(160deg, var(--accent) 0%, #131210 120%);
}}
.concept__overlay {{
  position: absolute;
  inset: auto 0 0 0;
  padding: 18px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  color: #F5F0E8;
  font-family: "Inter Tight", sans-serif;
  font-size: 16px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -0.005em;
  z-index: 3;
}}
.concept__overlay-hook {{
  display: block;
  text-shadow: 0 2px 8px rgba(0,0,0,0.55);
}}
.concept__overlay-cta {{
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-ink);
  background: var(--accent);
  padding: 6px 10px;
  border-radius: 999px;
}}
.concept__label {{
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--accent);
  font-weight: 700;
  margin-bottom: 8px;
}}
.concept__title {{
  font-size: 19px;
  line-height: 1.18;
  font-weight: 600;
  margin: 0 0 10px;
}}
.concept__hook {{
  font-size: 13.5px;
  color: var(--card-ink-muted);
  line-height: 1.5;
  margin: 0 0 16px;
}}
.concept__test {{
  margin-top: auto;
  font-size: 12px;
  color: var(--card-ink-muted);
  line-height: 1.45;
  padding-top: 12px;
  border-top: 1px solid rgba(0,0,0,0.08);
  margin-bottom: 14px;
}}
.concept__test strong {{
  display: block;
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--card-ink);
  margin-bottom: 4px;
  font-weight: 700;
}}
.concept__cta {{
  display: inline-flex;
  align-self: flex-start;
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--accent-ink);
  background: var(--accent);
  padding: 8px 14px;
  border-radius: 999px;
}}

/* Process steps */
.process {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
.process__step {{
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  padding: 24px;
  display: grid;
  grid-template-columns: 56px 1fr;
  gap: 20px;
  align-items: start;
}}
.process__num {{
  font-family: "Inter Tight", sans-serif;
  font-size: 28px;
  font-weight: 700;
  color: var(--accent-on-dark);
}}
.process__label {{
  font-family: "Inter Tight", sans-serif;
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 6px;
}}
.process__desc {{
  font-size: 14px;
  color: var(--ink-muted);
  line-height: 1.55;
}}

/* Preview-video section */
.preview {{
  scroll-margin-top: 96px;
}}
.preview__layout {{
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  gap: 56px;
  align-items: center;
}}
.preview__copy {{
  display: flex;
  flex-direction: column;
  gap: 18px;
}}
.preview__copy h2.section__title {{ margin-bottom: 0; }}
.preview__copy p {{
  font-size: 16.5px;
  line-height: 1.55;
  color: var(--ink-muted);
  margin: 0;
}}
.preview__copy .preview__sub {{
  font-size: 13.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-top: 8px;
}}
.preview__copy .btn {{ margin-top: 8px; align-self: flex-start; }}
.preview__frame {{
  position: relative;
  width: 100%;
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: #050505;
  border: 1px solid var(--hairline-strong);
  box-shadow: 0 30px 80px rgba(0,0,0,0.55),
              inset 0 1px 0 rgba(245,240,232,0.04);
}}
.preview__frame--locked {{
  background:
    radial-gradient(at 30% 18%, rgba(245,240,232,0.07) 0%, transparent 55%),
    radial-gradient(at 70% 82%, rgba(245,240,232,0.04) 0%, transparent 50%),
    linear-gradient(170deg, var(--bg-soft) 0%, #050505 120%);
}}
.preview__frame-bar {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
  font-size: 10px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--ink-faint);
  border-bottom: 1px solid var(--hairline);
  background: rgba(10,10,10,0.55);
  -webkit-backdrop-filter: blur(6px);
  backdrop-filter: blur(6px);
}}
.preview__frame-bar-dot {{
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent-on-dark);
  box-shadow: 0 0 0 3px rgba(245,240,232,0.05);
}}
.preview__frame-bar-meta {{
  margin-left: auto;
  color: var(--ink-muted);
  letter-spacing: 0.16em;
  font-weight: 600;
}}
.preview__placeholder-lock {{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  align-self: center;
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--ink-faint);
  padding: 12px 18px;
  border-top: 1px solid var(--hairline);
  margin-top: auto;
}}
.preview__placeholder-lock-icon {{
  font-size: 16px;
  filter: grayscale(1);
}}
.preview__video {{
  width: 100%;
  display: block;
  background: #000;
  aspect-ratio: 9 / 16;
  max-height: 640px;
}}
.preview__placeholder {{
  position: relative;
  aspect-ratio: 9 / 16;
  max-height: 640px;
  display: grid;
  grid-template-rows: 1fr auto;
  background: transparent;
}}
.preview__placeholder-body {{
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 28px 20px;
  gap: 10px;
}}
.preview__placeholder-glyph {{
  width: 62px;
  height: 62px;
  border-radius: 50%;
  border: 1.5px solid var(--ink-faint);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 6px;
  font-size: 24px;
  color: var(--ink);
}}
.preview__placeholder-title {{
  font-family: "Inter Tight", sans-serif;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.005em;
}}
.preview__placeholder-sub {{
  font-size: 13px;
  color: var(--ink-muted);
  max-width: 26ch;
  line-height: 1.5;
}}
.preview__watermark-note {{
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--ink-faint);
  padding: 12px 20px;
  border-top: 1px solid var(--hairline);
  display: flex;
  justify-content: space-between;
  align-items: center;
}}
.preview__watermark-note::before {{
  content: '';
  width: 7px;
  height: 7px;
  background: var(--accent-on-dark);
  border-radius: 50%;
  display: inline-block;
  margin-right: 10px;
}}

/* Pricing */
.pricing {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 22px; }}
.tier {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: var(--radius-lg);
  padding: 36px 32px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  position: relative;
  border: 2px solid transparent;
  box-shadow: 0 22px 60px rgba(0,0,0,0.35);
}}
.tier--rec {{
  border-color: var(--accent);
  transform: translateY(-6px);
}}
.tier__rec-tag {{
  position: absolute;
  top: -12px;
  right: 24px;
  background: var(--accent);
  color: var(--accent-ink);
  font-size: 10px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  font-weight: 700;
  padding: 5px 10px;
  border-radius: 999px;
}}
.tier__name {{
  font-size: 13px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  font-weight: 700;
}}
.tier__price {{
  font-family: "Inter Tight", sans-serif;
  font-size: 64px;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1;
}}
.tier__tagline {{
  font-family: "Inter Tight", sans-serif;
  font-size: 19px;
  line-height: 1.3;
  font-weight: 500;
}}
.tier__bullets {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
.tier__bullets li {{
  padding-left: 22px;
  position: relative;
  font-size: 14.5px;
  line-height: 1.45;
  color: var(--card-ink-muted);
}}
.tier__bullets li::before {{
  content: '';
  position: absolute;
  left: 0;
  top: 9px;
  width: 12px;
  height: 2px;
  background: var(--accent);
}}

/* Next step */
.next-step {{
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 56px;
  align-items: center;
}}
.next-step__head {{
  font-size: clamp(56px, 8vw, 96px);
  line-height: 0.96;
  letter-spacing: -0.03em;
  margin: 0 0 24px;
}}
.next-step__body {{
  font-size: 19px;
  line-height: 1.5;
  color: var(--ink-muted);
  max-width: 54ch;
  margin-bottom: 36px;
}}
.next-step__actions {{ display: flex; gap: 14px; flex-wrap: wrap; }}
/* V6: next-step visual is a CREAM artboard composition - never a
   dark block with giant centred initials. Earlier versions had
   `background: var(--accent)` + 96 px monogram which collapsed to a
   near-black block with a giant "YA" for any brand whose primary
   color is dark (YANA #1A1A1A is the canonical worst case). The
   new treatment mirrors the concept-fallback artboard: cream floor,
   accent-tinted decorative shapes, a small "FIRST ROUTE WAITS"
   label, and a tiny corner brand chip. No centred initials anywhere. */
.next-step__visual {{
  position: relative;
  width: 100%;
  aspect-ratio: 4 / 5;
  border-radius: var(--radius-lg);
  overflow: hidden;
  background:
    radial-gradient(115% 75% at 8% 4%, color-mix(in srgb, var(--accent) 22%, transparent) 0%, transparent 55%),
    radial-gradient(120% 85% at 92% 96%, color-mix(in srgb, var(--accent) 12%, transparent) 0%, transparent 60%),
    linear-gradient(165deg, #FBF6EE 0%, #F1E8D8 100%);
  border: 1px solid color-mix(in srgb, var(--accent) 26%, transparent);
  box-shadow: 0 22px 60px rgba(0,0,0,0.18);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 22px;
  color: var(--card-ink);
}}
.next-step__visual-shape {{
  position: absolute;
  pointer-events: none;
  z-index: 0;
}}
.next-step__visual-shape--a {{
  width: 55%;
  aspect-ratio: 1;
  border-radius: 50%;
  top: -12%;
  right: -10%;
  background: radial-gradient(circle at 35% 35%, color-mix(in srgb, var(--accent) 32%, transparent), transparent 70%);
  filter: blur(22px);
  opacity: 0.7;
}}
.next-step__visual-shape--b {{
  width: 70%;
  aspect-ratio: 1;
  border-radius: 50%;
  bottom: -22%;
  left: -18%;
  background: radial-gradient(circle at 35% 35%, color-mix(in srgb, var(--accent) 22%, transparent), transparent 70%);
  filter: blur(26px);
  opacity: 0.55;
}}
.next-step__visual-shape--accent {{
  width: 60%;
  height: 18%;
  top: 38%;
  left: 18%;
  border-radius: 14px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 55%, transparent) 0%, transparent 80%);
  transform: rotate(-6deg);
  opacity: 0.5;
}}
.next-step__visual-label {{
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 10px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  font-weight: 700;
  color: color-mix(in srgb, var(--card-ink) 70%, transparent);
}}
.next-step__visual-label-dot {{
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}}
.next-step__visual-corner {{
  position: absolute;
  top: 18px;
  right: 18px;
  z-index: 2;
  font-family: "Inter Tight", sans-serif;
  font-size: 10px;
  letter-spacing: 0.18em;
  font-weight: 700;
  color: var(--card-ink);
  background: rgba(255,255,255,0.78);
  border: 1px solid color-mix(in srgb, var(--accent) 38%, transparent);
  padding: 5px 8px;
  border-radius: 6px;
}}
.next-step__visual-headline {{
  position: relative;
  z-index: 1;
  font-family: "Inter Tight", sans-serif;
  font-size: clamp(22px, 2.2vw, 30px);
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -0.012em;
  color: var(--card-ink);
  max-width: 80%;
  margin-top: 12px;
}}
.next-step__visual-meta {{
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  font-size: 11px;
  color: color-mix(in srgb, var(--card-ink) 60%, transparent);
}}

/* Interest form section ---------------------------------------------------- */
.interest {{
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  gap: 56px;
  align-items: stretch;
}}
.interest__copy {{ max-width: 540px; }}
.interest__bullets {{
  list-style: none;
  padding: 0;
  margin: 28px 0 0;
  display: grid;
  gap: 14px;
}}
.interest__bullets li {{
  position: relative;
  padding-left: 22px;
  font-size: 15.5px;
  line-height: 1.55;
  color: var(--ink-muted);
}}
.interest__bullets li::before {{
  content: "";
  position: absolute;
  left: 0;
  top: 9px;
  width: 8px;
  height: 8px;
  background: var(--accent);
  border-radius: 50%;
}}
.interest__panel {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: 18px;
  padding: 32px;
  box-shadow: 0 18px 48px rgba(0,0,0,0.18);
  display: flex;
  flex-direction: column;
  justify-content: stretch;
}}
.interest__form {{
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 16px 14px;
}}
.interest__field {{
  display: grid;
  gap: 6px;
  grid-column: 1 / -1;
}}
.interest__field--cc {{ grid-column: 1 / 2; }}
.interest__field--phone {{ grid-column: 2 / -1; }}
.interest__form .interest__submit {{ grid-column: 1 / -1; }}
.interest__form .interest__helper {{ grid-column: 1 / -1; }}
.interest__form .interest__form__hidden,
.interest__form input[type="hidden"] {{ display: none !important; }}
.interest__support {{
  margin: 18px 0 0;
  font-size: 14.5px;
  line-height: 1.55;
  color: var(--ink-muted);
}}
.interest__label {{
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  font-weight: 600;
}}
.interest__form input,
.interest__form textarea {{
  appearance: none;
  -webkit-appearance: none;
  background: rgba(0,0,0,0.03);
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 12px;
  padding: 13px 14px;
  font: inherit;
  color: var(--card-ink);
  width: 100%;
  box-sizing: border-box;
}}
.interest__form textarea {{
  resize: vertical;
  min-height: 88px;
}}
.interest__form input:focus,
.interest__form textarea:focus {{
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  background: #FFFFFF;
}}
.interest__submit {{
  margin-top: 6px;
  text-align: center;
  width: 100%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}}
.interest__helper {{
  font-size: 12.5px;
  color: var(--card-ink-muted);
  margin: 6px 0 0;
  line-height: 1.5;
}}

/* Tablet */
@media (max-width: 1100px) {{
  .live-ads-layout {{ grid-template-columns: 1fr; gap: 32px; }}
  .live-ads__intro {{ position: static; }}
  .concepts {{ grid-template-columns: repeat(2, 1fr); }}
  .pricing {{ grid-template-columns: 1fr; }}
  .preview__layout {{ grid-template-columns: 1fr; gap: 32px; }}
  .next-step {{ grid-template-columns: 1fr; gap: 32px; }}
  .next-step__visual {{ max-width: 360px; }}
  .gap-map__head, .gap-map__row {{ grid-template-columns: 1fr 1fr; }}
  .gap-map__test, .gap-map__conf {{ grid-column: 1 / -1; }}
  .concepts-rail {{ grid-auto-columns: minmax(260px, 76%); }}
  .interest {{ grid-template-columns: 1fr; gap: 32px; }}
  .interest__form {{ grid-template-columns: 100px minmax(0, 1fr); }}
}}

/* Mobile */
@media (max-width: 720px) {{
  .deck {{ padding: 0 20px 16px; }}
  .topbar, .footer {{ padding: 14px 20px; font-size: 11px; }}
  .footer {{ flex-direction: column; gap: 6px; align-items: flex-start; padding-bottom: 120px; }}
  .section {{ padding: 64px 0 80px; }}
  .section__title {{ font-size: 36px; max-width: none; }}
  .section__lede {{ font-size: 16.5px; margin-bottom: 36px; }}
  .hero {{ min-height: auto; padding: 32px 0 64px; gap: 28px; }}
  .hero__head {{ flex-wrap: wrap; gap: 14px; }}
  .hero__monogram {{ width: 60px; height: 60px; font-size: 22px; }}
  .hero__badge {{ font-size: 10px; padding: 8px 12px; }}
  .hero__headline {{ font-size: clamp(38px, 11vw, 56px); }}
  .hero__subhead {{ font-size: 16.5px; margin-bottom: 28px; }}
  .hero__byline {{ flex-direction: column; gap: 6px; align-items: flex-start; }}
  .cards--2x2 {{ grid-template-columns: 1fr; }}
  .ad-card {{ grid-template-columns: 1fr; min-height: 0; }}
  .ad-card__preview {{ max-width: 240px; aspect-ratio: 9 / 14; }}
  .concepts {{ grid-template-columns: 1fr; }}
  .concepts-rail {{
    grid-auto-columns: 86%;
    padding-right: 32px;
  }}
  .process {{ grid-template-columns: 1fr; }}
  .gap-map__head, .gap-map__row {{ grid-template-columns: 1fr; padding: 18px 20px; gap: 12px; }}
  .gap-map__head {{ display: none; }}
  .gap-map__pattern {{ font-size: 16px; }}
  .gap-map__test {{ border-left: none; padding-left: 0; border-top: 1px solid rgba(0,0,0,0.06); padding-top: 12px; }}
  .next-step__visual {{ max-width: none; }}
  .btn {{ padding: 13px 18px; font-size: 12px; }}
  .sticky-cta {{
    right: 16px;
    left: 16px;
    bottom: 16px;
    justify-content: center;
    padding: 14px 20px;
  }}
  .sticky-cta__label {{ font-size: 12px; }}
}}

/* Print: one section per page, no chrome */
@media print {{
  body {{ background: #0A0A0A; }}
  .progress-bar, .topbar, .footer, .sticky-cta, .status-banner {{ display: none; }}
  .deck {{ padding: 0; max-width: none; }}
  .section {{
    page-break-after: always;
    min-height: 0;
    border: none;
    padding: 40px 36px;
  }}
  .section:last-of-type {{ page-break-after: auto; }}
  .hero {{ padding: 40px 36px; min-height: 0; }}
  .hero__headline {{ font-size: 64px; }}
  .next-step__head {{ font-size: 64px; }}
  .section__title {{ font-size: 42px; }}
  [data-reveal] {{ opacity: 1; transform: none; transition: none; }}
}}

@page {{
  size: A4 landscape;
  margin: 0;
}}
"""


# --------------------------------------------------------------------------- #
# Section renderers
# --------------------------------------------------------------------------- #


def _section_hero(brief: DeckBrief, assets: _AssetResolver, used: set[Path]) -> str:
    """Above-the-fold hero. Brand image as full-bleed background, monogram,
    private-note badge, custom headline, sub-head, primary CTA pointing at
    the preview-video section.

    V5: hero__monogram is ALWAYS a small text chip. Embedding the logo
    image stretched a 32px favicon into a 76px brand-color circle which
    looked like a "giant YA block" for brands with dark primary colors.
    The wordmark stays in the topbar; the small chip carries just the
    initials. The full logo path is still consumed so the renderer's
    used_paths set stays consistent.
    """
    if brief.logo_path and brief.logo_path not in used:
        used.add(brief.logo_path)
    hero_url = assets.url_for(brief.hero_image_path) if brief.hero_image_path else None
    if brief.hero_image_path and brief.hero_image_path not in used:
        used.add(brief.hero_image_path)

    # Text initials only - never an image. Capped to 2 chars by `_initials`.
    monogram_html = _e(_initials(brief.prospect_name))
    if hero_url:
        bg_html = (
            f'<div class="hero__bg">'
            f'<img src="{_e(hero_url)}" alt="" loading="eager">'
            f'</div>'
        )
    else:
        # Designed fallback - never just a dark rectangle, never a
        # giant centred monogram. We render a layered collage of small
        # cards (proof chip + preview frame + caption sliver) so the
        # composition reads as intentional and the brand-mark stays
        # subtle. The hero__bg--mock CSS draws the brand-gradient floor
        # and decorative orbs behind these elements.
        initials = _initials(brief.prospect_name)
        bg_html = f"""
<div class="hero__bg hero__bg--mock" aria-hidden="true">
  <div class="hero__mock-collage">
    <div class="hero__mock-card hero__mock-card--proof">
      <span class="hero__mock-card-label">LIVE AD READ</span>
      <span class="hero__mock-card-meta">Sampled in market</span>
    </div>
    <div class="hero__mock-card hero__mock-card--frame">
      <span class="hero__mock-card-label">9:16 ROUTE FRAME</span>
      <div class="hero__mock-card-bars">
        <span></span><span></span><span></span>
      </div>
      <span class="hero__mock-card-corner">{_e(initials[:2])}</span>
    </div>
    <div class="hero__mock-card hero__mock-card--caption">
      <span class="hero__mock-card-label">FIRST ROUTE</span>
      <span class="hero__mock-card-text">One opening. One audience. One offer.</span>
    </div>
  </div>
</div>"""

    tone_byline = (
        _e(brief.brand_tone) if brief.brand_tone else _e(brief.niche)
    )

    return f"""
<section class="section hero" data-slide="1">
  {bg_html}
  <div class="hero__gradient" aria-hidden="true"></div>
  <div class="hero__head">
    <div class="hero__monogram">{monogram_html}</div>
    <span class="hero__badge">Private for {_e(brief.prospect_name)}</span>
  </div>
  <div class="hero__body">
    <h1 class="hero__headline">{_e(brief.cover_headline)}</h1>
    <p class="hero__subhead">{_e(brief.cover_subhead)}</p>
    <p class="hero__explainer">A short, senior-led creative note - one route picked from your active ads, ready to ship as a watermarked first cut.</p>
    <div class="hero__actions">
      <a class="btn btn--primary" href="#preview-video">Show me the first route</a>
      <a class="btn btn--ghost" href="#live-ads">See what we'd change</a>
    </div>
  </div>
  <div class="hero__byline">
    <span>By {_e(brief.agency_name)}</span>
    <span>{tone_byline}</span>
  </div>
</section>
"""


def _section_45_second(brief: DeckBrief) -> str:
    cards = "".join(
        f"""
<div class="card">
  <span class="card__kicker">{_e(c.label)}</span>
  <p class="card__body">{_e(c.body)}</p>
</div>"""
        for c in brief.forty_five_second_cards
    )
    return f"""
<section class="section" id="snapshot" data-slide="2" data-reveal>
  <div class="section__meta"><span class="section__index">02</span><span>The 45-second version</span></div>
  <h2 class="section__title">If you only read four cards, read these.</h2>
  <p class="section__lede">A short, honest read of what we'd actually do for {_e(brief.prospect_name)} - and what it costs to try.</p>
  <div class="cards cards--2x2">{cards}</div>
</section>
"""


def _section_live_ads(brief: DeckBrief, assets: _AssetResolver, used: set[Path]) -> str:
    """V3 sticky-pin layout: left intro panel pins while the ad-card stack
    on the right scrolls past. On mobile the layout collapses back to a
    single column. The intro repeats the 'why this section exists' line
    and credits the public sources."""
    if not brief.ads:
        body = (
            '<div class="live-ads__stack">'
            '<p class="section__lede">No live ads were captured for this prospect. '
            'When the Meta Ads Library returns an empty result, we lean on the '
            'gap map and concept board instead.</p>'
            '</div>'
        )
    else:
        cards = "".join(
            _render_ad_card(ad, brief, assets, used, reveal_index=i)
            for i, ad in enumerate(brief.ads)
        )
        body = f'<div class="live-ads__stack">{cards}</div>'
    return f"""
<section class="section" id="live-ads" data-slide="3" data-reveal>
  <div class="section__meta"><span class="section__index">03</span><span>What we saw in your ads</span></div>
  <div class="live-ads-layout">
    <div class="live-ads__intro">
      <h2 class="section__title">Your real ads - and the routes we'd test next to them.</h2>
      <p class="section__lede">Your live library, one card at a time - what each ad is doing, what's holding it back, and the route we'd ship next to it.</p>
      <span class="section__footnote">Sources: public Meta Ad Library + brand website.</span>
      <span class="live-ads__progress" aria-hidden="true"><span class="live-ads__progress-fill"></span></span>
    </div>
    {body}
  </div>
</section>
"""


def _render_ad_card(
    ad: AdProof,
    brief: DeckBrief,
    assets: _AssetResolver,
    used: set[Path],
    *,
    reveal_index: int = 0,
) -> str:
    """One ad-proof card.

    When a real ad screenshot is available it becomes the dominant visual,
    with floating chip overlays (LIVE AD, days active) and an 'Open ad'
    pill bottom-right. When not, a Meta-shaped fallback (page avatar +
    sponsored chip + body image area + CTA bar) renders instead - never
    a generic placeholder.
    """
    preview_url = assets.url_for(ad.screenshot_path) if ad.screenshot_path else None
    if ad.screenshot_path and ad.screenshot_path not in used:
        used.add(ad.screenshot_path)
    days_label = (
        f"Active {ad.days_active} days"
        if ad.days_active
        else "Active in the current library"
    )
    if preview_url:
        preview_html = f"""
<div class="ad-card__preview">
  <img src="{_e(preview_url)}" alt="ad preview" loading="lazy">
  <div class="ad-card__overlay-top">
    <span class="ad-card__chip ad-card__chip--live">Live ad</span>
    <span class="ad-card__chip">{_e(days_label)}</span>
  </div>
  <div class="ad-card__overlay-bottom">
    <span></span>
    <a class="ad-card__overlay-open" href="{_e(ad.library_url)}" target="_blank" rel="noopener">Open ad</a>
  </div>
</div>"""
    else:
        excerpt = ad.body_excerpt or f"{brief.prospect_name} ad"
        excerpt_short = excerpt if len(excerpt) <= 90 else excerpt[:87] + "..."
        avatar_initials = _initials(brief.prospect_name)
        cta_label = ad.cta_text or "Shop now"
        preview_html = f"""
<div class="ad-card__preview ad-card__preview--mock">
  <div class="ad-card__preview-head">
    <div class="ad-card__preview-avatar">{_e(avatar_initials)}</div>
    <div class="ad-card__preview-meta">
      <strong>{_e(brief.prospect_name)}</strong>
      <span>Sponsored &middot; {_e(days_label)}</span>
    </div>
    <span class="ad-card__preview-sponsored">Meta</span>
  </div>
  <div class="ad-card__preview-body"><span>{_e(excerpt_short)}</span></div>
  <div class="ad-card__preview-cta">
    <span>{_e(brief.website_url) if brief.website_url else _e(brief.prospect_name)}</span>
    <span class="ad-card__preview-cta-button">{_e(cta_label)}</span>
  </div>
</div>"""
    cta_state = ad.cta_text or "No CTA captured"
    return f"""
<article class="ad-card" data-reveal data-reveal-index="{reveal_index}">
  {preview_html}
  <div class="ad-card__content">
    <div class="ad-card__header">
      <span class="ad-card__brandname">{_e(brief.prospect_name)} on Meta</span>
      &middot;
      <span>{_e(days_label)}</span>
    </div>
    <span class="ad-card__tag">{_e(ad.issue_label)}</span>
    <p class="ad-card__excerpt">{_e(ad.body_excerpt or 'No copy captured in the audit sample.')}</p>
    <p class="ad-card__diag">{_e(ad.issue_explainer)}</p>
    <div class="ad-card__route">
      <strong>What we'd test instead</strong>
      {_e(ad.suggested_route)}
    </div>
    <div class="ad-card__footer">
      <span class="ad-card__meta">CTA: {_e(cta_state)}</span>
      <a class="ad-card__open" href="{_e(ad.library_url)}" target="_blank" rel="noopener">Open ad</a>
    </div>
  </div>
</article>
"""


def _section_gap_map(brief: DeckBrief) -> str:
    """What's missing - patterns we'd untie, in a scannable table."""
    if not brief.gap_map_rows:
        body = (
            '<p class="section__lede">No clear gap pattern was captured for this '
            'prospect. We will go light on the diagnosis and lean on the '
            'concept board.</p>'
        )
    else:
        rows = "".join(_render_gap_row(r) for r in brief.gap_map_rows)
        body = f"""
<div class="gap-map">
  <div class="gap-map__head">
    <span>Current pattern</span>
    <span>Why it limits growth</span>
    <span>Short-form test to run</span>
    <span>Confidence</span>
  </div>
  {rows}
</div>"""
    return f"""
<section class="section section--cream" id="gap-map" data-slide="4" data-reveal>
  <div class="section__meta"><span class="section__index">04</span><span>What's missing</span></div>
  <h2 class="section__title">The patterns we'd untie - and what to ship in their place.</h2>
  {body}
</section>
"""


def _render_gap_row(row: GapMapRow) -> str:
    conf_cls = "gap-map__conf"
    if row.confidence == "high":
        conf_cls += " gap-map__conf--high"
        conf_label = "High"
    elif row.confidence == "medium":
        conf_cls += " gap-map__conf--medium"
        conf_label = "Medium"
    elif row.confidence == "low":
        conf_cls += " gap-map__conf--low"
        conf_label = "Low"
    else:
        conf_cls += " gap-map__conf--low"
        conf_label = "Signal"
    return f"""
<div class="gap-map__row">
  <div class="gap-map__pattern">{_e(row.current_pattern)}</div>
  <div class="gap-map__diag">{_e(row.why_it_limits_growth)}</div>
  <div class="gap-map__test">{_e(row.ugc_test)}</div>
  <div class="{conf_cls}">{_e(conf_label)}</div>
</div>
"""



def _section_concept_board(brief: DeckBrief, assets: _AssetResolver, used: set[Path]) -> str:
    """Concept rail - the page's primary concept board.

    Renders every concept from `brief.concepts` as a phone-mockup card
    on a horizontal scroll-snap rail. Real product images go to the
    first cards in order; remaining cards render the cream-artboard
    fallback. The previous V7 'featured-route' hero section is gone -
    a single primary concept rail is the only concepts surface.
    """
    brand_initials = _initials(brief.prospect_name)
    cards = "".join(
        _render_concept(
            c, assets, used,
            reveal_index=i,
            brand_initials=brand_initials,
            brand_name=brief.prospect_name,
        )
        for i, c in enumerate(brief.concepts)
    )
    if not cards:
        return """
<section class="section" id="concepts" data-slide="5" data-reveal>
  <div class="section__meta"><span class="section__index">05</span><span>The routes we'd test</span></div>
  <h2 class="section__title">The routes we'd test.</h2>
  <p class="section__lede">No concept routes available.</p>
</section>"""
    return f"""
<section class="section" id="concepts" data-slide="5" data-reveal>
  <div class="section__meta"><span class="section__index">05</span><span>The routes we'd test</span></div>
  <h2 class="section__title">Four routes we'd test first.</h2>
  <p class="section__lede">Each route is a 12-15 second short-form cut. Same product, same audience, different first three seconds.</p>
  <span class="concepts-rail-hint">Swipe the rail</span>
  <div class="concepts-rail">{cards}</div>
</section>
"""


def _render_concept_phone(
    c: ConceptRoute,
    assets: _AssetResolver,
    used: set[Path],
    *,
    brand_initials: str = "AD",
    brand_name: str = "",
    variant_index: int = 0,
) -> str:
    """Render JUST the 9:16 phone-frame portion of a concept card.

    Used only by `_render_concept` now that V7's featured-route hero
    has been rolled back. Kept as a separate helper so the rail card
    markup stays compact.

    Image policy (unchanged from V4):
      * `visual_path` set + path not already embedded -> embed <img>.
      * Otherwise -> render the cream-artboard designed fallback.
    """
    visual_url = assets.url_for(c.visual_path) if c.visual_path else None
    embed_image = bool(visual_url and c.visual_path and c.visual_path not in used)
    if embed_image:
        used.add(c.visual_path)  # type: ignore[arg-type]

    if embed_image:
        return f"""
<div class="concept__phone concept__phone--filled" aria-label="{_e(c.title)} concept still">
  <img src="{_e(visual_url)}" alt="{_e(brand_name or 'brand product')} concept still" loading="lazy">
  <div class="concept__phone-meta">
    <span class="concept__phone-meta-dot" aria-hidden="true"></span>
    <span>{_e(c.label)} &middot; 9:16 ROUTE FRAME</span>
  </div>
  <div class="concept__overlay">
    <span class="concept__overlay-hook">{_e(c.hook)}</span>
    <span class="concept__overlay-cta">{_e(c.cta)}</span>
  </div>
</div>"""

    # Designed fallback - hook-led cream artboard, no giant initials.
    variant = (variant_index % 4) + 1
    corner_chip = _e(brand_initials)[:2]
    return f"""
<div class="concept__phone concept__phone--designed concept__phone--variant-{variant}" role="img" aria-label="{_e(c.title)} concept frame">
  <span class="concept__phone-noise" aria-hidden="true"></span>
  <span class="concept__phone-shape concept__phone-shape--a" aria-hidden="true"></span>
  <span class="concept__phone-shape concept__phone-shape--b" aria-hidden="true"></span>
  <span class="concept__phone-shape concept__phone-shape--accent" aria-hidden="true"></span>
  <div class="concept__phone-frame-label">
    <span class="concept__phone-frame-dot" aria-hidden="true"></span>
    <span>{_e(c.label)} &middot; OPENING FRAME</span>
  </div>
  <span class="concept__phone-corner-mark" aria-hidden="true">{corner_chip}</span>
  <div class="concept__phone-hook-block">
    <span class="concept__phone-hook-eyebrow">HOOK</span>
    <div class="concept__phone-hook">{_e(c.hook)}</div>
  </div>
  <div class="concept__phone-cta-row">
    <span class="concept__phone-cta">{_e(c.cta)}</span>
    <span class="concept__phone-scene-tag" aria-hidden="true">01 / SCENE</span>
  </div>
</div>"""


def _render_concept(
    c: ConceptRoute,
    assets: _AssetResolver,
    used: set[Path],
    *,
    reveal_index: int = 0,
    brand_initials: str = "AD",
    brand_name: str = "",
) -> str:
    """Render a single concept card for the rail."""
    phone_html = _render_concept_phone(
        c, assets, used,
        brand_initials=brand_initials,
        brand_name=brand_name,
        variant_index=reveal_index,
    )
    return f"""
<article class="concept" data-reveal data-reveal-index="{reveal_index}">
  {phone_html}
  <span class="concept__label">{_e(c.label)}</span>
  <h3 class="concept__title">{_e(c.title)}</h3>
  <p class="concept__hook">{_e(c.hook)}</p>
  <div class="concept__test">
    <strong>What this tests</strong>
    A new opening against the same product - body, audience and CTA held constant.
  </div>
  <span class="concept__cta">{_e(c.cta)}</span>
</article>
"""


def _section_how_this_works(brief: DeckBrief) -> str:
    steps = "".join(
        f"""
<div class="process__step">
  <div class="process__num">{_e(s.number)}</div>
  <div>
    <div class="process__label">{_e(s.label)}</div>
    <div class="process__desc">{_e(s.description)}</div>
  </div>
</div>"""
        for s in brief.process_steps
    )
    return f"""
<section class="section" id="process" data-slide="6" data-reveal>
  <div class="section__meta"><span class="section__index">06</span><span>How simple this is</span></div>
  <h2 class="section__title">From your inputs to a finished cut.</h2>
  <p class="section__lede">A short, senior-led process - your imagery, your tone, our edit. Two-round revisions, fixed price, original files yours.</p>
  <div class="process">{steps}</div>
</section>
"""


def _section_preview_video(brief: DeckBrief, preview_video_url: Optional[str]) -> str:
    """Always rendered. Real video when URL provided, premium phone-frame
    placeholder otherwise. Anchored at #preview-video for the sticky CTA.

    Even the empty state is meant to feel like a locked premium preview -
    not a 'coming soon' card. Copy reads as 'this is reserved for you',
    not as 'we have nothing yet'."""
    if preview_video_url:
        frame = f"""
<div class="preview__frame preview__frame--filled">
  <div class="preview__frame-bar">
    <span class="preview__frame-bar-dot"></span>
    <span>Watermarked route preview</span>
    <span class="preview__frame-bar-meta">{_e(brief.prospect_name)}</span>
  </div>
  <video class="preview__video" controls preload="metadata" playsinline>
    <source src="{_e(preview_video_url)}" type="video/mp4">
    Your browser cannot play this preview. <a href="{_e(preview_video_url)}">Download it</a> instead.
  </video>
  <div class="preview__watermark-note">
    <span>Watermarked preview &middot; clean export available after approval</span>
    <span>{_e(brief.prospect_name)}</span>
  </div>
</div>
"""
        copy_body = (
            f"A short watermarked cut of one of the routes above - made for {_e(brief.prospect_name)}, "
            "ready to play. The clean export sits behind the next step: reply and we will "
            "share the watermark-free MP4 with source files."
        )
        cta_label = "Want the clean version?"
    else:
        frame = """
<div class="preview__frame preview__frame--locked">
  <div class="preview__frame-bar">
    <span class="preview__frame-bar-dot"></span>
    <span>Watermarked route preview</span>
    <span class="preview__frame-bar-meta">Reserved</span>
  </div>
  <div class="preview__placeholder">
    <div class="preview__placeholder-body">
      <div class="preview__placeholder-glyph">&#9658;</div>
      <div class="preview__placeholder-title">Your first route lives here</div>
      <p class="preview__placeholder-sub">A 12-15 second watermarked cut, made from imagery you already own.</p>
    </div>
    <div class="preview__placeholder-lock">
      <span class="preview__placeholder-lock-icon">&#x1F512;</span>
      <span>Clean export unlocks after approval</span>
    </div>
  </div>
</div>
"""
        copy_body = (
            "Once approved, this is where the watermarked first route sits - "
            "a short cut made from imagery you already own."
        )
        cta_label = "Send the route request"

    return f"""
<section class="section preview" id="preview-video" data-slide="preview" data-reveal>
  <div class="section__meta"><span class="section__index">PREVIEW</span><span>Watermarked route preview</span></div>
  <div class="preview__layout">
    <div class="preview__copy">
      <h2 class="section__title">Here is where your first route would sit.</h2>
      <p>{copy_body}</p>
      <span class="preview__sub">Watermarked while you decide &middot; clean version after approval</span>
      <a class="btn btn--primary" href="#interest">{cta_label}</a>
    </div>
    {frame}
  </div>
</section>
"""


def _section_pricing(brief: DeckBrief) -> str:
    tiers = "".join(_render_tier(t) for t in brief.pricing)
    return f"""
<section class="section" id="pricing" data-slide="7" data-reveal>
  <div class="section__meta"><span class="section__index">07</span><span>The price</span></div>
  <h2 class="section__title">Three ways to try.</h2>
  <p class="section__lede">Fixed price, pay per finished cut. Scale only the routes that earn their place.</p>
  <div class="pricing">{tiers}</div>
</section>
"""


def _render_tier(t: PricingTier) -> str:
    cls = "tier tier--rec" if t.is_recommended else "tier"
    rec_tag = '<span class="tier__rec-tag">Recommended</span>' if t.is_recommended else ""
    bullets = "".join(f"<li>{_e(b)}</li>" for b in t.bullets)
    return f"""
<article class="{cls}">
  {rec_tag}
  <span class="tier__name">{_e(t.name)}</span>
  <div class="tier__price">{_e(t.price)}</div>
  <p class="tier__tagline">{_e(t.tagline)}</p>
  <ul class="tier__bullets">{bullets}</ul>
</article>
"""


def _section_next_step(brief: DeckBrief) -> str:
    """Closing CTA. Primary button reads as a direct reply instruction so
    the prospect knows exactly what to write back; secondary buttons
    surface the prospect's own ad/site/page so reviewers can verify."""
    actions: list[str] = ['<a class="btn btn--primary" href="#interest">Reply with &lsquo;send the first route&rsquo;</a>']
    if brief.website_url:
        actions.append(f'<a class="btn" href="{_e(brief.website_url)}" target="_blank" rel="noopener">Open website</a>')
    if brief.ads and brief.ads[0].library_url:
        actions.append(f'<a class="btn" href="{_e(brief.ads[0].library_url)}" target="_blank" rel="noopener">Open active ad</a>')
    if brief.facebook_url:
        actions.append(f'<a class="btn" href="{_e(brief.facebook_url)}" target="_blank" rel="noopener">Open Facebook</a>')
    actions_html = "".join(actions)

    visual_initials = _initials(brief.prospect_name)[:2]
    name = _e(brief.prospect_name) or "your brand"
    return f"""
<section class="section" id="next-step" data-slide="8" data-reveal>
  <div class="section__meta"><span class="section__index">08</span><span>What to do next</span></div>
  <div class="next-step">
    <div>
      <h2 class="next-step__head">{_e(brief.cta_headline)}</h2>
      <p class="next-step__body">{_e(brief.cta_body)}</p>
      <div class="next-step__actions">{actions_html}</div>
    </div>
    <div class="next-step__visual" role="img" aria-label="First route waits for {name}">
      <span class="next-step__visual-shape next-step__visual-shape--a" aria-hidden="true"></span>
      <span class="next-step__visual-shape next-step__visual-shape--b" aria-hidden="true"></span>
      <span class="next-step__visual-shape next-step__visual-shape--accent" aria-hidden="true"></span>
      <span class="next-step__visual-corner" aria-hidden="true">{_e(visual_initials)}</span>
      <div class="next-step__visual-label">
        <span class="next-step__visual-label-dot" aria-hidden="true"></span>
        <span>FIRST ROUTE &middot; RESERVED</span>
      </div>
      <h3 class="next-step__visual-headline">One opening. One audience. One offer.</h3>
      <div class="next-step__visual-meta">
        <span>9:16 watermarked cut</span>
        <span>Clean export after approval</span>
      </div>
    </div>
  </div>
</section>
"""


# --------------------------------------------------------------------------- #
# Interest form section
# --------------------------------------------------------------------------- #


_INTEREST_TITLE = "Want us to make the first route?"
_INTEREST_SUBTITLE = (
    "Share the best way to reach you and we'll send the next step personally."
)
_INTEREST_SUPPORT = (
    "Leave your contact details and two or three times that usually work. "
    "We'll reply with a simple next step for the first watermarked route."
)

# Visible form fields. Each tuple is (name, label, type, is_textarea,
# wrapper_class). The wrapper_class drives a CSS grid placement so
# country code + phone sit on the same row.
_INTEREST_FIELDS = (
    ("name",                    "Your name",            "text",  False, "interest__field--name"),
    ("email",                   "Email",                "email", False, "interest__field--email"),
    ("country_code",            "Country code",         "text",  False, "interest__field--cc"),
    ("phone",                   "Phone number",         "tel",   False, "interest__field--phone"),
    ("preferred_contact_method", "Preferred contact method (email / WhatsApp / phone)", "text", False, "interest__field--contact"),
    ("availability",            "Preferred times",      "text",  False, "interest__field--avail"),
    ("message",                 "Anything you want us to know first?", "textarea", True, "interest__field--message"),
)


def _mailto_template(
    *,
    contact_email: str,
    brand_name: str,
    prospect_id: Optional[str],
    private_slug: Optional[str],
    public_url: Optional[str],
) -> str:
    """Build the `mailto:` URL with a useful pre-filled subject + body.

    The subject names the brand so the operator inbox is auto-threaded.
    The body carries the prospect identifiers as labelled lines so the
    operator knows which audit the reply belongs to without parsing the
    URL by hand, plus blank labelled lines for every visible form
    field so the prospect can fill them in.
    """
    from urllib.parse import quote

    subject = f"Interested in the first route for {brand_name}"
    body_lines = [
        f"Brand: {brand_name}",
        f"Prospect ID: {prospect_id or '(unspecified)'}",
        f"Private slug: {private_slug or '(unspecified)'}",
        f"Public URL: {public_url or '(local draft)'}",
        "",
        "Name:",
        "Email:",
        "Country code:",
        "Phone number:",
        "Preferred contact method:",
        "Preferred call times:",
        "Message:",
        "",
    ]
    return (
        f"mailto:{quote(contact_email)}"
        f"?subject={quote(subject)}"
        f"&body={quote(chr(10).join(body_lines))}"
    )


def _interest_hidden_fields(
    *,
    brand_name: str,
    prospect_id: Optional[str],
    private_slug: Optional[str],
    public_url: Optional[str],
) -> str:
    """Hidden inputs that travel with a real POST submission."""
    items = (
        ("brand_name", brand_name),
        ("prospect_id", prospect_id or ""),
        ("private_slug", private_slug or ""),
        ("public_url", public_url or ""),
    )
    return "".join(
        f'<input type="hidden" name="{_e(name)}" value="{_e(value)}">'
        for name, value in items
    )


def _interest_visible_fields() -> str:
    """Form inputs shown to the prospect. Same set is reused for the
    mailto-mode preview card (visual only - inputs there are inert)."""
    rows: list[str] = []
    for name, label, type_, is_textarea, wrap_cls in _INTEREST_FIELDS:
        if is_textarea:
            ctrl = (
                f'<textarea id="interest-{name}" name="{name}" rows="3" '
                f'placeholder="{_e(label)}"></textarea>'
            )
        elif name == "country_code":
            ctrl = (
                f'<input id="interest-{name}" type="text" name="{name}" '
                f'inputmode="tel" placeholder="+44" autocomplete="tel-country-code" '
                f'maxlength="6">'
            )
        elif name == "phone":
            ctrl = (
                f'<input id="interest-{name}" type="tel" name="{name}" '
                f'placeholder="7700 900000" autocomplete="tel-national" '
                f'inputmode="tel">'
            )
        else:
            ctrl = (
                f'<input id="interest-{name}" type="{type_}" name="{name}" '
                f'placeholder="{_e(label)}">'
            )
        rows.append(
            f'<label class="interest__field {wrap_cls}">'
            f'<span class="interest__label">{_e(label)}</span>{ctrl}</label>'
        )
    return "".join(rows)


def _section_interest(
    brief: DeckBrief,
    *,
    contact_email: str,
    form_endpoint: Optional[str],
    prospect_id: Optional[str],
    private_slug: Optional[str],
    public_url: Optional[str],
) -> str:
    """Closing-most section. Two modes:

      MVP (mailto): no endpoint configured. Renders the same polished
      form-style card but the submit button is an `<a href="mailto:...">`
      with a brand-named subject and pre-filled body. Browsers open the
      operator's default email client.

      Future (endpoint): `form_endpoint` is set (e.g. `/api/interest`).
      Renders a real `<form method="POST" action="<endpoint>">` plus
      hidden brand/prospect identifiers. The frontend never carries a
      secret - the Cloudflare Pages Function / Worker behind that path
      reads its own bindings and decides where to forward the message.
    """
    brand_name = brief.prospect_name or "your brand"
    visible_inputs = _interest_visible_fields()

    if form_endpoint:
        hidden = _interest_hidden_fields(
            brand_name=brand_name,
            prospect_id=prospect_id,
            private_slug=private_slug,
            public_url=public_url,
        )
        action_url = _e(form_endpoint)
        # The microcopy is direct so a reviewer can audit the security
        # story without reading code: the endpoint is the only place
        # where credentials live.
        helper_note = (
            "A private route request, reviewed manually by Yuvo Studio."
        )
        action_html = f"""
<form class="interest__form" method="POST" action="{action_url}" novalidate>
  {hidden}
  {visible_inputs}
  <button class="btn btn--primary interest__submit" type="submit">Send my route request</button>
  <p class="interest__helper">{_e(helper_note)}</p>
</form>"""
    else:
        mailto_url = _mailto_template(
            contact_email=contact_email,
            brand_name=brand_name,
            prospect_id=prospect_id,
            private_slug=private_slug,
            public_url=public_url,
        )
        helper_note = (
            f"A private route request, reviewed manually by Yuvo Studio. "
            f"Opens your email app pre-addressed to {contact_email}."
        )
        action_html = f"""
<div class="interest__form interest__form--mailto" data-mode="mailto">
  {visible_inputs}
  <a class="btn btn--primary interest__submit" href="{_e(mailto_url)}">
    Email my route request
  </a>
  <p class="interest__helper">{_e(helper_note)}</p>
</div>"""

    return f"""
<section class="section" id="interest" data-slide="interest" data-reveal>
  <div class="section__meta"><span class="section__index">FIRST ROUTE</span><span>Send the request</span></div>
  <div class="interest">
    <div class="interest__copy">
      <h2 class="section__title">{_e(_INTEREST_TITLE)}</h2>
      <p class="section__lede">{_e(_INTEREST_SUBTITLE)}</p>
      <p class="interest__support">{_e(_INTEREST_SUPPORT)}</p>
      <ul class="interest__bullets">
        <li>One first-route concept, built around your current ads.</li>
        <li>A low-friction test using the assets you already have.</li>
        <li>A personal reply from Yuvo Studio.</li>
      </ul>
    </div>
    <div class="interest__panel">{action_html}</div>
  </div>
</section>
"""


# --------------------------------------------------------------------------- #
# Misc helpers
# --------------------------------------------------------------------------- #


def _initials(name: str) -> str:
    """Up to 2 uppercase letters from a brand name, for the cover monogram."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "AD"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][:1] + parts[1][:1]).upper()


def _accent_for_dark_bg(hex_color: str) -> str:
    """Return a version of `hex_color` bright enough to read against #0A0A0A.

    Monochrome brands (e.g. YANA's #1A1A1A) carry a primary colour that is
    almost identical to our editorial background and would otherwise
    disappear on the cover dot, slide index, and process numerals. We mix
    the colour with cream until the perceived brightness clears a safe
    floor (~140 on the 0-255 scale).
    """
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
