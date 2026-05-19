"use client";

// Operator-only. Imports lib/quality-tiers (cost math). The client portal
// must never render this component.

import * as React from "react";
import {
  AUDIO_FIXER_ESTIMATE_LABEL,
  DEFAULT_QUALITY_TIER,
  QUALITY_TIERS,
  estimateGenerationCredits,
  formatCredits,
} from "@/lib/quality-tiers";
import type { QualityTierId } from "@/lib/types";

interface Props {
  durationSec?: number;
  initialTier?: QualityTierId;
}

export function QualityTierPicker({
  durationSec = 15,
  initialTier = DEFAULT_QUALITY_TIER,
}: Props) {
  const [tier, setTier] = React.useState<QualityTierId>(initialTier);
  const order: QualityTierId[] = ["draft_480p", "standard_720p", "premium_1080p"];

  return (
    <div className="rounded-lg border border-[color:var(--color-hairline)] bg-white p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h4 className="font-semibold text-sm uppercase tracking-[0.14em] text-[color:var(--color-ink-muted)]">
          Quality tier
        </h4>
        <span className="text-xs text-[color:var(--color-ink-faint)]">
          For a {durationSec}s run
        </span>
      </div>

      <div className="grid sm:grid-cols-3 gap-3">
        {order.map((id) => {
          const t = QUALITY_TIERS[id];
          const active = tier === id;
          const credits = estimateGenerationCredits(id, durationSec);
          return (
            <button
              key={id}
              type="button"
              onClick={() => setTier(id)}
              aria-pressed={active}
              className={[
                "text-left rounded-md border p-4 transition-colors",
                active
                  ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent)]/5"
                  : "border-[color:var(--color-hairline)] hover:bg-[color:var(--color-cream-soft)]",
              ].join(" ")}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">{t.label}</span>
                {t.isDefault && (
                  <span className="text-[10px] uppercase tracking-[0.14em] text-[color:var(--color-accent)] font-semibold">
                    Default
                  </span>
                )}
              </div>
              <div className="text-xs text-[color:var(--color-ink-muted)] mt-1">
                {t.useCase}
              </div>
              <div className="mt-3 flex items-baseline gap-1">
                <span className="text-lg font-semibold tabular-nums">
                  {formatCredits(credits)}
                </span>
                <span className="text-xs text-[color:var(--color-ink-faint)]">
                  credits · est.
                </span>
              </div>
              {t.warning && (
                <div className="mt-2 text-[11px] text-[color:var(--color-warn)] leading-snug">
                  {t.warning}
                </div>
              )}
            </button>
          );
        })}
      </div>

      <div className="mt-4 rounded-md bg-[color:var(--color-cream-soft)] border border-[color:var(--color-hairline)] px-3 py-2 text-xs text-[color:var(--color-ink-muted)] leading-relaxed">
        <span className="font-semibold text-[color:var(--color-ink)]">
          Audio Fixer is manual.
        </span>{" "}
        Review the raw audio before spending extra credits. Estimate:{" "}
        <span className="font-mono">{AUDIO_FIXER_ESTIMATE_LABEL}</span>.
      </div>
    </div>
  );
}
