"use client";

// Phase 1D — single component that handles Approve / Request changes /
// Comment for a client content item.
//
// Phase 1O: writes go through the POST /api/portal/feedback ROUTE
// HANDLER instead of invoking the server actions as RPC. Under OpenNext
// on Cloudflare Workers, Server Actions don't reliably receive the
// session cookie, so the writes silently failed auth. Route handlers DO
// get the cookie. The endpoint returns the same { ok, message?, error? }
// shape so the flash UI below is unchanged.
//
// CLIENT-SAFE BOUNDARY: this component never receives operator-only
// fields. Its props are scoped to (portalSlug, contentId, status,
// initial comments).

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  FEEDBACK_REASON_KEYS,
  FEEDBACK_REASON_LABELS,
  type FeedbackEntry,
  type FeedbackReason,
} from "@/lib/data/feedback";

interface FeedbackActionResult {
  ok: boolean;
  message?: string;
  error?: string;
}

/** POST to the cookie-safe route handler. `credentials: "same-origin"`
 *  ensures the Supabase session cookie rides along. Network / parse
 *  failures degrade to a friendly error in the same shape the UI
 *  already renders. */
async function postFeedback(
  payload: Record<string, unknown>,
): Promise<FeedbackActionResult> {
  try {
    const res = await fetch("/api/portal/feedback", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = (await res.json()) as FeedbackActionResult;
    if (typeof data?.ok !== "boolean") {
      return { ok: false, error: "Unexpected server response." };
    }
    return data;
  } catch {
    return {
      ok: false,
      error: "Network error — please check your connection and try again.",
    };
  }
}

interface Props {
  portalSlug: string;
  contentId: string;
  initialStatus: "ready_for_review" | "approved" | "changes_requested";
  initialComments: FeedbackEntry[];
  /** True if Supabase auth is configured. False in demo mode — we keep
   *  the UI usable but flag that nothing is sent over the wire. */
  isLive: boolean;
}

type Stage = "idle" | "requesting_changes" | "commenting";

export function ClientFeedbackPanel({
  portalSlug,
  contentId,
  initialStatus,
  initialComments,
  isLive,
}: Props) {
  const [status, setStatus] =
    React.useState<Props["initialStatus"]>(initialStatus);
  const [stage, setStage] = React.useState<Stage>("idle");
  const [reason, setReason] = React.useState<NonNullable<FeedbackReason>>("wrong_tone");
  const [changesBody, setChangesBody] = React.useState("");
  const [commentBody, setCommentBody] = React.useState("");
  const [comments, setComments] = React.useState<FeedbackEntry[]>(initialComments);
  const [flash, setFlash] = React.useState<
    { kind: "ok"; message: string } | { kind: "err"; error: string } | null
  >(null);
  const [pending, startTransition] = React.useTransition();

  const flashRef = React.useRef<HTMLDivElement | null>(null);

  function showResult(
    result: { ok: boolean; message?: string; error?: string },
    optimistic?: () => void,
  ) {
    if (result.ok) {
      setFlash({ kind: "ok", message: result.message ?? "Saved." });
      optimistic?.();
    } else {
      setFlash({ kind: "err", error: result.error ?? "Action failed." });
    }
    // Phase 1O — scroll the flash into view so a failed write isn't
    // missed below the fold on mobile.
    requestAnimationFrame(() => {
      flashRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  function onApprove() {
    setFlash(null);
    startTransition(async () => {
      const result = await postFeedback({
        action: "approve",
        portalSlug,
        contentId,
      });
      showResult(result, () => setStatus("approved"));
    });
  }

  function onSubmitChanges(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFlash(null);
    const body = changesBody.trim();
    if (!body) {
      setFlash({ kind: "err", error: "Please add a short note." });
      return;
    }
    startTransition(async () => {
      const result = await postFeedback({
        action: "request_changes",
        portalSlug,
        contentId,
        reason,
        body,
      });
      showResult(result, () => {
        setStatus("changes_requested");
        setComments((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            author: "client",
            body,
            reason,
            createdAt: new Date().toISOString(),
          },
        ]);
        setChangesBody("");
        setStage("idle");
      });
    });
  }

  function onSubmitComment(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFlash(null);
    const body = commentBody.trim();
    if (!body) {
      setFlash({ kind: "err", error: "Comment cannot be empty." });
      return;
    }
    startTransition(async () => {
      const result = await postFeedback({
        action: "comment",
        portalSlug,
        contentId,
        body,
      });
      showResult(result, () => {
        setComments((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            author: "client",
            body,
            reason: null,
            createdAt: new Date().toISOString(),
          },
        ]);
        setCommentBody("");
        setStage("idle");
      });
    });
  }

  return (
    <div className="space-y-4">
      {/* Decision row */}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="success"
          size="md"
          disabled={pending || status === "approved"}
          onClick={onApprove}
        >
          {status === "approved" ? "✓ Approved" : "Approve"}
        </Button>
        <Button
          variant="secondary"
          size="md"
          disabled={pending}
          onClick={() =>
            setStage(stage === "requesting_changes" ? "idle" : "requesting_changes")
          }
        >
          {status === "changes_requested" ? "Update change request" : "Request changes"}
        </Button>
        <Button
          variant="ghost"
          size="md"
          disabled={pending}
          onClick={() => setStage(stage === "commenting" ? "idle" : "commenting")}
        >
          Add comment
        </Button>
      </div>

      {!isLive && (
        <div className="text-xs text-[color:var(--color-ink-faint)] italic">
          Demo mode — feedback is saved in memory only and resets when the
          server restarts. Configure Supabase in <code className="font-mono">web/.env.local</code>{" "}
          to persist real client decisions.
        </div>
      )}

      <div ref={flashRef}>
        {flash?.kind === "ok" && (
          <div
            role="status"
            className="text-sm rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-3 py-2"
          >
            {flash.message}
          </div>
        )}
        {flash?.kind === "err" && (
          <div
            role="alert"
            className="text-sm rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/40 px-3 py-2"
          >
            <span className="font-semibold">Couldn&rsquo;t save: </span>
            {flash.error}
          </div>
        )}
      </div>

      {/* Request-changes inline form */}
      {stage === "requesting_changes" && (
        <form
          onSubmit={onSubmitChanges}
          className="space-y-3 rounded-md border border-[color:var(--color-hairline)] bg-white p-4"
        >
          <div>
            <label className="block text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              What's wrong?
            </label>
            <select
              value={reason ?? "other"}
              onChange={(e) =>
                setReason(e.target.value as NonNullable<FeedbackReason>)
              }
              className="mt-1.5 w-full h-10 px-3 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm outline-none focus:border-[color:var(--color-accent)]"
            >
              {FEEDBACK_REASON_KEYS.map((k) => (
                <option key={k} value={k}>
                  {FEEDBACK_REASON_LABELS[k]}
                </option>
              ))}
            </select>
          </div>
          <Textarea
            value={changesBody}
            onChange={(e) => setChangesBody(e.target.value)}
            placeholder="Share what you'd like changed."
            rows={4}
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="md"
              type="button"
              onClick={() => setStage("idle")}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="md"
              type="submit"
              disabled={pending || !changesBody.trim()}
            >
              {pending ? "Sending…" : "Send change request"}
            </Button>
          </div>
        </form>
      )}

      {/* Comment inline form */}
      {stage === "commenting" && (
        <form
          onSubmit={onSubmitComment}
          className="space-y-3 rounded-md border border-[color:var(--color-hairline)] bg-white p-4"
        >
          <Textarea
            value={commentBody}
            onChange={(e) => setCommentBody(e.target.value)}
            placeholder="Anything you'd like the team to know."
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="md"
              type="button"
              onClick={() => setStage("idle")}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="md"
              type="submit"
              disabled={pending || !commentBody.trim()}
            >
              {pending ? "Sending…" : "Send comment"}
            </Button>
          </div>
        </form>
      )}

      {/* Comments thread */}
      {comments.length > 0 && (
        <div className="space-y-3">
          <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            Conversation
          </div>
          <ul className="space-y-2">
            {comments.map((c) => (
              <li
                key={c.id}
                className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 text-sm"
              >
                <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                  <span>{c.author}</span>
                  <span>·</span>
                  <span>{new Date(c.createdAt).toLocaleString("en-GB")}</span>
                  {c.reason && (
                    <>
                      <span>·</span>
                      <span>{FEEDBACK_REASON_LABELS[c.reason]}</span>
                    </>
                  )}
                </div>
                <div className="mt-1 leading-relaxed">{c.body}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
