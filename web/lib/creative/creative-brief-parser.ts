// Yuvo Studio — Phase 4C Creative Brief parser.
//
// Pure, deterministic. No I/O, no fetch, no paid call.
//
// Parses the `[creative brief]` provenance block that Phase 4A writes
// into `content_items.prompt_summary`. The block has two layers:
//
//   1. A fixed structured-key header (very reliable — written by code):
//        creative_brief_status: drafted
//        creative_brief_source: social_creative_brief_agent
//        creative_brief_format: <ContentFormat>
//        creative_brief_channel: <ContentChannel>
//        creative_brief_mode: <CreativeBriefMode>
//        creative_brief_created_at: <ISO timestamp>
//        creative_brief_operator_note: <optional>
//
//   2. A human-readable markdown body (best-effort — produced by
//      `renderCreativeBriefMarkdown`). We extract per-section text
//      using section-heading anchors, but every field falls back to
//      `null` / `[]` if absent so the renderer can degrade gracefully.
//
// The parser NEVER throws. Bad / missing input returns an empty result.

export type CreativeBriefStatus = "none" | "drafted";

export type CreativeBriefMode =
  | "carousel"
  | "story"
  | "feed_post"
  | "static_image"
  | "linkedin_text"
  | "reel_support"
  | "video_visual_support"
  | "copy_only"
  | "unknown";

export interface ParsedCarouselSlide {
  slideNumber: number;
  headline: string;
  bodyCopy: string | null;
  visualDirection: string | null;
  layoutNote: string | null;
}

export interface ParsedStoryFrame {
  frameNumber: number;
  textOverlay: string | null;
  visualDirection: string | null;
  interactionStickerSuggestion: string | null;
}

export interface ParsedCreativeBrief {
  /** Always set when `hasCreativeBrief` is true. */
  status: "drafted";
  source: string | null;
  format: string | null;
  channel: string | null;
  mode: CreativeBriefMode;
  createdAt: string | null;
  operatorNote: string | null;
  /** Phase 4D2 — optional template id chosen for this brief. Legacy
   *  briefs (Phase 4A → 4C) have null here; the renderer falls back
   *  to `getDefaultTemplateId(mode)`. */
  templateId: string | null;

  // Markdown-derived fields (best-effort, may be null/empty)
  title: string | null;
  creativeDirection: string | null;
  visualConcept: string | null;
  layoutType: string | null;
  assetRequirements: string[];
  copyPlacement: string | null;
  brandElements: string[];
  doNotInclude: string[];
  callToAction: string | null;
  shotOrDesignNotes: string | null;

  // Per-mode (exactly one populated for a valid brief)
  slides?: ParsedCarouselSlide[];      // carousel
  frames?: ParsedStoryFrame[];         // story
  mainVisual?: string | null;          // feed_post / static_image
  headlineOverlay?: string | null;     // feed_post / static_image
  captionSupport?: string | null;      // feed_post / static_image
  compositionNotes?: string | null;    // feed_post / static_image
  postHook?: string | null;            // linkedin_text
  imageConcept?: string | null;        // linkedin_text
  professionalToneNotes?: string | null; // linkedin_text
  thumbnailConcept?: string | null;    // reel_support / video_visual_support
  onScreenTextBeats?: string[];        // reel_support
  brollCues?: string[];                // reel_support
  hookFrame?: string | null;           // video_visual_support
  propBrief?: string | null;           // video_visual_support

  /** Raw markdown body of the brief (after the structured keys), for
   *  templates that prefer to render the original text verbatim. */
  rawMarkdown: string;
}

const BRIEF_MARKER = "\n\n[creative brief]\n";

const VALID_MODES = new Set<CreativeBriefMode>([
  "carousel",
  "story",
  "feed_post",
  "static_image",
  "linkedin_text",
  "reel_support",
  "video_visual_support",
  "copy_only",
]);

function asMode(v: string | null): CreativeBriefMode {
  if (!v) return "unknown";
  return (VALID_MODES as Set<string>).has(v) ? (v as CreativeBriefMode) : "unknown";
}

/** True iff a `[creative brief]` block is present. */
export function hasCreativeBrief(promptSummary: string | null | undefined): boolean {
  return Boolean(promptSummary && promptSummary.includes("[creative brief]"));
}

/** "drafted" iff a `creative_brief_status: drafted` line is present in
 *  the brief block. Lightweight — does NOT parse the markdown. */
export function getCreativeBriefStatus(
  promptSummary: string | null | undefined,
): CreativeBriefStatus {
  if (!hasCreativeBrief(promptSummary)) return "none";
  const block = (promptSummary as string).split(BRIEF_MARKER).slice(1).join("");
  return /(?:^|\n)creative_brief_status:\s*drafted/i.test(block) ? "drafted" : "none";
}

/** Heuristic mode inference from a content item when there is no brief
 *  yet — used by the preview route's empty state. */
export function inferCreativePreviewMode(input: {
  format?: string | null;
  channel?: string | null;
}): CreativeBriefMode {
  const fmt = (input.format ?? "").toLowerCase();
  switch (fmt) {
    case "carousel": return "carousel";
    case "story": return "story";
    case "feed_post": return "feed_post";
    case "static_image": return "static_image";
    case "text_post": return "linkedin_text";
    case "organic_reel":
    case "short_video":
      return "reel_support";
    case "ugc_video_ad":
    case "long_video":
      return "video_visual_support";
    default:
      return "unknown";
  }
}

// ---------------------------------------------------------------------------
// Markdown section extraction. The brief markdown uses `## <Title>`
// section anchors. Each helper finds a section and returns the slice
// from after its heading line to the next `## ` (or end-of-string).

function getSection(md: string, heading: string): string | null {
  const re = new RegExp(`(?:^|\\n)##\\s+${escapeRe(heading)}\\s*\\n([\\s\\S]*?)(?=\\n##\\s+|$)`, "i");
  const m = md.match(re);
  return m ? m[1].trim() : null;
}

function getSubSection(md: string, heading: string): string | null {
  const re = new RegExp(`(?:^|\\n)###\\s+${escapeRe(heading)}\\s*\\n([\\s\\S]*?)(?=\\n###\\s+|\\n##\\s+|$)`, "i");
  const m = md.match(re);
  return m ? m[1].trim() : null;
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Parse a "-" bullet list from a markdown chunk. Returns the body of
 *  each bullet (after "- "), trimmed. Sub-bullets ("  - ") become own
 *  entries. */
function parseBulletList(chunk: string | null): string[] {
  if (!chunk) return [];
  const lines = chunk.split("\n");
  const out: string[] = [];
  for (const ln of lines) {
    const m = ln.match(/^\s*[-*]\s+(.+?)\s*$/);
    if (m) out.push(m[1]);
  }
  return out;
}

/** Pull "label: value" pairs from a bullet block, returning a map. */
function parseLabeledBullets(chunk: string | null): Map<string, string> {
  const out = new Map<string, string>();
  for (const item of parseBulletList(chunk)) {
    const m = item.match(/^([a-z][a-z0-9_\- ]*?):\s*(.+)$/i);
    if (m) out.set(m[1].trim().toLowerCase(), m[2].trim());
  }
  return out;
}

// ---------------------------------------------------------------------------
// Per-mode parsers

function parseSlides(md: string): ParsedCarouselSlide[] {
  // Slides section: `## Slides` then repeating `### Slide N — <headline>`
  const section = getSection(md, "Slides");
  if (!section) return [];
  const slides: ParsedCarouselSlide[] = [];
  const re = /(?:^|\n)###\s+Slide\s+(\d+)\s*[—\-:]\s*(.+?)\s*\n([\s\S]*?)(?=\n###\s+Slide\s+\d|\n##\s+|$)/gi;
  for (const m of section.matchAll(re)) {
    const num = parseInt(m[1], 10);
    const headline = m[2].trim();
    const body = m[3];
    const fields = parseLabeledBullets(body);
    slides.push({
      slideNumber: Number.isFinite(num) ? num : slides.length + 1,
      headline,
      bodyCopy: fields.get("body") ?? null,
      visualDirection: fields.get("visual") ?? null,
      layoutNote: fields.get("layout") ?? null,
    });
  }
  return slides;
}

function parseFrames(md: string): ParsedStoryFrame[] {
  const section = getSection(md, "Story frames");
  if (!section) return [];
  const frames: ParsedStoryFrame[] = [];
  const re = /(?:^|\n)###\s+Frame\s+(\d+)\s*\n([\s\S]*?)(?=\n###\s+Frame\s+\d|\n##\s+|$)/gi;
  for (const m of section.matchAll(re)) {
    const num = parseInt(m[1], 10);
    const body = m[2];
    const fields = parseLabeledBullets(body);
    frames.push({
      frameNumber: Number.isFinite(num) ? num : frames.length + 1,
      textOverlay: fields.get("overlay") ?? null,
      visualDirection: fields.get("visual") ?? null,
      interactionStickerSuggestion: fields.get("sticker") ?? null,
    });
  }
  return frames;
}

function parseLinkedIn(md: string): {
  postHook: string | null;
  imageConcept: string | null;
  professionalToneNotes: string | null;
} {
  const section = getSection(md, "LinkedIn post");
  if (!section) return { postHook: null, imageConcept: null, professionalToneNotes: null };
  const fields = parseLabeledBullets(section);
  return {
    postHook: fields.get("hook") ?? null,
    imageConcept: fields.get("optional image") ?? null,
    professionalToneNotes: fields.get("tone notes") ?? null,
  };
}

function parseVisualSupport(md: string): {
  thumbnailConcept: string | null;
  onScreenTextBeats: string[];
  brollCues: string[];
  hookFrame: string | null;
  propBrief: string | null;
} {
  const section = getSection(md, "Visual support");
  const fields = parseLabeledBullets(section);
  // The list-of-list items (on-screen text beats, b-roll cues) are
  // nested as "  - " bullets under the label; parseBulletList flattens
  // them. We slice them out by re-extracting from sub-positions.
  const beats: string[] = [];
  const cues: string[] = [];
  if (section) {
    const beatsMatch = section.match(/-\s*on-screen text beats:\s*\n([\s\S]*?)(?=\n-\s+[a-z]|\n##\s+|$)/i);
    if (beatsMatch) beats.push(...parseBulletList(beatsMatch[1]));
    const cuesMatch = section.match(/-\s*b-roll cues:\s*\n([\s\S]*?)(?=\n-\s+[a-z]|\n##\s+|$)/i);
    if (cuesMatch) cues.push(...parseBulletList(cuesMatch[1]));
  }
  return {
    thumbnailConcept: fields.get("thumbnail / cover") ?? null,
    onScreenTextBeats: beats,
    brollCues: cues,
    hookFrame: fields.get("hook frame") ?? null,
    propBrief: fields.get("prop brief") ?? null,
  };
}

function parseMainVisual(md: string): {
  mainVisual: string | null;
  headlineOverlay: string | null;
  captionSupport: string | null;
  compositionNotes: string | null;
} {
  const section = getSection(md, "Main visual");
  if (!section) {
    return {
      mainVisual: null,
      headlineOverlay: null,
      captionSupport: null,
      compositionNotes: null,
    };
  }
  // The top of the section before the first sub-heading is the main
  // visual text.
  const beforeSub = section.split(/\n###\s+/)[0].trim() || null;
  const headlineOverlay = getSubSection(md, "Headline overlay");
  const captionSupport = getSubSection(md, "Caption support");
  const compositionNotes = getSubSection(md, "Composition notes");
  return {
    mainVisual: beforeSub,
    headlineOverlay,
    captionSupport,
    compositionNotes,
  };
}

function parseAssetRequirements(md: string): { layoutType: string | null; requirements: string[] } {
  const section = getSection(md, "Layout & assets");
  if (!section) return { layoutType: null, requirements: [] };
  const fields = parseLabeledBullets(section);
  const layoutType = fields.get("layout") ?? null;
  // Requirements are sub-bullets under "- asset requirements:".
  const reqMatch = section.match(/-\s*asset requirements:\s*\n([\s\S]*?)(?=\n-\s+[a-z]|\n##\s+|$)/i);
  const requirements = reqMatch ? parseBulletList(reqMatch[1]) : [];
  return { layoutType, requirements };
}

// ---------------------------------------------------------------------------
// Entrypoint

export function parseCreativeBriefBlock(
  promptSummary: string | null | undefined,
): ParsedCreativeBrief | null {
  if (!hasCreativeBrief(promptSummary)) return null;
  // The block sits behind a "\n\n[creative brief]\n" marker. There is
  // exactly one (the action strips prior blocks before appending).
  const block = (promptSummary as string).split(BRIEF_MARKER).slice(1).join("");
  if (!block.trim()) return null;

  const sk = (key: string): string | null => {
    const m = block.match(new RegExp(`(?:^|\\n)${key}:\\s*(.+?)(?=\\n|$)`, "i"));
    return m ? m[1].trim() : null;
  };

  const status = sk("creative_brief_status");
  if (status !== "drafted") {
    // Block exists but is malformed. Surface a graceful null.
    return null;
  }

  const source = sk("creative_brief_source");
  const format = sk("creative_brief_format");
  const channel = sk("creative_brief_channel");
  const modeRaw = sk("creative_brief_mode");
  const createdAt = sk("creative_brief_created_at");
  const operatorNote = sk("creative_brief_operator_note");
  const templateId = sk("creative_brief_template_id");
  const mode = asMode(modeRaw);

  // The markdown body starts after the structured keys (a blank line
  // separates them). Splitting on the first `# Creative brief` heading
  // gives us the readable section.
  const headingIdx = block.indexOf("\n# Creative brief");
  const md = headingIdx === -1 ? "" : block.slice(headingIdx + 1);

  const titleMatch = md.match(/^#\s+Creative brief\s*[—\-:]\s*(.+?)\s*\n/);
  const title = titleMatch ? titleMatch[1].trim() : null;

  const creativeDirection = getSection(md, "Creative direction");
  const visualConcept = getSection(md, "Visual concept");
  const { layoutType, requirements } = parseAssetRequirements(md);
  const copyPlacement = getSection(md, "Copy placement");
  const brandElements = parseBulletList(getSection(md, "Brand elements"));
  const doNotInclude = parseBulletList(getSection(md, "Do NOT include"));
  const callToAction = getSection(md, "Call to action");
  const shotOrDesignNotes = getSection(md, "Shot / design notes");

  const parsed: ParsedCreativeBrief = {
    status: "drafted",
    source,
    format,
    channel,
    mode,
    createdAt,
    operatorNote,
    templateId,
    title,
    creativeDirection,
    visualConcept,
    layoutType,
    assetRequirements: requirements,
    copyPlacement,
    brandElements,
    doNotInclude,
    callToAction,
    shotOrDesignNotes,
    rawMarkdown: md.trim(),
  };

  switch (mode) {
    case "carousel":
      parsed.slides = parseSlides(md);
      break;
    case "story":
      parsed.frames = parseFrames(md);
      break;
    case "feed_post":
    case "static_image": {
      const mv = parseMainVisual(md);
      parsed.mainVisual = mv.mainVisual;
      parsed.headlineOverlay = mv.headlineOverlay;
      parsed.captionSupport = mv.captionSupport;
      parsed.compositionNotes = mv.compositionNotes;
      break;
    }
    case "linkedin_text": {
      const li = parseLinkedIn(md);
      parsed.postHook = li.postHook;
      parsed.imageConcept = li.imageConcept;
      parsed.professionalToneNotes = li.professionalToneNotes;
      break;
    }
    case "reel_support": {
      const vs = parseVisualSupport(md);
      parsed.thumbnailConcept = vs.thumbnailConcept;
      parsed.onScreenTextBeats = vs.onScreenTextBeats;
      parsed.brollCues = vs.brollCues;
      break;
    }
    case "video_visual_support": {
      const vs = parseVisualSupport(md);
      parsed.thumbnailConcept = vs.thumbnailConcept;
      parsed.hookFrame = vs.hookFrame;
      parsed.propBrief = vs.propBrief;
      break;
    }
    case "copy_only":
    case "unknown":
      // No per-mode payload.
      break;
  }

  return parsed;
}

// ---------------------------------------------------------------------------
// Phase 4D1 — internal approval for the visual brief. Mirrors the
// `[copy approval]` provenance pattern. The block always sits AFTER
// the `[creative brief]` block at the tail of `prompt_summary` so a
// tail-slice strip is safe (same as `stripApprovalBlock` in
// web/lib/actions/copy-draft.ts).

export type CreativeBriefApprovalStatus = "none" | "approved_internal";

export interface ParsedCreativeBriefApproval {
  status: "approved_internal";
  approvedAt: string | null;
  approvedBy: string | null;
  notes: string | null;
}

const APPROVAL_MARKER = "\n\n[creative brief approval]\n";

/** True iff a `[creative brief approval]` block is present. */
export function hasCreativeBriefApproval(
  promptSummary: string | null | undefined,
): boolean {
  return Boolean(
    promptSummary && promptSummary.includes("[creative brief approval]"),
  );
}

/** Approval status: `"approved_internal"` when the block is present
 *  AND its `creative_brief_approval_status` line says so; `"none"`
 *  otherwise. Lightweight — does NOT parse the markdown body. */
export function getCreativeBriefApprovalStatus(
  promptSummary: string | null | undefined,
): CreativeBriefApprovalStatus {
  if (!hasCreativeBriefApproval(promptSummary)) return "none";
  const block = (promptSummary as string).split(APPROVAL_MARKER).slice(1).join("");
  return /(?:^|\n)creative_brief_approval_status:\s*approved_internal/i.test(
    block,
  )
    ? "approved_internal"
    : "none";
}

/** Full parsed approval block, or null when absent / malformed. */
export function parseCreativeBriefApproval(
  promptSummary: string | null | undefined,
): ParsedCreativeBriefApproval | null {
  if (!hasCreativeBriefApproval(promptSummary)) return null;
  const block = (promptSummary as string).split(APPROVAL_MARKER).slice(1).join("");
  const sk = (key: string): string | null => {
    const m = block.match(new RegExp(`(?:^|\\n)${key}:\\s*(.+?)(?=\\n|$)`, "i"));
    return m ? m[1].trim() : null;
  };
  if (sk("creative_brief_approval_status") !== "approved_internal") return null;
  return {
    status: "approved_internal",
    approvedAt: sk("creative_brief_approved_at"),
    approvedBy: sk("creative_brief_approved_by"),
    notes: sk("creative_brief_approval_notes"),
  };
}
