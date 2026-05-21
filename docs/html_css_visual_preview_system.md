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

## Phase 4E additions (shipped on top of 4C/4D)

These extend the preview system without changing its safety contract.
Nothing here renders pixels off the dashboard; nothing calls a paid
API; nothing writes Supabase outside the already-documented
`[creative brief approval]` block (Phase 4D1).

### Template variants (Phase 4E)

`web/lib/creative/templates.ts` now ships **multiple variants per
mode** (carousel ×3, story ×3, feed_post ×3, static_image ×1,
linkedin_image ×2, reel_thumbnail ×1, video_thumbnail ×1). Each
`CreativeBriefTemplate` entry carries `id`, `label`, `mode`,
`description`, `bestFor`, `aspectRatio`, `defaultTheme`, `exportSize`
(width / height), and `status` (`"active"` | `"planned"`).

For Phase 4E only one variant per mode is `"active"` (the
`*_neutral_v1` one). The remaining variants are `"planned"` —
metadata-only entries so the preview UI can surface the choice today
while the per-variant rendering component lands later. When a planned
variant is selected, the preview falls back to the active default and
the export manifest surfaces a `template "<id>" is registered but not
yet active` blocker.

### Theme presets (Phase 4E)

`web/lib/creative/themes.ts` defines five presets: `neutral`,
`editorial`, `bold`, `soft`, `premium_dark`. Each has a description,
a `surfaceClass`, a `highlightClass`, an `accentClass`, a
`headlineClass`, `textHierarchyNotes`, and a `bestFor` label. Themes
are applied at the shared `PreviewCard` surface (via a
`ThemeIdContext` so per-template code doesn't need to thread the
prop). Resolution precedence: `?theme=<id>` querystring override →
template's `defaultTheme` → `"neutral"`.

### Querystring overrides (Phase 4E)

- `?template=<id>` overrides the recorded template. Invalid id → null
  + an "Unknown template id" warning chip; falls back to the
  recorded id (or default).
- `?theme=<id>` overrides the resolved theme. Invalid id → null +
  an "Unknown theme id" warning chip; falls back to template default.
- `?slide=N` / `?frame=N` (Phase 4D3) still work for focused
  carousel / story views.
- Nothing in this flow writes to Supabase.

### Export manifest (Phase 4E)

`web/lib/creative/export-manifest.ts` exports `buildExportManifest`
and `renderExportManifestText`. The manifest is a **planning-only**
projection of the preview into the fields a future local export
script needs: `contentItemId`, `mode`, `templateId`, `templateLabel`,
`templateStatus`, `themeId`, `recommendedWidth/Height`,
`aspectRatio`, `slideCount` / `frameCount`, `exportFormat = "png"`,
`assetNamingSuggestion[]`, `exportReadiness` (`"ready"` |
`"not_ready"`), `blockers[]`, `notes[]`.

`exportReadiness === "ready"` iff there are zero blockers. Blockers
include: unknown mode, no template id, planned-only template, zero
slides/frames parsed, no internal approval. The preview page shows
the manifest in a side panel and a "Copy export brief" button copies
the plaintext rendering to the clipboard (clipboard-only; no server
action, no fetch).

### Local export script stub (Phase 4E)

`scripts/export_visual_preview_stub.py` is a documented placeholder
that prints the intended future workflow and exits 0. It imports
nothing browser-related, talks to no service, and requires no
credentials. The real Puppeteer / Playwright script lands in
Phase 4D.

### Internal QA checklist (Phase 4E)

`PreviewQAChecklist` (in
`web/components/creative-preview/preview-side-panels.tsx`) renders
eight read-only checklist items: text readable, CTA visible, layout
fits format, no forbidden text, no internal notes visible, brand
tone OK, claim safe, ready for export later. Pure display — no
persistence in 4E. A future phase may persist the checks against the
`[creative brief approval]` block.

### Preview shell upgrades (Phase 4E)

- New chip: `theme: <id>` next to the template chip.
- Disabled "Export PNG (Phase 4D — coming soon)" placeholder (4D4)
  is unchanged; the export-manifest panel now provides the
  "ready to export" signal.
- New two-column layout: preview + manifest on the left, template
  options + theme options + QA checklist on the right.
- New `WhatHappensNextPanel` summarising 4D / 4E / 4F roadmap.
- Graceful warning chips for invalid `?template=` / `?theme=` ids
  (no thrown errors).

### Limitations and forward-compatibility

- Variants currently route to the active default's render
  component. The per-variant styling lands when each `_v1` planned
  variant gets its own component / theme-class set.
- The export manifest is the contract the future export script
  (Phase 4D) and the future client-share lifecycle (Phase 4E
  migration — see `docs/client_safe_visual_preview_plan.md`) will
  consume. Changing field shapes after 4D ships will need a deprecation
  pass.

## Phase 4F additions (shipped on top of 4E)

These extend the preview system without changing its safety contract.
Nothing here renders pixels off the dashboard, calls a paid API, or
writes Supabase outside the already-documented provenance blocks.

### Persisted internal QA checklist (Phase 4F)

The Phase 4E `PreviewQAChecklist` (read-only display) has been
retired. The preview page now mounts a single, persisted
`CreativePreviewQAPanel` (client component) sourced from the same
`QA_ITEMS` set. Operators flip each item pass / fail and click
**Save QA**. The action `saveCreativePreviewQAAction` writes a
`[creative preview QA]` block to `content_items.prompt_summary`:

```
[creative preview QA]
qa_status: passed | needs_attention
qa_checked_at: <ISO>
qa_items: text_readable=pass,cta_visible=pass,…
```

Idempotent strip-then-append (mirrors `[copy approval]` from Phase
2F). `qa_status` is `passed` iff every saved item is `pass`;
otherwise `needs_attention`. The companion
`resetCreativePreviewQAAction` strips the block. The QA block lives
in `prompt_summary`, which `client_content_items_v` (migration 009)
does NOT project — structurally invisible to the client.

The action whitelists both keys and values (item ids from a fixed
set, values must be exactly `pass` or `fail`) so no operator-supplied
free text reaches `prompt_summary` through this path.

### Approval polish (Phase 4F)

`CreativeBriefApprovalPanel` now accepts the manifest's
`exportReadiness` + `blockers[]` and renders a compact `ManifestSummary`
beside the approve button. The blockers list is purely informational —
approval is NOT blocked by them — but the operator sees what still
needs resolving before the future export script will accept the brief.

### Local export command handoff (Phase 4F)

`CopyExportCommandButton` joins the existing `CopyExportBriefButton`
on the preview page. It copies a future-safe local export command to
the clipboard, referencing `scripts/export_visual_preview_stub.py`,
the preview URL (with current `?template=` / `?theme=` querystrings
preserved), and the content item id. **The command does not execute
from the website.** Both copy buttons respect the same
`disabledReason` derived from `manifest.exportReadiness !== "ready"`,
so neither writes to the clipboard when blockers remain.

### Migration 011 (PROPOSAL ONLY)

`supabase/migrations/011_client_safe_visual_preview.sql` documents
the Phase 4E/4F schema change that will eventually land
(`client_safe_visual_url`, `client_safe_visual_thumbnail_url`,
`visual_preview_status`). The file is idempotent (`add column if
not exists` + `do $$ if not exists $$` for the CHECK constraint) but
is NOT applied in this build chunk. The file's header carries an
explicit "DO NOT APPLY — proposal only" banner.

The application code does NOT read or write these proposed columns
in Phase 4F. They become live only after both (a) migration 011 is
applied AND (b) the proposed Phase 4E server actions
(`prepareClientVisualPreviewAction` /
`shareVisualPreviewWithClientAction`) ship — neither lands in 4F.

## Phase 4G additions

Phase 4G adds the **export command contract + dry-run CLI scaffold**
on top of 4F. No execution surface — the dashboard never spawns the
command, and the local stub script refuses to render pixels.

### Export command contract (shared)

`web/lib/creative/export-command.ts` — pure TS module. Exports
`VisualExportCommandInput`, `VisualExportCommand`, plus three pure
builders: `buildVisualExportCommand`, `buildVisualExportArgs`,
`buildVisualExportFilenameSuggestion`. The dashboard's
`CopyExportCommandButton`, the local stub script, and the
script-level tests all read the same contract — adding a real
export later only needs the stub flipped to a real implementation.

### Copy local export command button (refactor)

`web/components/creative-preview/copy-export-command-button.tsx`
now reads the command via `buildVisualExportCommand(…)` and accepts
`mode`, `width`, `height`, `format`, `outputDir` props. The button
remains disabled when `manifest.exportReadiness !== "ready"`. A
small disclosure shows the planned argv length and filename
suggestion + a full command preview before the operator copies.

### Dry-run CLI stub

`scripts/export_visual_preview_stub.py` is a real `argparse` CLI
scaffold with strict validation (see
`docs/visual_preview_export_readiness.md` for the full list).
Pinned exit codes: `0` dry-run ok, `1` validation failed, `2`
`--execute` refused. Twenty-three unit tests in
`scripts/test_export_visual_preview_stub.py` cover the happy path,
each validation failure mode, the `--execute` refusal, and pin the
stub's import graph (no puppeteer / playwright / requests / httpx).

### Preview-page UI changes

- New **Export readiness panel** consolidates ready / blocked /
  unknown into a single chip + plain-English explainer + three-line
  safety reminders ("Not executable from website / dry-run only /
  no files created / no upload / no client share / no paid API").
- `previewUrl` for the local command is now built from the current
  `?template=` / `?theme=` querystrings so the operator can paste
  the command without re-tabbing through the page.
- The disabled `Export PNG (Phase 4D — coming soon)` placeholder
  on the preview shell is unchanged.

### Creative-briefs list readiness chip

`/agency/creative-briefs` rows now carry a single readiness chip
per item: `needs brief` → `needs approval` → `approved · export
pending` → `export ready later`. Derived in-memory from the
existing `listCreativeBriefQueueForWorkspace` payload — no extra DB
read, no extra write.

### Safety boundary (unchanged from 4F)

Phase 4G ships zero new write surfaces and zero new external calls.
The new module / button / panel / chip are pure render code; the
new stub + tests live entirely under `scripts/`. The dashboard's
existing safety contract is preserved.

## Phase 4H additions

Phase 4H restructures the dry-run scaffold (in
`scripts/export_visual_preview_stub.py`) so the future real
exporter can plug in cleanly. The dashboard still **only copies**
commands; the script still **refuses** `--execute`.

### Dataclass-based scaffold

`ExportRequest` (validated input) and `ExportResult` (`exit_code +
manifest + errors`) are the public seam. Five pure entry-points:
`parse_args`, `validate_request`, `build_plan`, `run_dry_run`,
`run_execute_refused`, plus the documented placeholder
`future_export_with_browser` (always exit `3`, never imported).

### Manifest schema bump (`v0` → `v1`)

Adds `planned_output_path`, `html_snapshot_path`, `phase`,
`session_requirements` (recommended workflow for the future real
run), and three more `safety.*` flags
(`creates_generated_assets_row / uploads / publishes /
shares_with_client` — all `false`).

### New CLI flags

- `--html-snapshot-path PATH` — optional `.html` / `.htm` target
  the future exporter would write. String-validated; no file
  created in 4H.
- `--json` — emits a single machine-readable JSON object on stdout
  for future automation. Skips the prose footer.

### Stronger URL allowlist

Subdomain-aware: `*.workers.dev`, `*.pages.dev`, `*.yuvo.studio`,
`*.yuvostudio.com`. Dashboard-shaped URLs that point at a non-preview
path are rejected. External hosts are rejected unconditionally.

### TS command-builder updates

`web/lib/creative/export-command.ts`:
- `VisualExportCommandInput` gains optional `htmlSnapshotPath` +
  `emitJson` fields.
- `VisualExportCommand` carries a new `plannedOutputPath` string
  (deterministic `<outputDir>/<filenameSuggestion>`).
- New helper `buildVisualExportPlannedOutputPath()`.
- New helper `sanitizeHtmlSnapshotPath()` (mirrors the stub's
  string-only validation).
- `buildVisualExportArgs` appends `--html-snapshot-path …` and
  `--json` when supplied; everything else is unchanged.

`CopyExportCommandButton` now shows the planned on-disk path in the
disclosure summary instead of the bare filename suggestion.

### Tests

`scripts/test_export_visual_preview_stub.py` now ships **50 tests**
(23 carried + 27 new). Coverage is documented in detail under
`docs/visual_preview_export_readiness.md`.

### Safety boundary (unchanged from 4G)

Phase 4H ships zero new write surfaces, zero new external calls,
zero new browser-automation imports, zero new file-system writes,
and zero new env reads. The new dataclasses + flags are pure
metadata. The existing safety contract is preserved.

## Phase 5A additions (preview-side)

Each `PreviewCard` rendered in the grid now carries a stable
`data-export-slide` (carousel + single-card modes) or
`data-export-frame` (story) attribute. `CreativePreviewShell`'s
inner wrapper carries `data-export-root` + `data-export-mode`.
These markers are pure DOM additions — no visual change, no
client-facing exposure — and let the future local exporter (Phase
5A-followup + Phase 5B) locate the screenshot target without
relying on Tailwind class names.

Note that the **focused** carousel slide / story frame (the
expanded inline view) deliberately does NOT carry an export
attribute so a Playwright/Puppeteer locator on
`[data-export-slide="N"]` always matches exactly one element per
preview. The grid cards remain the canonical screenshot targets.

See `docs/visual_preview_export_readiness.md` for the full Phase 5A
section — runtime detection, confirmation phrase, the double-gate
permit list, manifest changes, and the safety boundaries.
