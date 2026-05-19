"use client";

// Phase 2L — one saved Claude Code task row on /agency/claude-tasks.
//
// Copy the reconstructed prompt + (if draft) mark it ready. NO execute
// button. NO Claude API. NO process spawn. The dashboard never runs the
// task — the operator runs Claude Code themselves and writes results
// back to Supabase.

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  markClaudeCodeTaskReadyAction,
  completeClaudeCodeTaskAction,
  failClaudeCodeTaskAction,
  cancelClaudeCodeTaskAction,
  type ClaudeCodeTaskActionResult,
} from "@/lib/actions/claude-code-tasks";

interface Props {
  id: string;
  title: string;
  status: string;
  riskLevel: string;
  taskType: string;
  inboxItemKind: string;
  instructions: string;
  safetyRules: string[];
  expectedOutputs: string[];
  relatedLinks: string[];
  createdAt: string;
  completedAt: string | null;
  resultSummary: string | null;
  errorMessage: string | null;
}

const STATUS_TONE: Record<
  string,
  "neutral" | "info" | "warn" | "success" | "danger"
> = {
  draft: "warn",
  ready_for_claude: "info",
  in_progress: "info",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
};

export function ClaudeTaskRow(props: Props) {
  const [copied, setCopied] = React.useState(false);
  const [pending, startTransition] = React.useTransition();
  const [res, setRes] = React.useState<ClaudeCodeTaskActionResult | null>(
    null,
  );
  const [status, setStatus] = React.useState(props.status);
  const [resultSummary, setResultSummary] = React.useState(
    props.resultSummary ?? "",
  );
  const [errorMessage, setErrorMessage] = React.useState(
    props.errorMessage ?? "",
  );
  const [showComplete, setShowComplete] = React.useState(false);
  const [showFail, setShowFail] = React.useState(false);

  const copyText = [
    `# Claude Code task — ${props.title}`,
    `Task type: ${props.taskType}`,
    `Risk level: ${props.riskLevel}`,
    `Inbox kind: ${props.inboxItemKind}`,
    "",
    "## Requested action",
    props.instructions,
    "",
    "## Hard safety rules",
    ...props.safetyRules.map((r) => `- ${r}`),
    "",
    "## Expected outputs",
    ...props.expectedOutputs.map((o) => `- ${o}`),
    "",
    "## Related",
    ...props.relatedLinks.map((l) => `- ${l}`),
    "",
    "## Reporting",
    "Report what you inspected/changed and the next operator action.",
    "Halt before any paid or irreversible step.",
  ].join("\n");

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(copyText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  function onMarkReady() {
    setRes(null);
    startTransition(async () => {
      const r = await markClaudeCodeTaskReadyAction({ taskId: props.id });
      setRes(r);
      if (r.ok && r.status) setStatus(r.status);
    });
  }

  function onComplete() {
    setRes(null);
    startTransition(async () => {
      const r = await completeClaudeCodeTaskAction({
        taskId: props.id,
        resultSummary,
      });
      setRes(r);
      if (r.ok && r.status) {
        setStatus(r.status);
        setShowComplete(false);
      }
    });
  }

  function onFail() {
    setRes(null);
    startTransition(async () => {
      const r = await failClaudeCodeTaskAction({
        taskId: props.id,
        errorMessage,
      });
      setRes(r);
      if (r.ok && r.status) {
        setStatus(r.status);
        setShowFail(false);
      }
    });
  }

  function onCancel() {
    setRes(null);
    startTransition(async () => {
      const r = await cancelClaudeCodeTaskAction({ taskId: props.id });
      setRes(r);
      if (r.ok && r.status) setStatus(r.status);
    });
  }

  const isOpenForOutcome =
    status === "ready_for_claude" || status === "in_progress";

  return (
    <li className="py-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={STATUS_TONE[status] ?? "neutral"}>{status}</Badge>
        <Badge tone="neutral">{props.riskLevel}</Badge>
        <Badge tone="info">{props.taskType}</Badge>
        <span className="font-semibold truncate">{props.title}</span>
      </div>
      <div className="text-xs text-[color:var(--color-ink-muted)]">
        from {props.inboxItemKind} · created{" "}
        {new Date(props.createdAt).toLocaleString("en-GB")}
        {props.completedAt &&
          ` · completed ${new Date(props.completedAt).toLocaleString("en-GB")}`}
      </div>
      <div className="text-[11px] text-[color:var(--color-ink-faint)] line-clamp-2">
        {props.instructions}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={onCopy}>
          {copied ? "Copied!" : "Copy prompt"}
        </Button>
        {status === "draft" && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onMarkReady}
            disabled={pending}
          >
            {pending ? "Updating…" : "Mark ready for Claude"}
          </Button>
        )}
        {isOpenForOutcome && !showComplete && !showFail && (
          <>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setShowComplete(true);
                setShowFail(false);
                setRes(null);
              }}
            >
              Record completion
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setShowFail(true);
                setShowComplete(false);
                setRes(null);
              }}
            >
              Record failure
            </Button>
          </>
        )}
        {(status === "draft" || status === "ready_for_claude") && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onCancel}
            disabled={pending}
          >
            Cancel
          </Button>
        )}
        <span className="text-[10px] text-[color:var(--color-ink-faint)]">
          This only records the outcome. Run the task manually in Claude
          Code first — the dashboard never executes it.
        </span>
      </div>

      {isOpenForOutcome && showComplete && (
        <div className="rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 p-2 space-y-2">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            Result summary (paste what Claude Code reported)
          </div>
          <textarea
            value={resultSummary}
            onChange={(e) => setResultSummary(e.target.value)}
            rows={5}
            maxLength={8000}
            placeholder="What Claude Code did + the recommended next operator action."
            className="w-full rounded-md border border-[color:var(--color-hairline)] bg-white p-2 text-[11px] leading-relaxed"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              onClick={onComplete}
              disabled={pending || !resultSummary.trim()}
            >
              {pending ? "Saving…" : "Mark completed"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowComplete(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {isOpenForOutcome && showFail && (
        <div className="rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/10 p-2 space-y-2">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            Failure reason (paste the error / why it could not complete)
          </div>
          <textarea
            value={errorMessage}
            onChange={(e) => setErrorMessage(e.target.value)}
            rows={4}
            maxLength={8000}
            placeholder="What went wrong + any safe remediation."
            className="w-full rounded-md border border-[color:var(--color-hairline)] bg-white p-2 text-[11px] leading-relaxed"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="danger"
              onClick={onFail}
              disabled={pending || !errorMessage.trim()}
            >
              {pending ? "Saving…" : "Mark failed"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setShowFail(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {status === "completed" && props.resultSummary && (
        <div className="rounded-md border border-[color:var(--color-success)]/30 bg-[color:var(--color-success)]/10 p-2 text-[11px]">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            Result summary
            {props.completedAt &&
              ` · ${new Date(props.completedAt).toLocaleString("en-GB")}`}
          </div>
          <pre className="mt-1 whitespace-pre-wrap break-words">
            {props.resultSummary}
          </pre>
        </div>
      )}

      {status === "failed" && props.errorMessage && (
        <div className="rounded-md border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/10 p-2 text-[11px]">
          <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            Failure reason
            {props.completedAt &&
              ` · ${new Date(props.completedAt).toLocaleString("en-GB")}`}
          </div>
          <pre className="mt-1 whitespace-pre-wrap break-words">
            {props.errorMessage}
          </pre>
        </div>
      )}

      {res && (
        <div
          className={
            res.ok
              ? "text-[11px] text-[color:var(--color-success)]"
              : "text-[11px] text-[color:var(--color-danger)]"
          }
        >
          {res.ok ? res.message ?? "Done." : res.error}
        </div>
      )}
    </li>
  );
}
