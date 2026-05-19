// Yuvo Studio — Phase 1C Supabase row types.
//
// These are the *narrowed* row shapes the data layer actually reads. They
// are NOT a full generated `Database` type from the Supabase CLI — that
// would couple us to the CLI being on PATH, and it would over-fit to
// schema rev 001 + 002. Instead we define a hand-rolled, intentionally
// minimal projection per table: only the columns `web/lib/data/*` selects.
//
// When the schema evolves, update the per-table type below + the matching
// SELECT string used in `web/lib/data/*` together. Compilation will fail
// loudly if a SELECT picks up a column not declared here.
//
// All `*_at` fields are ISO8601 timestamptz strings as returned by
// supabase-js's default JSON serializer. Dates are plain `YYYY-MM-DD`.

export interface BrandRow {
  id: string;
  workspace_id: string;
  name: string;
  niche: string | null;
  website_url: string | null;
  brand_tone: string | null;
  audience_assumption: string | null;
  primary_color_hex: string | null;
  thumbnail_path: string | null;
}

export interface CampaignRow {
  id: string;
  brand_id: string;
  client_portal_id: string | null;
  title: string;
  strategic_pattern: string | null;
  created_at: string;
}

/** A campaign row joined to its portal slug. We embed the portal row via
 *  PostgREST resource embedding (`client_portals(slug)`) so a single
 *  `select=...` round-trips both. */
export interface CampaignWithPortalRow extends CampaignRow {
  client_portals: { slug: string } | null;
}

export interface ClientPortalRow {
  id: string;
  client_id: string;
  slug: string;
  status: "active" | "paused" | "archived";
}

/** Operator-side content_items row: every column the data layer reads.
 *  The operator UI is the only consumer; the client surface never
 *  selects from this table — it uses `client_content_items_v` instead. */
export interface ContentItemRow {
  id: string;
  campaign_id: string;
  content_calendar_id: string | null;
  title: string;
  status: string;
  scheduled_for: string | null;
  platforms: string[];
  hook_text: string | null;
  hook_source: "operator" | "ai_suggested" | "client_request" | null;
  caption_draft: string | null;
  prompt_summary: string | null;
  quality_tier: "draft_480p" | "standard_720p" | "premium_1080p";
  resolution: "480p" | "720p" | "1080p" | null;
  duration_sec: number | null;
  cost_estimate_credits: number | null;
  cost_actual_credits: number | null;
  internal_raw_path: string | null;
  internal_audio_fixed_path: string | null;
  internal_thumb_path: string | null;
  client_safe_poster_url: string | null;
  client_safe_video_url: string | null;
  shared_with_client: boolean;
  audio_fixer_triggered: boolean;
  audio_fixer_completed: boolean;
  audio_fixer_credits_actual: number | null;
}

/** Client-safe projection — exactly the columns exposed by the
 *  `client_content_items_v` view in migrations 001 + 006. By construction
 *  this type cannot include `prompt_summary`, `cost_*`, `internal_*`,
 *  `quality_tier`, or any audio-fixer column. Phase 1O adds the
 *  operator-shared MP4 URL via `client_safe_video_url`. */
export interface ClientContentItemRow {
  id: string;
  campaign_id: string;
  content_calendar_id: string | null;
  title: string;
  status: string;
  scheduled_for: string | null;
  platforms: string[];
  hook_text: string | null;
  caption_draft: string | null;
  client_safe_poster_url: string | null;
  duration_sec: number | null;
  /** Phase 1O — CDN MP4 URL the operator has explicitly shared. Null
   *  when there's no playable video yet (image fallback). */
  client_safe_video_url: string | null;
}
