"use client";

// Phase 1F — operator action buttons for the job list + detail pages.
//
// OPERATOR-ONLY. None of these buttons calls Enhancor / Seedance /
// Audio Fixer. They wrap the four server actions in
// `web/lib/actions/generation-jobs.ts`, all of which are mock-only.
//
// Three components live here so the calls stay tree-shaken to the
// pages that mount them:
//   - JobRowActions       : compact "Cancel" / "Mark queued" + view link.
//   - JobCancelButton     : single danger button (used on detail page).
//   - JobMarkQueuedButton : single primary button (used on detail page).
//   - JobNoteForm         : textarea + submit for the operator timeline.

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  addGenerationJobNoteAction,
  cancelGenerationJobAction,
  markGenerationJobQueuedAction,
} from "@/lib/actions/generation-jobs";
import type { GenerationJobStatus } from "@/lib/data/generation-jobs";

interface Flash {
  kind: "ok" | "err";
  text: string;
}

function FlashBanner({ flash }: { flash: Flash | null }) {
  if (!flash) return null;
  const cls =
    flash.kind === "ok"
      ? "bg-[color:var(--color-success)]/10 border-[color:var(--color-success)]/30"
      : "bg-[color:var(--color-danger)]/10 border-[color:var(--color-danger)]/30";
  return (
    <div className={`text-sm rounded-md border px-3 py-2 ${cls}`}>{flash.text}</div>
  );
}

// ---------------------------------------------------------------------------
// JobRowActions  (used in /agency/jobs list)
// ---------------------------------------------------------------------------
export function JobRowActions({
  jobId,
  status,
}: {
  jobId: string;
  status: GenerationJobStatus;
}) {
  const [flash, setFlash] = React.useState<Flash | null>(null);
  const [pending, startTransition] = React.useTransition();

  const canCancel = status !== "completed" && status !== "cancelled";
  const canQueue = status === "draft";

  function onQueue() {
    setFlash(null);
    startTransition(async () => {
      const r = await markGenerationJobQueuedAction({ jobId });
      setFlash({ kind: r.ok ? "ok" : "err", text: r.message ?? r.error ?? "" });
    });
  }

  function onCancel() {
    setFlash(null);
    startTransition(async () => {
      const r = await cancelGenerationJobAction({ jobId });
      setFlash({ kind: r.ok ? "ok" : "err", text: r.message ?? r.error ?? "" });
    });
  }

  return (
    <div className="flex flex-col items-end gap-1 min-w-[12rem]">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          href={`/agency/jobs/${jobId}`}
          className="text-xs px-2.5 py-1.5 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)]"
        >
          View job
        </Link>
        {canQueue && (
          <Button size="sm" variant="secondary" onClick={onQueue} disabled={pending}>
            {pending ? "…" : "Mark queued (mock)"}
          </Button>
        )}
        {canCancel && (
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
            {pending ? "…" : "Cancel"}
          </Button>
        )}
      </div>
      {flash && (
        <span
          className={
            flash.kind === "ok"
              ? "text-[10px] text-[color:var(--color-success)]"
              : "text-[10px] text-[color:var(--color-danger)]"
          }
        >
          {flash.text}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// JobMarkQueuedButton  (detail page)
// ---------------------------------------------------------------------------
export function JobMarkQueuedButton({
  jobId,
  status,
}: {
  jobId: string;
  status: GenerationJobStatus;
}) {
  const [flash, setFlash] = React.useState<Flash | null>(null);
  const [pending, startTransition] = React.useTransition();
  const disabled = status !== "draft" || pending;

  function onClick() {
    setFlash(null);
    startTransition(async () => {
      const r = await markGenerationJobQueuedAction({ jobId });
      setFlash({ kind: r.ok ? "ok" : "err", text: r.message ?? r.error ?? "" });
    });
  }

  return (
    <div className="space-y-2">
      <Button variant="primary" onClick={onClick} disabled={disabled}>
        {pending ? "Submitting…" : "Mark queued (mock)"}
      </Button>
      <p className="text-xs text-[color:var(--color-ink-faint)] italic">
        Mock-only — no paid call. Phase 1G replaces this with the real Enhancor
        submission.
      </p>
      <FlashBanner flash={flash} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// JobCancelButton (detail page)
// ---------------------------------------------------------------------------
export function JobCancelButton({
  jobId,
  status,
}: {
  jobId: string;
  status: GenerationJobStatus;
}) {
  const [flash, setFlash] = React.useState<Flash | null>(null);
  const [pending, startTransition] = React.useTransition();
  const disabled =
    pending || status === "completed" || status === "cancelled";

  function onClick() {
    setFlash(null);
    startTransition(async () => {
      const r = await cancelGenerationJobAction({ jobId });
      setFlash({ kind: r.ok ? "ok" : "err", text: r.message ?? r.error ?? "" });
    });
  }

  return (
    <div className="space-y-2">
      <Button variant="danger" onClick={onClick} disabled={disabled}>
        {pending ? "Cancelling…" : "Cancel job"}
      </Button>
      <FlashBanner flash={flash} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// JobNoteForm  (detail page — append-only operator note)
// ---------------------------------------------------------------------------
export function JobNoteForm({ jobId }: { jobId: string }) {
  const [value, setValue] = React.useState("");
  const [flash, setFlash] = React.useState<Flash | null>(null);
  const [pending, startTransition] = React.useTransition();

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const message = value.trim();
    if (!message) {
      setFlash({ kind: "err", text: "Note cannot be empty." });
      return;
    }
    setFlash(null);
    startTransition(async () => {
      const r = await addGenerationJobNoteAction({ jobId, message });
      if (r.ok) setValue("");
      setFlash({ kind: r.ok ? "ok" : "err", text: r.message ?? r.error ?? "" });
    });
  }

  return (
    <form className="space-y-2" onSubmit={onSubmit}>
      <Textarea
        rows={3}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Operator note — visible to the agency, never to the client."
      />
      <div className="flex justify-end">
        <Button size="sm" variant="secondary" type="submit" disabled={pending}>
          {pending ? "Saving…" : "Add note"}
        </Button>
      </div>
      <FlashBanner flash={flash} />
    </form>
  );
}
