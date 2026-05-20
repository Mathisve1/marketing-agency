"use client";

// Phase 4F — operator panel that captures the internal QA checklist
// pass/fail decisions and saves them via
// saveCreativePreviewQAAction. Internal-only; lives in prompt_summary.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  resetCreativePreviewQAAction,
  saveCreativePreviewQAAction,
} from "@/lib/actions/creative-preview-qa";

interface Item {
  id: string;
  label: string;
}

interface Props {
  contentItemId: string;
  items: Item[];
  initialDecisions: Record<string, "pass" | "fail">;
  initialStatus: "none" | "passed" | "needs_attention";
  initialCheckedAt: string | null;
}

export function CreativePreviewQAPanel({
  contentItemId,
  items,
  initialDecisions,
  initialStatus,
  initialCheckedAt,
}: Props) {
  const [decisions, setDecisions] = React.useState<
    Record<string, "pass" | "fail">
  >({ ...initialDecisions });
  const [pending, startTransition] = React.useTransition();
  const [flash, setFlash] = React.useState<
    | { kind: "ok"; message: string; qaStatus: "passed" | "needs_attention" }
    | { kind: "err"; error: string }
    | null
  >(null);

  function toggle(id: string, value: "pass" | "fail") {
    setDecisions((prev) => {
      if (prev[id] === value) {
        const next = { ...prev };
        delete next[id];
        return next;
      }
      return { ...prev, [id]: value };
    });
  }

  function onSave() {
    setFlash(null);
    startTransition(async () => {
      const r = await saveCreativePreviewQAAction({
        contentItemId,
        items: decisions,
      });
      setFlash(
        r.ok
          ? {
              kind: "ok",
              message: r.message ?? "Saved.",
              qaStatus: r.qaStatus ?? "needs_attention",
            }
          : { kind: "err", error: r.error ?? "Could not save." },
      );
    });
  }

  function onReset() {
    setFlash(null);
    startTransition(async () => {
      const r = await resetCreativePreviewQAAction({ contentItemId });
      if (r.ok) {
        setDecisions({});
        setFlash({ kind: "ok", message: r.message ?? "Cleared.", qaStatus: "needs_attention" });
      } else {
        setFlash({ kind: "err", error: r.error ?? "Could not reset." });
      }
    });
  }

  const dirty = JSON.stringify(decisions) !== JSON.stringify(initialDecisions);

  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Internal QA checklist
        </div>
        {initialStatus === "passed" && <Badge tone="success">passed</Badge>}
        {initialStatus === "needs_attention" && (
          <Badge tone="warn">needs attention</Badge>
        )}
        {initialStatus === "none" && <Badge tone="neutral">not checked</Badge>}
      </div>
      <ul className="space-y-1">
        {items.map((it) => {
          const current = decisions[it.id] ?? null;
          return (
            <li
              key={it.id}
              className="flex items-center justify-between gap-2 text-xs"
            >
              <span className="text-[color:var(--color-ink-muted)] leading-snug">
                {it.label}
              </span>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  onClick={() => toggle(it.id, "pass")}
                  disabled={pending}
                  className={
                    "text-[10px] px-2 py-0.5 rounded-md border " +
                    (current === "pass"
                      ? "bg-[color:var(--color-success)]/15 border-[color:var(--color-success)]/40 text-[color:var(--color-success)] font-semibold"
                      : "border-[color:var(--color-hairline)] text-[color:var(--color-ink-muted)] hover:bg-[color:var(--color-hairline)]")
                  }
                >
                  pass
                </button>
                <button
                  type="button"
                  onClick={() => toggle(it.id, "fail")}
                  disabled={pending}
                  className={
                    "text-[10px] px-2 py-0.5 rounded-md border " +
                    (current === "fail"
                      ? "bg-[color:var(--color-danger)]/15 border-[color:var(--color-danger)]/40 text-[color:var(--color-danger)] font-semibold"
                      : "border-[color:var(--color-hairline)] text-[color:var(--color-ink-muted)] hover:bg-[color:var(--color-hairline)]")
                  }
                >
                  fail
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      <div className="flex items-center justify-between gap-2 pt-1">
        <div className="text-[10px] text-[color:var(--color-ink-faint)]">
          {initialCheckedAt
            ? `Last checked ${new Date(initialCheckedAt).toLocaleString("en-GB")}`
            : "Not yet saved."}
        </div>
        <div className="flex items-center gap-1.5">
          {initialStatus !== "none" && (
            <Button size="sm" variant="ghost" onClick={onReset} disabled={pending}>
              Clear
            </Button>
          )}
          <Button
            size="sm"
            variant="primary"
            onClick={onSave}
            disabled={pending || Object.keys(decisions).length === 0 || !dirty}
            title={
              !dirty
                ? "No changes since last save"
                : Object.keys(decisions).length === 0
                  ? "Pick at least one pass/fail"
                  : ""
            }
          >
            {pending ? "Saving…" : "Save QA"}
          </Button>
        </div>
      </div>
      {flash?.kind === "err" && (
        <div className="text-xs rounded-md bg-[color:var(--color-danger)]/10 border border-[color:var(--color-danger)]/30 px-2 py-1.5">
          {flash.error}
        </div>
      )}
      {flash?.kind === "ok" && (
        <div className="text-xs rounded-md bg-[color:var(--color-success)]/10 border border-[color:var(--color-success)]/30 px-2 py-1.5">
          {flash.message} · status:{" "}
          <span className="font-semibold">{flash.qaStatus}</span>
        </div>
      )}
      <div className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        QA lives in <code className="font-mono">prompt_summary</code> —
        invisible to the client. No image generation, no export, no share.
      </div>
    </div>
  );
}
