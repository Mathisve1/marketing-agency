// Yuvo Studio — Phase 1E prompt_versions operator actions.
//
// OPERATOR-ONLY. Every action validates the caller's persona is
// operator (or demo mode) before mutating.
//
// Three actions cover the editor flow:
//   - savePromptVersionDraftAction
//       Update an existing prompt_versions row in-place. Used when the
//       operator is still iterating.
//   - markPromptVersionApprovedForGenerationAction
//       Flip status to 'approved_for_generation' and mark any sibling
//       previously-approved version 'superseded'. NO paid call is
//       made — this only records intent.
//   - createPromptVersionFromRegenerationRequestAction
//       Fork a new prompt_versions row from an existing version, link
//       it to the regeneration_request, set status='operator_editing',
//       and (best-effort) mark the regeneration_request 'accepted'.
//
// In all cases NO Enhancor / Seedance / Audio Fixer call is made. The
// downstream generation handoff is Phase 1F+.

"use server";

import { revalidatePath } from "next/cache";
import { randomUUID } from "crypto";
import {
  _demoUpsertPromptVersion,
  _demoFindPromptVersionById,
  _demoListPromptVersions,
  type PromptVersion,
  type PromptVersionQualityTier,
  type PromptVersionStatus,
} from "@/lib/data/prompt-versions";
import { _demoUpdateRegenerationRequest } from "@/lib/data/regeneration-requests";
import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";

export interface PromptVersionActionResult {
  ok: boolean;
  message?: string;
  error?: string;
  promptVersionId?: string;
}

const EDITABLE_FIELDS = [
  "label",
  "hook",
  "script",
  "promptBody",
  "negativePrompt",
  "scenePlan",
  "creatorDirection",
  "productConstraints",
  "qualityTier",
  "notes",
] as const;

type EditableField = (typeof EDITABLE_FIELDS)[number];

const FIELD_TO_COLUMN: Record<EditableField, string> = {
  label: "label",
  hook: "hook",
  script: "script",
  promptBody: "prompt_body",
  negativePrompt: "negative_prompt",
  scenePlan: "scene_plan",
  creatorDirection: "creator_direction",
  productConstraints: "product_constraints",
  qualityTier: "quality_tier",
  notes: "notes",
};

const ALLOWED_TIERS: PromptVersionQualityTier[] = [
  "draft_480p",
  "standard_720p",
  "premium_1080p",
];

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

function revalidatePromptPaths(campaignId: string, contentId: string): void {
  revalidatePath(`/agency/campaigns/${campaignId}/outputs`);
  revalidatePath(
    `/agency/campaigns/${campaignId}/content/${contentId}/prompt`,
  );
}

interface DraftInput {
  promptVersionId: string;
  campaignId: string;
  contentId: string;
  label?: string;
  hook?: string;
  script?: string;
  promptBody?: string;
  negativePrompt?: string;
  scenePlan?: string;
  creatorDirection?: string;
  productConstraints?: string;
  qualityTier?: PromptVersionQualityTier;
  notes?: string;
}

function pickFields(input: DraftInput): Partial<PromptVersion> {
  const out: Partial<PromptVersion> = {};
  for (const field of EDITABLE_FIELDS) {
    const value = input[field];
    if (value === undefined) continue;
    if (field === "qualityTier") {
      if (!ALLOWED_TIERS.includes(value as PromptVersionQualityTier)) continue;
      out.qualityTier = value as PromptVersionQualityTier;
    } else {
      // narrow: each EditableField (except qualityTier) maps to a
      // string|null column. assignment is safe because the source
      // shape only emits strings via the form.
      (out as Record<string, string | null>)[field] =
        typeof value === "string" ? value : null;
    }
  }
  return out;
}

export async function savePromptVersionDraftAction(
  input: DraftInput,
): Promise<PromptVersionActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const fields = pickFields(input);

  if (getDataSource() === "demo") {
    const existing = _demoFindPromptVersionById(input.promptVersionId);
    if (!existing) return { ok: false, error: "Prompt version not found." };
    _demoUpsertPromptVersion({
      ...existing,
      ...fields,
      status:
        existing.status === "approved_for_generation"
          ? "approved_for_generation"
          : "operator_editing",
      updatedAt: new Date().toISOString(),
    });
    revalidatePromptPaths(input.campaignId, input.contentId);
    return {
      ok: true,
      message: "Draft saved (demo mode — in-memory only).",
      promptVersionId: input.promptVersionId,
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const update: Record<string, unknown> = {};
    for (const field of EDITABLE_FIELDS) {
      if (fields[field] === undefined) continue;
      update[FIELD_TO_COLUMN[field]] = fields[field];
    }
    // Keep status sticky on approved-for-generation; otherwise bump to
    // operator_editing so the queue reflects active iteration.
    const { data: existingRow, error: existingErr } = await admin
      .from("prompt_versions")
      .select("status")
      .eq("id", input.promptVersionId)
      .maybeSingle();
    if (existingErr) return { ok: false, error: existingErr.message };
    const existing = existingRow as { status: PromptVersionStatus } | null;
    if (!existing) return { ok: false, error: "Prompt version not found." };
    if (existing.status !== "approved_for_generation") {
      update.status = "operator_editing";
    }

    const { error } = await admin
      .from("prompt_versions")
      .update(update)
      .eq("id", input.promptVersionId);
    if (error) return { ok: false, error: error.message };
    revalidatePromptPaths(input.campaignId, input.contentId);
    return {
      ok: true,
      message: "Draft saved.",
      promptVersionId: input.promptVersionId,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function markPromptVersionApprovedForGenerationAction(input: {
  promptVersionId: string;
  campaignId: string;
  contentId: string;
}): Promise<PromptVersionActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };

  if (getDataSource() === "demo") {
    const target = _demoFindPromptVersionById(input.promptVersionId);
    if (!target) return { ok: false, error: "Prompt version not found." };
    const now = new Date().toISOString();
    // Supersede any sibling previously-approved version.
    for (const sibling of _demoListPromptVersions()) {
      if (
        sibling.contentItemId === target.contentItemId &&
        sibling.id !== target.id &&
        sibling.status === "approved_for_generation"
      ) {
        _demoUpsertPromptVersion({
          ...sibling,
          status: "superseded",
          updatedAt: now,
        });
      }
    }
    _demoUpsertPromptVersion({
      ...target,
      status: "approved_for_generation",
      updatedAt: now,
    });
    revalidatePromptPaths(input.campaignId, input.contentId);
    return {
      ok: true,
      message:
        "Marked approved for generation. NO paid call was made — this only records intent.",
      promptVersionId: input.promptVersionId,
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data: targetRow, error: targetErr } = await admin
      .from("prompt_versions")
      .select("content_item_id")
      .eq("id", input.promptVersionId)
      .maybeSingle();
    if (targetErr) return { ok: false, error: targetErr.message };
    const target = targetRow as { content_item_id: string } | null;
    if (!target) return { ok: false, error: "Prompt version not found." };

    // Supersede sibling approved-for-generation rows first so the
    // partial unique index does not reject the flip.
    const { error: supErr } = await admin
      .from("prompt_versions")
      .update({ status: "superseded" })
      .eq("content_item_id", target.content_item_id)
      .eq("status", "approved_for_generation")
      .neq("id", input.promptVersionId);
    if (supErr) return { ok: false, error: supErr.message };

    const { error } = await admin
      .from("prompt_versions")
      .update({ status: "approved_for_generation" })
      .eq("id", input.promptVersionId);
    if (error) return { ok: false, error: error.message };
    revalidatePromptPaths(input.campaignId, input.contentId);
    return {
      ok: true,
      message:
        "Marked approved for generation. NO paid call was made — this only records intent.",
      promptVersionId: input.promptVersionId,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// ---------------------------------------------------------------------------
// Phase 1X — create a new draft (operator_editing) prompt version from a
// Brand Analysis agent draft.
//
// Hard rules:
//   - status is ALWAYS operator_editing. Never approved_for_generation.
//     This action cannot trigger any paid call.
//   - No generation_jobs row is inserted.
//   - No provider is contacted.
//   - parent_version_id = the latest existing version on the content item
//     (if any), so the editor's "Version history" displays the lineage.
//   - source_regeneration_request_id stays NULL (this isn't a regen
//     request — it's an agent draft). The agent provenance is recorded
//     in `notes` so it stays human-readable inside the existing editor.
// ---------------------------------------------------------------------------
export interface AgentDraftPromptInput {
  contentItemId: string;
  /** Selected agent prompt-draft fields. All strings (no nulls); the
   *  client UI is responsible for filling them from the agent output. */
  label: string;
  hook: string;
  script: string;
  promptBody: string;
  scenePlan: string;
  creatorDirection: string;
  productConstraints: string;
  negativePrompt: string;
  /** Free-text caller-side note. Combined with the source-summary into
   *  prompt_versions.notes so the editor surfaces the provenance. */
  callerNotes?: string;
  /** Optional source metadata recorded in `notes`. None of these fields
   *  is a schema column; they're stitched into the notes text. */
  sourceMetadata?: {
    productUrl?: string;
    agentType?: "brand_analysis_ugc_prompt_planning";
    matchedNiche?: string;
    /** Phase 1Y — when the Brand Analysis agent run was persisted, the
     *  resulting agent_runs.id flows through here. We don't add a new
     *  column to prompt_versions — the id goes into the notes text so
     *  it remains visible in the existing editor. */
    agentRunId?: string;
  };
}

const PHASE_1X_QUALITY_TIER: PromptVersionQualityTier = "standard_720p";

function buildAgentNotes(input: AgentDraftPromptInput): string {
  const meta = input.sourceMetadata ?? {};
  const lines = [
    `[agent draft] source: ${meta.agentType ?? "brand_analysis_ugc_prompt_planning"}`,
    meta.agentRunId ? `agent_run_id: ${meta.agentRunId}` : null,
    meta.productUrl ? `product_url: ${meta.productUrl}` : null,
    meta.matchedNiche ? `matched_niche: ${meta.matchedNiche}` : null,
    "status: operator_editing — created from a deterministic agent draft.",
    "review every section before flipping to approved_for_generation.",
    input.callerNotes ? `\noperator note: ${input.callerNotes}` : null,
  ].filter((s): s is string => Boolean(s));
  return lines.join("\n");
}

export async function createPromptVersionFromAgentDraftAction(
  input: AgentDraftPromptInput,
): Promise<PromptVersionActionResult & { editorHref?: string }> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const operatorId = "userId" in auth ? auth.userId : null;

  if (!input.contentItemId || input.contentItemId.length < 8) {
    return { ok: false, error: "A content item must be selected." };
  }
  if (!input.label?.trim()) {
    return { ok: false, error: "Prompt label is required." };
  }
  if (!input.promptBody?.trim()) {
    return { ok: false, error: "Prompt body is required." };
  }
  if (input.negativePrompt && input.negativePrompt.length > 500) {
    return {
      ok: false,
      error: `Negative prompt must be ≤500 chars (was ${input.negativePrompt.length}).`,
    };
  }

  const notes = buildAgentNotes(input);

  if (getDataSource() === "demo") {
    const siblings = _demoListPromptVersions().filter(
      (p) => p.contentItemId === input.contentItemId,
    );
    const parent = siblings.sort((a, b) => b.versionNumber - a.versionNumber)[0];
    const nextVersion = siblings.reduce(
      (m, p) => Math.max(m, p.versionNumber),
      0,
    ) + 1;
    const now = new Date().toISOString();
    const newId = randomUUID();
    _demoUpsertPromptVersion({
      id: newId,
      contentItemId: input.contentItemId,
      versionNumber: nextVersion,
      label: input.label,
      hook: input.hook || null,
      script: input.script || null,
      promptBody: input.promptBody,
      negativePrompt: input.negativePrompt || null,
      scenePlan: input.scenePlan || null,
      creatorDirection: input.creatorDirection || null,
      productConstraints: input.productConstraints || null,
      qualityTier: PHASE_1X_QUALITY_TIER,
      status: "operator_editing",
      notes,
      parentVersionId: parent?.id ?? null,
      sourceRegenerationRequestId: null,
      createdBy: operatorId,
      createdAt: now,
      updatedAt: now,
    });
    return {
      ok: true,
      message: `Draft prompt v${nextVersion} created (demo mode).`,
      promptVersionId: newId,
    };
  }

  try {
    const admin = getServiceRoleSupabase();

    // Resolve content item + its campaign so we can hand back a working
    // editor URL. Also a cheap existence + access check.
    const { data: contentRow, error: contentErr } = await admin
      .from("content_items")
      .select("id, campaign_id")
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (contentErr) return { ok: false, error: contentErr.message };
    const content = contentRow as { id: string; campaign_id: string } | null;
    if (!content) return { ok: false, error: "Content item not found." };

    // Find the latest existing prompt version (for parent_version_id +
    // next version_number). NULL parent is fine if the content item has
    // no prompt yet.
    const { data: latestRow, error: latestErr } = await admin
      .from("prompt_versions")
      .select("id, version_number")
      .eq("content_item_id", content.id)
      .order("version_number", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (latestErr) return { ok: false, error: latestErr.message };
    const latest = latestRow as
      | { id: string; version_number: number }
      | null;
    const nextVersion = (latest?.version_number ?? 0) + 1;

    const { data: inserted, error: insertErr } = await admin
      .from("prompt_versions")
      .insert({
        content_item_id: content.id,
        version_number: nextVersion,
        label: input.label,
        hook: input.hook || null,
        script: input.script || null,
        prompt_body: input.promptBody,
        negative_prompt: input.negativePrompt || null,
        scene_plan: input.scenePlan || null,
        creator_direction: input.creatorDirection || null,
        product_constraints: input.productConstraints || null,
        quality_tier: PHASE_1X_QUALITY_TIER,
        // Hard guarantee: operator_editing only. Never approved.
        status: "operator_editing",
        notes,
        parent_version_id: latest?.id ?? null,
        source_regeneration_request_id: null,
        created_by: operatorId,
      })
      .select("id")
      .maybeSingle();
    if (insertErr) return { ok: false, error: insertErr.message };
    const newId = (inserted as { id: string } | null)?.id;
    if (!newId) return { ok: false, error: "Insert returned no id." };

    const editorHref = `/agency/campaigns/${content.campaign_id}/content/${content.id}/prompt`;
    revalidatePromptPaths(content.campaign_id, content.id);
    return {
      ok: true,
      message: `Draft prompt v${nextVersion} created. Status = operator_editing. No generation job was created; no paid call was made.`,
      promptVersionId: newId,
      editorHref,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function createPromptVersionFromRegenerationRequestAction(input: {
  regenerationRequestId: string;
  parentVersionId: string;
  campaignId: string;
  contentId: string;
  label?: string;
}): Promise<PromptVersionActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const operatorId = "userId" in auth ? auth.userId : null;

  if (getDataSource() === "demo") {
    const parent = _demoFindPromptVersionById(input.parentVersionId);
    if (!parent) return { ok: false, error: "Parent version not found." };
    const siblings = _demoListPromptVersions().filter(
      (p) => p.contentItemId === parent.contentItemId,
    );
    const nextVersion = siblings.reduce(
      (max, p) => Math.max(max, p.versionNumber),
      0,
    ) + 1;
    const now = new Date().toISOString();
    const newId = randomUUID();
    _demoUpsertPromptVersion({
      ...parent,
      id: newId,
      versionNumber: nextVersion,
      label: input.label ?? `v${nextVersion} (from request)`,
      status: "operator_editing",
      parentVersionId: parent.id,
      sourceRegenerationRequestId: input.regenerationRequestId,
      createdBy: operatorId,
      createdAt: now,
      updatedAt: now,
    });
    _demoUpdateRegenerationRequest(input.regenerationRequestId, {
      status: "accepted",
    });
    revalidatePromptPaths(input.campaignId, input.contentId);
    return {
      ok: true,
      message: "Forked a new prompt version.",
      promptVersionId: newId,
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data: parentRow, error: parentErr } = await admin
      .from("prompt_versions")
      .select(
        "content_item_id, label, hook, script, prompt_body, negative_prompt, " +
          "scene_plan, creator_direction, product_constraints, quality_tier",
      )
      .eq("id", input.parentVersionId)
      .maybeSingle();
    if (parentErr) return { ok: false, error: parentErr.message };
    const parent = parentRow as Record<string, unknown> | null;
    if (!parent) return { ok: false, error: "Parent version not found." };

    const { data: maxRow, error: maxErr } = await admin
      .from("prompt_versions")
      .select("version_number")
      .eq("content_item_id", parent.content_item_id as string)
      .order("version_number", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (maxErr) return { ok: false, error: maxErr.message };
    const nextVersion =
      ((maxRow as { version_number?: number } | null)?.version_number ?? 0) + 1;

    const { data: inserted, error: insertErr } = await admin
      .from("prompt_versions")
      .insert({
        content_item_id: parent.content_item_id,
        version_number: nextVersion,
        label: input.label ?? `v${nextVersion} (from request)`,
        hook: parent.hook,
        script: parent.script,
        prompt_body: parent.prompt_body,
        negative_prompt: parent.negative_prompt,
        scene_plan: parent.scene_plan,
        creator_direction: parent.creator_direction,
        product_constraints: parent.product_constraints,
        quality_tier: parent.quality_tier,
        status: "operator_editing",
        parent_version_id: input.parentVersionId,
        source_regeneration_request_id: input.regenerationRequestId,
        created_by: operatorId,
      })
      .select("id")
      .maybeSingle();
    if (insertErr) return { ok: false, error: insertErr.message };
    const newId = (inserted as { id: string } | null)?.id;
    if (!newId) return { ok: false, error: "Insert returned no id." };

    const { error: reqErr } = await admin
      .from("regeneration_requests")
      .update({ status: "accepted" })
      .eq("id", input.regenerationRequestId);
    if (reqErr) {
      console.warn(
        "[yuvo] regeneration_request status update failed:",
        reqErr.message,
      );
    }
    revalidatePromptPaths(input.campaignId, input.contentId);
    return {
      ok: true,
      message: "Forked a new prompt version.",
      promptVersionId: newId,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}
