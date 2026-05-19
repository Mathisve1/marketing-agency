// Yuvo Studio — Phase 1C Supabase-row → TS-DTO mappers.
//
// One mapper per (row → existing `web/lib/types.ts` shape) pair. Each
// mapper:
//   - Returns the exact `Brand | Campaign | ContentItem | ClientContentView`
//     interface from `web/lib/types.ts`. Pages don't see Supabase rows.
//   - Performs no I/O. Pure transformation.
//   - Defends against nullable columns by substituting sensible defaults
//     (empty string for required text fields, empty array for platforms,
//     etc.) so the UI doesn't crash on a partial seed.
//
// The CLIENT-SAFE mapper deliberately drops every operator-only field
// (prompt_summary, costs, internal asset paths, quality_tier, audio_fixer
// internals). The view `client_content_items_v` enforces this at the SQL
// layer too — the mapper here is belt + braces.

import type {
  Brand,
  Campaign,
  ContentItem,
  ContentStatus,
  Hook,
  Platform,
  QualityTierId,
  ClientContentView,
} from "@/lib/types";
import { CLIENT_VISIBLE_STATUSES, deriveClientMediaType } from "@/lib/types";
import type {
  BrandRow,
  CampaignWithPortalRow,
  ClientContentItemRow,
  ContentItemRow,
} from "@/lib/supabase/types";

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

const KNOWN_PLATFORMS: ReadonlySet<Platform> = new Set<Platform>([
  "instagram_reels",
  "tiktok",
  "meta_ads",
  "youtube_shorts",
]);

const KNOWN_STATUSES: ReadonlySet<ContentStatus> = new Set<ContentStatus>([
  "draft",
  "generating",
  "raw_ready",
  "audio_fixer_pending",
  "audio_fixed",
  "ready_for_client_review",
  "shared_with_client",
  "approved_by_client",
  "changes_requested_by_client",
  "failed",
]);

const KNOWN_QUALITY_TIERS: ReadonlySet<QualityTierId> = new Set<QualityTierId>([
  "draft_480p",
  "standard_720p",
  "premium_1080p",
]);

function safePlatforms(raw: readonly string[] | null | undefined): Platform[] {
  if (!raw) return [];
  return raw.filter((p): p is Platform =>
    KNOWN_PLATFORMS.has(p as Platform),
  );
}

function safeStatus(raw: string): ContentStatus {
  if (KNOWN_STATUSES.has(raw as ContentStatus)) return raw as ContentStatus;
  // Unknown status from a future schema rev — degrade to `draft` so the UI
  // shows the item but treats it as non-shareable.
  return "draft";
}

function safeQualityTier(
  raw: string | null | undefined,
): QualityTierId {
  if (raw && KNOWN_QUALITY_TIERS.has(raw as QualityTierId)) {
    return raw as QualityTierId;
  }
  return "standard_720p";
}

function safeResolution(
  raw: string | null | undefined,
): "480p" | "720p" | "1080p" {
  if (raw === "480p" || raw === "720p" || raw === "1080p") return raw;
  return "720p";
}

function safeHookSource(
  raw: string | null | undefined,
): Hook["source"] {
  if (raw === "operator" || raw === "ai_suggested" || raw === "client_request") {
    return raw;
  }
  return "operator";
}

// ---------------------------------------------------------------------------
// Brand
// ---------------------------------------------------------------------------
export function brandRowToBrand(row: BrandRow): Brand {
  return {
    id: row.id,
    workspaceId: row.workspace_id,
    name: row.name,
    niche: row.niche ?? "",
    websiteUrl: row.website_url ?? "",
    brandTone: row.brand_tone ?? "",
    audienceAssumption: row.audience_assumption ?? "",
    primaryColorHex: row.primary_color_hex ?? "#000000",
    thumbnailPath: row.thumbnail_path ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Campaign
// ---------------------------------------------------------------------------
/** Maps a campaign row (with embedded `client_portals(slug)`) to the
 *  Phase 1A `Campaign` shape. When no portal is linked yet, the slug
 *  becomes the empty string — pages that route on slug should check
 *  truthiness before linking. */
export function campaignRowToCampaign(row: CampaignWithPortalRow): Campaign {
  return {
    id: row.id,
    brandId: row.brand_id,
    title: row.title,
    strategicPattern: row.strategic_pattern ?? "",
    clientPortalSlug: row.client_portals?.slug ?? "",
    createdAt: row.created_at,
  };
}

// ---------------------------------------------------------------------------
// ContentItem (operator-side)
// ---------------------------------------------------------------------------
/** Maps a full `content_items` row to the operator-side `ContentItem`
 *  shape. Comments and clientRequests stay empty until Phase 1C also
 *  wires the `content_feedback` / `content_requests` reads — Phase 1B's
 *  demo seed has zero rows there. */
export function contentItemRowToContentItem(row: ContentItemRow): ContentItem {
  const hook: Hook = {
    text: row.hook_text ?? "",
    source: safeHookSource(row.hook_source),
  };

  return {
    id: row.id,
    campaignId: row.campaign_id,
    title: row.title,
    platforms: safePlatforms(row.platforms),
    status: safeStatus(row.status),
    scheduledFor: row.scheduled_for ?? "",
    hook,
    captionDraft: row.caption_draft ?? "",
    promptSummary: row.prompt_summary ?? "",
    internalAssetPaths: {
      rawMp4: row.internal_raw_path ?? undefined,
      audioFixedMp4: row.internal_audio_fixed_path ?? undefined,
      thumbnailWebp: row.internal_thumb_path ?? undefined,
    },
    clientSafePosterUrl: row.client_safe_poster_url ?? undefined,
    clientSafeVideoUrl: row.client_safe_video_url ?? undefined,
    qualityTier: safeQualityTier(row.quality_tier),
    durationSec: row.duration_sec ?? 0,
    resolution: safeResolution(row.resolution),
    costEstimateCredits: row.cost_estimate_credits ?? 0,
    costActualCredits: row.cost_actual_credits ?? undefined,
    audioFixer: {
      triggeredManually: row.audio_fixer_triggered,
      completed: row.audio_fixer_completed,
      creditsActual: row.audio_fixer_credits_actual ?? undefined,
    },
    comments: [],
    clientRequests: [],
  };
}

// ---------------------------------------------------------------------------
// ClientContentView (client-safe)
// ---------------------------------------------------------------------------
/** Maps a row from `client_content_items_v` to the client-safe DTO. The
 *  view's SQL definition guarantees operator-only columns are absent;
 *  this mapper is just the typescript-side projection. */
export function clientContentRowToClientView(
  row: ClientContentItemRow,
): ClientContentView | null {
  const status = safeStatus(row.status);
  if (!CLIENT_VISIBLE_STATUSES.has(status)) return null;

  const clientStatus: ClientContentView["status"] =
    status === "approved_by_client"
      ? "approved"
      : status === "changes_requested_by_client"
        ? "changes_requested"
        : "ready_for_review";

  const posterUrl = row.client_safe_poster_url ?? undefined;
  const videoUrl = row.client_safe_video_url ?? undefined;
  return {
    id: row.id,
    title: row.title,
    platforms: safePlatforms(row.platforms),
    status: clientStatus,
    scheduledFor: row.scheduled_for ?? "",
    hook: row.hook_text ?? "",
    captionDraft: row.caption_draft ?? "",
    posterUrl,
    videoUrl,
    mediaType: deriveClientMediaType(videoUrl, posterUrl),
    durationSec: row.duration_sec ?? 0,
    comments: [],
  };
}
