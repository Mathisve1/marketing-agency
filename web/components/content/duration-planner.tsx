"use client";

// Yuvo Studio — operator multi-clip duration planner (PREVIEW ONLY).
//
// Renders the deterministic clip plan for a chosen target duration so
// the operator can SEE how a 20/25/30s ad will be split into connected
// clips, the per-clip + total credit cost, the shared negative prompt,
// the product-reference strategy (Pai V4 lesson), and the stitch plan —
// BEFORE anything is generated.
//
// Hard guarantees:
//   - No server action. No Supabase write. No generation_jobs created.
//   - No paid call. This is a planning surface; the existing
//     "Mark approved for generation" + per-clip job creation flow
//     remains the only path to a paid run, and stays operator-gated.
//   - Pure: delegates all logic to web/lib/planning/duration-plan.ts.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatCredits } from "@/lib/quality-tiers";
import type { QualityTierId } from "@/lib/types";
import {
  TARGET_DURATION_OPTIONS,
  planAdDuration,
  type TargetDurationSeconds,
} from "@/lib/planning/duration-plan";
import { createDurationPlanDraftAction } from "@/lib/actions/generation-jobs";

interface Props {
  /** From the latest prompt version — the shared negative prompt that
   *  every clip inherits. */
  negativePrompt: string | null;
  qualityTier: QualityTierId;
  promptVersionId: string;
  campaignId: string;
  contentId: string;
  /** Only `approved_for_generation` versions can be turned into a
   *  draft plan — the button is disabled + explained otherwise. */
  promptStatus: string;
}

const ROLE_TONE: Record<string, "neutral" | "info" | "success" | "warn"> = {
  standalone: "neutral",
  open_loop: "warn",
  close_loop: "success",
};

export function DurationPlanner({
  negativePrompt,
  qualityTier,
  promptVersionId,
  campaignId,
  contentId,
  promptStatus,
}: Props) {
  const [target, setTarget] = React.useState<TargetDurationSeconds>(15);
  // Default ON: the Pai V4 lesson is that assuming a labelled packshot
  // is safe was wrong. Operator can untick for products with no
  // packaging-text risk.
  const [labelRisk, setLabelRisk] = React.useState(true);
  const [pending, startTransition] = React.useTransition();
  const [result, setResult] = React.useState<
    | { kind: "ok"; message: string; batchId?: string; jobIds?: string[] }
    | { kind: "err"; error: string }
    | null
  >(null);

  const canCreate = promptStatus === "approved_for_generation";

  function onCreateDraft() {
    setResult(null);
    startTransition(async () => {
      const r = await createDurationPlanDraftAction({
        promptVersionId,
        campaignId,
        contentId,
        targetDurationSeconds: target,
      });
      if (r.ok) {
        setResult({
          kind: "ok",
          message: r.message ?? "Draft plan created.",
          batchId: r.batchId,
          jobIds: r.jobIds,
        });
      } else {
        setResult({ kind: "err", error: r.error ?? "Could not create plan." });
      }
    });
  }

  const plan = React.useMemo(
    () =>
      planAdDuration({
        targetDurationSeconds: target,
        qualityTier,
        sharedNegativePrompt: negativePrompt ?? "",
        labelHallucinationRisk: labelRisk,
      }),
    [target, qualityTier, negativePrompt, labelRisk],
  );

  return (
    <div className="space-y-5">
      <div className="rounded-md bg-[color:var(--color-accent)]/8 border border-[color:var(--color-accent)]/25 px-3 py-2 text-xs text-[color:var(--color-ink-muted)]">
        Planning preview only. Choosing a duration here does <strong>not</strong>{" "}
        create jobs or spend credits. A 25–30s ad is planned as a whole and
        realised as multiple connected clips that are stitched after the
        operator approves each one.
      </div>

      {/* Duration selector */}
      <div>
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] mb-1.5">
          Target ad duration
        </div>
        <div className="flex flex-wrap gap-2">
          {TARGET_DURATION_OPTIONS.map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => setTarget(opt)}
              className={[
                "h-9 px-4 rounded-md text-sm font-medium border transition-colors",
                opt === target
                  ? "bg-[color:var(--color-accent)] text-[color:var(--color-cream)] border-[color:var(--color-accent)]"
                  : "bg-white text-[color:var(--color-ink)] border-[color:var(--color-hairline)] hover:bg-[color:var(--color-cream-soft)]",
              ].join(" ")}
            >
              {opt}s
            </button>
          ))}
        </div>
      </div>

      {/* Strategy summary */}
      <div className="grid sm:grid-cols-3 gap-3 text-sm">
        <Stat label="Strategy" value={plan.clipStrategy === "single_clip" ? "Single clip" : "Multi-clip stitched"} />
        <Stat label="Generations" value={`${plan.generationCount} × paid Seedance`} />
        <Stat label="Est. total credits" value={formatCredits(plan.totalEstimatedCredits)} />
      </div>

      {/* Label-risk toggle (Pai V4 lesson) */}
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={labelRisk}
          onChange={(e) => setLabelRisk(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          <span className="font-medium">
            Product has label-hallucination risk
          </span>
          <span className="block text-xs text-[color:var(--color-ink-muted)] mt-0.5">
            {plan.productReferenceStrategy.rationale}
          </span>
        </span>
      </label>

      {/* Clip plan */}
      <div className="space-y-3">
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Clip plan
        </div>
        {plan.clips.map((c) => (
          <div
            key={c.clipNumber}
            className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="info">Clip {c.clipNumber}</Badge>
              <Badge tone="neutral">{c.durationSeconds}s</Badge>
              <Badge tone={ROLE_TONE[c.continuationRole] ?? "neutral"}>
                {c.continuationRole}
              </Badge>
              <span className="text-xs text-[color:var(--color-ink-muted)]">
                full-ad {c.scriptWindow.startSec}–{c.scriptWindow.endSec}s ·{" "}
                {formatCredits(c.estimatedCredits)} cr
              </span>
            </div>
            <div className="font-medium">{c.purpose}</div>
            <Field label="Script segment">{c.scriptSegment}</Field>
            <Field label="Visual direction">{c.visualDirection}</Field>
            <Field label="Continuity">{c.continuityNotes}</Field>
            <Field label="Stitch">{c.stitchNotes}</Field>
          </div>
        ))}
      </div>

      {/* Shared negative + stitch */}
      <div className="grid sm:grid-cols-2 gap-3">
        <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 text-sm">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] mb-1">
            Shared negative prompt (all clips)
          </div>
          <p className="leading-relaxed whitespace-pre-wrap">
            {plan.sharedNegativePrompt || "— (set it on the prompt version)"}
          </p>
        </div>
        <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 text-sm">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] mb-1">
            Stitch plan
          </div>
          <ul className="space-y-0.5 text-[color:var(--color-ink-muted)]">
            <li>method: {plan.stitchPlan.method}</li>
            <li>transition: {plan.stitchPlan.transition}</li>
            <li>audio: {plan.stitchPlan.audioStrategy}</li>
            <li>final asset: {plan.stitchPlan.finalAssetKind}</li>
          </ul>
          <p className="mt-2 text-xs leading-relaxed">{plan.stitchPlan.notes}</p>
        </div>
      </div>

      {/* Commit: create DRAFT jobs (no generation, no paid call) */}
      <div className="border-t border-[color:var(--color-hairline)] pt-4 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            size="md"
            disabled={!canCreate || pending}
            onClick={onCreateDraft}
          >
            {pending
              ? "Creating draft plan…"
              : `Create draft generation plan (${target}s)`}
          </Button>
          <span className="text-xs text-[color:var(--color-ink-muted)]">
            {plan.generationCount} draft clip job
            {plan.generationCount === 1 ? "" : "s"} ·{" "}
            {formatCredits(plan.totalEstimatedCredits)} cr total ·{" "}
            {plan.generationCount} paid generation
            {plan.generationCount === 1 ? "" : "s"} required <em>later</em>
          </span>
        </div>
        <p className="text-xs text-[color:var(--color-ink-faint)] italic">
          This only creates draft jobs. It does <strong>not</strong> generate
          videos, does not call Seedance/Enhancor, and does not spend credits.
          Each clip still requires explicit per-clip operator approval before
          any paid generation.
        </p>
        {!canCreate && (
          <p className="text-xs text-[color:var(--color-warn)]">
            The prompt version must be <em>approved for generation</em> before
            a draft plan can be created (current: {promptStatus}).
          </p>
        )}
        {result?.kind === "ok" && (
          <div
            role="status"
            className="text-sm rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-3 py-2 space-y-1"
          >
            <div>{result.message}</div>
            {result.batchId && (
              <div className="text-xs font-mono break-all">
                batch: {result.batchId}
              </div>
            )}
            {result.jobIds && result.jobIds.length > 0 && (
              <div className="text-xs font-mono break-all">
                jobs: {result.jobIds.join(", ")}
              </div>
            )}
          </div>
        )}
        {result?.kind === "err" && (
          <div
            role="alert"
            className="text-sm rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/40 px-3 py-2"
          >
            <span className="font-semibold">Couldn&rsquo;t create plan: </span>
            {result.error}
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3">
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div className="mt-0.5 font-semibold">{value}</div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div className="mt-0.5 leading-relaxed text-[color:var(--color-ink-muted)]">
        {children}
      </div>
    </div>
  );
}
