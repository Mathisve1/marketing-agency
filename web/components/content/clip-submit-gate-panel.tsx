"use client";

// Phase 1S — per-clip submit gate (job detail page).
//
// Shown ONLY for multi-clip jobs (clip_number != null). It:
//   - surfaces clip metadata + the "this is ONE paid Seedance call"
//     warning
//   - enforces the sequencing rule in the UI: clip N's dry-run +
//     submit affordances stay disabled until clip N-1 is completed
//   - runs a SAFE dry-run (prepareClipDryRunAction → read-only Python,
//     no API call, no DB write)
//   - gates the paid path behind a typed "SUBMIT CLIP" phrase that
//     ONLY reveals the operator-driven CLI instructions. The dashboard
//     never performs the paid call itself in Phase 1S.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  prepareClipDryRunAction,
  type ClipDryRunResult,
} from "@/lib/actions/clip-gate";

interface PriorClip {
  clipNumber: number | null;
  status: string;
}

interface Props {
  jobId: string;
  batchId: string;
  clipNumber: number;
  clipRole: string | null;
  durationSeconds: number | null;
  estimatedCredits: number | null;
  status: string;
  targetDurationSeconds: number | null;
  provider: string;
  /** The clip N-1 row in the same batch, or null for clip 1. */
  priorClip: PriorClip | null;
}

const SUBMIT_PHRASE = "SUBMIT CLIP";

export function ClipSubmitGatePanel({
  jobId,
  batchId,
  clipNumber,
  clipRole,
  durationSeconds,
  estimatedCredits,
  status,
  targetDurationSeconds,
  provider,
  priorClip,
}: Props) {
  const [pending, startTransition] = React.useTransition();
  const [res, setRes] = React.useState<ClipDryRunResult | null>(null);
  const [showPayload, setShowPayload] = React.useState(false);
  const [typed, setTyped] = React.useState("");

  // Sequencing rule (mirrors scripts/clip_dry_run.py exactly).
  const isFirstClip = clipNumber <= 1;
  const priorCompleted =
    isFirstClip || (priorClip?.status === "completed");
  const statusIsDraft = status === "draft";
  const gateBlocked = !statusIsDraft || !priorCompleted;

  const blockReason = !statusIsDraft
    ? `This clip's status is "${status}" — only draft clips can be dry-run or submitted.`
    : !priorCompleted
      ? `Clip ${clipNumber} is blocked until clip ${clipNumber - 1} is completed and reviewed (clip ${clipNumber - 1} is currently "${priorClip?.status ?? "missing"}").`
      : null;

  function onDryRun() {
    setRes(null);
    setShowPayload(false);
    startTransition(async () => {
      const r = await prepareClipDryRunAction({ jobId });
      setRes(r);
    });
  }

  const phraseOk = typed.trim() === SUBMIT_PHRASE;

  return (
    <div className="space-y-4">
      <div>
        <div className="text-sm font-semibold">
          Per-clip submit gate (clip {clipNumber}
          {clipRole ? ` · ${clipRole}` : ""})
        </div>
        <p className="mt-1 text-xs text-[color:var(--color-ink-muted)]">
          This clip is <strong>one paid Seedance generation</strong>. The
          dashboard never makes the paid call. Each clip is submitted
          separately; clip 2 stays locked until clip 1 is completed and
          reviewed. No automatic batch or clip-2 submit. No Audio Fixer.
          No client sharing. No stitched final video in this phase.
        </p>
      </div>

      <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 text-xs space-y-1">
        <Line label="Batch id" value={batchId} mono />
        <Line label="Clip number" value={String(clipNumber)} />
        <Line label="Clip role" value={clipRole ?? "—"} />
        <Line
          label="Duration"
          value={durationSeconds !== null ? `${durationSeconds}s` : "—"}
        />
        <Line
          label="Target ad duration"
          value={
            targetDurationSeconds !== null
              ? `${targetDurationSeconds}s (multi-clip)`
              : "—"
          }
        />
        <Line
          label="Estimated credits"
          value={
            estimatedCredits !== null
              ? estimatedCredits.toLocaleString("en-US")
              : "—"
          }
        />
        <Line label="Provider" value={provider} />
        <Line label="Status" value={status} />
        <Line label="Audio Fixer" value="NOT run (manual, never auto)" />
      </div>

      {gateBlocked ? (
        <div
          role="status"
          className="rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/10 px-3 py-2 text-xs"
        >
          <div className="flex items-center gap-2">
            <Badge tone="warn">Blocked</Badge>
            <span className="font-semibold">Sequencing gate</span>
          </div>
          <p className="mt-1 leading-relaxed">{blockReason}</p>
        </div>
      ) : (
        <div
          role="status"
          className="rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-3 py-2 text-xs"
        >
          <Badge tone="success">Eligible</Badge>{" "}
          {isFirstClip
            ? "Clip 1 (open_loop) may be dry-run now."
            : `Clip ${clipNumber - 1} is completed — this clip is unlocked.`}
        </div>
      )}

      {/* Dry-run — always safe, no API call, no DB write */}
      <div className="space-y-2">
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          1. Dry-run payload (safe — no API call, no DB write)
        </div>
        <Button
          size="sm"
          variant="secondary"
          onClick={onDryRun}
          disabled={pending || gateBlocked}
        >
          {pending ? "Building payload…" : "Generate dry-run payload"}
        </Button>
        {gateBlocked && (
          <p className="text-[10px] text-[color:var(--color-ink-muted)]">
            Dry-run is disabled while the sequencing gate is blocked.
          </p>
        )}

        {res && (
          <div className="space-y-2">
            {res.error ? (
              <div
                role="alert"
                className="text-xs rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/40 px-3 py-2"
              >
                {res.error}
              </div>
            ) : (
              <>
                <div
                  className={[
                    "text-xs rounded-md px-3 py-2 border",
                    res.result === "ok"
                      ? "bg-[color:var(--color-success)]/10 border-[color:var(--color-success)]/30"
                      : res.blocked
                        ? "bg-[color:var(--color-warn)]/10 border-[color:var(--color-warn)]/40"
                        : "bg-[color:var(--color-danger)]/10 border-[color:var(--color-danger)]/40",
                  ].join(" ")}
                >
                  <span className="font-semibold">
                    Result: {res.result ?? "unknown"}
                  </span>
                  {res.message && (
                    <p className="mt-1 leading-relaxed">{res.message}</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setShowPayload((v) => !v)}
                  className="text-xs text-[color:var(--color-accent)] underline"
                >
                  {showPayload ? "Hide" : "Show"} script output
                </button>
                {showPayload && (
                  <pre className="text-[10px] leading-snug bg-[color:var(--color-cream-soft)] border border-[color:var(--color-hairline)] rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-all max-h-96">
                    {res.stdout}
                  </pre>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* Paid submit — CLI-only this phase, behind a typed phrase */}
      <div className="space-y-3 rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/5 p-3">
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-danger)] font-semibold">
          2. Paid submit (operator-driven CLI — not performed by the dashboard)
        </div>
        <p className="text-xs leading-relaxed">
          Phase 1S stops at dry-run. The dashboard will not submit this
          clip. To spend the estimated{" "}
          <strong>
            {estimatedCredits !== null
              ? estimatedCredits.toLocaleString("en-US")
              : "—"}{" "}
            credits
          </strong>
          , an operator runs the paid CLI on their own machine with real
          product + influencer reference URLs. Type{" "}
          <code className="font-mono font-semibold">{SUBMIT_PHRASE}</code>{" "}
          to reveal the exact details — typing it does NOT start any call.
        </p>
        <input
          type="text"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          disabled={gateBlocked}
          placeholder={SUBMIT_PHRASE}
          spellCheck={false}
          autoComplete="off"
          className="w-full h-9 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm font-mono outline-none focus:border-[color:var(--color-accent)] disabled:opacity-50"
        />
        {gateBlocked ? (
          <p className="text-[10px] text-[color:var(--color-ink-muted)]">
            Locked — resolve the sequencing gate first.
          </p>
        ) : phraseOk ? (
          <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 text-xs space-y-1">
            <Line label="Job id" value={jobId} mono />
            <Line label="Clip number" value={String(clipNumber)} />
            <Line
              label="Estimated credits"
              value={
                estimatedCredits !== null
                  ? estimatedCredits.toLocaleString("en-US")
                  : "—"
              }
            />
            <Line label="Provider" value={provider} />
            <Line
              label="Duration"
              value={durationSeconds !== null ? `${durationSeconds}s` : "—"}
            />
            <Line
              label="Product reference URL"
              value="operator-supplied (real public HTTPS) — never a placeholder"
            />
            <Line
              label="Influencer reference URL"
              value="operator-supplied (real public HTTPS) — never a placeholder"
            />
            <p className="pt-2 mt-1 border-t border-[color:var(--color-hairline)] text-[10px] text-[color:var(--color-ink-faint)] italic leading-relaxed">
              Paid submit for multi-clip Supabase jobs is intentionally
              deferred beyond Phase 1S. When wired, it will require this
              same typed phrase plus a real webhook + non-placeholder
              asset URLs, and clip {clipNumber === 1 ? 2 : clipNumber + 1}{" "}
              will remain locked until this clip is completed.
            </p>
          </div>
        ) : (
          <p className="text-[10px] text-[color:var(--color-ink-muted)]">
            Type the phrase exactly to reveal the submit details.
          </p>
        )}
      </div>
    </div>
  );
}

function Line({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[color:var(--color-ink-muted)]">{label}</span>
      <span
        className={
          mono
            ? "font-mono text-[10px] break-all text-right"
            : "tabular-nums text-right"
        }
      >
        {value}
      </span>
    </div>
  );
}
