// Yuvo Studio — Phase 1D client-feedback server actions.
//
// Three actions:
//   approveContentAction       — client clicks "Approve"
//   requestChangesContentAction — client clicks "Request changes" + reason
//   commentContentAction       — client adds a comment
//
// All three:
//   1. In DEMO mode, push the row into the in-memory demo store and
//      return success. No DB writes.
//   2. In SUPABASE mode, validate that the authenticated session user
//      is a member of the named portal AND that the contentId belongs
//      to a campaign on that portal AND that the content is shared.
//      Then write via the SERVICE-ROLE client because:
//        - content_feedback / content_approvals INSERT policies require
//          a real auth.uid() AND author_kind/actor_kind = 'client', and
//        - the status flip on content_items.shared_with_client →
//          approved_by_client / changes_requested_by_client needs UPDATE
//          on content_items, which Phase 1B's client policy does not
//          grant (only operator policies do).
//      The service-role writes are safe because the ownership check is
//      proven in app code BEFORE the write.
//
// The hard rule: nothing here ever returns operator-only fields
// (cost_*, prompt_summary, internal asset paths, quality_tier). The
// only Returns are { ok, message?, error? }.

"use server";

import { revalidatePath } from "next/cache";
import {
  _demoPushApproval,
  _demoPushFeedback,
  joinReasonBody,
  type FeedbackReason,
} from "@/lib/data/feedback";
import { _demoPushRegenerationRequest } from "@/lib/data/regeneration-requests";
import { getDataSource } from "@/lib/data/_source";
import {
  getServerSupabase,
  getServiceRoleSupabase,
  hasSupabaseEnv,
} from "@/lib/supabase/server";
import { isPortalMember } from "@/lib/auth/persona";
import { randomUUID } from "crypto";

export interface ActionResult {
  ok: boolean;
  message?: string;
  error?: string;
}

interface ClientWriteAuthorization {
  userId: string;
  portalId: string;
  campaignId: string;
}

/** Validates that the caller is signed in, is a member of the given
 *  portal, AND that the content item belongs to the portal's campaign
 *  AND is currently shared. Returns the resolved IDs on success.
 *  Throws on failure — callers catch and convert to ActionResult. */
async function authorizeClientWrite(
  portalSlug: string,
  contentId: string,
): Promise<ClientWriteAuthorization> {
  // Phase 1O — each failure mode throws a tagged error so the action's
  // catch block, the UI flash, and the server-side console log all
  // distinguish (a) cookies / SSR session not reaching the action,
  // (b) the signed-in user not linked to this portal, (c) the content
  // row not belonging to the portal. The tag is the first token of
  // the error message; the operator can grep Worker logs for it.
  if (!hasSupabaseEnv()) {
    throw new Error("config: Supabase auth is not configured (demo mode).");
  }
  const supabase = await getServerSupabase();
  const { data: userData, error: userErr } = await supabase.auth.getUser();
  if (userErr) {
    throw new Error(`auth: ${userErr.message}`);
  }
  const user = userData?.user;
  if (!user) {
    throw new Error(
      "auth: not signed in — please reload the page and sign in again.",
    );
  }

  const isMember = await isPortalMember(user.id, portalSlug);
  if (!isMember) {
    throw new Error(
      `portal: you are not a member of "${portalSlug}". Ask the team to add you.`,
    );
  }

  // Service-role for the ownership lookup so the chain doesn't get
  // RLS-denied by policies that depend on auth helpers. The IDs are
  // not echoed back to the caller — only used internally.
  const admin = getServiceRoleSupabase();

  const { data: portalRow, error: portalErr } = await admin
    .from("client_portals")
    .select("id")
    .eq("slug", portalSlug)
    .maybeSingle();
  if (portalErr) throw new Error(`portal: ${portalErr.message}`);
  if (!portalRow) throw new Error(`portal: "${portalSlug}" not found.`);
  const portalId = (portalRow as { id: string }).id;

  const { data: contentRow, error: contentErr } = await admin
    .from("content_items")
    .select("campaign_id, shared_with_client, status, campaigns(client_portal_id)")
    .eq("id", contentId)
    .maybeSingle();
  if (contentErr) throw new Error(`content: ${contentErr.message}`);
  if (!contentRow) throw new Error("content: this video was not found.");
  const content = contentRow as unknown as {
    campaign_id: string;
    shared_with_client: boolean;
    status: string;
    campaigns: { client_portal_id: string | null } | { client_portal_id: string | null }[] | null;
  };
  // PostgREST embeds an FK target as either an object or an array of
  // length one; normalize to a single object.
  const embedded = Array.isArray(content.campaigns)
    ? content.campaigns[0] ?? null
    : content.campaigns;
  if (embedded?.client_portal_id !== portalId) {
    throw new Error("content: this video doesn't belong to your portal.");
  }
  if (!content.shared_with_client) {
    throw new Error(
      "content: this video isn't shared with you yet — please refresh.",
    );
  }

  return { userId: user.id, portalId, campaignId: content.campaign_id };
}

/** Updates content_items.status. Service-role; only called after
 *  authorizeClientWrite has proven ownership. */
async function bumpStatus(
  contentId: string,
  newStatus: "approved_by_client" | "changes_requested_by_client",
): Promise<void> {
  const admin = getServiceRoleSupabase();
  const { error } = await admin
    .from("content_items")
    .update({ status: newStatus, shared_with_client: true })
    .eq("id", contentId);
  if (error) throw new Error(`Status update failed: ${error.message}`);
}

// ---------------------------------------------------------------------------
// approve
// ---------------------------------------------------------------------------
export async function approveContentAction(input: {
  portalSlug: string;
  contentId: string;
  note?: string;
}): Promise<ActionResult> {
  const note = (input.note ?? "").trim() || null;

  if (getDataSource() === "demo") {
    _demoPushApproval({
      id: randomUUID(),
      contentItemId: input.contentId,
      decision: "approved",
      actor: "client",
      note,
      decidedAt: new Date().toISOString(),
    });
    revalidatePath(`/client/${input.portalSlug}/content/${input.contentId}`);
    return {
      ok: true,
      message: "Demo mode — approval saved locally (resets on server restart).",
    };
  }

  try {
    const auth = await authorizeClientWrite(input.portalSlug, input.contentId);
    const admin = getServiceRoleSupabase();
    const { error } = await admin.from("content_approvals").insert({
      content_item_id: input.contentId,
      decision: "approved",
      actor_kind: "client",
      profile_id: auth.userId,
      note,
    });
    if (error) {
      console.warn("[yuvo] approveContentAction insert failed:", error.message);
      return { ok: false, error: `db: ${error.message}` };
    }
    await bumpStatus(input.contentId, "approved_by_client");
    revalidatePath(`/client/${input.portalSlug}/content/${input.contentId}`);
    revalidatePath(`/client/${input.portalSlug}`);
    return { ok: true, message: "Approved. The team has been notified." };
  } catch (err) {
    const message = (err as Error).message;
    console.warn("[yuvo] approveContentAction threw:", message);
    return { ok: false, error: message };
  }
}

// ---------------------------------------------------------------------------
// request changes
// ---------------------------------------------------------------------------
export async function requestChangesContentAction(input: {
  portalSlug: string;
  contentId: string;
  reason: FeedbackReason;
  body: string;
}): Promise<ActionResult> {
  const body = (input.body ?? "").trim();
  if (!body) return { ok: false, error: "Please add a short note." };

  const composedBody = joinReasonBody(input.reason, body);

  if (getDataSource() === "demo") {
    const now = new Date().toISOString();
    const approvalId = randomUUID();
    const feedbackId = randomUUID();
    _demoPushApproval({
      id: approvalId,
      contentItemId: input.contentId,
      decision: "changes_requested",
      actor: "client",
      note: body,
      decidedAt: now,
    });
    _demoPushFeedback({
      id: feedbackId,
      contentItemId: input.contentId,
      author: "client",
      body,
      reason: input.reason,
      createdAt: now,
    });
    // Phase 1E — every client change-request also opens a structured
    // regeneration_request so the operator sees it as a queue item on
    // the outputs page.
    _demoPushRegenerationRequest({
      id: randomUUID(),
      contentItemId: input.contentId,
      sourceFeedbackId: feedbackId,
      sourceApprovalId: approvalId,
      requestedByProfileId: null,
      requestedByKind: "client",
      reason: input.reason,
      body,
      status: "open",
      acceptedPromptVersionId: null,
      resolvedAt: null,
      resolvedByProfileId: null,
      createdAt: now,
      updatedAt: now,
    });
    revalidatePath(`/client/${input.portalSlug}/content/${input.contentId}`);
    return {
      ok: true,
      message: "Demo mode — change request saved locally.",
    };
  }

  try {
    const auth = await authorizeClientWrite(input.portalSlug, input.contentId);
    const admin = getServiceRoleSupabase();

    const { data: feedbackRow, error: feedbackErr } = await admin
      .from("content_feedback")
      .insert({
        content_item_id: input.contentId,
        author_kind: "client",
        profile_id: auth.userId,
        body: composedBody,
      })
      .select("id")
      .maybeSingle();
    if (feedbackErr) return { ok: false, error: feedbackErr.message };
    const feedbackId = (feedbackRow as { id: string } | null)?.id ?? null;

    const { data: approvalRow, error: approvalErr } = await admin
      .from("content_approvals")
      .insert({
        content_item_id: input.contentId,
        decision: "changes_requested",
        actor_kind: "client",
        profile_id: auth.userId,
        note: body,
      })
      .select("id")
      .maybeSingle();
    if (approvalErr) return { ok: false, error: approvalErr.message };
    const approvalId = (approvalRow as { id: string } | null)?.id ?? null;

    // Phase 1E — open a structured regeneration_request alongside the
    // feedback + approval rows so the operator queue surfaces it. We
    // best-effort the insert; a failure here doesn't roll back the
    // change-request itself (the client has already been told their
    // feedback landed).
    const { error: regenErr } = await admin
      .from("regeneration_requests")
      .insert({
        content_item_id: input.contentId,
        source_feedback_id: feedbackId,
        source_approval_id: approvalId,
        requested_by_profile_id: auth.userId,
        requested_by_kind: "client",
        reason: input.reason,
        body,
        status: "open",
      });
    if (regenErr) {
      // Log-only; do not fail the user-facing action. Phase 1F will
      // collapse the three writes into a single transaction.
      console.warn(
        "[yuvo] regeneration_requests insert failed:",
        regenErr.message,
      );
    }

    await bumpStatus(input.contentId, "changes_requested_by_client");
    revalidatePath(`/client/${input.portalSlug}/content/${input.contentId}`);
    revalidatePath(`/client/${input.portalSlug}`);
    return { ok: true, message: "Change request sent." };
  } catch (err) {
    const message = (err as Error).message;
    console.warn("[yuvo] requestChangesContentAction threw:", message);
    return { ok: false, error: message };
  }
}

// ---------------------------------------------------------------------------
// comment
// ---------------------------------------------------------------------------
export async function commentContentAction(input: {
  portalSlug: string;
  contentId: string;
  body: string;
}): Promise<ActionResult> {
  const body = (input.body ?? "").trim();
  if (!body) return { ok: false, error: "Comment cannot be empty." };

  if (getDataSource() === "demo") {
    _demoPushFeedback({
      id: randomUUID(),
      contentItemId: input.contentId,
      author: "client",
      body,
      reason: null,
      createdAt: new Date().toISOString(),
    });
    revalidatePath(`/client/${input.portalSlug}/content/${input.contentId}`);
    return {
      ok: true,
      message: "Demo mode — comment saved locally.",
    };
  }

  try {
    const auth = await authorizeClientWrite(input.portalSlug, input.contentId);
    const admin = getServiceRoleSupabase();
    const { error } = await admin.from("content_feedback").insert({
      content_item_id: input.contentId,
      author_kind: "client",
      profile_id: auth.userId,
      body,
    });
    if (error) {
      console.warn("[yuvo] commentContentAction insert failed:", error.message);
      return { ok: false, error: `db: ${error.message}` };
    }
    revalidatePath(`/client/${input.portalSlug}/content/${input.contentId}`);
    return { ok: true, message: "Comment sent." };
  } catch (err) {
    const message = (err as Error).message;
    console.warn("[yuvo] commentContentAction threw:", message);
    return { ok: false, error: message };
  }
}
