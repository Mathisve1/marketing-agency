// Yuvo Studio — Phase 1E regeneration_requests operator actions.
//
// Three operator-only actions on the regeneration_requests queue:
//   - acceptRegenerationRequestAction({requestId})
//       Marks the request 'accepted'. Operator commits to acting on
//       it but hasn't created a new prompt version yet.
//   - dismissRegenerationRequestAction({requestId, note?})
//       Marks the request 'dismissed' with an optional operator note
//       written back to the content_feedback thread.
//   - markRegenerationRequestFulfilledAction({requestId, promptVersionId})
//       Called by the prompt-version flow once a new version exists
//       that addresses the request.
//
// These are operator-only — guarded by getCurrentPersona().kind ===
// "operator". In demo mode they mutate the in-memory store.
//
// No paid Enhancor / generation calls are triggered. "Fulfilled" just
// means "a prompt version that we'd generate with exists"; the
// generation handoff is Phase 1F+.

"use server";

import { revalidatePath } from "next/cache";
import { _demoUpdateRegenerationRequest } from "@/lib/data/regeneration-requests";
import { _demoPushFeedback } from "@/lib/data/feedback";
import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import { randomUUID } from "crypto";

export interface RegenerationActionResult {
  ok: boolean;
  message?: string;
  error?: string;
}

async function requireOperator(): Promise<{ userId: string } | { error: string }> {
  if (getDataSource() === "demo") {
    // Demo mode has no real persona; trust the caller is the operator
    // running the dev server.
    return { userId: "00000000-0000-0000-0000-000000000000" };
  }
  if (!hasSupabaseEnv()) return { error: "Supabase auth is not configured." };
  const persona = await getCurrentPersona();
  if (!persona) return { error: "Please sign in first." };
  if (persona.kind !== "operator") {
    return { error: "Operator access required." };
  }
  return { userId: persona.userId };
}

function revalidateRequestPaths(campaignId: string, contentId: string): void {
  revalidatePath(`/agency/campaigns/${campaignId}/outputs`);
  revalidatePath(
    `/agency/campaigns/${campaignId}/content/${contentId}/prompt`,
  );
}

export async function acceptRegenerationRequestAction(input: {
  requestId: string;
  campaignId: string;
  contentId: string;
}): Promise<RegenerationActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };

  if (getDataSource() === "demo") {
    const row = _demoUpdateRegenerationRequest(input.requestId, {
      status: "accepted",
    });
    if (!row) return { ok: false, error: "Request not found." };
    revalidateRequestPaths(input.campaignId, input.contentId);
    return { ok: true, message: "Accepted. Open the prompt editor next." };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { error } = await admin
      .from("regeneration_requests")
      .update({ status: "accepted" })
      .eq("id", input.requestId)
      .eq("status", "open");
    if (error) return { ok: false, error: error.message };
    revalidateRequestPaths(input.campaignId, input.contentId);
    return { ok: true, message: "Accepted." };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function dismissRegenerationRequestAction(input: {
  requestId: string;
  campaignId: string;
  contentId: string;
  note?: string;
}): Promise<RegenerationActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const note = (input.note ?? "").trim() || null;
  const now = new Date().toISOString();

  if (getDataSource() === "demo") {
    const row = _demoUpdateRegenerationRequest(input.requestId, {
      status: "dismissed",
      resolvedAt: now,
      resolvedByProfileId: auth.userId,
    });
    if (!row) return { ok: false, error: "Request not found." };
    if (note) {
      // Surface the operator note back to the client thread.
      _demoPushFeedback({
        id: randomUUID(),
        contentItemId: row.contentItemId,
        author: "operator",
        body: note,
        reason: null,
        createdAt: now,
      });
    }
    revalidateRequestPaths(input.campaignId, input.contentId);
    return { ok: true, message: "Dismissed." };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { error } = await admin
      .from("regeneration_requests")
      .update({
        status: "dismissed",
        resolved_at: now,
        resolved_by_profile_id: auth.userId,
      })
      .eq("id", input.requestId);
    if (error) return { ok: false, error: error.message };

    if (note) {
      const { error: noteErr } = await admin.from("content_feedback").insert({
        content_item_id: input.contentId,
        author_kind: "operator",
        profile_id: auth.userId,
        body: note,
      });
      if (noteErr) {
        console.warn(
          "[yuvo] operator dismiss note failed:",
          noteErr.message,
        );
      }
    }
    revalidateRequestPaths(input.campaignId, input.contentId);
    return { ok: true, message: "Dismissed." };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export async function markRegenerationRequestFulfilledAction(input: {
  requestId: string;
  promptVersionId: string;
  campaignId: string;
  contentId: string;
}): Promise<RegenerationActionResult> {
  const auth = await requireOperator();
  if ("error" in auth) return { ok: false, error: auth.error };
  const now = new Date().toISOString();

  if (getDataSource() === "demo") {
    const row = _demoUpdateRegenerationRequest(input.requestId, {
      status: "fulfilled",
      acceptedPromptVersionId: input.promptVersionId,
      resolvedAt: now,
      resolvedByProfileId: auth.userId,
    });
    if (!row) return { ok: false, error: "Request not found." };
    revalidateRequestPaths(input.campaignId, input.contentId);
    return { ok: true, message: "Marked fulfilled." };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { error } = await admin
      .from("regeneration_requests")
      .update({
        status: "fulfilled",
        accepted_prompt_version_id: input.promptVersionId,
        resolved_at: now,
        resolved_by_profile_id: auth.userId,
      })
      .eq("id", input.requestId);
    if (error) return { ok: false, error: error.message };
    revalidateRequestPaths(input.campaignId, input.contentId);
    return { ok: true, message: "Marked fulfilled." };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

