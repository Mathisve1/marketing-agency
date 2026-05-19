// Yuvo Studio — Phase 2J Unified Content Review Inbox.
//
// OPERATOR-ONLY, READ-ONLY. Composes the existing prompt-review, copy-
// draft, owner-snapshot, agent-run, and regeneration-request queues
// into a single prioritised stream. This page issues no server
// actions, mutates no rows, calls no providers, sends no email,
// publishes nothing. Every row is a deep-link to the existing domain
// page where the actual gated work happens.

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import {
  listAgencyInboxItemsForWorkspace,
  type InboxItem,
} from "@/lib/data/owner-overview";
import {
  buildClaudeCodeTaskForInboxItem,
  CLAUDE_TASK_RISK_LABELS,
} from "@/lib/tasks/claude-code-tasks";
import { ClaudeTaskHandoffPanel } from "@/components/agents/claude-task-handoff-panel";
import { claudeCodeTasksTableReady } from "@/lib/data/claude-code-tasks";

export const dynamic = "force-dynamic";

type FilterKey =
  | "all"
  | "urgent"
  | "video"
  | "copy"
  | "prompts"
  | "client"
  | "failed";

const FILTER_TABS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "urgent", label: "Urgent" },
  { key: "video", label: "Video" },
  { key: "copy", label: "Copy" },
  { key: "prompts", label: "Prompts" },
  { key: "client", label: "Client" },
  { key: "failed", label: "Failed" },
];

function passesFilter(item: InboxItem, filter: FilterKey): boolean {
  switch (filter) {
    case "all":
      return true;
    case "urgent":
      return item.priority === 1;
    case "video":
      return item.category === "video";
    case "copy":
      return item.category === "copy";
    case "prompts":
      return item.category === "prompt";
    case "client":
      return (
        item.category === "client" ||
        item.kind === "copy_client_requested_changes" ||
        item.kind === "video_client_requested_changes" ||
        item.kind === "copy_approved_by_client" ||
        item.kind === "video_approved_by_client"
      );
    case "failed":
      return item.category === "failed";
  }
}

interface PageProps {
  searchParams: Promise<{ filter?: string }>;
}

export default async function AgencyInboxPage({ searchParams }: PageProps) {
  const sp = await searchParams;
  const filter: FilterKey = (
    [
      "all",
      "urgent",
      "video",
      "copy",
      "prompts",
      "client",
      "failed",
    ] as FilterKey[]
  ).includes(sp.filter as FilterKey)
    ? (sp.filter as FilterKey)
    : "all";

  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (persona?.kind === "operator" && persona.workspaceIds.length > 0) {
      workspaceId = persona.workspaceIds[0];
    }
  }

  const [{ items, summary }, tableReady] = await Promise.all([
    listAgencyInboxItemsForWorkspace(workspaceId),
    claudeCodeTasksTableReady(),
  ]);
  const visible = items.filter((i) => passesFilter(i, filter));

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="text-2xl font-semibold">Content review inbox</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          One operator queue across video, copy, prompts, and client
          decisions. <strong>Read-only:</strong> this page does not
          approve, submit, share, publish, send email, call any paid
          API, or mutate any database row. Every row deep-links to the
          domain page where the existing gated action lives.
        </p>
      </header>

      {/* Summary cards */}
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Urgent" value={summary.urgent} tone="danger" />
        <Stat label="Needs review" value={summary.needsReview} tone="warn" />
        <Stat
          label="Ready to share / generate"
          value={summary.readyToShareOrGenerate}
          tone="success"
        />
        <Stat
          label="Client decisions"
          value={summary.clientDecisions}
          tone="info"
        />
      </section>

      {/* Filter tabs */}
      <nav className="flex flex-wrap gap-2">
        {FILTER_TABS.map((t) => {
          const active = t.key === filter;
          const count = items.filter((i) => passesFilter(i, t.key)).length;
          return (
            <Link
              key={t.key}
              href={`/agency/inbox?filter=${t.key}`}
              className={[
                "px-3 py-1.5 rounded-md text-xs font-medium border inline-flex items-center gap-1.5",
                active
                  ? "bg-[color:var(--color-accent)] text-[color:var(--color-cream)] border-[color:var(--color-accent)]"
                  : "bg-white text-[color:var(--color-ink)] border-[color:var(--color-hairline)] hover:bg-[color:var(--color-cream-soft)]",
              ].join(" ")}
            >
              {t.label}
              <span
                className={
                  active
                    ? "text-[10px] tabular-nums opacity-80"
                    : "text-[10px] tabular-nums text-[color:var(--color-ink-faint)]"
                }
              >
                {count}
              </span>
            </Link>
          );
        })}
      </nav>

      {/* Inbox list */}
      <Card>
        <CardHeader>
          <CardTitle>
            Items · {visible.length} of {summary.total}
          </CardTitle>
        </CardHeader>
        <CardBody>
          {visible.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              Nothing matches this filter. Inbox zero is a feature.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {visible.map((it) => (
                <InboxRow
                  key={it.id}
                  item={it}
                  tableReady={tableReady}
                />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        Items are sorted by priority then by latest activity. Open a row
        to act on it in the domain page — generation submits, prompt
        approvals, copy approvals, preview prepare/share, and client
        sends remain behind their existing operator gates.
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

function InboxRow({
  item,
  tableReady,
}: {
  item: InboxItem;
  tableReady: boolean;
}) {
  const priorityLabel =
    item.priority === 1 ? "Urgent" : item.priority === 2 ? "Today" : "Soon";
  const priorityTone: InboxItem["badgeTone"] =
    item.priority === 1 ? "danger" : item.priority === 2 ? "warn" : "neutral";
  // Pure, server-side. No I/O — just a deterministic string builder.
  const task = buildClaudeCodeTaskForInboxItem(item);
  return (
    <li className="py-3 flex items-start justify-between gap-4">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={priorityTone}>{priorityLabel}</Badge>
          <Badge tone={item.badgeTone}>{item.badgeLabel}</Badge>
          <span className="font-semibold truncate">{item.title}</span>
        </div>
        <div className="text-xs text-[color:var(--color-ink-muted)]">
          {item.brandName ?? "—"} · {item.campaignName ?? "—"}
          {item.status && ` · status ${item.status}`}
          {" · "}
          {new Date(item.updatedAt).toLocaleString("en-GB")}
        </div>
        {item.description && (
          <div className="text-xs text-[color:var(--color-ink-muted)] truncate">
            {item.description}
          </div>
        )}
        <ClaudeTaskHandoffPanel
          task={task}
          riskLabel={CLAUDE_TASK_RISK_LABELS[task.riskLevel]}
          tableReady={tableReady}
          saveContext={{
            inboxItemKind: item.kind,
            inboxItemId: item.id,
            contentItemId: item.contentItemId,
          }}
        />
      </div>
      <Link
        href={item.href}
        className="text-sm text-[color:var(--color-accent)] underline shrink-0 mt-0.5"
      >
        Open →
      </Link>
    </li>
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
