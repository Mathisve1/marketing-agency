// Pai Skincare demo seed for the Phase 1A scaffold.
//
// This is local-state-only data. The real Phase 1B implementation reads
// equivalent rows from Supabase. No file paths are loaded as live MP4s in
// the Next.js app — internalAssetPaths are operator-only audit trail.

import type { Brand, Campaign, ContentItem, Workspace } from "./types";

export const DEMO_WORKSPACE: Workspace = {
  id: "ws_yuvo",
  name: "Yuvo Studio",
  agencyName: "Yuvo Studio",
};

export const DEMO_BRANDS: Brand[] = [
  {
    id: "brand_pai",
    workspaceId: DEMO_WORKSPACE.id,
    name: "Pai Skincare",
    niche: "premium organic skincare",
    websiteUrl: "https://www.paiskincare.com",
    brandTone: "Calm, considered, science-led, gentle.",
    audienceAssumption:
      "Women 28–50, sensitive/reactive skin, organic / clean values.",
    primaryColorHex: "#3C4A3B",
    thumbnailPath: "/demo/pai-thumb.webp",
  },
];

export const DEMO_CAMPAIGNS: Campaign[] = [
  {
    id: "camp_pai_route01",
    brandId: "brand_pai",
    title: "Route 01 — UGC ingredient-led sensitive-skin serum",
    strategicPattern: "ingredient_proof",
    clientPortalSlug: "pai-skincare-demo",
    createdAt: "2026-05-16T08:00:00Z",
  },
];

export const DEMO_CONTENT: ContentItem[] = [
  {
    id: "content_pai_route01_v1",
    campaignId: "camp_pai_route01",
    title: "What I keep coming back to — 15s UGC product talk",
    platforms: ["instagram_reels", "tiktok", "meta_ads"],
    // Demo state: this is the audio-fixed Pai test landed on 2026-05-16.
    // It is "shared with client" in the demo seed so the client portal has
    // a visible item out of the box.
    status: "shared_with_client",
    scheduledFor: "2026-05-20",
    hook: {
      text: "I like that this feels simple — one serum, a few ingredients I can actually understand.",
      source: "operator",
    },
    captionDraft:
      "One serum. Ingredients you can read. A routine that doesn't feel like too much. — for sensitive, reactive skin. #skincare #sensitiveskin",
    promptSummary:
      "UGC product-talk, 15s, 720p target on next runs. Real-friend register; calm British VO laid in post. No competitor reference uploaded.",
    internalAssetPaths: {
      rawMp4:
        "prospects/pai-skincare/production/clips/route_01_enhancor_ugc_raw_15s.mp4",
      audioFixedMp4:
        "prospects/pai-skincare/production/clips/route_01_enhancor_ugc_audiofixed_15s.mp4",
      thumbnailWebp:
        "prospects/pai-skincare/production/clips/route_01_enhancor_ugc_thumb.webp",
    },
    clientSafePosterUrl: "/demo/pai-thumb.webp",
    // The Pai test was generated at 1080p. The product strategy locks the
    // *default for future runs* at Standard 720p; we record this item's
    // realized resolution honestly.
    qualityTier: "premium_1080p",
    durationSec: 15,
    resolution: "1080p",
    costEstimateCredits: 5940,
    costActualCredits: 5940,
    audioFixer: {
      triggeredManually: true,
      completed: true,
      creditsActual: 2104, // 2103.75 rounded
    },
    comments: [],
    clientRequests: [],
  },
  {
    id: "content_pai_route02_draft",
    campaignId: "camp_pai_route01",
    title: "Next week — ingredient close-up variant",
    platforms: ["instagram_reels", "tiktok"],
    // Phase 1O — operator has shared the 720p Seedance output with the
    // client for review. Status flipped from 'draft' so the client
    // portal renders it. The CDN MP4 URL below is the real Cloudfront
    // location of the completed 720p job (provider request
    // 6a09e50d13b1573a3bc1cd17, job 1b1b1b1b-...).
    status: "shared_with_client",
    scheduledFor: "2026-05-27",
    hook: {
      text: "Three ingredients. That's it.",
      source: "operator",
    },
    captionDraft:
      "Rosehip BioRegenerate. The whole story in one bottle. — made for sensitive skin.",
    promptSummary:
      "Macro-led variant. Standard 720p planned. Audio Fixer manual after raw review.",
    internalAssetPaths: {},
    clientSafePosterUrl:
      "https://d2i9jqncnkplwq.cloudfront.net/thumbnails/0a8813b8-76de-4d8a-bd6a-540c59880251.webp",
    clientSafeVideoUrl:
      "https://d2i9jqncnkplwq.cloudfront.net/videos/4e5a03d2-c5b7-418c-9897-90cf3cd60f2c.mp4",
    qualityTier: "standard_720p",
    durationSec: 15,
    resolution: "720p",
    costEstimateCredits: 2646,
    audioFixer: { triggeredManually: false, completed: false },
    comments: [],
    clientRequests: [],
  },
];

// ----- Lookup helpers -----
export function getBrand(brandId: string): Brand | undefined {
  return DEMO_BRANDS.find((b) => b.id === brandId);
}

export function getCampaign(campaignId: string): Campaign | undefined {
  return DEMO_CAMPAIGNS.find((c) => c.id === campaignId);
}

export function getCampaignByPortalSlug(slug: string): Campaign | undefined {
  return DEMO_CAMPAIGNS.find((c) => c.clientPortalSlug === slug);
}

export function getContent(contentId: string): ContentItem | undefined {
  return DEMO_CONTENT.find((c) => c.id === contentId);
}

export function listContentForCampaign(campaignId: string): ContentItem[] {
  return DEMO_CONTENT.filter((c) => c.campaignId === campaignId).sort(
    (a, b) => a.scheduledFor.localeCompare(b.scheduledFor),
  );
}
