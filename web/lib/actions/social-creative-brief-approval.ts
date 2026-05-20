// Yuvo Studio — Phase 4D1 internal approval action for the visual
// creative brief.
//
// OPERATOR-ONLY. Records an internal sign-off on a drafted creative
// brief by appending a `[creative brief approval]` block to
// `content_items.prompt_summary`. Idempotent (strip-then-append).
//
// HARD RULES:
//   - NEVER generates an image / video / asset.
//   - NEVER calls Seedance / Enhancor / Audio Fixer / OpenAI /
//     Anthropic / any image-gen API / paid API / `fetch()`.
//   - NEVER touches `caption_draft`, `client_safe_*`,
//     `shared_with_client`, `status`, or any other table.
//   - The approval block lives in `prompt_summary`, which the client
//     view (`client_content_items_v`) does NOT project — structurally
//     invisible to the client.

"use server";

import { revalidatePath } from "next/cache";
import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import {
  getCreativeBriefApprovalStatus,
  hasCreativeBrief,
} from "@/lib/creative/creative-brief-parser";

export interface CreativeBriefApprovalInput {
  contentItemId: string;
  notes?: string;
}

export interface CreativeBriefApprovalResult {
  ok: boolean;
  error?: string;
  message?: string;
  contentItemId?: string;
  approvedAt?: string;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Same operator-editable set as the copy-draft + creative-brief
// actions — never approve over an item the client has signed off on.
const EDITABLE_STATUSES = new Set([
  "draft",
  "generating",
  "raw_ready",
  "audio_fixer_pending",
  "audio_fixed",
  "ready_for_client_review",
  "changes_requested_by_client",
  "failed",
]);

const APPROVAL_MARKER = "\n\n[creative brief approval]\n";

async function requireOperator(): Promise<true | { error: string; persona?: { id: string } }> {
  if (getDataSource() === "demo") return true;
  if (!hasSupabaseEnv()) return { error: "Supabase auth is not configured." };
  const persona = await getCurrentPersona();
  if (!persona) return { error: "Please sign in first." };
  if (persona.kind !== "operator") {
    return { error: "Operator access required." };
  }
  return true;
}

/** Drop any prior `[creative brief approval]` block — always at the
 *  tail of `prompt_summary` (after `[creative brief]`), so a slice is
 *  safe. */
function stripApprovalBlock(promptSummary: string): string {
  const idx = promptSummary.indexOf(APPROVAL_MARKER);
  return idx === -1 ? promptSummary : promptSummary.slice(0, idx);
}

export async function approveCreativeBriefInternalAction(
  input: CreativeBriefApprovalInput,
): Promise<CreativeBriefApprovalResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if ((input.notes ?? "").length > 500) {
    return { ok: false, error: "Notes are too long (500 cap)." };
  }

  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist creative brief approvals. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();

    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select("id, campaign_id, status, prompt_summary")
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (ciErr) return { ok: false, error: ciErr.message };
    const ci = ciRow as {
      id: string;
      campaign_id: string;
      status: string;
      prompt_summary: string | null;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };
    if (!EDITABLE_STATUSES.has(ci.status)) {
      return {
        ok: false,
        error:
          `Content item status is "${ci.status}"; the creative brief can `
          + `only be internally approved while it is operator-editable.`,
      };
    }
    if (!hasCreativeBrief(ci.prompt_summary)) {
      return {
        ok: false,
        error: "No creative brief on this item — draft one first.",
      };
    }

    // Idempotent strip-then-append. The approval block always sits at
    // the very end of prompt_summary; the existing creative brief
    // block survives untouched in front of it.
    const nowIso = new Date().toISOString();
    const baseSummary = stripApprovalBlock(ci.prompt_summary ?? "");
    const approvalBlock =
      APPROVAL_MARKER +
      [
        "creative_brief_approval_status: approved_internal",
        `creative_brief_approved_at: ${nowIso}`,
        input.notes
          ? `creative_brief_approval_notes: ${input.notes.trim().slice(0, 500)}`
          : null,
      ]
        .filter((s): s is string => s !== null)
        .join("\n");
    const newSummary = baseSummary + approvalBlock;

    const { error: updErr } = await admin
      .from("content_items")
      .update({
        // ONLY prompt_summary. caption_draft, shared_with_client,
        // client_safe_*, status, and every other column are
        // deliberately untouched.
        prompt_summary: newSummary,
      })
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath(`/agency/creative-briefs/${ci.id}/preview`);
    revalidatePath("/agency/creative-briefs");
    revalidatePath("/agency");

    return {
      ok: true,
      message: "Creative brief approved (internal).",
      contentItemId: ci.id,
      approvedAt: nowIso,
    };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

/** Reset (clear) the internal creative brief approval. Pure status
 *  flip on `prompt_summary` — strips the `[creative brief approval]`
 *  block. The `[creative brief]` block is preserved. */
export async function resetCreativeBriefApprovalAction(
  input: { contentItemId: string },
): Promise<CreativeBriefApprovalResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }

  if (getDataSource() === "demo") {
    return { ok: false, error: "Demo mode does not persist this action." };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select("id, campaign_id, prompt_summary")
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (ciErr) return { ok: false, error: ciErr.message };
    const ci = ciRow as {
      id: string;
      campaign_id: string;
      prompt_summary: string | null;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };

    if (getCreativeBriefApprovalStatus(ci.prompt_summary) === "none") {
      return { ok: true, message: "Approval was already not set." };
    }

    const newSummary = stripApprovalBlock(ci.prompt_summary ?? "");
    const { error: updErr } = await admin
      .from("content_items")
      .update({ prompt_summary: newSummary })
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath(`/agency/creative-briefs/${ci.id}/preview`);
    revalidatePath("/agency/creative-briefs");
    revalidatePath("/agency");

    return { ok: true, message: "Internal approval cleared.", contentItemId: ci.id };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
