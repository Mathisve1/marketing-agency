"use client";

// Phase 2K — per-inbox-row Claude Code task handoff (COPY-ONLY).
//
// The prepared task is computed SERVER-SIDE by the pure
// buildClaudeCodeTaskForInboxItem() and passed in as a prop. This
// component only expands a panel and copies the prompt to the
// clipboard. It NEVER executes Claude Code, calls any API, spawns a
// process, or writes to the database.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type {
  ClaudeTaskRiskLevel,
  PreparedClaudeCodeTask,
} from "@/lib/tasks/claude-code-tasks";
import {
  savePreparedClaudeCodeTaskAction,
  markClaudeCodeTaskReadyAction,
  type ClaudeCodeTaskActionResult,
} from "@/lib/actions/claude-code-tasks";

export interface ClaudeTaskSaveContext {
  inboxItemKind: string;
  inboxItemId: string;
  contentItemId: string | null;
}

const RISK_TONE: Record<
  ClaudeTaskRiskLevel,
  "neutral" | "info" | "warn" | "success" | "danger"
> = {
  read_only: "success",
  info_only: "neutral",
  draft_write: "warn",
  gated_paid: "danger",
};

export function ClaudeTaskHandoffPanel({
  task,
  riskLabel,
  saveContext,
  tableReady,
}: {
  task: PreparedClaudeCodeTask;
  riskLabel: string;
  /** Source inbox identifiers, persisted into the task's context. */
  saveContext: ClaudeTaskSaveContext;
  /** When false, migration 010 isn't applied yet — show a hint
   *  instead of the Save button. The copy-only flow is unaffected. */
  tableReady: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const [pending, startTransition] = React.useTransition();
  const [saveRes, setSaveRes] =
    React.useState<ClaudeCodeTaskActionResult | null>(null);

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(task.copyText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard blocked (insecure context / permissions). The text
      // is still visible in the <textarea> for manual select+copy.
      setCopied(false);
    }
  }

  function onSave() {
    setSaveRes(null);
    startTransition(async () => {
      const r = await savePreparedClaudeCodeTaskAction({
        inboxItemKind: saveContext.inboxItemKind,
        taskType: task.taskType,
        riskLevel: task.riskLevel,
        title: task.title,
        instructions: task.instructions,
        safetyRules: task.safetyRules,
        expectedOutputs: task.expectedOutputs,
        relatedLinks: task.relatedLinks,
        context: {
          inboxItemId: saveContext.inboxItemId,
          contentItemId: saveContext.contentItemId,
        },
      });
      setSaveRes(r);
    });
  }

  function onMarkReady() {
    if (!saveRes?.taskId) return;
    const id = saveRes.taskId;
    startTransition(async () => {
      const r = await markClaudeCodeTaskReadyAction({ taskId: id });
      setSaveRes((prev) => ({ ...(prev ?? { ok: true }), ...r }));
    });
  }

  if (!open) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        Prepare Claude Code task
      </Button>
    );
  }

  return (
    <div className="mt-1 rounded-md border border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="info">Claude Code task</Badge>
        <Badge tone={RISK_TONE[task.riskLevel]}>{riskLabel}</Badge>
        <span className="text-xs font-semibold">{task.title}</span>
      </div>

      <div
        role="note"
        className="rounded-md border border-[color:var(--color-warn)]/40 bg-[color:var(--color-warn)]/10 px-2 py-1.5 text-[11px] leading-relaxed"
      >
        This does <strong>not</strong> execute Claude Code. The dashboard
        prepares the brief only. Paste it into your own Claude Code /
        MCP session manually; results flow back via Supabase.
      </div>

      <textarea
        readOnly
        value={task.copyText}
        rows={14}
        spellCheck={false}
        onFocus={(e) => e.currentTarget.select()}
        className="w-full rounded-md border border-[color:var(--color-hairline)] bg-white p-2 font-mono text-[10px] leading-relaxed"
      />

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" onClick={onCopy}>
          {copied ? "Copied!" : "Copy prompt"}
        </Button>
        {tableReady && !saveRes?.taskId && (
          <Button
            size="sm"
            variant="secondary"
            onClick={onSave}
            disabled={pending}
          >
            {pending ? "Saving…" : "Save task"}
          </Button>
        )}
        {tableReady &&
          saveRes?.taskId &&
          saveRes.status === "draft" && (
            <Button
              size="sm"
              variant="secondary"
              onClick={onMarkReady}
              disabled={pending}
            >
              {pending ? "Updating…" : "Mark ready for Claude"}
            </Button>
          )}
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Close
        </Button>
        <span className="text-[10px] text-[color:var(--color-ink-faint)]">
          Saving a task does <strong>not</strong> execute Claude Code.
        </span>
      </div>

      {!tableReady && (
        <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
          Persistence (Save task) is off until migration 010
          (claude_code_tasks) is applied. The copy-paste handoff above
          works regardless.
        </p>
      )}

      {saveRes && (
        <div
          role="status"
          className={
            saveRes.ok
              ? "rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 px-2 py-1.5 text-[11px]"
              : "rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/10 px-2 py-1.5 text-[11px]"
          }
        >
          {saveRes.ok ? (
            <>
              <div className="font-semibold">
                {saveRes.message ?? "Saved."}
              </div>
              {saveRes.taskId && (
                <div className="font-mono break-all text-[10px]">
                  task: {saveRes.taskId} · status {saveRes.status}
                </div>
              )}
              <a
                href="/agency/claude-tasks"
                className="text-[color:var(--color-accent)] underline"
              >
                Open Claude Tasks queue →
              </a>
            </>
          ) : (
            <div>
              <span className="font-semibold">Couldn&rsquo;t save: </span>
              {saveRes.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
