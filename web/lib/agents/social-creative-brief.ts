// Yuvo Studio — Phase 4A deterministic Social Creative Brief Agent.
//
// DETERMINISTIC. No LLM, no OpenAI / Anthropic / image-API call, no
// fetch, no scrape, no paid API. Given a content item's
// format/channel + brand context (parsed from prompt_summary) + an
// optional operator note, this returns a structured **planning brief**
// for the visual side of a social asset.
//
// HARD RULE: this module never generates a final image, never writes
// to disk, never calls an external API. It only composes strings.

import type { ContentChannel, ContentFormat } from "@/lib/content/taxonomy";

// ---------------------------------------------------------------------------
// Input + output types

export interface CreativeBriefInput {
  format: ContentFormat;
  channel: ContentChannel;
  /** Content item title — used as the working subject. */
  title: string;
  /** Free-text brief from caption_draft / content item brief. May be
   *  empty; the agent falls back to the title. */
  brief: string;
  /** Optional brand context parsed from prompt_summary or agent run
   *  (matched_niche / product_url / brand tone). */
  brandContext?: {
    productUrl?: string;
    matchedNiche?: string;
    brandTone?: string;
    audienceAssumption?: string;
    primaryColorHex?: string;
  };
  /** Optional caption draft (already produced by the Copy Draft Agent
   *  if available) so the visual brief reinforces the copy direction. */
  captionDraft?: string;
  /** Optional content goal (awareness, trust_building, ...). Free text;
   *  the agent passes it through into the brief. */
  contentGoal?: string;
  operatorNotes?: string;
}

/** High-level template the brief follows. Mirrors the user-visible
 *  format taxonomy but adds a few visual-only variants. */
export type CreativeBriefMode =
  | "carousel"
  | "story"
  | "feed_post"
  | "static_image"
  | "linkedin_text"
  | "reel_support"
  | "video_visual_support"
  | "copy_only";

export interface CarouselSlide {
  slideNumber: number;
  headline: string;
  bodyCopy: string;
  visualDirection: string;
  layoutNote: string;
}

export interface StoryFrame {
  frameNumber: number;
  textOverlay: string;
  visualDirection: string;
  interactionStickerSuggestion: string;
}

export interface CreativeBriefVariant {
  label: string;
  twist: string;
}

/** Structured creative brief. NOT a final asset — every field is a
 *  planning instruction a designer / image-gen step would later use. */
export interface CreativeBrief {
  contentItemId: string;
  channel: ContentChannel;
  format: ContentFormat;
  mode: CreativeBriefMode;
  distributionType: string | null;
  contentGoal: string | null;
  title: string;

  // Universal planning fields
  creativeDirection: string;
  visualConcept: string;
  layoutType: string;
  assetRequirements: string[];
  copyPlacement: string;
  shotOrDesignNotes: string;
  brandElements: string[];
  doNotInclude: string[];
  callToAction: string;
  variants: CreativeBriefVariant[];

  // Format-specific shapes — exactly ONE is populated per brief, the
  // others are undefined. Consumers narrow by `mode`.
  slides?: CarouselSlide[];          // carousel
  frames?: StoryFrame[];             // story
  mainVisual?: string;               // feed_post / static_image
  headlineOverlay?: string;          // feed_post / static_image
  captionSupport?: string;           // feed_post / static_image
  compositionNotes?: string;         // feed_post / static_image
  imageConcept?: string;             // linkedin (optional image)
  postHook?: string;                 // linkedin
  professionalToneNotes?: string;    // linkedin
  thumbnailConcept?: string;         // reel_support / video_visual_support
  onScreenTextBeats?: string[];      // reel_support
  brollCues?: string[];              // reel_support
  hookFrame?: string;                // video_visual_support
  propBrief?: string;                // video_visual_support
}

export interface CreativeBriefResult {
  brief: CreativeBrief;
  /** Human-readable markdown rendering of the structured brief, suitable
   *  for embedding in `prompt_summary` after the structured key block. */
  markdown: string;
  caveats: string[];
}

// ---------------------------------------------------------------------------
// Niche → palette / prop language (deterministic lookup; no fetch).

const NICHE_PALETTE: Record<string, string> = {
  skincare: "warm cream + soft botanical greens, gentle daylight",
  supplements: "calm pastel + clean white space, morning kitchen light",
  fitness_apparel: "high-contrast moody studio + sweat-catching rim light",
  coffee_beverages: "rich espresso brown + steam, golden-hour kitchen",
  saas_b2b: "muted neutrals + product UI snippet over a flat desk scene",
  fashion_apparel: "editorial neutral, soft window light, single-prop scenes",
  home_kitchen: "warm wood + linen, soft natural light",
};

const NICHE_PROPS: Record<string, string[]> = {
  skincare: ["bare hands", "a single ingredient (e.g. rosehip stem)", "ceramic dish with product"],
  supplements: ["clean drinking glass", "morning notebook + pen", "single capsule on linen"],
  fitness_apparel: ["chalk dust", "weight plate edge", "athlete mid-rep silhouette"],
  coffee_beverages: ["latte heart pour", "steaming mug top-down", "coffee beans scatter"],
  saas_b2b: ["laptop screen showing the dashboard", "sticky note on monitor", "coffee + clean desk"],
  fashion_apparel: ["fabric drape close-up", "single garment on hanger", "model-free flat lay"],
  home_kitchen: ["wooden board with knife mark", "linen napkin folded", "single fresh ingredient"],
};

function paletteFor(niche?: string): string {
  if (!niche) return "brand-on palette; soft daylight; avoid harsh studio flash";
  return NICHE_PALETTE[niche] ?? "brand-on palette; soft daylight; avoid harsh studio flash";
}

function propsFor(niche?: string): string[] {
  if (!niche) return ["one hero prop", "one supporting prop", "negative space for copy"];
  return NICHE_PROPS[niche] ?? ["one hero prop", "one supporting prop", "negative space for copy"];
}

// ---------------------------------------------------------------------------
// Mode resolution

function modeForFormat(format: ContentFormat): CreativeBriefMode {
  switch (format) {
    case "carousel":
      return "carousel";
    case "story":
      return "story";
    case "feed_post":
      return "feed_post";
    case "static_image":
      return "static_image";
    case "text_post":
      return "linkedin_text";
    case "organic_reel":
    case "short_video":
      return "reel_support";
    case "ugc_video_ad":
    case "long_video":
      return "video_visual_support";
    default:
      return "copy_only";
  }
}

// ---------------------------------------------------------------------------
// Universal do-not-include list (safety / brand) — used everywhere.

const UNIVERSAL_DO_NOT: string[] = [
  "no celebrity / real-person likeness without an explicit release",
  "no medical or curative claims",
  "no competitor product or logo in frame",
  "no AI-generated visible hands or text artefacts (if image gen used downstream)",
  "no client logo lock-up on top of human faces",
];

// ---------------------------------------------------------------------------
// Builders per mode

function buildCarousel(
  input: CreativeBriefInput,
  base: CreativeBrief,
): CreativeBrief {
  const niche = input.brandContext?.matchedNiche;
  const props = propsFor(niche);
  const slides: CarouselSlide[] = [
    {
      slideNumber: 1,
      headline: titleAsHook(input.title),
      bodyCopy: "Open with the single sharpest claim from the brief. ≤8 words.",
      visualDirection: `Cover slide. ${paletteFor(niche)}. Negative space top-right for the headline. Hero: ${props[0]}.`,
      layoutNote: "Headline anchored top-left or top-right; logo bottom-right at 60% opacity.",
    },
    {
      slideNumber: 2,
      headline: "The problem (named precisely)",
      bodyCopy: namedProblemLine(input),
      visualDirection: `Mid-shot of ${props[1] ?? props[0]} with subtle texture; same palette as cover.`,
      layoutNote: "Single line of body copy centred; small caption underneath.",
    },
    {
      slideNumber: 3,
      headline: "How we solve it",
      bodyCopy: solutionLine(input),
      visualDirection: `Product / hero element clearly visible; show one ingredient / detail in close-up.`,
      layoutNote: "Two-column: image left, three bullets right.",
    },
    {
      slideNumber: 4,
      headline: "Proof or routine",
      bodyCopy: "One concrete proof point (testimonial line, ingredient stat, before/after frame).",
      visualDirection: "Real-feeling, hand-held framing. No stock-photo look.",
      layoutNote: "Quote pulled out + small attribution; or step 1/2/3 routine list.",
    },
    {
      slideNumber: 5,
      headline: "Call to action",
      bodyCopy: ctaForChannel(input),
      visualDirection: "Clean product shot on the brand palette; copy-safe negative space top.",
      layoutNote: "Big CTA headline; minor URL / handle underneath.",
    },
  ];
  return {
    ...base,
    mode: "carousel",
    slides,
    creativeDirection:
      "Five-slide narrative arc: hook → named problem → solution → proof → CTA. "
      + "Each slide must stand on its own in a feed; the deck should not require sound.",
    visualConcept: `Editorial / lifestyle, ${paletteFor(niche)}. Same crop ratio across slides (4:5 recommended).`,
    layoutType: "5 × portrait 4:5 with consistent type lock-up",
    assetRequirements: [
      "5 final images at 1080×1350 (4:5) — exported as JPG ≤ 500 KB each",
      "Optional 6th \"saved for later\" tile",
      "All copy laid out in-image; no third-party fonts that require licensing",
    ],
    copyPlacement:
      "Headline lives inside the image; long-form copy lives in the post caption (not on the slide).",
    shotOrDesignNotes:
      "If reshooting: shoot all five slides in the same session/light. If designing: lock fonts + grid before slide 2.",
  };
}

function buildStory(
  input: CreativeBriefInput,
  base: CreativeBrief,
): CreativeBrief {
  const niche = input.brandContext?.matchedNiche;
  const props = propsFor(niche);
  const frames: StoryFrame[] = [
    {
      frameNumber: 1,
      textOverlay: titleAsHook(input.title),
      visualDirection: `Vertical 9:16. ${paletteFor(niche)}. Hero: ${props[0]}.`,
      interactionStickerSuggestion: "Poll: \"do you struggle with this?\" (Yes / Sometimes)",
    },
    {
      frameNumber: 2,
      textOverlay: solutionLine(input),
      visualDirection: "Product / hero element framed close. Top third reserved for overlay text.",
      interactionStickerSuggestion: "Quiz: pick one of two options (positions the product naturally)",
    },
    {
      frameNumber: 3,
      textOverlay: ctaForChannel(input),
      visualDirection: "Brand-safe clean shot; CTA banner mid-frame.",
      interactionStickerSuggestion: "Link sticker (creator only) or \"DM us\" sticker",
    },
  ];
  return {
    ...base,
    mode: "story",
    frames,
    creativeDirection:
      "Three-frame story: hook → product moment → CTA. Each frame must hold ≤7s; "
      + "text overlay readable on mute.",
    visualConcept: `Vertical 9:16, ${paletteFor(niche)}. Top 250px reserved for the IG/TT chrome.`,
    layoutType: "3 × 1080×1920",
    assetRequirements: [
      "3 final stills or 3 short clips at 1080×1920",
      "Each frame independently usable",
      "Caption overlays burned in OR provided as a separate text layer",
    ],
    copyPlacement: "All copy lives ON the frame; assume the viewer is muted.",
    shotOrDesignNotes:
      "Avoid 'logo over face' framing. Keep one strong colour family across all 3 frames.",
  };
}

function buildFeedOrStatic(
  input: CreativeBriefInput,
  base: CreativeBrief,
  isStatic: boolean,
): CreativeBrief {
  const niche = input.brandContext?.matchedNiche;
  const props = propsFor(niche);
  return {
    ...base,
    mode: isStatic ? "static_image" : "feed_post",
    mainVisual: `Hero: ${props[0]}. Supporting: ${props[1] ?? "negative space"}. ${paletteFor(niche)}.`,
    headlineOverlay: titleAsHook(input.title),
    captionSupport:
      input.captionDraft && input.captionDraft.trim().length > 0
        ? "Use the Copy Draft Agent caption as the post body; do NOT duplicate it on the image."
        : "Run the Copy Draft Agent first; that text goes in the post caption (not on the image).",
    compositionNotes:
      "Subject on a thirds intersection; reserve the top-left for the in-image headline; "
      + "ensure 80% of the canvas is brand-on palette.",
    creativeDirection:
      isStatic
        ? "Single-frame, copy-light. The visual carries 80%; the headline overlay carries 20%."
        : "Single feed post; image-first. Treat the caption as the long-form vehicle.",
    visualConcept: `${paletteFor(niche)}; lifestyle / editorial; avoid obvious stock photography.`,
    layoutType: "1080×1350 (4:5) preferred; fallback 1080×1080 square",
    assetRequirements: [
      "1 final image at 1080×1350 (4:5)",
      "Optional 1080×1920 vertical crop for Story repost",
      "JPG ≤ 500 KB; do NOT export with embedded metadata other than alt text",
    ],
    copyPlacement: "Headline in-image; long-form copy lives in the post caption.",
    shotOrDesignNotes:
      "If image gen is later approved: regenerate up to 3 variants but never publish without operator review.",
  };
}

function buildLinkedIn(
  input: CreativeBriefInput,
  base: CreativeBrief,
): CreativeBrief {
  const niche = input.brandContext?.matchedNiche;
  return {
    ...base,
    mode: "linkedin_text",
    postHook: titleAsHook(input.title),
    imageConcept:
      `Optional companion image: simple flat design with one statistic or a single product still.`
      + ` Palette: ${paletteFor(niche)}.`,
    professionalToneNotes:
      "Professional, first-person, specific. Avoid corporate cliches "
      + "(\"in today's fast-paced world\", \"thrilled to announce\"). One link max.",
    creativeDirection:
      "LinkedIn long-form text post (~150-220 words). Optional 1:1 companion image only if it adds info.",
    visualConcept: "Text-led; if an image is used, keep it minimal and on-palette.",
    layoutType: "Text post; optional 1080×1080 companion image",
    assetRequirements: [
      "Final text body from the Copy Draft Agent (LinkedIn mode)",
      "Optional 1 × 1080×1080 companion JPG",
    ],
    copyPlacement: "All copy lives in the post body; the optional image carries at most one stat.",
    shotOrDesignNotes:
      "If a companion image is shipped, keep typography native-LinkedIn (no third-party heavy display fonts).",
  };
}

function buildReelSupport(
  input: CreativeBriefInput,
  base: CreativeBrief,
): CreativeBrief {
  const niche = input.brandContext?.matchedNiche;
  const props = propsFor(niche);
  return {
    ...base,
    mode: "reel_support",
    thumbnailConcept:
      `Custom cover frame: ${props[0]} in frame, ${paletteFor(niche)},`
      + ` text overlay = the hook ("${titleAsHook(input.title)}").`,
    onScreenTextBeats: [
      "0.0s — Hook overlay matches the cover headline (consistency on scroll)",
      "2.0s — Named problem (1 line, ≤6 words)",
      "5.0s — Solution / product reveal (1 line)",
      "10.0s — Proof / one specific detail",
      "End — CTA (\"Save for later\" or \"Tap to shop\")",
    ],
    brollCues: [
      `Close-up of ${props[0]} (1-2s)`,
      `Hands interacting with ${props[1] ?? "the product"} (1-2s)`,
      "One environmental wide (room / counter / studio) for breathing room",
    ],
    creativeDirection:
      "Visual support brief for an organic Reel. The AGENT does NOT produce video. "
      + "It only specs the cover frame, on-screen text beats, and b-roll cues.",
    visualConcept: `${paletteFor(niche)}; hand-held feel; minimal post-production.`,
    layoutType: "9:16 short video; custom cover 1080×1920",
    assetRequirements: [
      "1 cover-frame still 1080×1920",
      "On-screen text beats list (text only)",
      "B-roll shotlist (3-5 cues)",
    ],
    copyPlacement: "On-screen text burned in OR a separate caption layer.",
    shotOrDesignNotes:
      "Audio: pick a trending sound BEFORE shooting so visuals can be cut to the beat.",
  };
}

function buildVideoVisualSupport(
  input: CreativeBriefInput,
  base: CreativeBrief,
): CreativeBrief {
  const niche = input.brandContext?.matchedNiche;
  const props = propsFor(niche);
  return {
    ...base,
    mode: "video_visual_support",
    thumbnailConcept:
      `Cover frame for the paid UGC video. ${paletteFor(niche)}. Hook overlay ≤6 words.`,
    hookFrame:
      `First 1.5s: ${props[0]} clearly in frame; hook line burned in across the top third.`,
    propBrief:
      `Required props: ${props.join(", ")}. Avoid: anything not in the brand palette; competitor packaging; medical claims.`,
    creativeDirection:
      "Visual support brief for an existing UGC video pipeline. The AGENT does NOT call "
      + "Seedance / Enhancor / Audio Fixer. It only specs the cover frame, hook frame, and prop brief.",
    visualConcept: `${paletteFor(niche)}; lifestyle realism; not over-lit.`,
    layoutType: "9:16 short video; custom cover 1080×1920",
    assetRequirements: [
      "1 cover-frame still 1080×1920",
      "Hook-frame description (text only)",
      "Prop checklist (text only)",
    ],
    copyPlacement: "Burned-in hook on the cover + first frame; rest of copy in caption.",
    shotOrDesignNotes:
      "If image gen is later approved for the cover: never publish without operator review.",
  };
}

// ---------------------------------------------------------------------------
// Small text helpers (deterministic; pure)

function titleAsHook(title: string): string {
  const t = title.trim();
  if (!t) return "(brand-on hook)";
  // If the title already looks like a sentence, keep it; otherwise add a soft turn.
  return t.length > 80 ? t.slice(0, 78) + "…" : t;
}

function namedProblemLine(input: CreativeBriefInput): string {
  const niche = input.brandContext?.matchedNiche ?? "this category";
  return `Name the exact pain (one line) that ${niche} customers feel — no generic "tired of ..." copy.`;
}

function solutionLine(input: CreativeBriefInput): string {
  return (
    input.brief?.trim()
      ? `Tie the solution back to the brief in plain language: "${input.brief.trim().slice(0, 90)}"`
      : "Lead with the single, concrete way the product changes the named pain — no list of features."
  );
}

function ctaForChannel(input: CreativeBriefInput): string {
  switch (input.channel) {
    case "linkedin":
      return "Soft CTA: \"DM me if useful\" or \"Comment for the link\" — never a hard buy on first touch.";
    case "instagram":
    case "tiktok":
      return "Save / share CTA: \"Save this for next time you …\" — link in bio for the product page.";
    case "email":
      return "Single button CTA pointing to the product URL.";
    default:
      return "One concrete next step. Avoid stacking multiple CTAs.";
  }
}

function variantsForMode(mode: CreativeBriefMode): CreativeBriefVariant[] {
  switch (mode) {
    case "carousel":
      return [
        { label: "A · narrative", twist: "Slides as a 5-beat story (current default)" },
        { label: "B · listicle", twist: "Convert slides 2-4 into a numbered list, keep slide 5 CTA" },
        { label: "C · POV", twist: "Reframe slide 1 as a first-person line; rest unchanged" },
      ];
    case "story":
      return [
        { label: "A · poll-led", twist: "Lead frame 1 with the poll sticker (current default)" },
        { label: "B · UGC clip", twist: "Replace frame 2 with a 4s real-customer clip" },
      ];
    case "feed_post":
    case "static_image":
      return [
        { label: "A · headline-led", twist: "Big in-image headline (current default)" },
        { label: "B · product-led", twist: "No headline; product hero only" },
      ];
    case "linkedin_text":
      return [
        { label: "A · short", twist: "150 words, no image" },
        { label: "B · medium", twist: "220 words + 1 companion image" },
      ];
    case "reel_support":
      return [
        { label: "A · hook-first", twist: "Cover = hook overlay (current default)" },
        { label: "B · scene-first", twist: "Cover = product hero, no overlay; rely on caption" },
      ];
    case "video_visual_support":
      return [
        { label: "A · clean", twist: "No overlay on cover; let the product carry it" },
        { label: "B · hook", twist: "Hook overlay on cover (current default)" },
      ];
    case "copy_only":
      return [];
  }
}

// ---------------------------------------------------------------------------
// Entrypoint

export function planSocialCreativeBrief(
  contentItemId: string,
  input: CreativeBriefInput,
): CreativeBriefResult {
  const niche = input.brandContext?.matchedNiche;
  const mode = modeForFormat(input.format);

  const base: CreativeBrief = {
    contentItemId,
    channel: input.channel,
    format: input.format,
    mode,
    distributionType: null,
    contentGoal: input.contentGoal ?? null,
    title: input.title,
    creativeDirection: "",
    visualConcept: "",
    layoutType: "",
    assetRequirements: [],
    copyPlacement: "",
    shotOrDesignNotes: "",
    brandElements: [
      input.brandContext?.brandTone
        ? `Tone: ${input.brandContext.brandTone}`
        : "Tone: brand-on (see brand guide)",
      input.brandContext?.audienceAssumption
        ? `Audience: ${input.brandContext.audienceAssumption}`
        : "Audience: per brand guide",
      input.brandContext?.primaryColorHex
        ? `Primary colour: ${input.brandContext.primaryColorHex}`
        : "Primary colour: per brand guide",
      ...(niche ? [`Niche palette: ${paletteFor(niche)}`] : []),
    ].filter(Boolean),
    doNotInclude: [...UNIVERSAL_DO_NOT],
    callToAction: ctaForChannel(input),
    variants: variantsForMode(mode),
  };

  let brief: CreativeBrief;
  switch (mode) {
    case "carousel":
      brief = buildCarousel(input, base);
      break;
    case "story":
      brief = buildStory(input, base);
      break;
    case "feed_post":
      brief = buildFeedOrStatic(input, base, false);
      break;
    case "static_image":
      brief = buildFeedOrStatic(input, base, true);
      break;
    case "linkedin_text":
      brief = buildLinkedIn(input, base);
      break;
    case "reel_support":
      brief = buildReelSupport(input, base);
      break;
    case "video_visual_support":
      brief = buildVideoVisualSupport(input, base);
      break;
    case "copy_only":
      brief = {
        ...base,
        creativeDirection:
          "Copy-only format. No visual brief required — the Copy Draft Agent output is sufficient.",
        visualConcept: "n/a",
        layoutType: "n/a",
        assetRequirements: [],
        copyPlacement: "All copy lives in the post body.",
        shotOrDesignNotes: "n/a",
      };
      break;
  }

  if (input.operatorNotes && input.operatorNotes.trim().length > 0) {
    brief.shotOrDesignNotes =
      `[operator note] ${input.operatorNotes.trim()}\n` + brief.shotOrDesignNotes;
  }

  const caveats: string[] = [
    "This is a planning brief only. No final image, video, or asset has been generated.",
    "No paid API was called. No external service was contacted.",
    "Do NOT share this brief with the client portal — it's internal direction.",
  ];

  return {
    brief,
    markdown: renderCreativeBriefMarkdown(brief),
    caveats,
  };
}

// ---------------------------------------------------------------------------
// Renderer — deterministic markdown for human review + safe storage.

export function renderCreativeBriefMarkdown(b: CreativeBrief): string {
  const lines: string[] = [];
  lines.push(`# Creative brief — ${b.title}`);
  lines.push("");
  lines.push(`- format: ${b.format}`);
  lines.push(`- channel: ${b.channel}`);
  lines.push(`- mode: ${b.mode}`);
  if (b.contentGoal) lines.push(`- goal: ${b.contentGoal}`);
  lines.push("");

  lines.push("## Creative direction");
  lines.push(b.creativeDirection);
  lines.push("");

  lines.push("## Visual concept");
  lines.push(b.visualConcept);
  lines.push("");

  lines.push("## Layout & assets");
  lines.push(`- layout: ${b.layoutType}`);
  lines.push("- asset requirements:");
  for (const r of b.assetRequirements) lines.push(`  - ${r}`);
  lines.push("");

  lines.push("## Copy placement");
  lines.push(b.copyPlacement);
  lines.push("");

  if (b.slides && b.slides.length > 0) {
    lines.push("## Slides");
    for (const s of b.slides) {
      lines.push(`### Slide ${s.slideNumber} — ${s.headline}`);
      lines.push(`- body: ${s.bodyCopy}`);
      lines.push(`- visual: ${s.visualDirection}`);
      lines.push(`- layout: ${s.layoutNote}`);
      lines.push("");
    }
  }

  if (b.frames && b.frames.length > 0) {
    lines.push("## Story frames");
    for (const f of b.frames) {
      lines.push(`### Frame ${f.frameNumber}`);
      lines.push(`- overlay: ${f.textOverlay}`);
      lines.push(`- visual: ${f.visualDirection}`);
      lines.push(`- sticker: ${f.interactionStickerSuggestion}`);
      lines.push("");
    }
  }

  if (b.mainVisual) {
    lines.push("## Main visual");
    lines.push(b.mainVisual);
    if (b.headlineOverlay) {
      lines.push("");
      lines.push(`### Headline overlay`);
      lines.push(b.headlineOverlay);
    }
    if (b.captionSupport) {
      lines.push("");
      lines.push(`### Caption support`);
      lines.push(b.captionSupport);
    }
    if (b.compositionNotes) {
      lines.push("");
      lines.push(`### Composition notes`);
      lines.push(b.compositionNotes);
    }
    lines.push("");
  }

  if (b.postHook) {
    lines.push("## LinkedIn post");
    lines.push(`- hook: ${b.postHook}`);
    if (b.imageConcept) lines.push(`- optional image: ${b.imageConcept}`);
    if (b.professionalToneNotes)
      lines.push(`- tone notes: ${b.professionalToneNotes}`);
    lines.push("");
  }

  if (b.thumbnailConcept) {
    lines.push("## Visual support");
    lines.push(`- thumbnail / cover: ${b.thumbnailConcept}`);
    if (b.onScreenTextBeats && b.onScreenTextBeats.length > 0) {
      lines.push("- on-screen text beats:");
      for (const t of b.onScreenTextBeats) lines.push(`  - ${t}`);
    }
    if (b.brollCues && b.brollCues.length > 0) {
      lines.push("- b-roll cues:");
      for (const c of b.brollCues) lines.push(`  - ${c}`);
    }
    if (b.hookFrame) lines.push(`- hook frame: ${b.hookFrame}`);
    if (b.propBrief) lines.push(`- prop brief: ${b.propBrief}`);
    lines.push("");
  }

  lines.push("## Brand elements");
  for (const e of b.brandElements) lines.push(`- ${e}`);
  lines.push("");

  lines.push("## Call to action");
  lines.push(b.callToAction);
  lines.push("");

  lines.push("## Do NOT include");
  for (const d of b.doNotInclude) lines.push(`- ${d}`);
  lines.push("");

  if (b.variants.length > 0) {
    lines.push("## Variants to consider");
    for (const v of b.variants) lines.push(`- **${v.label}** — ${v.twist}`);
    lines.push("");
  }

  if (b.shotOrDesignNotes) {
    lines.push("## Shot / design notes");
    lines.push(b.shotOrDesignNotes);
    lines.push("");
  }

  lines.push(
    "_Planning brief only. No final asset has been generated. Internal — do not share with the client._",
  );

  return lines.join("\n");
}
