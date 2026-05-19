// Yuvo Studio — Phase 1F generation_jobs operator actions (MOCK-ONLY).
//
// OPERATOR-ONLY. Every action validates the caller's persona is operator
// (or demo mode) before mutating. NONE of these actions calls Enhancor,
// Seedance, Audio Fixer, or any other paid provider — they only record
// operator intent in the generation_batches / generation_jobs /
// generation_job_events tables landed by migration 005.
//
// Four actions cover Phase 1F:
//   1. createMockGenerationJobFromPromptVersionAction
//        — operator opt-in: "I would generate this approved prompt."
//        Creates a batch + a single job with status='draft'. Estimates
//        credits from the prompt version's quality_tier × 15s default
//        (or the duration_sec on the parent content_item, if present).
//   2. markGenerationJobQueuedAction
//        — flips status from draft → queued. Still no API call. This is
//        the dry-run analogue of "operator clicks Submit"; Phase 1G
//        will replace it with the real submission action.
//   3. cancelGenerationJobAction
//        — flips status → cancelled. Logs a cancelled event.
//   4. addGenerationJobNoteAction
//        — append-only operator_note event on the job timeline.
//
// In all four cases NO Enhancor / Seedance / Audio Fixer call is made.

"use server";

import { revalidatePath } from "next/cache";
import { randomUUID } from "crypto";
import {
  _demoFindGenerationJobById,
  _demoPushGenerationBatch,
  _demoPushGenerationJob,
  _demoPushGenerationJobEvent,
  _demoUpdateGenerationJob,
  type GenerationJobStatus,
} from "@/lib/data/generation-jobs";
import {
  _demoFindPromptVersionById,
  type PromptVersionQualityTier,
} from "@/lib/data/prompt-versions";
import { getDataSource } from "@/lib/data/_source";
import {
  getEstimatedSeedanceCredits,
} from "@/lib/quality-tiers";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import {
  planAdDuration,
  toClipPlanJson,
  TARGET_DURATION_OPTIONS,
  type TargetDurationSeconds,
} from "@/lib/planning/duration-plan";

export interface GenerationJobActionResult {
  ok: boolean;
  message?: string;
  error?: string;
  jobId?: string;
  batchId?: string;
  /** Phase 1R — multi-clip draft plan results. */
  jobIds?: string[];
  clipCount?: number;
  totalEstimatedCredits?: number;
}

const DEFAULT_DURATION_SEC = 15;
const DEFAULT_PROVIDER = "enhancor_seedance" as const;
const DEFAULT_PROVIDER_MODE = "ugc";

const RESOLUTION_BY_TIER: Record<PromptVersionQualityTier, "480p" | "720p" | "1080p"> = {
  draft_480p: "480p",
  standard_720p: "720p",
  premium_1080p: "1080p",
};

async function requireOperator(): Promise<
  { userId: string | null } | { error: string }
> {
  if (getDataSource() === "demo") return { userId: null };
  if (!hasSupabaseEnv()) return { error: "Supabase auth is not configured." };
  const persona = await getCurrentPersona();
  if (!persona) return { error: "Please sign in first." };
  if (persona.kind !== "operator") {
    return { error: "Operator access required." };
  }
  return { userId: persona.userId };
}

function revalidateJobsPaths(jobId?: string, contentId?: string, campaignId?: string): void {
  revalidatePath("/agency/jobs");
  if (jobId) revalidatePath(`/agency/jobs/${jobId}`);
  if (campaignId) revalidatePath(`/agency/campaigns/${campaignId}/outputs`);
  if (campaignId && contentId) {
    revalidatePath(`/agency/campaigns/${campaignId}/content/${contentId}/prompt`);
  }
}

// ---------------------------------------------------------------------------
// 1. createMockGenerationJobFromPromptVersionAction
// ---------------------------------------------------------------------------
export async function createMockGenerationJobFromPromptVersionAction(input: {
  promptVersionId: string;
  campaignId: string;
  contentId: string;
}): Promise<GenerationJobActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const operatorId = "userId" in auth ? auth.userId : null;

  if (getDataSource() === "demo") {
    const promptVersion = _demoFindPromptVersionById(input.promptVersionId);
    if (!promptVersion) return { ok: false, error: "Prompt version not found." };
    if (promptVersion.status !== "approved_for_generation") {
      return {
        ok: false,
        error:
          "Only prompt versions in status approved_for_generation can be turned into jobs.",
      };
    }
    const durationSec = DEFAULT_DURATION_SEC;
    const estimated = getEstimatedSeedanceCredits(
      promptVersion.qualityTier,
      durationSec,
    );
    const now = new Date().toISOString();
    const batchId = randomUUID();
    const jobId = randomUUID();
    _demoPushGenerationBatch({
      id: batchId,
      workspaceId: "ws_yuvo",
      brandId: "brand_pai",
      campaignId: input.campaignId,
      createdBy: operatorId,
      label: `Mock batch from ${promptVersion.label ?? "prompt v" + promptVersion.versionNumber}`,
      status: "ready",
      totalEstimatedCredits: estimated,
      totalActualCredits: null,
      createdAt: now,
      updatedAt: now,
    });
    _demoPushGenerationJob({
      id: jobId,
      batchId,
      contentItemId: input.contentId,
      promptVersionId: input.promptVersionId,
      provider: DEFAULT_PROVIDER,
      providerMode: DEFAULT_PROVIDER_MODE,
      qualityTier: promptVersion.qualityTier,
      resolution: RESOLUTION_BY_TIER[promptVersion.qualityTier],
      durationSeconds: durationSec,
      status: "draft",
      estimatedCredits: estimated,
      actualCredits: null,
      providerRequestId: null,
      resultUrl: null,
      thumbnailUrl: null,
      rawAssetId: null,
      errorMessage: null,
      rawRequestJson: null,
      rawResponseJson: null,
      createdAt: now,
      updatedAt: now,
    });
    _demoPushGenerationJobEvent({
      id: randomUUID(),
      generationJobId: jobId,
      eventType: "created",
      message:
        "Mock generation job created from approved prompt version. No paid call has been made.",
      rawPayload: null,
      createdAt: now,
    });
    revalidateJobsPaths(jobId, input.contentId, input.campaignId);
    return {
      ok: true,
      message: "Mock generation job created. No paid call was made.",
      jobId,
      batchId,
    };
  }

  // Supabase path.
  try {
    const admin = getServiceRoleSupabase();
    // Look up the prompt version + walk to its content_item + campaign +
    // brand + workspace so we have the FK chain to write the batch.
    const { data: promptRow, error: promptErr } = await admin
      .from("prompt_versions")
      .select(
        "id, content_item_id, quality_tier, status, label, version_number",
      )
      .eq("id", input.promptVersionId)
      .maybeSingle();
    if (promptErr) return { ok: false, error: promptErr.message };
    const prompt = promptRow as {
      id: string;
      content_item_id: string;
      quality_tier: PromptVersionQualityTier;
      status: string;
      label: string | null;
      version_number: number;
    } | null;
    if (!prompt) return { ok: false, error: "Prompt version not found." };
    if (prompt.status !== "approved_for_generation") {
      return {
        ok: false,
        error:
          "Only prompt versions in status approved_for_generation can be turned into jobs.",
      };
    }

    const { data: contentRow, error: contentErr } = await admin
      .from("content_items")
      .select("id, campaign_id, duration_sec")
      .eq("id", prompt.content_item_id)
      .maybeSingle();
    if (contentErr) return { ok: false, error: contentErr.message };
    const content = contentRow as {
      id: string;
      campaign_id: string;
      duration_sec: number | null;
    } | null;
    if (!content) return { ok: false, error: "Content item not found." };

    const { data: campaignRow, error: campaignErr } = await admin
      .from("campaigns")
      .select("id, brand_id")
      .eq("id", content.campaign_id)
      .maybeSingle();
    if (campaignErr) return { ok: false, error: campaignErr.message };
    const campaign = campaignRow as { id: string; brand_id: string } | null;
    if (!campaign) return { ok: false, error: "Campaign not found." };

    const { data: brandRow, error: brandErr } = await admin
      .from("brands")
      .select("id, workspace_id")
      .eq("id", campaign.brand_id)
      .maybeSingle();
    if (brandErr) return { ok: false, error: brandErr.message };
    const brand = brandRow as { id: string; workspace_id: string } | null;
    if (!brand) return { ok: false, error: "Brand not found." };

    const durationSec = content.duration_sec ?? DEFAULT_DURATION_SEC;
    const estimated = getEstimatedSeedanceCredits(prompt.quality_tier, durationSec);

    const { data: batchInsert, error: batchErr } = await admin
      .from("generation_batches")
      .insert({
        workspace_id: brand.workspace_id,
        brand_id: brand.id,
        campaign_id: campaign.id,
        created_by: operatorId,
        label: `Mock batch from ${prompt.label ?? "prompt v" + prompt.version_number}`,
        status: "ready",
        total_estimated_credits: estimated,
      })
      .select("id")
      .maybeSingle();
    if (batchErr) return { ok: false, error: batchErr.message };
    const batchId = (batchInsert as { id: string } | null)?.id;
    if (!batchId) return { ok: false, error: "Batch insert returned no id." };

    const { data: jobInsert, error: jobErr } = await admin
      .from("generation_jobs")
      .insert({
        batch_id: batchId,
        content_item_id: content.id,
        prompt_version_id: prompt.id,
        provider: DEFAULT_PROVIDER,
        provider_mode: DEFAULT_PROVIDER_MODE,
        quality_tier: prompt.quality_tier,
        resolution: RESOLUTION_BY_TIER[prompt.quality_tier],
        duration_seconds: durationSec,
        status: "draft",
        estimated_credits: estimated,
      })
      .select("id")
      .maybeSingle();
    if (jobErr) return { ok: false, error: jobErr.message };
    const jobId = (jobInsert as { id: string } | null)?.id;
    if (!jobId) return { ok: false, error: "Job insert returned no id." };

    const { error: eventErr } = await admin
      .from("generation_job_events")
      .insert({
        generation_job_id: jobId,
        event_type: "created",
        message:
          "Mock generation job created from approved prompt version. No paid call has been made.",
      });
    if (eventErr) {
      console.warn(
        "[yuvo] generation_job_events insert failed:",
        eventErr.message,
      );
    }

    revalidateJobsPaths(jobId, content.id, campaign.id);
    return {
      ok: true,
      message: "Mock generation job created. No paid call was made.",
      jobId,
      batchId,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// ---------------------------------------------------------------------------
// 1b. createDurationPlanDraftAction  (Phase 1R — multi-clip DRAFT only)
//
// Persists a whole-ad plan: ONE generation_batches row (carrying
// target_duration_seconds + clip_plan) and N draft generation_jobs
// rows (one per clip, with clip_number + clip_role). Creates a
// `created` event per job. NEVER submits, NEVER calls Enhancor /
// Seedance / Audio Fixer. Every job lands status='draft' with
// provider_request_id / raw_request_json / raw_response_json /
// actual_credits all NULL.
//
// Requires migration 007's columns (target_duration_seconds, clip_plan,
// clip_number, clip_role). In supabase mode, if 007 is not yet applied,
// the batch/job INSERT fails fast and the action returns a clear
// "migration 007 not applied" error WITHOUT creating partial state
// (the batch insert is the first write; if its extra columns are
// rejected, nothing else runs).
// ---------------------------------------------------------------------------
export async function createDurationPlanDraftAction(input: {
  promptVersionId: string;
  campaignId: string;
  contentId: string;
  targetDurationSeconds: number;
}): Promise<GenerationJobActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const operatorId = "userId" in auth ? auth.userId : null;

  if (
    !TARGET_DURATION_OPTIONS.includes(
      input.targetDurationSeconds as TargetDurationSeconds,
    )
  ) {
    return {
      ok: false,
      error: `targetDurationSeconds must be one of ${TARGET_DURATION_OPTIONS.join(", ")}.`,
    };
  }
  const target = input.targetDurationSeconds as TargetDurationSeconds;

  // -------- demo mode: full in-memory plan (no DB, no paid call) -------- #
  if (getDataSource() === "demo") {
    const promptVersion = _demoFindPromptVersionById(input.promptVersionId);
    if (!promptVersion) return { ok: false, error: "Prompt version not found." };
    if (promptVersion.status !== "approved_for_generation") {
      return {
        ok: false,
        error:
          "Only prompt versions in status approved_for_generation can be planned.",
      };
    }
    const plan = planAdDuration({
      targetDurationSeconds: target,
      qualityTier: promptVersion.qualityTier,
      sharedNegativePrompt: promptVersion.negativePrompt ?? "",
      labelHallucinationRisk: true,
    });
    const now = new Date().toISOString();
    const batchId = randomUUID();
    _demoPushGenerationBatch({
      id: batchId,
      workspaceId: "ws_yuvo",
      brandId: "brand_pai",
      campaignId: input.campaignId,
      createdBy: operatorId,
      label: `${target}s draft plan from ${promptVersion.label ?? "prompt v" + promptVersion.versionNumber}`,
      status: "ready",
      totalEstimatedCredits: plan.totalEstimatedCredits,
      totalActualCredits: null,
      targetDurationSeconds: target,
      clipPlan: toClipPlanJson(plan),
      createdAt: now,
      updatedAt: now,
    });
    const jobIds: string[] = [];
    for (const clip of plan.clips) {
      const jobId = randomUUID();
      jobIds.push(jobId);
      _demoPushGenerationJob({
        id: jobId,
        batchId,
        contentItemId: input.contentId,
        promptVersionId: input.promptVersionId,
        provider: DEFAULT_PROVIDER,
        providerMode: DEFAULT_PROVIDER_MODE,
        qualityTier: promptVersion.qualityTier,
        resolution: RESOLUTION_BY_TIER[promptVersion.qualityTier],
        durationSeconds: clip.durationSeconds,
        status: "draft",
        estimatedCredits: clip.estimatedCredits,
        actualCredits: null,
        providerRequestId: null,
        resultUrl: null,
        thumbnailUrl: null,
        rawAssetId: null,
        errorMessage: null,
        rawRequestJson: null,
        rawResponseJson: null,
        clipNumber: clip.clipNumber,
        clipRole: clip.continuationRole,
        createdAt: now,
        updatedAt: now,
      });
      _demoPushGenerationJobEvent({
        id: randomUUID(),
        generationJobId: jobId,
        eventType: "created",
        message: `Draft clip ${clip.clipNumber}/${plan.clipCount} (${clip.durationSeconds}s, ${clip.continuationRole}) created from duration plan. No paid call has been made.`,
        rawPayload: null,
        createdAt: now,
      });
    }
    revalidateJobsPaths(undefined, input.contentId, input.campaignId);
    return {
      ok: true,
      message: `Created a ${target}s draft plan: 1 batch + ${plan.clipCount} draft clip job(s). No paid call was made.`,
      batchId,
      jobIds,
      clipCount: plan.clipCount,
      totalEstimatedCredits: plan.totalEstimatedCredits,
    };
  }

  // -------- supabase mode -------- #
  try {
    const admin = getServiceRoleSupabase();
    const { data: promptRow, error: promptErr } = await admin
      .from("prompt_versions")
      .select(
        "id, content_item_id, quality_tier, status, label, version_number, negative_prompt",
      )
      .eq("id", input.promptVersionId)
      .maybeSingle();
    if (promptErr) return { ok: false, error: promptErr.message };
    const prompt = promptRow as {
      id: string;
      content_item_id: string;
      quality_tier: PromptVersionQualityTier;
      status: string;
      label: string | null;
      version_number: number;
      negative_prompt: string | null;
    } | null;
    if (!prompt) return { ok: false, error: "Prompt version not found." };
    if (prompt.status !== "approved_for_generation") {
      return {
        ok: false,
        error:
          "Only prompt versions in status approved_for_generation can be planned.",
      };
    }

    const { data: contentRow, error: contentErr } = await admin
      .from("content_items")
      .select("id, campaign_id")
      .eq("id", prompt.content_item_id)
      .maybeSingle();
    if (contentErr) return { ok: false, error: contentErr.message };
    const content = contentRow as {
      id: string;
      campaign_id: string;
    } | null;
    if (!content) return { ok: false, error: "Content item not found." };

    const { data: campaignRow, error: campaignErr } = await admin
      .from("campaigns")
      .select("id, brand_id")
      .eq("id", content.campaign_id)
      .maybeSingle();
    if (campaignErr) return { ok: false, error: campaignErr.message };
    const campaign = campaignRow as { id: string; brand_id: string } | null;
    if (!campaign) return { ok: false, error: "Campaign not found." };

    const { data: brandRow, error: brandErr } = await admin
      .from("brands")
      .select("id, workspace_id")
      .eq("id", campaign.brand_id)
      .maybeSingle();
    if (brandErr) return { ok: false, error: brandErr.message };
    const brand = brandRow as { id: string; workspace_id: string } | null;
    if (!brand) return { ok: false, error: "Brand not found." };

    const plan = planAdDuration({
      targetDurationSeconds: target,
      qualityTier: prompt.quality_tier,
      sharedNegativePrompt: prompt.negative_prompt ?? "",
      labelHallucinationRisk: true,
    });

    const { data: batchInsert, error: batchErr } = await admin
      .from("generation_batches")
      .insert({
        workspace_id: brand.workspace_id,
        brand_id: brand.id,
        campaign_id: campaign.id,
        created_by: operatorId,
        label: `${target}s draft plan from ${prompt.label ?? "prompt v" + prompt.version_number}`,
        status: "ready",
        total_estimated_credits: plan.totalEstimatedCredits,
        target_duration_seconds: target,
        clip_plan: toClipPlanJson(plan),
      })
      .select("id")
      .maybeSingle();
    if (batchErr) {
      // Most likely cause: migration 007 not applied yet (the
      // target_duration_seconds / clip_plan columns don't exist).
      // No row was created — fail clean.
      return {
        ok: false,
        error:
          `Could not create the plan batch: ${batchErr.message}. ` +
          `If this mentions a missing column, apply migration 007 ` +
          `(supabase/migrations/007_multi_clip_duration.sql) first.`,
      };
    }
    const batchId = (batchInsert as { id: string } | null)?.id;
    if (!batchId) return { ok: false, error: "Batch insert returned no id." };

    const jobIds: string[] = [];
    for (const clip of plan.clips) {
      const { data: jobInsert, error: jobErr } = await admin
        .from("generation_jobs")
        .insert({
          batch_id: batchId,
          content_item_id: content.id,
          prompt_version_id: prompt.id,
          provider: DEFAULT_PROVIDER,
          provider_mode: DEFAULT_PROVIDER_MODE,
          quality_tier: prompt.quality_tier,
          resolution: RESOLUTION_BY_TIER[prompt.quality_tier],
          duration_seconds: clip.durationSeconds,
          status: "draft",
          estimated_credits: clip.estimatedCredits,
          clip_number: clip.clipNumber,
          clip_role: clip.continuationRole,
        })
        .select("id")
        .maybeSingle();
      if (jobErr) {
        return {
          ok: false,
          error:
            `Batch ${batchId} created but clip ${clip.clipNumber} job ` +
            `insert failed: ${jobErr.message}. Apply migration 007 if ` +
            `this mentions clip_number/clip_role.`,
          batchId,
          jobIds,
        };
      }
      const jobId = (jobInsert as { id: string } | null)?.id;
      if (!jobId) return { ok: false, error: "Job insert returned no id." };
      jobIds.push(jobId);

      const { error: eventErr } = await admin
        .from("generation_job_events")
        .insert({
          generation_job_id: jobId,
          event_type: "created",
          message: `Draft clip ${clip.clipNumber}/${plan.clipCount} (${clip.durationSeconds}s, ${clip.continuationRole}) created from duration plan. No paid call has been made.`,
        });
      if (eventErr) {
        console.warn(
          "[yuvo] generation_job_events insert failed:",
          eventErr.message,
        );
      }
    }

    revalidateJobsPaths(undefined, content.id, campaign.id);
    return {
      ok: true,
      message: `Created a ${target}s draft plan: 1 batch + ${plan.clipCount} draft clip job(s). No paid call was made.`,
      batchId,
      jobIds,
      clipCount: plan.clipCount,
      totalEstimatedCredits: plan.totalEstimatedCredits,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// ---------------------------------------------------------------------------
// 2. markGenerationJobQueuedAction  (still no API call)
// ---------------------------------------------------------------------------
export async function markGenerationJobQueuedAction(input: {
  jobId: string;
}): Promise<GenerationJobActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };

  if (getDataSource() === "demo") {
    const existing = _demoFindGenerationJobById(input.jobId);
    if (!existing) return { ok: false, error: "Job not found." };
    if (existing.status === "cancelled") {
      return { ok: false, error: "Cannot queue a cancelled job." };
    }
    const updated = _demoUpdateGenerationJob(input.jobId, {
      status: "queued" as GenerationJobStatus,
    });
    if (!updated) return { ok: false, error: "Job not found." };
    _demoPushGenerationJobEvent({
      id: randomUUID(),
      generationJobId: input.jobId,
      eventType: "queued",
      message:
        "Operator marked job queued (mock). Phase 1G will replace this with the real Enhancor submission.",
      rawPayload: null,
      createdAt: new Date().toISOString(),
    });
    revalidateJobsPaths(input.jobId);
    return {
      ok: true,
      message: "Marked queued (mock). NO paid call was made.",
      jobId: input.jobId,
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data: existingRow, error: existingErr } = await admin
      .from("generation_jobs")
      .select("status")
      .eq("id", input.jobId)
      .maybeSingle();
    if (existingErr) return { ok: false, error: existingErr.message };
    const existing = existingRow as { status: string } | null;
    if (!existing) return { ok: false, error: "Job not found." };
    if (existing.status === "cancelled") {
      return { ok: false, error: "Cannot queue a cancelled job." };
    }
    const { error } = await admin
      .from("generation_jobs")
      .update({ status: "queued" })
      .eq("id", input.jobId);
    if (error) return { ok: false, error: error.message };

    const { error: eventErr } = await admin
      .from("generation_job_events")
      .insert({
        generation_job_id: input.jobId,
        event_type: "queued",
        message:
          "Operator marked job queued (mock). Phase 1G will replace this with the real Enhancor submission.",
      });
    if (eventErr) {
      console.warn(
        "[yuvo] generation_job_events insert failed:",
        eventErr.message,
      );
    }
    revalidateJobsPaths(input.jobId);
    return {
      ok: true,
      message: "Marked queued (mock). NO paid call was made.",
      jobId: input.jobId,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// ---------------------------------------------------------------------------
// 3. cancelGenerationJobAction
// ---------------------------------------------------------------------------
export async function cancelGenerationJobAction(input: {
  jobId: string;
  reason?: string;
}): Promise<GenerationJobActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };

  if (getDataSource() === "demo") {
    const existing = _demoFindGenerationJobById(input.jobId);
    if (!existing) return { ok: false, error: "Job not found." };
    _demoUpdateGenerationJob(input.jobId, {
      status: "cancelled" as GenerationJobStatus,
    });
    _demoPushGenerationJobEvent({
      id: randomUUID(),
      generationJobId: input.jobId,
      eventType: "cancelled",
      message: input.reason ?? "Operator cancelled job.",
      rawPayload: null,
      createdAt: new Date().toISOString(),
    });
    revalidateJobsPaths(input.jobId);
    return { ok: true, message: "Job cancelled.", jobId: input.jobId };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { error } = await admin
      .from("generation_jobs")
      .update({ status: "cancelled" })
      .eq("id", input.jobId);
    if (error) return { ok: false, error: error.message };

    const { error: eventErr } = await admin
      .from("generation_job_events")
      .insert({
        generation_job_id: input.jobId,
        event_type: "cancelled",
        message: input.reason ?? "Operator cancelled job.",
      });
    if (eventErr) {
      console.warn(
        "[yuvo] generation_job_events insert failed:",
        eventErr.message,
      );
    }
    revalidateJobsPaths(input.jobId);
    return { ok: true, message: "Job cancelled.", jobId: input.jobId };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// ---------------------------------------------------------------------------
// 4. addGenerationJobNoteAction
// ---------------------------------------------------------------------------
export async function addGenerationJobNoteAction(input: {
  jobId: string;
  message: string;
}): Promise<GenerationJobActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const trimmed = input.message.trim();
  if (trimmed.length === 0) {
    return { ok: false, error: "Note cannot be empty." };
  }

  if (getDataSource() === "demo") {
    _demoPushGenerationJobEvent({
      id: randomUUID(),
      generationJobId: input.jobId,
      eventType: "operator_note",
      message: trimmed,
      rawPayload: null,
      createdAt: new Date().toISOString(),
    });
    revalidateJobsPaths(input.jobId);
    return { ok: true, message: "Note added.", jobId: input.jobId };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { error } = await admin
      .from("generation_job_events")
      .insert({
        generation_job_id: input.jobId,
        event_type: "operator_note",
        message: trimmed,
      });
    if (error) return { ok: false, error: error.message };
    revalidateJobsPaths(input.jobId);
    return { ok: true, message: "Note added.", jobId: input.jobId };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}
