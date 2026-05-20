// Yuvo Studio — Phase 4F internal QA checklist persistence action.
//
// OPERATOR-ONLY. Records the operator's pass/fail decisions on the
// internal QA checklist (Phase 4F `CreativePreviewQAPanel`, mounted on
// the preview page) by appending a `[creative preview QA]` block to
// `content_items.prompt_summary`. Idempotent strip-then-append. Lives
// at the tail of the provenance trail (after `[creative brief]` and
// `[creative brief approval]`).
//
// HARD RULES:
//   - NEVER generates an image / video / asset.
//   - NEVER calls Seedance / Enhancor / Audio Fixer / OpenAI /
//     Anthropic / any image-gen API / paid API / `fetch()`.
//   - NEVER touches `caption_draft`, `client_safe_*`,
//     `shared_with_client`, `status`, or any other column / table.
//   - The QA block lives in `prompt_summary`, which the client view
//     (`client_content_items_v`) does NOT project — structurally
//     invisible to the client.

"use server";

import { revalidatePath } from "next/cache";
import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import { hasCreativeBrief } from "@/lib/creative/creative-brief-parser";

export interface CreativePreviewQAInput {
  contentItemId: string;
  /** Map of item-id → "pass" | "fail". Item-id keys match the
   *  shared QA_ITEMS set in `web/lib/creative/qa-items.ts`, which the
   *  persisted `CreativePreviewQAPanel` reads from. */
  items: Record<string, "pass" | "fail">;
}

export interface CreativePreviewQAResult {
  ok: boolean;
  error?: string;
  message?: string;
  contentItemId?: string;
  qaStatus?: "passed" | "needs_attention";
  checkedAt?: string;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

const QA_MARKER = "\n\n[creative preview QA]\n";
const ALLOWED_ITEM_IDS = new Set([
  "text_readable",
  "cta_visible",
  "layout_fits",
  "no_forbidden_text",
  "no_internal_notes_visible",
  "brand_tone_ok",
  "claim_safe",
  "ready_for_export_later",
]);

async function requireOperator(): Promise<true | { error: string }> {
  if (getDataSource() === "demo") return true;
  if (!hasSupabaseEnv()) return { error: "Supabase auth is not configured." };
  const persona = await getCurrentPersona();
  if (!persona) return { error: "Please sign in first." };
  if (persona.kind !== "operator") {
    return { error: "Operator access required." };
  }
  return true;
}

function stripQABlock(promptSummary: string): string {
  const idx = promptSummary.indexOf(QA_MARKER);
  return idx === -1 ? promptSummary : promptSummary.slice(0, idx);
}

export async function saveCreativePreviewQAAction(
  input: CreativePreviewQAInput,
): Promise<CreativePreviewQAResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if (!input.items || typeof input.items !== "object") {
    return { ok: false, error: "Items map is required." };
  }
  // Whitelist the keys + values to guarantee no free-text injection
  // into prompt_summary via this action.
  const sanitized: Record<string, "pass" | "fail"> = {};
  for (const [k, v] of Object.entries(input.items)) {
    if (!ALLOWED_ITEM_IDS.has(k)) continue;
    if (v !== "pass" && v !== "fail") continue;
    sanitized[k] = v;
  }
  if (Object.keys(sanitized).length === 0) {
    return { ok: false, error: "No valid QA items submitted." };
  }

  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist QA checks. Switch "
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
          `Content item status is "${ci.status}"; QA can only be saved `
          + `while the item is operator-editable.`,
      };
    }
    if (!hasCreativeBrief(ci.prompt_summary)) {
      return {
        ok: false,
        error: "No creative brief on this item — draft one first.",
      };
    }

    // qa_status = "passed" iff every saved item is "pass";
    // otherwise "needs_attention". (Items not submitted are absent,
    // not failing — operator can save partial state.)
    const allPass = Object.values(sanitized).every((v) => v === "pass");
    const qaStatus: "passed" | "needs_attention" = allPass
      ? "passed"
      : "needs_attention";

    const nowIso = new Date().toISOString();
    const itemsLine = Object.entries(sanitized)
      .map(([k, v]) => `${k}=${v}`)
      .join(",");

    const baseSummary = stripQABlock(ci.prompt_summary ?? "");
    const qaBlock =
      QA_MARKER +
      [
        `qa_status: ${qaStatus}`,
        `qa_checked_at: ${nowIso}`,
        `qa_items: ${itemsLine}`,
      ].join("\n");
    const newSummary = baseSummary + qaBlock;

    const { error: updErr } = await admin
      .from("content_items")
      .update({
        // ONLY prompt_summary. No other column or table is touched.
        prompt_summary: newSummary,
      })
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath(`/agency/creative-briefs/${ci.id}/preview`);
    revalidatePath("/agency/creative-briefs");

    return {
      ok: true,
      message: `QA saved (${qaStatus}).`,
      contentItemId: ci.id,
      qaStatus,
      checkedAt: nowIso,
    };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function resetCreativePreviewQAAction(
  input: { contentItemId: string },
): Promise<CreativePreviewQAResult> {
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
      .select("id, prompt_summary")
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (ciErr) return { ok: false, error: ciErr.message };
    const ci = ciRow as { id: string; prompt_summary: string | null } | null;
    if (!ci) return { ok: false, error: "Content item not found." };

    const baseSummary = stripQABlock(ci.prompt_summary ?? "");
    const { error: updErr } = await admin
      .from("content_items")
      .update({ prompt_summary: baseSummary })
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath(`/agency/creative-briefs/${ci.id}/preview`);
    revalidatePath("/agency/creative-briefs");
    return { ok: true, message: "QA cleared.", contentItemId: ci.id };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
