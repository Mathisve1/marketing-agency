// Yuvo Studio — Phase 2L Claude Code task queue.
//
// OPERATOR-ONLY. Lists durable Claude Code handoff tasks the operator
// prepared from the Unified Inbox. This page does NOT execute Claude
// Code, call the Claude API, spawn a process, or run a worker. It only
// reads claude_code_tasks and offers copy-prompt + mark-ready (a status
// transition). Fail-soft: empty + a hint when migration 010 isn't
// applied yet.

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import {
  listClaudeCodeTasksForWorkspace,
  claudeCodeTasksTableReady,
} from "@/lib/data/claude-code-tasks";
import { ClaudeTaskRow } from "@/components/agents/claude-task-row";

export const dynamic = "force-dynamic";

export default async function ClaudeTasksPage() {
  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (persona?.kind === "operator" && persona.workspaceIds.length > 0) {
      workspaceId = persona.workspaceIds[0];
    }
  }

  const [tableReady, tasks] = await Promise.all([
    claudeCodeTasksTableReady(),
    listClaudeCodeTasksForWorkspace(workspaceId, { limit: 100 }),
  ]);

  const counts = {
    draft: tasks.filter((t) => t.status === "draft").length,
    ready: tasks.filter((t) => t.status === "ready_for_claude").length,
    done: tasks.filter((t) => t.status === "completed").length,
    failed: tasks.filter((t) => t.status === "failed").length,
  };

  return (
    <div className="space-y-6 max-w-5xl">
      <header>
        <h1 className="text-2xl font-semibold">Claude Code tasks</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Durable queue of operator-prepared handoff tasks. The dashboard
          <strong> only saves and re-statuses</strong> these — it never
          executes Claude Code, calls the Claude API, spawns a process,
          or runs a worker. Run Claude Code yourself; it writes results
          back to Supabase and you mark the task completed/failed.
        </p>
      </header>

      {!tableReady && (
        <Card>
          <CardHeader>
            <CardTitle>Persistence not enabled yet</CardTitle>
          </CardHeader>
          <CardBody className="text-sm text-[color:var(--color-ink-muted)] space-y-2">
            <p>
              Migration 010 (<code className="font-mono">claude_code_tasks</code>)
              is written but not applied. The Phase 2K copy-only handoff
              on <Link href="/agency/inbox" className="underline text-[color:var(--color-accent)]">/agency/inbox</Link>{" "}
              works without it. Apply{" "}
              <code className="font-mono">
                supabase/migrations/010_claude_code_tasks.sql
              </code>{" "}
              in the Supabase SQL editor to enable saving tasks here.
            </p>
          </CardBody>
        </Card>
      )}

      {tableReady && (
        <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="Draft" value={counts.draft} tone="warn" />
          <Stat label="Ready for Claude" value={counts.ready} tone="info" />
          <Stat label="Completed" value={counts.done} tone="success" />
          <Stat label="Failed" value={counts.failed} tone="danger" />
        </section>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Tasks · {tasks.length}</CardTitle>
        </CardHeader>
        <CardBody>
          {tasks.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              {tableReady
                ? "No saved tasks yet. Prepare one from the inbox and click “Save task”."
                : "No tasks (persistence disabled until migration 010)."}
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {tasks.map((t) => (
                <ClaudeTaskRow
                  key={t.id}
                  id={t.id}
                  title={t.title}
                  status={t.status}
                  riskLevel={t.riskLevel}
                  taskType={t.taskType}
                  inboxItemKind={t.inboxItemKind}
                  instructions={t.instructions}
                  safetyRules={t.safetyRules}
                  expectedOutputs={t.expectedOutputs}
                  relatedLinks={t.relatedLinks}
                  createdAt={t.createdAt}
                  completedAt={t.completedAt}
                  resultSummary={t.resultSummary}
                  errorMessage={t.errorMessage}
                />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        There is intentionally no &ldquo;Run task&rdquo; / &ldquo;Claim
        task&rdquo; button and no local worker. Execution is manual via
        your own Claude Code / MCP session.
      </p>
      <Link
        href="/agency"
        className="text-sm text-[color:var(--color-accent)] underline"
      >
        ← Owner command center
      </Link>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "neutral" | "info" | "warn" | "success" | "danger";
}) {
  const cls =
    tone === "danger"
      ? "text-[color:var(--color-danger)]"
      : tone === "warn"
        ? "text-[color:var(--color-warn)]"
        : tone === "success"
          ? "text-[color:var(--color-success)]"
          : tone === "info"
            ? "text-[color:var(--color-accent)]"
            : "text-[color:var(--color-ink)]";
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3">
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${cls}`}>
        {value}
      </div>
    </div>
  );
}
