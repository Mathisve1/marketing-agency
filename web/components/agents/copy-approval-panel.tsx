"use client";

// Phase 2F — per-item Copy Approval panel.
//
// OPERATOR-ONLY. Internal sign-off ONLY. This component does NOT send
// email, publish, share with the client, or trigger any paid call. It
// calls `approveCopyDraftAction` / `resetCopyApprovalAction`, which
// write to `content_items.caption_draft` + `prompt_summary`'s
// `[copy approval]` block and nothing else.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  approveCopyDraftAction,
  resetCopyApprovalAction,
  type CopyApprovalActionResult,
} from "@/lib/actions/copy-draft";

export interface CopyApprovalPanelProps {
  contentItemId: string;
  /** True iff a `[copy draft]` block exists for this item. Drives the
   *  "no copy yet — draft first" disabled state. */
  hasDraft: boolean;
  /** Current approval state parsed from prompt_summary. */
  approvalStatus: "none" | "approved_internal";
  /** ISO timestamp from the approval block (if present). */
  approvedAt: string | null;
  /** Optional note from the approval block. */
  approvalNotes: string | null;
  /** Full current caption_draft, used to pre-fill the optional edit
   *  textarea. */
  captionDraft: string;
}

export function CopyApprovalPanel({
  contentItemId,
  hasDraft,
  approvalStatus,
  approvedAt,
  approvalNotes,
  captionDraft,
}: CopyApprovalPanelProps) {
  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState(false);
  const [draftEdit, setDraftEdit] = React.useState(captionDraft);
  const [notes, setNotes] = React.useState(approvalNotes ?? "");
  const [pending, startTransition] = React.useTransition();
  const [res, setRes] = React.useState<CopyApprovalActionResult | null>(null);

  React.useEffect(() => {
    setDraftEdit(captionDraft);
  }, [captionDraft]);
  React.useEffect(() => {
    setNotes(approvalNotes ?? "");
  }, [approvalNotes]);

  const isApproved = approvalStatus === "approved_internal";

  function onApprove() {
    setRes(null);
    startTransition(async () => {
      const r = await approveCopyDraftAction({
        contentItemId,
        approvedCaptionDraft:
          editing && draftEdit.trim() !== captionDraft.trim()
            ? draftEdit
            : undefined,
        approvalNotes: notes.trim() || undefined,
      });
      setRes(r);
      if (r.ok) {
        setOpen(false);
        setEditing(false);
      }
    });
  }

  function onReset() {
    setRes(null);
    startTransition(async () => {
      const r = await resetCopyApprovalAction({ contentItemId });
      setRes(r);
    });
  }

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone={isApproved ? "success" : "neutral"}>
          {isApproved ? "approved internally" : "copy approval"}
        </Badge>
        {isApproved && approvedAt && (
          <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            {new Date(approvedAt).toLocaleString("en-GB")}
          </span>
        )}
        <span className="text-[10px] text-[color:var(--color-ink-muted)]">
          internal sign-off · NOT sent · NOT published · NOT shared with client
        </span>
      </div>

      {!hasDraft ? (
        <p className="text-[11px] text-[color:var(--color-ink-muted)]">
          Generate the copy draft first, then return here to review &amp;
          approve internally.
        </p>
      ) : null}

      {hasDraft && !open && !isApproved && (
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
          Review &amp; approve copy
        </Button>
      )}

      {hasDraft && isApproved && !open && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
            Re-approve / edit
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={onReset}
          >
            {pending ? "Clearing…" : "Reset approval"}
          </Button>
        </div>
      )}

      {open && (
        <div className="space-y-2">
          <div
            role="note"
            className="rounded-md border border-[color:var(--color-warn)]/30 bg-[color:var(--color-warn)]/10 px-2 py-1.5 text-[11px] leading-relaxed text-[color:var(--color-ink)]"
          >
            Approving copy here is <strong>internal only</strong>. It does
            not send an email, publish to any channel, share with the
            client, or create a generation job.
          </div>

          <div className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                Current copy (caption_draft)
              </span>
              {!editing ? (
                <button
                  type="button"
                  className="text-[11px] text-[color:var(--color-accent)] underline"
                  onClick={() => setEditing(true)}
                >
                  Edit before approving
                </button>
              ) : (
                <button
                  type="button"
                  className="text-[11px] text-[color:var(--color-ink-muted)] underline"
                  onClick={() => {
                    setEditing(false);
                    setDraftEdit(captionDraft);
                  }}
                >
                  Cancel edit
                </button>
              )}
            </div>
            {!editing ? (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-2 text-[10px] leading-relaxed">
                {captionDraft || "(no caption_draft on file)"}
              </pre>
            ) : (
              <textarea
                value={draftEdit}
                onChange={(e) => setDraftEdit(e.target.value)}
                maxLength={5000}
                rows={10}
                className="w-full rounded-md border border-[color:var(--color-hairline)] bg-white p-2 text-[11px] font-mono leading-relaxed"
              />
            )}
          </div>

          <label className="block space-y-1">
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Approval notes (optional)
            </span>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={500}
              placeholder="e.g. cleared with Anna; CTA shortened."
              className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
            />
          </label>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={pending || !hasDraft}
              onClick={onApprove}
            >
              {pending ? "Approving…" : "Approve copy internally"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setOpen(false);
                setEditing(false);
                setDraftEdit(captionDraft);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {res && (
        <div
          role="status"
          className={
            res.ok
              ? "rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-2 py-1.5 text-[11px]"
              : "rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/10 px-2 py-1.5 text-[11px]"
          }
        >
          {res.ok ? (
            <>
              <div className="font-semibold">
                {res.message ?? "Updated."}
              </div>
              {res.approvedAt && (
                <div className="text-[10px] text-[color:var(--color-ink-muted)] mt-0.5">
                  approved at {new Date(res.approvedAt).toLocaleString("en-GB")}
                </div>
              )}
            </>
          ) : (
            <div>
              <span className="font-semibold">Couldn&rsquo;t update: </span>
              {res.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
