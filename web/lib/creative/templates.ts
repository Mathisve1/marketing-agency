// Yuvo Studio — Phase 4D2/4E visual template registry.
//
// Pure constants. No I/O. Lists the template ids the dashboard knows
// how to render per preview mode, plus rich metadata for the
// preview-page selection UI and the export-manifest helper.
//
// Phase 4C ships one rendering component per mode. Phase 4E adds
// **multiple template variants per mode** to the registry so the
// preview UI can offer alternatives. The variants currently MAP to
// the same shared rendering component — the visual difference will
// arrive when the per-variant component lands. Until then, the
// variant choice is metadata-only and surfaces in:
//   - the preview shell chip ("template: …")
//   - the template-options panel
//   - the export manifest
//   - the doc

import type { VisualPreviewMode } from "./visual-preview-types";

export type TemplateStatus = "active" | "planned";

export interface ExportSize {
  width: number;
  height: number;
}

export interface CreativeBriefTemplate {
  id: string;
  label: string;
  mode: VisualPreviewMode;
  description: string;
  bestFor: string;
  aspectRatio: string;
  defaultTheme: string;
  exportSize: ExportSize;
  /** `active` = renders today via the shared component.
   *  `planned` = metadata-only; preview falls back to the active
   *  default for the mode. */
  status: TemplateStatus;
}

const T = (t: CreativeBriefTemplate): CreativeBriefTemplate => t;

export const CREATIVE_BRIEF_TEMPLATES: Record<
  VisualPreviewMode,
  CreativeBriefTemplate[]
> = {
  carousel: [
    T({
      id: "carousel_neutral_v1",
      label: "Neutral 4:5 carousel",
      mode: "carousel",
      description: "5× 4:5 cards on a black/white neutral base.",
      bestFor: "Trust-building, education, ingredient stories.",
      aspectRatio: "4:5",
      defaultTheme: "neutral",
      exportSize: { width: 1080, height: 1350 },
      status: "active",
    }),
    T({
      id: "carousel_editorial_v1",
      label: "Editorial 4:5 carousel",
      mode: "carousel",
      description:
        "Magazine-style headlines on a warm cream base. Type-led.",
      bestFor: "Brand storytelling, founder POV, lifestyle.",
      aspectRatio: "4:5",
      defaultTheme: "editorial",
      exportSize: { width: 1080, height: 1350 },
      status: "planned",
    }),
    T({
      id: "carousel_bold_offer_v1",
      label: "Bold offer 4:5 carousel",
      mode: "carousel",
      description:
        "High-contrast accent block on each slide for an offer / sale.",
      bestFor: "Launches, promos, urgency-led offers.",
      aspectRatio: "4:5",
      defaultTheme: "bold",
      exportSize: { width: 1080, height: 1350 },
      status: "planned",
    }),
  ],
  story: [
    T({
      id: "story_neutral_v1",
      label: "Neutral 9:16 story",
      mode: "story",
      description: "3× 9:16 frames with chrome-safe overlay text.",
      bestFor: "Default story sequence; engagement stickers.",
      aspectRatio: "9:16",
      defaultTheme: "neutral",
      exportSize: { width: 1080, height: 1920 },
      status: "active",
    }),
    T({
      id: "story_minimal_v1",
      label: "Minimal 9:16 story",
      mode: "story",
      description: "Single overlay line, no chrome, lots of negative space.",
      bestFor: "Soft brand moments, quote frames.",
      aspectRatio: "9:16",
      defaultTheme: "soft",
      exportSize: { width: 1080, height: 1920 },
      status: "planned",
    }),
    T({
      id: "story_promo_v1",
      label: "Promo 9:16 story",
      mode: "story",
      description: "Accent banner + countdown-style typography.",
      bestFor: "Launches, restocks, time-limited offers.",
      aspectRatio: "9:16",
      defaultTheme: "bold",
      exportSize: { width: 1080, height: 1920 },
      status: "planned",
    }),
  ],
  feed_post: [
    T({
      id: "feed_post_neutral_v1",
      label: "Neutral 4:5 feed post",
      mode: "feed_post",
      description: "Single 4:5 card; headline-led.",
      bestFor: "Daily feed, evergreen.",
      aspectRatio: "4:5",
      defaultTheme: "neutral",
      exportSize: { width: 1080, height: 1350 },
      status: "active",
    }),
    T({
      id: "feed_post_editorial_v1",
      label: "Editorial 4:5 feed post",
      mode: "feed_post",
      description: "Cream surface + serif headline + photo-led.",
      bestFor: "Founder / brand story, editorial pieces.",
      aspectRatio: "4:5",
      defaultTheme: "editorial",
      exportSize: { width: 1080, height: 1350 },
      status: "planned",
    }),
    T({
      id: "feed_post_offer_v1",
      label: "Offer 4:5 feed post",
      mode: "feed_post",
      description: "High-contrast offer banner + product hero.",
      bestFor: "Promos, launches, bundles.",
      aspectRatio: "4:5",
      defaultTheme: "bold",
      exportSize: { width: 1080, height: 1350 },
      status: "planned",
    }),
  ],
  static_image: [
    T({
      id: "static_image_neutral_v1",
      label: "Neutral 4:5 static",
      mode: "static_image",
      description: "Single 4:5 card; copy-light variant.",
      bestFor: "Image-led post, low-text static.",
      aspectRatio: "4:5",
      defaultTheme: "neutral",
      exportSize: { width: 1080, height: 1350 },
      status: "active",
    }),
  ],
  linkedin_image: [
    T({
      id: "linkedin_neutral_v1",
      label: "Neutral 1:1 LinkedIn companion",
      mode: "linkedin_image",
      description: "Optional 1:1 companion image alongside the post hook.",
      bestFor: "Trust-building, founder POV.",
      aspectRatio: "1:1",
      defaultTheme: "neutral",
      exportSize: { width: 1080, height: 1080 },
      status: "active",
    }),
    T({
      id: "linkedin_thought_leader_v1",
      label: "Thought-leader 1:1 LinkedIn",
      mode: "linkedin_image",
      description:
        "Quote-tile layout with attribution; one stat or pull-quote.",
      bestFor: "Founder essays, opinion posts.",
      aspectRatio: "1:1",
      defaultTheme: "premium_dark",
      exportSize: { width: 1080, height: 1080 },
      status: "planned",
    }),
  ],
  reel_thumbnail: [
    T({
      id: "reel_thumbnail_neutral_v1",
      label: "Neutral 9:16 reel cover",
      mode: "reel_thumbnail",
      description: "9:16 cover frame + on-screen text beats + b-roll cues.",
      bestFor: "Organic reels.",
      aspectRatio: "9:16",
      defaultTheme: "neutral",
      exportSize: { width: 1080, height: 1920 },
      status: "active",
    }),
  ],
  video_thumbnail: [
    T({
      id: "video_thumbnail_neutral_v1",
      label: "Neutral 9:16 video cover",
      mode: "video_thumbnail",
      description: "9:16 cover frame + hook frame + prop brief.",
      bestFor: "Paid UGC videos.",
      aspectRatio: "9:16",
      defaultTheme: "neutral",
      exportSize: { width: 1080, height: 1920 },
      status: "active",
    }),
  ],
  unknown: [],
};

/** Default (active) template id for a given preview mode. Used when
 *  the brief has no `creative_brief_template_id` key (legacy briefs)
 *  or when the recorded id is no longer in the registry. */
export function getDefaultTemplateId(mode: VisualPreviewMode): string | null {
  const list = CREATIVE_BRIEF_TEMPLATES[mode];
  if (!list || list.length === 0) return null;
  const firstActive = list.find((t) => t.status === "active");
  return (firstActive ?? list[0]).id;
}

/** Resolve a brief's recorded template id against the registry; falls
 *  back to the default for the mode. Returns null only when the mode
 *  itself has no templates registered. */
export function resolveTemplateId(
  mode: VisualPreviewMode,
  recordedId: string | null | undefined,
): string | null {
  const list = CREATIVE_BRIEF_TEMPLATES[mode];
  if (!list || list.length === 0) return null;
  if (recordedId && list.some((t) => t.id === recordedId)) return recordedId;
  return getDefaultTemplateId(mode);
}

/** Lookup metadata for a registered template, by id. Null when unknown. */
export function findTemplate(
  mode: VisualPreviewMode,
  id: string | null | undefined,
): CreativeBriefTemplate | null {
  if (!id) return null;
  return (CREATIVE_BRIEF_TEMPLATES[mode] ?? []).find((t) => t.id === id) ?? null;
}

/** Is the given id valid for the mode? Used to decide whether a
 *  `?template=…` querystring override is honoured or surfaced as an
 *  invalid-template warning. */
export function isValidTemplateForMode(
  mode: VisualPreviewMode,
  id: string | null | undefined,
): boolean {
  return findTemplate(mode, id) !== null;
}
