// Yuvo Studio — Phase 1E prompt_versions readers + demo store.
//
// OPERATOR-ONLY. Never imported from the /client/* surface. The matching
// DB-level guarantee is that prompt_versions has operator-only RLS in
// migration 004 (no client SELECT policy).

import { getDataSource, SupabaseDataError } from "./_source";
import { getSupabaseServerClient } from "@/lib/supabase/client";

export type PromptVersionStatus =
  | "draft"
  | "operator_editing"
  | "approved_for_generation"
  | "superseded";

export type PromptVersionQualityTier =
  | "draft_480p"
  | "standard_720p"
  | "premium_1080p";

export interface PromptVersion {
  id: string;
  contentItemId: string;
  versionNumber: number;
  label: string | null;
  hook: string | null;
  script: string | null;
  promptBody: string | null;
  negativePrompt: string | null;
  scenePlan: string | null;
  creatorDirection: string | null;
  productConstraints: string | null;
  qualityTier: PromptVersionQualityTier;
  status: PromptVersionStatus;
  notes: string | null;
  parentVersionId: string | null;
  sourceRegenerationRequestId: string | null;
  createdBy: string | null;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Demo-mode in-memory store. Seeded with the two prompt versions that
// mirror the Supabase seed in supabase/seed.sql so the operator can
// click into the prompt editor immediately without configuring
// Supabase.
// ---------------------------------------------------------------------------
const NOW = "2026-05-16T08:00:00Z";

const DEMO_PROMPT_VERSIONS: PromptVersion[] = [
  {
    id: "promptver_pai_v1_1080p",
    contentItemId: "content_pai_route01_v1",
    versionNumber: 1,
    label: "1080p hero (historical)",
    hook:
      "I like that this feels simple — one serum, a few ingredients I can actually understand.",
    script:
      "Intro hook on camera, hold serum bottle at chest height, brief glance at label, look back to camera for the close. Calm British register, real-friend cadence.",
    promptBody:
      "UGC product-talk, 15s. Single take. Bathroom or soft-window living room. One creator, 28–40, sensitive-skin profile. Serum bottle visible but not the hero of the frame. Calm British VO. Slow ambient music bed.",
    negativePrompt:
      "No exaggerated claims. No clinical-white studio. No on-screen logo overlays. No competing skincare visible in the frame. No glossy commercial lighting.",
    scenePlan:
      "0–2s hook on camera. 2–8s glance to product + brief read. 8–14s reflection on routine. 14–15s soft close.",
    creatorDirection:
      "Real-friend register, not influencer-energy. Slow blinks, occasional looks away. Body language: comfortable, not posed.",
    productConstraints:
      "Pai Skincare BioRegenerate Rosehip Oil. Label must be legible when in frame; do not invent ingredient claims. Brand name spelled \"Pai\" (not \"Pái\" / \"Pie\").",
    qualityTier: "premium_1080p",
    status: "superseded",
    notes:
      "Realised version. Production cost 5940 raw + 2104 audio-fixer. Kept for lineage; new takes target 720p.",
    parentVersionId: null,
    sourceRegenerationRequestId: null,
    createdBy: null,
    createdAt: NOW,
    updatedAt: NOW,
  },
  {
    id: "promptver_pai_v2_720p_draft",
    contentItemId: "content_pai_route02_draft",
    versionNumber: 1,
    label: "720p stricter label-text guard",
    hook: "Three ingredients. That's it.",
    script:
      "Macro-led pickup of the bottle, slow rotation to expose the label, hands stay in soft focus, voice-over rides over the rotation. Close on the dropper.",
    promptBody:
      "Macro-led UGC variant, 15s, 720p. One creator's hands only — no face this take. Soft daylight. Two cuts maximum. Calm British VO. The story is the label.",
    negativePrompt:
      "No animated text, no graphics overlays, no warped or melted label text, no AI-typical extra fingers, no fake ingredient names, no jewellery, no nail polish, no competing brands.",
    scenePlan:
      "0–3s ingredient-led hook over a hand reaching for the bottle. 3–9s slow rotation of the bottle so the label reads. 9–13s dropper close-up. 13–15s soft close, hand setting bottle down.",
    creatorDirection:
      "Hands-only this take. Calm, deliberate motion. No fidgeting. Skin tone should match a sensitive-skin profile (no heavy makeup on hands).",
    productConstraints:
      "Pai Skincare BioRegenerate Rosehip Oil. Label text MUST be legible and spelled exactly as on the real packaging. If the label cannot be rendered legibly, fall back to an out-of-focus pass rather than inventing text. No competing skincare visible.",
    qualityTier: "standard_720p",
    status: "operator_editing",
    notes:
      "Working draft. Sharpened label-text guard after the 1080p take rendered an OK but not perfect label. NOT approved for generation yet — operator is still iterating.",
    parentVersionId: null,
    sourceRegenerationRequestId: null,
    createdBy: null,
    createdAt: NOW,
    updatedAt: NOW,
  },
];

export function _demoUpsertPromptVersion(row: PromptVersion): void {
  const idx = DEMO_PROMPT_VERSIONS.findIndex((p) => p.id === row.id);
  if (idx === -1) DEMO_PROMPT_VERSIONS.push(row);
  else DEMO_PROMPT_VERSIONS[idx] = row;
}

export function _demoListPromptVersions(): PromptVersion[] {
  return [...DEMO_PROMPT_VERSIONS];
}

export function _demoFindPromptVersionById(id: string): PromptVersion | null {
  return DEMO_PROMPT_VERSIONS.find((p) => p.id === id) ?? null;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------
const SELECT_COLS =
  "id, content_item_id, version_number, label, hook, script, prompt_body, " +
  "negative_prompt, scene_plan, creator_direction, product_constraints, " +
  "quality_tier, status, notes, parent_version_id, " +
  "source_regeneration_request_id, created_by, created_at, updated_at";

function rowToView(r: {
  id: string;
  content_item_id: string;
  version_number: number;
  label: string | null;
  hook: string | null;
  script: string | null;
  prompt_body: string | null;
  negative_prompt: string | null;
  scene_plan: string | null;
  creator_direction: string | null;
  product_constraints: string | null;
  quality_tier: PromptVersionQualityTier;
  status: PromptVersionStatus;
  notes: string | null;
  parent_version_id: string | null;
  source_regeneration_request_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}): PromptVersion {
  return {
    id: r.id,
    contentItemId: r.content_item_id,
    versionNumber: r.version_number,
    label: r.label,
    hook: r.hook,
    script: r.script,
    promptBody: r.prompt_body,
    negativePrompt: r.negative_prompt,
    scenePlan: r.scene_plan,
    creatorDirection: r.creator_direction,
    productConstraints: r.product_constraints,
    qualityTier: r.quality_tier,
    status: r.status,
    notes: r.notes,
    parentVersionId: r.parent_version_id,
    sourceRegenerationRequestId: r.source_regeneration_request_id,
    createdBy: r.created_by,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

export async function listPromptVersions(
  contentItemId: string,
): Promise<PromptVersion[]> {
  if (getDataSource() === "demo") {
    return DEMO_PROMPT_VERSIONS
      .filter((p) => p.contentItemId === contentItemId)
      .sort((a, b) => b.versionNumber - a.versionNumber);
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("prompt_versions")
    .select(SELECT_COLS)
    .eq("content_item_id", contentItemId)
    .order("version_number", { ascending: false });
  if (error) throw new SupabaseDataError("listPromptVersions", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof rowToView>[0][]).map(rowToView);
}

export async function getLatestPromptVersion(
  contentItemId: string,
): Promise<PromptVersion | null> {
  const all = await listPromptVersions(contentItemId);
  return all[0] ?? null;
}

export async function getActivePromptVersion(
  contentItemId: string,
): Promise<PromptVersion | null> {
  const all = await listPromptVersions(contentItemId);
  return all.find((p) => p.status === "approved_for_generation") ?? null;
}

export async function getPromptVersionById(
  id: string,
): Promise<PromptVersion | null> {
  if (getDataSource() === "demo") {
    return _demoFindPromptVersionById(id);
  }
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("prompt_versions")
    .select(SELECT_COLS)
    .eq("id", id)
    .maybeSingle();
  if (error) throw new SupabaseDataError("getPromptVersionById", error);
  if (!data) return null;
  return rowToView(data as unknown as Parameters<typeof rowToView>[0]);
}
