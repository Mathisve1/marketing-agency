// Yuvo Studio — Phase 1V Owner Command Center aggregator.
//
// OPERATOR-ONLY. Every read in this module returns operator-visible data
// (costs, provider ids, internal statuses, raw asset urls). Do NOT
// import this file from anything under `web/app/client/*`. The
// client-portal surface remains restricted to `web/lib/data/content.ts`,
// which reads through the `client_content_items_v` VIEW + the
// `toClientContentView` mapper.
//
// Phase 1V scope:
//   - No new tables, no new migrations.
//   - Composes existing per-entity readers in web/lib/data/*.
//   - Adds workspace-wide listers (campaigns, content items,
//     regeneration requests, audio fixer jobs) that previously only
//     existed in per-campaign / per-content scopes.
//   - All readers are demo + supabase aware, same shape as the rest of
//     web/lib/data/*.

import { getDataSource, SupabaseDataError } from "./_source";
import { getSupabaseServerClient } from "@/lib/supabase/client";
import {
  DEMO_BRANDS,
  DEMO_CAMPAIGNS,
  DEMO_CONTENT,
} from "@/lib/demo-data";
import {
  _demoListGenerationJobs,
  _demoListGenerationBatches,
  type GenerationJob,
  type GenerationJobStatus,
  type AudioFixerJob,
} from "./generation-jobs";
import {
  _demoListRegenerationRequests,
  type RegenerationRequest,
} from "./regeneration-requests";
import {
  listContentRequestsForWorkspace,
  type ContentRequest,
} from "./content-requests";
import {
  listAgentRunsForWorkspace,
  type AgentRun,
} from "./agent-runs";
import type { Brand, Campaign, ContentItem, ContentStatus } from "@/lib/types";
import { contentItemRowToContentItem, campaignRowToCampaign } from "./mappers";
import type {
  CampaignWithPortalRow,
  ContentItemRow,
} from "@/lib/supabase/types";

// ---------------------------------------------------------------------------
// Workspace-wide lists (additive — kept here rather than polluting the
// existing per-entity modules with a new method).
// ---------------------------------------------------------------------------

const CAMPAIGN_SELECT =
  "id, brand_id, client_portal_id, title, strategic_pattern, created_at, " +
  "client_portals(slug)";

const CONTENT_ITEM_SELECT =
  "id, campaign_id, content_calendar_id, title, status, scheduled_for, " +
  "platforms, hook_text, hook_source, caption_draft, prompt_summary, " +
  "quality_tier, resolution, duration_sec, cost_estimate_credits, " +
  "cost_actual_credits, internal_raw_path, internal_audio_fixed_path, " +
  "internal_thumb_path, client_safe_poster_url, client_safe_video_url, " +
  "shared_with_client, audio_fixer_triggered, audio_fixer_completed, " +
  "audio_fixer_credits_actual";

const REGEN_SELECT_COLS =
  "id, content_item_id, source_feedback_id, source_approval_id, " +
  "requested_by_profile_id, requested_by_kind, reason, body, status, " +
  "accepted_prompt_version_id, resolved_at, resolved_by_profile_id, " +
  "created_at, updated_at";

const AUDIO_FIXER_SELECT_COLS =
  "id, generation_job_id, input_asset_id, provider, status, estimated_credits, " +
  "actual_credits, provider_request_id, result_url, output_asset_id, " +
  "error_message, raw_request_json, raw_response_json, created_at, updated_at";

/** Every campaign visible to the workspace, newest first. Phase 1V. */
export async function listCampaignsForWorkspace(
  workspaceId: string,
): Promise<Campaign[]> {
  if (getDataSource() === "demo") {
    // Demo seed has a single workspace + a single brand; the brand's
    // workspaceId matches the seeded one, so we just sort all demo
    // campaigns by createdAt desc.
    void workspaceId;
    return [...DEMO_CAMPAIGNS].sort((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
  }

  // Walk: workspace → brand ids → campaigns. Mirrors the pattern used in
  // content-requests.ts's workspace-wide reader.
  const supabase = getSupabaseServerClient();
  const { data: brandRows, error: brandErr } = await supabase
    .from("brands")
    .select("id")
    .eq("workspace_id", workspaceId);
  if (brandErr) throw new SupabaseDataError("listCampaignsForWorkspace", brandErr);
  const brandIds = (brandRows ?? []).map((r) => (r as { id: string }).id);
  if (brandIds.length === 0) return [];

  const { data, error } = await supabase
    .from("campaigns")
    .select(CAMPAIGN_SELECT)
    .in("brand_id", brandIds)
    .order("created_at", { ascending: false });
  if (error) throw new SupabaseDataError("listCampaignsForWorkspace", error);
  if (!data) return [];
  return (data as unknown as CampaignWithPortalRow[]).map(campaignRowToCampaign);
}

/** Every content item visible to the workspace, newest scheduled first.
 *  Walks workspace → brands → campaigns → content_items in supabase mode,
 *  and reads the in-memory demo store otherwise. Phase 1V. */
export async function listContentItemsForWorkspace(
  workspaceId: string,
): Promise<ContentItem[]> {
  if (getDataSource() === "demo") {
    void workspaceId;
    return [...DEMO_CONTENT].sort((a, b) =>
      b.scheduledFor.localeCompare(a.scheduledFor),
    );
  }

  const supabase = getSupabaseServerClient();
  const { data: brandRows, error: brandErr } = await supabase
    .from("brands")
    .select("id")
    .eq("workspace_id", workspaceId);
  if (brandErr) throw new SupabaseDataError("listContentItemsForWorkspace", brandErr);
  const brandIds = (brandRows ?? []).map((r) => (r as { id: string }).id);
  if (brandIds.length === 0) return [];

  const { data: campaignRows, error: campErr } = await supabase
    .from("campaigns")
    .select("id")
    .in("brand_id", brandIds);
  if (campErr) throw new SupabaseDataError("listContentItemsForWorkspace", campErr);
  const campaignIds = (campaignRows ?? []).map((r) => (r as { id: string }).id);
  if (campaignIds.length === 0) return [];

  const { data, error } = await supabase
    .from("content_items")
    .select(CONTENT_ITEM_SELECT)
    .in("campaign_id", campaignIds)
    .order("scheduled_for", { ascending: false, nullsFirst: false });
  if (error) throw new SupabaseDataError("listContentItemsForWorkspace", error);
  if (!data) return [];
  return (data as unknown as ContentItemRow[]).map(contentItemRowToContentItem);
}

/** Every regeneration_request visible to the workspace, newest first. */
export async function listRegenerationRequestsForWorkspace(
  workspaceId: string,
): Promise<RegenerationRequest[]> {
  if (getDataSource() === "demo") {
    void workspaceId;
    return _demoListRegenerationRequests().sort((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
  }

  const supabase = getSupabaseServerClient();
  const { data: brandRows, error: brandErr } = await supabase
    .from("brands")
    .select("id")
    .eq("workspace_id", workspaceId);
  if (brandErr)
    throw new SupabaseDataError("listRegenerationRequestsForWorkspace", brandErr);
  const brandIds = (brandRows ?? []).map((r) => (r as { id: string }).id);
  if (brandIds.length === 0) return [];

  const { data: campaignRows, error: campErr } = await supabase
    .from("campaigns")
    .select("id")
    .in("brand_id", brandIds);
  if (campErr)
    throw new SupabaseDataError("listRegenerationRequestsForWorkspace", campErr);
  const campaignIds = (campaignRows ?? []).map((r) => (r as { id: string }).id);
  if (campaignIds.length === 0) return [];

  const { data: contentRows, error: ciErr } = await supabase
    .from("content_items")
    .select("id")
    .in("campaign_id", campaignIds);
  if (ciErr)
    throw new SupabaseDataError("listRegenerationRequestsForWorkspace", ciErr);
  const contentIds = (contentRows ?? []).map((r) => (r as { id: string }).id);
  if (contentIds.length === 0) return [];

  const { data, error } = await supabase
    .from("regeneration_requests")
    .select(REGEN_SELECT_COLS)
    .in("content_item_id", contentIds)
    .order("created_at", { ascending: false });
  if (error)
    throw new SupabaseDataError("listRegenerationRequestsForWorkspace", error);
  if (!data) return [];
  type RawRow = {
    id: string;
    content_item_id: string;
    source_feedback_id: string | null;
    source_approval_id: string | null;
    requested_by_profile_id: string | null;
    requested_by_kind: "client" | "operator";
    reason: RegenerationRequest["reason"];
    body: string;
    status: RegenerationRequest["status"];
    accepted_prompt_version_id: string | null;
    resolved_at: string | null;
    resolved_by_profile_id: string | null;
    created_at: string;
    updated_at: string;
  };
  return (data as unknown as RawRow[]).map((r) => ({
    id: r.id,
    contentItemId: r.content_item_id,
    sourceFeedbackId: r.source_feedback_id,
    sourceApprovalId: r.source_approval_id,
    requestedByProfileId: r.requested_by_profile_id,
    requestedByKind: r.requested_by_kind,
    reason: r.reason,
    body: r.body,
    status: r.status,
    acceptedPromptVersionId: r.accepted_prompt_version_id,
    resolvedAt: r.resolved_at,
    resolvedByProfileId: r.resolved_by_profile_id,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  }));
}

/** Every audio_fixer_job in the workspace, newest first. Phase 1V. */
export async function listAudioFixerJobsForWorkspace(
  workspaceId: string,
): Promise<AudioFixerJob[]> {
  if (getDataSource() === "demo") {
    void workspaceId;
    // The demo store keeps its audio-fixer rows private to
    // generation-jobs.ts. Exposing a workspace-wide reader purely for
    // the demo dashboard isn't worth adding a new export there; we
    // approximate via the single seeded historical row.
    return [];
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("audio_fixer_jobs")
    .select(AUDIO_FIXER_SELECT_COLS)
    .order("created_at", { ascending: false });
  if (error) throw new SupabaseDataError("listAudioFixerJobsForWorkspace", error);
  if (!data) return [];
  type RawRow = {
    id: string;
    generation_job_id: string;
    input_asset_id: string | null;
    provider: AudioFixerJob["provider"];
    status: AudioFixerJob["status"];
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
  };
  return (data as unknown as RawRow[]).map((r) => ({
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
  }));
}

// ---------------------------------------------------------------------------
// Owner-command-center snapshot — composes everything above so the page
// renders from a single async call.
// ---------------------------------------------------------------------------

export interface OwnerOverviewSnapshot {
  workspaceId: string;
  brands: Brand[];
  campaigns: Campaign[];
  contentItems: ContentItem[];
  generationJobs: GenerationJob[];
  audioFixerJobs: AudioFixerJob[];
  regenerationRequests: RegenerationRequest[];
  contentRequests: ContentRequest[];
  /** Phase 1Y — recent agent runs (Brand Analysis + future agents).
   *  Fails-soft to [] if migration 008 isn't applied. */
  agentRuns: AgentRun[];
  /** Total Enhancor credits actually spent (Seedance + Audio Fixer).
   *  Estimates are ignored — costs in this dashboard are realized only. */
  totalCreditsActual: number;
  /** Per-brand realized-cost rollup. Same units as totalCreditsActual. */
  creditsByBrandId: Record<string, number>;
  /** Counts grouped by operator-side ContentStatus. Empty buckets are
   *  initialised to zero so the UI doesn't have to defensive-code. */
  statusCounts: Record<ContentStatus, number>;
  /** Counts of recent generation_jobs by status. */
  jobStatusCounts: Record<GenerationJobStatus, number>;
}

const EMPTY_STATUS_COUNTS = (): Record<ContentStatus, number> => ({
  draft: 0,
  generating: 0,
  raw_ready: 0,
  audio_fixer_pending: 0,
  audio_fixed: 0,
  ready_for_client_review: 0,
  shared_with_client: 0,
  approved_by_client: 0,
  changes_requested_by_client: 0,
  failed: 0,
});

const EMPTY_JOB_STATUS_COUNTS = (): Record<GenerationJobStatus, number> => ({
  draft: 0,
  queued: 0,
  submitted: 0,
  processing: 0,
  completed: 0,
  failed: 0,
  cancelled: 0,
});

/** Phase 1V — single async call backing /agency.
 *  No paid API. No DB writes. All reads are operator-only. */
export async function getOwnerOverview(
  workspaceId: string,
): Promise<OwnerOverviewSnapshot> {
  // Brands first — every other supabase-mode walker also needs the brand
  // id set, so resolving it once is cheaper than re-walking inside each
  // child reader. (Demo mode ignores the workspace filter anyway.)
  const brands = await listBrandsForWorkspace(workspaceId);

  const [
    campaigns,
    contentItems,
    generationJobs,
    audioFixerJobs,
    regenerationRequests,
    contentRequests,
    agentRuns,
  ] = await Promise.all([
    listCampaignsForWorkspace(workspaceId),
    listContentItemsForWorkspace(workspaceId),
    listAllGenerationJobsScopedToWorkspace(workspaceId),
    listAudioFixerJobsForWorkspace(workspaceId),
    listRegenerationRequestsForWorkspace(workspaceId),
    listContentRequestsForWorkspace(workspaceId),
    listAgentRunsForWorkspace(workspaceId, { limit: 20 }),
  ]);

  // Rollups —
  const statusCounts = EMPTY_STATUS_COUNTS();
  for (const c of contentItems) {
    statusCounts[c.status] = (statusCounts[c.status] ?? 0) + 1;
  }

  const jobStatusCounts = EMPTY_JOB_STATUS_COUNTS();
  for (const j of generationJobs) {
    jobStatusCounts[j.status] = (jobStatusCounts[j.status] ?? 0) + 1;
  }

  // Realised credit spend per brand. Generation jobs carry actualCredits
  // on completion; audio_fixer_jobs carry actualCredits separately. We
  // attribute audio-fixer cost to the parent job's brand by joining
  // through the content item.
  const creditsByBrandId: Record<string, number> = {};
  let totalCreditsActual = 0;
  const contentToBrand = new Map<string, string>();
  const campaignToBrand = new Map<string, string>();
  for (const c of campaigns) campaignToBrand.set(c.id, c.brandId);
  for (const ci of contentItems) {
    const b = campaignToBrand.get(ci.campaignId);
    if (b) contentToBrand.set(ci.id, b);
  }
  for (const j of generationJobs) {
    if (typeof j.actualCredits !== "number") continue;
    const b = contentToBrand.get(j.contentItemId);
    if (!b) continue;
    creditsByBrandId[b] = (creditsByBrandId[b] ?? 0) + j.actualCredits;
    totalCreditsActual += j.actualCredits;
  }
  const jobIdToBrand = new Map<string, string>();
  for (const j of generationJobs) {
    const b = contentToBrand.get(j.contentItemId);
    if (b) jobIdToBrand.set(j.id, b);
  }
  for (const a of audioFixerJobs) {
    if (typeof a.actualCredits !== "number") continue;
    const b = jobIdToBrand.get(a.generationJobId);
    if (!b) continue;
    creditsByBrandId[b] = (creditsByBrandId[b] ?? 0) + a.actualCredits;
    totalCreditsActual += a.actualCredits;
  }

  return {
    workspaceId,
    brands,
    campaigns,
    contentItems,
    generationJobs,
    audioFixerJobs,
    regenerationRequests,
    contentRequests,
    agentRuns,
    totalCreditsActual,
    creditsByBrandId,
    statusCounts,
    jobStatusCounts,
  };
}

// ---------------------------------------------------------------------------
// Helpers — small wrappers over existing readers so the snapshot above
// stays linear / Promise.all-shaped.
// ---------------------------------------------------------------------------

async function listBrandsForWorkspace(workspaceId: string): Promise<Brand[]> {
  if (getDataSource() === "demo") {
    void workspaceId;
    return [...DEMO_BRANDS];
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("brands")
    .select(
      "id, workspace_id, name, niche, website_url, brand_tone, " +
        "audience_assumption, primary_color_hex, thumbnail_path",
    )
    .eq("workspace_id", workspaceId)
    .order("name", { ascending: true });
  if (error) throw new SupabaseDataError("listBrandsForWorkspace", error);
  if (!data) return [];
  type RawRow = {
    id: string;
    workspace_id: string;
    name: string;
    niche: string;
    website_url: string;
    brand_tone: string;
    audience_assumption: string;
    primary_color_hex: string;
    thumbnail_path: string | null;
  };
  return (data as unknown as RawRow[]).map((r) => ({
    id: r.id,
    workspaceId: r.workspace_id,
    name: r.name,
    niche: r.niche,
    websiteUrl: r.website_url,
    brandTone: r.brand_tone,
    audienceAssumption: r.audience_assumption,
    primaryColorHex: r.primary_color_hex,
    thumbnailPath: r.thumbnail_path ?? undefined,
  }));
}

async function listAllGenerationJobsScopedToWorkspace(
  workspaceId: string,
): Promise<GenerationJob[]> {
  // Demo mode has a single workspace; supabase mode currently lacks a
  // workspace_id on generation_jobs, but every job is reachable via
  // batch.brand_id → brands.workspace_id. We approximate by reading all
  // jobs and filtering down where possible. For Phase 1V this is fine
  // because the production workspace is single-tenant.
  if (getDataSource() === "demo") {
    void workspaceId;
    return _demoListGenerationJobs().sort((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
  }

  // Supabase path: pull batches in the workspace's brands, then jobs.
  const supabase = getSupabaseServerClient();
  const { data: brandRows } = await supabase
    .from("brands")
    .select("id")
    .eq("workspace_id", workspaceId);
  const brandIds = (brandRows ?? []).map((r) => (r as { id: string }).id);
  if (brandIds.length === 0) return [];

  const { data: batchRows } = await supabase
    .from("generation_batches")
    .select("id")
    .in("brand_id", brandIds);
  const batchIds = (batchRows ?? []).map((r) => (r as { id: string }).id);
  if (batchIds.length === 0) return [];

  const JOB_COLS =
    "id, batch_id, content_item_id, prompt_version_id, provider, provider_mode, " +
    "quality_tier, resolution, duration_seconds, status, estimated_credits, " +
    "actual_credits, provider_request_id, result_url, thumbnail_url, raw_asset_id, " +
    "error_message, raw_request_json, raw_response_json, created_at, updated_at, " +
    "clip_number, clip_role";

  const { data, error } = await supabase
    .from("generation_jobs")
    .select(JOB_COLS)
    .in("batch_id", batchIds)
    .order("created_at", { ascending: false });
  if (error)
    throw new SupabaseDataError(
      "listAllGenerationJobsScopedToWorkspace",
      error,
    );
  if (!data) return [];
  // Inline the same mapper shape as generation-jobs.jobRowToView. We
  // can't import that private function so we re-shape here.
  type RawJobRow = {
    id: string;
    batch_id: string;
    content_item_id: string;
    prompt_version_id: string;
    provider: GenerationJob["provider"];
    provider_mode: string | null;
    quality_tier: GenerationJob["qualityTier"];
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
  };
  return (data as unknown as RawJobRow[]).map((r) => ({
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
  }));
}

// Demo store fallback — used by tests / dev paths that want every
// historical generation_batch in the workspace without a supabase round
// trip. Not currently consumed by the dashboard but exported for
// completeness alongside the demo readers.
export function _demoListAllBatches() {
  return _demoListGenerationBatches();
}

// ---------------------------------------------------------------------------
// Phase 1V — deterministic next-action model.
//
// Pure function over the snapshot — every action is reproducible from
// the same inputs, no clock-skew or random ordering. The dashboard
// renders the top-N by priority; users can navigate to the linked
// surface to actually do the work.
// ---------------------------------------------------------------------------

export type NextActionKind =
  | "accept_regeneration_request"
  | "fix_failed_job"
  | "submit_next_clip"
  | "stitch_completed_clips"
  | "review_generated_video"
  | "apply_generation_result"
  | "share_with_client"
  | "respond_to_feedback"
  | "approve_prompt"
  | "publish_ready_video"
  | "plan_content_calendar";

export interface NextAction {
  id: string;
  kind: NextActionKind;
  /** 1 = urgent, 2 = today, 3 = soon. Lower wins. */
  priority: 1 | 2 | 3;
  title: string;
  context: string;
  href: string;
  /** ISO timestamp used for tiebreaking (newer surfaces first within
   *  the same priority). Optional. */
  createdAt?: string;
}

const ACTION_KIND_LABELS: Record<NextActionKind, string> = {
  accept_regeneration_request: "Apply client request",
  fix_failed_job: "Fix failed job",
  submit_next_clip: "Submit next clip",
  stitch_completed_clips: "Stitch completed clips",
  review_generated_video: "Review generated video",
  apply_generation_result: "Apply generation result",
  share_with_client: "Share with client",
  respond_to_feedback: "Respond to client feedback",
  approve_prompt: "Approve prompt",
  publish_ready_video: "Ready to publish",
  plan_content_calendar: "Plan next content calendar",
};

export function nextActionLabel(kind: NextActionKind): string {
  return ACTION_KIND_LABELS[kind];
}

/** Build the deterministic next-action queue from a snapshot.
 *
 * The order is: sort by priority ASC, then createdAt DESC. Callers
 * typically take the top 8–12 for an "Inbox" feed.
 */
export function deriveOwnerNextActions(
  snapshot: OwnerOverviewSnapshot,
): NextAction[] {
  const out: NextAction[] = [];
  const {
    contentItems,
    campaigns,
    generationJobs,
    regenerationRequests,
  } = snapshot;
  const campaignById = new Map(campaigns.map((c) => [c.id, c]));
  const contentById = new Map(contentItems.map((c) => [c.id, c]));

  // 1. Open regeneration requests — direct client asks.
  for (const r of regenerationRequests) {
    if (r.status !== "open") continue;
    const ci = contentById.get(r.contentItemId);
    if (!ci) continue;
    const camp = campaignById.get(ci.campaignId);
    if (!camp) continue;
    out.push({
      id: `regen:${r.id}`,
      kind: "accept_regeneration_request",
      priority: 1,
      title: `Client request: ${r.reason ?? "see details"}`,
      context: `${ci.title}`,
      href: `/agency/campaigns/${camp.id}/content/${ci.id}/prompt`,
      createdAt: r.createdAt,
    });
  }

  // 2. Failed jobs — operator must triage.
  for (const j of generationJobs) {
    if (j.status !== "failed") continue;
    out.push({
      id: `fix:${j.id}`,
      kind: "fix_failed_job",
      priority: 1,
      title: "Generation job failed",
      context: j.errorMessage ?? j.id,
      href: `/agency/jobs/${j.id}`,
      createdAt: j.updatedAt,
    });
  }

  // 3. Multi-clip sequencing — clip N draft + clip N-1 completed.
  //    Also surfaces "all clips completed → stitch" when applicable.
  const jobsByBatch = new Map<string, GenerationJob[]>();
  for (const j of generationJobs) {
    if (!j.batchId) continue;
    if (!jobsByBatch.has(j.batchId)) jobsByBatch.set(j.batchId, []);
    jobsByBatch.get(j.batchId)!.push(j);
  }
  for (const [batchId, jobs] of jobsByBatch) {
    const multi = jobs.filter((j) => typeof j.clipNumber === "number");
    if (multi.length === 0) continue;
    multi.sort((a, b) => (a.clipNumber ?? 0) - (b.clipNumber ?? 0));
    // submit-next-clip
    for (let i = 1; i < multi.length; i++) {
      const prev = multi[i - 1];
      const curr = multi[i];
      if (prev.status === "completed" && curr.status === "draft") {
        out.push({
          id: `submit:${curr.id}`,
          kind: "submit_next_clip",
          priority: 2,
          title: `Submit clip ${curr.clipNumber} (${curr.clipRole ?? "clip"})`,
          context: `Clip ${prev.clipNumber} is complete — clip ${curr.clipNumber} is next.`,
          href: `/agency/jobs/${curr.id}`,
          createdAt: prev.updatedAt,
        });
      }
    }
    // stitch — every clip in the batch is completed
    if (multi.length >= 2 && multi.every((j) => j.status === "completed")) {
      out.push({
        id: `stitch:${batchId}`,
        kind: "stitch_completed_clips",
        priority: 2,
        title: `Stitch ${multi.length}-clip ad`,
        context: `Batch ${batchId.slice(0, 8)} has all ${multi.length} clips completed.`,
        href: `/agency/jobs/${multi[0].id}`,
        createdAt: multi[multi.length - 1].updatedAt,
      });
    }
  }

  // 4. Content-status derived actions.
  for (const ci of contentItems) {
    const camp = campaignById.get(ci.campaignId);
    if (!camp) continue;
    const hrefPrompt = `/agency/campaigns/${camp.id}/content/${ci.id}/prompt`;
    const hrefOutputs = `/agency/campaigns/${camp.id}/outputs`;
    switch (ci.status) {
      case "raw_ready":
        out.push({
          id: `review:${ci.id}`,
          kind: "review_generated_video",
          priority: 1,
          title: "Review generated video",
          context: ci.title,
          href: hrefOutputs,
        });
        break;
      case "audio_fixer_pending":
        out.push({
          id: `apply:${ci.id}`,
          kind: "apply_generation_result",
          priority: 2,
          title: "Decide on Audio Fixer",
          context: ci.title,
          href: hrefOutputs,
        });
        break;
      case "audio_fixed":
      case "ready_for_client_review":
        out.push({
          id: `share:${ci.id}`,
          kind: "share_with_client",
          priority: 1,
          title: "Share with client",
          context: ci.title,
          href: hrefOutputs,
        });
        break;
      case "changes_requested_by_client":
        out.push({
          id: `respond:${ci.id}`,
          kind: "respond_to_feedback",
          priority: 1,
          title: "Client requested changes",
          context: ci.title,
          href: hrefPrompt,
        });
        break;
      case "draft":
        out.push({
          id: `approve:${ci.id}`,
          kind: "approve_prompt",
          priority: 3,
          title: "Move prompt to approved",
          context: ci.title,
          href: hrefPrompt,
        });
        break;
      case "approved_by_client":
        out.push({
          id: `publish:${ci.id}`,
          kind: "publish_ready_video",
          priority: 3,
          title: "Client approved — ready to publish",
          context: ci.title,
          href: hrefOutputs,
        });
        break;
    }
  }

  // 5. Calendar nudge — no draft items at all across a brand suggests
  //    the next planning cycle is overdue.
  const draftsByBrand = new Map<string, number>();
  for (const ci of contentItems) {
    const camp = campaignById.get(ci.campaignId);
    if (!camp) continue;
    if (ci.status !== "draft") continue;
    draftsByBrand.set(camp.brandId, (draftsByBrand.get(camp.brandId) ?? 0) + 1);
  }
  for (const b of snapshot.brands) {
    if ((draftsByBrand.get(b.id) ?? 0) === 0) {
      out.push({
        id: `calendar:${b.id}`,
        kind: "plan_content_calendar",
        priority: 3,
        title: `Plan next calendar for ${b.name}`,
        context: "No drafts queued — the next planning cycle is open.",
        href: `/agency/brands/${b.id}`,
      });
    }
  }

  out.sort((a, b) => {
    if (a.priority !== b.priority) return a.priority - b.priority;
    const aT = a.createdAt ?? "";
    const bT = b.createdAt ?? "";
    return bT.localeCompare(aT);
  });
  return out;
}

// ---------------------------------------------------------------------------
// Phase 1V — recent activity feed (composed from existing data).
// ---------------------------------------------------------------------------

export type ActivityKind =
  | "job_created"
  | "job_submitted"
  | "job_completed"
  | "job_failed"
  | "regeneration_request_opened"
  | "regeneration_request_accepted"
  | "content_request_received"
  | "asset_shared"
  | "agent_run_completed"
  | "agent_run_failed";

export interface ActivityEntry {
  id: string;
  kind: ActivityKind;
  at: string;
  title: string;
  detail: string;
  href?: string;
}

export function deriveRecentActivity(
  snapshot: OwnerOverviewSnapshot,
  limit = 12,
): ActivityEntry[] {
  const out: ActivityEntry[] = [];
  for (const j of snapshot.generationJobs) {
    let kind: ActivityKind = "job_created";
    if (j.status === "submitted") kind = "job_submitted";
    else if (j.status === "completed") kind = "job_completed";
    else if (j.status === "failed") kind = "job_failed";
    out.push({
      id: `job:${j.id}:${j.status}`,
      kind,
      at: j.updatedAt,
      title: `Job ${j.status}`,
      detail:
        (j.clipNumber ? `clip ${j.clipNumber} · ` : "") +
        `${j.provider}${j.providerMode ? ` · ${j.providerMode}` : ""}`,
      href: `/agency/jobs/${j.id}`,
    });
  }
  for (const r of snapshot.regenerationRequests) {
    out.push({
      id: `regen:${r.id}`,
      kind:
        r.status === "accepted"
          ? "regeneration_request_accepted"
          : "regeneration_request_opened",
      at: r.updatedAt,
      title:
        r.status === "accepted"
          ? "Client request accepted"
          : "Client requested a change",
      detail: r.body.slice(0, 140),
    });
  }
  for (const cr of snapshot.contentRequests) {
    out.push({
      id: `creq:${cr.id}`,
      kind: "content_request_received",
      at: cr.createdAt,
      title: "Client next-week request",
      detail: cr.body.slice(0, 140),
    });
  }
  for (const ci of snapshot.contentItems) {
    if (ci.status === "shared_with_client" && ci.clientSafeVideoUrl) {
      out.push({
        id: `share:${ci.id}`,
        kind: "asset_shared",
        at: ci.scheduledFor,
        title: "Shared with client",
        detail: ci.title,
      });
    }
  }
  // Phase 1Y — agent runs in the activity feed. Agent runs are pure
  // planning, never paid; they appear here so the operator sees the
  // tempo of agent use without leaving the dashboard.
  for (const r of snapshot.agentRuns) {
    if (r.status !== "completed" && r.status !== "failed") continue;
    const url =
      r.input && typeof r.input === "object"
        ? String(
            (r.input as Record<string, unknown>).productUrl ??
              "(no product url)",
          )
        : "(no product url)";
    out.push({
      id: `agent:${r.id}`,
      kind:
        r.status === "completed"
          ? "agent_run_completed"
          : "agent_run_failed",
      at: r.updatedAt,
      title:
        r.status === "completed"
          ? "Brand Analysis agent run"
          : "Brand Analysis agent run failed",
      detail:
        r.status === "failed" && r.errorMessage
          ? r.errorMessage.slice(0, 140)
          : url.slice(0, 140),
      href: "/agency/agents/brand-analysis",
    });
  }
  out.sort((a, b) => b.at.localeCompare(a.at));
  return out.slice(0, limit);
}
