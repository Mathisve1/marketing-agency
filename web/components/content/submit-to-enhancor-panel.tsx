"use client";

// Phase 1G — Submit-to-Enhancor confirmation panel.
//
// The dashboard process NEVER initiates a paid call. This panel:
//   1. Displays the exact command the operator must run in their
//      terminal to spend credits.
//   2. Locks the command behind two confirmation gates:
//        - a checkbox: "I understand this will spend credits"
//        - a typed confirmation: the operator must type SUBMIT
//      Both gates only unlock the COPY button — they do not trigger any
//      network call. This is intentional belt-and-braces: copying the
//      command into the clipboard still requires the operator to paste
//      and press Enter in a real terminal.
//   3. Surfaces the dry-run command unconditionally; that one never
//      spends credits.

import * as React from "react";
import { Button } from "@/components/ui/button";

interface Props {
  jobId: string;
  estimatedCredits: number;
  qualityTier: string;
  resolution: string | null;
  durationSeconds: number | null;
  /** Already-completed jobs hide the panel; failed/cancelled show only
   *  the dry-run line. */
  status:
    | "draft"
    | "queued"
    | "submitted"
    | "processing"
    | "completed"
    | "failed"
    | "cancelled";
}

const SUBMIT_TYPED_PHRASE = "SUBMIT";

function CopyableCommand({
  label,
  command,
  disabled = false,
  disabledReason,
}: {
  label: string;
  command: string;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const [copied, setCopied] = React.useState(false);

  async function onCopy() {
    if (disabled) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Some browsers / contexts block clipboard. The command is still
      // visible in the <pre>, so the operator can select+copy by hand.
    }
  }

  return (
    <div className="space-y-2">
      <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <pre className="text-[11px] leading-snug bg-[color:var(--color-cream-soft)] border border-[color:var(--color-hairline)] rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-all">
        {command}
      </pre>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant={disabled ? "ghost" : "secondary"}
          onClick={onCopy}
          disabled={disabled}
          aria-disabled={disabled}
        >
          {copied ? "Copied!" : disabled ? "Locked" : "Copy command"}
        </Button>
        {disabled && disabledReason && (
          <span className="text-[10px] text-[color:var(--color-ink-muted)]">
            {disabledReason}
          </span>
        )}
      </div>
    </div>
  );
}

export function SubmitToEnhancorPanel({
  jobId,
  estimatedCredits,
  qualityTier,
  resolution,
  durationSeconds,
  status,
}: Props) {
  const [acknowledged, setAcknowledged] = React.useState(false);
  const [typedConfirmation, setTypedConfirmation] = React.useState("");

  // The dry-run command is ALWAYS shown — it never spends credits.
  const dryRunCmd = `py -3.11 scripts/run_generation_job.py --job-id ${jobId} --dry-run`;

  // The submit command needs real product / influencer / webhook URLs.
  // The placeholders below are intentionally obvious; the script's
  // is_placeholder_url() refuses to actually run with them in place.
  const submitCmd = [
    "py -3.11 scripts/run_generation_job.py",
    `  --job-id ${jobId}`,
    "  --product-url https://your-cdn.example/pai-bottle.jpg",
    "  --influencer-url https://your-cdn.example/creator.jpg",
    "  --webhook-url https://your-hooks.example/enhancor/seedance",
    "  --submit --confirm",
  ].join(" \\\n");

  const submitUnlocked =
    acknowledged && typedConfirmation.trim() === SUBMIT_TYPED_PHRASE;

  if (status === "completed") {
    // Nothing to submit — the job is already done.
    return null;
  }

  const submitAllowed = status === "draft" || status === "queued";

  return (
    <div className="space-y-4">
      <div>
        <div className="text-sm font-semibold">Submit to Enhancor</div>
        <p className="mt-1 text-xs text-[color:var(--color-ink-muted)]">
          The dashboard never makes the paid call itself. Copy the
          generated command and run it in your terminal. The script
          loads <code className="font-mono">ENHANCOR_API_KEY</code> from{" "}
          <code className="font-mono">.env</code> on your machine.
        </p>
      </div>

      <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 text-xs space-y-1">
        <Line label="Provider" value="enhancor_seedance" />
        <Line label="Mode" value="ugc" />
        <Line label="Quality tier" value={qualityTier} />
        {resolution && <Line label="Resolution" value={resolution} />}
        {durationSeconds !== null && (
          <Line label="Duration" value={`${durationSeconds}s`} />
        )}
        <Line
          label="Estimated credits"
          value={estimatedCredits.toLocaleString("en-US")}
        />
        <Line label="Audio Fixer" value="NOT included (manual, later)" />
      </div>

      <CopyableCommand label="1. Dry-run (safe, no API call)" command={dryRunCmd} />

      <div className="space-y-3 rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/5 p-3">
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-danger)] font-semibold">
          ⚠ Paid submission
        </div>
        <p className="text-xs leading-relaxed">
          The next command will POST to Seedance{" "}
          <code className="font-mono">/queue</code> and spend an estimated{" "}
          <strong>{estimatedCredits.toLocaleString("en-US")} credits</strong>
          . Replace the placeholder URLs with real public HTTPS URLs
          before running — the script refuses to submit with placeholders.
        </p>
        <label className="flex items-start gap-2 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            disabled={!submitAllowed}
            className="mt-0.5"
          />
          <span>I understand this will spend credits.</span>
        </label>
        <label className="block text-xs space-y-1">
          <span>
            Type <code className="font-mono font-semibold">SUBMIT</code> to
            unlock the Copy button:
          </span>
          <input
            type="text"
            value={typedConfirmation}
            onChange={(e) => setTypedConfirmation(e.target.value)}
            disabled={!submitAllowed || !acknowledged}
            placeholder="SUBMIT"
            className="w-full h-9 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm font-mono outline-none focus:border-[color:var(--color-accent)]"
            spellCheck={false}
            autoComplete="off"
          />
        </label>
        <CopyableCommand
          label="2. Submit (PAID)"
          command={submitCmd}
          disabled={!submitAllowed || !submitUnlocked}
          disabledReason={
            !submitAllowed
              ? `Status is ${status} — only draft/queued jobs are submittable.`
              : !acknowledged
                ? "Tick the acknowledgement first."
                : "Type SUBMIT to unlock."
          }
        />
      </div>

      <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        Audio Fixer is manual. Phase 1G never auto-runs it. Once the raw
        video lands, a separate confirmation flow will be added under
        equally explicit gates.
      </p>
    </div>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[color:var(--color-ink-muted)]">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}
