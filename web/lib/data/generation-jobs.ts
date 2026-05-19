// Yuvo Studio — Phase 1F generation_jobs readers + demo store.
//
// OPERATOR-ONLY. The four tables backing this file (generation_batches,
// generation_jobs, generation_job_events, audio_fixer_jobs) all carry
// operator-only data: provider request payloads, cost numbers, internal
// asset paths. None of these is ever surfaced to the client portal.
//
// Phase 1F is DRY-RUN ONLY. No function in this module performs a paid
// generation call. The demo store mirrors `supabase/seed.sql` row-for-row
// so the operator dashboard renders the same content with
// NEXT_PUBLIC_DATA_SOURCE=demo (default) or =supabase.

import { getDataSource, SupabaseDataError } from "./_source";
import { getSupabaseServerClient } from "@/lib/supabase/client";
import type { PromptVersionQualityTier } from "./prompt-versions";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type GenerationBatchStatus =
  | "draft"
  | "ready"
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "cancelled";

export type GenerationJobStatus =
  | "draft"
  | "queued"
  | "submitted"
  | "processing"
  | "completed"
  | "failed"
  | "cancelled";

export type GenerationJobEventType =
  | "created"
  | "queued"
  | "submitted"
  | "status_polled"
  | "completed"
  | "failed"
  | "cancelled"
  | "operator_note";

export type GenerationProvider =
  | "enhancor_seedance"
  | "enhancor_audio_fixer"
  | "enhancor_other"
  | "mock";

export type AudioFixerStatus =
  | "not_needed"
  | "available"
  | "queued"
  | "submitted"
  | "processing"
  | "completed"
  | "failed"
  | "skipped_by_operator";

export interface GenerationBatch {
  id: string;
  workspaceId: string;
  brandId: string;
  campaignId: string;
  createdBy: string | null;
  label: string | null;
  status: GenerationBatchStatus;
  totalEstimatedCredits: number | null;
  totalActualCredits: number | null;
  createdAt: string;
  updatedAt: string;
  // Phase 1R (migration 007) — optional so existing reads/mappers that
  // don't select these columns keep type-checking. Populated only by
  // the duration-plan draft path; undefined for legacy single-clip
  // batches and for the supabase read path until 007's columns are
  // added to BATCH_SELECT_COLS in a later phase.
  targetDurationSeconds?: number | null;
  clipPlan?: unknown;
}

export interface GenerationJob {
  id: string;
  batchId: string;
  contentItemId: string;
  promptVersionId: string;
  provider: GenerationProvider;
  providerMode: string | null;
  qualityTier: PromptVersionQualityTier;
  resolution: "480p" | "720p" | "1080p" | null;
  durationSeconds: number | null;
  status: GenerationJobStatus;
  estimatedCredits: number | null;
  actualCredits: number | null;
  providerRequestId: string | null;
  resultUrl: string | null;
  thumbnailUrl: string | null;
  rawAssetId: string | null;
  errorMessage: string | null;
  rawRequestJson: unknown;
  rawResponseJson: unknown;
  // Phase 1R (migration 007) — optional, same rationale as
  // GenerationBatch above. NULL/undefined = legacy single clip.
  clipNumber?: number | null;
  clipRole?: "standalone" | "open_loop" | "close_loop" | null;
  createdAt: string;
  updatedAt: string;
}

export interface GenerationJobEvent {
  id: string;
  generationJobId: string;
  eventType: GenerationJobEventType;
  message: string | null;
  rawPayload: unknown;
  createdAt: string;
}

export interface AudioFixerJob {
  id: string;
  generationJobId: string;
  inputAssetId: string | null;
  provider: "enhancor_audio_fixer" | "mock";
  status: AudioFixerStatus;
  estimatedCredits: number | null;
  actualCredits: number | null;
  providerRequestId: string | null;
  resultUrl: string | null;
  outputAssetId: string | null;
  errorMessage: string | null;
  rawRequestJson: unknown;
  rawResponseJson: unknown;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Demo store — mirrors supabase/seed.sql Phase 1F block.
//
// IDs match the seed.sql UUID convention so the same job is reachable
// under the same URL whether the operator is in demo or supabase mode.
// ---------------------------------------------------------------------------

const HISTORICAL_BATCH_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff";
const HISTORICAL_JOB_ID = "0a0a0a0a-0a0a-0a0a-0a0a-0a0a0a0a0a0a";
const HISTORICAL_AUDIO_FIXER_ID = "0d0d0d0d-0d0d-0d0d-0d0d-0d0d0d0d0d0d";
const MOCK_BATCH_ID = "1a1a1a1a-1a1a-1a1a-1a1a-1a1a1a1a1a1a";
const MOCK_JOB_ID = "1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b";

const DEMO_BATCHES: GenerationBatch[] = [
  {
    id: HISTORICAL_BATCH_ID,
    workspaceId: "ws_yuvo",
    brandId: "brand_pai",
    campaignId: "camp_pai_route01",
    createdBy: null,
    label: "Route 01 — 1080p hero (historical Pai test)",
    status: "completed",
    totalEstimatedCredits: 5940,
    totalActualCredits: 5940,
    createdAt: "2026-05-15T12:00:00Z",
    updatedAt: "2026-05-15T12:34:00Z",
  },
  {
    id: MOCK_BATCH_ID,
    workspaceId: "ws_yuvo",
    brandId: "brand_pai",
    campaignId: "camp_pai_route01",
    createdBy: null,
    label: "Route 01 — 720p ingredient close-up (next-week mock)",
    status: "ready",
    totalEstimatedCredits: 2646,
    totalActualCredits: null,
    createdAt: "2026-05-16T08:30:00Z",
    updatedAt: "2026-05-16T08:30:00Z",
  },
];

const DEMO_JOBS: GenerationJob[] = [
  {
    id: HISTORICAL_JOB_ID,
    batchId: HISTORICAL_BATCH_ID,
    contentItemId: "content_pai_route01_v1",
    promptVersionId: "promptver_pai_v1_1080p",
    provider: "enhancor_seedance",
    providerMode: "ugc",
    qualityTier: "premium_1080p",
    resolution: "1080p",
    durationSeconds: 15,
    status: "completed",
    estimatedCredits: 5940,
    actualCredits: 5940,
    providerRequestId: "historical-pai-1080p-15s",
    resultUrl:
      "prospects/pai-skincare/production/clips/route_01_enhancor_ugc_raw_15s.mp4",
    thumbnailUrl: "/demo/pai-thumb.webp",
    rawAssetId: null,
    errorMessage: null,
    rawRequestJson: { note: "historical — original request payload not retained" },
    rawResponseJson: { note: "historical — original response payload not retained" },
    createdAt: "2026-05-15T12:00:30Z",
    updatedAt: "2026-05-15T12:18:00Z",
  },
  {
    id: MOCK_JOB_ID,
    batchId: MOCK_BATCH_ID,
    contentItemId: "content_pai_route02_draft",
    promptVersionId: "promptver_pai_v2_720p_draft",
    provider: "enhancor_seedance",
    providerMode: "ugc",
    qualityTier: "standard_720p",
    resolution: "720p",
    durationSeconds: 15,
    status: "draft",
    estimatedCredits: 2646,
    actualCredits: null,
    providerRequestId: null,
    resultUrl: null,
    thumbnailUrl: null,
    rawAssetId: null,
    errorMessage: null,
    rawRequestJson: null,
    rawResponseJson: null,
    createdAt: "2026-05-16T08:30:00Z",
    updatedAt: "2026-05-16T08:30:00Z",
  },
];

const DEMO_EVENTS: GenerationJobEvent[] = [
  {
    id: "0b0b0b0b-0b0b-0b0b-0b0b-0b0b0b0b0b0b",
    generationJobId: HISTORICAL_JOB_ID,
    eventType: "created",
    message: "Historical Pai 1080p run — backfilled from operator notes.",
    rawPayload: null,
    createdAt: "2026-05-15T12:00:30Z",
  },
  {
    id: "0c0c0c0c-0c0c-0c0c-0c0c-0c0c0c0c0c0c",
    generationJobId: HISTORICAL_JOB_ID,
    eventType: "completed",
    message: "Raw 15s clip rendered. Operator triggered Audio Fixer manually.",
    rawPayload: null,
    createdAt: "2026-05-15T12:18:00Z",
  },
  {
    id: "1c1c1c1c-1c1c-1c1c-1c1c-1c1c1c1c1c1c",
    generationJobId: MOCK_JOB_ID,
    eventType: "created",
    message:
      "Mock generation job created from approved 720p prompt version. No paid call has been made.",
    rawPayload: null,
    createdAt: "2026-05-16T08:30:00Z",
  },
];

const DEMO_AUDIO_FIXER_JOBS: AudioFixerJob[] = [
  {
    id: HISTORICAL_AUDIO_FIXER_ID,
    generationJobId: HISTORICAL_JOB_ID,
    inputAssetId: "asset_pai_raw_1080p",
    provider: "enhancor_audio_fixer",
    status: "completed",
    estimatedCredits: 2100,
    actualCredits: 2104,
    providerRequestId: "historical-pai-audiofixer-15s",
    resultUrl:
      "prospects/pai-skincare/production/clips/route_01_enhancor_ugc_audiofixed_15s.mp4",
    outputAssetId: "asset_pai_audiofixed_1080p",
    errorMessage: null,
    rawRequestJson: {
      note: "historical — original audio-fixer payload not retained",
    },
    rawResponseJson: {
      note: "historical — original audio-fixer response not retained",
    },
    createdAt: "2026-05-15T12:20:00Z",
    updatedAt: "2026-05-15T12:24:00Z",
  },
];

export function _demoPushGenerationBatch(row: GenerationBatch): void {
  DEMO_BATCHES.push(row);
}
export function _demoPushGenerationJob(row: GenerationJob): void {
  DEMO_JOBS.push(row);
}
export function _demoPushGenerationJobEvent(row: GenerationJobEvent): void {
  DEMO_EVENTS.push(row);
}
export function _demoUpdateGenerationJob(
  id: string,
  patch: Partial<GenerationJob>,
): GenerationJob | null {
  const idx = DEMO_JOBS.findIndex((j) => j.id === id);
  if (idx === -1) return null;
  DEMO_JOBS[idx] = {
    ...DEMO_JOBS[idx],
    ...patch,
    updatedAt: new Date().toISOString(),
  };
  return DEMO_JOBS[idx];
}
export function _demoFindGenerationJobById(id: string): GenerationJob | null {
  return DEMO_JOBS.find((j) => j.id === id) ?? null;
}
export function _demoListGenerationJobs(): GenerationJob[] {
  return [...DEMO_JOBS];
}
export function _demoListGenerationBatches(): GenerationBatch[] {
  return [...DEMO_BATCHES];
}

// ---------------------------------------------------------------------------
// Row → view mappers
// ---------------------------------------------------------------------------

// Phase 1S — migration 007 is applied & verified live, so the multi-clip
// columns are now SELECT-safe. Legacy single-clip rows return NULL for
// target_duration_seconds / clip_plan / clip_number / clip_role, which
// the mappers below pass through unchanged (treated as "legacy clip 1").
const BATCH_SELECT_COLS =
  "id, workspace_id, brand_id, campaign_id, created_by, label, status, " +
  "total_estimated_credits, total_actual_credits, created_at, updated_at, " +
  "target_duration_seconds, clip_plan";

const JOB_SELECT_COLS =
  "id, batch_id, content_item_id, prompt_version_id, provider, provider_mode, " +
  "quality_tier, resolution, duration_seconds, status, estimated_credits, " +
  "actual_credits, provider_request_id, result_url, thumbnail_url, raw_asset_id, " +
  "error_message, raw_request_json, raw_response_json, created_at, updated_at, " +
  "clip_number, clip_role";

const EVENT_SELECT_COLS =
  "id, generation_job_id, event_type, message, raw_payload, created_at";

const AUDIO_FIXER_SELECT_COLS =
  "id, generation_job_id, input_asset_id, provider, status, estimated_credits, " +
  "actual_credits, provider_request_id, result_url, output_asset_id, " +
  "error_message, raw_request_json, raw_response_json, created_at, updated_at";

function batchRowToView(r: {
  id: string;
  workspace_id: string;
  brand_id: string;
  campaign_id: string;
  created_by: string | null;
  label: string | null;
  status: GenerationBatchStatus;
  total_estimated_credits: number | null;
  total_actual_credits: number | null;
  created_at: string;
  updated_at: string;
  target_duration_seconds?: number | null;
  clip_plan?: unknown;
}): GenerationBatch {
  return {
    id: r.id,
    workspaceId: r.workspace_id,
    brandId: r.brand_id,
    campaignId: r.campaign_id,
    createdBy: r.created_by,
    label: r.label,
    status: r.status,
    totalEstimatedCredits: r.total_estimated_credits,
    totalActualCredits: r.total_actual_credits,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    targetDurationSeconds: r.target_duration_seconds ?? null,
    clipPlan: r.clip_plan ?? null,
  };
}

function jobRowToView(r: {
  id: string;
  batch_id: string;
  content_item_id: string;
  prompt_version_id: string;
  provider: GenerationProvider;
  provider_mode: string | null;
  quality_tier: PromptVersionQualityTier;
  resolution: "480p" | "720p" | "1080p" | null;
  duration_seconds: number | null;
  status: GenerationJobStatus;
  estimated_credits: number | null;
  actual_credits: number | null;
  provider_request_id: string | null;
  result_url: string | null;
  thumbnail_url: string | null;
  raw_asset_id: string | null;
  error_message: string | null;
  raw_request_json: unknown;
  raw_response_json: unknown;
  created_at: string;
  updated_at: string;
  clip_number?: number | null;
  clip_role?: "standalone" | "open_loop" | "close_loop" | null;
}): GenerationJob {
  return {
    id: r.id,
    batchId: r.batch_id,
    contentItemId: r.content_item_id,
    promptVersionId: r.prompt_version_id,
    provider: r.provider,
    providerMode: r.provider_mode,
    qualityTier: r.quality_tier,
    resolution: r.resolution,
    durationSeconds: r.duration_seconds,
    status: r.status,
    estimatedCredits: r.estimated_credits,
    actualCredits: r.actual_credits,
    providerRequestId: r.provider_request_id,
    resultUrl: r.result_url,
    thumbnailUrl: r.thumbnail_url,
    rawAssetId: r.raw_asset_id,
    errorMessage: r.error_message,
    rawRequestJson: r.raw_request_json,
    rawResponseJson: r.raw_response_json,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    clipNumber: r.clip_number ?? null,
    clipRole: r.clip_role ?? null,
  };
}

function eventRowToView(r: {
  id: string;
  generation_job_id: string;
  event_type: GenerationJobEventType;
  message: string | null;
  raw_payload: unknown;
  created_at: string;
}): GenerationJobEvent {
  return {
    id: r.id,
    generationJobId: r.generation_job_id,
    eventType: r.event_type,
    message: r.message,
    rawPayload: r.raw_payload,
    createdAt: r.created_at,
  };
}

function audioFixerRowToView(r: {
  id: string;
  generation_job_id: string;
  input_asset_id: string | null;
  provider: "enhancor_audio_fixer" | "mock";
  status: AudioFixerStatus;
  estimated_credits: number | null;
  actual_credits: number | null;
  provider_request_id: string | null;
  result_url: string | null;
  output_asset_id: string | null;
  error_message: string | null;
  raw_request_json: unknown;
  raw_response_json: unknown;
  created_at: string;
  updated_at: string;
}): AudioFixerJob {
  return {
    id: r.id,
    generationJobId: r.generation_job_id,
    inputAssetId: r.input_asset_id,
    provider: r.provider,
    status: r.status,
    estimatedCredits: r.estimated_credits,
    actualCredits: r.actual_credits,
    providerRequestId: r.provider_request_id,
    resultUrl: r.result_url,
    outputAssetId: r.output_asset_id,
    errorMessage: r.error_message,
    rawRequestJson: r.raw_request_json,
    rawResponseJson: r.raw_response_json,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/** Every generation job in the workspace, newest first. Phase 1F has no
 *  workspace filter in demo mode (single workspace seeded); supabase
 *  mode lets RLS handle the workspace gate. */
export async function listAllGenerationJobs(): Promise<GenerationJob[]> {
  if (getDataSource() === "demo") {
    return [...DEMO_JOBS].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("generation_jobs")
    .select(JOB_SELECT_COLS)
    .order("created_at", { ascending: false });
  if (error) throw new SupabaseDataError("listAllGenerationJobs", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof jobRowToView>[0][]).map(jobRowToView);
}

export async function listGenerationJobsForCampaign(
  campaignId: string,
): Promise<GenerationJob[]> {
  if (getDataSource() === "demo") {
    // batch.campaign_id is the source of truth.
    const batchIds = DEMO_BATCHES
      .filter((b) => b.campaignId === campaignId)
      .map((b) => b.id);
    return DEMO_JOBS
      .filter((j) => batchIds.includes(j.batchId))
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
  const supabase = getSupabaseServerClient();
  // PostgREST inner-join via the batch table: `batch_id` references
  // `generation_batches`, and `!inner` forces an inner join so the
  // filter on `campaign_id` actually filters the parent rows.
  const { data, error } = await supabase
    .from("generation_jobs")
    .select(`${JOB_SELECT_COLS}, generation_batches!inner(campaign_id)`)
    .eq("generation_batches.campaign_id", campaignId)
    .order("created_at", { ascending: false });
  if (error) throw new SupabaseDataError("listGenerationJobsForCampaign", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof jobRowToView>[0][]).map(jobRowToView);
}

export async function listGenerationJobsForContentItem(
  contentItemId: string,
): Promise<GenerationJob[]> {
  if (getDataSource() === "demo") {
    return DEMO_JOBS
      .filter((j) => j.contentItemId === contentItemId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("generation_jobs")
    .select(JOB_SELECT_COLS)
    .eq("content_item_id", contentItemId)
    .order("created_at", { ascending: false });
  if (error) throw new SupabaseDataError("listGenerationJobsForContentItem", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof jobRowToView>[0][]).map(jobRowToView);
}

export async function listGenerationJobsForPromptVersion(
  promptVersionId: string,
): Promise<GenerationJob[]> {
  if (getDataSource() === "demo") {
    return DEMO_JOBS
      .filter((j) => j.promptVersionId === promptVersionId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("generation_jobs")
    .select(JOB_SELECT_COLS)
    .eq("prompt_version_id", promptVersionId)
    .order("created_at", { ascending: false });
  if (error)
    throw new SupabaseDataError("listGenerationJobsForPromptVersion", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof jobRowToView>[0][]).map(jobRowToView);
}

export async function getGenerationJob(
  jobId: string,
): Promise<GenerationJob | null> {
  if (getDataSource() === "demo") {
    return _demoFindGenerationJobById(jobId);
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("generation_jobs")
    .select(JOB_SELECT_COLS)
    .eq("id", jobId)
    .maybeSingle();
  if (error) throw new SupabaseDataError("getGenerationJob", error);
  if (!data) return null;
  return jobRowToView(data as unknown as Parameters<typeof jobRowToView>[0]);
}

export async function getGenerationBatch(
  batchId: string,
): Promise<GenerationBatch | null> {
  if (getDataSource() === "demo") {
    return DEMO_BATCHES.find((b) => b.id === batchId) ?? null;
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("generation_batches")
    .select(BATCH_SELECT_COLS)
    .eq("id", batchId)
    .maybeSingle();
  if (error) throw new SupabaseDataError("getGenerationBatch", error);
  if (!data) return null;
  return batchRowToView(data as unknown as Parameters<typeof batchRowToView>[0]);
}

/** Phase 1S — every job in a batch, ordered by clip_number (NULLs last,
 *  then created_at). Used by the per-clip submit gate to evaluate the
 *  sequencing rule: clip N stays blocked until clip N-1 is completed. */
export async function listGenerationJobsForBatch(
  batchId: string,
): Promise<GenerationJob[]> {
  if (getDataSource() === "demo") {
    return DEMO_JOBS.filter((j) => j.batchId === batchId).sort(
      (a, b) =>
        (a.clipNumber ?? 9999) - (b.clipNumber ?? 9999) ||
        a.createdAt.localeCompare(b.createdAt),
    );
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("generation_jobs")
    .select(JOB_SELECT_COLS)
    .eq("batch_id", batchId)
    .order("clip_number", { ascending: true, nullsFirst: false })
    .order("created_at", { ascending: true });
  if (error) throw new SupabaseDataError("listGenerationJobsForBatch", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof jobRowToView>[0][]).map(
    jobRowToView,
  );
}

export async function listGenerationJobEvents(
  jobId: string,
): Promise<GenerationJobEvent[]> {
  if (getDataSource() === "demo") {
    return DEMO_EVENTS
      .filter((e) => e.generationJobId === jobId)
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("generation_job_events")
    .select(EVENT_SELECT_COLS)
    .eq("generation_job_id", jobId)
    .order("created_at", { ascending: true });
  if (error) throw new SupabaseDataError("listGenerationJobEvents", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof eventRowToView>[0][]).map(
    eventRowToView,
  );
}

export async function listAudioFixerJobsForGenerationJob(
  generationJobId: string,
): Promise<AudioFixerJob[]> {
  if (getDataSource() === "demo") {
    return DEMO_AUDIO_FIXER_JOBS
      .filter((a) => a.generationJobId === generationJobId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("audio_fixer_jobs")
    .select(AUDIO_FIXER_SELECT_COLS)
    .eq("generation_job_id", generationJobId)
    .order("created_at", { ascending: false });
  if (error)
    throw new SupabaseDataError("listAudioFixerJobsForGenerationJob", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof audioFixerRowToView>[0][]).map(
    audioFixerRowToView,
  );
}

// ---------------------------------------------------------------------------
// Phase 1H — generated_assets reader (filtered by generation_job_id).
//
// The ingester (scripts/ingest_generation_job_run.py) inserts one
// generated_assets row per downloaded result.mp4 with
// generation_job_id pointing at the parent job. The job detail page
// mounts a "Raw video asset" card when at least one row is found.
// ---------------------------------------------------------------------------

export type GeneratedAssetKind =
  | "raw_video"
  | "audio_fixed_video"
  | "thumbnail"
  | "caption_srt"
  | "export"
  | "static_image"
  | "caption_pack"
  | "post_creative";

export interface GeneratedAsset {
  id: string;
  contentItemId: string;
  generationJobId: string | null;
  audioFixerJobId: string | null;
  kind: GeneratedAssetKind;
  storagePath: string;
  publicUrl: string | null;
  mime: string | null;
  byteSize: number | null;
  durationSec: number | null;
  resolution: string | null;
  generatedAt: string;
}

const GENERATED_ASSET_SELECT_COLS =
  "id, content_item_id, generation_job_id, audio_fixer_job_id, kind, " +
  "storage_path, public_url, mime, byte_size, duration_sec, resolution, " +
  "generated_at";

function generatedAssetRowToView(r: {
  id: string;
  content_item_id: string;
  generation_job_id: string | null;
  audio_fixer_job_id: string | null;
  kind: GeneratedAssetKind;
  storage_path: string;
  public_url: string | null;
  mime: string | null;
  byte_size: number | null;
  duration_sec: number | null;
  resolution: string | null;
  generated_at: string;
}): GeneratedAsset {
  return {
    id: r.id,
    contentItemId: r.content_item_id,
    generationJobId: r.generation_job_id,
    audioFixerJobId: r.audio_fixer_job_id,
    kind: r.kind,
    storagePath: r.storage_path,
    publicUrl: r.public_url,
    mime: r.mime,
    byteSize: r.byte_size,
    durationSec: r.duration_sec,
    resolution: r.resolution,
    generatedAt: r.generated_at,
  };
}

/** Phase 1H — returns the generated_assets rows linked to a given
 *  generation_jobs row, newest first. The demo store has no
 *  generated_assets for the seeded mock job; the historical 1080p
 *  job's assets predate the job table so they aren't linked there
 *  either. The reader is therefore an empty array in demo mode until
 *  the ingester writes a real row.
 */
export async function listGeneratedAssetsForGenerationJob(
  generationJobId: string,
): Promise<GeneratedAsset[]> {
  if (getDataSource() === "demo") {
    // Demo mode keeps no generated_assets-by-job-id index — the demo
    // store seeded these as plain "asset_pai_*" strings on the
    // historical audio_fixer_job, not as full rows. Once an operator
    // runs scripts/ingest_generation_job_run.py against supabase mode
    // the rows appear there; the demo path stays empty.
    void generationJobId;
    return [];
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("generated_assets")
    .select(GENERATED_ASSET_SELECT_COLS)
    .eq("generation_job_id", generationJobId)
    .order("generated_at", { ascending: false });
  if (error)
    throw new SupabaseDataError("listGeneratedAssetsForGenerationJob", error);
  if (!data) return [];
  return (
    data as unknown as Parameters<typeof generatedAssetRowToView>[0][]
  ).map(generatedAssetRowToView);
}
