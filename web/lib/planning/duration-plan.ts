// Yuvo Studio — multi-clip ad duration planning model.
//
// PURE module. No React, no I/O, no Supabase, no env. Importable from
// both the operator UI (preview) and any future server action (persist
// the plan). Deterministic: same input → same plan.
//
// Product decision (do NOT change without operator sign-off):
//   We never "generate 15s then randomly extend". A 25–30s ad is
//   PLANNED as a whole up front, then realised as multiple connected
//   clips. Enhancor/Seedance generates at most ~15s of clean UGC per
//   call, so longer ads are N connected clips stitched after the fact.
//
//   15s  → 1 clip  [15]
//   20s  → 2 clips  [15, 5]
//   25s  → 2 clips  [15, 10]
//   30s  → 2 clips  [15, 15]
//
// The Pai V4 lesson is baked in: for products with label-hallucination
// risk the plan's product_reference_strategy defaults to a
// blank-label / label-stripped reference, NOT a sharp packshot, and the
// product is treated as a soft prop with no readable label text.

import { estimateGenerationCredits } from "@/lib/quality-tiers";
import type { QualityTierId } from "@/lib/types";

export type TargetDurationSeconds = 15 | 20 | 25 | 30;

export const TARGET_DURATION_OPTIONS: readonly TargetDurationSeconds[] = [
  15, 20, 25, 30,
] as const;

export type ClipStrategy = "single_clip" | "multi_clip_stitched";

/** A clip's narrative job inside the whole ad. `open_loop` deliberately
 *  does NOT resolve the ad (clip 2 follows); `close_loop` lands the CTA;
 *  `standalone` is the 15s single-clip case. */
export type ContinuationRole = "standalone" | "open_loop" | "close_loop";

export interface PlannedClip {
  clipNumber: number;
  durationSeconds: number;
  /** Short human label of what this clip does in the ad. */
  purpose: string;
  continuationRole: ContinuationRole;
  /** The slice of the overall script this clip covers, expressed as a
   *  time window over the FULL ad timeline (not the clip's local time). */
  scriptWindow: { startSec: number; endSec: number };
  /** Operator-editable scaffold; the real copy is authored in the
   *  prompt editor. These are starting points, not final prompts. */
  scriptSegment: string;
  visualDirection: string;
  continuityNotes: string;
  stitchNotes: string;
  /** Per-clip credit estimate at the chosen tier. */
  estimatedCredits: number;
}

export interface StitchPlan {
  method: "concat";
  transition: "hard_cut_or_natural_pause";
  audioStrategy: "native_audio_per_clip_then_review";
  /** Final asset kind once all clips are stitched + operator-approved. */
  finalAssetKind: "stitched_video";
  notes: string;
}

export interface ProductReferenceStrategy {
  /** When true, the plan instructs use of a blank-label / label-stripped
   *  reference image and "product as soft prop, no readable label". */
  blankLabelRequired: boolean;
  rationale: string;
}

export interface AdDurationPlan {
  targetDurationSeconds: TargetDurationSeconds;
  clipStrategy: ClipStrategy;
  clipCount: number;
  clips: PlannedClip[];
  /** Shared across every clip — same creator, setting, tone, product
   *  rules. Authored once in the prompt editor, applied to all clips. */
  sharedNegativePrompt: string;
  productReferenceStrategy: ProductReferenceStrategy;
  stitchPlan: StitchPlan;
  /** Sum of every clip's estimate. The operator pays per generation —
   *  this is N generations, never "one 30s API call". */
  totalEstimatedCredits: number;
  /** How many paid Seedance generations this plan implies. */
  generationCount: number;
}

// ---------------------------------------------------------------------------
// Clip duration table — the single source of truth for the split.
// ---------------------------------------------------------------------------
const CLIP_DURATIONS: Record<TargetDurationSeconds, number[]> = {
  15: [15],
  20: [15, 5],
  25: [15, 10],
  30: [15, 15],
};

/** Narrative skeleton for the WHOLE ad, expressed over the full
 *  timeline. The clip planner slices this by each clip's window so
 *  clip 2 reads as a continuation, not a fresh ad. Mirrors the 30s
 *  structure in docs/multi_clip_ad_duration_plan.md. */
interface Beat {
  startSec: number;
  endSec: number;
  label: string;
}

function adBeats(target: TargetDurationSeconds): Beat[] {
  if (target === 15) {
    return [
      { startSec: 0, endSec: 3, label: "relatable hook" },
      { startSec: 3, endSec: 10, label: "creator context + soft proof" },
      { startSec: 10, endSec: 15, label: "natural closing line" },
    ];
  }
  // 20 / 25 / 30 — open-loop clip 1, continuation clip 2.
  return [
    { startSec: 0, endSec: 5, label: "relatable hook / problem" },
    { startSec: 5, endSec: 12, label: "creator explains context" },
    { startSec: 12, endSec: 15, label: "soft transition (NOT an ending)" },
    {
      startSec: 15,
      endSec: Math.max(22, target - 8),
      label: "routine / application / proof continuation",
    },
    {
      startSec: Math.max(22, target - 8),
      endSec: target - 3,
      label: "benefit / reason-to-believe",
    },
    { startSec: target - 3, endSec: target, label: "natural CTA / closing line" },
  ];
}

function beatsInWindow(beats: Beat[], startSec: number, endSec: number): Beat[] {
  return beats.filter((b) => b.startSec < endSec && b.endSec > startSec);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface PlanInput {
  targetDurationSeconds: TargetDurationSeconds;
  qualityTier: QualityTierId;
  /** Carried from the prompt version. The planner does NOT invent ad
   *  copy — it scaffolds windows; the operator authors the real text. */
  sharedNegativePrompt: string;
  /** Pai V4 learning: set true for products whose real packaging has
   *  readable copy the model will hallucinate (skincare with dense
   *  labels, etc.). Drives the blank-label reference instruction. */
  labelHallucinationRisk: boolean;
}

export function planAdDuration(input: PlanInput): AdDurationPlan {
  const { targetDurationSeconds, qualityTier } = input;
  const durations = CLIP_DURATIONS[targetDurationSeconds];
  const clipStrategy: ClipStrategy =
    durations.length === 1 ? "single_clip" : "multi_clip_stitched";
  const beats = adBeats(targetDurationSeconds);

  let cursor = 0;
  const clips: PlannedClip[] = durations.map((dur, idx) => {
    const clipNumber = idx + 1;
    const startSec = cursor;
    const endSec = cursor + dur;
    cursor = endSec;

    const isOnlyClip = durations.length === 1;
    const isLastClip = clipNumber === durations.length;
    const continuationRole: ContinuationRole = isOnlyClip
      ? "standalone"
      : isLastClip
        ? "close_loop"
        : "open_loop";

    const windowBeats = beatsInWindow(beats, startSec, endSec);
    const beatSummary = windowBeats.map((b) => b.label).join("; ");

    const purpose = isOnlyClip
      ? "Full self-contained 15s UGC ad"
      : clipNumber === 1
        ? "Hook + context — opens a loop, does NOT resolve the ad"
        : "Routine/proof continuation + natural CTA — closes the loop";

    const scriptSegment =
      `[Full-ad timeline ${startSec}-${endSec}s] ${beatSummary}. ` +
      (continuationRole === "open_loop"
        ? "End on an unfinished thought / soft transition — clip 2 must " +
          "feel like the same take continuing, not a new ad."
        : continuationRole === "close_loop"
          ? "Pick up exactly where clip 1 left off (same sentence energy, " +
            "same setting/lighting/creator), then land the CTA naturally."
          : "Self-contained: hook → context → soft close.");

    const visualDirection =
      "Same creator, same room, same lighting, same wardrobe as every " +
      "other clip. Real-friend UGC register, handheld feel. Product is a " +
      "soft prop, never a label-hero shot. " +
      (clipNumber === 1
        ? "Establish the creator + setting in the first 2s."
        : "Match clip 1's framing + colour exactly so the cut is invisible.");

    const continuityNotes =
      isOnlyClip
        ? "Single clip — no cross-clip continuity needed."
        : clipNumber === 1
          ? "Lock creator identity, wardrobe, room, lighting, focal length, " +
            "and product-reference image — clip 2 reuses ALL of these."
          : "MUST reuse clip 1's exact creator/wardrobe/room/lighting and " +
            "the SAME product reference image. No new location, no new " +
            "outfit, no time-of-day change.";

    const stitchNotes = isOnlyClip
      ? "No stitch — single deliverable."
      : isLastClip
        ? "Concatenate after clip 1. Expect a hard cut or a natural " +
          "pause at the seam; review audio continuity before client share."
        : "This clip ends mid-thought; the seam to clip 2 should land on " +
          "a natural pause/breath, not a finished sentence.";

    return {
      clipNumber,
      durationSeconds: dur,
      purpose,
      continuationRole,
      scriptWindow: { startSec, endSec },
      scriptSegment,
      visualDirection,
      continuityNotes,
      stitchNotes,
      estimatedCredits: estimateGenerationCredits(qualityTier, dur),
    };
  });

  const totalEstimatedCredits = clips.reduce(
    (sum, c) => sum + c.estimatedCredits,
    0,
  );

  const productReferenceStrategy: ProductReferenceStrategy = {
    blankLabelRequired: input.labelHallucinationRisk,
    rationale: input.labelHallucinationRisk
      ? "Pai V4 lesson: prompt-only label guardrails FAILED; a " +
        "blank-label / label-stripped product reference SUCCEEDED. Use a " +
        "label-stripped reference image, treat the product as a soft " +
        "prop, and verify no readable label text before client share."
      : "No known label-hallucination risk for this product. Standard " +
        "reference acceptable, but still keep the product a soft prop " +
        "and avoid packaging-text close-ups.",
  };

  const stitchPlan: StitchPlan = {
    method: "concat",
    transition: "hard_cut_or_natural_pause",
    audioStrategy: "native_audio_per_clip_then_review",
    finalAssetKind: "stitched_video",
    notes:
      clipStrategy === "single_clip"
        ? "Single clip — the raw_video IS the deliverable; no stitch step."
        : "When ALL clips reach status=completed, concat in clip order " +
          "into one final asset (kind=stitched_video). Raw clips stay as " +
          "raw_video assets. client_safe_video_url only points at the " +
          "stitched final AFTER operator approval + label-readability check.",
  };

  return {
    targetDurationSeconds,
    clipStrategy,
    clipCount: clips.length,
    clips,
    sharedNegativePrompt: input.sharedNegativePrompt,
    productReferenceStrategy,
    stitchPlan,
    totalEstimatedCredits,
    generationCount: clips.length,
  };
}

/** Serialise to the JSON shape stored in
 *  `generation_batches.clip_plan` (migration 007). Kept separate from
 *  the rich planning object so the DB column stays compact + stable. */
export function toClipPlanJson(plan: AdDurationPlan): Record<string, unknown> {
  return {
    target_duration_seconds: plan.targetDurationSeconds,
    clip_strategy: plan.clipStrategy,
    clips: plan.clips.map((c) => ({
      clip_number: c.clipNumber,
      duration_seconds: c.durationSeconds,
      purpose: c.purpose,
      continuation_role: c.continuationRole,
    })),
    stitch_plan: {
      method: plan.stitchPlan.method,
      transition: plan.stitchPlan.transition,
      audio_strategy: plan.stitchPlan.audioStrategy,
      final_asset_kind: plan.stitchPlan.finalAssetKind,
    },
    product_reference_strategy: {
      blank_label_required: plan.productReferenceStrategy.blankLabelRequired,
    },
    estimated_credits_total: plan.totalEstimatedCredits,
    generation_count: plan.generationCount,
  };
}
