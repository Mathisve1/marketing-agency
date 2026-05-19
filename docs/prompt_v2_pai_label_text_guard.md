# Pai 720p — Prompt v2 plan (label-text guard)

**Planning document. NOT an authorisation to regenerate.**

The Phase 1H 720p UGC take landed visually convincing but hallucinated
small label text on the bottle:

- "NAD+ & **TriPepttide**" (invented double-`t`; real product line is
  "TriPeptide")
- "Sérum **Régéne̲rant**" (mangled second French diacritic)

Both issues fall in the same failure mode: Seedance rendered the label
sharp and legibly, then **invented** the small text it couldn't reason
about from the product image. Audio Fixer does NOT address this —
Phase 1I explicitly recommends skipping Audio Fixer for this take and
fixing the prompt instead.

This document captures the v2 prompt the operator should land in the
prompt editor (`/agency/campaigns/camp_pai_route01/content/content_pai_route02_draft/prompt`)
before any future Pai re-submit. **No regeneration runs from this plan
until the operator explicitly approves a paid `--submit`.**

## Source — what to fork from

| Field | Value |
|---|---|
| Parent prompt_version | `eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee` (Phase 1E seed: "720p stricter label-text guard", `operator_editing`) |
| Parent content_item | `99999999-9999-9999-9999-999999999999` ("Next week — ingredient close-up variant") |
| Parent campaign | `66666666-6666-6666-6666-666666666666` (Route 01 — UGC ingredient-led sensitive-skin serum) |
| Strategic tier | `standard_720p` (do not change — Phase 1H confirmed the 720p register reads more naturally for UGC) |
| Duration / aspect | 15 s · 9:16 (unchanged) |
| Provider mode | `enhancor_seedance` / `ugc` (unchanged) |
| Status on fork | `operator_editing` (NOT `approved_for_generation` — operator decides when v2 is ready) |

## Field-level diff against v1

### Hook
*Unchanged.* "Three ingredients. That's it." — already deliberately
ambiguous and never reads as a packaging claim.

### Prompt body
*Unchanged.* Macro-led UGC variant, 15 s, 720p, hands-only, soft
daylight, calm British VO. The framing is fine; the label is the
problem.

### Scene plan — TIGHTEN

Replace the bottle-rotation beat (currently `3-9s slow rotation of the
bottle so the label reads`) with motion or angle that prevents the
small label text from being sharply readable in the first place:

```
0-3s  ingredient-led hook over a hand reaching for the bottle
3-9s  bottle held at a slight ~15° tilt to camera; subtle motion
      keeps the label out of the sharp focal plane (defocus + motion
      blur on the small text below the brand mark)
9-13s dropper close-up; product label deliberately outside the
      shallow depth of field
13-15s soft close, hand setting bottle down
```

Rationale: Seedance hallucinates sharp text. If the small text is
*supposed* to be out of focus or motion-blurred, the model has license
to leave it unreadable instead of inventing.

### Creator direction
*Unchanged.* Hands-only, calm deliberate motion, sensitive-skin tone.

### Product constraints — REPLACE

The Phase 1E v1 said "Label text MUST be legible and spelled exactly as
on the real packaging. If the label cannot be rendered legibly, fall
back to an out-of-focus pass rather than inventing text." That didn't
work — Seedance rendered the label sharp and invented anyway. v2
inverts the policy: **the label MUST be unreadable by default**, with
only the brand mark allowed sharp.

```
Pai Skincare BioRegenerate Rosehip Oil.

Readable label policy:
  - ONLY the brand mark "Pai" may be sharp and legible.
  - All other label text MUST be softly blurred, motion-blurred,
    out-of-focus, or otherwise unreadable.
  - DO NOT INVENT any small text on the packaging.
  - DO NOT render "AGE CONFIDENCE", "Renewal Serum",
    "Sérum Régénérant", "NAD+", "TriPeptide", "CLINICALLY PROVED",
    "FOR SENSITIVE SKIN", or any other readable claim.
  - DO NOT add any text not visible in the reference product image.
  - If a beat would force the small label into the sharp focal plane,
    instead shoot at a slight angle, add motion, or push the label
    out of focus.
Brand-name spelling — when "Pai" is visible:
  - Spelled exactly "Pai".
  - NOT "Pái", NOT "Pie", NOT "PAI" (all-caps), NOT "Pal".
  - The Pai green-curve "swoosh" logo may appear above or behind the
    word; the swoosh itself carries no text.
Packaging:
  - Frosted/translucent white bottle, white pump cap (per reference image).
  - No competing skincare visible.
  - No on-screen graphics, overlays, or animated text.
```

### Negative prompt — EXTEND

Append explicit denylist tokens for the v1 hallucinations and other
plausible ones:

```
No animated text, no graphics overlays, no warped or melted label text,
no AI-typical extra fingers, no fake ingredient names, no jewellery,
no nail polish, no competing brands.

ADDITIONAL Phase 1H label denylist:
no readable "TriPepttide", no readable "TriPeptide", no readable
"NAD+", no readable "AGE CONFIDENCE", no readable "Renewal Serum",
no readable "Sérum Régénérant" (any spelling), no readable
"CLINICALLY PROVED", no readable "CLINICALLY PROVEN", no readable
"FOR SENSITIVE SKIN", no readable "TESTED ON SENSITIVE SKIN",
no readable percentage or measurement claims, no readable ingredient
percentages, no readable manufacturer address, no readable batch
codes, no readable barcodes, no readable QR codes, no readable
sub-headings beneath the Pai brand mark.

Sharp small text on the label is forbidden. The only sharp text
allowed anywhere in the frame is the word "Pai" itself.
```

### Operator notes for the new version

```
v2 of the 720p take. Forks v1 (eeee…) after the Phase 1H real run
produced a sharp but hallucinated label ("TriPepttide" extra t;
mangled French diacritic on "Régénérant").

v2 inverts the v1 label-text policy: instead of "render legibly OR
fall back to out-of-focus", the new rule is "label MUST be unreadable
by default except for the brand mark Pai". Scene plan tightened to
keep the small label out of the sharp focal plane (slight tilt +
motion in beat 2, out-of-DoF in beat 3).

Quality tier remains standard_720p — the Phase 1H take confirmed
720p reads more naturally for UGC than 1080p, so the resolution
change is NOT the fix.

Audio remains untouched. Native AAC from UGC mode is fine; Audio
Fixer is not needed for this take.

DO NOT MARK approved_for_generation YET. v2 needs a manual review
pass + (optional) one more iteration before any future paid
re-submit.
```

## How to land this in the operator console

1. Open `/agency/campaigns/camp_pai_route01/content/content_pai_route02_draft/prompt`.
2. Click **Fork from this version** on the v1 row (or use the
   regeneration-request fork flow if there's already an open queue
   row — Phase 1E supports both paths).
3. Paste the v2 field bodies above into the matching editor fields
   (Scene plan / Product constraints / Negative prompt / Operator
   notes).
4. Click **Save draft**. The new row lands as
   `status=operator_editing`.
5. **DO NOT** click "Mark approved for generation" until the operator
   has read v2 end-to-end and is happy with it. Phase 1J expressly
   does not authorise a new paid run.

## What this plan does NOT do

- **No regeneration.** No new `generation_jobs` row is created.
  `scripts/run_generation_job.py --submit` is not invoked. Zero
  credits are spent by this plan.
- **No Audio Fixer.** The Phase 1H native audio was fine; Audio Fixer
  is the wrong tool for a label-text issue.
- **No prompt-data write from this document.** The operator types v2
  into the prompt editor. The system of record is the
  `prompt_versions` table (Phase 1E migration 004); this `.md` is
  just the recipe.
- **No client surface change.** Prompt versions are operator-only by
  Phase 1E RLS posture; the client portal sees none of this.

## When v2 is ready

The next action — only after explicit operator approval — would be:

```bash
# 1. Create the Phase 1F mock job from the new v2 prompt:
#    (via the dashboard "Create mock generation job" button on the
#     prompt editor route, once the operator marks v2
#     approved_for_generation)

# 2. Submit to Seedance (PAID):
py -3.11 scripts/run_generation_job.py \
  --job-id <new-job-id> \
  --product-url https://yuvo-pitches.pages.dev/p/pai-skincare-p9wybu/refs/renewal-serum-primary-packshot-9x16.jpg \
  --influencer-url https://yuvo-pitches.pages.dev/p/pai-skincare-p9wybu/refs/test-influencer-synthetic.jpg \
  --webhook-url <a fresh https://webhook.site/<token> bucket> \
  --submit --confirm

# 3. Poll → Download → Ingest exactly as in Phase 1H/1I.
```

Until the operator says go, the v1 artefact (`result.mp4` + thumbnail)
remains the canonical Pai 720p reference take on disk.
