// Yuvo Studio — Phase 4C visual preview types.
//
// Pure types. No code, no I/O. Consumed by the preview builder
// (web/lib/creative/build-visual-preview.ts) and the React template
// components (web/components/creative-preview/*).

export type VisualPreviewMode =
  | "carousel"
  | "story"
  | "feed_post"
  | "static_image"
  | "linkedin_image"
  | "reel_thumbnail"
  | "video_thumbnail"
  | "unknown";

export interface VisualPreviewTheme {
  /** Display name pulled from the content item / brand row. */
  brandName: string | null;
  /** Optional brand color hex (e.g. "#1a1a1a") — falls back to a
   *  neutral black/white palette when unset. */
  primaryColorHex: string | null;
  /** Niche label parsed from the brief (informational chip). */
  niche: string | null;
}

export interface VisualPreviewSlide {
  slideNumber: number;
  headline: string;
  bodyCopy: string | null;
  visualDirection: string | null;
  layoutNote: string | null;
}

export interface VisualPreviewFrame {
  frameNumber: number;
  textOverlay: string | null;
  visualDirection: string | null;
  interactionStickerSuggestion: string | null;
}

/** A single preview asset that one template renders. For carousels and
 *  stories this is the *whole* multi-card preview; for feed/static/
 *  linkedin/thumbnail modes it is the single card preview. */
export interface VisualPreviewAsset {
  /** Caller-supplied id, useful for `key` props. */
  id: string;
  mode: VisualPreviewMode;
  channel: string | null;
  format: string | null;
  title: string;
  /** Short note rendered as a subtitle on every card. */
  subtitle: string | null;
  callToAction: string | null;
  /** Phase 4D2 — resolved template id (default if the brief has none). */
  templateId: string | null;

  // Carousel only
  slides?: VisualPreviewSlide[];
  // Story only
  frames?: VisualPreviewFrame[];
  // feed_post / static_image
  mainVisual?: string | null;
  headlineOverlay?: string | null;
  captionSupport?: string | null;
  compositionNotes?: string | null;
  // linkedin_image
  postHook?: string | null;
  imageConcept?: string | null;
  professionalToneNotes?: string | null;
  // reel_thumbnail / video_thumbnail
  thumbnailConcept?: string | null;
  onScreenTextBeats?: string[];
  brollCues?: string[];
  hookFrame?: string | null;
  propBrief?: string | null;
}

export interface VisualPreviewRenderInput {
  contentItemId: string;
  asset: VisualPreviewAsset;
  theme: VisualPreviewTheme;
  /** Universal warnings for the operator (do-not-include + brief
   *  caveats). Rendered as a small footnote. */
  warnings: string[];
}
