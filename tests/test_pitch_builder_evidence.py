"""V1.6 evidence-discipline tests for the outreach pitch PDF builder.

Pins:
  - Bare-string weaknesses still render (back-compat with old audit JSONs).
  - Dict-shaped weaknesses with description + evidence + confidence reach
    the renderer with the right text in the right order.
  - Mixed lists (some str, some dict) render in one PDF.
  - Unknown shapes degrade gracefully via repr() instead of raising.
  - Confidence label is omitted when the field is missing or invalid.

Approach: monkey-patch FPDF.multi_cell to capture every rendered text
string. PDF text streams are zlib-compressed by default, so substring
checks against the on-disk bytes are unreliable; intercepting at the
renderer call boundary is deterministic and tests exactly what we care
about (that _render_weakness emits the right text in the right order).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fpdf import FPDF

from agents.outreach.reporting import pitch_builder
from agents.outreach.reporting.pitch_builder import build_pitch_pdf


@pytest.fixture
def captured_text(monkeypatch) -> list[str]:
    """Captures every multi_cell text payload, in order. Renderer still
    runs end-to-end and produces a PDF on disk; we just also see what it
    emitted."""
    captured: list[str] = []
    real_multi_cell = FPDF.multi_cell

    def spy(self, w, h, txt="", *args, **kwargs):
        # txt may be the third positional or come via the new_x/new_y kwargs
        # API; capture whatever string we actually got.
        if isinstance(txt, str):
            captured.append(txt)
        return real_multi_cell(self, w, h, txt, *args, **kwargs)

    monkeypatch.setattr(FPDF, "multi_cell", spy)
    return captured


# --------------------------------------------------------------------------- #
# Bare-string back-compat
# --------------------------------------------------------------------------- #


def test_bare_string_weaknesses_still_render(tmp_path: Path, captured_text: list[str]):
    out = tmp_path / "pitch.pdf"
    build_pitch_pdf(
        output_path=out,
        prospect_name="Acme Co",
        niche="fitness apparel",
        weaknesses=[
            "Brand relies on a single copy angle across all 10 long-running ads",
            "No video creative on Meta",
        ],
        competitor_ad_summary=[],
        cta="Reply to schedule a 20-minute audit walkthrough.",
    )
    assert out.exists()
    rendered = "\n".join(captured_text)
    assert "- Brand relies on a single copy angle" in rendered
    assert "- No video creative on Meta" in rendered


# --------------------------------------------------------------------------- #
# Dict-shaped weakness with evidence + confidence
# --------------------------------------------------------------------------- #


def test_dict_weakness_with_evidence_and_confidence_renders(
    tmp_path: Path, captured_text: list[str]
):
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="Gymshark",
        niche="performance wear",
        weaknesses=[
            {
                "description": "Image-only ad library; no Meta video presence",
                "evidence": [
                    "10 of 10 ads have media_type=IMAGE",
                    "ad_archive_id 12345 (active 84 days, image only)",
                ],
                "confidence": "high",
            },
        ],
        competitor_ad_summary=[],
    )
    rendered = captured_text

    # Description appears as a top-level bullet.
    assert any("- Image-only ad library" in t for t in rendered), (
        f"description missing; captured = {rendered}"
    )
    # Each evidence item appears as an indented sub-bullet.
    assert any("    - 10 of 10 ads have media_type=IMAGE" in t for t in rendered)
    assert any("    - ad_archive_id 12345" in t for t in rendered)
    # Confidence label rendered.
    assert any("Confidence: high" in t for t in rendered)


# --------------------------------------------------------------------------- #
# Mixed shapes
# --------------------------------------------------------------------------- #


def test_mixed_str_and_dict_weaknesses_render_together(
    tmp_path: Path, captured_text: list[str]
):
    """Real-world: an audit refresh might overwrite some bullets with the
    new structured shape and leave others as legacy strings. Both must
    coexist in one PDF."""
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="On Running",
        niche="performance footwear",
        weaknesses=[
            "Legacy free-text observation about CTA repetition",
            {
                "description": "No fresh creative in the last 60+ days",
                "evidence": ["newest start_date is 2026-02-15"],
                "confidence": "medium",
            },
        ],
        competitor_ad_summary=[],
    )
    rendered = captured_text
    # Order matters: legacy string first, structured dict second.
    legacy_idx = next(
        i for i, t in enumerate(rendered) if "Legacy free-text" in t
    )
    structured_idx = next(
        i for i, t in enumerate(rendered) if "No fresh creative" in t
    )
    assert legacy_idx < structured_idx
    # Structured evidence reached the renderer.
    assert any("newest start_date" in t for t in rendered)
    assert any("Confidence: medium" in t for t in rendered)


# --------------------------------------------------------------------------- #
# Defensive shapes
# --------------------------------------------------------------------------- #


def test_unknown_weakness_shape_does_not_crash(
    tmp_path: Path, captured_text: list[str]
):
    """If a future writer puts a non-str/non-dict (an int, a tuple, etc.)
    into weaknesses, the renderer must not take down the whole PDF render.
    It falls through to a repr() bullet."""
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="Edge Case Co",
        niche="testing",
        weaknesses=[42, ("a", "b")],
        competitor_ad_summary=[],
    )
    rendered = "\n".join(captured_text)
    assert "- 42" in rendered          # repr(42) -> "42"
    assert "('a', 'b')" in rendered     # repr(tuple)


def test_dict_weakness_without_confidence_omits_label(
    tmp_path: Path, captured_text: list[str]
):
    """Confidence is optional; if missing, the renderer must not invent
    one. Only known values (high/medium/low) get a label."""
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="No Conf Co",
        niche="test",
        weaknesses=[
            {
                "description": "A claim without confidence metadata",
                "evidence": ["one supporting observation"],
                # no "confidence" key
            },
        ],
        competitor_ad_summary=[],
    )
    rendered = captured_text
    assert any("A claim without confidence" in t for t in rendered)
    assert any("one supporting observation" in t for t in rendered)
    assert not any("Confidence:" in t for t in rendered)


def test_dict_weakness_with_invalid_confidence_omits_label(
    tmp_path: Path, captured_text: list[str]
):
    """Only the documented confidence values are honoured. An LLM that
    invents 'pretty-good' must NOT propagate that to the customer-facing
    PDF as if it were a real grade."""
    build_pitch_pdf(
        output_path=tmp_path / "pitch.pdf",
        prospect_name="Bad Conf Co",
        niche="test",
        weaknesses=[
            {
                "description": "A claim with invented confidence",
                "evidence": ["one item"],
                "confidence": "pretty-good",
            },
        ],
        competitor_ad_summary=[],
    )
    rendered = captured_text
    assert any("invented confidence" in t for t in rendered)
    # Garbage confidence is dropped silently rather than rendered.
    assert not any("Confidence:" in t for t in rendered)


# --------------------------------------------------------------------------- #
# Default framework strengths language softening (Remark 2)
# --------------------------------------------------------------------------- #


def test_default_framework_strengths_no_longer_overclaim():
    """V1.6 language audit: pitch PDF default copy must not claim hooks
    are 'proven'. Long-running ads are a SIGNAL, not proof."""
    joined = " ".join(pitch_builder.DEFAULT_FRAMEWORK_STRENGTHS).lower()
    assert "proven" not in joined, (
        "framework strengths must avoid 'proven' - long-running ads are a "
        "signal, not proof. See CTO Remark 2."
    )
    # And the new wording explicitly mentions signal / candidate framing.
    assert "signal" in joined or "candidate" in joined
