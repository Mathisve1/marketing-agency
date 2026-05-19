"use client";

// Phase 1E — operator controls for a single open regeneration_request.
// Renders Accept + Dismiss buttons; on dismiss, the operator can leave
// a short note that flows back to the client thread.
//
// OPERATOR-ONLY. Mounted from the agency outputs page; never imported
// by the client portal.

import * as React from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  acceptRegenerationRequestAction,
  dismissRegenerationRequestAction,
} from "@/lib/actions/regeneration-requests";

interface Props {
  requestId: string;
  campaignId: string;
  contentId: string;
  status: "open" | "accepted" | "dismissed" | "fulfilled";
}

export function RegenerationRequestControls({
  requestId,
  campaignId,
  contentId,
  status,
}: Props) {
  const [stage, setStage] = React.useState<"idle" | "dismissing">("idle");
  const [note, setNote] = React.useState("");
  const [flash, setFlash] = React.useState<
    { kind: "ok"; message: string } | { kind: "err"; error: string } | null
  >(null);
  const [pending, startTransition] = React.useTransition();

  if (status !== "open" && status !== "accepted") {
    return (
      <div className="text-xs text-[color:var(--color-ink-faint)]">
        {status === "fulfilled"
          ? "Fulfilled — a new prompt version exists for this request."
          : "Dismissed."}
      </div>
    );
  }

  function onAccept() {
    setFlash(null);
    startTransition(async () => {
      const result = await acceptRegenerationRequestAction({
        requestId,
        campaignId,
        contentId,
      });
      if (result.ok) setFlash({ kind: "ok", message: result.message ?? "Accepted." });
      else setFlash({ kind: "err", error: result.error ?? "Could not accept." });
    });
  }

  function onSubmitDismiss(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFlash(null);
    startTransition(async () => {
      const result = await dismissRegenerationRequestAction({
        requestId,
        campaignId,
        contentId,
        note: note.trim() || undefined,
      });
      if (result.ok) {
        setFlash({ kind: "ok", message: result.message ?? "Dismissed." });
        setNote("");
        setStage("idle");
      } else {
        setFlash({ kind: "err", error: result.error ?? "Could not dismiss." });
      }
    });
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {status === "open" && (
          <Button
            variant="success"
            size="sm"
            disabled={pending}
            onClick={onAccept}
          >
            Accept
          </Button>
        )}
        {status === "accepted" && (
          <span className="text-xs italic text-[color:var(--color-ink-muted)] self-center">
            Accepted — open prompt editor to create a new version.
          </span>
        )}
        <Button
          variant="secondary"
          size="sm"
          disabled={pending}
          onClick={() => setStage(stage === "dismissing" ? "idle" : "dismissing")}
        >
          Dismiss
        </Button>
        <Link
          href={`/agency/campaigns/${campaignId}/content/${contentId}/prompt`}
          className="text-sm self-center underline text-[color:var(--color-accent)]"
        >
          Open prompt editor →
        </Link>
      </div>

      {flash?.kind === "ok" && (
        <div className="text-xs rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-3 py-1.5">
          {flash.message}
        </div>
      )}
      {flash?.kind === "err" && (
        <div className="text-xs rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/30 px-3 py-1.5">
          {flash.error}
        </div>
      )}

      {stage === "dismissing" && (
        <form
          onSubmit={onSubmitDismiss}
          className="space-y-2 rounded-md border border-[color:var(--color-hairline)] bg-white p-3"
        >
          <label className="block text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            Optional note back to the client
          </label>
          <Textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why we won't act on this, in client-friendly language."
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setStage("idle")}
            >
              Cancel
            </Button>
            <Button type="submit" variant="primary" size="sm" disabled={pending}>
              {pending ? "Dismissing…" : "Confirm dismiss"}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
