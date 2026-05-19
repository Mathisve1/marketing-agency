# Phase 1O — Pai Prompt v2 regeneration plan (planning only — DO NOT RUN)

The Phase 1F+ 720p generation (job `1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b`,
provider request `6a09e50d13b1573a3bc1cd17`) landed a usable internal
demo. The clip is structurally fine; the **only** issue is on-pack
text hallucination — the model invented partly-readable label copy
("TriPepttide" etc.) that doesn't exist on the real Pai BioRegenerate
Rosehip Oil packaging. Audio is fine. Therefore:

> **Audio Fixer must NOT be run for this issue.** Audio Fixer corrects
> mouth/lip-sync and timing; it does nothing about label-text
> hallucination. Running it is paid credits spent on the wrong problem.

This document captures the v2 prompt guardrails so the operator can
fork the existing 720p prompt version, save the v2 draft, and (only
after explicit go-ahead) run **exactly one** controlled 720p
regeneration.

## Decision criteria

Re-run iff all of the following hold:

- [ ] Operator has reviewed this plan end-to-end.
- [ ] Operator has explicitly approved a v2 regeneration.
- [ ] Prompt v2 exists as a `prompt_versions` row with
      `status='approved_for_generation'`.
- [ ] The previous v1 is correctly marked `superseded` so the partial
      unique index on `(content_item_id) where status='approved_for_generation'`
      allows the flip.
- [ ] Resolution = `standard_720p` (we don't reintroduce 1080p just to
      fix a text bug — the cost premium isn't earned by this iteration).
- [ ] No Audio Fixer queued; if v2 looks bad, fork a v3 instead.

## What v2 changes vs v1

### Hard guardrails on label text (the actual fix)

- Only the word **"Pai"** may be rendered as legible on-pack text.
- Every other piece of small label copy MUST be either:
  - Softly blurred / out-of-focus by depth-of-field, OR
  - Obscured by hand, motion blur, or framing.
- The model is explicitly forbidden from inventing ingredient names,
  percentage claims, certifications, batch codes, or marketing
  slogans on packaging.
- If legible small text would be visible, the model should err on the
  side of blur — fall back to an out-of-focus pass rather than
  inventing copy.

### Negative-prompt additions

Append the following to v2's `negative_prompt`:

```
no fabricated label copy, no invented ingredient names, no fake
percentage claims, no fake certifications (organic, vegan, COSMOS,
Soil Association, etc. — none unless verified), no readable label
small-print, no warped/melted/garbled text on packaging, no
substitute brand names, no on-pack words other than "Pai".
```

### Composition cues to support the guardrail

- Show the bottle at a slight angle so the front-label small-print
  catches glare or rotates out of perfect readability.
- Add natural motion blur during the bottle-rotation beat (hand
  movement at typical speed, not a tripod-locked macro).
- Keep the hero on the **"Pai"** brand mark and the dropper — those
  are simple enough shapes that the model renders them cleanly.
- The dropper close-up should focus on the liquid + glass, NOT on
  back-label fine print.

### Tone / format stays unchanged

- 15s, 9:16, 720p, hands-only macro UGC (as v1).
- Calm British register (handled in post — no on-camera VO this take).
- No clinical lighting, no on-screen text overlays, no glossy
  commercial polish — keep it authentic UGC.

## Operator pre-flight checklist

Before clicking "Mark approved for generation" in the prompt editor:

1. Open `/agency/campaigns/<campaignId>/content/<contentId>/prompt`.
2. Confirm v1 status is `superseded` (it will flip automatically when
   v2 is approved, but verifying first avoids the partial-unique
   index rejecting the write).
3. Confirm the new negative-prompt block is present.
4. Confirm `quality_tier = standard_720p`.
5. Save draft.
6. Mark approved for generation — **this still does NOT call
   Enhancor / Seedance / Audio Fixer**. It only records intent.
7. **STOP HERE.** Do not run the generation script until the
   operator gives explicit "go" outside this document.

## What the actual paid run looks like (for reference — DO NOT execute yet)

The Phase 1G+ submit path is documented in
`docs/dashboard_phase_1g_real_submit.md`. The single command intended
here is the existing 720p Seedance submission script with the v2
prompt loaded — exactly one job, then poll, then download, then
ingest.

Crucial: NO Audio Fixer for this regeneration. The Audio Fixer
command panel on the resulting job's detail page MUST be left
untouched.

## If v2 still hallucinates label text

Fork v3 inside the prompt editor. Two further levers, in this order
of preference:

1. Strengthen the negative prompt with explicit per-word bans
   ("BioRegenerate", "Rosehip", "Oil" — keep only "Pai").
2. Change the scene plan so the label is NEVER on-screen tight enough
   to render fine-print — close on the dropper, the liquid in glass,
   or the silhouette only.

Do NOT escalate to 1080p as a fix. Higher resolution makes the model
*more* likely to commit to invented copy, not less.

## Out of scope for this plan

- Audio Fixer (unrelated bug class).
- Switching to a non-Seedance provider.
- Multi-shot batch regenerations.
- Changing brand voice, audience, or platform mix.
- Anything 1080p.

## Next step

Wait for explicit operator approval. Once approved, fork v2 in the
prompt editor, save draft with the changes above, mark approved for
generation, and run exactly ONE 720p Seedance job. Report back the
new job id + provider request id, attach to this document, and
re-review label-text legibility before any further action.
