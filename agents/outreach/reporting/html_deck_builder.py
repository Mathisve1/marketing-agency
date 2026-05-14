"""HTML/CSS deck builder - the new client-facing route.

Takes a `DeckBrief` and writes a single self-contained HTML file to
`prospects/<id>/deck/index.html`. Open in any modern browser. Print
to PDF with Cmd/Ctrl+P -> 'Save as PDF' for a paper deliverable.

Design philosophy:
  - Black editorial background (#0A0A0A), warm cream cards (#F5F0E8).
  - Prospect brand is the hero - their primary_color drives the accent.
  - Inter / Inter Tight via Google Fonts CDN; falls back cleanly to
    system sans-serif when offline (no broken-font flash).
  - Inline CSS only. No external JS. No remote images fetched at render
    time. All assets are referenced as `../assets/<file>` so the deck
    folder stays decoupled from the audit.
  - Each image asset is used at most once across the whole deck (the
    `DeckBrief` already enforces this; the builder asserts on render).
  - Print CSS lays out one slide per A4 page so browser-print -> PDF
    gives a clean handout.

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


def build_html_deck(
    brief: DeckBrief,
    *,
    output_dir: Optional[Path] = None,
    noindex: bool = False,
    preview_video_url: Optional[str] = None,
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
      preview_video_url: URL (relative or absolute) of a watermarked
        preview MP4. When set, the deck gets an extra "Your first route
        preview" slide between Pricing and Next step. The caller is
        responsible for the watermark guarantee; this layer just
        embeds what it is told.
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
            # On Windows relpath uses backslashes; HTML wants forward slashes.
            return Path(rel).as_posix()
        except ValueError:
            # Cross-drive on Windows: relpath raises. Fall back to file URI.
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
) -> str:
    """Top-level HTML render. Each slide is composed by a private helper."""
    css = _stylesheet(brief)
    slides = [
        _slide_cover(brief, assets, used),
        _slide_45_second(brief),
        _slide_live_ads(brief, assets, used),
        _slide_gap_map(brief),
        _slide_concept_board(brief, assets, used),
        _slide_how_this_works(brief),
        _slide_pricing(brief),
    ]
    if preview_video_url:
        slides.append(_slide_preview_video(brief, preview_video_url))
    slides.append(_slide_next_step(brief))

    title = f"{_e(brief.prospect_name)} - Private Creative Note"
    robots_meta = (
        '<meta name="robots" content="noindex,nofollow">' if noindex else ""
    )
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
<header class="topbar">
  <span class="topbar__kicker">{_e(brief.cover_kicker)}</span>
  <span class="topbar__brand">{_e(brief.prospect_name)} &times; {_e(brief.agency_name)}</span>
</header>
<main class="deck">
{"".join(slides)}
</main>
<footer class="footer">
  <span>{_e(brief.agency_name)} &mdash; private creative note</span>
  <span>Prepared for {_e(brief.prospect_name)}</span>
</footer>
</body>
</html>"""


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
  --ink: #F5F0E8;
  --ink-muted: rgba(245, 240, 232, 0.62);
  --ink-faint: rgba(245, 240, 232, 0.36);
  --card: #F5F0E8;
  --card-ink: #131210;
  --card-ink-muted: rgba(19, 18, 16, 0.62);
  --hairline: rgba(245, 240, 232, 0.10);
  --accent: {accent};
  --accent-on-dark: {accent_on_dark};
  --accent-ink: {accent_text};
}}
* {{ box-sizing: border-box; }}
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
h1, h2, h3, h4 {{
  font-family: "Inter Tight", "Inter", -apple-system, "Segoe UI", sans-serif;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0;
}}
a {{ color: inherit; text-decoration: none; }}

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
  backdrop-filter: blur(8px);
  z-index: 10;
}}
.topbar {{ top: 0; }}
.footer {{
  border-top: 1px solid var(--hairline);
  border-bottom: none;
  bottom: 0;
}}
.topbar__kicker {{ color: var(--accent-on-dark); font-weight: 600; }}

/* Slide structure */
.deck {{ padding: 0 36px 64px; }}
.slide {{
  min-height: calc(100vh - 120px);
  padding: 72px 0 96px;
  border-bottom: 1px solid var(--hairline);
  display: flex;
  flex-direction: column;
}}
.slide:last-of-type {{ border-bottom: none; }}
.slide__meta {{
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-faint);
  margin-bottom: 32px;
}}
.slide__index {{ color: var(--accent-on-dark); font-weight: 600; }}
.slide__title {{
  font-size: 56px;
  line-height: 1.05;
  letter-spacing: -0.02em;
  max-width: 18ch;
  margin-bottom: 24px;
}}
.slide__lede {{
  font-size: 19px;
  line-height: 1.5;
  color: var(--ink-muted);
  max-width: 60ch;
  margin-bottom: 56px;
}}

/* Cover slide */
.cover {{
  position: relative;
  overflow: hidden;
  padding: 48px 0 0;
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 32px;
  min-height: calc(100vh - 120px);
}}
.cover__head {{
  display: flex;
  justify-content: space-between;
  align-items: center;
}}
.cover__monogram {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: var(--accent);
  color: var(--accent-ink);
  font-family: "Inter Tight", sans-serif;
  font-weight: 700;
  font-size: 28px;
  letter-spacing: -0.02em;
}}
.cover__monogram img {{
  width: 100%;
  height: 100%;
  border-radius: 50%;
  object-fit: cover;
}}
.cover__chip {{
  font-size: 12px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--ink-faint);
  display: inline-flex;
  align-items: center;
  gap: 10px;
}}
.cover__chip::before {{
  content: '';
  width: 8px;
  height: 8px;
  background: var(--accent-on-dark);
  border-radius: 50%;
}}
.cover__body {{
  display: flex;
  flex-direction: column;
  justify-content: center;
  max-width: 22ch;
}}
.cover__headline {{
  font-size: 88px;
  line-height: 0.96;
  letter-spacing: -0.025em;
  margin: 0 0 28px;
}}
.cover__subhead {{
  font-size: 21px;
  line-height: 1.45;
  color: var(--ink-muted);
  max-width: 52ch;
}}
.cover__hero {{
  position: relative;
  width: 100%;
  height: 360px;
  border-radius: 22px;
  overflow: hidden;
  background: #1A1A1A;
  border: 1px solid var(--hairline);
}}
.cover__hero img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.cover__hero--placeholder {{
  display: flex;
  align-items: flex-end;
  padding: 32px;
  background: linear-gradient(135deg, #161616 0%, #1F1F1F 100%);
}}
.cover__hero--placeholder span {{
  font-size: 14px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-faint);
}}
.cover__byline {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--ink-faint);
}}

/* Cream cards */
.cards {{ display: grid; gap: 20px; }}
.cards--2x2 {{ grid-template-columns: 1fr 1fr; }}
.cards--3up {{ grid-template-columns: repeat(3, 1fr); }}
.cards--4up {{ grid-template-columns: repeat(4, 1fr); }}
.card {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: 22px;
  padding: 32px;
  display: flex;
  flex-direction: column;
  min-height: 220px;
}}
.card__kicker {{
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  font-weight: 600;
  margin-bottom: 16px;
}}
.card__title {{
  font-size: 26px;
  line-height: 1.15;
  letter-spacing: -0.01em;
  margin-bottom: 14px;
}}
.card__body {{
  font-size: 15.5px;
  line-height: 1.55;
  color: var(--card-ink-muted);
}}

/* Ad route cards (slide 3) */
.ad-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
.ad-row {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: 22px;
  padding: 24px;
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 24px;
  min-height: 260px;
}}
.ad-row__preview {{
  position: relative;
  border-radius: 14px;
  background: #0F0F0F;
  overflow: hidden;
  aspect-ratio: 9 / 16;
  display: flex;
  flex-direction: column;
}}
.ad-row__preview img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.ad-row__preview--mock {{
  display: grid;
  grid-template-rows: 38px 1fr 48px;
  background: linear-gradient(180deg, #181614 0%, #0F0E0C 100%);
  color: var(--ink);
  padding: 0;
}}
.ad-row__preview-head {{
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  border-bottom: 1px solid var(--hairline);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-faint);
}}
.ad-row__preview-dot {{
  width: 8px;
  height: 8px;
  background: var(--accent);
  border-radius: 50%;
}}
.ad-row__preview-body {{
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  text-align: center;
}}
.ad-row__preview-body span {{
  font-family: "Inter Tight", sans-serif;
  font-weight: 600;
  font-size: 22px;
  line-height: 1.15;
  letter-spacing: -0.01em;
  color: var(--ink);
}}
.ad-row__preview-cta {{
  background: var(--accent);
  color: var(--accent-ink);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-weight: 600;
}}
.ad-row__content {{ display: flex; flex-direction: column; gap: 14px; }}
.ad-row__tag {{
  display: inline-flex;
  align-self: flex-start;
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 600;
  color: var(--accent);
  background: rgba(0,0,0,0.04);
  padding: 6px 10px;
  border-radius: 999px;
}}
.ad-row__excerpt {{
  font-family: "Inter Tight", sans-serif;
  font-size: 20px;
  line-height: 1.3;
  letter-spacing: -0.005em;
}}
.ad-row__diag {{
  font-size: 14px;
  color: var(--card-ink-muted);
  line-height: 1.5;
}}
.ad-row__route {{
  font-size: 14.5px;
  font-weight: 500;
  color: var(--card-ink);
  padding: 14px;
  background: rgba(0,0,0,0.04);
  border-radius: 12px;
  border-left: 3px solid var(--accent);
}}
.ad-row__route strong {{
  display: block;
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  margin-bottom: 6px;
  font-weight: 700;
}}
.ad-row__footer {{
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--card-ink-muted);
}}
.ad-row__open {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  font-weight: 600;
  color: var(--card-ink);
  border: 1px solid var(--card-ink);
  padding: 8px 12px;
  border-radius: 999px;
}}
.ad-row__open::after {{ content: '\\2197'; font-size: 14px; }}

/* Gap map table */
.gap-map {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: 22px;
  overflow: hidden;
}}
.gap-map__head, .gap-map__row {{
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 100px;
  gap: 24px;
  padding: 22px 28px;
  align-items: start;
}}
.gap-map__head {{
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--card-ink-muted);
  font-weight: 600;
  border-bottom: 1px solid rgba(0,0,0,0.08);
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

/* Concept board */
.concepts {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; }}
.concept {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: 22px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  min-height: 460px;
}}
.concept__phone {{
  position: relative;
  width: 100%;
  aspect-ratio: 9 / 16;
  border-radius: 28px;
  border: 1px solid rgba(0,0,0,0.12);
  overflow: hidden;
  background: #111;
  margin-bottom: 16px;
}}
.concept__phone img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}}
.concept__phone--mock {{
  background: linear-gradient(160deg, var(--accent) 0%, #1A1A1A 130%);
  display: flex;
  align-items: flex-end;
  padding: 20px;
  color: var(--accent-ink);
}}
.concept__overlay {{
  position: absolute;
  inset: auto 0 0 0;
  padding: 18px;
  background: linear-gradient(0deg, rgba(0,0,0,0.62) 0%, rgba(0,0,0,0) 100%);
  color: #F5F0E8;
  font-family: "Inter Tight", sans-serif;
  font-size: 16px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -0.005em;
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
  font-size: 18px;
  line-height: 1.2;
  font-weight: 600;
  margin-bottom: 10px;
}}
.concept__hook {{
  font-size: 13.5px;
  color: var(--card-ink-muted);
  line-height: 1.45;
  margin-bottom: auto;
}}
.concept__cta {{
  margin-top: 16px;
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--card-ink);
}}

/* Process steps */
.process {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
.process__step {{
  background: rgba(245, 240, 232, 0.04);
  border: 1px solid var(--hairline);
  border-radius: 18px;
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
  line-height: 1.5;
}}

/* Pricing */
.pricing {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }}
.tier {{
  background: var(--card);
  color: var(--card-ink);
  border-radius: 22px;
  padding: 36px 32px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  position: relative;
  border: 2px solid transparent;
}}
.tier--rec {{ border-color: var(--accent); }}
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
  font-weight: 600;
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

/* Next-step slide */
.next-step {{
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 56px;
  align-items: center;
}}
.next-step__head {{
  font-size: 96px;
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
.btn {{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 16px 22px;
  border-radius: 999px;
  font-size: 13px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  font-weight: 700;
  border: 1px solid var(--ink);
  color: var(--ink);
}}
.btn--primary {{
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--accent);
}}
.btn::after {{ content: '\\2197'; font-size: 16px; }}
.next-step__visual {{
  position: relative;
  width: 100%;
  aspect-ratio: 4 / 5;
  border-radius: 28px;
  overflow: hidden;
  background: var(--accent);
}}
.next-step__visual--mock {{
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: "Inter Tight", sans-serif;
  font-weight: 700;
  font-size: 96px;
  letter-spacing: -0.03em;
  color: var(--accent-ink);
}}

/* Preview-video slide */
.preview__frame {{
  position: relative;
  width: min(100%, 960px);
  margin: 0 auto;
  border-radius: 22px;
  overflow: hidden;
  background: #050505;
  border: 1px solid var(--hairline);
  box-shadow: 0 30px 80px rgba(0,0,0,0.55);
}}
.preview__video {{
  width: 100%;
  display: block;
  background: #000;
  aspect-ratio: 16 / 9;
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
}}
.preview__watermark-note::before {{
  content: '';
  width: 8px;
  height: 8px;
  background: var(--accent-on-dark);
  border-radius: 50%;
  display: inline-block;
  margin-right: 10px;
  vertical-align: middle;
}}
.preview__actions {{
  display: flex;
  gap: 16px;
  align-items: center;
  margin-top: 28px;
  flex-wrap: wrap;
}}
.preview__sub {{
  font-size: 13.5px;
  color: var(--ink-muted);
  line-height: 1.5;
  max-width: 48ch;
}}

/* Print: one slide per page */
@media print {{
  body {{ background: #0A0A0A; }}
  .topbar, .footer {{ display: none; }}
  .deck {{ padding: 0; }}
  .slide {{
    page-break-after: always;
    min-height: 0;
    border: none;
    padding: 40px 36px;
  }}
  .slide:last-of-type {{ page-break-after: auto; }}
  .cover__headline {{ font-size: 64px; }}
  .next-step__head {{ font-size: 64px; }}
  .slide__title {{ font-size: 42px; }}
}}

@page {{
  size: A4 landscape;
  margin: 0;
}}
"""


# --------------------------------------------------------------------------- #
# Slide renderers
# --------------------------------------------------------------------------- #


def _slide_cover(brief: DeckBrief, assets: _AssetResolver, used: set[Path]) -> str:
    logo_url = assets.url_for(brief.logo_path) if brief.logo_path else None
    if brief.logo_path and brief.logo_path not in used:
        used.add(brief.logo_path)
    hero_url = assets.url_for(brief.hero_image_path) if brief.hero_image_path else None
    if brief.hero_image_path and brief.hero_image_path not in used:
        used.add(brief.hero_image_path)

    monogram_html = (
        f'<img src="{_e(logo_url)}" alt="{_e(brief.prospect_name)} mark">'
        if logo_url
        else _e(_initials(brief.prospect_name))
    )
    hero_html = (
        f'<img src="{_e(hero_url)}" alt="{_e(brief.prospect_name)} brand image">'
        if hero_url
        else (
            '<div class="cover__hero--placeholder">'
            f'<span>{_e(brief.prospect_name)} - brand canvas</span></div>'
        )
    )
    return f"""
<section class="slide cover" data-slide="1">
  <div class="cover__head">
    <div class="cover__monogram">{monogram_html}</div>
    <span class="cover__chip">Private Creative Note &middot; Prepared for {_e(brief.prospect_name)}</span>
  </div>
  <div class="cover__body">
    <h1 class="cover__headline">{_e(brief.cover_headline)}</h1>
    <p class="cover__subhead">{_e(brief.cover_subhead)}</p>
  </div>
  <div class="cover__hero">{hero_html}</div>
  <div class="cover__byline">
    <span>By {_e(brief.agency_name)}</span>
    <span>{_e(brief.brand_tone) if brief.brand_tone else _e(brief.niche)}</span>
  </div>
</section>
"""


def _slide_45_second(brief: DeckBrief) -> str:
    cards = "".join(
        f"""
<div class="card">
  <span class="card__kicker">{_e(c.label)}</span>
  <p class="card__body">{_e(c.body)}</p>
</div>"""
        for c in brief.forty_five_second_cards
    )
    return f"""
<section class="slide" data-slide="2">
  <div class="slide__meta"><span class="slide__index">02</span><span>The 45-second version</span></div>
  <h2 class="slide__title">A short, honest read of what we'd actually do.</h2>
  <p class="slide__lede">If you only read four cards, read these.</p>
  <div class="cards cards--2x2">{cards}</div>
</section>
"""


def _slide_live_ads(brief: DeckBrief, assets: _AssetResolver, used: set[Path]) -> str:
    if not brief.ads:
        rows = (
            '<p class="slide__lede">No live ads were captured for this prospect. '
            'When the Meta Ads Library returns an empty result, we lean on the '
            'gap-map and concept board instead.</p>'
        )
    else:
        cards = []
        for ad in brief.ads:
            cards.append(_render_ad_row(ad, brief, assets, used))
        rows = f'<div class="ad-grid">{"".join(cards)}</div>'
    return f"""
<section class="slide" data-slide="3">
  <div class="slide__meta"><span class="slide__index">03</span><span>From live ad to video route</span></div>
  <h2 class="slide__title">Where your current ads are stuck - and what we'd test instead.</h2>
  <p class="slide__lede">Each card shows one of your active ads, the limiting pattern, and a single UGC route we'd ship against it.</p>
  {rows}
</section>
"""


def _render_ad_row(ad: AdProof, brief: DeckBrief, assets: _AssetResolver, used: set[Path]) -> str:
    preview_url = assets.url_for(ad.screenshot_path) if ad.screenshot_path else None
    if ad.screenshot_path and ad.screenshot_path not in used:
        used.add(ad.screenshot_path)
    if preview_url:
        preview_html = (
            f'<div class="ad-row__preview">'
            f'<img src="{_e(preview_url)}" alt="ad preview"></div>'
        )
    else:
        # CSS mock that still looks designed - never a cheap placeholder.
        excerpt = ad.body_excerpt or f"{brief.prospect_name} ad"
        excerpt_short = excerpt if len(excerpt) <= 90 else excerpt[:87] + "..."
        preview_html = f"""
<div class="ad-row__preview ad-row__preview--mock">
  <div class="ad-row__preview-head">
    <span class="ad-row__preview-dot"></span><span>Live on Meta</span>
  </div>
  <div class="ad-row__preview-body"><span>{_e(excerpt_short)}</span></div>
  <div class="ad-row__preview-cta">{_e(ad.cta_text or 'Open ad')}</div>
</div>"""
    days_label = (
        f"Active for {ad.days_active} days"
        if ad.days_active
        else "Active in the current library"
    )
    return f"""
<article class="ad-row">
  {preview_html}
  <div class="ad-row__content">
    <span class="ad-row__tag">{_e(ad.issue_label)}</span>
    <p class="ad-row__excerpt">{_e(ad.body_excerpt or 'No copy captured in the audit sample.')}</p>
    <p class="ad-row__diag">{_e(ad.issue_explainer)}</p>
    <div class="ad-row__route">
      <strong>What we'd test instead</strong>
      {_e(ad.suggested_route)}
    </div>
    <div class="ad-row__footer">
      <span>{_e(days_label)}</span>
      <a class="ad-row__open" href="{_e(ad.library_url)}" target="_blank" rel="noopener">Open ad</a>
    </div>
  </div>
</article>
"""


def _slide_gap_map(brief: DeckBrief) -> str:
    if not brief.gap_map_rows:
        body = (
            '<p class="slide__lede">No clear gap pattern was captured for this '
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
    <span>UGC test to run</span>
    <span>Confidence</span>
  </div>
  {rows}
</div>"""
    return f"""
<section class="slide" data-slide="4">
  <div class="slide__meta"><span class="slide__index">04</span><span>Creative gap map</span></div>
  <h2 class="slide__title">The patterns we'd untie - and what to ship in their place.</h2>
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


def _slide_concept_board(brief: DeckBrief, assets: _AssetResolver, used: set[Path]) -> str:
    cards = "".join(_render_concept(c, assets, used) for c in brief.concepts)
    if not cards:
        return """
<section class="slide" data-slide="5">
  <div class="slide__meta"><span class="slide__index">05</span><span>Concept board</span></div>
  <h2 class="slide__title">Four routes we'd ship.</h2>
  <p class="slide__lede">No concept routes available.</p>
</section>"""
    return f"""
<section class="slide" data-slide="5">
  <div class="slide__meta"><span class="slide__index">05</span><span>Concept board</span></div>
  <h2 class="slide__title">Four routes we'd ship - one product, four openings.</h2>
  <p class="slide__lede">Each route is a 12-15 second short-form cut. Same product, same audience, different first three seconds. The cheapest test you can run in paid social.</p>
  <div class="concepts">{cards}</div>
</section>
"""


def _render_concept(c: ConceptRoute, assets: _AssetResolver, used: set[Path]) -> str:
    visual_url = assets.url_for(c.visual_path) if c.visual_path else None
    if c.visual_path and c.visual_path not in used:
        used.add(c.visual_path)
    if visual_url:
        phone_html = (
            f'<div class="concept__phone">'
            f'<img src="{_e(visual_url)}" alt="concept still">'
            f'<div class="concept__overlay">{_e(c.hook)}</div>'
            f'</div>'
        )
    else:
        phone_html = (
            f'<div class="concept__phone concept__phone--mock">'
            f'<div class="concept__overlay">{_e(c.hook)}</div>'
            f'</div>'
        )
    return f"""
<article class="concept">
  {phone_html}
  <span class="concept__label">{_e(c.label)}</span>
  <h3 class="concept__title">{_e(c.title)}</h3>
  <p class="concept__hook">{_e(c.hook)}</p>
  <span class="concept__cta">CTA &middot; {_e(c.cta)}</span>
</article>
"""


def _slide_how_this_works(brief: DeckBrief) -> str:
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
<section class="slide" data-slide="6">
  <div class="slide__meta"><span class="slide__index">06</span><span>How this works</span></div>
  <h2 class="slide__title">No shoot day. No retainer. Two-round revisions.</h2>
  <p class="slide__lede">We work from imagery, video and brand collateral you already own. Seven steps from first reply to a finished cut you can ship.</p>
  <div class="process">{steps}</div>
</section>
"""


def _slide_pricing(brief: DeckBrief) -> str:
    tiers = "".join(_render_tier(t) for t in brief.pricing)
    return f"""
<section class="slide" data-slide="7">
  <div class="slide__meta"><span class="slide__index">07</span><span>Pricing &amp; first test</span></div>
  <h2 class="slide__title">Three ways to try.</h2>
  <p class="slide__lede">Fixed, transparent, no retainer. Pay per finished cut. Scale only the routes that earn it.</p>
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


def _slide_preview_video(brief: DeckBrief, preview_video_url: str) -> str:
    """Optional 'Your first route preview' slide.

    The caller (microsite_builder) is responsible for only passing a URL
    that points to a watermarked MP4. This renderer makes that contract
    visible to the prospect via copy on the slide and a 'Want the clean
    version?' CTA.
    """
    return f"""
<section class="slide preview" data-slide="preview">
  <div class="slide__meta"><span class="slide__index">PREVIEW</span><span>Your first route preview</span></div>
  <h2 class="slide__title">Here is a first route - watermarked, on us.</h2>
  <p class="slide__lede">A short watermarked cut of one of the routes above. The clean export sits behind the next step - reply to unlock the full-res, watermark-free version with the source files.</p>
  <div class="preview__frame">
    <video class="preview__video" controls preload="metadata" playsinline>
      <source src="{_e(preview_video_url)}" type="video/mp4">
      Your browser cannot play this preview. <a href="{_e(preview_video_url)}">Download it</a> instead.
    </video>
    <div class="preview__watermark-note">Watermarked preview &middot; not for paid placement</div>
  </div>
  <div class="preview__actions">
    <a class="btn btn--primary" href="#">Want the clean version?</a>
    <span class="preview__sub">Reply with &lsquo;send the clean cut&rsquo; and we will share the watermark-free MP4 + source.</span>
  </div>
</section>
"""


def _slide_next_step(brief: DeckBrief) -> str:
    actions: list[str] = ['<a class="btn btn--primary" href="#">Reply to start</a>']
    if brief.website_url:
        actions.append(f'<a class="btn" href="{_e(brief.website_url)}" target="_blank" rel="noopener">Open website</a>')
    if brief.ads and brief.ads[0].library_url:
        actions.append(f'<a class="btn" href="{_e(brief.ads[0].library_url)}" target="_blank" rel="noopener">Open active ad</a>')
    if brief.facebook_url:
        actions.append(f'<a class="btn" href="{_e(brief.facebook_url)}" target="_blank" rel="noopener">Open Facebook</a>')
    actions_html = "".join(actions)

    visual_initials = _initials(brief.prospect_name)
    return f"""
<section class="slide" data-slide="8">
  <div class="slide__meta"><span class="slide__index">08</span><span>Next step</span></div>
  <div class="next-step">
    <div>
      <h2 class="next-step__head">{_e(brief.cta_headline)}</h2>
      <p class="next-step__body">{_e(brief.cta_body)}</p>
      <div class="next-step__actions">{actions_html}</div>
    </div>
    <div class="next-step__visual next-step__visual--mock"><span>{_e(visual_initials)}</span></div>
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
    # Mix toward cream until we clear the threshold.
    cream = (245, 240, 232)
    for ratio in (0.45, 0.6, 0.75, 0.85):
        mr = int(r * (1 - ratio) + cream[0] * ratio)
        mg = int(g * (1 - ratio) + cream[1] * ratio)
        mb = int(b * (1 - ratio) + cream[2] * ratio)
        if (mr * 299 + mg * 587 + mb * 114) / 1000 >= 140:
            return f"#{mr:02X}{mg:02X}{mb:02X}"
    return "#F5F0E8"
