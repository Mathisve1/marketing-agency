// Yuvo Studio — Phase 2C Operator Prompt Review Queue.
//
// OPERATOR-ONLY, READ-ONLY. Lists every workspace content item + its
// prompt-draft state with a deterministic next action. This page makes
// NO writes, NO provider calls, NO generation. Approval happens in the
// existing prompt editor (linked per row), never here.

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import {
  listPromptReviewQueueForWorkspace,
  type PromptReviewNextAction,
  type PromptReviewQueueItem,
} from "@/lib/data/owner-overview";

export const dynamic = "force-dynamic";

type FilterKey = "all" | "missing" | "review" | "approved" | "blocked";

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "missing", label: "Missing prompt" },
  { key: "review", label: "Needs review" },
  { key: "approved", label: "Approved" },
  { key: "blocked", label: "Blocked" },
];

const NEXT_ACTION_LABEL: Record<PromptReviewNextAction, string> = {
  create_prompt_draft: "Create prompt draft",
  review_prompt_draft: "Review prompt draft",
  approve_prompt_for_generation: "Approve prompt for generation",
  create_generation_job: "Create generation job",
  already_approved: "Already approved",
  blocked_or_needs_attention: "Needs attention",
  create_copy_draft: "Create copy draft",
  review_copy_draft: "Review copy",
  create_carousel_outline: "Create carousel outline",
  create_story_brief: "Create story copy/visual brief",
};

const NEXT_ACTION_TONE: Record<
  PromptReviewNextAction,
  "neutral" | "info" | "warn" | "success" | "danger"
> = {
  create_prompt_draft: "warn",
  review_prompt_draft: "info",
  approve_prompt_for_generation: "info",
  create_generation_job: "success",
  already_approved: "neutral",
  blocked_or_needs_attention: "danger",
  create_copy_draft: "warn",
  review_copy_draft: "info",
  create_carousel_outline: "info",
  create_story_brief: "info",
};

const PROMPT_STATUS_TONE: Record<
  string,
  "neutral" | "info" | "warn" | "success" | "danger"
> = {
  draft: "warn",
  operator_editing: "info",
  approved_for_generation: "success",
  superseded: "neutral",
};

function matchesFilter(
  item: PromptReviewQueueItem,
  filter: FilterKey,
): boolean {
  switch (filter) {
    case "missing":
      return item.nextAction === "create_prompt_draft";
    case "review":
      return item.nextAction === "review_prompt_draft";
    case "approved":
      return item.hasApprovedForGenerationPrompt;
    case "blocked":
      return item.nextAction === "blocked_or_needs_attention";
    default:
      return true;
  }
}

interface PageProps {
  searchParams: Promise<{ filter?: string }>;
}

export default async function PromptReviewQueuePage({
  searchParams,
}: PageProps) {
  const sp = await searchParams;
  const filter: FilterKey = (
    ["all", "missing", "review", "approved", "blocked"] as FilterKey[]
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

  const queue = await listPromptReviewQueueForWorkspace(workspaceId);
  const visible = queue.items.filter((i) => matchesFilter(i, filter));

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="text-2xl font-semibold">Prompt review queue</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Operator-only, read-only. This page never approves a prompt,
          submits a generation job, calls Seedance/Enhancor, runs Audio
          Fixer, or shares with the client. Approval happens in the
          prompt editor linked per row.
        </p>
        <p className="mt-1 text-xs text-[color:var(--color-ink-faint)]">
          Multi-format aware: items whose format is a copy/visual format
          (LinkedIn / feed post / story / carousel / email) get a
          copy-or-brief next action instead of a video prompt — not every
          content item becomes a Seedance video.
        </p>
      </header>

      {/* 1. Summary cards */}
      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryCard
          label="Need a prompt"
          value={queue.summary.missingPrompt}
          tone={queue.summary.missingPrompt > 0 ? "warn" : "neutral"}
        />
        <SummaryCard
          label="Drafts to review"
          value={queue.summary.needsReview}
          tone={queue.summary.needsReview > 0 ? "info" : "neutral"}
        />
        <SummaryCard
          label="Approved (ready)"
          value={queue.summary.approvedReady}
          tone={queue.summary.approvedReady > 0 ? "success" : "neutral"}
        />
        <SummaryCard
          label="Blocked / attention"
          value={queue.summary.blocked}
          tone={queue.summary.blocked > 0 ? "danger" : "neutral"}
        />
      </section>

      {/* 3. Filters */}
      <nav className="flex flex-wrap gap-2">
        {FILTERS.map((f) => {
          const active = f.key === filter;
          return (
            <Link
              key={f.key}
              href={`/agency/prompt-review?filter=${f.key}`}
              className={[
                "px-3 py-1.5 rounded-md text-xs font-medium border",
                active
                  ? "bg-[color:var(--color-accent)] text-[color:var(--color-cream)] border-[color:var(--color-accent)]"
                  : "bg-white text-[color:var(--color-ink)] border-[color:var(--color-hairline)] hover:bg-[color:var(--color-cream-soft)]",
              ].join(" ")}
            >
              {f.label}
            </Link>
          );
        })}
      </nav>

      {/* 2. Queue */}
      <Card>
        <CardHeader>
          <CardTitle>
            Queue · {visible.length} of {queue.summary.total} item
            {queue.summary.total === 1 ? "" : "s"}
          </CardTitle>
        </CardHeader>
        <CardBody>
          {visible.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              Nothing matches this filter.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {visible.map((it) => (
                <li
                  key={it.contentItemId}
                  className="py-3 flex items-start justify-between gap-4"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{it.title}</span>
                      <Badge tone={NEXT_ACTION_TONE[it.nextAction]}>
                        {NEXT_ACTION_LABEL[it.nextAction]}
                      </Badge>
                    </div>
                    <div className="text-xs text-[color:var(--color-ink-muted)]">
                      {it.brandName ?? "—"} ·{" "}
                      {it.campaignName ?? it.campaignId.slice(0, 8)} ·{" "}
                      scheduled{" "}
                      {it.scheduledFor
                        ? new Date(it.scheduledFor).toLocaleDateString(
                            "en-GB",
                          )
                        : "—"}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <Badge tone="neutral">{it.contentStatus}</Badge>
                      {it.latestPromptStatus ? (
                        <Badge
                          tone={
                            PROMPT_STATUS_TONE[it.latestPromptStatus] ??
                            "neutral"
                          }
                        >
                          prompt: {it.latestPromptStatus}
                        </Badge>
                      ) : (
                        <Badge tone="warn">no prompt</Badge>
                      )}
                      <span className="text-[color:var(--color-ink-faint)]">
                        {it.promptVersionCount} version
                        {it.promptVersionCount === 1 ? "" : "s"}
                      </span>
                      {it.platforms.length > 0 && (
                        <span className="text-[color:var(--color-ink-faint)]">
                          · {it.platforms.join(", ")}
                        </span>
                      )}
                    </div>
                  </div>
                  <Link
                    href={it.editorHref}
                    className="text-sm text-[color:var(--color-accent)] underline shrink-0 mt-0.5"
                  >
                    Open prompt editor →
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
        Next-action labels are advisory. Generation is only ever started
        later, behind the existing &ldquo;Mark approved for
        generation&rdquo; operator gate inside the prompt editor — this
        queue cannot trigger it.
      </p>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "neutral" | "info" | "warn" | "success" | "danger";
}) {
  const valueClass =
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
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${valueClass}`}>
        {value}
      </div>
    </div>
  );
}
