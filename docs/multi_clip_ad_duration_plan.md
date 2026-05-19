# Multi-clip ad duration plan (15 / 20 / 25 / 30s)

Status: **planning + model layer landed. No paid call. No schema applied
yet** (migration 007 is a file, not yet run — applying it is a deploy
and needs explicit operator approval). The planning model works without
the migration; the migration only persists a chosen plan.

## 1. Why 25–30s ads are planned from the start

Enhancor/Seedance produces at most ~15s of clean UGC per call. The
wrong way to get a 30s ad is "generate 15s, then randomly extend" —
that yields two halves that feel like two different ads (different
energy, an ending in the middle, a cold restart). The right way: **plan
the whole ad first**, then realise it as N connected clips that are
stitched after each clip is operator-approved.

Concretely, the operator picks a target duration and the system derives
a deterministic clip split, a per-clip narrative role, a shared
negative prompt + reference strategy, a stitch plan, and a per-clip +
total credit estimate — all before clip 1 is generated.

## 2. Duration mapping (single source of truth)

| Target | clip_count | clip durations | strategy |
|---|---|---|---|
| 15s | 1 | `[15]` | `single_clip` |
| 20s | 2 | `[15, 5]` | `multi_clip_stitched` |
| 25s | 2 | `[15, 10]` | `multi_clip_stitched` |
| 30s | 2 | `[15, 15]` | `multi_clip_stitched` |

Implemented identically in two pure modules (kept byte-for-byte in
sync):

- `web/lib/planning/duration-plan.ts` — `planAdDuration()` / `toClipPlanJson()`
- `agents/producer/dashboard/duration_plan.py` — `plan_ad_duration()` / `to_clip_plan_json()`

Both are pure (no I/O, no network, no Supabase, no paid call) and
deterministic.

## 3. Clip structure

Each planned clip carries:

- `clip_number` — 1-based order within the batch
- `duration_seconds`
- `purpose` — what this clip does in the ad
- `continuation_role` — `standalone` | `open_loop` | `close_loop`
- `script_window` — the slice of the **full-ad** timeline this clip covers
- `script_segment` — operator-editable scaffold (the operator authors
  the real copy in the prompt editor)
- `visual_direction` — same-creator/setting/lighting constraints
- `continuity_notes` — what clip 2 must reuse from clip 1
- `stitch_notes` — what happens at the seam
- `estimated_credits` — per-clip estimate at the chosen quality tier

`continuation_role` is the key idea:

- **open_loop** (clip 1 of a multi-clip ad): hook + context, ends on an
  unfinished thought / soft transition. It does **not** resolve the ad.
- **close_loop** (last clip): picks up exactly where clip 1 left off
  (same sentence energy, same creator/room/lighting), lands the CTA.
- **standalone** (the 15s case): self-contained hook → context → soft
  close.

### Example 30s structure (full-ad timeline)

```
0–5s    relatable hook / problem            ┐ clip 1 (15s, open_loop)
5–12s   creator explains context            │
12–15s  soft transition (NOT an ending)     ┘
15–22s  routine / application / proof        ┐ clip 2 (15s, close_loop)
22–27s  benefit / reason-to-believe          │
27–30s  natural CTA / closing line           ┘
```

20s = clip 1 `[0–15]` open_loop + clip 2 `[15–20]` close_loop (5s CTA).
25s = clip 1 `[0–15]` open_loop + clip 2 `[15–25]` close_loop (10s).

## 4. Prompt strategy for longer ads

Rules enforced by the planner's scaffolds + documented for the operator:

1. Plan the whole 25–30s ad before generating clip 1.
2. Each clip is **not** a separate ad. Clip 1 of a multi-clip ad must
   not fully close.
3. Clip 2 is a continuation, not a new take: same creator, setting,
   lighting, wardrobe, tone, focal length, **and the same product
   reference image**.
4. Product is a soft prop, never a label-hero shot.
5. No readable label text. For label-hallucination-risk products, use a
   blank-label / label-stripped reference (see §9).
6. Native audio only unless the operator manually chooses Audio Fixer
   later (Audio Fixer is never auto-run).
7. No on-screen captions unless explicitly requested.
8. No product close-ups with packaging text.

## 5. Schema / data model (migration 007 — file only, not applied)

`supabase/migrations/007_multi_clip_duration.sql` adds (all additive,
fully backwards-compatible — every existing 15s job + the Pai V4 job
read back unchanged with NULL clip fields):

- `generation_batches.target_duration_seconds int` (15|20|25|30, NULL = legacy)
- `generation_batches.clip_plan jsonb` (compact plan from `toClipPlanJson`)
- `generation_jobs.clip_number int` (NULL = legacy single clip)
- `generation_jobs.clip_role text` (standalone|open_loop|close_loop, NULL = legacy)
- `generation_jobs.status` widened to add `stitched`
- `generated_assets.kind` widened to add `stitched_video`, `final_video`
- index `generation_jobs (batch_id, clip_number)`

The whole-ad plan lives on the **batch**; each clip remains exactly one
`generation_jobs` row (1 paid Seedance call = 1 clip). The compact
`clip_plan` JSON shape (what gets stored):

```json
{
  "target_duration_seconds": 30,
  "clip_strategy": "multi_clip_stitched",
  "clips": [
    { "clip_number": 1, "duration_seconds": 15,
      "purpose": "Hook + context — opens a loop…",
      "continuation_role": "open_loop" },
    { "clip_number": 2, "duration_seconds": 15,
      "purpose": "Routine/proof continuation + natural CTA…",
      "continuation_role": "close_loop" }
  ],
  "stitch_plan": {
    "method": "concat",
    "transition": "hard_cut_or_natural_pause",
    "audio_strategy": "native_audio_per_clip_then_review",
    "final_asset_kind": "stitched_video"
  },
  "product_reference_strategy": { "blank_label_required": true },
  "estimated_credits_total": 5292,
  "generation_count": 2
}
```

## 6. Dashboard UX

`web/components/content/duration-planner.tsx` — a **preview-only** client
component wired into the prompt editor
(`/agency/campaigns/[campaignId]/content/[contentId]/prompt`). It shows:

- target duration selector (15 / 20 / 25 / 30)
- strategy + generation count + total credit estimate
- a "product has label-hallucination risk" toggle (default ON — Pai V4
  lesson)
- the full clip plan (per-clip purpose, role, window, script/visual/
  continuity/stitch scaffolds, per-clip credits)
- the shared negative prompt + stitch plan

It performs **no** server action, **no** Supabase write, **no**
generation. It explicitly tells the operator the cost is N paid
generations, never "one 30s API call". The existing "Mark approved for
generation" + per-clip job-creation flow stays the only path to a paid
run.

## 7. Generation batch / job lifecycle (multi-clip)

When `target_duration_seconds > 15` (design — not auto-executed):

1. One `generation_batches` row, `target_duration_seconds` + `clip_plan`
   set.
2. N `generation_jobs` rows under that batch, each with its own
   `clip_number`, `clip_role`, `duration_seconds`, all referencing the
   same prompt version (or a clip-level prompt derived from it).
3. Each clip is generated by its own paid Seedance call, polled,
   downloaded, label-checked, ingested — exactly the proven Pai V4
   single-clip flow, repeated per clip.
4. Job status set: `draft → ready → submitted → processing → completed
   → failed`, plus `stitched` once the clip is part of an approved
   stitched final.

No job is auto-submitted. Each clip is an explicit operator-approved
paid run, same gate as today.

## 8. Stitching lifecycle

Placeholder plan (no heavy video editing implemented — no concat script
exists in the repo today, confirmed by inspection):

- When **all** clips reach `status=completed`, the final ad is produced
  by `concat` in clip order.
- Raw per-clip videos stay as `generated_assets.kind='raw_video'`.
- The concatenated deliverable is a new asset
  `kind='stitched_video'`.
- `content_items.client_safe_video_url` only points at the stitched
  final **after** operator approval + a label-readability check on the
  stitched output (not just per-clip).
- Audio: native per clip, reviewed at the seam before client share;
  Audio Fixer remains manual + opt-in, never auto.

Concrete concat tooling (e.g. an ffmpeg `concat` demuxer script using
the already-vendored `imageio-ffmpeg` binary) is a small, safe future
addition — deliberately **not** implemented in this phase to keep scope
to planning + model.

## 9. Pai V4 label-reference lesson (carried into the planner)

Pai V1–V3 proved that **prompt-only label guardrails fail**: Seedance
treats the supplied product reference image as authoritative visual
ground truth and reconstructs/hallucinates its label copy
("TriPepttide", "AGE CONFIDENCE", lowercase "pai", mangled French
sub-lines) no matter how strict the negative prompt is.

**V4 fixed it by changing the reference image, not the prompt:** a
Pillow-generated label-stripped reference
(`scripts/strip_pai_label.py`) plus "product as soft prop, creator
carries the ad" produced a clean, client-safe take on the retry (job
`4e4e…`), verified across 5 sampled frames.

The planner bakes this in:

- `labelHallucinationRisk` defaults **true** in the UI.
- When true, `product_reference_strategy.blank_label_required = true`
  and the rationale string explicitly cites the V4 lesson.
- The clip scaffolds all say "product is a soft prop, never a
  label-hero shot" and "no readable label text".
- Stitching requires a label-readability check on the **stitched**
  output before `client_safe_video_url` is updated.

Rule of thumb for any future product: if the real packaging has dense
readable copy, generate a label-stripped reference first; never feed a
sharp packshot; verify label readability before client share.

## 10. Cost implications

The operator pays **per generation**, never "per ad". A multi-clip ad
costs the sum of its clips:

| Target | Generations | Est. credits @ standard_720p |
|---|---|---|
| 15s | 1 | ~2,646 (1 × 15s) |
| 20s | 2 | ~2,646 + ~882 = ~3,528 |
| 25s | 2 | ~2,646 + ~1,764 = ~4,410 |
| 30s | 2 | ~2,646 + ~2,646 = ~5,292 |

(Estimates use `estimateGenerationCredits` = `perSecondCredits ×
duration`. The real cost at submit time is the `cost` the provider
echoes — Pai V4's actual 15s/720p cost was ~2,268, lower than the
2,646 estimate; the estimator is intentionally conservative.) The UI
never presents a 30s ad as one API call.

## 11. Safety guardrails

- No paid call anywhere in the planning layer. The planner is pure.
- Migration 007 is a file, not applied — applying = deploy = explicit
  operator approval required.
- Backwards compatible: existing Pai V4 job (`4e4e…`) and all 15s jobs
  read back unchanged (NULL clip fields = legacy single clip).
- Per-clip generation stays behind the existing operator approval gate.
- Audio Fixer never auto-runs.
- Stitched final only becomes the client-facing video after operator
  approval + label-readability check.

## 12. Recommended next step

Build a safe "30s draft planner" action in the dashboard that, on
explicit operator click, (a) applies migration 007, (b) creates one
batch + N draft clip jobs from the plan, and (c) **stops** — still
requiring the existing per-clip "approve + submit" gate before any paid
generation. No auto-submit, ever.
