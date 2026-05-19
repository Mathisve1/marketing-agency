// Yuvo Studio — Phase 1C client-portal reads.
//
// CLIENT-SAFE SURFACE. Every function exported here either:
//   (a) returns a `ClientContentView` (already stripped of costs /
//       prompt_summary / internal paths / quality_tier / audio_fixer
//       details), or
//   (b) returns a deliberately minimal portal context (brand display
//       name only — no brand_tone, no audience_assumption, no website).
//
// This is the only module the `web/app/client/[portalSlug]/*` pages
// should import for data. The matching DB-level guarantee lives in
// `client_content_items_v` + the client RLS policies in migration 002.
//
// Supabase reads use the `client_content_items_v` VIEW (not the base
// `content_items` table) so operator-only columns physically cannot be
// projected onto the wire even if a future bug widened the column list.

import type { ClientContentView } from "@/lib/types";
import { toClientContentView } from "@/lib/types";
import {
  DEMO_BRANDS,
  DEMO_CAMPAIGNS,
  DEMO_CONTENT,
} from "@/lib/demo-data";
import { getSupabaseServerClient } from "@/lib/supabase/client";
import type { ClientContentItemRow } from "@/lib/supabase/types";
import { clientContentRowToClientView } from "./mappers";
import { SupabaseDataError, getDataSource } from "./_source";

// Exact column list of the view. Re-stating it here gives us the same
// belt-and-braces guarantee as the view itself. Phase 1O adds
// `client_safe_video_url` (operator-shared MP4 URL).
const CLIENT_CONTENT_SELECT =
  "id, campaign_id, content_calendar_id, title, status, scheduled_for, " +
  "platforms, hook_text, caption_draft, client_safe_poster_url, " +
  "client_safe_video_url, duration_sec";

/** What the client portal layout + overview pages need.
 *  No operator-internal brand fields. */
export interface ClientPortalContext {
  /** The portal's id — uuid in supabase mode, slug in demo mode (the
   *  demo data layer doesn't carry a separate portal entity). Pass
   *  this to content-requests readers; never expose to URL params. */
  portalId: string;
  portalSlug: string;
  campaignId: string;
  brand: {
    id: string;
    name: string;
  };
}

export async function getClientPortalBySlug(
  slug: string,
): Promise<ClientPortalContext | null> {
  if (getDataSource() === "demo") {
    const campaign = DEMO_CAMPAIGNS.find((c) => c.clientPortalSlug === slug);
    if (!campaign) return null;
    const brand = DEMO_BRANDS.find((b) => b.id === campaign.brandId);
    if (!brand) return null;
    return {
      portalId: slug,
      portalSlug: slug,
      campaignId: campaign.id,
      brand: { id: brand.id, name: brand.name },
    };
  }

  // Supabase path. The portal slug uniquely identifies a `client_portals`
  // row; from there we walk to the campaign linked via
  // campaigns.client_portal_id, and look the brand up via clients → brands.
  // Three sequential queries (not joined) so the typescript inference from
  // supabase-js stays simple — embedded PostgREST resources lose row-shape
  // info without a generated Database type, and walking by hand is
  // cheaper than maintaining one in Phase 1C.
  const supabase = getSupabaseServerClient();

  const { data: portalRow, error: portalErr } = await supabase
    .from("client_portals")
    .select("id, client_id")
    .eq("slug", slug)
    .maybeSingle();

  if (portalErr) throw new SupabaseDataError("getClientPortalBySlug", portalErr);
  if (!portalRow) return null;
  const portal = portalRow as unknown as { id: string; client_id: string };

  const { data: clientRow, error: clientErr } = await supabase
    .from("clients")
    .select("brand_id")
    .eq("id", portal.client_id)
    .maybeSingle();

  if (clientErr) throw new SupabaseDataError("getClientPortalBySlug", clientErr);
  if (!clientRow) return null;
  const client = clientRow as unknown as { brand_id: string };

  const { data: brandRow, error: brandErr } = await supabase
    .from("brands")
    .select("id, name")
    .eq("id", client.brand_id)
    .maybeSingle();

  if (brandErr) throw new SupabaseDataError("getClientPortalBySlug", brandErr);
  if (!brandRow) return null;
  const brand = brandRow as unknown as { id: string; name: string };

  // Find the campaign linked to this portal. A portal may host multiple
  // campaigns in the future; Phase 1B's seed has exactly one, so we pick
  // the most-recently-created campaign on the portal.
  const { data: campaignRow, error: campaignErr } = await supabase
    .from("campaigns")
    .select("id")
    .eq("client_portal_id", portal.id)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (campaignErr) throw new SupabaseDataError("getClientPortalBySlug", campaignErr);
  if (!campaignRow) return null;
  const campaign = campaignRow as unknown as { id: string };

  return {
    portalId: portal.id,
    portalSlug: slug,
    campaignId: campaign.id,
    brand: { id: brand.id, name: brand.name },
  };
}

/** Returns only the content items the client is allowed to see (filtered
 *  through the client-safe mapper / view), sorted by scheduled_for ascending. */
export async function getClientVisibleContentItems(
  campaignId: string,
): Promise<ClientContentView[]> {
  if (getDataSource() === "demo") {
    return DEMO_CONTENT
      .filter((c) => c.campaignId === campaignId)
      .map(toClientContentView)
      .filter((v): v is NonNullable<typeof v> => v !== null)
      .sort((a, b) => a.scheduledFor.localeCompare(b.scheduledFor));
  }

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("client_content_items_v")
    .select(CLIENT_CONTENT_SELECT)
    .eq("campaign_id", campaignId)
    .order("scheduled_for", { ascending: true, nullsFirst: true });

  if (error) throw new SupabaseDataError("getClientVisibleContentItems", error);
  if (!data) return [];
  return (data as unknown as ClientContentItemRow[])
    .map(clientContentRowToClientView)
    .filter((v): v is ClientContentView => v !== null);
}

/** Fetch a single content item from the perspective of a specific portal.
 *  The portal ownership check happens here — callers don't need to
 *  re-validate `campaignId`. Returns null when:
 *    - the portal slug is unknown,
 *    - the content id is unknown,
 *    - the content item belongs to a different campaign/portal, OR
 *    - the item is not in the client-visible status set. */
export async function getClientContentItem(
  portalSlug: string,
  contentId: string,
): Promise<ClientContentView | null> {
  if (getDataSource() === "demo") {
    const portal = await getClientPortalBySlug(portalSlug);
    if (!portal) return null;
    const raw = DEMO_CONTENT.find((c) => c.id === contentId);
    if (!raw) return null;
    if (raw.campaignId !== portal.campaignId) return null;
    return toClientContentView(raw);
  }

  // Supabase path: resolve the portal first so we know which campaign_id
  // the contentId must belong to, then select from the view (which
  // already enforces the shared_with_client + status filter).
  const portal = await getClientPortalBySlug(portalSlug);
  if (!portal) return null;

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("client_content_items_v")
    .select(CLIENT_CONTENT_SELECT)
    .eq("id", contentId)
    .eq("campaign_id", portal.campaignId)
    .maybeSingle();

  if (error) throw new SupabaseDataError("getClientContentItem", error);
  if (!data) return null;
  return clientContentRowToClientView(data as unknown as ClientContentItemRow);
}
