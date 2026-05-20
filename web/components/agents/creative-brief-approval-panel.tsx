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
  /** Phase 4F — manifest export-readiness summary surfaced inline so
   *  the operator sees blockers right next to the approve button.
   *  Approval is informational w.r.t. these blockers (we never block
   *  approval) — it just clarifies what "approved internal" means in
   *  this phase. */
  exportReadiness?: "ready" | "not_ready" | null;
  blockers?: string[];
}

export function CreativeBriefApprovalPanel({
  contentItemId,
  currentStatus,
  approvedAt,
  exportReadiness = null,
  blockers = [],
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
      <div className="space-y-2">
        <div className="rounded-md border border-[color:var(--color-success)]/40 bg-[color:var(--color-success)]/8 px-3 py-2 flex flex-wrap items-center gap-3">
          <div className="text-xs">
            <div className="font-semibold text-[color:var(--color-success)]">
              Approved internal
            </div>
            <div className="text-[color:var(--color-ink-muted)]">
              {approvedAt
                ? new Date(approvedAt).toLocaleString("en-GB")
                : "(no timestamp)"}{" "}
              · means "ready for future export" — NOT shared with the
              client.
            </div>
          </div>
          <div className="flex-1" />
          <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
            Re-approve / clear
          </Button>
        </div>
        <ManifestSummary
          exportReadiness={exportReadiness}
          blockers={blockers}
        />
      </div>
    );
  }

  if (!open) {
    return (
      <div className="space-y-2">
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
          Approve internal
        </Button>
        <ManifestSummary
          exportReadiness={exportReadiness}
          blockers={blockers}
        />
      </div>
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

      <div className="text-[11px] text-[color:var(--color-ink-faint)] italic">
        "Approved internal" means <strong>ready for future export</strong>{" "}
        — NOT shared with the client. Client sharing arrives with the
        Phase 4F migration (<code className="font-mono">client_safe_visual_url</code>).
      </div>

      <ManifestSummary
        exportReadiness={exportReadiness}
        blockers={blockers}
      />
    </div>
  );
}

/** Compact, read-only summary of the export manifest's blockers /
 *  readiness, shown next to the approval controls so the operator
 *  sees what still has to be resolved. Approval is NOT blocked by
 *  these — they're informational. */
function ManifestSummary({
  exportReadiness,
  blockers,
}: {
  exportReadiness: "ready" | "not_ready" | null;
  blockers: string[];
}) {
  if (exportReadiness === null) return null;
  if (exportReadiness === "ready") {
    return (
      <div className="text-[11px] rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/8 px-2 py-1.5">
        <span className="font-semibold text-[color:var(--color-success)]">
          export ready
        </span>{" "}
        — manifest has zero blockers. The local export script (Phase 4D)
        will accept this once it ships.
      </div>
    );
  }
  return (
    <div className="text-[11px] rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/8 px-2 py-1.5">
      <div className="font-semibold text-[color:var(--color-warn)]">
        export not ready
      </div>
      {blockers.length > 0 ? (
        <ul className="mt-1 space-y-0.5 text-[color:var(--color-ink-muted)]">
          {blockers.slice(0, 4).map((b) => (
            <li key={b}>· {b}</li>
          ))}
          {blockers.length > 4 && (
            <li className="italic">+ {blockers.length - 4} more (see manifest panel)</li>
          )}
        </ul>
      ) : (
        <div className="text-[color:var(--color-ink-muted)] mt-0.5">
          See manifest panel for details.
        </div>
      )}
    </div>
  );
}
