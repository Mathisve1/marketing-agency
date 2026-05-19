// Yuvo Studio — Phase 1E content_requests readers.
//
// content_requests is the table behind the client portal's "What would
// you like to see next week?" form. The table existed since Phase 1B
// (migration 001) but Phase 1D shipped with the form inert; Phase 1E
// wires both sides.
//
// Two readers, both demo/supabase aware:
//   - listContentRequestsForPortal(portalId)
//       What the client sees on their portal — only requests scoped to
//       their portal.
//   - listContentRequestsForWorkspace(workspaceId)
//       What the operator sees on the agency dashboard — all incoming
//       requests across every portal owned by their workspace.
//
// The wire shape is intentionally identical for both personas; the only
// difference is which client_portal_id the rows are scoped to.

import { getDataSource, SupabaseDataError } from "./_source";
import { getSupabaseServerClient } from "@/lib/supabase/client";

export interface ContentRequest {
  id: string;
  clientPortalId: string;
  brandId: string;
  profileId: string | null;
  body: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Demo-mode in-memory store.
//
// The Phase 1A demo seed didn't include content_requests rows (the form
// was inert), so the store starts empty. createContentRequestAction
// (defined in web/lib/actions/content-requests.ts) pushes new rows here.
// ---------------------------------------------------------------------------
const DEMO_CONTENT_REQUESTS: ContentRequest[] = [];

export function _demoPushContentRequest(row: ContentRequest): void {
  DEMO_CONTENT_REQUESTS.push(row);
}

export function _demoListContentRequests(): ContentRequest[] {
  return [...DEMO_CONTENT_REQUESTS];
}

/** Demo-mode portal id → brand id resolver, so the agency dashboard
 *  reader can echo a stable brand_id back even though demo mode has no
 *  Supabase row for the portal. */
function _demoBrandIdForPortal(portalSlug: string): string | null {
  // Demo seed has one brand (Pai) and one portal slug. Hard-coding the
  // mapping is OK here because the demo seed is a single line of code.
  if (portalSlug === "pai-skincare-demo") return "brand_pai";
  return null;
}
export const _DEMO_PORTAL_BRAND_RESOLVER = _demoBrandIdForPortal;

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------
export async function listContentRequestsForPortal(
  portalId: string,
): Promise<ContentRequest[]> {
  if (getDataSource() === "demo") {
    // Demo mode uses the portal SLUG as the identifier — the demo seed
    // has only one portal, so we treat the slug == id collision as
    // intentional and accept either form. Real supabase mode uses uuid.
    return DEMO_CONTENT_REQUESTS
      .filter((r) => r.clientPortalId === portalId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("content_requests")
    .select("id, client_portal_id, brand_id, profile_id, body, created_at")
    .eq("client_portal_id", portalId)
    .order("created_at", { ascending: false });

  if (error) throw new SupabaseDataError("listContentRequestsForPortal", error);
  if (!data) return [];
  return (data as unknown as Array<{
    id: string;
    client_portal_id: string;
    brand_id: string;
    profile_id: string | null;
    body: string;
    created_at: string;
  }>).map((r) => ({
    id: r.id,
    clientPortalId: r.client_portal_id,
    brandId: r.brand_id,
    profileId: r.profile_id,
    body: r.body,
    createdAt: r.created_at,
  }));
}

export async function listContentRequestsForWorkspace(
  workspaceId: string,
): Promise<ContentRequest[]> {
  if (getDataSource() === "demo") {
    // Demo mode: one workspace, one brand, so return every request that
    // hit any portal. Phase 1F can refine if a second brand is added.
    return [..._demoListContentRequests()].sort((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
  }

  // Walk: workspace → brands → ids; brands.workspace_id is set on every
  // brand row, and content_requests carries brand_id, so the workspace
  // filter is a single IN clause once we have the brand list.
  const supabase = getSupabaseServerClient();

  const { data: brandRows, error: brandErr } = await supabase
    .from("brands")
    .select("id")
    .eq("workspace_id", workspaceId);
  if (brandErr) throw new SupabaseDataError("listContentRequestsForWorkspace", brandErr);
  const brandIds = (brandRows ?? []).map((r) => (r as { id: string }).id);
  if (brandIds.length === 0) return [];

  const { data, error } = await supabase
    .from("content_requests")
    .select("id, client_portal_id, brand_id, profile_id, body, created_at")
    .in("brand_id", brandIds)
    .order("created_at", { ascending: false });

  if (error) throw new SupabaseDataError("listContentRequestsForWorkspace", error);
  if (!data) return [];
  return (data as unknown as Array<{
    id: string;
    client_portal_id: string;
    brand_id: string;
    profile_id: string | null;
    body: string;
    created_at: string;
  }>).map((r) => ({
    id: r.id,
    clientPortalId: r.client_portal_id,
    brandId: r.brand_id,
    profileId: r.profile_id,
    body: r.body,
    createdAt: r.created_at,
  }));
}
