// Yuvo Studio — Phase 4D2 visual template registry.
//
// Pure constants. No I/O. Lists the template ids the dashboard knows
// how to render per preview mode, plus a default per mode.
//
// Phase 4C ships exactly one template per mode (the bundled
// HTML/CSS templates in `web/components/creative-preview/*`). The
// registry is in place so future phases can add variants WITHOUT
// touching the action or parser — just register the id + label and
// extend the renderer switch.

import type { VisualPreviewMode } from "./visual-preview-types";

export interface CreativeBriefTemplate {
  id: string;
  label: string;
  description: string;
}

export const CREATIVE_BRIEF_TEMPLATES: Record<
  VisualPreviewMode,
  CreativeBriefTemplate[]
> = {
  carousel: [
    {
      id: "carousel_neutral_v1",
      label: "Neutral 4:5 carousel",
      description: "5× 4:5 cards on a black/white neutral base.",
    },
  ],
  story: [
    {
      id: "story_neutral_v1",
      label: "Neutral 9:16 story",
      description: "3× 9:16 frames with chrome-safe overlay text.",
    },
  ],
  feed_post: [
    {
      id: "feed_post_neutral_v1",
      label: "Neutral 4:5 feed post",
      description: "Single 4:5 card; headline-led.",
    },
  ],
  static_image: [
    {
      id: "static_image_neutral_v1",
      label: "Neutral 4:5 static",
      description: "Single 4:5 card; copy-light variant.",
    },
  ],
  linkedin_image: [
    {
      id: "linkedin_neutral_v1",
      label: "Neutral 1:1 LinkedIn companion",
      description: "Optional 1:1 companion image alongside the post hook.",
    },
  ],
  reel_thumbnail: [
    {
      id: "reel_thumbnail_neutral_v1",
      label: "Neutral 9:16 reel cover",
      description: "9:16 cover frame + on-screen text beats + b-roll cues.",
    },
  ],
  video_thumbnail: [
    {
      id: "video_thumbnail_neutral_v1",
      label: "Neutral 9:16 video cover",
      description: "9:16 cover frame + hook frame + prop brief.",
    },
  ],
  unknown: [],
};

/** Default template id for a given preview mode. Used when the brief
 *  has no `creative_brief_template_id` key (legacy briefs) or when the
 *  recorded id is no longer in the registry. */
export function getDefaultTemplateId(mode: VisualPreviewMode): string | null {
  const list = CREATIVE_BRIEF_TEMPLATES[mode];
  return list && list.length > 0 ? list[0].id : null;
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
  return list[0].id;
}

/** Lookup metadata for a registered template, by id. Null when unknown. */
export function findTemplate(
  mode: VisualPreviewMode,
  id: string | null | undefined,
): CreativeBriefTemplate | null {
  if (!id) return null;
  return (CREATIVE_BRIEF_TEMPLATES[mode] ?? []).find((t) => t.id === id) ?? null;
}
