"use client";

// Phase 2E — per-item Copy Draft panel. Deterministic, operator-review
// only. Never publishes, never shares, never generates video.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  createCopyDraftForContentItemAction,
  type CopyDraftActionResult,
} from "@/lib/actions/copy-draft";

export function CopyDraftPanel({
  contentItemId,
  alreadyDrafted,
}: {
  contentItemId: string;
  alreadyDrafted: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const [tone, setTone] = React.useState("");
  const [cta, setCta] = React.useState("");
  const [notes, setNotes] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [res, setRes] = React.useState<CopyDraftActionResult | null>(null);

  function onCreate() {
    setRes(null);
    startTransition(async () => {
      const r = await createCopyDraftForContentItemAction({
        contentItemId,
        tone: tone.trim() || undefined,
        cta: cta.trim() || undefined,
        operatorNotes: notes.trim() || undefined,
      });
      setRes(r);
    });
  }

  return (
    <div className="rounded-md border border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/8 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="info">copy draft</Badge>
        <span className="text-xs font-semibold">
          {alreadyDrafted ? "Re-draft copy" : "Create copy draft"}
        </span>
        <span className="text-[10px] text-[color:var(--color-ink-muted)]">
          deterministic · no video · no publish · no client share
        </span>
      </div>
      {!open ? (
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
          {alreadyDrafted ? "Re-draft copy" : "Create copy draft"}
        </Button>
      ) : (
        <div className="space-y-2">
          <div
            role="note"
            className="rounded-md border border-[color:var(--color-hairline)] bg-white px-2 py-1.5 text-[11px] leading-relaxed text-[color:var(--color-ink-muted)]"
          >
            Writes operator-review copy into{" "}
            <code className="font-mono">caption_draft</code>. It does not
            generate a video, create a prompt version, send an email,
            publish, or share with the client.
          </div>
          <div className="grid sm:grid-cols-2 gap-2">
            <label className="block space-y-1">
              <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                Tone (optional)
              </span>
              <input
                value={tone}
                onChange={(e) => setTone(e.target.value)}
                maxLength={120}
                placeholder="e.g. warm, plain-spoken"
                className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                CTA (optional)
              </span>
              <input
                value={cta}
                onChange={(e) => setCta(e.target.value)}
                maxLength={200}
                placeholder="e.g. Reply to book a call"
                className="w-full h-9 px-2 rounded-md border border-[color:var(--color-hairline)] bg-white text-sm"
              />
            </label>
          </div>
          <label className="block space-y-1">
            <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
              Operator note (optional)
            </span>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={500}
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
              {pending ? "Drafting copy…" : "Generate copy draft"}
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
                {res.message ?? "Copy drafted."}
              </div>
              <div className="text-[10px] text-[color:var(--color-ink-muted)]">
                format: {res.format} · channel: {res.channel} · saved to
                caption_draft
              </div>
              {res.copyText && (
                <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[color:var(--color-hairline)] bg-white p-2 text-[10px] leading-relaxed">
                  {res.copyText}
                </pre>
              )}
              {res.calendarHref && (
                <a
                  href={res.calendarHref}
                  className="text-[color:var(--color-accent)] underline"
                >
                  Open campaign calendar →
                </a>
              )}
            </>
          ) : (
            <div>
              <span className="font-semibold">Couldn&rsquo;t draft: </span>
              {res.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
