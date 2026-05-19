// Yuvo Studio — Phase 2D multi-format content taxonomy.
//
// Pure constants + helpers. No schema, no DB, no side effects. This is
// the single source of truth the dashboard uses to reason about content
// that is NOT only paid UGC video ads (organic reels, stories, feed
// posts, carousels, LinkedIn/Facebook text, email snippets, etc.).
//
// Mirror: agents/producer/dashboard/content_taxonomy.py — keep the two
// in lockstep when adding values.

export const CONTENT_CHANNELS = [
  "instagram",
  "tiktok",
  "facebook",
  "linkedin",
  "email",
  "website",
  "other",
] as const;
export type ContentChannel = (typeof CONTENT_CHANNELS)[number];

export const CONTENT_FORMATS = [
  "ugc_video_ad",
  "organic_reel",
  "story",
  "feed_post",
  "carousel",
  "static_image",
  "short_video",
  "long_video",
  "text_post",
  "email_snippet",
  "blog_snippet",
] as const;
export type ContentFormat = (typeof CONTENT_FORMATS)[number];

export const DISTRIBUTION_TYPES = [
  "paid",
  "organic",
  "client_review_only",
] as const;
export type DistributionType = (typeof DISTRIBUTION_TYPES)[number];

export const CONTENT_GOALS = [
  "awareness",
  "trust_building",
  "education",
  "offer",
  "launch",
  "testimonial",
  "conversion",
  "community",
  "retention",
] as const;
export type ContentGoal = (typeof CONTENT_GOALS)[number];

export const RECOMMENDED_ASSET_TYPES = [
  "ugc_video",
  "short_video",
  "long_video",
  "static_image",
  "carousel_slides",
  "story_frames",
  "copy_only",
  "email_copy",
  "blog_copy",
] as const;
export type RecommendedAssetType = (typeof RECOMMENDED_ASSET_TYPES)[number];

// --------------------------------------------------------------------------- #
// Format → behaviour helpers

/** Formats that (eventually, behind the existing operator gate) need a
 *  paid video generation. Everything else is copy/visual-brief work. */
const VIDEO_FORMATS: ReadonlySet<ContentFormat> = new Set<ContentFormat>([
  "ugc_video_ad",
  "organic_reel",
  "short_video",
  "long_video",
]);

/** Formats whose operator workflow runs through a prompt_versions row
 *  (the existing prompt editor + review queue). Non-prompt formats use
 *  copy/brief paths instead. */
const PROMPT_FORMATS: ReadonlySet<ContentFormat> = VIDEO_FORMATS;

export function formatNeedsVideoGeneration(format: ContentFormat): boolean {
  return VIDEO_FORMATS.has(format);
}

export function formatNeedsPromptVersion(format: ContentFormat): boolean {
  return PROMPT_FORMATS.has(format);
}

/** Default recommended asset type for a format. */
export function defaultAssetTypeForFormat(
  format: ContentFormat,
): RecommendedAssetType {
  switch (format) {
    case "ugc_video_ad":
      return "ugc_video";
    case "organic_reel":
    case "short_video":
      return "short_video";
    case "long_video":
      return "long_video";
    case "carousel":
      return "carousel_slides";
    case "story":
      return "story_frames";
    case "static_image":
    case "feed_post":
      return "static_image";
    case "text_post":
      return "copy_only";
    case "email_snippet":
      return "email_copy";
    case "blog_snippet":
      return "blog_copy";
    default:
      return "copy_only";
  }
}

export function isContentChannel(v: unknown): v is ContentChannel {
  return (
    typeof v === "string" &&
    (CONTENT_CHANNELS as readonly string[]).includes(v)
  );
}
export function isContentFormat(v: unknown): v is ContentFormat {
  return (
    typeof v === "string" &&
    (CONTENT_FORMATS as readonly string[]).includes(v)
  );
}

// --------------------------------------------------------------------------- #
// Multi-format calendar idea shape (produced by the planner, consumed by
// the Calendar Agent + dashboard).

export interface MultiFormatCalendarIdea {
  dayOffset: number;
  title: string;
  brief: string;
  suggestedChannel: ContentChannel;
  suggestedFormat: ContentFormat;
  distributionType: DistributionType;
  contentGoal: ContentGoal;
  recommendedAssetType: RecommendedAssetType;
  /** Whether this idea will (later, behind the existing operator gate)
   *  need a paid video generation. NEVER auto-triggers anything. */
  needsGeneration: boolean;
  /** Whether this idea's operator workflow runs through a
   *  prompt_versions row (true) vs a copy/brief path (false). */
  needsPromptVersion: boolean;
  operatorNotes: string;
}
