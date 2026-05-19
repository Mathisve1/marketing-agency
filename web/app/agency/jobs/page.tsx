import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { JobRowActions } from "@/components/content/generation-job-actions";
import {
  listAllGenerationJobs,
  type GenerationJob,
  type GenerationJobStatus,
} from "@/lib/data/generation-jobs";
import { getCampaign, getBrand, getContent } from "@/lib/demo-data";
import { formatCredits } from "@/lib/quality-tiers";

// Phase 1F — operator-only generation jobs dashboard.
//
// Route: /agency/jobs
//
// What this page does:
//   - Lists every generation_jobs row across the workspace, newest first.
//   - Surfaces status, brand/campaign/content item, quality tier,
//     estimated/actual credits, provider/mode, created date.
//   - Per-row buttons: View / Mark queued (mock, status=draft only) / Cancel.
//
// What this page DOES NOT do:
//   - It NEVER triggers a paid Enhancor / Seedance / Audio Fixer call.
//   - It is never reachable from /client/*.

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

export default async function GenerationJobsPage() {
  const jobs = await listAllGenerationJobs();

  return (
    <div className="space-y-6 max-w-6xl">
      <div>
        <h1 className="text-2xl font-semibold">Generation jobs</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-muted)]">
          Operator-only. Phase 1F is dry-run: every row here is a record of
          intent. No paid Enhancor / Seedance / Audio Fixer call is made by
          this page or its buttons.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            All jobs{" "}
            <span className="ml-2 text-xs font-normal text-[color:var(--color-ink-faint)]">
              ({jobs.length})
            </span>
          </CardTitle>
        </CardHeader>
        <CardBody>
          {jobs.length === 0 ? (
            <p className="text-sm text-[color:var(--color-ink-muted)]">
              No generation jobs yet. Approve a prompt version in the prompt
              editor and click &ldquo;Create mock generation job&rdquo; to
              create one.
            </p>
          ) : (
            <ul className="divide-y divide-[color:var(--color-hairline)]">
              {jobs.map((job) => (
                <JobListRow key={job.id} job={job} />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function JobListRow({ job }: { job: GenerationJob }) {
  // Demo-mode resolves brand/campaign/content via the in-memory store.
  // The supabase seed uses the SAME content_item_ids the demo seed
  // does (they're aligned in seed.sql + demo-data.ts), so this lookup
  // succeeds in both modes. Phase 1G will swap to real fetches via the
  // data layer once the supabase content_items reader is in place.
  const content = getContent(job.contentItemId);
  const campaign = content ? getCampaign(content.campaignId) : null;
  const brand = campaign ? getBrand(campaign.brandId) : null;

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
        </div>
        <div className="mt-1 font-semibold leading-snug">
          {content?.title ?? "Unknown content item"}
        </div>
        <div className="text-xs text-[color:var(--color-ink-muted)] leading-snug">
          {brand?.name ?? "—"} · {campaign?.title ?? "—"}
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
        </div>
        <div className="mt-1 text-[10px] text-[color:var(--color-ink-faint)] font-mono">
          job <Link href={`/agency/jobs/${job.id}`} className="underline">
            {job.id.slice(0, 8)}…
          </Link>
        </div>
      </div>
      <JobRowActions jobId={job.id} status={job.status} />
    </li>
  );
}
