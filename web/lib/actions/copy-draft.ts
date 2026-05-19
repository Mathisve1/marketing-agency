// Yuvo Studio — Phase 2E Copy Draft Agent action.
//
// OPERATOR-ONLY. Generates deterministic operator-review copy for a
// (typically non-video) draft content item and writes it to
// content_items.caption_draft, plus a structured [copy draft] block in
// prompt_summary. It NEVER:
//   - generates a video / creates a generation_jobs row
//   - creates or modifies a prompt_versions row
//   - calls Seedance / Enhancor / Audio Fixer / any paid API
//   - sends an email or publishes anything
//   - changes content status / shared_with_client / client_safe_video_url

"use server";

import { revalidatePath } from "next/cache";
import { getDataSource } from "@/lib/data/_source";
import {
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { getCurrentPersona } from "@/lib/auth/persona";
import {
  isContentChannel,
  isContentFormat,
  type ContentChannel,
  type ContentFormat,
} from "@/lib/content/taxonomy";
import { planCopyDraft } from "@/lib/agents/copy-draft";

export interface CopyDraftActionInput {
  contentItemId: string;
  tone?: string;
  cta?: string;
  operatorNotes?: string;
}

export interface CopyDraftActionResult {
  ok: boolean;
  error?: string;
  message?: string;
  contentItemId?: string;
  format?: string;
  channel?: string;
  /** The exact text written to caption_draft (for UI preview). */
  copyText?: string;
  calendarHref?: string;
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Same operator-editable set used by the prompt-draft actions: never
// touch items the client has signed off on.
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

const COPY_BLOCK_MARKER = "\n\n[copy draft]\n";

// Phase 2F — operator internal approval marker. Lives in the same
// `prompt_summary` provenance trail as `[copy draft]`. Strict
// strip-then-append on re-runs so the block is idempotent. The client
// portal cannot read this column (the `client_content_items_v` view
// does not project `prompt_summary`) so the approval state is
// structurally invisible to the client.
const COPY_APPROVAL_MARKER = "\n\n[copy approval]\n";
export type CopyApprovalStatus = "approved_internal";

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

function parseTag(promptSummary: string | null, tag: string): string | null {
  if (!promptSummary) return null;
  const m = promptSummary.match(
    new RegExp(`(?:^|\\n)${tag}:\\s*([a-z0-9_\\-]+)`, "i"),
  );
  return m ? m[1] : null;
}

/** Drop any prior [copy draft] block so re-runs are idempotent. Also
 *  drops any subsequent [copy approval] block — a fresh copy draft
 *  invalidates the previous approval (the operator is producing new copy
 *  that has not been reviewed yet). */
function stripCopyBlock(promptSummary: string): string {
  const idx = promptSummary.indexOf(COPY_BLOCK_MARKER);
  if (idx === -1) {
    // No copy-draft block — but a stray approval block from a
    // hand-edit can still exist; strip it too so subsequent re-drafts
    // can re-attach a clean approval block.
    const aIdx = promptSummary.indexOf(COPY_APPROVAL_MARKER);
    return aIdx === -1 ? promptSummary : promptSummary.slice(0, aIdx);
  }
  return promptSummary.slice(0, idx);
}

/** Drop any prior [copy approval] block (and any text after it). The
 *  approval block is always last in the provenance trail, so a tail
 *  slice is safe. Used by `approveCopyDraftAction` to make re-approvals
 *  idempotent and by `resetCopyApprovalAction` to clear sign-off. */
function stripApprovalBlock(promptSummary: string): string {
  const idx = promptSummary.indexOf(COPY_APPROVAL_MARKER);
  return idx === -1 ? promptSummary : promptSummary.slice(0, idx);
}

export async function createCopyDraftForContentItemAction(
  input: CopyDraftActionInput,
): Promise<CopyDraftActionResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if ((input.tone ?? "").length > 120) {
    return { ok: false, error: "Tone is too long (120 char cap)." };
  }
  if ((input.cta ?? "").length > 200) {
    return { ok: false, error: "CTA is too long (200 char cap)." };
  }
  if ((input.operatorNotes ?? "").length > 500) {
    return { ok: false, error: "Operator notes are too long (500 cap)." };
  }

  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist copy drafts. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();

    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select(
        "id, campaign_id, status, prompt_summary, title, caption_draft, platforms",
      )
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (ciErr) return { ok: false, error: ciErr.message };
    const ci = ciRow as {
      id: string;
      campaign_id: string;
      status: string;
      prompt_summary: string | null;
      title: string;
      caption_draft: string | null;
      platforms: string[] | null;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };
    if (!EDITABLE_STATUSES.has(ci.status)) {
      return {
        ok: false,
        error:
          `Content item status is "${ci.status}"; copy drafts can only be `
          + `created in an operator-editable state (not under client `
          + `sign-off).`,
      };
    }

    // Tenancy: content → campaign → brand resolves (cheap guard).
    const { data: campRow, error: campErr } = await admin
      .from("campaigns")
      .select("id, brand_id")
      .eq("id", ci.campaign_id)
      .maybeSingle();
    if (campErr) return { ok: false, error: campErr.message };
    const camp = campRow as { id: string; brand_id: string } | null;
    if (!camp) return { ok: false, error: "Campaign not found." };

    // Resolve format + channel from prompt_summary (Phase 2D
    // provenance), falling back to platforms[0] for channel and a safe
    // non-video default for format.
    const fmtRaw = parseTag(ci.prompt_summary, "format");
    const chRaw =
      parseTag(ci.prompt_summary, "channel") ??
      (Array.isArray(ci.platforms) ? ci.platforms[0] ?? null : null);
    const format: ContentFormat = isContentFormat(fmtRaw)
      ? fmtRaw
      : "feed_post";
    const channel: ContentChannel = isContentChannel(chRaw)
      ? chRaw
      : "other";

    const matchedNiche = parseTag(ci.prompt_summary, "matched_niche");
    const productUrl = parseTag(ci.prompt_summary, "product_url");

    const draft = planCopyDraft({
      format,
      channel,
      title: ci.title,
      brief: ci.caption_draft?.trim() || ci.title,
      brandContext: {
        productUrl: productUrl ?? undefined,
        matchedNiche: matchedNiche ?? undefined,
      },
      tone: input.tone,
      cta: input.cta,
      operatorNotes: input.operatorNotes,
    });

    const nowIso = new Date().toISOString();
    const baseSummary = stripCopyBlock(ci.prompt_summary ?? "");
    const copyBlock =
      COPY_BLOCK_MARKER +
      [
        "copy_draft_status: drafted",
        "copy_draft_source: copy_draft_agent",
        `copy_format: ${format}`,
        `copy_channel: ${channel}`,
        `copy_mode: ${draft.mode}`,
        `copy_drafted_at: ${nowIso}`,
        input.operatorNotes
          ? `copy_operator_note: ${input.operatorNotes}`
          : null,
      ]
        .filter((s): s is string => s !== null)
        .join("\n");
    const newSummary = baseSummary + copyBlock;

    const { error: updErr } = await admin
      .from("content_items")
      .update({
        caption_draft: draft.plainText,
        prompt_summary: newSummary,
        // status / shared_with_client / client_safe_video_url
        // deliberately NOT in this update.
      })
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath("/agency/copy-drafts");
    revalidatePath("/agency/prompt-review");
    revalidatePath(`/agency/campaigns/${ci.campaign_id}/calendar`);
    revalidatePath("/agency");

    return {
      ok: true,
      message:
        `Copy draft (${draft.mode}) written to caption_draft for "${ci.title}". `
        + `No video, no prompt version, no client share, no paid call.`,
      contentItemId: ci.id,
      format,
      channel,
      copyText: draft.plainText,
      calendarHref: `/agency/campaigns/${ci.campaign_id}/calendar`,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// ===========================================================================
// Phase 2F — Review & Approve Copy Drafts (INTERNAL ONLY).
//
// Two operator actions:
//   - approveCopyDraftAction       : mark copy as approved_internal
//   - resetCopyApprovalAction      : un-approve / clear sign-off
//
// SAFETY (both):
//   - Operator persona required (or demo mode).
//   - Editable-status guard (never touches items the client signed off on).
//   - Writes ONLY `caption_draft` (when a new caption is provided) and
//     `prompt_summary` (provenance block).
//   - NEVER changes `status`, `shared_with_client`, `client_safe_video_url`.
//   - NEVER creates `generation_jobs`, `prompt_versions`, `generated_assets`,
//     `audio_fixer_jobs`.
//   - NEVER calls Seedance / Enhancor / Audio Fixer / any paid API.
//   - NEVER sends email, publishes, or shares with the client. "Approved
//     internally" means exactly that — the operator has signed off; the
//     client-share / publish gate is a separate, future, explicit step.
// ===========================================================================

export interface CopyApprovalActionInput {
  contentItemId: string;
  /** Optional final-edit override. When provided, this string replaces
   *  the current `caption_draft` before the approval block is written.
   *  500-char cap is intentionally generous (LinkedIn captions can run
   *  long); the cap exists to bound the request, not to enforce a
   *  platform-specific limit. */
  approvedCaptionDraft?: string;
  /** Optional operator note recorded inside the approval block. */
  approvalNotes?: string;
}

export interface CopyApprovalActionResult {
  ok: boolean;
  error?: string;
  message?: string;
  contentItemId?: string;
  /** ISO timestamp written into the approval block. */
  approvedAt?: string;
  approvalStatus?: CopyApprovalStatus;
  /** The exact text currently stored in caption_draft after the action
   *  (so the UI can refresh without a re-fetch). */
  copyText?: string;
}

export async function approveCopyDraftAction(
  input: CopyApprovalActionInput,
): Promise<CopyApprovalActionResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if ((input.approvedCaptionDraft ?? "").length > 5000) {
    return {
      ok: false,
      error: "Approved caption draft is too long (5,000 char cap).",
    };
  }
  if ((input.approvalNotes ?? "").length > 500) {
    return { ok: false, error: "Approval notes are too long (500 cap)." };
  }

  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist copy approvals. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select(
        "id, campaign_id, status, prompt_summary, title, caption_draft, "
        + "shared_with_client, client_safe_video_url",
      )
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (ciErr) return { ok: false, error: ciErr.message };
    const ci = ciRow as {
      id: string;
      campaign_id: string;
      status: string;
      prompt_summary: string | null;
      title: string;
      caption_draft: string | null;
      shared_with_client: boolean | null;
      client_safe_video_url: string | null;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };
    if (!EDITABLE_STATUSES.has(ci.status)) {
      return {
        ok: false,
        error:
          `Content item status is "${ci.status}"; copy approval is only `
          + `allowed in an operator-editable state.`,
      };
    }

    // Require copy to exist before sign-off. Prevents approving an
    // empty draft.
    const updatedCaption =
      typeof input.approvedCaptionDraft === "string" &&
      input.approvedCaptionDraft.trim() !== ""
        ? input.approvedCaptionDraft
        : ci.caption_draft;
    if (!updatedCaption || updatedCaption.trim() === "") {
      return {
        ok: false,
        error:
          "Copy is empty. Generate or paste a caption_draft first, "
          + "then approve.",
      };
    }

    const persona =
      getDataSource() === "demo" ? null : await getCurrentPersona();
    const approver =
      persona?.kind === "operator" && persona.userId
        ? persona.userId
        : "operator";

    const nowIso = new Date().toISOString();
    const baseSummary = stripApprovalBlock(ci.prompt_summary ?? "");
    const approvalBlock =
      COPY_APPROVAL_MARKER +
      [
        "copy_approval_status: approved_internal",
        `copy_approved_at: ${nowIso}`,
        `copy_approved_by: ${approver}`,
        input.approvalNotes
          ? `copy_approval_notes: ${input.approvalNotes}`
          : null,
      ]
        .filter((s): s is string => s !== null)
        .join("\n");
    const newSummary = baseSummary + approvalBlock;

    const patch: Record<string, unknown> = {
      prompt_summary: newSummary,
    };
    if (updatedCaption !== ci.caption_draft) {
      patch.caption_draft = updatedCaption;
    }
    // Intentionally absent from the patch (belt-and-braces — also
    // documented in the action header):
    //   - status
    //   - shared_with_client
    //   - client_safe_video_url
    //   - client_safe_poster_url
    //   - audio_fixer_* / cost_*
    const { error: updErr } = await admin
      .from("content_items")
      .update(patch)
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath("/agency/copy-drafts");
    revalidatePath("/agency/prompt-review");
    revalidatePath(`/agency/campaigns/${ci.campaign_id}/calendar`);
    revalidatePath("/agency");

    return {
      ok: true,
      message:
        `Copy approved internally for "${ci.title}". `
        + `Nothing was shared with the client, nothing was sent, nothing was published.`,
      contentItemId: ci.id,
      approvedAt: nowIso,
      approvalStatus: "approved_internal",
      copyText: updatedCaption,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export interface CopyApprovalResetInput {
  contentItemId: string;
}

/** Strip the `[copy approval]` block. Caption_draft is left alone. */
export async function resetCopyApprovalAction(
  input: CopyApprovalResetInput,
): Promise<CopyApprovalActionResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist copy approvals. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select("id, campaign_id, status, prompt_summary, title")
      .eq("id", input.contentItemId)
      .maybeSingle();
    if (ciErr) return { ok: false, error: ciErr.message };
    const ci = ciRow as {
      id: string;
      campaign_id: string;
      status: string;
      prompt_summary: string | null;
      title: string;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };
    if (!EDITABLE_STATUSES.has(ci.status)) {
      return {
        ok: false,
        error:
          `Content item status is "${ci.status}"; cannot reset copy `
          + `approval on a client-signed-off item.`,
      };
    }

    const stripped = stripApprovalBlock(ci.prompt_summary ?? "");
    if (stripped === (ci.prompt_summary ?? "")) {
      return {
        ok: true,
        message: "No copy approval block was present; nothing changed.",
        contentItemId: ci.id,
      };
    }
    const { error: updErr } = await admin
      .from("content_items")
      .update({ prompt_summary: stripped })
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath("/agency/copy-drafts");
    revalidatePath("/agency/prompt-review");
    revalidatePath(`/agency/campaigns/${ci.campaign_id}/calendar`);
    revalidatePath("/agency");

    return {
      ok: true,
      message: `Copy approval cleared for "${ci.title}".`,
      contentItemId: ci.id,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}
