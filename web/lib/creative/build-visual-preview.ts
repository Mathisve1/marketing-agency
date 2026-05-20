// Yuvo Studio — Phase 4C visual preview builder.
//
// Pure, deterministic. No I/O, no fetch, no DB call. Takes a parsed
// creative brief (from web/lib/creative/creative-brief-parser.ts) plus
// content-item / brand metadata and produces a render-ready
// VisualPreviewRenderInput. The React templates consume that and
// render an internal-only preview — no PNG, no export, no client share.

import type { ParsedCreativeBrief, CreativeBriefMode } from "./creative-brief-parser";
import { resolveTemplateId } from "./templates";
import type {
  VisualPreviewAsset,
  VisualPreviewMode,
  VisualPreviewRenderInput,
  VisualPreviewTheme,
} from "./visual-preview-types";

export interface BuildVisualPreviewInput {
  contentItemId: string;
  contentItemTitle: string | null;
  brandName?: string | null;
  campaignName?: string | null;
  brandPrimaryColorHex?: string | null;
  brandNiche?: string | null;
  /** Result of `parseCreativeBriefBlock(contentItem.promptSummary)`.
   *  May be null when the operator has not run the brief yet. */
  brief: ParsedCreativeBrief | null;
}

function modeForMissingBrief(): VisualPreviewMode {
  return "unknown";
}

function previewModeFromBriefMode(mode: CreativeBriefMode): VisualPreviewMode {
  switch (mode) {
    case "carousel": return "carousel";
    case "story": return "story";
    case "feed_post": return "feed_post";
    case "static_image": return "static_image";
    case "linkedin_text": return "linkedin_image";
    case "reel_support": return "reel_thumbnail";
    case "video_visual_support": return "video_thumbnail";
    case "copy_only":
    case "unknown":
    default:
      return "unknown";
  }
}

export function buildVisualPreview(input: BuildVisualPreviewInput): VisualPreviewRenderInput {
  const theme: VisualPreviewTheme = {
    brandName: input.brandName ?? null,
    primaryColorHex: input.brandPrimaryColorHex ?? null,
    niche: input.brandNiche ?? null,
  };

  const title = input.brief?.title ?? input.contentItemTitle ?? "Untitled content item";
  const subtitleParts: string[] = [];
  if (input.brandName) subtitleParts.push(input.brandName);
  if (input.campaignName) subtitleParts.push(input.campaignName);
  const subtitle = subtitleParts.length > 0 ? subtitleParts.join(" · ") : null;

  const warnings: string[] = [];
  if (input.brief?.doNotInclude && input.brief.doNotInclude.length > 0) {
    warnings.push(...input.brief.doNotInclude.map((d) => `do not: ${d}`));
  }
  warnings.push("Internal preview only — not exported, not shared with the client.");

  if (!input.brief) {
    // No brief yet — degrade to an unknown placeholder asset.
    const asset: VisualPreviewAsset = {
      id: input.contentItemId,
      mode: modeForMissingBrief(),
      channel: null,
      format: null,
      title,
      subtitle,
      callToAction: null,
      templateId: null,
    };
    return { contentItemId: input.contentItemId, asset, theme, warnings };
  }

  const mode = previewModeFromBriefMode(input.brief.mode);
  const asset: VisualPreviewAsset = {
    id: input.contentItemId,
    mode,
    channel: input.brief.channel,
    format: input.brief.format,
    title,
    subtitle,
    callToAction: input.brief.callToAction,
    templateId: resolveTemplateId(mode, input.brief.templateId),
  };

  switch (mode) {
    case "carousel":
      asset.slides = (input.brief.slides ?? []).map((s) => ({
        slideNumber: s.slideNumber,
        headline: s.headline,
        bodyCopy: s.bodyCopy,
        visualDirection: s.visualDirection,
        layoutNote: s.layoutNote,
      }));
      // Fallback: if no slides parsed, synthesize one from creativeDirection.
      if (asset.slides.length === 0) {
        asset.slides = [
          {
            slideNumber: 1,
            headline: title,
            bodyCopy: input.brief.creativeDirection,
            visualDirection: input.brief.visualConcept,
            layoutNote: input.brief.layoutType,
          },
        ];
      }
      break;
    case "story":
      asset.frames = (input.brief.frames ?? []).map((f) => ({
        frameNumber: f.frameNumber,
        textOverlay: f.textOverlay,
        visualDirection: f.visualDirection,
        interactionStickerSuggestion: f.interactionStickerSuggestion,
      }));
      if (asset.frames.length === 0) {
        asset.frames = [
          {
            frameNumber: 1,
            textOverlay: title,
            visualDirection: input.brief.visualConcept,
            interactionStickerSuggestion: null,
          },
        ];
      }
      break;
    case "feed_post":
    case "static_image":
      asset.mainVisual = input.brief.mainVisual ?? input.brief.visualConcept;
      asset.headlineOverlay = input.brief.headlineOverlay ?? title;
      asset.captionSupport = input.brief.captionSupport;
      asset.compositionNotes =
        input.brief.compositionNotes ?? input.brief.creativeDirection;
      break;
    case "linkedin_image":
      asset.postHook = input.brief.postHook ?? title;
      asset.imageConcept = input.brief.imageConcept ?? input.brief.visualConcept;
      asset.professionalToneNotes = input.brief.professionalToneNotes;
      break;
    case "reel_thumbnail":
      asset.thumbnailConcept =
        input.brief.thumbnailConcept ?? input.brief.visualConcept;
      asset.onScreenTextBeats = input.brief.onScreenTextBeats ?? [];
      asset.brollCues = input.brief.brollCues ?? [];
      break;
    case "video_thumbnail":
      asset.thumbnailConcept =
        input.brief.thumbnailConcept ?? input.brief.visualConcept;
      asset.hookFrame = input.brief.hookFrame ?? null;
      asset.propBrief = input.brief.propBrief ?? null;
      break;
    case "unknown":
      // No per-mode payload; the renderer will show a generic card.
      break;
  }

  return { contentItemId: input.contentItemId, asset, theme, warnings };
}
