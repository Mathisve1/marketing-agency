"use client";

// Phase 4D1 — operator panel that calls
// approveCreativeBriefInternalAction (or reset). Internal-only.
// Mirrors the copy-approval panel shape. NO client share, NO export.

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  approveCreativeBriefInternalAction,
  resetCreativeBriefApprovalAction,
} from "@/lib/actions/social-creative-brief-approval";

interface Props {
  contentItemId: string;
  currentStatus: "none" | "approved_internal";
  approvedAt: string | null;
}

export function CreativeBriefApprovalPanel({
  contentItemId,
  currentStatus,
  approvedAt,
}: Props) {
  const [open, setOpen] = React.useState(false);
  const [notes, setNotes] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [flash, setFlash] = React.useState<
    { kind: "ok"; message: string } | { kind: "err"; error: string } | null
  >(null);

  function onApprove() {
    setFlash(null);
    startTransition(async () => {
      const r = await approveCreativeBriefInternalAction({
        contentItemId,
        notes: notes.trim() || undefined,
      });
      setFlash(
        r.ok
          ? { kind: "ok", message: r.message ?? "Approved." }
          : { kind: "err", error: r.error ?? "Could not approve." },
      );
    });
  }

  function onReset() {
    setFlash(null);
    startTransition(async () => {
      const r = await resetCreativeBriefApprovalAction({ contentItemId });
      setFlash(
        r.ok
          ? { kind: "ok", message: r.message ?? "Reset." }
          : { kind: "err", error: r.error ?? "Could not reset." },
      );
    });
  }

  if (currentStatus === "approved_internal" && !open) {
    return (
      <div className="rounded-md border border-[color:var(--color-success)]/40 bg-[color:var(--color-success)]/8 px-3 py-2 flex flex-wrap items-center gap-3">
        <div className="text-xs">
          <div className="font-semibold text-[color:var(--color-success)]">
            Approved internal
          </div>
          <div className="text-[color:var(--color-ink-muted)]">
            {approvedAt
              ? new Date(approvedAt).toLocaleString("en-GB")
              : "(no timestamp)"}{" "}
            · internal only — not shared with the client.
          </div>
        </div>
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
          Re-approve / clear
        </Button>
      </div>
    );
  }

  if (!open) {
    return (
      <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
        Approve internal
      </Button>
    );
  }

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-3 max-w-2xl">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">
            Internal creative brief approval
          </div>
          <div className="text-[11px] text-[color:var(--color-ink-muted)]">
            Records a sign-off on this brief. Internal only — does NOT
            share with the client, does NOT export, does NOT publish.
          </div>
        </div>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>

      <label className="block">
        <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Approval notes (optional, ≤500 chars)
        </span>
        <Textarea
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value.slice(0, 500))}
          placeholder="e.g. carousel slides 1+5 strong; reshoot slide 3 product close-up"
          className="mt-1.5"
        />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" onClick={onApprove} disabled={pending}>
          {pending ? "Saving…" : "Approve internal"}
        </Button>
        {currentStatus === "approved_internal" && (
          <Button size="sm" variant="danger" onClick={onReset} disabled={pending}>
            {pending ? "…" : "Clear approval"}
          </Button>
        )}
        <span className="text-[11px] text-[color:var(--color-ink-faint)] italic">
          Approval lives in <code className="font-mono">prompt_summary</code> —
          invisible to the client.
        </span>
      </div>

      {flash?.kind === "err" && (
        <div className="text-sm rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/30 px-3 py-2">
          {flash.error}
        </div>
      )}
      {flash?.kind === "ok" && (
        <div className="text-sm rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-3 py-2">
          {flash.message}
        </div>
      )}
    </div>
  );
}
