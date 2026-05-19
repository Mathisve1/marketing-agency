"use client";

// Phase 1I — Manual Audio Fixer command panel.
//
// OPERATOR-ONLY. Mounted on /agency/jobs/[jobId] when the parent
// generation_jobs row reached COMPLETED and a `raw_video`
// generated_assets row is present. The panel:
//
//   1. Shows the estimated Audio Fixer cost (~2,100 cr per Pai 15s
//      reference) and a one-line warning.
//   2. Surfaces the Phase 1I dry-run command — never any paid command.
//      The script's --submit + --confirm path exists but Phase 1I is
//      explicit: dashboard prints dry-run only.
//   3. Carries a "I understand this would spend credits" checkbox + the
//      typed SUBMIT phrase identical to the Phase 1G Submit-to-Enhancor
//      panel — but the unlocked Copy button copies the EXACT command
//      the operator would run, NOT a button that calls the API.
//
// Phase 1I never runs Audio Fixer. The card stays "manual, opt-in,
// operator-driven CLI" until the operator explicitly approves a paid
// run.

import * as React from "react";
import { Button } from "@/components/ui/button";

interface Props {
  jobId: string;
  audioFixerEstimateCredits: number;
}

const TYPED_PHRASE = "AUDIO-FIXER";

function CopyableCommand({
  label,
  command,
  disabled = false,
  disabledReason,
  intent = "neutral",
}: {
  label: string;
  command: string;
  disabled?: boolean;
  disabledReason?: string;
  intent?: "neutral" | "danger";
}) {
  const [copied, setCopied] = React.useState(false);
  async function onCopy() {
    if (disabled) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* fallthrough: the operator can select+copy from the <pre>. */
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
          variant={
            disabled ? "ghost" : intent === "danger" ? "danger" : "secondary"
          }
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

export function AudioFixerCommandPanel({
  jobId,
  audioFixerEstimateCredits,
}: Props) {
  const [acknowledged, setAcknowledged] = React.useState(false);
  const [typed, setTyped] = React.useState("");

  // Dry-run command is ALWAYS unlocked. No credits at risk.
  const dryRunCmd =
    `py -3.11 scripts/run_audio_fixer_job.py --generation-job-id ${jobId} --dry-run`;
  // Hypothetical paid command. Phase 1I keeps the Copy button locked
  // unless the operator both acknowledges AND types the phrase. Even
  // when unlocked, the dashboard never makes the call — the operator
  // runs the command themselves.
  const paidCmd = [
    "py -3.11 scripts/run_audio_fixer_job.py",
    `  --generation-job-id ${jobId}`,
    "  --webhook-url https://your-hooks.example/enhancor/audio-fixer",
    "  --submit --confirm",
  ].join(" \\\n");
  const paidUnlocked = acknowledged && typed.trim() === TYPED_PHRASE;

  return (
    <div className="space-y-4">
      <div>
        <div className="text-sm font-semibold">Manual Audio Fixer</div>
        <p className="mt-1 text-xs text-[color:var(--color-ink-muted)]">
          The raw video already carries a native AAC audio track. Only run
          Audio Fixer if the raw audio needs improvement — for the Pai
          720p reference take the native audio was usable. Audio Fixer is
          never auto-triggered.
        </p>
      </div>

      <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 text-xs space-y-1">
        <Line label="Provider" value="enhancor_audio_fixer" />
        <Line
          label="Estimated extra cost"
          value={`${audioFixerEstimateCredits.toLocaleString("en-US")} cr (Pai 15s reference)`}
        />
        <Line label="Phase 1I default" value="dry-run command only" />
      </div>

      <CopyableCommand
        label="1. Dry-run (safe, no API call)"
        command={dryRunCmd}
      />

      <div className="space-y-3 rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/5 p-3">
        <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-danger)] font-semibold">
          ⚠ Paid Audio Fixer (do not run in Phase 1I)
        </div>
        <p className="text-xs leading-relaxed">
          Only run if raw audio needs improvement. Phase 1I dashboard
          surfaces the command but does not enable it for an actual run;
          treat the unlocked Copy button as a draft only — paste, review,
          and only execute after an explicit operator approval moment.
        </p>
        <label className="flex items-start gap-2 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            I understand this would spend ≈
            {audioFixerEstimateCredits.toLocaleString("en-US")} extra credits.
          </span>
        </label>
        <label className="block text-xs space-y-1">
          <span>
            Type <code className="font-mono font-semibold">{TYPED_PHRASE}</code>{" "}
            to unlock the Copy button:
          </span>
          <input
            type="text"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            disabled={!acknowledged}
            placeholder={TYPED_PHRASE}
            className="w-full h-9 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm font-mono outline-none focus:border-[color:var(--color-accent)]"
            spellCheck={false}
            autoComplete="off"
          />
        </label>
        <CopyableCommand
          label="2. Submit Audio Fixer (PAID — Phase 1I forbids running this)"
          command={paidCmd}
          intent="danger"
          disabled={!paidUnlocked}
          disabledReason={
            !acknowledged
              ? "Tick the acknowledgement first."
              : `Type ${TYPED_PHRASE} to unlock.`
          }
        />
      </div>

      <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        Audio Fixer remains manual after Phase 1I. The script's --submit
        path exists for future use but is not exercised by Phase 1I; the
        recommended action when the raw audio is already good is to skip
        the Fixer entirely.
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
