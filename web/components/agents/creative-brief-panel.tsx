"use client";

// Phase 4A — operator panel that calls the Social Creative Brief Agent
// and previews the resulting structured brief inline. Internal-only:
// the brief is NEVER shown in /client/* (the client portal view does
// not project prompt_summary).

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { createSocialCreativeBriefAction } from "@/lib/actions/social-creative-brief";

interface Props {
  contentItemId: string;
  /** "none" / "drafted" — drives button label only. */
  currentStatus: "none" | "drafted";
}

export function CreativeBriefPanel({ contentItemId, currentStatus }: Props) {
  const [open, setOpen] = React.useState(false);
  const [notes, setNotes] = React.useState("");
  const [pending, startTransition] = React.useTransition();
  const [flash, setFlash] = React.useState<
    | { kind: "ok"; message: string; markdown: string; mode: string }
    | { kind: "err"; error: string }
    | null
  >(null);

  function onCreate() {
    setFlash(null);
    startTransition(async () => {
      const r = await createSocialCreativeBriefAction({
        contentItemId,
        operatorNotes: notes.trim() || undefined,
      });
      if (r.ok) {
        setFlash({
          kind: "ok",
          message: r.message ?? "Creative brief drafted.",
          markdown: r.markdown ?? "",
          mode: r.mode ?? "",
        });
      } else {
        setFlash({ kind: "err", error: r.error ?? "Could not draft brief." });
      }
    });
  }

  if (!open) {
    return (
      <Button
        size="sm"
        variant={currentStatus === "drafted" ? "ghost" : "primary"}
        onClick={() => setOpen(true)}
      >
        {currentStatus === "drafted"
          ? "Re-draft creative brief"
          : "Create creative brief"}
      </Button>
    );
  }

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-3 max-w-3xl">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">Social Creative Brief Agent</div>
          <div className="text-[11px] text-[color:var(--color-ink-muted)]">
            Generates a structured <b>planning brief</b> only — no image, no
            video, no paid call, no share with the client.
          </div>
        </div>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>

      <label className="block">
        <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Optional operator note (visible in the brief, ≤500 chars)
        </span>
        <Textarea
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value.slice(0, 500))}
          placeholder="e.g. lean into the rosehip ingredient; keep slide 1 punchy"
          className="mt-1.5"
        />
      </label>

      <div className="flex items-center gap-2">
        <Button size="sm" variant="primary" onClick={onCreate} disabled={pending}>
          {pending
            ? "Drafting…"
            : currentStatus === "drafted"
              ? "Re-draft brief"
              : "Draft creative brief"}
        </Button>
        <span className="text-[11px] text-[color:var(--color-ink-faint)] italic">
          This creates a planning brief only. It does not generate images or
          publish anything.
        </span>
      </div>

      {flash?.kind === "err" && (
        <div className="text-sm rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/30 px-3 py-2">
          {flash.error}
        </div>
      )}
      {flash?.kind === "ok" && (
        <div className="space-y-2">
          <div className="text-xs rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-3 py-2">
            {flash.message} · mode: <code className="font-mono">{flash.mode}</code>
          </div>
          <details className="rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)]">
            <summary className="cursor-pointer px-3 py-2 text-xs font-semibold">
              Preview brief (internal — not shown to the client)
            </summary>
            <pre className="px-3 py-2 text-[11px] whitespace-pre-wrap font-mono text-[color:var(--color-ink)] overflow-x-auto">
              {flash.markdown}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}
