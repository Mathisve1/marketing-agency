import Link from "next/link";
import { notFound } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AudioFixerCommandPanel } from "@/components/content/audio-fixer-command-panel";
import {
  JobCancelButton,
  JobMarkQueuedButton,
  JobNoteForm,
} from "@/components/content/generation-job-actions";
import { PollDownloadPanel } from "@/components/content/poll-download-panel";
import { SubmitToEnhancorPanel } from "@/components/content/submit-to-enhancor-panel";
import { ClipSubmitGatePanel } from "@/components/content/clip-submit-gate-panel";
import {
  getGenerationBatch,
  getGenerationJob,
  listAudioFixerJobsForGenerationJob,
  listGeneratedAssetsForGenerationJob,
  listGenerationJobEvents,
  listGenerationJobsForBatch,
  type GeneratedAsset,
  type GenerationJobEventType,
  type GenerationJobStatus,
  type AudioFixerStatus,
} from "@/lib/data/generation-jobs";
import { getPromptVersionById } from "@/lib/data/prompt-versions";
import { getContentApprovals, getContentFeedback } from "@/lib/data/feedback";
import { ClientFeedbackSummary } from "@/components/content/client-feedback-summary";
import { getContentItemById } from "@/lib/data/owner-overview";
import { getCampaignById } from "@/lib/data/campaigns";
import { getBrandById } from "@/lib/data/brands";
import {
  formatCredits,
  getEstimatedAudioFixerCredits,
  getEstimatedSeedanceCredits,
} from "@/lib/quality-tiers";

interface PageProps {
  params: Promise<{ jobId: string }>;
}

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

const EVENT_LABEL: Record<GenerationJobEventType, string> = {
  created: "Created",
  queued: "Queued",
  submitted: "Submitted",
  status_polled: "Status polled",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  operator_note: "Operator note",
};

const AUDIO_FIXER_STATUS_LABEL: Record<AudioFixerStatus, string> = {
  not_needed: "Not needed",
  available: "Available (manual)",
  queued: "Queued",
  submitted: "Submitted",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
  skipped_by_operator: "Skipped by operator",
};

const AUDIO_FIXER_STATUS_TONE: Record<
  AudioFixerStatus,
  "info" | "warn" | "success" | "danger" | "neutral"
> = {
  not_needed: "neutral",
  available: "info",
  queued: "warn",
  submitted: "warn",
  processing: "warn",
  completed: "success",
  failed: "danger",
  skipped_by_operator: "neutral",
};

const QUALITY_TIER_LABEL: Record<string, string> = {
  draft_480p: "Draft · 480p",
  standard_720p: "Standard · 720p",
  premium_1080p: "Premium · 1080p",
};

export default async function GenerationJobDetail({ params }: PageProps) {
  const { jobId } = await params;
  const job = await getGenerationJob(jobId);
  if (!job) notFound();

  const [
    batch,
    events,
    audioFixerJobs,
    promptVersion,
    generatedAssets,
    feedback,
    approvals,
    batchJobs,
  ] = await Promise.all([
    getGenerationBatch(job.batchId),
    listGenerationJobEvents(job.id),
    listAudioFixerJobsForGenerationJob(job.id),
    getPromptVersionById(job.promptVersionId),
    listGeneratedAssetsForGenerationJob(job.id),
    // Phase 1O — surface client feedback on this job's content item so
    // the operator looking at the completed run sees the conversation
    // without bouncing to the campaign outputs page.
    getContentFeedback(job.contentItemId),
    getContentApprovals(job.contentItemId),
    // Phase 1S — sibling clips in the same batch, for the per-clip
    // sequencing gate (clip N blocked until clip N-1 is completed).
    listGenerationJobsForBatch(job.batchId),
  ]);

  // Phase 1S — this is a multi-clip job iff clip_number is set. The
  // prior clip (clip_number - 1) gates this one.
  const isMultiClip =
    typeof job.clipNumber === "number" && job.clipNumber >= 1;
  const priorClipJob =
    isMultiClip && (job.clipNumber as number) > 1
      ? batchJobs.find(
          (j) => j.clipNumber === (job.clipNumber as number) - 1,
        ) ?? null
      : null;
  const rawVideoAsset =
    generatedAssets.find((a) => a.kind === "raw_video") ?? null;
  // Phase 3F — live brand/campaign/content lookups (no demo-data leak).
  // Three small awaits in parallel; each falls back to null cleanly.
  const content = await getContentItemById(job.contentItemId);
  const campaign = content
    ? await getCampaignById(content.campaignId)
    : null;
  const brand = campaign ? await getBrandById(campaign.brandId) : null;

  const durationSec = job.durationSeconds ?? 15;
  const seedanceEstimate = getEstimatedSeedanceCredits(
    job.qualityTier,
    durationSec,
  );
  const audioFixerEstimate = getEstimatedAudioFixerCredits(durationSec);
  const latestAudioFixerJob = audioFixerJobs[0] ?? null;

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <Link
          href="/agency/jobs"
          className="text-sm text-[color:var(--color-ink-muted)] hover:text-[color:var(--color-ink)]"
        >
          ← All jobs
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">
          {content?.title ?? "Generation job"}
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge tone={STATUS_TONE[job.status]}>{STATUS_LABEL[job.status]}</Badge>
          <span className="text-xs text-[color:var(--color-ink-muted)]">
            {brand?.name ?? "—"} · {campaign?.title ?? "—"}
          </span>
          <span className="text-xs text-[color:var(--color-ink-muted)]">
            · created {new Date(job.createdAt).toLocaleString("en-GB")}
          </span>
        </div>
        <p className="mt-3 text-sm text-[color:var(--color-ink-muted)] max-w-2xl">
          The buttons on this page DO NOT call Enhancor, Seedance, or
          Audio Fixer. Mock actions mutate{" "}
          <code className="font-mono text-xs px-1">generation_jobs.status</code>{" "}
          and append events. Phase 1G surfaces the copy-paste Seedance
          submit command behind explicit confirmation; Phase 1H ingests
          the resulting JSON / MP4 artefacts back into Supabase via{" "}
          <code className="font-mono text-xs px-1">
            scripts/ingest_generation_job_run.py
          </code>
          .
        </p>
      </div>

      <div className="grid lg:grid-cols-[1fr_320px] gap-5">
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>Result / thumbnail placeholder</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3">
              {job.thumbnailUrl ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img
                  src={job.thumbnailUrl}
                  alt={content?.title ?? "Generation thumbnail"}
                  className="w-full max-w-md rounded-md border border-[color:var(--color-hairline)]"
                />
              ) : (
                <div className="aspect-video w-full max-w-md rounded-md border border-dashed border-[color:var(--color-hairline)] bg-[color:var(--color-cream-soft)] flex items-center justify-center text-xs text-[color:var(--color-ink-muted)]">
                  No thumbnail yet — Phase 1G writes one here once the
                  provider returns.
                </div>
              )}
              {job.resultUrl ? (
                <div className="text-xs text-[color:var(--color-ink-muted)] break-all">
                  Internal raw path:{" "}
                  <code className="font-mono">{job.resultUrl}</code>
                  <span className="block mt-0.5 italic">
                    Operator-only. Never exposed to the client portal.
                  </span>
                </div>
              ) : (
                <div className="text-xs text-[color:var(--color-ink-muted)]">
                  No result URL yet. Status is{" "}
                  <strong>{STATUS_LABEL[job.status]}</strong>.
                </div>
              )}
            </CardBody>
          </Card>

          {rawVideoAsset && (
            <Card>
              <CardHeader>
                <CardTitle>Raw video asset (Phase 1H ingested)</CardTitle>
              </CardHeader>
              <CardBody className="space-y-2 text-sm">
                <RawVideoAssetRows asset={rawVideoAsset} />
              </CardBody>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Prompt version</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3 text-sm">
              {promptVersion ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="info">
                      v{promptVersion.versionNumber}
                      {promptVersion.label ? ` · ${promptVersion.label}` : ""}
                    </Badge>
                    <Badge tone="neutral">
                      {QUALITY_TIER_LABEL[promptVersion.qualityTier] ??
                        promptVersion.qualityTier}
                    </Badge>
                    <Badge tone="neutral">{promptVersion.status}</Badge>
                  </div>
                  {promptVersion.hook && (
                    <PromptField label="Hook">{promptVersion.hook}</PromptField>
                  )}
                  {promptVersion.promptBody && (
                    <PromptField label="Prompt body">
                      {promptVersion.promptBody}
                    </PromptField>
                  )}
                  {promptVersion.negativePrompt && (
                    <PromptField label="Negative prompt">
                      {promptVersion.negativePrompt}
                    </PromptField>
                  )}
                  {campaign && content && (
                    <Link
                      href={`/agency/campaigns/${campaign.id}/content/${content.id}/prompt`}
                      className="inline-block text-sm text-[color:var(--color-accent)] underline"
                    >
                      Open prompt editor →
                    </Link>
                  )}
                </>
              ) : (
                <p className="text-[color:var(--color-ink-muted)]">
                  Prompt version not found.
                </p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Client feedback</CardTitle>
            </CardHeader>
            <CardBody>
              <ClientFeedbackSummary approvals={approvals} feedback={feedback} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Raw request placeholder</CardTitle>
            </CardHeader>
            <CardBody>
              <pre className="text-[11px] leading-snug bg-[color:var(--color-cream-soft)] border border-[color:var(--color-hairline)] rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-all">
                {JSON.stringify(
                  job.rawRequestJson ?? {
                    note: "No request payload yet — populated by Phase 1G's real Enhancor submission.",
                  },
                  null,
                  2,
                )}
              </pre>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Status timeline</CardTitle>
            </CardHeader>
            <CardBody>
              {events.length === 0 ? (
                <p className="text-sm text-[color:var(--color-ink-muted)]">
                  No events yet.
                </p>
              ) : (
                <ol className="space-y-3">
                  {events.map((e) => (
                    <li
                      key={e.id}
                      className="flex items-start gap-3 text-sm"
                    >
                      <span className="mt-0.5">
                        <Badge tone="neutral">{EVENT_LABEL[e.eventType]}</Badge>
                      </span>
                      <div className="min-w-0">
                        <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
                          {new Date(e.createdAt).toLocaleString("en-GB")}
                        </div>
                        {e.message && (
                          <div className="mt-0.5 leading-relaxed">
                            {e.message}
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
              <div className="mt-5 pt-4 border-t border-[color:var(--color-hairline)]">
                <h3 className="text-xs uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)] mb-2">
                  Add operator note
                </h3>
                <JobNoteForm jobId={job.id} />
              </div>
            </CardBody>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>Cost estimate</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2 text-sm">
              <Line
                label="Seedance"
                value={`${formatCredits(seedanceEstimate)} cr`}
              />
              <Line
                label="Audio Fixer (optional)"
                value={`${formatCredits(audioFixerEstimate)} cr`}
              />
              <div className="pt-2 mt-2 border-t border-[color:var(--color-hairline)]">
                <Line
                  label="Estimated total"
                  value={`${formatCredits(seedanceEstimate)} cr (base)`}
                  bold
                />
                <p className="text-[10px] text-[color:var(--color-ink-faint)] italic mt-0.5">
                  Audio Fixer is manual; it is NOT added to the base total
                  unless the operator explicitly opts in.
                </p>
              </div>
              {typeof job.actualCredits === "number" && (
                <div className="pt-2 mt-2 border-t border-[color:var(--color-hairline)]">
                  <Line
                    label="Actual"
                    value={`${formatCredits(job.actualCredits)} cr`}
                    bold
                  />
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Job actions</CardTitle>
            </CardHeader>
            <CardBody className="space-y-4">
              <JobMarkQueuedButton jobId={job.id} status={job.status} />
              <JobCancelButton jobId={job.id} status={job.status} />
            </CardBody>
          </Card>

          {isMultiClip ? (
            <Card>
              <CardHeader>
                <CardTitle>Per-clip submit gate (Phase 1S)</CardTitle>
              </CardHeader>
              <CardBody>
                <ClipSubmitGatePanel
                  jobId={job.id}
                  batchId={job.batchId}
                  clipNumber={job.clipNumber as number}
                  clipRole={job.clipRole ?? null}
                  durationSeconds={job.durationSeconds}
                  estimatedCredits={job.estimatedCredits}
                  status={job.status}
                  targetDurationSeconds={
                    batch?.targetDurationSeconds ?? null
                  }
                  provider={job.provider}
                  priorClip={
                    priorClipJob
                      ? {
                          clipNumber: priorClipJob.clipNumber ?? null,
                          status: priorClipJob.status,
                        }
                      : null
                  }
                />
              </CardBody>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Submit to Enhancor (Phase 1G)</CardTitle>
              </CardHeader>
              <CardBody>
                <SubmitToEnhancorPanel
                  jobId={job.id}
                  estimatedCredits={
                    typeof job.estimatedCredits === "number"
                      ? job.estimatedCredits
                      : seedanceEstimate
                  }
                  qualityTier={job.qualityTier}
                  resolution={job.resolution}
                  durationSeconds={job.durationSeconds}
                  status={job.status}
                />
              </CardBody>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Poll &amp; download</CardTitle>
            </CardHeader>
            <CardBody>
              <PollDownloadPanel
                jobId={job.id}
                status={job.status}
                providerRequestId={job.providerRequestId}
                resultUrl={job.resultUrl}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Audio Fixer</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3 text-sm">
              {latestAudioFixerJob ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={AUDIO_FIXER_STATUS_TONE[latestAudioFixerJob.status]}>
                      {AUDIO_FIXER_STATUS_LABEL[latestAudioFixerJob.status]}
                    </Badge>
                    {typeof latestAudioFixerJob.actualCredits === "number" && (
                      <span className="text-xs text-[color:var(--color-ink-muted)]">
                        Actual{" "}
                        {formatCredits(latestAudioFixerJob.actualCredits)} cr
                      </span>
                    )}
                  </div>
                  {latestAudioFixerJob.resultUrl && (
                    <div className="text-xs text-[color:var(--color-ink-muted)] break-all">
                      Audio-fixed path:{" "}
                      <code className="font-mono">
                        {latestAudioFixerJob.resultUrl}
                      </code>
                    </div>
                  )}
                </>
              ) : job.status === "completed" && rawVideoAsset ? (
                <AudioFixerCommandPanel
                  jobId={job.id}
                  audioFixerEstimateCredits={audioFixerEstimate}
                />
              ) : (
                <>
                  <Badge tone="neutral">
                    Available (manual) — waiting on a completed raw video
                  </Badge>
                  <p className="text-[color:var(--color-ink-muted)]">
                    Audio Fixer adds an estimated{" "}
                    <strong>
                      {formatCredits(audioFixerEstimate)} credits
                    </strong>{" "}
                    to this run. It is never auto-triggered. The
                    operator-driven CLI bridge (Phase 1I,{" "}
                    <code className="font-mono">
                      scripts/run_audio_fixer_job.py
                    </code>
                    ) becomes available on this card once the parent job
                    reaches <code>completed</code> AND a raw_video asset
                    has been ingested.
                  </p>
                  <Button variant="secondary" disabled>
                    Run Audio Fixer later
                  </Button>
                  <p className="text-[10px] text-[color:var(--color-ink-faint)] italic">
                    Audio Fixer stays manual; the opt-in confirmation
                    flow that actually spends credits is operator-driven
                    via the CLI bridge, never the dashboard process.
                  </p>
                </>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Job metadata</CardTitle>
            </CardHeader>
            <CardBody className="space-y-1 text-xs text-[color:var(--color-ink-muted)]">
              <Line label="Provider" value={job.provider} />
              {job.providerMode && (
                <Line label="Provider mode" value={job.providerMode} />
              )}
              <Line
                label="Quality tier"
                value={QUALITY_TIER_LABEL[job.qualityTier] ?? job.qualityTier}
              />
              {job.resolution && <Line label="Resolution" value={job.resolution} />}
              {typeof job.durationSeconds === "number" && (
                <Line label="Duration" value={`${job.durationSeconds}s`} />
              )}
              {job.providerRequestId && (
                <Line
                  label="Provider request id"
                  value={job.providerRequestId}
                />
              )}
              {batch && (
                <Line
                  label="Batch"
                  value={`${batch.label ?? batch.id} · ${batch.status}`}
                />
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}

function PromptField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-faint)]">
        {label}
      </div>
      <div className="mt-0.5 leading-relaxed">{children}</div>
    </div>
  );
}

function Line({
  label,
  value,
  bold = false,
}: {
  label: string;
  value: string;
  bold?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[color:var(--color-ink-muted)]">{label}</span>
      <span
        className={
          bold
            ? "font-semibold tabular-nums text-[color:var(--color-ink)]"
            : "tabular-nums text-[color:var(--color-ink)]"
        }
      >
        {value}
      </span>
    </div>
  );
}

function formatBytes(bytes: number | null): string | null {
  if (bytes === null || !Number.isFinite(bytes)) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function RawVideoAssetRows({ asset }: { asset: GeneratedAsset }) {
  const byteLine = formatBytes(asset.byteSize);
  return (
    <>
      <Line label="Asset id" value={asset.id} />
      <Line label="Kind" value={asset.kind} />
      <Line label="Storage path" value={asset.storagePath} />
      {asset.publicUrl && (
        <Line label="Provider URL" value={asset.publicUrl} />
      )}
      {byteLine && <Line label="Size" value={byteLine} />}
      {asset.durationSec !== null && (
        <Line label="Duration" value={`${asset.durationSec.toFixed(2)}s`} />
      )}
      {asset.resolution && <Line label="Resolution" value={asset.resolution} />}
      {asset.mime && <Line label="MIME" value={asset.mime} />}
      <p className="pt-2 mt-2 border-t border-[color:var(--color-hairline)] text-[10px] text-[color:var(--color-ink-faint)] italic leading-relaxed">
        Local filesystem path until Supabase Storage lands (Phase 1I+).
        The dashboard never serves the MP4 directly; clients only see
        the safe thumbnail URL on shared content items.
      </p>
    </>
  );
}
