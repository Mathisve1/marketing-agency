// Yuvo Studio — Phase 1A shared types.
//
// These shapes deliberately mirror the future Supabase schema (Phase 1B):
// Workspace -> Brand -> Campaign -> Content -> Asset, plus a Client Portal
// view that hides operator internals. Until Supabase lands, the records
// are seeded in lib/demo-data.ts.

export type QualityTierId = "draft_480p" | "standard_720p" | "premium_1080p";

export interface QualityTier {
  id: QualityTierId;
  label: string;
  resolution: "480p" | "720p" | "1080p";
  perSecondCredits: number;
  fifteenSecondCredits: number;
  useCase: string;
  audioFixer: "manual";
  isDefault: boolean;
  warning?: string;
}

/** Operator-only status for a Content item. The Client Portal sees a
 *  reduced set: only `ready_for_client_review`, `shared_with_client`,
 *  `approved_by_client`, `changes_requested_by_client`. */
export type ContentStatus =
  | "draft"
  | "generating"
  | "raw_ready"
  | "audio_fixer_pending"
  | "audio_fixed"
  | "ready_for_client_review"
  | "shared_with_client"
  | "approved_by_client"
  | "changes_requested_by_client"
  | "failed";

/** Maps an operator status to the public-facing client status. */
export const CLIENT_VISIBLE_STATUSES: ReadonlySet<ContentStatus> = new Set([
  "shared_with_client",
  "approved_by_client",
  "changes_requested_by_client",
]);

export type Platform =
  | "instagram_reels"
  | "tiktok"
  | "meta_ads"
  | "youtube_shorts";

export interface Workspace {
  id: string;
  name: string;
  agencyName: string;
}

export interface Brand {
  id: string;
  workspaceId: string;
  name: string;
  niche: string;
  websiteUrl: string;
  brandTone: string;
  audienceAssumption: string;
  primaryColorHex: string;
  thumbnailPath?: string;
}

export interface Campaign {
  id: string;
  brandId: string;
  title: string;
  strategicPattern: string;
  clientPortalSlug: string;
  createdAt: string;
}

export interface Hook {
  text: string;
  source: "operator" | "ai_suggested" | "client_request";
}

export interface ContentItem {
  id: string;
  campaignId: string;
  title: string;
  platforms: Platform[];
  status: ContentStatus;
  scheduledFor: string; // ISO date (calendar slot)
  hook: Hook;
  captionDraft: string;
  promptSummary: string;
  /** Operator-only: source file paths on the local FS. The client portal
   *  never sees these. */
  internalAssetPaths: {
    rawMp4?: string;
    audioFixedMp4?: string;
    thumbnailWebp?: string;
  };
  /** Public-safe poster image URL the portal embeds. */
  clientSafePosterUrl?: string;
  /** Phase 1O — public-safe CDN MP4 URL the operator has explicitly
   *  shared with the client. The portal renders `<video>` when set. */
  clientSafeVideoUrl?: string;
  qualityTier: QualityTierId;
  durationSec: number;
  resolution: "480p" | "720p" | "1080p";
  /** Cost in Enhancor credits — OPERATOR-ONLY field. Never echoed in the
   *  client portal. The Client view selects a different DTO. */
  costEstimateCredits: number;
  costActualCredits?: number;
  audioFixer: {
    triggeredManually: boolean;
    completed: boolean;
    creditsActual?: number;
  };
  /** Comments are shared across operator + client (the client writes them;
   *  the operator reads them). */
  comments: Array<{
    author: "client" | "operator";
    body: string;
    createdAt: string;
  }>;
  /** Client requests stay in the same comments thread but with
   *  `intent: "request_changes" | "request_next_week"`. */
  clientRequests: Array<{
    intent: "request_changes" | "request_next_week";
    body: string;
    createdAt: string;
  }>;
}

/** What the client portal renders for a single content item. Phase 1O. */
export type ClientMediaType = "video" | "image" | "none";

/** Strictly client-safe projection. Use this DTO when rendering anything
 *  under /client/[portalSlug]. Phase 1O adds `videoUrl` + `mediaType`
 *  so the portal can render a real `<video>` with poster fallback. */
export interface ClientContentView {
  id: string;
  title: string;
  platforms: Platform[];
  status: "ready_for_review" | "approved" | "changes_requested";
  scheduledFor: string;
  hook: string;
  captionDraft: string;
  posterUrl?: string;
  /** Public CDN MP4 URL. Absent when the operator hasn't shared a
   *  playable video yet (image fallback). */
  videoUrl?: string;
  /** Derived: "video" iff videoUrl is set, else "image" iff posterUrl
   *  is set, else "none". Pages branch on this. */
  mediaType: ClientMediaType;
  durationSec: number;
  comments: Array<{ author: "client" | "operator"; body: string; createdAt: string }>;
}

/** Helper: derive ClientMediaType from raw URLs. Used by both demo and
 *  supabase mappers so the rule lives in one place. */
export function deriveClientMediaType(
  videoUrl: string | null | undefined,
  posterUrl: string | null | undefined,
): ClientMediaType {
  if (videoUrl) return "video";
  if (posterUrl) return "image";
  return "none";
}

/** Convert an operator-side ContentItem to the client-safe DTO. Drops
 *  costs, provider details, internal paths, and any non-shared status.
 *  Phase 1O: demo mode has no operator-shared video URL on the in-memory
 *  ContentItem yet, so videoUrl is undefined here; the supabase mapper
 *  in lib/data/mappers.ts is the path that emits real video URLs. */
export function toClientContentView(c: ContentItem): ClientContentView | null {
  if (!CLIENT_VISIBLE_STATUSES.has(c.status)) return null;
  const clientStatus: ClientContentView["status"] =
    c.status === "approved_by_client"
      ? "approved"
      : c.status === "changes_requested_by_client"
      ? "changes_requested"
      : "ready_for_review";
  return {
    id: c.id,
    title: c.title,
    platforms: c.platforms,
    status: clientStatus,
    scheduledFor: c.scheduledFor,
    hook: c.hook.text,
    captionDraft: c.captionDraft,
    posterUrl: c.clientSafePosterUrl,
    videoUrl: c.clientSafeVideoUrl,
    mediaType: deriveClientMediaType(c.clientSafeVideoUrl, c.clientSafePosterUrl),
    durationSec: c.durationSec,
    comments: c.comments,
  };
}
