// Yuvo Studio — Phase 1D feedback + approval reads.
//
// Two reader functions, both demo + supabase aware:
//   - getContentFeedback(contentId)  : timeline of comments
//   - getContentApprovals(contentId) : timeline of approval decisions
//
// These are used by BOTH the client portal (to show the client their
// own thread) and the agency dashboard (to show the operator everything
// shared with them). The client surface filters at the API-action layer
// by validating portal ownership; this reader returns the raw shape.
//
// The structured "reason" is encoded inside the content_feedback row
// body using a `[reason:<key>]` prefix — Phase 1B's schema doesn't have
// a dedicated reason column, and adding one would require a fresh
// migration. The prefix is parsed back into a structured field by
// `splitReasonBody()` below.

import { getDataSource, SupabaseDataError } from "./_source";
import { getSupabaseServerClient } from "@/lib/supabase/client";

export type FeedbackReason =
  | "wrong_tone"
  | "wrong_product"
  | "not_on_brand"
  | "bad_voice"
  | "bad_face_hands"
  | "bad_caption"
  | "different_offer_needed"
  | "other"
  | null;

export interface FeedbackEntry {
  id: string;
  author: "client" | "operator";
  body: string;
  reason: FeedbackReason;
  createdAt: string;
}

export interface ApprovalEntry {
  id: string;
  decision: "approved" | "rejected" | "changes_requested";
  actor: "client" | "operator";
  note: string | null;
  decidedAt: string;
}

export const FEEDBACK_REASON_LABELS: Record<NonNullable<FeedbackReason>, string> =
  {
    wrong_tone: "Wrong tone",
    wrong_product: "Wrong product",
    not_on_brand: "Not on brand",
    bad_voice: "Bad voice",
    bad_face_hands: "Bad face / hands",
    bad_caption: "Bad caption",
    different_offer_needed: "Different offer needed",
    other: "Other",
  };

export const FEEDBACK_REASON_KEYS = Object.keys(
  FEEDBACK_REASON_LABELS,
) as (keyof typeof FEEDBACK_REASON_LABELS)[];

// ---------------------------------------------------------------------------
// reason <-> body codec
// ---------------------------------------------------------------------------
const REASON_PREFIX_RE = /^\[reason:([a-z_]+)\]\s*/i;

/** Encode a structured reason into the body string written to
 *  content_feedback.body. We use a `[reason:<key>]` prefix to avoid
 *  needing a fresh column on the existing schema. */
export function joinReasonBody(
  reason: FeedbackReason,
  body: string,
): string {
  const trimmed = body.trim();
  if (!reason) return trimmed;
  return `[reason:${reason}] ${trimmed}`;
}

export function splitReasonBody(raw: string): {
  reason: FeedbackReason;
  body: string;
} {
  const match = REASON_PREFIX_RE.exec(raw);
  if (!match) return { reason: null, body: raw };
  const key = match[1].toLowerCase();
  if (!(key in FEEDBACK_REASON_LABELS)) {
    // Unknown reason from a future schema rev — degrade to 'other' and
    // keep the rest of the body intact.
    return { reason: "other", body: raw.slice(match[0].length) };
  }
  return {
    reason: key as NonNullable<FeedbackReason>,
    body: raw.slice(match[0].length),
  };
}

// ---------------------------------------------------------------------------
// Demo-mode in-memory store
// ---------------------------------------------------------------------------
// Demo mode keeps feedback in module-level memory. This is only ever
// visible within a single dev process; refreshing in production demos
// resets state. Live reads use the Supabase tables instead.
//
// We expose tiny CRUD on this so the demo server actions can push into
// it. Tests and the page renderers go through getContentFeedback /
// getContentApprovals which read from the same store.

interface DemoFeedbackRow extends FeedbackEntry {
  contentItemId: string;
}
interface DemoApprovalRow extends ApprovalEntry {
  contentItemId: string;
}

const DEMO_FEEDBACK: DemoFeedbackRow[] = [];
const DEMO_APPROVALS: DemoApprovalRow[] = [];

export function _demoPushFeedback(row: DemoFeedbackRow): void {
  DEMO_FEEDBACK.push(row);
}
export function _demoPushApproval(row: DemoApprovalRow): void {
  DEMO_APPROVALS.push(row);
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------
export async function getContentFeedback(
  contentItemId: string,
): Promise<FeedbackEntry[]> {
  if (getDataSource() === "demo") {
    return DEMO_FEEDBACK
      .filter((r) => r.contentItemId === contentItemId)
      .map(({ contentItemId: _omit, ...rest }) => rest)
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  }

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("content_feedback")
    .select("id, author_kind, body, created_at")
    .eq("content_item_id", contentItemId)
    .order("created_at", { ascending: true });

  if (error) throw new SupabaseDataError("getContentFeedback", error);
  if (!data) return [];

  return (data as unknown as Array<{
    id: string;
    author_kind: "client" | "operator";
    body: string;
    created_at: string;
  }>).map((r) => {
    const { reason, body } = splitReasonBody(r.body);
    return {
      id: r.id,
      author: r.author_kind,
      body,
      reason,
      createdAt: r.created_at,
    };
  });
}

export async function getContentApprovals(
  contentItemId: string,
): Promise<ApprovalEntry[]> {
  if (getDataSource() === "demo") {
    return DEMO_APPROVALS
      .filter((r) => r.contentItemId === contentItemId)
      .map(({ contentItemId: _omit, ...rest }) => rest)
      .sort((a, b) => b.decidedAt.localeCompare(a.decidedAt));
  }

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("content_approvals")
    .select("id, decision, actor_kind, note, decided_at")
    .eq("content_item_id", contentItemId)
    .order("decided_at", { ascending: false });

  if (error) throw new SupabaseDataError("getContentApprovals", error);
  if (!data) return [];

  return (data as unknown as Array<{
    id: string;
    decision: "approved" | "rejected" | "changes_requested";
    actor_kind: "client" | "operator";
    note: string | null;
    decided_at: string;
  }>).map((r) => ({
    id: r.id,
    decision: r.decision,
    actor: r.actor_kind,
    note: r.note,
    decidedAt: r.decided_at,
  }));
}
