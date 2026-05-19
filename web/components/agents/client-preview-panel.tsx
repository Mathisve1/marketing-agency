"use client";

// Phase 2G — per-item client-review preparation panel.
//
// OPERATOR-ONLY. Two explicit steps:
//   1. Prepare client preview — writes the clean preview text to
//      content_items.client_safe_copy_preview. Does NOT share with
//      client, does NOT send email, does NOT publish.
//   2. Share preview with client — flips
//      content_items.shared_with_client + status to
//      'shared_with_client'. Requires the operator to type the
//      literal string "SHARE COPY" first. Does NOT send email, does
//      NOT publish to any social platform, does NOT trigger any paid
//      call.
//
// Both actions live in web/lib/actions/copy-draft.ts. This panel is
// the ONLY surface in the UI that calls them.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  prepareClientCopyPreviewAction,
  shareCopyPreviewWithClientAction,
  type PrepareClientCopyPreviewResult,
  type ShareCopyPreviewWithClientResult,
} from "@/lib/actions/copy-draft";

export interface ClientPreviewPanelProps {
  contentItemId: string;
  /** True iff `[copy approval]` block reports `approved_internal`. */
  isApprovedInternally: boolean;
  /** Phase 2G — derived from queue reader. */
  previewStatus: "none" | "prepared" | "shared_with_client";
  /** ISO timestamp from the [client copy preview] block. */
  previewAt: string | null;
  /** Current text stored in content_items.client_safe_copy_preview. */
  previewText: string;
  /** Used to seed the textarea when no preview text exists yet. */
  captionDraft: string;
}

const SHARE_TOKEN = "SHARE COPY";

export function ClientPreviewPanel({
  contentItemId,
  isApprovedInternally,
  previewStatus,
  previewAt,
  previewText,
  captionDraft,
}: ClientPreviewPanelProps) {
  const [open, setOpen] = React.useState(false);
  const [draftPreview, setDraftPreview] = React.useState(
    previewText || captionDraft,
  );
  const [operatorNotes, setOperatorNotes] = React.useState("");
  const [shareOpen, setShareOpen] = React.useState(false);
  const [shareToken, setShareToken] = React.useState("");
  const [shareNotes, setShareNotes] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [prepRes, setPrepRes] =
    React.useState<PrepareClientCopyPreviewResult | null>(null);
  const [shareRes, setShareRes] =
    React.useState<ShareCopyPreviewWithClientResult | null>(null);

  React.useEffect(() => {
    setDraftPreview(previewText || captionDraft);
  }, [previewText, captionDraft]);

  function onPrepare() {
    setPrepRes(null);
    setShareRes(null);
    startTransition(async () => {
      const r = await prepareClientCopyPreviewAction({
        contentItemId,
        clientSafeCopyPreview: draftPreview.trim()
          ? draftPreview
          : undefined,
        operatorNotes: operatorNotes.trim() || undefined,
      });
      setPrepRes(r);
      if (r.ok) setOpen(false);
    });
  }

  function onShare() {
    setShareRes(null);
    setPrepRes(null);
    startTransition(async () => {
      const r = await shareCopyPreviewWithClientAction({
        contentItemId,
        confirmationToken: shareToken,
        operatorNotes: shareNotes.trim() || undefined,
      });
      setShareRes(r);
      if (r.ok) {
        setShareOpen(false);
        setShareToken("");
      }
    });
  }

  const isShared = previewStatus === "shared_with_client";
  const isPrepared = previewStatus === "prepared" || isShared;

  // Disabled-state explanation. The operator needs the approval gate
  // satisfied before they can even start preparing a client preview.
  if (!isApprovedInternally && previewStatus === "none") {
    return (
      <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge tone="neutral">client preview</Badge>
          <span className="text-[10px] text-[color:var(--color-ink-muted)]">
            blocked · approve copy internally first
          </span>
        </div>
        <p className="text-[11px] text-[color:var(--color-ink-muted)]">
          Once the copy is approved internally, you can prepare a clean
          client-facing preview here. Preparing does not send, publish,
          or share with the client.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge
          tone={
            isShared
              ? "success"
              : previewStatus === "prepared"
                ? "info"
                : "neutral"
          }
        >
          {isShared
            ? "shared with client"
            : previewStatus === "prepared"
              ? "preview prepared"
              : "client preview"}
        </Badge>
        {previewAt && (
          <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            {new Date(previewAt).toLocaleString("en-GB")}
          </span>
        )}
        <span className="text-[10px] text-[color:var(--color-ink-muted)]">
          {isShared
            ? "visible on client portal · NO email · NO publish"
            : "NOT shared · NO email · NO publish"}
        </span>
      </div>

      {/* Step 1: Prepare client preview */}
      {!open ? (
        <div className="flex flex-wrap items-center gap-2">
          {!isPrepared ? (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setOpen(true)}
            >
              Prepare client preview
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
              Edit preview
            </Button>
          )}
          {isPrepared && !isShared && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setShareOpen(true)}
            >
              Share preview with client
            </Button>
          )}
          {isShared && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShareOpen(true)}
            >
              Re-share / refresh
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <div
            role="note"
            className="rounded-md border border-[color:var(--color-warn)]/30 bg-[color:var(--color-warn)]/10 px-2 py-1.5 text-[11px] leading-relaxed text-[color:var(--color-ink)]"
          >
            This only prepares the client portal preview. It does{" "}
            <strong>not</strong> email, publish, or notify the client.
          </div>
          <label className="block space-y-1">
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Client-facing preview text
            </span>
            <textarea
              value={draftPreview}
              onChange={(e) => setDraftPreview(e.target.value)}
              maxLength={5000}
              rows={10}
              className="w-full rounded-md border border-[color:var(--color-hairline)] bg-white p-2 text-[11px] font-mono leading-relaxed"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Operator note (optional, internal)
            </span>
            <input
              value={operatorNotes}
              onChange={(e) => setOperatorNotes(e.target.value)}
              maxLength={500}
              className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={pending}
              onClick={onPrepare}
            >
              {pending ? "Preparing…" : "Save preview"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Step 2: Share gate with explicit confirmation */}
      {shareOpen && (
        <div className="space-y-2 rounded-md border border-[color:var(--color-warn)]/30 bg-[color:var(--color-warn)]/10 p-2">
          <div className="text-[11px] leading-relaxed">
            <strong>Sharing the preview with the client</strong> sets{" "}
            <code className="font-mono">shared_with_client = true</code>{" "}
            and flips status to{" "}
            <code className="font-mono">shared_with_client</code>. The
            client can review in the portal — but{" "}
            <strong>no email is sent and nothing is published</strong>.
            Type <code className="font-mono">{SHARE_TOKEN}</code> below to
            confirm.
          </div>
          <input
            value={shareToken}
            onChange={(e) => setShareToken(e.target.value)}
            placeholder={SHARE_TOKEN}
            className="w-full h-9 px-2 rounded-md border border-[color:var(--color-warn)] bg-white text-sm font-mono"
          />
          <input
            value={shareNotes}
            onChange={(e) => setShareNotes(e.target.value)}
            maxLength={500}
            placeholder="Optional internal note about this share"
            className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-[11px]"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={pending || shareToken.trim() !== SHARE_TOKEN}
              onClick={onShare}
            >
              {pending ? "Sharing…" : "Share with client"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setShareOpen(false);
                setShareToken("");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* Current preview text (read-only display) */}
      {previewText && !open && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-[color:var(--color-ink-muted)]">
            Show current client preview ({previewText.length} chars)
          </summary>
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-2 text-[10px] leading-relaxed">
            {previewText}
          </pre>
        </details>
      )}

      {(prepRes || shareRes) && (
        <div
          role="status"
          className={
            (prepRes?.ok ?? shareRes?.ok)
              ? "rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-2 py-1.5 text-[11px]"
              : "rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/10 px-2 py-1.5 text-[11px]"
          }
        >
          {(prepRes?.ok ?? shareRes?.ok) ? (
            <div className="font-semibold">
              {prepRes?.message ?? shareRes?.message ?? "Updated."}
            </div>
          ) : (
            <div>
              <span className="font-semibold">Couldn&rsquo;t update: </span>
              {prepRes?.error ?? shareRes?.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
