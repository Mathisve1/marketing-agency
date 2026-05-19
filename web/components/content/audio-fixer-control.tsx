"use client";

// Operator-only. Audio Fixer is *manual* in this product — the button is
// always opt-in and shows a clear cost estimate.

import * as React from "react";
import { Button } from "@/components/ui/button";
import { AUDIO_FIXER_ESTIMATE_LABEL } from "@/lib/quality-tiers";

interface Props {
  completed: boolean;
}

export function AudioFixerControl({ completed }: Props) {
  const [running, setRunning] = React.useState(false);
  const [done, setDone] = React.useState(completed);

  if (done) {
    return (
      <div className="rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-4 py-3 text-sm">
        <div className="font-semibold text-[color:var(--color-success)]">
          Audio Fixer complete
        </div>
        <div className="text-xs text-[color:var(--color-ink-muted)] mt-0.5">
          Final muxed MP4 ready. Compare with the raw take before sharing.
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white px-4 py-3 flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <div className="font-semibold text-sm">Audio Fixer</div>
        <span className="text-[11px] uppercase tracking-[0.14em] text-[color:var(--color-warn)] font-semibold">
          Manual
        </span>
      </div>
      <div className="text-xs text-[color:var(--color-ink-muted)] leading-relaxed">
        Improves the raw native audio. Never auto-runs. Estimated cost:{" "}
        <span className="font-mono">{AUDIO_FIXER_ESTIMATE_LABEL}</span>.
      </div>
      <div className="flex flex-wrap gap-2 pt-1">
        <Button
          size="sm"
          variant="primary"
          disabled={running}
          onClick={() => {
            // Phase 1A: local state only — no provider call.
            setRunning(true);
            window.setTimeout(() => {
              setDone(true);
              setRunning(false);
            }, 1200);
          }}
        >
          {running ? "Submitting…" : "Run Audio Fixer"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setDone(true)}
          disabled={running}
        >
          Skip Audio Fixer
        </Button>
      </div>
    </div>
  );
}
