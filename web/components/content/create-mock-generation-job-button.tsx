"use client";

// Phase 1F — operator button on the prompt editor route.
//
// Visible only when the prompt version is in status
// `approved_for_generation`. Clicking it creates a generation_batch +
// generation_job + "created" generation_job_event, all with mock data —
// NO Enhancor / Seedance / Audio Fixer call is made.

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { createMockGenerationJobFromPromptVersionAction } from "@/lib/actions/generation-jobs";

interface Props {
  promptVersionId: string;
  campaignId: string;
  contentId: string;
}

export function CreateMockGenerationJobButton({
  promptVersionId,
  campaignId,
  contentId,
}: Props) {
  const [pending, startTransition] = React.useTransition();
  const [result, setResult] = React.useState<
    | { kind: "ok"; jobId: string; message: string }
    | { kind: "err"; error: string }
    | null
  >(null);

  function onClick() {
    setResult(null);
    startTransition(async () => {
      const r = await createMockGenerationJobFromPromptVersionAction({
        promptVersionId,
        campaignId,
        contentId,
      });
      if (r.ok && r.jobId) {
        setResult({
          kind: "ok",
          jobId: r.jobId,
          message: r.message ?? "Mock job created.",
        });
      } else {
        setResult({ kind: "err", error: r.error ?? "Failed to create mock job." });
      }
    });
  }

  return (
    <div className="space-y-2">
      <Button variant="primary" onClick={onClick} disabled={pending}>
        {pending ? "Creating…" : "Create mock generation job"}
      </Button>
      <p className="text-xs text-[color:var(--color-ink-faint)] italic">
        Phase 1F dry-run. Records intent in <code>generation_jobs</code>; no
        paid Enhancor call is made.
      </p>
      {result?.kind === "ok" && (
        <div className="text-sm rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-3 py-2">
          {result.message}{" "}
          <Link
            href={`/agency/jobs/${result.jobId}`}
            className="underline text-[color:var(--color-accent)]"
          >
            Open job →
          </Link>
        </div>
      )}
      {result?.kind === "err" && (
        <div className="text-sm rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/30 px-3 py-2">
          {result.error}
        </div>
      )}
    </div>
  );
}
