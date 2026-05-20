# Visual Asset Generation Plan (Phase 4B)

Status: **planning + architecture only**. No code, no migration, no
external service call, no paid API. This document chooses *how*
creative briefs (Phase 4A) become final social-post pixels in a way
that preserves the existing safety model: nothing paid runs without an
explicit operator gate; nothing reaches the client portal until an
operator flips a `client_safe_*` field.

## Current state after Phase 4A

What we already have:

- **Structured creative briefs** in
  `content_items.prompt_summary` as a `[creative brief]` block (see
  `docs/social_creative_brief_builder.md`). The brief is a
  deterministic JSON-shaped object with universal fields plus
  format-specific shapes: `slides[]` (carousel, 5×), `frames[]`
  (story, 3×), `mainVisual` / `headlineOverlay` / `captionSupport`
  (feed_post / static_image), `postHook` / `imageConcept` /
  `professionalToneNotes` (LinkedIn), `thumbnailConcept` /
  `onScreenTextBeats[]` / `brollCues[]` (reel_support),
  `thumbnailConcept` / `hookFrame` / `propBrief`
  (video_visual_support).
- **Formats supported by the brief**: carousel, story, feed_post,
  static_image, text_post (LinkedIn), organic_reel + short_video
  (reel-support), ugc_video_ad + long_video (visual-support).
- **Rendering metadata available**: title, channel, content goal,
  format, mode, plus all the structured per-slide / per-frame text
  and visual direction. Brand elements (tone, audience, primary
  colour, niche palette) are also in the brief.
- **Existing asset storage**: `generated_assets` table — kinds
  `raw_video`, `audio_fixed_video`, `thumbnail`, `caption_srt`,
  `export`, `static_image`, `caption_pack`, `post_creative`. Has
  `storage_path` / `public_url` / `mime` / `byte_size` / `resolution`.
  **Currently coupled to `generation_jobs` / `audio_fixer_jobs`** —
  every row carries one of those FK fields, so it's a poor fit for
  brief-driven creative assets that have no upstream paid job.
- **Client projection**: `client_content_items_v` (migration 009)
  exposes `client_safe_poster_url`, `client_safe_video_url`,
  `client_safe_copy_preview` — but **no `client_safe_visual_url` yet**.

What is missing before final asset generation can happen:

1. A render pipeline (HTML/CSS → PNG, or SVG → PNG, or AI → PNG).
2. An asset-metadata model linking brief → slide/frame → render →
   template_id → variant → status.
3. A `client_safe_visual_url` field or table + a "prepare → share"
   lifecycle equivalent to the copy preview.
4. Storage for binary outputs (no R2/KV bindings exist yet —
   `wrangler.jsonc` notes them as "deferred").
5. An export path. Cloudflare Workers cannot run Puppeteer / native
   image-processing modules; the export needs a different runtime
   (see §7 below).
6. An internal-approval column (or block in `prompt_summary`)
   equivalent to `[copy approval]` for visuals.

---

## Option comparison

Trade-off summary, then detail.

| Dimension | A — HTML/CSS | B — SVG/Canvas | C — AI image gen | D — Hybrid |
|---|---|---|---|---|
| Per-render cost | $0 | $0 | $0.02–$0.08+/img | $0–$0.08/img |
| Text accuracy | 100% | 100% | poor (hallucination) | 100% (templates) |
| Brand consistency | excellent | excellent | poor | excellent |
| Carousel support | ideal | tedious | risky | ideal |
| Story support | ideal | acceptable | risky | ideal |
| Layout flexibility | high | medium | low | high |
| Image handling | native (`<img>`) | weak | native | native |
| Cloudflare-runs preview | yes | yes | n/a | yes |
| Cloudflare-runs export | **no** | partial (wasm rasterizer) | yes (fetch) | no (template export needs runtime) |
| Client-revision cost | low | low | high (regen) | low |
| Risk of going off-brand | low | low | **high** | low |

### Option A — HTML/CSS template export

**Description.** Build a React/HTML/CSS template per format. Render
server-side (Next.js server component) for an internal preview;
export to PNG via a headless browser.

- **Strengths**: cheap, deterministic, perfect text, pixel-locked
  brand layouts, easy client revisions (change brief → re-render),
  unbeatable for carousels (5-slide grid) and stories (vertical 9:16
  with overlay text), good for LinkedIn graphics.
- **Weaknesses**: PNG export needs a headless browser, which does
  NOT run in a Cloudflare Worker. Export must live elsewhere
  (external service, Cloudflare Browser Rendering API, or local
  operator script). Typography needs explicit web-font hosting.
- **Cost**: $0 for preview; PNG export cost depends on chosen runtime
  (Browser Rendering API: ~$0.04/sec; external service: depends;
  local: $0).
- **Speed**: preview <100 ms; PNG export ~1–3 s/slide depending on
  runtime.
- **Brand consistency**: highest possible. Templates are committed code.
- **Text accuracy**: 100%; you specify the text.
- **Carousel support**: ideal — a `<CarouselTemplate slides={...}>`
  React component renders the 5 slides as a deterministic grid; each
  slide exports individually.
- **Story support**: ideal — a `<StoryTemplate frames={...}>`
  component renders 3 vertical frames.
- **Client revisions**: easy — operator re-runs the brief or tweaks
  template props; re-render is free.
- **Technical complexity**: moderate. The split between
  (server preview) + (external export) adds a deployment surface.
- **Cloudflare/Workers deployability**: preview yes; export no
  (Puppeteer / Chromium do not run in Workers; `nodejs_compat`
  flag does not cover native binaries).
- **Puppeteer/Playwright feasibility on current hosting**: **no**,
  not in a Worker. Feasible in: (a) Cloudflare Browser Rendering API
  (beta, paid), (b) external service like browserless.io / your own
  VPS, (c) local Node.js script invoked by the operator (already
  established pattern — e.g. `scripts/run_audio_fixer_job.py`).
- **External render service**: only required if (b) is chosen.

### Option B — Canvas/SVG server-side exports

**Description.** Compose layouts as SVG strings in code; rasterize
to PNG using a wasm SVG → PNG library (e.g. `resvg-js`).

- **Strengths**: pure code, no browser, can in principle run inside a
  Worker. 100% text accuracy. Brand consistency excellent.
- **Weaknesses**: image handling is fragile (you have to fetch+embed
  raster images yourself; CORS + bandwidth headaches inside a
  Worker). Typography limited — web fonts in SVG are brittle; you'll
  ship font files in the bundle. Layouts that look easy in HTML
  (flexbox, grid) require manual coordinate math in SVG. Carousels
  possible but tedious; the per-slide complexity adds up.
- **Cost**: $0/render; wasm CPU usage counts against Worker CPU
  limits (50 ms by default; can be raised). Larger templates may
  exceed.
- **Brand consistency**: excellent.
- **Layout flexibility**: lower than HTML/CSS. Harder to handle
  complex content reflow.
- **Typography quality**: medium. Subpixel rendering and kerning
  worse than browser engines.
- **Image handling**: weak. You must base64-embed every raster used.
- **Carousel support**: yes, but each slide is hand-coordinated.
- **Cloudflare compatibility**: best of the three for in-Worker
  export (wasm rasterizers exist), but bundle size + CPU budget
  matters.

### Option C — AI image generation API

**Description.** Call DALL·E 3 / Imagen / Stable Diffusion to
generate the full image from a prompt.

- **Strengths**: photorealistic backgrounds, lifestyle scenes,
  abstract textures, hero shots without a photo shoot.
- **Weaknesses**:
  - **Text hallucination is the killer.** Every model still mangles
    long on-image text — a one-word headline often comes out
    misspelt; multi-line copy is unusable.
  - **Product accuracy risk.** Model invents product packaging,
    ingredient lists, label text. Unacceptable for skincare /
    supplements / fitness apparel where the actual product matters.
  - **Cost**: DALL·E 3 standard ~$0.04/img, HD ~$0.08/img; Imagen
    similar; Midjourney via API requires a paid plan. Multiply by
    ~5 slides per carousel and ~3 frames per story.
  - **Moderation and safety**: every output needs human review;
    risk of off-brand / off-tone outputs.
  - **Control limitations**: hard to reproduce a specific look
    across slides; carousels become inconsistent.
- **Best use cases**: stylized backgrounds, abstract textures,
  lifestyle scenes WITHOUT visible product packaging or on-image
  text; **as a layer underneath a deterministic template**, never
  as the whole final asset.
- **Worst use cases**: anything with a product close-up, any
  on-image headline / CTA / pricing, brand-locked layouts,
  carousels with consistency requirements, LinkedIn corporate
  graphics.
- **Required operator gates**:
  - Explicit confirmation phrase like the existing Seedance gate
    (e.g. operator must type "RUN PAID IMAGE GEN").
  - Cost-estimate preview before any call.
  - Mandatory manual review **before** the asset can be marked
    `approved_internal`.
  - Separate client-preview lifecycle — generated output never
    becomes `client_safe_visual_url` automatically.
  - Product/text accuracy check column on the asset row.

### Option D — Hybrid

**Description.** Deterministic HTML/CSS template owns the layout +
all text + brand elements. AI image gen (option C) provides
**replaceable background / lifestyle layers** that the template
composites on top of. The operator picks an AI background, the
template lays brand-safe text and product over it.

- **How it works**: template props include `backgroundLayerUrl`. If
  unset, the template uses a brand-palette fill or a stock asset. If
  set, the template renders the AI background as the base layer.
  The operator can swap the background freely without touching the
  text/CTA/layout.
- **Why it may be best long-term**: combines the brand-safety of
  templates with the visual variety of AI, while structurally
  preventing the two failure modes of pure AI: text hallucination
  (template owns text) and product inaccuracy (template owns
  product layer / brand layer).
- **What should be built first**: the templates (option A
  preview-only first; then export). AI backgrounds are an opt-in
  later layer.
- **Brand-consistency protection**: by construction — text and
  product never come from the AI side.
- **Text-hallucination avoidance**: by construction — all on-image
  text comes from the template, not the model.
- **Fits existing safety gates**: template renders are free and can
  ship without a gate; AI background calls inherit the Seedance-style
  paid-action gate (confirmation phrase + cost estimate + operator
  review before share).

---

## Recommendation

The current operator preference is correct: **start with HTML/CSS
template previews**. Validated honestly:

✅ Cheap, predictable, brand-safe — confirmed.
✅ Strong for carousels / stories / LinkedIn — confirmed.
✅ Avoids AI text hallucination — confirmed.
✅ Easier client revisions — confirmed.
✅ Better for agency production consistency — confirmed.

⚠️ **Caveat.** PNG export from HTML/CSS does NOT run in a Cloudflare
Worker. The MVP should be **preview-only** (React server component in
the dashboard); PNG export is a follow-up build that picks one of
three runtimes. **Do not block the MVP on the export choice.**

### Implementation order

**Build 1 — HTML/CSS template previews (Phase 4C MVP).**
Internal-only React templates rendered server-side from the
`[creative brief]` block. One template each for carousel, story,
feed_post. No PNG, no DB schema change, no client share.

**Build 2 — PNG export pipe.**
Pick one of:
- (B2-a) Cloudflare Browser Rendering API (native, beta, paid per
  second).
- (B2-b) External headless-browser service (browserless.io /
  pdfshift / your own small VPS) — most flexible, vendor-neutral.
- (B2-c) Local operator-run Node.js Puppeteer script that hits the
  dashboard's preview URL and uploads to R2 (mirrors the existing
  Seedance copy-paste pattern: operator runs it on their own
  machine).

Recommendation: **start with B2-c (local operator script)** because
it matches the existing "operator-controlled paid step" pattern, has
zero deploy surface, and zero new vendor dependency. Migrate to
B2-a/B2-b later if volume warrants.

**Build 3 — Client-safe visual preview lifecycle.**
Add `content_items.client_safe_visual_url` (or a `creative_assets`
table — see §4). Mirror the copy-preview lifecycle exactly:
`prepared` → `shared_with_client`. Never automatic.

**Avoid for now (defer to Build 4+):**
- AI image generation. Defer until templates ship and the team has a
  proven manual approval flow. When introduced, it lives **only** as
  a background-layer opt-in inside the template — never as the
  whole final asset.
- Mass / parallel render queues.
- Direct social-platform publishing integrations.

---

## Proposed data model (NOT applied)

A new table is cleaner than extending `generated_assets`: the existing
table is FK-coupled to `generation_jobs` / `audio_fixer_jobs`, whereas
brief-driven creative assets have no upstream paid job.

```sql
-- supabase/migrations/0XX_creative_assets.sql (PROPOSAL — DO NOT APPLY)
create table public.creative_assets (
  id                       uuid primary key default gen_random_uuid(),
  workspace_id             uuid not null references public.workspaces(id) on delete cascade,
  content_item_id          uuid not null references public.content_items(id) on delete cascade,

  -- Provenance: which brief produced this asset.
  creative_brief_source    jsonb,           -- structured brief snapshot (immutable)
  asset_type               text not null,   -- 'carousel_slide' | 'story_frame' |
                                            -- 'feed_post' | 'static_image' | 'linkedin_image' |
                                            -- 'reel_thumbnail' | 'video_thumbnail'
  channel                  text,
  format                   text,

  -- Render details (which template + variant).
  render_strategy          text not null,   -- 'html_css_template' | 'svg_template' |
                                            -- 'ai_background_layer' | 'manual_upload'
  template_id              text,
  template_version         text,
  brief_json               jsonb not null,  -- the JSON used to render
  variant_number           int  not null default 1,
  slide_number             int,             -- carousel only
  frame_number             int,             -- story only

  -- Output.
  asset_url                text,            -- internal R2 / supabase storage
  thumbnail_url            text,
  export_format            text,            -- 'png' | 'jpg' | 'webp'
  width                    int,
  height                   int,

  -- Lifecycle (mirrors copy lifecycle).
  status                   text not null,   -- see below
  approved_internal_at     timestamptz,
  client_safe_visual_url   text,            -- only set after explicit operator action
  shared_with_client       boolean not null default false,

  created_by               uuid references public.profiles(id) on delete set null,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

-- Allowed status values (CHECK constraint or domain):
--   draft
--   rendered_internal
--   approved_internal
--   client_preview_prepared
--   shared_with_client
--   approved_by_client
--   changes_requested_by_client
--   archived
```

RLS: operator-only `select`/`insert`/`update` via
`app.is_workspace_member(workspace_id)`. **No DELETE policy.** Client
portal sees only rows with `shared_with_client = true` AND a
non-null `client_safe_visual_url`, through a scoped view
(`client_creative_assets_v`) that projects no provider IDs / costs.

`creative_brief_source` snapshots the brief so the asset stays
reproducible even if the underlying `[creative brief]` block is
later re-drafted on the content_item.

**Do NOT apply this migration in Phase 4B.** It is a proposal only.
Phase 4C MVP ships without it (preview is computed from the existing
`[creative brief]` block on each render — no persistence needed
until export lands).

---

## Asset lifecycle

```
[creative brief]
   |
   v
visual asset DRAFT
   |  (template chosen; brief_json frozen for this render)
   v
RENDERED_INTERNAL              <-- React template preview / future PNG export
   |  (operator reviews pixels)
   v
APPROVED_INTERNAL              <-- internal sign-off; not yet client-visible
   |  (operator explicitly prepares preview)
   v
CLIENT_PREVIEW_PREPARED        <-- client_safe_visual_url set; shared_with_client=false
   |  (operator clicks "Share")
   v
SHARED_WITH_CLIENT             <-- shared_with_client=true; client portal can see it
   |
   +-- APPROVED_BY_CLIENT
   |
   +-- CHANGES_REQUESTED_BY_CLIENT --> revision loop --> new draft
   |
   +-- ARCHIVED
```

Compared to the existing copy lifecycle (Phase 2F–2I):

| Stage | Copy (Phase 2) | Visual (Phase 4 plan) |
|---|---|---|
| draft | `[copy draft]` block | `creative_assets.status='draft'` |
| internal approval | `[copy approval]` block | `creative_assets.approved_internal_at` |
| client preview prepared | `content_items.client_safe_copy_preview` | `creative_assets.client_safe_visual_url` |
| shared | `content_items.shared_with_client=true` | `creative_assets.shared_with_client=true` |
| client decision | `content_approvals` row | `content_approvals` row (reused) |
| changes | `regeneration_requests` / revise action | new creative_asset row (revision) |

**Same safety contract as copy:**

- No automatic publishing. Posting to Instagram / Facebook / LinkedIn
  is **out of scope** until a future explicit phase.
- No automatic client share. `shared_with_client` flips only via an
  explicit operator action (mirrors `shareCopyPreviewWithClientAction`).
- No hidden paid call. Template renders are free; any paid render
  (Browser Rendering API, AI image gen) sits behind the
  Seedance-style gate.
- Every irreversible / paid step has an explicit operator gate.

---

## Phase 4C MVP scope

**Phase 4C — HTML/CSS Carousel & Story Template Renderer (preview-only).**

In scope:
- Three deterministic React templates: `CarouselTemplate`,
  `StoryTemplate`, `FeedPostTemplate`.
- Server-rendered preview at
  `/agency/creative-briefs/[contentItemId]/preview` (new route).
- Each template reads the `[creative brief]` block off the
  content item and renders the structured slides/frames/visual
  per-format.
- Template-selection UX (single dropdown — initially one template
  per format, expandable).
- Internal-only "Approve internal" button that writes a small
  marker to `prompt_summary` (no schema change yet):
  `[creative brief approval]` block with
  `creative_brief_approved_internal_at: <iso>`. Mirrors `[copy approval]`.
- **No PNG export.**
- **No client share.**
- **No new table.** (Schema lands in a later migration when export
  arrives.)
- **No paid APIs.** No image gen. No external service.
- **No publishing.**

Out of scope (deferred to 4D+):
- PNG/JPG export pipe (Build 2 above).
- `creative_assets` table + migration.
- `client_safe_visual_url` field.
- AI background layer.
- Multi-variant generation.

### Alternative (smaller) MVP

If even the new route is too much, the smaller version of 4C is:

- Enhance the existing `CreativeBriefPanel` (Phase 4A) to render the
  slides/frames as styled cards (still HTML, not exported to PNG)
  instead of plain markdown.
- No new route, no new template module.

Either path keeps the safety surface the same; the first path is
recommended because it gives a clean reusable template module that
the future export pipe can target.

---

## Cloudflare / hosting feasibility

**Current deployment**: Next.js 15 App Router via OpenNext on
Cloudflare Workers (`yuvo-dashboard`). `wrangler.jsonc` has
`compatibility_flags: ["nodejs_compat", "global_fetch_strictly_public"]`;
no R2, KV, D1, or Durable Object bindings yet (the file notes them as
"deferred").

What runs inside the dashboard Worker today:

| Capability | In Worker? | Notes |
|---|---|---|
| Server-side React (Next server components) | ✅ yes | Phase 4C MVP target |
| Tailwind / inline CSS rendering | ✅ yes | already used |
| `fetch()` to Supabase | ✅ yes | core data path |
| `fetch()` to external HTTP API | ✅ yes | gated by safety rules |
| Puppeteer / Playwright | ❌ no | requires Chromium binary; not available |
| `node-canvas` / `sharp` / native modules | ❌ no | `nodejs_compat` doesn't cover native binaries |
| `resvg-js` / wasm SVG → PNG | ⚠️ partial | wasm modules work; bundle-size + 50ms-CPU caveat |
| Cloudflare Browser Rendering API | ✅ yes | binding-based, beta, paid per second |
| Cloudflare R2 binding | ✅ yes | currently unbound — add later when export lands |

**Conclusion.**

- **Preview can ship now** — server-render the templates in the
  Worker just like every other agency page.
- **PNG export needs an out-of-Worker runtime.** Three viable
  options (in order of recommended first try): local operator
  Puppeteer script (zero deploy surface, mirrors existing
  copy-paste-Seedance pattern); external service; Cloudflare
  Browser Rendering API.
- **R2 binding** will need to be added to `wrangler.jsonc` when
  export ships (with `wrangler secret`-managed credentials for any
  external service). Out of scope for 4C MVP.

---

## Safety + client-boundary rules (carried forward)

Visual asset work must inherit and extend the existing rules:

- **No image API call without explicit operator confirmation.** Same
  shape as the Seedance gate: confirmation phrase + cost estimate.
- **No paid render call without confirmation.** Template renders
  remain free; only paid runtimes (Browser Rendering API, AI image
  gen) require the gate.
- **No publishing.** The dashboard never posts to Instagram /
  Facebook / LinkedIn. (Future publishing phase will need its own
  full safety review.)
- **No client share until `client_safe_visual_url` is prepared.**
  Mirrors `client_safe_copy_preview`. `shared_with_client` flips
  only via an explicit server action.
- **Internal design notes never exposed.** `creative_brief_source`,
  template ids, provider request ids, costs, and brief markdown
  stay operator-only. The scoped `client_creative_assets_v` view
  must NOT project them.
- **`prompt_summary` remains hidden.** Already true via
  `client_content_items_v` (migration 009). The new
  `[creative brief approval]` block in Phase 4C inherits this.
- **Generated assets must not leak provider IDs / costs to client.**
  Same projection rule as videos: `client_content_items_v` projects
  only `client_safe_*` fields.
- **Product / text accuracy verification** is mandatory before any
  AI-generated visual reaches `approved_internal`. A future
  per-asset checkbox + operator note column will enforce this.

---

## Future roadmap

| Phase | Build | Adds |
|---|---|---|
| 4C | HTML/CSS template **previews** | 3 React templates + preview route + internal-approval marker (no schema) |
| 4D | PNG export pipe (local script) | `creative_assets` migration + R2 binding + export endpoint |
| 4E | Client-safe visual preview lifecycle | `client_safe_visual_url` + prepare/share actions + `client_creative_assets_v` |
| 4F | Template library | brand-kit fonts/colours pulled from brand row; multi-template choice |
| 4G | AI **background-layer** opt-in (option D) | gated paid call; operator review; never auto-publish |
| 4H | Cloudflare Browser Rendering API migration | move PNG export inside Cloudflare if volume warrants |
| 4I | Direct social-platform publishing | separate phase with its own full safety review |

Each step keeps the same contract: no paid call without an operator
gate; no client visibility until an operator explicitly prepares +
shares the `client_safe_visual_url`; nothing irreversible runs from
the dashboard without confirmation.
