# HTML/CSS Social Visual Preview System (Phase 4C)

Status: **shipped (code-only, not deployed yet)** — parser, preview
types, render builder, 5 template components, preview route, list
link, doc. Strictly **read-only with respect to Supabase**, the
**client portal**, and **all external services**. No PNG export, no
image API, no publishing.

## Purpose

Phase 4B chose HTML/CSS templates as the first path from a creative
brief to a final social asset (vs SVG/canvas, AI image gen, hybrid).
Phase 4C ships the **internal preview half** of that plan: every
content item that has a `[creative brief]` block can now be rendered
as a per-format dashboard preview at:

```
/agency/creative-briefs/[contentItemId]/preview
```

Operators get a pixel-feel sense of the planned carousel slides,
story frames, feed/static post, LinkedIn companion image, or
reel/video cover frame — without any image being generated, exported,
or shared.

## Supported preview modes

Resolved from the parsed brief's `mode` field via a deterministic map:

| Brief mode (Phase 4A) | Preview mode (Phase 4C) | Template component |
|---|---|---|
| `carousel` | `carousel` | `CarouselPreviewTemplate` (5× 4:5 grid) |
| `story` | `story` | `StoryPreviewTemplate` (3× 9:16 grid) |
| `feed_post` | `feed_post` | `FeedPostPreviewTemplate` (single 4:5) |
| `static_image` | `static_image` | `FeedPostPreviewTemplate` (single 4:5) |
| `linkedin_text` | `linkedin_image` | `LinkedInPreviewTemplate` (1:1 + post hook) |
| `reel_support` | `reel_thumbnail` | `ThumbnailPreviewTemplate` (9:16 cover + beats) |
| `video_visual_support` | `video_thumbnail` | `ThumbnailPreviewTemplate` (9:16 cover + hook frame) |
| `copy_only` | `unknown` | placeholder card |

All templates share a `CreativePreviewShell` wrapper that renders:
- Chips: mode, format, channel, brand, niche, plus a permanent
  `internal preview only` warn badge
- Title + subtitle + CTA
- The per-mode template
- A footnote of operator warnings (the brief's `Do NOT include` list
  plus the universal "Internal preview only — not exported, not
  shared with the client.")

The shell + every template renders as **pure HTML/CSS**: black/white
neutral base, subtle radial highlight via inline CSS, no external
images, no fonts beyond the existing dashboard stack. No client-facing
copy strings.

## How `[creative brief]` is parsed

`web/lib/creative/creative-brief-parser.ts` exports
`parseCreativeBriefBlock(promptSummary)`. Two layers:

1. **Structured key block** (high reliability — written by code in
   `web/lib/actions/social-creative-brief.ts`):
   ```
   creative_brief_status: drafted
   creative_brief_source:  social_creative_brief_agent
   creative_brief_format:  <ContentFormat>
   creative_brief_channel: <ContentChannel>
   creative_brief_mode:    <CreativeBriefMode>
   creative_brief_created_at: <ISO timestamp>
   creative_brief_operator_note: <optional>
   ```
2. **Markdown body** (best-effort — produced by
   `renderCreativeBriefMarkdown`). The parser walks per-section
   headings (`## Creative direction`, `## Visual concept`,
   `## Layout & assets`, `## Slides`, `## Story frames`,
   `## Main visual`, `## LinkedIn post`, `## Visual support`,
   `## Brand elements`, `## Call to action`, `## Do NOT include`,
   `## Shot / design notes`) and extracts text per section.

**Limitations** (documented and tolerated):
- Markdown parsing is regex-based, not a real markdown AST. If a
  future render changes a heading or label, the corresponding field
  becomes null and the template uses a fallback (e.g. carousel with
  no parsed slides synthesises one slide from `creativeDirection` +
  `visualConcept`).
- The parser never throws. Bad / missing input returns `null` from
  `parseCreativeBriefBlock` and `"none"` from `getCreativeBriefStatus`.
- If the brief was hand-edited and the `creative_brief_status` key is
  missing, the parser returns `null` (caller shows the empty state).
- The raw markdown body is preserved on `rawMarkdown` for templates
  that prefer verbatim rendering in the future.

**Going forward**: if hand-edits become common or the markdown
structure starts drifting, the cleaner path is to expand the
structured-key block to carry the full brief JSON (e.g.
`creative_brief_json:` followed by a base64-encoded payload) and
rely on the markdown only for the human-readable display. The
parser's structured-key path is already the load-bearing one;
swapping in a JSON key is a minor change. Phase 4D is the natural
place to land this if needed.

## Preview-only safety boundary

The preview path is strictly read-only end-to-end:

- **Database**: only `.select()` / `.maybeSingle()` reads
  (`getContentItemById`, `getCampaignById`, `getBrandById`). No
  `.insert` / `.update` / `.delete` / `.upsert` / `.rpc` anywhere in
  the Phase 4C files. Live test confirmed all 10 tables
  byte-identical pre/post.
- **External services**: no `fetch()` to any provider. No OpenAI /
  Anthropic / DALL·E / Imagen / Stable Diffusion / Seedance /
  Enhancor / Audio Fixer reference. No `nodemailer` / `sendgrid` /
  `.publish(`.
- **Client portal**: the preview route lives under `/agency/*` and is
  persona-gated to operators (mirrors the existing agency layout
  guard). No `/client/*` page imports any Phase 4C file. The brief
  itself lives in `content_items.prompt_summary`, which the client
  view (`client_content_items_v`, migration 009) does NOT project.
- **Asset storage**: no `generated_assets` row is inserted; no R2
  upload; no binary output.
- **Operator UI**: zero "Run" / "Submit" / "Publish" / "Share" /
  "Export" buttons on the preview route. The list page's
  "Preview visuals →" link is a pure `<Link>` (navigation only).

## Why there is no PNG export yet

Per the Phase 4B feasibility analysis
(`docs/visual_asset_generation_plan.md`):

- ✅ Server-side React rendering runs natively in the Cloudflare
  Worker — preview is shipping now.
- ❌ Puppeteer / Playwright / Chromium does NOT run in a Worker
  (`nodejs_compat` does not cover native binaries).
- ❌ `node-canvas` / `sharp` do not run in a Worker.
- ⚠️ wasm-based SVG → PNG rasterisers (e.g. `resvg-js`) can work
  in a Worker, but only for SVG-only templates — kills HTML/CSS.

PNG export needs an **out-of-Worker runtime**, which Phase 4D will
choose between:
- (B2-c) **Local operator Puppeteer script** that hits the dashboard
  preview URL → uploads to R2 (recommended first; mirrors the
  existing "operator runs the paid step locally" pattern, e.g.
  `scripts/run_audio_fixer_job.py`).
- (B2-a) Cloudflare Browser Rendering API (native, beta, paid per
  second).
- (B2-b) External headless service (browserless.io / your own VPS).

The MVP intentionally ships preview-only to **decouple** the choice
of export runtime from the immediate operator value.

## Cloudflare / hosting feasibility

| Capability | In Worker? | Used by Phase 4C? |
|---|---|---|
| Server-side React (Next server components) | ✅ yes | ✅ yes (preview templates) |
| Tailwind / inline CSS | ✅ yes | ✅ yes |
| `fetch()` to Supabase | ✅ yes | ✅ yes (read-only) |
| Puppeteer / Playwright / Chromium | ❌ no | not used |
| `node-canvas` / `sharp` / native modules | ❌ no | not used |
| `resvg-js` / wasm SVG → PNG | ⚠️ partial | not used (no SVG path) |
| R2 binding | ✅ (when added) | not used (no asset storage in 4C) |
| Cloudflare Browser Rendering API | ✅ (beta, paid) | not used |

## Phase 4D export plan (preview)

Phase 4D will add the **PNG/JPG export pipe** on top of the existing
preview. Sketch (full plan stays in
`docs/visual_asset_generation_plan.md`):

1. Add a `creative_assets` table (proposed in Phase 4B, **not yet
   applied**) so the export can be persisted with its full provenance
   (which brief, which template, which slide/frame, which variant).
2. Add an R2 binding to `wrangler.jsonc` and to `wrangler secret put`
   the credentials for the chosen runtime.
3. Add an operator-only server action `exportVisualPreviewAction(
   contentItemId, sliceIndex?)` that:
   - Requires an explicit confirmation phrase if the runtime is paid.
   - Estimates the export cost up front.
   - Calls the chosen runtime (local script / Browser Rendering API
     / external service).
   - Writes one `creative_assets` row per slide/frame.
   - NEVER auto-publishes. NEVER auto-shares with the client.

## Future client-safe visual preview lifecycle

Phase 4E will add a `content_items.client_safe_visual_url` (or move
to the `creative_assets.client_safe_visual_url` column) and the
prepare/share lifecycle, mirroring `client_safe_copy_preview`
(Phase 2G):

```
[creative brief]
   → DRAFT
   → RENDERED_INTERNAL          (Phase 4C — this phase)
   → APPROVED_INTERNAL          (next: 4D1 approval block)
   → CLIENT_PREVIEW_PREPARED    (Phase 4E)
   → SHARED_WITH_CLIENT         (Phase 4E — explicit operator action)
   → APPROVED_BY_CLIENT | CHANGES_REQUESTED_BY_CLIENT
```

Until then, nothing in the visual pipeline is visible to clients.

## File map

| File | Purpose |
|---|---|
| `web/lib/creative/creative-brief-parser.ts` | parse `[creative brief]` block |
| `web/lib/creative/visual-preview-types.ts` | shared TS types for render input |
| `web/lib/creative/build-visual-preview.ts` | deterministic builder (brief → render input) |
| `web/components/creative-preview/creative-preview-shell.tsx` | shared chrome + `PreviewCard` |
| `web/components/creative-preview/carousel-preview-template.tsx` | 5× 4:5 carousel |
| `web/components/creative-preview/story-preview-template.tsx` | 3× 9:16 story |
| `web/components/creative-preview/feed-post-preview-template.tsx` | single 4:5 feed / static |
| `web/components/creative-preview/linkedin-preview-template.tsx` | 1:1 LinkedIn companion + hook |
| `web/components/creative-preview/thumbnail-preview-template.tsx` | 9:16 reel / video cover |
| `web/app/agency/creative-briefs/[contentItemId]/preview/page.tsx` | new operator-only route |
| `web/app/agency/creative-briefs/page.tsx` | "Preview visuals →" link added |
| `docs/html_css_visual_preview_system.md` | this doc |
