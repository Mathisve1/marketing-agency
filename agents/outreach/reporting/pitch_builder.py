"""FPDF2-based 'Brand Audit & Agency Pitch' generator.

Renders a multi-page PDF for cold outreach: cover -> diagnosed weaknesses in
the prospect's current Meta ad library -> snapshot of their current ads ->
how our AI-UGC framework solves it -> CTA. Output is written to:
  prospects/<prospect_id>/pitch.pdf

Structure mirrors agents/strategist/reporting/pdf_builder.py for consistency
(Helvetica core font + latin-1 _sanitize for diacritics).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from fpdf import FPDF


class _PitchReport(FPDF):
    """FPDF subclass with branded header/footer (suppressed on cover page)."""

    def __init__(self, agency_name: str):
        super().__init__()
        self.agency_name = agency_name
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"{self.agency_name} - Brand Audit & Pitch", align="L")
        self.ln(15)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _sanitize(text: str) -> str:
    """FPDF core fonts are latin-1; replace out-of-range chars rather than crash."""
    return (text or "").encode("latin-1", errors="replace").decode("latin-1")


DEFAULT_FRAMEWORK_STRENGTHS = (
    # V1.6: softened from "Hooks proven by competitor-ad longevity..."
    # Long-running ads are a strong market signal worth testing, not a
    # guarantee of performance; the previous wording overclaimed.
    "Hooks informed by competitor-ad longevity signals (>=14 days of "
    "sustained paid spend in your market is a strong candidate worth "
    "testing - we treat it as evidence to study, not proof of victory).",
    "Kling 3.0 V2V production using your brand-safe character + product "
    "references - inherits the motion of long-running competitor ads "
    "while guaranteeing copyright originality.",
    "Built-in performance feedback loop: every Meta campaign's ROAS/CTR "
    "auto-feeds back into our hook library, retiring underperformers and "
    "compounding the angles your real audience actually responds to.",
    "Per-client isolation: your brand voice, forbidden terms, and audience "
    "data never bleed into another client's creative.",
)


# --------------------------------------------------------------------------- #
# Weakness rendering - accepts both shapes per V1.6 evidence-discipline pass
# --------------------------------------------------------------------------- #


_VALID_CONFIDENCES = {"high", "medium", "low"}


def _render_weakness(pdf: FPDF, weakness) -> None:
    """Render one weakness bullet on the open PDF page.

    Accepts either a bare string (back-compat) or a dict of shape
    {description, evidence: list[str], confidence: high|medium|low}.
    Unknown dict shapes degrade gracefully to repr() rather than crash.

    Implementation note: every text emission goes through multi_cell to
    keep the cursor in a known state. Mixing cell()+ln()+multi_cell()
    interacts badly with fpdf2's auto page-break logic and was the
    source of "Not enough horizontal space" crashes during V1.6 dev.
    """
    # fpdf2 2.8 defaults multi_cell's post-render cursor to (RIGHT, TOP),
    # leaving x at the right margin. A subsequent multi_cell(w=0, ...)
    # then has zero remaining width and crashes "Not enough horizontal
    # space". We follow every multi_cell with pdf.ln(...) to reset x to
    # the left margin (matches the pattern used elsewhere in this file).

    if isinstance(weakness, str):
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, 6, _sanitize(f"- {weakness}"))
        pdf.ln(2)
        return

    if isinstance(weakness, dict):
        description = weakness.get("description") or weakness.get("desc") or ""
        evidence = weakness.get("evidence") or []
        confidence = (weakness.get("confidence") or "").strip().lower()

        # Description line (bold)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, 6, _sanitize(f"- {description or '(no description)'}"))
        pdf.ln(0)  # reset x to left margin without advancing y

        # Confidence label, if recognised
        if confidence in _VALID_CONFIDENCES:
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(120, 120, 120)
            pdf.multi_cell(0, 5, _sanitize(f"    Confidence: {confidence}"))
            pdf.ln(0)

        # Evidence sub-bullets
        if evidence:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(80, 80, 80)
            for item in evidence:
                pdf.multi_cell(0, 5, _sanitize(f"    - {item}"))
                pdf.ln(0)
        pdf.ln(3)
        return

    # Unknown type: render its repr so the operator sees what came through
    # rather than crashing the whole PDF render.
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 6, _sanitize(f"- {weakness!r}"))
    pdf.ln(2)


def build_pitch_pdf(
    output_path: Path,
    prospect_name: str,
    niche: str,
    weaknesses: Sequence[str],
    competitor_ad_summary: Sequence[dict],
    our_framework_strengths: Sequence[str] = DEFAULT_FRAMEWORK_STRENGTHS,
    cta: Optional[str] = None,
    agency_name: str = "Our Agency",
) -> Path:
    """Render the pitch PDF and return the absolute output path.

    competitor_ad_summary entries are expected to carry these keys (any may
    be absent and the renderer degrades gracefully):
      page_name, media_type, days_active, body_text
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pdf = _PitchReport(agency_name)

    # ---- Cover ----
    pdf.add_page()
    pdf.set_y(70)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(20, 20, 20)
    pdf.multi_cell(0, 12, "Brand Audit & AI-UGC Pitch", align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(60, 60, 60)
    pdf.multi_cell(0, 10, _sanitize(prospect_name), align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, _sanitize(f"Niche: {niche}"), align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 8, datetime.now(timezone.utc).strftime("%B %d, %Y"), align="C")

    # ---- Diagnosed weaknesses ----
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 10, "Diagnosed weaknesses in your current ads")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(40, 40, 40)
    if not weaknesses:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 6, "No specific weaknesses recorded - audit may need a re-run.")
    else:
        for w in weaknesses:
            _render_weakness(pdf, w)

    # ---- Current ad library snapshot ----
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 10, f"Current ad library snapshot ({len(competitor_ad_summary)} ads)")
    pdf.ln(12)
    if not competitor_ad_summary:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 6, "No ads scraped - either zero active ads or the FB Ads scrape failed.")
    else:
        for ad in competitor_ad_summary:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(20, 20, 20)
            page = ad.get("page_name") or "Unknown page"
            media = ad.get("media_type") or "?"
            days = ad.get("days_active")
            days_str = f"{days} days active" if days is not None else "duration unknown"
            pdf.cell(0, 6, _sanitize(f"{page} - {media} - {days_str}"))
            pdf.ln(6)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(60, 60, 60)
            body = (ad.get("body_text") or "(no body text)")
            if len(body) > 400:
                body = body[:400] + "..."
            pdf.multi_cell(0, 5, _sanitize(body))
            pdf.ln(6)

    # ---- Our framework ----
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 10, "Our AI-UGC framework solves this")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(40, 40, 40)
    for s in our_framework_strengths:
        pdf.multi_cell(0, 6, _sanitize(f"- {s}"))
        pdf.ln(2)

    # ---- CTA (optional) ----
    if cta:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 12, "Next step")
        pdf.ln(16)
        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, 7, _sanitize(cta))

    pdf.output(str(output_path))
    return output_path
