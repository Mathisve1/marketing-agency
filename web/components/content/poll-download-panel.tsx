"use client";

// Phase 1G — Poll + Download command panel.
//
// Like SubmitToEnhancorPanel, this never initiates a network call from
// the dashboard. It displays the exact commands the operator runs in
// their terminal once a job has a provider_request_id.

import * as React from "react";
import { Button } from "@/components/ui/button";

interface Props {
  jobId: string;
  /** Job status; gates which commands are shown. */
  status:
    | "draft"
    | "queued"
    | "submitted"
    | "processing"
    | "completed"
    | "failed"
    | "cancelled";
  /** Once present, the poll command is unlocked. */
  providerRequestId: string | null;
  /** Once present, the download command is unlocked. */
  resultUrl: string | null;
}

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
  intent?: "neutral" | "primary";
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
          variant={disabled ? "ghost" : intent === "primary" ? "primary" : "secondary"}
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

export function PollDownloadPanel({
  jobId,
  status,
  providerRequestId,
  resultUrl,
}: Props) {
  const pollCmd = `py -3.11 scripts/run_generation_job.py --job-id ${jobId} --poll`;
  const downloadCmd = `py -3.11 scripts/run_generation_job.py --job-id ${jobId} --download`;

  // Poll is meaningful only after submission + before terminal.
  const pollUnlocked =
    !!providerRequestId &&
    status !== "completed" &&
    status !== "failed" &&
    status !== "cancelled";

  // Download requires a completed terminal state with a result URL.
  const downloadUnlocked = status === "completed" && !!resultUrl;

  // Nothing to do yet?
  if (!providerRequestId && status !== "completed") {
    return (
      <div className="text-xs text-[color:var(--color-ink-muted)] italic">
        Once the job has been submitted from your terminal and a{" "}
        <code className="font-mono">provider_request_id</code> exists, the
        poll command will appear here. Phase 1H will sync that id back
        into the dashboard automatically.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <CopyableCommand
        label="3. Poll status"
        command={pollCmd}
        disabled={!pollUnlocked}
        disabledReason={
          !providerRequestId
            ? "No provider_request_id yet — run --submit first."
            : status === "completed"
              ? "Job already completed; nothing to poll."
              : status === "failed"
                ? "Job failed; nothing to poll."
                : "Job cancelled."
        }
      />
      <CopyableCommand
        label="4. Download result"
        command={downloadCmd}
        intent="primary"
        disabled={!downloadUnlocked}
        disabledReason={
          status !== "completed"
            ? `Status is ${status} — download is enabled only on COMPLETED.`
            : "No result_url yet — run --poll once more."
        }
      />
      <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        All three commands save artefacts under
        <code className="font-mono px-1">
          prospects/pai-skincare/production/dashboard_job_runs/&lt;job-id&gt;/
        </code>
        . Phase 1H ingests those JSON files back into{" "}
        <code className="font-mono">generation_jobs</code> +{" "}
        <code className="font-mono">generated_assets</code>.
      </p>
    </div>
  );
}
