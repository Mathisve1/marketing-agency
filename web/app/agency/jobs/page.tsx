// Phase 3F — operator-only generation jobs dashboard, **read-only**.
//
// Route: /agency/jobs
//
// Source of truth: getOwnerOverview(workspaceId) — the same composed
// snapshot the Owner Command Center reads. This replaces the Phase 1F
// path that resolved brand/campaign/content via `@/lib/demo-data`.
//
// Hard rules (Phase 3F):
//   - No paid Enhancor / Seedance / Audio Fixer call is reachable from
//     this page (no operator action buttons rendered here).
//   - No DB writes (the page is pure read).
//   - Never reachable from /client/*.

import Link from "next/link";
import { redirect } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { getCurrentPersona } from "@/lib/auth/persona";
import {
  getDataSource,
  getDefaultWorkspaceId,
} from "@/lib/data/_source";
import { getOwnerOverview } from "@/lib/data/owner-overview";
import type {
  GenerationJob,
  GenerationJobStatus,
  AudioFixerStatus,
} from "@/lib/data/generation-jobs";
import { formatCredits } from "@/lib/quality-tiers";

const STATUS_LABEL: Record<GenerationJobStatus, string> = {
  draft: "Draft",
  queued: "Queued (mock)",
  submitted: "Submitted",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

const STATUS_TONE: Record<
  GenerationJobStatus,
  "info" | "warn" | "success" | "danger" | "neutral"
> = {
  draft: "info",
  queued: "warn",
  submitted: "warn",
  processing: "warn",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
};

const QUALITY_TIER_LABEL: Record<string, string> = {
  draft_480p: "Draft · 480p",
  standard_720p: "Standard · 720p",
  premium_1080p: "Premium · 1080p",
};

type FilterKey = "all" | "draft" | "running" | "completed" | "failed";

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "All" },
  { key: "draft", label: "Draft" },
  { key: "running", label: "Running" },
  { key: "completed", label: "Completed" },
  { key: "failed", label: "Failed" },
];

const RUNNING_STATUSES = new Set<GenerationJobStatus>([
  "queued",
  "submitted",
  "processing",
]);

function matchesFilter(j: GenerationJob, key: FilterKey): boolean {
  switch (key) {
    case "all":
      return true;
    case "draft":
      return j.status === "draft";
    case "running":
      return RUNNING_STATUSES.has(j.status);
    case "completed":
      return j.status === "completed";
    case "failed":
      return j.status === "failed";
  }
}

interface PageProps {
  searchParams: Promise<{ status?: string }>;
}

export default async function GenerationJobsPage({ searchParams }: PageProps) {
  // Phase 3F workspace resolution mirrors the rest of /agency/*.
  let workspaceId = getDefaultWorkspaceId();
  if (getDataSource() === "supabase") {
    const persona = await getCurrentPersona();
    if (!persona) redirect("/login?next=/agency/jobs");
    if (persona.kind !== "operator") redirect("/login?next=/agency/jobs");
    workspaceId = persona.workspaceIds[0] ?? getDefaultWorkspaceId();
  }

  const snapshot = await getOwnerOverview(workspaceId);
  const { generationJobs, contentItems, campaigns, brands, audioFixerJobs } =
    snapshot;

  // Live lookup maps — replace the Phase 1F demo-data getters.
  const contentById = new Map(contentItems.map((c) => [c.id, c]));
  const campaignById = new Map(campaigns.map((c) => [c.id, c]));
  const brandById = new Map(brands.map((b) => [b.id, b]));

  // Latest audio-fixer status per job (audioFixerJobs are unsorted in
  // the snapshot — fall back to updatedAt to find the freshest).
  const latestAudioFixerByJob = new Map<string, AudioFixerStatus>();
  for (const a of audioFixerJobs) {
    const prev = latestAudioFixerByJob.get(a.generationJobId);
    if (!prev) {
      latestAudioFixerByJob.set(a.generationJobId, a.status);
    }
  }

  // Summary counts over the unfiltered list.
  const total = generationJobs.length;
  const draftCount = generationJobs.filter((j) => j.status === "draft").length;
  const runningCount = generationJobs.filter((j) =>
    RUNNING_STATUSES.has(j.status),
  ).length;
  const completedCount = generationJobs.filter(
    (j) => j.status === "completed",
  ).length;
  const failedCount = generationJobs.filter((j) => j.status === "failed").length;
  const totalActualCredits = generationJobs.reduce(
    (sum, j) => sum + (typeof j.actualCredits === "number" ? j.actualCredits : 0),
    0,
  );

  // Filter from ?status= (default "all").
  const sp = await searchParams;
  const requested = (sp.status ?? "all") as string;
  const activeFilter: FilterKey = FILTERS.some((f) => f.key === requested)
    ? (requested as FilterKey)
    : "all";
  const filtered = generationJobs.filter((j) => matchesFilter(j, activeFilter));

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <h1 className="text-2xl font-semibold">Generation jobs</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          This page is read-only. Paid generation only happens from explicit
          gated actions on the job detail page. No paid Enhancor / Seedance /
          Audio Fixer call is made by simply opening this list.
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Stat label="Total jobs" value={total} />
        <Stat label="Draft" value={draftCount} />
        <Stat label="Running" value={runningCount} />
        <Stat label="Completed" value={completedCount} />
        <Stat
          label="Failed"
          value={failedCount}
          tone={failedCount > 0 ? "danger" : "neutral"}
        />
        <Stat
          label="Actual credits"
          value={formatCredits(totalActualCredits)}
        />
      </div>

      {/* Filter chips */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => {
          const isActive = f.key === activeFilter;
          const href = f.key === "all" ? "/agency/jobs" : `/agency/jobs?status=${f.key}`;
          return (
            <Link
              key={f.key}
              href={href}
              className={
                isActive
                  ? "text-xs px-2.5 py-1 rounded-full bg-[color:var(--color-accent)]/15 text-[color:var(--color-accent)] font-semibold"
                  : "text-xs px-2.5 py-1 rounded-full bg-[color:var(--color-hairline)]/60 text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
              }
            >
              {f.label}
            </Link>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            Jobs{" "}
            <span className="ml-2 text-xs font-normal text-[color:var(--color-ink-faint)]">
              ({filtered.length} of {total})
            </span>
          </CardTitle>
        </CardHeader>
        <CardBody>
          {filtered.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              {total === 0
                ? "No generation jobs in this workspace yet."
                : "No jobs match this filter. Try a different one above."}
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {filtered.map((job) => {
                const content = contentById.get(job.contentItemId) ?? null;
                const campaign = content
                  ? campaignById.get(content.campaignId) ?? null
                  : null;
                const brand = campaign
                  ? brandById.get(campaign.brandId) ?? null
                  : null;
                const audioFixer = latestAudioFixerByJob.get(job.id) ?? null;
                return (
                  <JobListRow
                    key={job.id}
                    job={job}
                    contentTitle={content?.title ?? null}
                    brandName={brand?.name ?? null}
                    campaignTitle={campaign?.title ?? null}
                    audioFixerStatus={audioFixer}
                  />
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>
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
  tone?: "neutral" | "danger";
}) {
  return (
    <div className="rounded-md border border-[color:var(--color-hairline)] bg-white px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div
        className={
          tone === "danger"
            ? "text-xl font-semibold text-[color:var(--color-danger)]"
            : "text-xl font-semibold"
        }
      >
        {value}
      </div>
    </div>
  );
}

function JobListRow({
  job,
  contentTitle,
  brandName,
  campaignTitle,
  audioFixerStatus,
}: {
  job: GenerationJob;
  contentTitle: string | null;
  brandName: string | null;
  campaignTitle: string | null;
  audioFixerStatus: AudioFixerStatus | null;
}) {
  const hasResult = Boolean(job.resultUrl);
  const hasThumb = Boolean(job.thumbnailUrl);
  return (
    <li className="py-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATUS_TONE[job.status]}>{STATUS_LABEL[job.status]}</Badge>
          <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            {new Date(job.createdAt).toLocaleString("en-GB")}
          </span>
          <span className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
            · {job.provider}
            {job.providerMode ? ` · ${job.providerMode}` : ""}
          </span>
          {hasResult && (
            <Badge tone="success">result</Badge>
          )}
          {hasThumb && !hasResult && (
            <Badge tone="info">thumb</Badge>
          )}
          {audioFixerStatus && (
            <Badge tone="neutral">audio fixer: {audioFixerStatus}</Badge>
          )}
        </div>
        <div className="mt-1 font-semibold leading-snug">
          {contentTitle ?? "Unknown content item"}
        </div>
        <div className="text-xs text-[color:var(--color-ink-muted)] leading-snug">
          {brandName ?? "—"} · {campaignTitle ?? "—"}
        </div>
        <div className="mt-1 text-xs text-[color:var(--color-ink-muted)]">
          <span className="mr-3">
            {QUALITY_TIER_LABEL[job.qualityTier] ?? job.qualityTier}
          </span>
          {typeof job.durationSeconds === "number" && (
            <span className="mr-3">· {job.durationSeconds}s</span>
          )}
          <span className="mr-3">
            · est{" "}
            {typeof job.estimatedCredits === "number"
              ? formatCredits(job.estimatedCredits)
              : "—"}{" "}
            cr
          </span>
          {typeof job.actualCredits === "number" && (
            <span>· actual {formatCredits(job.actualCredits)} cr</span>
          )}
          {typeof job.clipNumber === "number" && (
            <span className="ml-3">· clip {job.clipNumber}{job.clipRole ? ` (${job.clipRole})` : ""}</span>
          )}
        </div>
        <div className="mt-1 text-[10px] text-[color:var(--color-ink-faint)] font-mono">
          job {job.id.slice(0, 8)}…
        </div>
      </div>
      <div className="flex flex-col items-end gap-1 min-w-[10rem]">
        <Link
          href={`/agency/jobs/${job.id}`}
          className="text-xs px-2.5 py-1.5 rounded-md border border-[color:var(--color-hairline)] hover:bg-[color:var(--color-hairline)]"
        >
          View job →
        </Link>
      </div>
    </li>
  );
}
