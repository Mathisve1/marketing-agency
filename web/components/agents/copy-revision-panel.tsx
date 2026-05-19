"use client";

// Phase 2I — per-item panel: revise copy from client feedback.
//
// Shown ONLY when the content item is in `changes_requested_by_client`
// or has an open regeneration_request. Operator clicks "Revise from
// client feedback" → server action threads the feedback into the Copy
// Draft Agent, rewrites caption_draft + [copy draft] block, strips any
// prior [copy approval] / [client copy preview] blocks, bumps status
// back to `draft` + `shared_with_client=false`, and best-effort marks
// the regeneration_request `accepted`. Internal approval + client
// preview must run again before anything reaches the client. No email,
// no publishing, no generation job.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  reviseCopyDraftFromClientFeedbackAction,
  type ReviseCopyFromFeedbackResult,
} from "@/lib/actions/copy-draft";

interface Props {
  contentItemId: string;
  contentStatus: string;
  latestClientFeedback: {
    id: string;
    body: string;
    reason: string | null;
    createdAt: string;
  } | null;
  openRegenerationRequest: {
    id: string;
    body: string;
    reason: string | null;
    createdAt: string;
  } | null;
}

export function CopyRevisionPanel({
  contentItemId,
  contentStatus,
  latestClientFeedback,
  openRegenerationRequest,
}: Props) {
  const [open, setOpen] = React.useState(false);
  const [notes, setNotes] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [res, setRes] = React.useState<ReviseCopyFromFeedbackResult | null>(
    null,
  );

  // Surface the panel only when there is something to revise.
  const hasOpenRequest =
    contentStatus === "changes_requested_by_client" ||
    openRegenerationRequest !== null ||
    latestClientFeedback !== null;
  if (!hasOpenRequest) return null;

  const primarySource = openRegenerationRequest ?? latestClientFeedback;

  function onCreate() {
    setRes(null);
    startTransition(async () => {
      const r = await reviseCopyDraftFromClientFeedbackAction({
        contentItemId,
        operatorNotes: notes.trim() || undefined,
      });
      setRes(r);
    });
  }

  return (
    <div className="rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/10 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="warn">revise from client feedback</Badge>
        <span className="text-[10px] text-[color:var(--color-ink-muted)]">
          internal-only · no client share · no email · no publish
        </span>
      </div>
      {primarySource && (
        <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-2 text-[11px] space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge tone="neutral">client said</Badge>
            {primarySource.reason && (
              <Badge tone="neutral">reason: {primarySource.reason}</Badge>
            )}
            <span className="text-[10px] text-[color:var(--color-ink-faint)]">
              {new Date(primarySource.createdAt).toLocaleString("en-GB")}
            </span>
          </div>
          <pre className="whitespace-pre-wrap break-words text-[11px] leading-relaxed">
            {primarySource.body}
          </pre>
        </div>
      )}
      {!open ? (
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
          Revise copy from client feedback
        </Button>
      ) : (
        <div className="space-y-2">
          <div
            role="note"
            className="rounded-md border border-[color:var(--color-hairline)] bg-white px-2 py-1.5 text-[11px] leading-relaxed text-[color:var(--color-ink-muted)]"
          >
            This rewrites <code className="font-mono">caption_draft</code>{" "}
            and strips any prior approval / client preview blocks. Status
            resets to <strong>draft</strong> and{" "}
            <code className="font-mono">shared_with_client</code> flips to{" "}
            <strong>false</strong>. Internal approval (Phase 2F) and
            client preview (Phase 2G) must run again before this item
            reaches the client.
          </div>
          <label className="block space-y-1">
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Operator note (optional)
            </span>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={500}
              placeholder="e.g. lean harder on the trust angle the client mentioned"
              className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              onClick={onCreate}
              disabled={pending}
            >
              {pending ? "Revising…" : "Revise copy"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
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
              ? "rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-2 py-1.5 text-[11px] space-y-1"
              : "rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/10 px-2 py-1.5 text-[11px]"
          }
        >
          {res.ok ? (
            <>
              <div className="font-semibold">
                {res.message ?? "Copy revised."}
              </div>
              <div className="text-[10px] text-[color:var(--color-ink-muted)]">
                status now: <strong>{res.newStatus}</strong> · approval +
                preview stripped:{" "}
                <strong>{String(res.approvalAndPreviewStripped)}</strong> ·
                regen request accepted:{" "}
                <strong>{String(res.regenerationRequestAccepted)}</strong>
              </div>
              {res.newCopyText && (
                <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[color:var(--color-hairline)] bg-white p-2 text-[10px] leading-relaxed">
                  {res.newCopyText}
                </pre>
              )}
              <p className="text-[10px] italic text-[color:var(--color-ink-faint)]">
                Re-run internal approval (Copy approval) and client
                preview (Prepare + Share) before the client sees anything.
              </p>
            </>
          ) : (
            <div>
              <span className="font-semibold">Couldn&rsquo;t revise: </span>
              {res.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
