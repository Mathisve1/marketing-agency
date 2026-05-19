// Yuvo Studio — Phase 2E deterministic Copy Draft Agent.
//
// DETERMINISTIC. No LLM, no OpenAI/Anthropic call, no fetch, no scrape,
// no paid API. Given a content item's format/channel + brand context
// (parsed from prompt_summary), it returns operator-review copy. The
// caller writes it to content_items.caption_draft — it never publishes,
// never shares with a client, never generates a video.

import type { ContentChannel, ContentFormat } from "@/lib/content/taxonomy";

export interface CopyDraftInput {
  format: ContentFormat;
  channel: ContentChannel;
  /** Content item title — used as the working subject. */
  title: string;
  /** Free-text brief (content_items.caption_draft / brief). */
  brief: string;
  /** Optional brand context parsed from prompt_summary / agent run. */
  brandContext?: {
    productUrl?: string;
    matchedNiche?: string;
  };
  tone?: string;
  cta?: string;
  operatorNotes?: string;
}

export type CopyDraftMode =
  | "linkedin_text"
  | "social_feed"
  | "story"
  | "carousel"
  | "email"
  | "blog"
  | "video_caption";

export interface CopyDraftResult {
  mode: CopyDraftMode;
  /** Structured fields (per mode). The renderer flattens these into the
   *  plain-text block stored in caption_draft. */
  fields: Record<string, string | string[]>;
  /** The exact string the server action writes to caption_draft. */
  plainText: string;
  caveats: string[];
}

const NICHE_HASHTAGS: Record<string, string[]> = {
  skincare: ["#skincare", "#sensitiveskin", "#skintok", "#cleanbeauty"],
  supplements: ["#wellness", "#dailyroutine", "#supplements"],
  fitness_apparel: ["#fitness", "#activewear", "#trainhard"],
  coffee_beverages: ["#coffee", "#morningritual", "#specialtycoffee"],
  saas_b2b: ["#saas", "#productivity", "#founders"],
  fashion_apparel: ["#ootd", "#style", "#wardrobe"],
  home_kitchen: ["#homecooking", "#kitchen", "#foodie"],
};

function modeForFormat(format: ContentFormat): CopyDraftMode {
  switch (format) {
    case "text_post":
      return "linkedin_text";
    case "feed_post":
    case "static_image":
      return "social_feed";
    case "story":
      return "story";
    case "carousel":
      return "carousel";
    case "email_snippet":
      return "email";
    case "blog_snippet":
      return "blog";
    case "ugc_video_ad":
    case "organic_reel":
    case "short_video":
    case "long_video":
      // Video formats only get SUPPORTING caption copy here — never a
      // script (that stays in the prompt-version workflow).
      return "video_caption";
    default:
      return "social_feed";
  }
}

function hashtagsFor(input: CopyDraftInput): string[] {
  const niche = input.brandContext?.matchedNiche ?? "";
  return NICHE_HASHTAGS[niche] ?? ["#content", "#brand"];
}

function defaultCta(input: CopyDraftInput): string {
  return (
    input.cta?.trim() ||
    (input.channel === "linkedin"
      ? "What's worked for you? Reply below."
      : input.channel === "email"
        ? "Reply to this email and we'll help you pick."
        : "Tap the link in bio to see more.")
  );
}

function toneLine(input: CopyDraftInput): string {
  return input.tone?.trim()
    ? `[tone: ${input.tone.trim()}]`
    : "[tone: calm, real-friend register — adjust before review]";
}

// --------------------------------------------------------------------------- #
// Per-mode deterministic builders

function buildLinkedIn(input: CopyDraftInput): CopyDraftResult {
  const hook = `Most ${input.brandContext?.matchedNiche ?? "teams"} get this part wrong.`;
  const body = [
    hook,
    "",
    input.brief,
    "",
    "Here's the simple version:",
    "1. Start from the problem, not the product.",
    "2. Show the real routine, not a demo.",
    "3. Let the proof do the talking.",
    "",
    defaultCta(input),
  ].join("\n");
  const altBody = `${hook}\n\n${input.brief}\n\n${defaultCta(input)}`;
  const fields = {
    headline: hook,
    body,
    cta: defaultCta(input),
    hashtags: ["#strategy", "#content", "#marketing"],
    alternative: altBody,
  };
  return {
    mode: "linkedin_text",
    fields,
    plainText: [
      toneLine(input),
      `HEADLINE: ${hook}`,
      "",
      "POST BODY:",
      body,
      "",
      `HASHTAGS: ${fields.hashtags.join(" ")}`,
      "",
      "SHORT ALT VERSION:",
      altBody,
    ].join("\n"),
    caveats: copyCaveats(),
  };
}

function buildSocialFeed(input: CopyDraftInput): CopyDraftResult {
  const firstLine = `${input.title} — here's the honest take.`;
  const caption = [
    firstLine,
    "",
    input.brief,
    "",
    defaultCta(input),
  ].join("\n");
  const tags = hashtagsFor(input);
  return {
    mode: "social_feed",
    fields: {
      firstLineHook: firstLine,
      caption,
      cta: defaultCta(input),
      hashtags: tags,
      visualBrief:
        "Single real-environment shot. No studio lighting. Product incidental, not a packshot.",
    },
    plainText: [
      toneLine(input),
      `FIRST-LINE HOOK: ${firstLine}`,
      "",
      "CAPTION:",
      caption,
      "",
      `HASHTAGS: ${tags.join(" ")}`,
      "",
      "VISUAL BRIEF: Single real-environment shot. No studio lighting. Product incidental, not a packshot.",
    ].join("\n"),
    caveats: copyCaveats(),
  };
}

function buildStory(input: CopyDraftInput): CopyDraftResult {
  const frames = [
    { t: "Frame 1 — Hook", text: `${input.title}?`, dir: "Creator to camera, casual." },
    { t: "Frame 2 — Context", text: input.brief.slice(0, 90), dir: "Hands / product in real use." },
    { t: "Frame 3 — Proof", text: "What changed for me:", dir: "Close, natural light." },
    { t: "Frame 4 — CTA", text: defaultCta(input), dir: "CTA sticker — link / poll." },
  ];
  return {
    mode: "story",
    fields: {
      frames: frames.map((f) => `${f.t}: "${f.text}" | ${f.dir}`),
      ctaSticker: `Link sticker → ${defaultCta(input)}`,
    },
    plainText: [
      toneLine(input),
      "STORY FRAMES:",
      ...frames.map(
        (f, i) => `${i + 1}. ${f.t}\n   text: ${f.text}\n   visual: ${f.dir}`,
      ),
      "",
      `CTA STICKER: Link sticker → ${defaultCta(input)}`,
    ].join("\n"),
    caveats: copyCaveats(),
  };
}

function buildCarousel(input: CopyDraftInput): CopyDraftResult {
  const slides = [
    { h: "The problem", b: input.brief.slice(0, 110) },
    { h: "Why it happens", b: "Most fixes treat the symptom, not the cause." },
    { h: "What actually helps", b: "One simple change, done consistently." },
    { h: "Proof", b: "Here's what that looks like in a real routine." },
    { h: "Recap", b: "Keep it simple. Repeat it. That's the whole thing." },
    { h: "CTA", b: defaultCta(input) },
  ];
  return {
    mode: "carousel",
    fields: {
      slideHeadlines: slides.map((s) => s.h),
      slides: slides.map((s, i) => `Slide ${i + 1}: ${s.h} — ${s.b}`),
      finalCta: defaultCta(input),
    },
    plainText: [
      toneLine(input),
      "CAROUSEL OUTLINE:",
      ...slides.map(
        (s, i) => `Slide ${i + 1} [${s.h}]\n   ${s.b}`,
      ),
      "",
      `FINAL CTA SLIDE: ${defaultCta(input)}`,
    ].join("\n"),
    caveats: copyCaveats(),
  };
}

function buildEmail(input: CopyDraftInput): CopyDraftResult {
  const subject = `${input.title} (a 30-second read)`;
  const preview = input.brief.slice(0, 70);
  const body = [
    "Hi —",
    "",
    input.brief,
    "",
    defaultCta(input),
    "",
    "— The team",
  ].join("\n");
  return {
    mode: "email",
    fields: {
      subject,
      previewText: preview,
      body,
      cta: defaultCta(input),
    },
    plainText: [
      toneLine(input),
      `SUBJECT: ${subject}`,
      `PREVIEW: ${preview}`,
      "",
      "BODY:",
      body,
      "",
      "NOTE: draft only — never auto-sent. Operator review required.",
    ].join("\n"),
    caveats: [
      ...copyCaveats(),
      "Email is a DRAFT. It is never sent automatically.",
    ],
  };
}

function buildBlog(input: CopyDraftInput): CopyDraftResult {
  const title = `${input.title}: the short, honest version`;
  const intro = `${input.brief} This isn't a deep-dive — it's the part that actually matters.`;
  const outline = [
    "What most people get wrong",
    "The one idea that changes it",
    "How it looks in practice",
    "What to do next",
  ];
  return {
    mode: "blog",
    fields: {
      title,
      intro,
      outline,
      cta: defaultCta(input),
    },
    plainText: [
      toneLine(input),
      `TITLE: ${title}`,
      "",
      "INTRO:",
      intro,
      "",
      "OUTLINE:",
      ...outline.map((o, i) => `${i + 1}. ${o}`),
      "",
      `CTA: ${defaultCta(input)}`,
    ].join("\n"),
    caveats: copyCaveats(),
  };
}

function buildVideoCaption(input: CopyDraftInput): CopyDraftResult {
  // Supporting caption ONLY — no script. The video script stays in the
  // prompt-version workflow.
  const caption = [
    `${input.title}.`,
    "",
    input.brief,
    "",
    defaultCta(input),
  ].join("\n");
  const tags = hashtagsFor(input);
  return {
    mode: "video_caption",
    fields: {
      caption,
      hashtags: tags,
      note: "Supporting caption for a video item. The video script/prompt stays in the prompt-version workflow — this agent does NOT write scripts.",
    },
    plainText: [
      toneLine(input),
      "SUPPORTING CAPTION (video item — script stays in prompt workflow):",
      caption,
      "",
      `HASHTAGS: ${tags.join(" ")}`,
    ].join("\n"),
    caveats: [
      ...copyCaveats(),
      "This is supporting caption copy only. It does NOT replace the video prompt/script and does NOT trigger generation.",
    ],
  };
}

function copyCaveats(): string[] {
  return [
    "Deterministic draft from a template — operator must rewrite in real brand voice before any use.",
    "No LLM / no fetch / no paid call produced this.",
    "Not approved, not published, not shared with the client.",
  ];
}

// --------------------------------------------------------------------------- #
// Entry point

export function planCopyDraft(input: CopyDraftInput): CopyDraftResult {
  const mode = modeForFormat(input.format);
  switch (mode) {
    case "linkedin_text":
      return buildLinkedIn(input);
    case "social_feed":
      return buildSocialFeed(input);
    case "story":
      return buildStory(input);
    case "carousel":
      return buildCarousel(input);
    case "email":
      return buildEmail(input);
    case "blog":
      return buildBlog(input);
    case "video_caption":
      return buildVideoCaption(input);
    default:
      return buildSocialFeed(input);
  }
}
