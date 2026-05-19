// Yuvo Studio — Phase 1E regeneration_requests readers + demo store.
//
// A regeneration_request is a first-class operator queue item: "the
// client (or operator) wants this content_item regenerated". Phase 1E
// creates them as a side-effect of requestChangesContentAction so the
// operator sees structured workflow state ("open / accepted / dismissed
// / fulfilled") on the agency outputs page, separate from the
// free-form content_feedback thread.
//
// Reader is operator-only. The client-side view of the same workflow
// is the content_feedback thread + the next-week request form.

import { getDataSource, SupabaseDataError } from "./_source";
import { getSupabaseServerClient } from "@/lib/supabase/client";
import type { FeedbackReason } from "./feedback";

export type RegenerationStatus = "open" | "accepted" | "dismissed" | "fulfilled";
export type RegenerationRequester = "client" | "operator";

export interface RegenerationRequest {
  id: string;
  contentItemId: string;
  sourceFeedbackId: string | null;
  sourceApprovalId: string | null;
  requestedByProfileId: string | null;
  requestedByKind: RegenerationRequester;
  reason: FeedbackReason;
  body: string;
  status: RegenerationStatus;
  acceptedPromptVersionId: string | null;
  resolvedAt: string | null;
  resolvedByProfileId: string | null;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Demo-mode in-memory store. Mirrors the pattern in feedback.ts so the
// agency outputs page can render seeded + freshly-created regeneration
// requests during a dev session.
// ---------------------------------------------------------------------------
const DEMO_REGENERATION_REQUESTS: RegenerationRequest[] = [];

export function _demoPushRegenerationRequest(row: RegenerationRequest): void {
  DEMO_REGENERATION_REQUESTS.push(row);
}

export function _demoUpdateRegenerationRequest(
  id: string,
  patch: Partial<RegenerationRequest>,
): RegenerationRequest | null {
  const idx = DEMO_REGENERATION_REQUESTS.findIndex((r) => r.id === id);
  if (idx === -1) return null;
  DEMO_REGENERATION_REQUESTS[idx] = {
    ...DEMO_REGENERATION_REQUESTS[idx],
    ...patch,
    updatedAt: new Date().toISOString(),
  };
  return DEMO_REGENERATION_REQUESTS[idx];
}

export function _demoListRegenerationRequests(): RegenerationRequest[] {
  return [...DEMO_REGENERATION_REQUESTS];
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------
const SELECT_COLS =
  "id, content_item_id, source_feedback_id, source_approval_id, " +
  "requested_by_profile_id, requested_by_kind, reason, body, status, " +
  "accepted_prompt_version_id, resolved_at, resolved_by_profile_id, " +
  "created_at, updated_at";

function rowToView(r: {
  id: string;
  content_item_id: string;
  source_feedback_id: string | null;
  source_approval_id: string | null;
  requested_by_profile_id: string | null;
  requested_by_kind: RegenerationRequester;
  reason: FeedbackReason;
  body: string;
  status: RegenerationStatus;
  accepted_prompt_version_id: string | null;
  resolved_at: string | null;
  resolved_by_profile_id: string | null;
  created_at: string;
  updated_at: string;
}): RegenerationRequest {
  return {
    id: r.id,
    contentItemId: r.content_item_id,
    sourceFeedbackId: r.source_feedback_id,
    sourceApprovalId: r.source_approval_id,
    requestedByProfileId: r.requested_by_profile_id,
    requestedByKind: r.requested_by_kind,
    reason: r.reason,
    body: r.body,
    status: r.status,
    acceptedPromptVersionId: r.accepted_prompt_version_id,
    resolvedAt: r.resolved_at,
    resolvedByProfileId: r.resolved_by_profile_id,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

export async function listRegenerationRequestsForContent(
  contentItemId: string,
): Promise<RegenerationRequest[]> {
  if (getDataSource() === "demo") {
    return DEMO_REGENERATION_REQUESTS
      .filter((r) => r.contentItemId === contentItemId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("regeneration_requests")
    .select(SELECT_COLS)
    .eq("content_item_id", contentItemId)
    .order("created_at", { ascending: false });

  if (error) throw new SupabaseDataError("listRegenerationRequestsForContent", error);
  if (!data) return [];
  return (data as unknown as Parameters<typeof rowToView>[0][]).map(rowToView);
}

export async function listOpenRegenerationRequestsForContent(
  contentItemId: string,
): Promise<RegenerationRequest[]> {
  const all = await listRegenerationRequestsForContent(contentItemId);
  return all.filter((r) => r.status === "open");
}
