// Yuvo Studio — Phase 1V Owner Command Center.
//
// OPERATOR-ONLY surface. Renders the workspace-wide aggregation built
// in web/lib/data/owner-overview.ts. No paid API call, no DB write,
// no client-portal data leakage. The "Send to client" preview and the
// "Agent launcher" cards are intentionally read-only (planned / coming
// soon) — every button that would spend credits or contact a client
// stays disabled in this phase.

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DEMO_WORKSPACE,
} from "@/lib/demo-data";
import { getCurrentPersona } from "@/lib/auth/persona";
import { getDataSource, getDefaultWorkspaceId } from "@/lib/data/_source";
import {
  deriveOwnerNextActions,
  deriveRecentActivity,
  getOwnerOverview,
  nextActionLabel,
  type ActivityEntry,
  type NextAction,
} from "@/lib/data/owner-overview";
import type { Brand, Campaign, ContentItem, ContentStatus } from "@/lib/types";
import type {
  GenerationJob,
  GenerationJobStatus,
} from "@/lib/data/generation-jobs";

// ---------------------------------------------------------------------------
// Status label / tone maps (operator-side).
// ---------------------------------------------------------------------------
const CONTENT_STATUS_LABEL: Record<ContentStatus, string> = {
  draft: "Draft",
  generating: "Generating",
  raw_ready: "Raw ready",
  audio_fixer_pending: "Audio Fixer pending",
  audio_fixed: "Audio fixed",
  ready_for_client_review: "Ready for client",
  shared_with_client: "Shared",
  approved_by_client: "Client approved",
  changes_requested_by_client: "Changes requested",
  failed: "Failed",
};
const CONTENT_STATUS_TONE: Record<
  ContentStatus,
  "neutral" | "info" | "warn" | "success" | "danger"
> = {
  draft: "neutral",
  generating: "info",
  raw_ready: "warn",
  audio_fixer_pending: "warn",
  audio_fixed: "info",
  ready_for_client_review: "warn",
  shared_with_client: "info",
  approved_by_client: "success",
  changes_requested_by_client: "danger",
  failed: "danger",
};
const PIPELINE_GROUPS: Array<{
  label: string;
  statuses: ContentStatus[];
  tone: "neutral" | "info" | "warn" | "success" | "danger";
}> = [
  { label: "Draft / prompt editing", statuses: ["draft"], tone: "neutral" },
  {
    label: "Generation in flight",
    statuses: ["generating", "audio_fixer_pending"],
    tone: "info",
  },
  { label: "Raw / internal review", statuses: ["raw_ready", "audio_fixed"], tone: "warn" },
  {
    label: "Ready / shared with client",
    statuses: ["ready_for_client_review", "shared_with_client"],
    tone: "warn",
  },
  {
    label: "Changes requested",
    statuses: ["changes_requested_by_client"],
    tone: "danger",
  },
  { label: "Approved by client", statuses: ["approved_by_client"], tone: "success" },
  { label: "Failed", statuses: ["failed"], tone: "danger" },
];

const JOB_STATUS_LABEL: Record<GenerationJobStatus, string> = {
  draft: "Draft",
  queued: "Queued",
  submitted: "Submitted",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};
const JOB_STATUS_TONE: Record<
  GenerationJobStatus,
  "neutral" | "info" | "warn" | "success" | "danger"
> = {
  draft: "info",
  queued: "warn",
  submitted: "warn",
  processing: "warn",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
};

// ---------------------------------------------------------------------------
// Agent launcher — every card stays "coming soon" / disabled this phase.
// ---------------------------------------------------------------------------
const AGENTS: Array<{
  key: string;
  name: string;
  blurb: string;
  href?: string;
  state: "planned" | "linked" | "available";
}> = [
  {
    key: "lead",
    name: "Lead Research Agent",
    blurb: "Surfaces prospects from a niche + region brief.",
    state: "planned",
  },
  {
    key: "brand",
    name: "Brand Analysis + UGC Prompt Agent",
    blurb:
      "Paste a product URL → brand brief, audience, angles, scenes, prompt drafts, calendar.",
    href: "/agency/agents/brand-analysis",
    state: "available",
  },
  {
    key: "product",
    name: "Product / Website Analysis Agent",
    blurb: "Extracts product, audience, and risk signals.",
    state: "planned",
  },
  {
    key: "ugc-prompt",
    name: "UGC Prompt Agent (standalone)",
    blurb:
      "Drafts hook + script + scene-plan from a product URL. Folded into Brand Analysis for now.",
    state: "planned",
  },
  {
    key: "gen",
    name: "Generation Agent",
    blurb: "Plans a 15/20/25/30s ad and queues clip drafts.",
    href: "/agency/jobs",
    state: "linked",
  },
  {
    key: "review",
    name: "Review Agent",
    blurb: "Pulls completed clips for quick operator review.",
    href: "/agency/jobs",
    state: "linked",
  },
  {
    key: "calendar",
    name: "Content Calendar Agent",
    blurb: "Suggests next-week posts from past hits + client asks.",
    state: "planned",
  },
  {
    key: "comms",
    name: "Client Communication Agent",
    blurb: "Drafts client-facing updates from operator state.",
    state: "planned",
  },
  {
    key: "report",
    name: "Reporting Agent",
    blurb: "Weekly metrics email — saved here first, never auto-sent.",
    state: "planned",
  },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default async function AgencyHome() {
  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (persona?.kind === "operator" && persona.workspaceIds.length > 0) {
      workspaceId = persona.workspaceIds[0];
    }
  }
  const snapshot = await getOwnerOverview(workspaceId);
  const nextActions = deriveOwnerNextActions(snapshot).slice(0, 12);
  const activity = deriveRecentActivity(snapshot, 14);

  const {
    brands,
    campaigns,
    contentItems,
    generationJobs,
    regenerationRequests,
    statusCounts,
    jobStatusCounts,
    creditsByBrandId,
    totalCreditsActual,
  } = snapshot;

  // ---- Stat-row aggregates ----
  const openRegens = regenerationRequests.filter((r) => r.status === "open")
    .length;
  const jobsRunning =
    jobStatusCounts.queued + jobStatusCounts.submitted + jobStatusCounts.processing;
  const failedJobs = jobStatusCounts.failed;
  const videosReadyToShare =
    statusCounts.raw_ready + statusCounts.audio_fixed + statusCounts.ready_for_client_review;
  const inReview = statusCounts.shared_with_client;
  const feedbackWaiting = statusCounts.changes_requested_by_client + openRegens;

  // ---- Per-brand rollups ----
  const campaignsByBrand = new Map<string, Campaign[]>();
  for (const c of campaigns) {
    if (!campaignsByBrand.has(c.brandId)) campaignsByBrand.set(c.brandId, []);
    campaignsByBrand.get(c.brandId)!.push(c);
  }
  const campaignIdToBrandId = new Map<string, string>();
  for (const c of campaigns) campaignIdToBrandId.set(c.id, c.brandId);
  const contentByBrand = new Map<string, ContentItem[]>();
  for (const ci of contentItems) {
    const bid = campaignIdToBrandId.get(ci.campaignId);
    if (!bid) continue;
    if (!contentByBrand.has(bid)) contentByBrand.set(bid, []);
    contentByBrand.get(bid)!.push(ci);
  }
  const latestSharedByBrand = new Map<string, ContentItem>();
  for (const ci of contentItems) {
    if (!ci.clientSafeVideoUrl) continue;
    const bid = campaignIdToBrandId.get(ci.campaignId);
    if (!bid) continue;
    const cur = latestSharedByBrand.get(bid);
    if (!cur || cur.scheduledFor < ci.scheduledFor) {
      latestSharedByBrand.set(bid, ci);
    }
  }
  const openRequestsByBrand = new Map<string, number>();
  for (const r of regenerationRequests) {
    if (r.status !== "open") continue;
    const ci = contentItems.find((c) => c.id === r.contentItemId);
    if (!ci) continue;
    const bid = campaignIdToBrandId.get(ci.campaignId);
    if (!bid) continue;
    openRequestsByBrand.set(bid, (openRequestsByBrand.get(bid) ?? 0) + 1);
  }
  const portalSlugByBrand = new Map<string, string>();
  for (const c of campaigns) {
    if (c.clientPortalSlug && !portalSlugByBrand.has(c.brandId)) {
      portalSlugByBrand.set(c.brandId, c.clientPortalSlug);
    }
  }

  // ---- Pipeline groups ----
  const itemsByGroup = PIPELINE_GROUPS.map((g) => ({
    ...g,
    items: contentItems.filter((c) => g.statuses.includes(c.status)),
  }));

  // ---- Costs ----
  const paidJobs = generationJobs
    .filter((j) => typeof j.actualCredits === "number" && j.actualCredits > 0)
    .slice(0, 6);
  const failedJobsList = generationJobs.filter((j) => j.status === "failed").slice(0, 4);
  const pendingEstimate = generationJobs
    .filter((j) => j.status === "draft" || j.status === "queued")
    .reduce((acc, j) => acc + (j.estimatedCredits ?? 0), 0);

  // ---- Send-to-client preview ----
  const sharedItems = contentItems
    .filter((c) => c.clientSafeVideoUrl)
    .sort((a, b) => b.scheduledFor.localeCompare(a.scheduledFor))
    .slice(0, 4);

  return (
    <div className="space-y-6 max-w-7xl">
      <header>
        <h1 className="text-2xl font-semibold">
          {DEMO_WORKSPACE.name} · Owner command center
        </h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Workspace-wide operator view. No paid call is triggered from this
          page; every &ldquo;send&rdquo; / agent button is read-only this
          phase. Costs and provider ids are operator-only and never leak
          to the client portal.
        </p>
      </header>

      {/* 1. Business overview */}
      <section>
        <SectionTitle>Business overview</SectionTitle>
        <div className="mt-2 grid grid-cols-2 md:grid-cols-4 gap-3">
          <Stat label="Active clients" value={brands.length} />
          <Stat label="Active campaigns" value={campaigns.length} />
          <Stat label="In review (shared)" value={inReview} />
          <Stat label="Jobs running" value={jobsRunning} />
          <Stat label="Failed jobs" value={failedJobs} tone={failedJobs > 0 ? "danger" : "neutral"} />
          <Stat label="Videos ready to share" value={videosReadyToShare} />
          <Stat label="Client feedback waiting" value={feedbackWaiting} tone={feedbackWaiting > 0 ? "warn" : "neutral"} />
          <Stat label="Total credits spent" value={totalCreditsActual.toLocaleString("en-US")} />
        </div>
      </section>

      {/* 2. Next actions */}
      <section>
        <Card>
          <CardHeader>
            <CardTitle>Next actions</CardTitle>
          </CardHeader>
          <CardBody>
            {nextActions.length === 0 ? (
              <p className="text-sm text-[color:var(--color-ink-muted)]">
                Inbox zero. New requests + failed jobs will appear here.
              </p>
            ) : (
              <ul className="divide-y divide-[color:var(--color-hairline)]">
                {nextActions.map((a) => (
                  <NextActionRow key={a.id} action={a} />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </section>

      {/* 3 & 4 side-by-side on wide screens */}
      <section className="grid lg:grid-cols-[1.1fr_0.9fr] gap-5">
        {/* 3. Client / brand overview */}
        <Card>
          <CardHeader>
            <CardTitle>Clients & brands</CardTitle>
          </CardHeader>
          <CardBody>
            {brands.length === 0 ? (
              <p className="text-sm text-[color:var(--color-ink-muted)]">No brands yet.</p>
            ) : (
              <ul className="divide-y divide-[color:var(--color-hairline)]">
                {brands.map((b) => (
                  <BrandRow
                    key={b.id}
                    brand={b}
                    campaignCount={(campaignsByBrand.get(b.id) ?? []).length}
                    items={contentByBrand.get(b.id) ?? []}
                    latestShared={latestSharedByBrand.get(b.id)}
                    openRequests={openRequestsByBrand.get(b.id) ?? 0}
                    portalSlug={portalSlugByBrand.get(b.id) ?? null}
                  />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        {/* 4. Content pipeline */}
        <Card>
          <CardHeader>
            <CardTitle>Content pipeline</CardTitle>
          </CardHeader>
          <CardBody>
            <ul className="space-y-2">
              {itemsByGroup.map((g) => (
                <li key={g.label} className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <Badge tone={g.tone}>{g.items.length}</Badge>
                    <span className="text-sm">{g.label}</span>
                  </div>
                  <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] truncate">
                    {g.statuses.map((s) => CONTENT_STATUS_LABEL[s]).join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
            <div className="mt-4 pt-3 border-t border-[color:var(--color-hairline)] grid grid-cols-2 gap-1.5 text-xs">
              {(Object.keys(jobStatusCounts) as GenerationJobStatus[]).map((s) => (
                <div key={s} className="flex items-center justify-between gap-2">
                  <Badge tone={JOB_STATUS_TONE[s]}>{JOB_STATUS_LABEL[s]}</Badge>
                  <span className="tabular-nums">{jobStatusCounts[s]}</span>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      </section>

      {/* 5. Agent launcher */}
      <section>
        <Card>
          <CardHeader>
            <CardTitle>Agent launcher</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-xs text-[color:var(--color-ink-muted)] mb-3">
              Most agents are planned for upcoming phases. None of these
              buttons spend credits or contact a client.
            </p>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {AGENTS.map((a) => (
                <AgentCard key={a.key} agent={a} />
              ))}
            </div>
          </CardBody>
        </Card>
      </section>

      {/* 6. Recent activity */}
      <section>
        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
          </CardHeader>
          <CardBody>
            {activity.length === 0 ? (
              <p className="text-sm text-[color:var(--color-ink-muted)]">
                No recent activity.
              </p>
            ) : (
              <ul className="divide-y divide-[color:var(--color-hairline)]">
                {activity.map((e) => (
                  <ActivityRow key={e.id} entry={e} />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </section>

      {/* 7. Costs / credits */}
      <section>
        <Card>
          <CardHeader>
            <CardTitle>Cost &amp; credits</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4 text-sm">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <Stat
                label="Total credits spent (actual)"
                value={totalCreditsActual.toLocaleString("en-US")}
              />
              <Stat
                label="Pending estimate (draft+queued)"
                value={pendingEstimate.toLocaleString("en-US")}
              />
              <Stat
                label="Failed jobs (no cost)"
                value={failedJobsList.length}
                tone={failedJobsList.length > 0 ? "danger" : "neutral"}
              />
            </div>
            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] mb-2">
                  Spend by brand
                </div>
                <ul className="text-xs space-y-1">
                  {brands.map((b) => (
                    <li key={b.id} className="flex items-center justify-between gap-2">
                      <span>{b.name}</span>
                      <span className="tabular-nums">
                        {(creditsByBrandId[b.id] ?? 0).toLocaleString("en-US")} cr
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] mb-2">
                  Recent paid jobs
                </div>
                {paidJobs.length === 0 ? (
                  <p className="text-xs text-[color:var(--color-ink-muted)]">
                    No paid jobs on file yet.
                  </p>
                ) : (
                  <ul className="text-xs space-y-1">
                    {paidJobs.map((j) => (
                      <PaidJobRow key={j.id} job={j} />
                    ))}
                  </ul>
                )}
                {failedJobsList.length > 0 && (
                  <>
                    <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] mt-3 mb-1">
                      Failed jobs (no actual credits)
                    </div>
                    <ul className="text-xs space-y-1">
                      {failedJobsList.map((j) => (
                        <li
                          key={j.id}
                          className="flex items-center justify-between gap-2"
                        >
                          <Link
                            href={`/agency/jobs/${j.id}`}
                            className="text-[color:var(--color-danger)] underline truncate"
                          >
                            {j.id.slice(0, 8)} · {j.provider}
                          </Link>
                          <span className="tabular-nums">
                            {(j.estimatedCredits ?? 0).toLocaleString("en-US")}{" "}
                            est cr
                          </span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </div>
            <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
              Audio Fixer costs are folded into per-brand spend when their
              parent jobs are attributed. Audio Fixer never runs
              automatically — credits show here only after an operator
              triggers the CLI bridge.
            </p>
          </CardBody>
        </Card>
      </section>

      {/* 8. Send-to-client preview */}
      <section>
        <Card>
          <CardHeader>
            <CardTitle>Send to client (preview)</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <p className="text-xs text-[color:var(--color-ink-muted)]">
              These are the latest items already on a client portal. The
              buttons below are deliberately disabled — no email or message
              is sent from this dashboard yet.
            </p>
            {sharedItems.length === 0 ? (
              <p className="text-sm text-[color:var(--color-ink-muted)]">
                Nothing shared yet.
              </p>
            ) : (
              <ul className="grid sm:grid-cols-2 gap-3">
                {sharedItems.map((ci) => (
                  <SharedItemCard
                    key={ci.id}
                    item={ci}
                    portalSlug={
                      portalSlugByBrand.get(
                        campaignIdToBrandId.get(ci.campaignId) ?? "",
                      ) ?? null
                    }
                  />
                ))}
              </ul>
            )}
            <div className="flex flex-wrap gap-2 pt-3 border-t border-[color:var(--color-hairline)]">
              <Button variant="secondary" disabled>
                Send email to client (coming soon)
              </Button>
              <Button variant="secondary" disabled>
                Send weekly content calendar (coming soon)
              </Button>
            </div>
          </CardBody>
        </Card>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
      {children}
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | string;
  tone?: "neutral" | "warn" | "danger";
}) {
  const valueClass =
    tone === "danger"
      ? "text-[color:var(--color-danger)]"
      : tone === "warn"
        ? "text-[color:var(--color-warn)]"
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

function NextActionRow({ action }: { action: NextAction }) {
  const priorityTone =
    action.priority === 1 ? "danger" : action.priority === 2 ? "warn" : "neutral";
  return (
    <li className="py-3 flex items-start justify-between gap-3 text-sm">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Badge tone={priorityTone}>
            {action.priority === 1 ? "Urgent" : action.priority === 2 ? "Today" : "Soon"}
          </Badge>
          <Badge tone="info">{nextActionLabel(action.kind)}</Badge>
        </div>
        <div className="mt-1 font-medium">{action.title}</div>
        <div className="mt-0.5 text-xs text-[color:var(--color-ink-muted)] truncate">
          {action.context}
        </div>
      </div>
      <Link
        href={action.href}
        className="text-sm text-[color:var(--color-accent)] underline shrink-0 mt-0.5"
      >
        Open →
      </Link>
    </li>
  );
}

function BrandRow({
  brand,
  campaignCount,
  items,
  latestShared,
  openRequests,
  portalSlug,
}: {
  brand: Brand;
  campaignCount: number;
  items: ContentItem[];
  latestShared?: ContentItem;
  openRequests: number;
  portalSlug: string | null;
}) {
  const counts: Partial<Record<ContentStatus, number>> = {};
  for (const ci of items) {
    counts[ci.status] = (counts[ci.status] ?? 0) + 1;
  }
  return (
    <li className="py-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2 justify-between">
        <Link
          href={`/agency/brands/${brand.id}`}
          className="font-semibold hover:underline"
        >
          {brand.name}
        </Link>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-[color:var(--color-ink-muted)]">
            {campaignCount} campaign{campaignCount === 1 ? "" : "s"} ·{" "}
            {items.length} item{items.length === 1 ? "" : "s"}
          </span>
          {openRequests > 0 && (
            <Badge tone="warn">{openRequests} open request{openRequests === 1 ? "" : "s"}</Badge>
          )}
          {portalSlug && (
            <Link
              href={`/client/${portalSlug}`}
              className="text-xs text-[color:var(--color-accent)] underline"
            >
              portal /{portalSlug}
            </Link>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {(Object.keys(counts) as ContentStatus[]).map((s) => (
          <Badge key={s} tone={CONTENT_STATUS_TONE[s]}>
            {CONTENT_STATUS_LABEL[s]} · {counts[s]}
          </Badge>
        ))}
      </div>
      {latestShared && (
        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          Latest shared: {latestShared.title}
        </div>
      )}
    </li>
  );
}

function AgentCard({ agent }: { agent: (typeof AGENTS)[number] }) {
  const isAvailable = agent.state === "available" && !!agent.href;
  const isLinked = agent.state === "linked" && !!agent.href;
  const badgeTone =
    isAvailable ? "success" : isLinked ? "info" : "neutral";
  const badgeLabel =
    isAvailable ? "Available" : isLinked ? "Linked" : "Planned";
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="font-semibold text-sm">{agent.name}</div>
        <Badge tone={badgeTone}>{badgeLabel}</Badge>
      </div>
      <p className="text-xs text-[color:var(--color-ink-muted)] leading-relaxed">
        {agent.blurb}
      </p>
      {(isAvailable || isLinked) && agent.href ? (
        <Link
          href={agent.href}
          className="text-xs text-[color:var(--color-accent)] underline self-start"
        >
          {isAvailable ? "Launch →" : "Open →"}
        </Link>
      ) : (
        <Button variant="ghost" size="sm" disabled className="self-start">
          Coming soon
        </Button>
      )}
    </div>
  );
}

const ACTIVITY_TONE: Record<
  ActivityEntry["kind"],
  "neutral" | "info" | "warn" | "success" | "danger"
> = {
  job_created: "info",
  job_submitted: "warn",
  job_completed: "success",
  job_failed: "danger",
  regeneration_request_opened: "warn",
  regeneration_request_accepted: "success",
  content_request_received: "info",
  asset_shared: "success",
  agent_run_completed: "info",
  agent_run_failed: "danger",
};

function ActivityRow({ entry }: { entry: ActivityEntry }) {
  return (
    <li className="py-3 flex items-start gap-3 text-sm">
      <Badge tone={ACTIVITY_TONE[entry.kind]}>{entry.title}</Badge>
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          {new Date(entry.at).toLocaleString("en-GB")}
        </div>
        <div className="mt-0.5 leading-relaxed truncate">{entry.detail}</div>
      </div>
      {entry.href && (
        <Link
          href={entry.href}
          className="text-xs text-[color:var(--color-accent)] underline shrink-0 mt-0.5"
        >
          Open →
        </Link>
      )}
    </li>
  );
}

function PaidJobRow({ job }: { job: GenerationJob }) {
  return (
    <li className="flex items-center justify-between gap-2">
      <Link
        href={`/agency/jobs/${job.id}`}
        className="text-[color:var(--color-accent)] underline truncate"
      >
        {job.id.slice(0, 8)} · {job.provider}
        {job.clipNumber ? ` · clip ${job.clipNumber}` : ""}
      </Link>
      <span className="tabular-nums">
        {(job.actualCredits ?? 0).toLocaleString("en-US")} cr
      </span>
    </li>
  );
}

function SharedItemCard({
  item,
  portalSlug,
}: {
  item: ContentItem;
  portalSlug: string | null;
}) {
  return (
    <li className="rounded-md border border-[color:var(--color-hairline)] bg-white p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone={CONTENT_STATUS_TONE[item.status]}>
          {CONTENT_STATUS_LABEL[item.status]}
        </Badge>
        <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
          {item.resolution} · {item.durationSec}s
        </span>
      </div>
      <div className="font-medium text-sm leading-snug">{item.title}</div>
      {item.clientSafePosterUrl && (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={item.clientSafePosterUrl}
          alt={item.title}
          className="w-full max-h-40 object-cover rounded-md border border-[color:var(--color-hairline)]"
        />
      )}
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-[color:var(--color-ink-muted)]">
          {new Date(item.scheduledFor).toLocaleDateString("en-GB")}
        </span>
        {portalSlug && (
          <Link
            href={`/client/${portalSlug}/content/${item.id}`}
            className="text-[color:var(--color-accent)] underline"
          >
            View on portal →
          </Link>
        )}
      </div>
    </li>
  );
}
