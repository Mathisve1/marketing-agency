"""FPDF2-based Market Analysis & Hook Strategy report.

Renders a multi-page PDF with cover, executive summary, winning hooks list,
and referral motions section. Output is written to:
  clients/<id>/outputs/reports/market-analysis-<UTC-timestamp>.pdf
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from fpdf import FPDF

from core.context_schema import ReferralMotion, WinningHook


class _Report(FPDF):
    """FPDF subclass with branded header/footer (suppressed on cover page)."""

    def __init__(self, client_name: str):
        super().__init__()
        self.client_name = client_name
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Market Analysis - {self.client_name}", align="L")
        self.ln(15)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _sanitize(text: str) -> str:
    """FPDF core fonts are latin-1; replace out-of-range chars rather than crash.
    Dutch/French diacritics (e, e, c, ...) are all in latin-1; emojis are not."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


def build_market_analysis_pdf(
    client_root: Path,
    client_name: str,
    title: str,
    executive_summary: str,
    winning_hooks: Sequence[WinningHook],
    referral_motions: Sequence[ReferralMotion],
) -> Path:
    out_dir = (client_root / "outputs" / "reports").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"market-analysis-{timestamp}.pdf"

    pdf = _Report(client_name)

    # ---- Cover page ----
    pdf.add_page()
    pdf.set_y(80)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(20, 20, 20)
    pdf.multi_cell(0, 12, _sanitize(title), align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 10, _sanitize(client_name), align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 8, datetime.now(timezone.utc).strftime("%B %d, %Y"), align="C")

    # ---- Executive summary ----
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 10, "Executive Summary")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 6, _sanitize(executive_summary))

    # ---- Candidate hooks (longevity signal) ----
    # V1.6 language audit: section title softened from "Winning Hooks" to
    # avoid overclaiming. The data-model name `winning_hooks` is kept for
    # schema stability; only the customer-facing PDF copy changes.
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Candidate Hooks - Longevity Signal ({len(winning_hooks)})")
    pdf.ln(12)
    if not winning_hooks:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 6, "No hooks recorded yet.")
    else:
        for hook in winning_hooks:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(20, 20, 20)
            pdf.cell(0, 6, _sanitize(f"{hook.id} - {hook.pattern}"))
            pdf.ln(6)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(60, 60, 60)
            pdf.multi_cell(0, 5, _sanitize(hook.description))
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(0, 5, _sanitize(
                f"days_active={hook.days_active} | "
                f"confidence={hook.confidence.value} | "
                f"source={hook.source_ad_id or 'N/A'}"
            ))
            pdf.ln(10)

    # ---- Referral motions ----
    if referral_motions:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 10, f"Referral Motions ({len(referral_motions)})")
        pdf.ln(12)
        for motion in referral_motions:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 6, _sanitize(motion.id))
            pdf.ln(6)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(60, 60, 60)
            pdf.multi_cell(0, 5, _sanitize(motion.description))
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(0, 5, _sanitize(
                f"pacing={motion.pacing or '-'} | "
                f"camera={motion.camera_style or '-'} | "
                f"duration={motion.duration_seconds or '?'}s"
            ))
            pdf.ln(10)

    pdf.output(str(out_path))
    return out_path
