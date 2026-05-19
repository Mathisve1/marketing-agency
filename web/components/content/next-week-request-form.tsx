"use client";

// Phase 1E — client portal "What would you like to see next week?"
// form. Wired to createContentRequestAction in
// web/lib/actions/content-requests.ts.
//
// CLIENT-SAFE BOUNDARY: this component only knows the portal slug and
// the free-form body. No content_item ids, no operator fields.

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { createContentRequestAction } from "@/lib/actions/content-requests";

interface Props {
  portalSlug: string;
  /** True if Supabase auth is configured. False in demo mode — we keep
   *  the UI usable but flag that nothing is sent over the wire. */
  isLive: boolean;
}

export function NextWeekRequestForm({ portalSlug, isLive }: Props) {
  const [body, setBody] = React.useState("");
  const [flash, setFlash] = React.useState<
    { kind: "ok"; message: string } | { kind: "err"; error: string } | null
  >(null);
  const [pending, startTransition] = React.useTransition();

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFlash(null);
    const trimmed = body.trim();
    if (!trimmed) {
      setFlash({ kind: "err", error: "Please add a short note." });
      return;
    }
    startTransition(async () => {
      const result = await createContentRequestAction({
        portalSlug,
        body: trimmed,
      });
      if (result.ok) {
        setFlash({ kind: "ok", message: result.message ?? "Sent." });
        setBody("");
      } else {
        setFlash({ kind: "err", error: result.error ?? "Could not send." });
      }
    });
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3">
      <Textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="e.g. Can we test a 9-second cut with the rosehip ingredient up front? Or a routine-led variant?"
        rows={4}
        maxLength={2000}
      />
      {!isLive && (
        <div className="text-xs text-[color:var(--color-ink-faint)] italic">
          Demo mode — requests are saved in memory only and reset when the
          server restarts.
        </div>
      )}
      {flash?.kind === "ok" && (
        <div className="text-sm rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-3 py-2">
          {flash.message}
        </div>
      )}
      {flash?.kind === "err" && (
        <div className="text-sm rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/30 px-3 py-2">
          {flash.error}
        </div>
      )}
      <div className="flex justify-end">
        <Button
          type="submit"
          variant="primary"
          disabled={pending || !body.trim()}
        >
          {pending ? "Sending…" : "Send request"}
        </Button>
      </div>
    </form>
  );
}
