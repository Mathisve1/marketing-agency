"""Phase 1O — Pillow-based label-stripper for the Pai packshot.

Reads:
    build/pitches/p/pai-skincare-p9wybu/refs/renewal-serum-primary-packshot-9x16.jpg

Writes (next to the original):
    renewal-serum-blank-label-9x16.jpg

Strategy (deterministic, no AI):
  1. Keep canvas dimensions unchanged (1080x1920).
  2. Sample the clean upper-body of the bottle (a thin horizontal
     strip between the pump cap and the start of label text). That
     strip carries the frosted-white texture + the very subtle pale-
     green pigment of the bottle's contents.
  3. Vertically tile that strip across the entire label region so the
     replacement reads as "more of the same frosted glass" rather than
     a flat painted patch.
  4. Apply a feathered alpha mask on the top + bottom edges so the
     replacement blends into the surrounding bottle pixels without a
     visible seam.
  5. Composite back onto the original packshot at the same coordinates
     and save as JPEG.

What is intentionally NOT touched:
  - Pump cap and bottle rim above the label
  - Bottle's lower base
  - The grey studio background and shadow
  - Bottle silhouette (same shape, same finish)

What IS removed:
  - "AGE CONFIDENCE™" headline
  - The black "pai" brand mark + ® mark
  - The decorative green wavy lines
  - "Renewal Serum / Sérum Régénérant"
  - "NAD+ & TriPeptide"
  - "CLINICALLY PROVEN FOR SENSITIVE SKIN"

Hard rules:
  - No external API call. Pillow + stdlib only.
  - Does not modify the original file.
  - Outputs at high JPEG quality so Seedance gets a clean reference.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = (
    REPO_ROOT
    / "build" / "pitches" / "p" / "pai-skincare-p9wybu" / "refs"
    / "renewal-serum-primary-packshot-9x16.jpg"
)
DST = SRC.with_name("renewal-serum-blank-label-9x16.jpg")

# Label-bounding-box on the 1080x1920 packshot. Generous on Y so we
# cover the green decorative curves (they extend above "AGE CONFIDENCE"
# slightly), tight on X so the patch never reaches outside the bottle
# silhouette (a too-wide patch would smear bottle pixels onto the
# studio background and look obviously edited).
LABEL_LEFT = 425    # slightly inside bottle left edge
LABEL_RIGHT = 695   # slightly inside bottle right edge
LABEL_TOP = 830     # just above "AGE CONFIDENCE"
LABEL_BOTTOM = 1435 # well below "FOR SENSITIVE SKIN" so the feather
                    # zone (last 30px) doesn't reveal it. Bottle base
                    # starts curving around y=1470, so we have room.

# Sample strip — must be from a PURE-CYLINDRICAL zone of the bottle
# (no shoulder transition, no printed text). The bottle widens from
# the pump cap around y=750-770, then sits straight; AGE CONFIDENCE
# begins around y=850. y=780-830 is a clean 50px strip.
SAMPLE_TOP = 780
SAMPLE_BOTTOM = 830

# Feathering — Y blends along the bottle length so the patch melts
# into the cylinder above + below; X stays 0 because the patch already
# uses the same bottle color as the edge, AND a hard-X mask still gets
# a tiny natural softening from the post-tile blur.
FEATHER_Y_PX = 30
FEATHER_X_PX = 0
BLUR_RADIUS = 8.0


def _make_feather_mask(
    width: int, height: int, feather_x: int, feather_y: int,
) -> Image.Image:
    """Alpha mask: fully opaque in the middle, soft-fade on all four
    edges so the painted patch blends into the surrounding bottle
    pixels (vertical) and into the bottle's edge highlights (horizontal)
    without a visible seam.

    Built as a starting-all-opaque image, then a Gaussian blur applied
    after carving the inner opaque rectangle — gives a smoother radial
    feather than per-row linear ramps and is much shorter to read."""
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle(
        (feather_x, feather_y, width - feather_x, height - feather_y),
        fill=255,
    )
    radius = max(feather_x, feather_y)
    return mask.filter(ImageFilter.GaussianBlur(radius=radius))


def strip_label() -> dict:
    if not SRC.exists():
        raise SystemExit(f"FATAL: source not found at {SRC}")

    base = Image.open(SRC).convert("RGB")
    w, h = base.size
    if (w, h) != (1080, 1920):
        raise SystemExit(
            f"FATAL: expected 1080x1920 packshot; got {w}x{h}"
        )

    label_w = LABEL_RIGHT - LABEL_LEFT
    label_h = LABEL_BOTTOM - LABEL_TOP

    # Crop the clean upper-bottle strip (frosted texture + slight green tint).
    sample = base.crop((LABEL_LEFT, SAMPLE_TOP, LABEL_RIGHT, SAMPLE_BOTTOM))
    sample_h = sample.height

    # Tile the clean cylindrical strip straight down (no mirror — the
    # original mirror trick produced a visible "M/W" silhouette because
    # the bottle shoulder varied horizontally). The cylindrical-zone
    # sample has roughly uniform horizontal pixel values, so straight
    # tiling produces a near-continuous patch; the post-tile blur
    # finishes the job.
    patch = Image.new("RGB", (label_w, label_h))
    y = 0
    while y < label_h:
        slice_h = min(sample_h, label_h - y)
        patch.paste(sample.crop((0, 0, label_w, slice_h)), (0, y))
        y += slice_h

    # Wash out any micro-seams + soften high-frequency texture so the
    # patch reads as continuous frosted glass.
    patch = patch.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))

    # Build the feather mask + composite onto the original.
    mask = _make_feather_mask(label_w, label_h, FEATHER_X_PX, FEATHER_Y_PX)
    out = base.copy()
    out.paste(patch, (LABEL_LEFT, LABEL_TOP), mask)

    # Save high-quality JPEG.
    out.save(DST, "JPEG", quality=95, optimize=True, subsampling=1)

    return {
        "src": str(SRC),
        "dst": str(DST),
        "out_size": out.size,
        "dst_bytes": DST.stat().st_size,
        "label_box_xyxy": (LABEL_LEFT, LABEL_TOP, LABEL_RIGHT, LABEL_BOTTOM),
        "sample_strip_y": (SAMPLE_TOP, SAMPLE_BOTTOM),
        "feather_px": {"x": FEATHER_X_PX, "y": FEATHER_Y_PX},
        "blur_radius": BLUR_RADIUS,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(strip_label(), indent=2, default=str))
