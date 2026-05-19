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

// Phase 2G — client-preview marker. Records WHEN the operator
// generated the client-safe preview text and where the preview text
// itself lives. The actual preview text is stored on
// `content_items.client_safe_copy_preview` (added in migration 009);
// only the metadata lives in this provenance block. Same
// strip-then-append idempotency pattern as `[copy approval]`.
const CLIENT_PREVIEW_MARKER = "\n\n[client copy preview]\n";
export type ClientCopyPreviewStatus = "prepared" | "shared_with_client";

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

/** Phase 2G — drop any prior [client copy preview] block. Always at
 *  the tail (after [copy approval]), so a slice is safe. */
function stripClientPreviewBlock(promptSummary: string): string {
  const idx = promptSummary.indexOf(CLIENT_PREVIEW_MARKER);
  return idx === -1 ? promptSummary : promptSummary.slice(0, idx);
}

/** Phase 2G — parse the current approval status from prompt_summary.
 *  Returns "approved_internal" when the block is present and signed
 *  off, null otherwise. Mirrors the parser in
 *  `web/lib/data/owner-overview.ts` but kept local to the action
 *  module so the action does not depend on the data layer. */
function parseCopyApprovalStatus(
  promptSummary: string | null | undefined,
): "approved_internal" | null {
  if (!promptSummary) return null;
  // Note: the marker's terminating "\n" is already consumed by the
  // literal `\n\n\[copy approval\]\n`, so an additional `(?:^|\n)`
  // anchor in front of the key would never match (the position is
  // past the only candidate newline). The `[\s\S]*?` gap + the
  // marker context is enough to scope the match correctly — every
  // key inside the block (copy_approval_status, copy_approved_at,
  // copy_approved_by, copy_approval_notes) starts at a known offset
  // and is followed by `:`.
  const m = promptSummary.match(
    /\n\n\[copy approval\]\n[\s\S]*?copy_approval_status:\s*([a-z_]+)/i,
  );
  if (!m) return null;
  return m[1] === "approved_internal" ? "approved_internal" : null;
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

// ===========================================================================
// Phase 2G — Client-review preparation for non-video copy.
//
// Two operator actions:
//   - prepareClientCopyPreviewAction    : prepare clean client-facing preview
//   - shareCopyPreviewWithClientAction  : explicitly flip shared_with_client
//
// SAFETY (both):
//   - Operator persona required (or demo mode).
//   - Editable-status guard.
//   - Requires `copy_approval_status: approved_internal` first.
//   - NEVER sends email, NEVER publishes to any social platform,
//     NEVER calls Seedance / Enhancor / Audio Fixer / any paid API,
//     NEVER creates a generation_jobs / prompt_versions /
//     generated_assets / audio_fixer_jobs row, NEVER touches
//     `client_safe_video_url` or `client_safe_poster_url`.
//   - prepareClientCopyPreviewAction never flips `shared_with_client`
//     and never changes `status`.
//   - shareCopyPreviewWithClientAction is the ONLY surface that flips
//     `shared_with_client` to true (and the matching status), and it
//     refuses to run when no client preview has been prepared yet.
//
// SCHEMA DEPENDENCY:
//   These actions write to `content_items.client_safe_copy_preview`,
//   added in migration 009. The DB will reject the patch with a clear
//   PostgREST error ("column does not exist") if the migration has
//   not been applied yet; the action returns that error string to
//   the UI so the operator knows to apply 009.
// ===========================================================================

export interface PrepareClientCopyPreviewInput {
  contentItemId: string;
  /** Optional override. When provided, this string replaces the
   *  default (which is the current `caption_draft` text, capped to
   *  the platform-friendly length). Operators typically clean up the
   *  agent's structured markup ("[tone: …]", "HEADLINE:", etc.)
   *  before passing this. */
  clientSafeCopyPreview?: string;
  /** Optional operator note recorded inside the provenance block.
   *  Internal-only — never shown in the client portal. */
  operatorNotes?: string;
}

export interface PrepareClientCopyPreviewResult {
  ok: boolean;
  error?: string;
  message?: string;
  contentItemId?: string;
  previewText?: string;
  preparedAt?: string;
}

export async function prepareClientCopyPreviewAction(
  input: PrepareClientCopyPreviewInput,
): Promise<PrepareClientCopyPreviewResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if ((input.clientSafeCopyPreview ?? "").length > 5000) {
    return {
      ok: false,
      error: "Client preview text is too long (5,000 char cap).",
    };
  }
  if ((input.operatorNotes ?? "").length > 500) {
    return { ok: false, error: "Operator notes are too long (500 cap)." };
  }
  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist client previews. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select(
        "id, campaign_id, status, prompt_summary, title, caption_draft, "
        + "shared_with_client, client_safe_video_url, client_safe_poster_url",
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
      client_safe_poster_url: string | null;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };
    if (!EDITABLE_STATUSES.has(ci.status)) {
      return {
        ok: false,
        error:
          `Content item status is "${ci.status}"; client preview can only `
          + `be prepared in an operator-editable state.`,
      };
    }
    if (parseCopyApprovalStatus(ci.prompt_summary) !== "approved_internal") {
      return {
        ok: false,
        error:
          "Copy must be approved internally before a client preview can "
          + "be prepared. Use \"Review & approve copy\" first.",
      };
    }

    const preview =
      typeof input.clientSafeCopyPreview === "string" &&
      input.clientSafeCopyPreview.trim() !== ""
        ? input.clientSafeCopyPreview
        : ci.caption_draft;
    if (!preview || preview.trim() === "") {
      return {
        ok: false,
        error:
          "No preview text available. Provide a clientSafeCopyPreview "
          + "override or generate copy first.",
      };
    }

    const nowIso = new Date().toISOString();
    // Strip any prior client-preview block; the approval block (and
    // anything before it) is preserved.
    const baseSummary = stripClientPreviewBlock(ci.prompt_summary ?? "");
    const block =
      CLIENT_PREVIEW_MARKER +
      [
        "client_copy_preview_status: prepared",
        `client_copy_preview_prepared_at: ${nowIso}`,
        input.operatorNotes
          ? `client_copy_preview_operator_note: ${input.operatorNotes}`
          : null,
      ]
        .filter((s): s is string => s !== null)
        .join("\n");
    const newSummary = baseSummary + block;

    const patch: Record<string, unknown> = {
      client_safe_copy_preview: preview,
      prompt_summary: newSummary,
    };
    // Intentionally absent from the patch:
    //   - status
    //   - shared_with_client
    //   - client_safe_video_url
    //   - client_safe_poster_url
    //   - caption_draft (the raw agent output stays unchanged on the
    //     operator side — only the prepared client preview is new).
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
        `Client preview prepared for "${ci.title}". This only fills the `
        + `client portal preview — no email, no publishing, no client `
        + `share yet.`,
      contentItemId: ci.id,
      previewText: preview,
      preparedAt: nowIso,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

export interface ShareCopyPreviewWithClientInput {
  contentItemId: string;
  /** Operator must type "SHARE COPY" in the UI to enable the button.
   *  The action re-validates the confirmation token server-side so
   *  the gate is enforced even if the UI is bypassed. */
  confirmationToken: string;
  /** Optional operator note recorded inside the provenance block. */
  operatorNotes?: string;
}

export interface ShareCopyPreviewWithClientResult {
  ok: boolean;
  error?: string;
  message?: string;
  contentItemId?: string;
  /** ISO timestamp when the share happened. */
  sharedAt?: string;
  /** Mirrors content_items.status after the patch. */
  status?: string;
}

const SHARE_CONFIRMATION_TOKEN = "SHARE COPY";

export async function shareCopyPreviewWithClientAction(
  input: ShareCopyPreviewWithClientInput,
): Promise<ShareCopyPreviewWithClientResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if ((input.confirmationToken ?? "").trim() !== SHARE_CONFIRMATION_TOKEN) {
    return {
      ok: false,
      error:
        "Confirmation token is missing or incorrect. Type SHARE COPY "
        + "exactly to confirm.",
    };
  }
  if ((input.operatorNotes ?? "").length > 500) {
    return { ok: false, error: "Operator notes are too long (500 cap)." };
  }
  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist client shares. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();
    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select(
        "id, campaign_id, status, prompt_summary, title, "
        + "client_safe_copy_preview, shared_with_client",
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
      client_safe_copy_preview: string | null;
      shared_with_client: boolean | null;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };
    if (
      !ci.client_safe_copy_preview ||
      ci.client_safe_copy_preview.trim() === ""
    ) {
      return {
        ok: false,
        error:
          "No client preview is on file. Run \"Prepare client preview\" "
          + "before sharing.",
      };
    }
    if (parseCopyApprovalStatus(ci.prompt_summary) !== "approved_internal") {
      return {
        ok: false,
        error:
          "Copy is not approved internally. Approve it before sharing "
          + "with the client.",
      };
    }
    // We do NOT block re-shares — flipping the status from
    // shared_with_client → shared_with_client is a no-op the operator
    // may want for the audit trail / provenance refresh.

    const nowIso = new Date().toISOString();
    const baseSummary = stripClientPreviewBlock(ci.prompt_summary ?? "");
    const block =
      CLIENT_PREVIEW_MARKER +
      [
        "client_copy_preview_status: shared_with_client",
        `client_copy_preview_shared_at: ${nowIso}`,
        input.operatorNotes
          ? `client_copy_preview_share_note: ${input.operatorNotes}`
          : null,
      ]
        .filter((s): s is string => s !== null)
        .join("\n");
    const newSummary = baseSummary + block;

    // The check constraint `content_items_shared_flag_consistent`
    // requires `shared_with_client = true` whenever status is in the
    // client-visible set, and vice-versa. We flip both together.
    const patch: Record<string, unknown> = {
      status: "shared_with_client",
      shared_with_client: true,
      prompt_summary: newSummary,
    };
    // Intentionally absent from the patch:
    //   - client_safe_copy_preview (already on file)
    //   - client_safe_video_url / client_safe_poster_url (video flow)
    //   - caption_draft / quality_tier / cost_* / internal_*
    const { error: updErr } = await admin
      .from("content_items")
      .update(patch)
      .eq("id", ci.id);
    if (updErr) return { ok: false, error: updErr.message };

    revalidatePath("/agency/copy-drafts");
    revalidatePath("/agency/prompt-review");
    revalidatePath(`/agency/campaigns/${ci.campaign_id}/calendar`);
    revalidatePath("/agency");
    // Also revalidate the client portal so the new item appears on
    // their next visit. The portal slug is not on the content_items
    // row directly; we revalidate the wildcard parent path instead.
    revalidatePath("/client", "layout");

    return {
      ok: true,
      message:
        `Preview shared with client for "${ci.title}". No email sent, `
        + `no platform publish, no paid call. The client will see the `
        + `prepared preview on their portal at their next visit.`,
      contentItemId: ci.id,
      sharedAt: nowIso,
      status: "shared_with_client",
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}

// ===========================================================================
// Phase 2I — Revise copy from client feedback.
//
// Operator-only. For a copy item with open client feedback or an open
// regeneration_request:
//   1. Loads the latest client content_feedback row + the open
//      regeneration_request (if any), threads body + reason into the
//      Copy Draft Agent operatorNotes.
//   2. Delegates to createCopyDraftForContentItemAction to rewrite
//      caption_draft + the [copy draft] block. stripCopyBlock there
//      also removes any [copy approval] and [client copy preview]
//      blocks that lived after the prior [copy draft], so the revised
//      copy is back to "operator drafted, not yet reviewed".
//   3. Bumps content_items.status back to draft + shared_with_client
//      false (single atomic UPDATE; the row constraint is satisfied
//      because both columns flip together).
//   4. Best-effort marks the source regeneration_request status
//      'accepted'.
//
// NEVER sends email, publishes, calls Seedance/Enhancor/Audio Fixer,
// creates a generation_jobs / prompt_versions / generated_assets /
// audio_fixer_jobs row, or re-shares with the client (the row drops
// out of client_content_items_v because status=draft + shared_with_client
// false fall outside CLIENT_VISIBLE_STATUSES).
// ===========================================================================

export interface ReviseCopyFromFeedbackInput {
  contentItemId: string;
  feedbackId?: string;
  regenerationRequestId?: string;
  operatorNotes?: string;
}

export interface ReviseCopyFromFeedbackResult {
  ok: boolean;
  error?: string;
  message?: string;
  contentItemId?: string;
  usedFeedbackId?: string | null;
  usedRegenerationRequestId?: string | null;
  newCopyText?: string;
  newStatus?: string;
  regenerationRequestAccepted?: boolean;
  approvalAndPreviewStripped?: boolean;
}

const REVISION_NOTE_PREFIX = "[revise from client feedback]";

function composeRevisionBrief(args: {
  feedbackBody?: string | null;
  feedbackReason?: string | null;
  regenBody?: string | null;
  regenReason?: string | null;
  operatorNotes?: string | null;
}): string {
  const lines: string[] = [REVISION_NOTE_PREFIX];
  if (args.feedbackReason) lines.push(`feedback_reason: ${args.feedbackReason}`);
  else if (args.regenReason) lines.push(`regen_reason: ${args.regenReason}`);
  const body = (args.feedbackBody ?? args.regenBody ?? "").trim();
  if (body) lines.push(`client_said: ${body.slice(0, 600)}`);
  if (args.operatorNotes && args.operatorNotes.trim()) {
    lines.push(`operator_note: ${args.operatorNotes.trim().slice(0, 400)}`);
  }
  lines.push(
    "revise the draft to address the change request while staying on-brand "
      + "and keeping the format conventions intact.",
  );
  return lines.join(" | ").slice(0, 500);
}

export async function reviseCopyDraftFromClientFeedbackAction(
  input: ReviseCopyFromFeedbackInput,
): Promise<ReviseCopyFromFeedbackResult> {
  const auth = await requireOperator();
  if (auth !== true) return { ok: false, error: auth.error };

  if (!UUID_RE.test(input.contentItemId ?? "")) {
    return { ok: false, error: "Invalid content item id." };
  }
  if (input.feedbackId && !UUID_RE.test(input.feedbackId)) {
    return { ok: false, error: "Invalid feedback id." };
  }
  if (
    input.regenerationRequestId &&
    !UUID_RE.test(input.regenerationRequestId)
  ) {
    return { ok: false, error: "Invalid regeneration request id." };
  }
  if ((input.operatorNotes ?? "").length > 500) {
    return { ok: false, error: "Operator notes are too long (500 cap)." };
  }

  if (getDataSource() === "demo") {
    return {
      ok: false,
      error:
        "Demo mode does not persist revisions. Switch "
        + "NEXT_PUBLIC_DATA_SOURCE=supabase to use this action.",
    };
  }

  try {
    const admin = getServiceRoleSupabase();

    const { data: ciRow, error: ciErr } = await admin
      .from("content_items")
      .select(
        "id, campaign_id, status, prompt_summary, title, caption_draft, "
          + "shared_with_client, client_safe_copy_preview",
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
      shared_with_client: boolean;
      client_safe_copy_preview: string | null;
    } | null;
    if (!ci) return { ok: false, error: "Content item not found." };

    const fmtRaw = parseTag(ci.prompt_summary, "format");
    if (!fmtRaw || !isContentFormat(fmtRaw)) {
      return {
        ok: false,
        error:
          "This content item has no format tag in prompt_summary; the "
          + "revise action targets non-video copy items only.",
      };
    }
    const VIDEO_FORMATS = new Set([
      "ugc_video_ad",
      "organic_reel",
      "short_video",
      "long_video",
    ]);
    if (VIDEO_FORMATS.has(fmtRaw)) {
      return {
        ok: false,
        error:
          "Video formats use the prompt-version + Seedance review flow, "
          + "not the copy-revise flow.",
      };
    }

    let usedFeedbackId: string | null = null;
    let usedRegenId: string | null = null;
    let feedbackBody: string | null = null;
    let feedbackReason: string | null = null;
    let regenBody: string | null = null;
    let regenReason: string | null = null;

    if (input.feedbackId) {
      const { data: fbRow, error: fbErr } = await admin
        .from("content_feedback")
        .select("id, body, author_kind")
        .eq("id", input.feedbackId)
        .maybeSingle();
      if (fbErr) return { ok: false, error: fbErr.message };
      const fb = fbRow as
        | { id: string; body: string; author_kind: string }
        | null;
      if (!fb) return { ok: false, error: "Feedback row not found." };
      usedFeedbackId = fb.id;
      feedbackBody = fb.body;
    } else {
      const { data: fbRow, error: fbErr } = await admin
        .from("content_feedback")
        .select("id, body, author_kind, created_at")
        .eq("content_item_id", ci.id)
        .eq("author_kind", "client")
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle();
      if (fbErr) return { ok: false, error: fbErr.message };
      const fb = fbRow as { id: string; body: string } | null;
      if (fb) {
        usedFeedbackId = fb.id;
        feedbackBody = fb.body;
      }
    }
    if (feedbackBody) {
      const m = feedbackBody.match(/^\[([a-z_]+)\]\s*([\s\S]*)$/);
      if (m) {
        feedbackReason = m[1];
        feedbackBody = m[2].trim();
      }
    }

    if (input.regenerationRequestId) {
      const { data: rqRow, error: rqErr } = await admin
        .from("regeneration_requests")
        .select("id, body, reason, status")
        .eq("id", input.regenerationRequestId)
        .maybeSingle();
      if (rqErr) return { ok: false, error: rqErr.message };
      const rq = rqRow as
        | { id: string; body: string; reason: string | null; status: string }
        | null;
      if (!rq) {
        return { ok: false, error: "Regeneration request not found." };
      }
      usedRegenId = rq.id;
      regenBody = rq.body;
      regenReason = rq.reason ?? null;
    } else {
      const { data: rqRow, error: rqErr } = await admin
        .from("regeneration_requests")
        .select("id, body, reason, status, created_at")
        .eq("content_item_id", ci.id)
        .eq("status", "open")
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle();
      if (rqErr) return { ok: false, error: rqErr.message };
      const rq = rqRow as
        | { id: string; body: string; reason: string | null }
        | null;
      if (rq) {
        usedRegenId = rq.id;
        regenBody = rq.body;
        regenReason = rq.reason ?? null;
      }
    }

    if (!usedFeedbackId && !usedRegenId) {
      return {
        ok: false,
        error:
          "No client feedback or open regeneration_request found for this "
          + "item. Use the regular Copy Draft Agent instead.",
      };
    }

    const revisionBrief = composeRevisionBrief({
      feedbackBody,
      feedbackReason,
      regenBody,
      regenReason,
      operatorNotes: input.operatorNotes,
    });

    const draftRes = await createCopyDraftForContentItemAction({
      contentItemId: ci.id,
      operatorNotes: revisionBrief,
    });
    if (!draftRes.ok) {
      return {
        ok: false,
        error: `Copy draft regeneration failed: ${draftRes.error ?? "unknown"}`,
        usedFeedbackId,
        usedRegenerationRequestId: usedRegenId,
      };
    }

    const { data: updRow, error: updErr } = await admin
      .from("content_items")
      .update({ status: "draft", shared_with_client: false })
      .eq("id", ci.id)
      .select(
        "id, status, shared_with_client, caption_draft, prompt_summary, "
          + "client_safe_video_url, client_safe_copy_preview",
      )
      .maybeSingle();
    if (updErr) return { ok: false, error: updErr.message };
    const updated = updRow as
      | {
          id: string;
          status: string;
          shared_with_client: boolean;
          caption_draft: string | null;
          prompt_summary: string | null;
          client_safe_video_url: string | null;
          client_safe_copy_preview: string | null;
        }
      | null;
    if (!updated) {
      return { ok: false, error: "Post-update read returned no row." };
    }

    const approvalAndPreviewStripped =
      typeof updated.prompt_summary === "string" &&
      !updated.prompt_summary.includes("[copy approval]") &&
      !updated.prompt_summary.includes("[client copy preview]");

    let regenAccepted = false;
    if (usedRegenId) {
      const { error: rqUpdErr } = await admin
        .from("regeneration_requests")
        .update({
          status: "accepted",
          resolved_at: new Date().toISOString(),
        })
        .eq("id", usedRegenId);
      if (!rqUpdErr) regenAccepted = true;
    }

    revalidatePath("/agency/copy-drafts");
    revalidatePath("/agency/prompt-review");
    revalidatePath(`/agency/campaigns/${ci.campaign_id}/calendar`);
    revalidatePath("/agency");

    return {
      ok: true,
      message:
        `Revised copy for "${ci.title}". Status reset to draft; internal `
        + `approval (Phase 2F) and client preview (Phase 2G) must be re-run `
        + `before this item reaches the client again.`,
      contentItemId: ci.id,
      usedFeedbackId,
      usedRegenerationRequestId: usedRegenId,
      newCopyText: updated.caption_draft ?? undefined,
      newStatus: updated.status,
      regenerationRequestAccepted: regenAccepted,
      approvalAndPreviewStripped,
    };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}
