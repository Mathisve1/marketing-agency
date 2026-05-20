// Yuvo Studio — Phase 4E visual theme presets.
//
// Pure constants. No I/O. Theme presets are applied at preview level
// (querystring `?theme=…` override, falling back to the template's
// `defaultTheme`). They control surface / accent colours and a few
// typography hints on the shared `PreviewCard` — they do NOT change
// any persisted data.

export type ThemeId =
  | "neutral"
  | "editorial"
  | "bold"
  | "soft"
  | "premium_dark";

export interface VisualThemePreset {
  id: ThemeId;
  name: string;
  description: string;
  /** Tailwind utility classes applied to `PreviewCard`'s outer
   *  surface. Keep these self-contained so a renderer change does
   *  not need to ripple through every template. */
  surfaceClass: string;
  /** Subtle highlight class layered on top of the surface (radial
   *  gradient mask). */
  highlightClass: string;
  /** Class for the in-card accent text (small caps / dividers). */
  accentClass: string;
  /** Class for the dominant headline within the card. */
  headlineClass: string;
  /** Optional hint we surface on hover / chips. */
  textHierarchyNotes: string;
  bestFor: string;
}

const presets: VisualThemePreset[] = [
  {
    id: "neutral",
    name: "Neutral",
    description: "Black/white base with a soft top-left radial highlight.",
    surfaceClass: "bg-[color:var(--color-ink)] text-white",
    highlightClass:
      "bg-[radial-gradient(60%_80%_at_20%_0%,rgba(255,255,255,0.18)_0%,transparent_60%),radial-gradient(40%_60%_at_100%_100%,rgba(255,255,255,0.08)_0%,transparent_70%)]",
    accentClass: "text-white/55",
    headlineClass: "text-white",
    textHierarchyNotes:
      "Big headline, small accent caps, body at 85% opacity.",
    bestFor: "Default. Trust-building, ingredient stories.",
  },
  {
    id: "editorial",
    name: "Editorial",
    description:
      "Warm cream surface with deep ink type — magazine-feel.",
    surfaceClass:
      "bg-[color:var(--color-cream-soft)] text-[color:var(--color-ink)]",
    highlightClass:
      "bg-[radial-gradient(60%_80%_at_80%_0%,rgba(0,0,0,0.06)_0%,transparent_60%)]",
    accentClass: "text-[color:var(--color-ink-faint)]",
    headlineClass: "text-[color:var(--color-ink)] font-semibold tracking-tight",
    textHierarchyNotes:
      "Large serif-feel headline, generous leading, deep ink on cream.",
    bestFor: "Brand stories, founder POV, lifestyle.",
  },
  {
    id: "bold",
    name: "Bold",
    description:
      "Saturated accent banner over a neutral base — promo / offer feel.",
    surfaceClass: "bg-[color:var(--color-ink)] text-white",
    highlightClass:
      "bg-[linear-gradient(140deg,rgba(255,255,255,0)_55%,color-mix(in_oklab,var(--color-accent)_60%,transparent)_55%)]",
    accentClass: "text-[color:var(--color-accent)]",
    headlineClass: "text-white uppercase tracking-[0.04em]",
    textHierarchyNotes:
      "Diagonal accent block; uppercase headline; numeric pricing dominant.",
    bestFor: "Launches, restocks, time-limited offers.",
  },
  {
    id: "soft",
    name: "Soft",
    description: "Very light pastel surface with subtle type.",
    surfaceClass:
      "bg-white text-[color:var(--color-ink)] border border-[color:var(--color-hairline)]",
    highlightClass:
      "bg-[radial-gradient(80%_100%_at_50%_0%,rgba(0,0,0,0.04)_0%,transparent_70%)]",
    accentClass: "text-[color:var(--color-ink-faint)]",
    headlineClass: "text-[color:var(--color-ink)] font-medium",
    textHierarchyNotes:
      "Mid-weight headline, lots of negative space, no chrome.",
    bestFor: "Soft brand moments, quote frames, minimal stories.",
  },
  {
    id: "premium_dark",
    name: "Premium dark",
    description:
      "Deep charcoal with a single specular highlight; luxury-feel.",
    surfaceClass: "bg-[#0c0d10] text-white",
    highlightClass:
      "bg-[radial-gradient(80%_60%_at_30%_15%,rgba(255,255,255,0.16)_0%,transparent_60%)]",
    accentClass: "text-white/55 uppercase tracking-[0.18em]",
    headlineClass: "text-white font-semibold tracking-tight",
    textHierarchyNotes:
      "High-end charcoal; minimal accent caps; restrained typography.",
    bestFor: "LinkedIn thought-leader posts, premium product moments.",
  },
];

const byId = new Map<ThemeId, VisualThemePreset>(presets.map((p) => [p.id, p]));

export const VISUAL_THEMES: VisualThemePreset[] = presets;

export function getThemePreset(id: string | null | undefined): VisualThemePreset {
  if (id && (byId.get(id as ThemeId) ?? null)) return byId.get(id as ThemeId)!;
  return byId.get("neutral")!;
}

export function isValidThemeId(id: string | null | undefined): boolean {
  return Boolean(id && byId.has(id as ThemeId));
}

/** Theme id resolver: querystring override -> template's defaultTheme
 *  -> hardcoded "neutral". The caller normalises invalid ids to null
 *  before passing in (so we can surface an "invalid theme" warning). */
export function resolveThemeId(input: {
  queryThemeId?: string | null;
  templateDefault?: string | null;
}): ThemeId {
  if (input.queryThemeId && byId.has(input.queryThemeId as ThemeId)) {
    return input.queryThemeId as ThemeId;
  }
  if (input.templateDefault && byId.has(input.templateDefault as ThemeId)) {
    return input.templateDefault as ThemeId;
  }
  return "neutral";
}
