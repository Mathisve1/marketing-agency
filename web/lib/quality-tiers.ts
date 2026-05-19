// Single source of truth for the dashboard's quality-tier UI.
//
// Cost math comes directly from:
//   docs/enhancor_api_spec.md  →  "Recommended quality tiers"
//   docs/enhancor_capability_matrix.md  →  "Recommended quality tiers (Phase-1A product surface)"
//
// Rules:
//   - Default tier = standard_720p.
//   - Audio Fixer is manual on every tier (never auto).
//   - Premium 1080p shows a visible cost-warning chip.
//   - Operator-only file. The client portal NEVER imports this.

import type { QualityTier, QualityTierId } from "./types";

export const AUDIO_FIXER_ESTIMATE_CREDITS = 2100;
export const AUDIO_FIXER_ESTIMATE_LABEL = "≈ 2 100 credits (Pai 15s reference)";

export const QUALITY_TIERS: Record<QualityTierId, QualityTier> = {
  draft_480p: {
    id: "draft_480p",
    label: "Draft 480p",
    resolution: "480p",
    perSecondCredits: 82,
    fifteenSecondCredits: 1230,
    useCase: "Cheap prompt-iteration runs while a concept is being shaped. Never shared with the client.",
    audioFixer: "manual",
    isDefault: false,
  },
  standard_720p: {
    id: "standard_720p",
    label: "Standard 720p",
    resolution: "720p",
    perSecondCredits: 176.4,
    fifteenSecondCredits: 2646,
    useCase: "Default for real UGC work and the first cut shared with the client.",
    audioFixer: "manual",
    isDefault: true,
  },
  premium_1080p: {
    id: "premium_1080p",
    label: "Premium 1080p",
    resolution: "1080p",
    perSecondCredits: 396,
    fifteenSecondCredits: 5940,
    useCase: "Opt-in only. May read as too AI-polished for UGC; review the 720p take first.",
    audioFixer: "manual",
    isDefault: false,
    warning: "5 940 credits per 15s — explicit opt-in required.",
  },
};

export const DEFAULT_QUALITY_TIER: QualityTierId = "standard_720p";

/** Estimate Enhancor credits for a single generation run, no Audio Fixer.
 *  We round to the nearest credit; the source of truth at submission time
 *  is the `cost` echoed by the provider. */
export function estimateGenerationCredits(
  tierId: QualityTierId,
  durationSec: number,
): number {
  const tier = QUALITY_TIERS[tierId];
  return Math.round(tier.perSecondCredits * durationSec);
}

/** Phase 1F alias used by the generation_jobs data + actions layer.
 *  Same math as estimateGenerationCredits, named after the provider so
 *  the job-detail UI reads naturally ("Seedance credits estimate: …").
 *
 *  Reference (15s duration):
 *    draft_480p     → 1230
 *    standard_720p  → 2646   (default)
 *    premium_1080p  → 5940
 */
export function getEstimatedSeedanceCredits(
  tierId: QualityTierId,
  durationSec: number,
): number {
  return estimateGenerationCredits(tierId, durationSec);
}

/** Audio-Fixer estimate. ≈2,100 credits flat per the Pai 15s reference
 *  (real run logged 2,103.75 → 2,104 rounded). Kept as a separate
 *  exported helper so the job-detail page can render the line item next
 *  to (but not added on top of) the Seedance estimate. */
export function getEstimatedAudioFixerCredits(_durationSec: number): number {
  // The pricing curve is currently flat; the durationSec argument is
  // accepted for forward-compatibility once a per-second figure is on
  // file. Underscored to signal "intentionally unused for now".
  void _durationSec;
  return AUDIO_FIXER_ESTIMATE_CREDITS;
}

/** Estimate total run cost including manual Audio Fixer. Audio Fixer is
 *  flat-rate per Pai reference until the pricing curve is on file. */
export function estimateTotalCredits(
  tierId: QualityTierId,
  durationSec: number,
  withAudioFixer: boolean,
): number {
  return (
    estimateGenerationCredits(tierId, durationSec) +
    (withAudioFixer ? AUDIO_FIXER_ESTIMATE_CREDITS : 0)
  );
}

/** Friendly formatter for credit counts, e.g. 2646 → "2,646". */
export function formatCredits(n: number): string {
  return n.toLocaleString("en-US");
}
