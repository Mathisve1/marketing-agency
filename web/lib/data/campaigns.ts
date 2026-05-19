// Yuvo Studio — Phase 1C campaign reads.
//
// OPERATOR SURFACE. `getCampaignContentItems` returns the full ContentItem
// (costs, prompt_summary, internal asset paths). Do not import this module
// from anything under `web/app/client/*` — that surface uses
// `getClientVisibleContentItems` from `./content` instead, which drops
// every operator-only field via the client-safe view + mapper.

import type { Campaign, ContentItem } from "@/lib/types";
import { DEMO_CAMPAIGNS, DEMO_CONTENT } from "@/lib/demo-data";
import { getSupabaseServerClient } from "@/lib/supabase/client";
import type {
  CampaignWithPortalRow,
  ContentItemRow,
} from "@/lib/supabase/types";
import { campaignRowToCampaign, contentItemRowToContentItem } from "./mappers";
import { SupabaseDataError, getDataSource } from "./_source";

// PostgREST resource-embedding: pull the linked client_portals.slug in the
// same round-trip so the mapper can populate Campaign.clientPortalSlug.
const CAMPAIGN_SELECT =
  "id, brand_id, client_portal_id, title, strategic_pattern, created_at, " +
  "client_portals(slug)";

const CONTENT_ITEM_SELECT =
  "id, campaign_id, content_calendar_id, title, status, scheduled_for, " +
  "platforms, hook_text, hook_source, caption_draft, prompt_summary, " +
  "quality_tier, resolution, duration_sec, cost_estimate_credits, " +
  "cost_actual_credits, internal_raw_path, internal_audio_fixed_path, " +
  "internal_thumb_path, client_safe_poster_url, client_safe_video_url, " +
  "shared_with_client, audio_fixer_triggered, audio_fixer_completed, " +
  "audio_fixer_credits_actual";

export async function getCampaignById(
  campaignId: string,
): Promise<Campaign | null> {
  if (getDataSource() === "demo") {
    return DEMO_CAMPAIGNS.find((c) => c.id === campaignId) ?? null;
  }

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("campaigns")
    .select(CAMPAIGN_SELECT)
    .eq("id", campaignId)
    .maybeSingle();

  if (error) throw new SupabaseDataError("getCampaignById", error);
  if (!data) return null;
  return campaignRowToCampaign(data as unknown as CampaignWithPortalRow);
}

/** Returns full operator-side ContentItem rows for the given campaign,
 *  sorted by scheduled_for ascending. */
export async function getCampaignContentItems(
  campaignId: string,
): Promise<ContentItem[]> {
  if (getDataSource() === "demo") {
    return DEMO_CONTENT
      .filter((c) => c.campaignId === campaignId)
      .sort((a, b) => a.scheduledFor.localeCompare(b.scheduledFor));
  }

  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase
    .from("content_items")
    .select(CONTENT_ITEM_SELECT)
    .eq("campaign_id", campaignId)
    .order("scheduled_for", { ascending: true, nullsFirst: true });

  if (error) throw new SupabaseDataError("getCampaignContentItems", error);
  if (!data) return [];
  return (data as unknown as ContentItemRow[]).map(contentItemRowToContentItem);
}
