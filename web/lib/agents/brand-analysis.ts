// Yuvo Studio — Phase 1W Brand Analysis + UGC Prompt Planning agent.
//
// DETERMINISTIC PLANNER. No LLM call, no HTTP fetch, no scrape. Given a
// product URL + optional brand context + optional operator notes, this
// module returns a structured planning draft the operator can iterate
// on.
//
// Every output field is labelled either "Hypothesis" (operator must
// verify against the real source) or "Pattern" (a UGC convention that's
// safe to reuse). Nothing here should be sent to a client without
// operator review. No claim is factual.

import type { MultiFormatCalendarIdea } from "@/lib/content/taxonomy";

// --------------------------------------------------------------------------- #
// Public types

export type BrandAnalysisAgentType = "brand_analysis_ugc_prompt_planning";

export interface BrandAnalysisInput {
  productUrl: string;
  brandName?: string;
  brandNiche?: string;
  brandTone?: string;
  audienceAssumption?: string;
  operatorNotes?: string;
}

export interface PromptDraft {
  /** Short label the operator can paste as `prompt_versions.label`. */
  label: string;
  /** First-line hook (1–2 sentences, real-friend register). */
  hook: string;
  /** Operator-facing script outline (beats + voice direction). */
  script: string;
  /** The composed prompt body the Seedance payload builder consumes. */
  promptBody: string;
  /** Per-beat plan (timing + framing). */
  scenePlan: string;
  /** Creator-direction notes (register, body language, framing). */
  creatorDirection: string;
  /** Product constraints (label, branding, framing rules). */
  productConstraints: string;
  /** Negative prompt — ≤500 chars to fit the Seedance wire cap. */
  negativePrompt: string;
}

export interface BrandAnalysisPlan {
  /** What the operator typed in. Echoed back for context. */
  inputs: BrandAnalysisInput;
  /** What this module decided to assume about the brand (verify!). */
  brandBrief: { hypotheses: string[]; verify: string[] };
  productSummary: {
    inferredCategory: string;
    inferredForm: string;
    hypotheses: string[];
    verify: string[];
  };
  targetAudience: {
    primary: string;
    secondary: string;
    hypotheses: string[];
    verify: string[];
  };
  keySellingPoints: { title: string; rationale: string }[];
  objections: { title: string; rebuttal: string }[];
  contentAngles: { title: string; idea: string }[];
  ugcScenes: { title: string; scene: string; durationSec: number }[];
  promptDrafts: PromptDraft[];
  /** Phase 2D — multi-format calendar ideas. Not every idea is a paid
   *  video ad; each carries channel / format / distribution / goal +
   *  needsGeneration / needsPromptVersion so the dashboard can route it
   *  through the right (often non-video) workflow. */
  contentCalendarIdeas: MultiFormatCalendarIdea[];
  caveats: string[];
  /** Categorisation key used to pick templates (informational; useful for
   *  the UI to show "matched template: skincare"). */
  matchedNiche: string;
}

// --------------------------------------------------------------------------- #
// Niche matching — small, deterministic lookup table.

const NICHE_KEYWORDS: Record<string, string[]> = {
  skincare: [
    "skincare",
    "skin-care",
    "serum",
    "oil",
    "moisturiser",
    "moisturizer",
    "retinol",
    "cleanser",
    "rosehip",
    "balm",
  ],
  supplements: [
    "supplement",
    "vitamin",
    "collagen",
    "protein",
    "magnesium",
    "ashwagandha",
    "creatine",
  ],
  fitness_apparel: [
    "fitness",
    "activewear",
    "leggings",
    "gym",
    "athletic",
    "performance",
    "workout",
  ],
  coffee_beverages: [
    "coffee",
    "espresso",
    "matcha",
    "tea",
    "cold-brew",
    "kombucha",
    "drink",
  ],
  saas_b2b: [
    "saas",
    "platform",
    "dashboard",
    "automation",
    "crm",
    "analytics",
    "workflow",
  ],
  fashion_apparel: [
    "fashion",
    "clothing",
    "apparel",
    "outfit",
    "wear",
    "denim",
    "dress",
  ],
  home_kitchen: [
    "kitchen",
    "home",
    "cookware",
    "knife",
    "appliance",
    "blender",
  ],
};

function lc(s: string | undefined): string {
  return (s ?? "").toLowerCase();
}

function detectNiche(productUrl: string, brandNiche?: string): string {
  const hay = `${lc(productUrl)} ${lc(brandNiche)}`;
  for (const [niche, kws] of Object.entries(NICHE_KEYWORDS)) {
    if (kws.some((kw) => hay.includes(kw))) return niche;
  }
  return "generic_premium_consumer";
}

// --------------------------------------------------------------------------- #
// URL inspection — pure parsing, no fetch.

interface UrlFacts {
  raw: string;
  hostname: string;
  domain: string;
  slugWords: string[];
  /** A short, human label like "paiskincare.com / rosehip-bioregenerate-oil". */
  display: string;
}

function inspectUrl(raw: string): UrlFacts {
  let hostname = "(unparsable)";
  let pathname = "";
  try {
    // URL() accepts any valid absolute URL with a scheme. We do not call
    // it with user-controlled string for any I/O — only for parsing.
    const u = new URL(raw);
    hostname = u.hostname.toLowerCase();
    pathname = u.pathname;
  } catch {
    /* leave as fallback */
  }
  const domain = hostname.replace(/^www\./, "");
  const slugWords = pathname
    .split(/[/_-]+/)
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length >= 2 && /^[a-z0-9-]+$/.test(s));
  const display = `${domain}${pathname && pathname !== "/" ? " " + pathname : ""}`;
  return { raw, hostname, domain, slugWords, display };
}

function titleCaseFromSlug(words: string[]): string {
  if (words.length === 0) return "";
  return words
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// --------------------------------------------------------------------------- #
// Niche-specific template bank (kept small and proven). Every string is
// labelled hypothesis / pattern in the UI; nothing here is a factual
// claim about a specific brand.

interface NicheTemplate {
  brandTone: string;
  audiencePrimary: string;
  audienceSecondary: string;
  productCategory: string;
  productForm: string;
  sellingPoints: { title: string; rationale: string }[];
  objections: { title: string; rebuttal: string }[];
  angles: { title: string; idea: string }[];
  scenes: { title: string; scene: string; durationSec: number }[];
  productConstraints: string;
  negativeBase: string;
}

const TEMPLATES: Record<string, NicheTemplate> = {
  skincare: {
    brandTone: "Calm, ingredient-led, sensitive-skin friendly",
    audiencePrimary:
      "Women 28–45 with sensitive or reactive skin, ingredient-curious, low-volume routine",
    audienceSecondary:
      "Men 30–45 starting a minimal skincare routine for the first time",
    productCategory: "Skincare topical",
    productForm: "Bottle / dropper / pump",
    sellingPoints: [
      {
        title: "Simple ingredient list",
        rationale: "Reduces parsing fatigue for ingredient-curious buyers.",
      },
      {
        title: "Sensitive-skin compatibility",
        rationale: "Removes purchase risk for reactive-skin shoppers.",
      },
      {
        title: "Recognisable bottle / packaging",
        rationale: "Lets the creator hold the product without selling it.",
      },
    ],
    objections: [
      {
        title: "I have reactive skin — will this break me out?",
        rebuttal:
          "Show one creator, calm pacing, no claim escalation. Avoid clinical-white studio.",
      },
      {
        title: "Skincare is over-claimed online — why trust this?",
        rebuttal:
          "Lean on real-friend register; show the bottle as a prop, not a hero.",
      },
      {
        title: "I can't read the label in the ad — what's actually in it?",
        rebuttal:
          "Keep the label deliberately unreadable in-frame. Put the ingredient story in the caption + voiceover, not on a packshot.",
      },
    ],
    angles: [
      {
        title: "One serum, ingredients you can read",
        idea: "Hands-only or face-led UGC; the bottle is incidental. Caption carries the ingredient story.",
      },
      {
        title: "Routine that doesn't feel like too much",
        idea: "Single-take morning beat. Bottle on the counter, not held to camera.",
      },
      {
        title: "What I keep coming back to",
        idea: "Real-friend register; the creator's face carries the ad, not the packaging.",
      },
    ],
    scenes: [
      {
        title: "Hook on face (0–3s)",
        scene:
          "Creator at eye-level, calm. Bottle not yet in frame. Soft daylight, warm domestic interior.",
        durationSec: 3,
      },
      {
        title: "Prop beat (3–8s)",
        scene:
          "Bottle held low or sitting on the counter, partly turned away, soft focus. Not a hero shot.",
        durationSec: 5,
      },
      {
        title: "Application / texture (8–12s)",
        scene:
          "Back of hand or wrist; bottle off-screen entirely. No packaging label visible.",
        durationSec: 4,
      },
      {
        title: "Close (12–15s)",
        scene: "Back to creator's face, brief smile or look-away.",
        durationSec: 3,
      },
    ],
    productConstraints:
      "Recognisable as a premium skincare bottle (frosted/translucent finish, slim form) but no label text required to be legible. If text appears, it must be unreadable. No competing skincare. No on-screen graphics.",
    negativeBase:
      "All small label text MUST be unreadable. No ingredient names, percentages, batch codes, French sub-lines, AI-typical extra fingers, jewellery, nail polish, competing skincare. No label close-ups, no warped/melted text, no animated text, graphics overlays, subtitles, clinical-white studio.",
  },
  supplements: {
    brandTone: "Honest, low-claim, lifestyle-led",
    audiencePrimary:
      "Health-curious adults 25–45 wary of supplement overclaim",
    audienceSecondary:
      "Parents looking for a low-friction daily routine for themselves",
    productCategory: "Supplement / wellness consumable",
    productForm: "Bottle / pouch / packet",
    sellingPoints: [
      {
        title: "Single-ingredient transparency",
        rationale: "Lets the creator name what's actually in it without claim risk.",
      },
      {
        title: "Daily-routine convenience",
        rationale: "Buyers reward 'one less thing to think about'.",
      },
      {
        title: "Clean recognisable container",
        rationale: "Photographs well in real environments.",
      },
    ],
    objections: [
      {
        title: "Supplements are over-claimed — what's different here?",
        rebuttal: "No clinical claim in the ad. Show the routine, not the result.",
      },
      {
        title: "I don't trust influencer supplement ads",
        rebuttal: "Real-friend register. No before/after. No 'sponsored' energy.",
      },
    ],
    angles: [
      {
        title: "What I take and why I keep it simple",
        idea: "Single creator, kitchen counter, morning routine.",
      },
      {
        title: "One pouch, one habit",
        idea: "Hand reaches for the pouch as part of a normal morning.",
      },
    ],
    scenes: [
      {
        title: "Hook (0–3s)",
        scene: "Creator on camera, kitchen daylight.",
        durationSec: 3,
      },
      {
        title: "Routine beat (3–10s)",
        scene: "Creator opens the pouch / measures the scoop, all on-camera.",
        durationSec: 7,
      },
      {
        title: "Close (10–15s)",
        scene: "Creator carries on with the morning; product sits on the counter behind them.",
        durationSec: 5,
      },
    ],
    productConstraints:
      "Recognisable container; no medical or efficacy claim in any frame. Brand mark may be legible. No competing supplements.",
    negativeBase:
      "No clinical-claim text overlays, no before/after, no pill-pour close-ups, no medical setting, no white-coat actors, no graphics overlays, no warped text, no AI-typical extra fingers.",
  },
  fitness_apparel: {
    brandTone: "Functional, low-glam, real-effort",
    audiencePrimary:
      "Recreational lifters / runners 25–40 tired of model-photoshoot ads",
    audienceSecondary: "First-time gym-goers wanting unintimidating cues",
    productCategory: "Performance apparel",
    productForm: "Wearable garment",
    sellingPoints: [
      { title: "Fits real bodies", rationale: "Shows the garment in motion, on a non-model body type." },
      { title: "Functional fabric cue", rationale: "Sweat-wicking, no-ride-up — show, don't claim." },
    ],
    objections: [
      { title: "These ads always use models", rebuttal: "Use a non-model creator. Real gym, not studio." },
    ],
    angles: [
      { title: "Mid-set check-in", idea: "Creator talks to camera between sets, garment in normal use." },
    ],
    scenes: [
      { title: "Hook (0–3s)", scene: "Creator mid-warm-up, casual register.", durationSec: 3 },
      { title: "In-motion (3–11s)", scene: "Movement that exercises the garment naturally.", durationSec: 8 },
      { title: "Close (11–15s)", scene: "Creator catches breath, soft smile.", durationSec: 4 },
    ],
    productConstraints:
      "Garment on a non-model creator. No studio backdrop. No retouched skin. Brand label may be visible naturally.",
    negativeBase:
      "No studio photoshoot lighting, no model poses, no airbrushed skin, no slow-motion glamour, no graphics overlays, no warped text.",
  },
  coffee_beverages: {
    brandTone: "Sensory, mornings-led, low-hype",
    audiencePrimary: "Coffee-curious adults 25–45 valuing daily ritual over caffeine maximalism",
    audienceSecondary: "Specialty-coffee enthusiasts skeptical of mass branding",
    productCategory: "Beverage / consumable",
    productForm: "Cup / can / sachet",
    sellingPoints: [
      { title: "Daily-ritual cue", rationale: "The product belongs to a routine the buyer already has." },
    ],
    objections: [
      { title: "Sugar / additive worry", rebuttal: "Show the product in a plain mug; let the look speak." },
    ],
    angles: [
      { title: "My morning, the slow version", idea: "Creator narrates one beat of their routine." },
    ],
    scenes: [
      { title: "Hook (0–3s)", scene: "Creator pouring / opening the product, warm daylight.", durationSec: 3 },
      { title: "Taste beat (3–9s)", scene: "First sip; eyes-down look.", durationSec: 6 },
      { title: "Close (9–15s)", scene: "Creator carries on with the morning.", durationSec: 6 },
    ],
    productConstraints:
      "Cup / packaging is part of the scene, not the subject. No clinical-white studio.",
    negativeBase:
      "No splash macro shots, no studio table, no graphics overlays, no claim text, no warped logo, no AI-typical extra fingers.",
  },
  saas_b2b: {
    brandTone: "Specific, plain-spoken, ICP-led",
    audiencePrimary: "Founder / operator at a target ICP, watching ads with sound off",
    audienceSecondary: "Buyer's lieutenant doing due-diligence on tools",
    productCategory: "Software product",
    productForm: "Screen recording / talking-head UGC",
    sellingPoints: [
      { title: "One acute pain", rationale: "Generic 'productivity' lands flat — pick one job-to-be-done." },
    ],
    objections: [
      { title: "Tool fatigue", rebuttal: "Show before/after of the actual workflow, not a feature tour." },
    ],
    angles: [
      { title: "The 90-second before/after", idea: "Operator narrates the workflow on their own desk." },
    ],
    scenes: [
      { title: "Hook (0–3s)", scene: "Operator on camera with the screen visible behind them.", durationSec: 3 },
      { title: "Workflow (3–12s)", scene: "Screen-share of the actual flow, narrated.", durationSec: 9 },
      { title: "Close (12–15s)", scene: "Operator looks back at camera, single CTA.", durationSec: 3 },
    ],
    productConstraints:
      "Screen recording must show real (or convincingly mocked) data. No stock footage. No animated UI mock.",
    negativeBase:
      "No stock-footage office, no animated UI mock, no spinning gradient backgrounds, no graphics overlays, no claim text, no AI-typical extra fingers on the keyboard.",
  },
  fashion_apparel: {
    brandTone: "Personal, low-fashion-speak",
    audiencePrimary: "Style-curious adults 25–40 fatigued by influencer haul energy",
    audienceSecondary: "Gift-givers buying for partners",
    productCategory: "Apparel",
    productForm: "Wearable",
    sellingPoints: [
      { title: "Real-life fit", rationale: "Shows the garment outside a photoshoot." },
    ],
    objections: [
      { title: "Influencer-haul fatigue", rebuttal: "One creator, one outfit, real environment." },
    ],
    angles: [
      { title: "What I actually reach for", idea: "Creator narrates why this piece survives their rotation." },
    ],
    scenes: [
      { title: "Hook (0–3s)", scene: "Mirror or near-mirror, soft daylight.", durationSec: 3 },
      { title: "Wear beat (3–11s)", scene: "Garment in real movement.", durationSec: 8 },
      { title: "Close (11–15s)", scene: "Creator carries on with the day.", durationSec: 4 },
    ],
    productConstraints:
      "Garment styled the way a buyer would actually wear it. No retouched skin. No studio backdrop.",
    negativeBase:
      "No catalogue-photoshoot lighting, no model poses, no graphics overlays, no warped text, no AI-typical extra fingers.",
  },
  home_kitchen: {
    brandTone: "Practical, kitchen-honest",
    audiencePrimary: "Adults 28–55 cooking for themselves or a small household",
    audienceSecondary: "Gift-givers buying for a new home",
    productCategory: "Home / kitchen tool",
    productForm: "Tool / appliance",
    sellingPoints: [
      { title: "Real-use moment", rationale: "Show the tool doing one specific thing well." },
    ],
    objections: [
      { title: "Another kitchen gadget", rebuttal: "Show one tight use-case, not a feature list." },
    ],
    angles: [
      { title: "The thing I actually use every week", idea: "Creator walks through one cooking beat." },
    ],
    scenes: [
      { title: "Hook (0–3s)", scene: "Kitchen, warm daylight, tool ready on the bench.", durationSec: 3 },
      { title: "Use beat (3–12s)", scene: "Tool in real use. Hands-only is fine.", durationSec: 9 },
      { title: "Close (12–15s)", scene: "Plated / finished moment.", durationSec: 3 },
    ],
    productConstraints:
      "Tool on a real kitchen surface. No studio shot. No price overlay.",
    negativeBase:
      "No infomercial energy, no animated arrows, no graphics overlays, no warped text, no AI-typical extra fingers.",
  },
  generic_premium_consumer: {
    brandTone: "Calm, real-friend register",
    audiencePrimary: "Adults 25–45 fatigued by influencer-haul energy",
    audienceSecondary: "Gift-givers buying for partners or family",
    productCategory: "Consumer product",
    productForm: "Recognisable everyday object",
    sellingPoints: [
      { title: "One specific reason it gets reached for", rationale: "Generic 'good product' lands flat." },
    ],
    objections: [
      { title: "I've seen 100 ads like this", rebuttal: "One creator. One specific beat. No haul energy." },
    ],
    angles: [
      { title: "What I keep coming back to", idea: "Real-friend register, single creator, single beat." },
    ],
    scenes: [
      { title: "Hook (0–3s)", scene: "Creator on camera, calm, soft daylight.", durationSec: 3 },
      { title: "Prop / use beat (3–11s)", scene: "Product in the scene, not the subject.", durationSec: 8 },
      { title: "Close (11–15s)", scene: "Creator carries on with the day.", durationSec: 4 },
    ],
    productConstraints:
      "Product recognisable but never a hero packshot. No studio backdrop. Brand mark may be visible naturally.",
    negativeBase:
      "No studio packshot lighting, no graphics overlays, no claim text, no warped text, no AI-typical extra fingers, no competing brands visible.",
  },
};

// --------------------------------------------------------------------------- #
// Prompt-draft composer

function composeNegative(template: NicheTemplate, productName: string): string {
  const brandGuard = productName
    ? `Brand wordmark "${productName}" must be exact title-case (no diacritics, no all-caps variants) — and ideally unreadable. `
    : "";
  const out = `${brandGuard}${template.negativeBase}`;
  return out.length > 500 ? out.slice(0, 499) + "…" : out;
}

function composePromptBody(
  input: BrandAnalysisInput,
  template: NicheTemplate,
  productName: string,
): string {
  const audience = input.audienceAssumption || template.audiencePrimary;
  const tone = input.brandTone || template.brandTone;
  return [
    `UGC product-talk, 15s, 720p, 9:16 vertical, native audio.`,
    `Single take in a warm domestic interior (soft daylight — never a clinical-white studio).`,
    `Creator early-30s, ${audience.toLowerCase()}.`,
    `Register: ${tone.toLowerCase()}. Real-friend energy, not influencer-haul energy.`,
    `THE CREATOR CARRIES THE AD, NOT THE PACKAGING. The product is incidental.`,
    productName
      ? `Product: ${productName}. ${template.productConstraints}`
      : template.productConstraints,
  ].join(" ");
}

function composeScript(template: NicheTemplate, productName: string): string {
  const beats = template.scenes
    .map(
      (s, i) =>
        `Beat ${i + 1} (${s.durationSec}s) — ${s.title}: ${s.scene}`,
    )
    .join("\n");
  return [
    productName
      ? `Subject: ${productName}. Single take. ${template.scenes.length} beats over 15s.`
      : `Single take. ${template.scenes.length} beats over 15s.`,
    beats,
  ].join("\n\n");
}

function composeScenePlan(template: NicheTemplate): string {
  let elapsed = 0;
  return template.scenes
    .map((s) => {
      const from = elapsed;
      elapsed += s.durationSec;
      return `${from}-${elapsed}s: ${s.title}. ${s.scene}`;
    })
    .join("\n");
}

function composeCreatorDirection(template: NicheTemplate): string {
  return [
    `Tone: ${template.brandTone}.`,
    `Audience cue: ${template.audiencePrimary}.`,
    `Body language: relaxed, slow blinks, occasional looks-away. No 'presenting' the product.`,
    `Hands: relaxed; when the product is held, hold it loosely at chest-height or below.`,
  ].join(" ");
}

// --------------------------------------------------------------------------- #
// Top-level planner

export function planBrandAnalysisUGCPrompt(
  input: BrandAnalysisInput,
): BrandAnalysisPlan {
  const url = inspectUrl(input.productUrl);
  const niche = detectNiche(input.productUrl, input.brandNiche);
  const template = TEMPLATES[niche] ?? TEMPLATES.generic_premium_consumer;

  const productName =
    input.brandName ||
    titleCaseFromSlug(url.slugWords) ||
    titleCaseFromSlug(url.domain.split(".")[0]?.split("-") ?? []);

  const brandTone = input.brandTone || template.brandTone;
  const audiencePrimary = input.audienceAssumption || template.audiencePrimary;

  const hook =
    niche === "skincare"
      ? "I like that this feels simple — one thing, ingredients I can actually understand."
      : niche === "supplements"
        ? "What I take and why I keep it simple."
        : niche === "fitness_apparel"
          ? "Mid-set check-in — this is what I actually reach for."
          : niche === "coffee_beverages"
            ? "My morning, the slow version."
            : niche === "saas_b2b"
              ? "Here's the 90 seconds that replaced a Tuesday afternoon."
              : niche === "fashion_apparel"
                ? "What I actually reach for — not what I post."
                : niche === "home_kitchen"
                  ? "This is the one I use every week."
                  : "What I keep coming back to.";

  const promptDraftStandard: PromptDraft = {
    label: `${productName || "Untitled product"} · 15s UGC draft (v1)`,
    hook,
    script: composeScript(template, productName),
    promptBody: composePromptBody(input, template, productName),
    scenePlan: composeScenePlan(template),
    creatorDirection: composeCreatorDirection(template),
    productConstraints: template.productConstraints,
    negativePrompt: composeNegative(template, productName),
  };

  // Variant 2 — a stricter "label-stripped" variant for niches where
  // label hallucination has historically been a defect (skincare,
  // supplements). The Pai V4 lesson.
  const promptDraftLabelStrict: PromptDraft | null =
    niche === "skincare" || niche === "supplements"
      ? {
          ...promptDraftStandard,
          label: `${productName || "Untitled product"} · 15s UGC draft (label-strict v2)`,
          productConstraints:
            template.productConstraints +
            " STRICT MODE: the product reference image must be label-free or have its label digitally stripped. NO printed copy is allowed on the bottle/container in any frame.",
          negativePrompt: composeNegative(template, productName).replace(
            /^/,
            "ZERO readable label text. The container MUST be label-free. ",
          ),
        }
      : null;

  // Phase 2D — a multi-format two-week plan. Most of these are NOT paid
  // video ads: organic reels, stories, carousels, LinkedIn/feed copy,
  // an email snippet. Only the paid UGC ad needs a (later, gated) video
  // generation. needsGeneration / needsPromptVersion tell the dashboard
  // which workflow each item belongs to. Nothing here is auto-executed.
  const contentCalendarIdeas: MultiFormatCalendarIdea[] = [
    {
      dayOffset: 0,
      title: "Concept lock + format mix",
      brief:
        "Operator reviews this plan, confirms the brand brief, and picks which formats ship this cycle. No asset work yet.",
      suggestedChannel: "other",
      suggestedFormat: "text_post",
      distributionType: "client_review_only",
      contentGoal: "awareness",
      recommendedAssetType: "copy_only",
      needsGeneration: false,
      needsPromptVersion: false,
      operatorNotes:
        "Planning checkpoint only. Decide the week's format mix before any production.",
    },
    {
      dayOffset: 2,
      title: "Organic Instagram Reel — hook-led",
      brief:
        "Short organic reel using the standard UGC angle. Needs a video prompt later (gated).",
      suggestedChannel: "instagram",
      suggestedFormat: "organic_reel",
      distributionType: "organic",
      contentGoal: "awareness",
      recommendedAssetType: "short_video",
      needsGeneration: true,
      needsPromptVersion: true,
      operatorNotes:
        "Reuse the standard prompt draft. Organic, not boosted. Seedance only after explicit operator approval.",
    },
    {
      dayOffset: 3,
      title: "Instagram Story — quick routine",
      brief:
        "2–3 frame story showing the product in a real routine. Copy + frame brief, no video generation by default.",
      suggestedChannel: "instagram",
      suggestedFormat: "story",
      distributionType: "organic",
      contentGoal: "trust_building",
      recommendedAssetType: "story_frames",
      needsGeneration: false,
      needsPromptVersion: false,
      operatorNotes:
        "Story copy + visual brief only. No Seedance unless the operator later upgrades it to a short video.",
    },
    {
      dayOffset: 4,
      title: "LinkedIn post — founder POV",
      brief:
        "Text post on the problem the product solves, founder voice. Copy draft only.",
      suggestedChannel: "linkedin",
      suggestedFormat: "text_post",
      distributionType: "organic",
      contentGoal: "education",
      recommendedAssetType: "copy_only",
      needsGeneration: false,
      needsPromptVersion: false,
      operatorNotes:
        "Copy Draft Agent (future) or Claude Code handoff. No video, no prompt_version.",
    },
    {
      dayOffset: 5,
      title: "Carousel — ingredient / value breakdown",
      brief:
        "5–7 slide carousel explaining what makes the product different. Slide outline + copy.",
      suggestedChannel: "instagram",
      suggestedFormat: "carousel",
      distributionType: "organic",
      contentGoal: "education",
      recommendedAssetType: "carousel_slides",
      needsGeneration: false,
      needsPromptVersion: false,
      operatorNotes:
        "Carousel Outline Agent (future). Static slides; no Seedance.",
    },
    {
      dayOffset: 7,
      title: "Paid UGC video ad — primary",
      brief:
        promptDraftLabelStrict
          ? "The label-strict UGC variant as a paid ad. Needs a video prompt + (later, gated) Seedance generation."
          : "The standard UGC variant as a paid ad. Needs a video prompt + (later, gated) Seedance generation.",
      suggestedChannel: "instagram",
      suggestedFormat: "ugc_video_ad",
      distributionType: "paid",
      contentGoal: "conversion",
      recommendedAssetType: "ugc_video",
      needsGeneration: true,
      needsPromptVersion: true,
      operatorNotes:
        "This is the only inherently-paid item. Generation stays behind the existing per-clip operator gate.",
    },
    {
      dayOffset: 9,
      title: "Facebook feed post — social proof",
      brief:
        "Feed post pairing a short testimonial line with a static image idea. Copy + image brief.",
      suggestedChannel: "facebook",
      suggestedFormat: "feed_post",
      distributionType: "organic",
      contentGoal: "testimonial",
      recommendedAssetType: "static_image",
      needsGeneration: false,
      needsPromptVersion: false,
      operatorNotes: "Copy + static image brief. No video.",
    },
    {
      dayOffset: 11,
      title: "Email snippet — nurture",
      brief:
        "Short email section reinforcing the core value prop for the existing list. Copy only.",
      suggestedChannel: "email",
      suggestedFormat: "email_snippet",
      distributionType: "organic",
      contentGoal: "retention",
      recommendedAssetType: "email_copy",
      needsGeneration: false,
      needsPromptVersion: false,
      operatorNotes:
        "Copy only. Never auto-sent — drafted for operator review (see hybrid execution model).",
    },
    {
      dayOffset: 14,
      title: "Calendar restock + retro",
      brief:
        "Re-plan the next two weeks based on which formats landed. Planning checkpoint.",
      suggestedChannel: "other",
      suggestedFormat: "text_post",
      distributionType: "client_review_only",
      contentGoal: "community",
      recommendedAssetType: "copy_only",
      needsGeneration: false,
      needsPromptVersion: false,
      operatorNotes: "Planning checkpoint only. No asset work.",
    },
  ];

  return {
    inputs: input,
    matchedNiche: niche,
    brandBrief: {
      hypotheses: [
        `Brand presents as ${brandTone.toLowerCase()} on the supplied URL.`,
        productName
          ? `Likely product or line: ${productName}.`
          : "Product name could not be inferred from the URL — operator must supply it.",
        `Hostname inspected: ${url.domain || "(unparsable URL)"}.`,
      ],
      verify: [
        "Open the supplied URL and confirm the brand wordmark exactly (case + diacritics).",
        "Confirm the product photographed matches the inferred form factor (bottle, garment, packet, etc.).",
        "Confirm tone-of-voice against the brand's homepage or about page.",
      ],
    },
    productSummary: {
      inferredCategory: template.productCategory,
      inferredForm: template.productForm,
      hypotheses: [
        `Product category (hypothesis): ${template.productCategory}.`,
        `Product form (hypothesis): ${template.productForm}.`,
        `Niche template matched: ${niche.replaceAll("_", " ")}.`,
      ],
      verify: [
        "Confirm the product's category against the actual landing page.",
        "Confirm the form factor — UGC scene plan depends on this.",
        input.operatorNotes
          ? `Operator-supplied notes (raw, untransformed): "${input.operatorNotes}".`
          : "If the URL doesn't match this niche, override `brandNiche` and re-run.",
      ],
    },
    targetAudience: {
      primary: audiencePrimary,
      secondary: template.audienceSecondary,
      hypotheses: [
        `Primary audience (hypothesis): ${audiencePrimary}.`,
        `Secondary audience (hypothesis): ${template.audienceSecondary}.`,
      ],
      verify: [
        "Confirm against the brand's actual customer base.",
        "Sanity-check: would this audience watch a 15s 9:16 UGC ad? If not, re-plan.",
      ],
    },
    keySellingPoints: template.sellingPoints,
    objections: template.objections,
    contentAngles: template.angles,
    ugcScenes: template.scenes,
    promptDrafts: promptDraftLabelStrict
      ? [promptDraftStandard, promptDraftLabelStrict]
      : [promptDraftStandard],
    contentCalendarIdeas,
    caveats: [
      "Every section is a planning hypothesis — the operator must verify against the real brand source before any client share.",
      "No website fetch happened. No LLM call happened. No external data was retrieved.",
      "Claims about the product / brand / audience are NOT factual — they're conventions from the matched niche template.",
      "Prompt drafts are starting points. Paste into the prompt editor as a draft / operator_editing version; never auto-approve for generation.",
    ],
  };
}

// --------------------------------------------------------------------------- #
// Validation — kept tiny + pure. Used by the server action.

export interface PlanValidationResult {
  ok: boolean;
  error?: string;
}

export function validateBrandAnalysisInput(
  input: BrandAnalysisInput,
): PlanValidationResult {
  const u = (input.productUrl ?? "").trim();
  if (!u) return { ok: false, error: "Product URL is required." };
  if (u.length > 2048) {
    return { ok: false, error: "Product URL is unreasonably long." };
  }
  try {
    const parsed = new URL(u);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return {
        ok: false,
        error: "Product URL must use http:// or https://.",
      };
    }
  } catch {
    return { ok: false, error: "Product URL is not a valid URL." };
  }
  if ((input.brandName ?? "").length > 200) {
    return { ok: false, error: "Brand name is too long." };
  }
  if ((input.operatorNotes ?? "").length > 4000) {
    return { ok: false, error: "Operator notes are too long (4000 char cap)." };
  }
  return { ok: true };
}
