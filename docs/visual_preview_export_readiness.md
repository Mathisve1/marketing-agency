# Visual Preview Export Readiness (Phase 4E)

Status: **planning + UX scaffold only**. No pixels exported. No paid
API. No client share. No Supabase writes outside the documented
`[creative brief approval]` block. The dashboard surfaces the
operator-facing pieces a real export pipe (Phase 4D) will need —
nothing executes yet.

## What "export readiness" means here

A creative brief lives in `content_items.prompt_summary` (Phase 4A).
The dashboard previews it as a per-format HTML/CSS render (Phase 4C).
**"Export readiness"** is the dashboard's read-only signal that says:
*if a real export ran right now, would it succeed?*

The signal lives in the **export manifest** — a deterministic
projection of the preview into the fields a future script needs. The
operator sees the manifest, fixes any blockers, and copies the
plaintext brief into the future local export script (Phase 4D, real;
Phase 4E, stubbed).

## Template variants (Phase 4E)

See `web/lib/creative/templates.ts` and the bundled `*_neutral_v1`
defaults plus the planned variants:

| Mode | Active | Planned |
|---|---|---|
| `carousel` | `carousel_neutral_v1` | `carousel_editorial_v1`, `carousel_bold_offer_v1` |
| `story` | `story_neutral_v1` | `story_minimal_v1`, `story_promo_v1` |
| `feed_post` | `feed_post_neutral_v1` | `feed_post_editorial_v1`, `feed_post_offer_v1` |
| `static_image` | `static_image_neutral_v1` | (none) |
| `linkedin_image` | `linkedin_neutral_v1` | `linkedin_thought_leader_v1` |
| `reel_thumbnail` | `reel_thumbnail_neutral_v1` | (none) |
| `video_thumbnail` | `video_thumbnail_neutral_v1` | (none) |

Each entry carries `aspectRatio`, `defaultTheme`, and `exportSize`.
**Planned variants render via the active default** — they exist in
the registry so the preview UI can offer them today; the actual
visual differentiation lands when each variant gets its own
component / theme-class set.

## Theme presets (Phase 4E)

See `web/lib/creative/themes.ts`:

| id | name | best for |
|---|---|---|
| `neutral` | Neutral | Default; trust-building / ingredient stories |
| `editorial` | Editorial | Brand stories / founder POV |
| `bold` | Bold | Promos / launches / offers |
| `soft` | Soft | Quote frames / minimal stories |
| `premium_dark` | Premium dark | LinkedIn thought-leader / luxury moments |

Themes are applied at the shared `PreviewCard` surface and only
control visual classes. Resolution precedence: `?theme=…` override →
template's `defaultTheme` → `"neutral"`. Pure preview-time choice;
not persisted.

## Export manifest

`buildExportManifest({ preview, approvedInternal })` →
`ExportManifest`. Fields:

```ts
{
  contentItemId, mode, templateId, templateLabel, templateStatus,
  themeId, recommendedWidth, recommendedHeight, aspectRatio,
  slideCount, frameCount, exportFormat: "png",
  assetNamingSuggestion[],  // deterministic per-slide / per-frame names
  exportReadiness: "ready" | "not_ready",
  blockers[],               // human-readable reasons it isn't ready
  notes[],
}
```

`exportReadiness === "ready"` iff there are **zero blockers**. The
preview page renders the manifest in a side panel and surfaces an
operator-readable plaintext brief via "Copy export brief" (clipboard
only; no server action).

### Blockers (current set)

- `mode === "unknown"` — re-draft the brief so a recognised format
  lands.
- No template id resolved for the mode.
- Selected template is `"planned"` (renders via the active default).
- Carousel with zero parsed slides.
- Story with zero parsed frames.
- No `[creative brief approval]` block on the content item (Phase 4D1).

Approval is **always** a blocker for readiness — the operator must
explicitly sign off internally before the export pipe will run, even
for free template renders.

## QA checklist (Phase 4E → 4F)

Eight items:

- Text is readable at preview size
- CTA is visible / present
- Layout fits the format (4:5 / 9:16 / 1:1)
- No forbidden text (medical claims, competitor brands)
- No internal / operator notes visible on the surface
- Brand tone matches the brand guide
- Product / offer claim is safe and verifiable
- Ready for export later (Phase 4D pipe)

**Phase 4F update — now persisted.** The Phase 4E read-only
`PreviewQAChecklist` has been retired. The preview page mounts the
client-side `CreativePreviewQAPanel` (sourced from the same
`QA_ITEMS` constant in `web/lib/creative/qa-items.ts`). Pass/fail
decisions are saved by `saveCreativePreviewQAAction` into a
`[creative preview QA]` block in `content_items.prompt_summary`:

```
[creative preview QA]
qa_status: passed | needs_attention
qa_checked_at: <ISO>
qa_items: text_readable=pass,cta_visible=pass,…
```

Idempotent strip-then-append. `qa_status === "passed"` iff every
saved item is `pass`. The companion `resetCreativePreviewQAAction`
strips the block. The QA block lives in `prompt_summary` (which
`client_content_items_v` does NOT project) → structurally invisible
to the client portal.

The action whitelists both keys (fixed `ALLOWED_ITEM_IDS` set) and
values (`pass` / `fail` only), so no operator-supplied free text
can reach `prompt_summary` through this path.

## Phase 4G — local export pipeline preparation

Phase 4G turns the Phase 4F clipboard handoff into a deterministic
command contract + CLI scaffold, **still without executing any
export**. The dashboard never spawns the command; the local stub
script refuses to render pixels. The contract is:

```ts
// web/lib/creative/export-command.ts
buildVisualExportCommand({
  contentItemId, previewUrl, mode, templateId, themeId,
  width, height, format = "png",
  outputDir, dryRun = true,
  slideNumber?, frameNumber?,
}) -> { scriptPath, argv, command, filenameSuggestion,
        requiresOperatorExecution: true }
```

- **Pure** — no `fetch`, no `process`, no DB, no clipboard call.
- **Deterministic** — `argv` is identical to what the stub script
  parses via `argparse`; tests in
  `scripts/test_export_visual_preview_stub.py` cover the contract.
- **Sanitized** — filenames are slug-safe, output dirs reject
  `..`/`/etc`/`/root`, theme/template ids are lowercase-ascii only.
- **Future-safe** — the bash command header carries an explicit
  "does NOT execute from the dashboard" warning + a "dry-run by
  default" reminder. The stub refuses `--execute`.

### Dry-run CLI stub usage

```
py -3.11 scripts/export_visual_preview_stub.py \
  --content-item-id <uuid> \
  --preview-url "/agency/creative-briefs/<uuid>/preview?template=...&theme=..." \
  --mode feed_post \
  --theme-id neutral \
  --format png \
  --output-dir ./exports
```

Exit codes (pinned for CI and the dashboard):
- `0` — dry-run validated; planned manifest JSON printed to stdout.
- `1` — validation failed (invalid UUID, URL, dimensions, format,
  theme id, output-dir traversal, …).
- `2` — `--execute` was passed; the stub refuses until a real
  export implementation lands (Phase 4H+).

The stub validates:
- `--content-item-id` must be a UUID.
- `--preview-url` must be a relative dashboard path
  (`/agency/creative-briefs/<id>/preview…`) **or** a localhost /
  dashboard origin URL (`workers.dev` / `pages.dev` / `yuvo.studio`).
- `--mode` ∈ `{carousel, story, feed_post, static_image,
  linkedin_image, reel_thumbnail, video_thumbnail, unknown}`.
- `--format` ∈ `{png, jpg}`.
- `--width / --height / --slide-number / --frame-number` if set
  must be positive integers. `--slide-number` and `--frame-number`
  are mutually exclusive.
- `--theme-id / --template-id` must match `^[a-z0-9_]+$`.
- `--output-dir` must not contain `..` and must not start with
  `/etc` or `/root`.

### Why the stub still does not execute

- **No browser automation imports.** A pytest test pins the stub's
  import graph: importing the stub must NOT pull in `puppeteer`,
  `pyppeteer`, `playwright`, `playwright.sync_api`,
  `playwright.async_api`, `selenium`, `requests`, `httpx`, or
  `aiohttp`. The test fails if any of these leaks in.
- **No network.** No `requests`, `httpx`, or `urllib.request` call
  is made.
- **No file writes.** A pytest test sets `--output-dir` to a
  `tmp_path` and asserts the directory is empty pre and post.
- **No env reads.** A pytest test sets a sentinel env var and
  asserts it does not appear anywhere in stdout / stderr.

### Real export implementation (Phase 4H+, not in scope yet)

The actual PNG/JPG export will plug in behind the same argparse
contract. Two paths under consideration (mirrors
`docs/visual_asset_generation_plan.md`):

1. **Local operator Playwright/Puppeteer script** (recommended for
   the first cut — mirrors `scripts/run_audio_fixer_job.py`). The
   operator authenticates against the dashboard locally; the script
   opens the preview URL with the operator's session cookie, waits
   for fonts + radial highlight to render, then full-page-screenshots
   at the template's `exportSize`. Output goes to `--output-dir`
   using the deterministic filename suggestion.
2. **Cloudflare Browser Rendering API** (native, beta, paid per
   second). Sits behind the same Seedance-style confirmation phrase
   gate. Would let the dashboard offer "Render preview" buttons in
   a later phase — still behind an explicit `confirmationToken`,
   never auto-run.

In both paths:
- Output naming follows `buildVisualExportFilenameSuggestion`.
- Optional Cloudflare R2 upload is a separate explicit operator step.
- `client_safe_visual_url` flips only via a separate gated server
  action (Phase 4E migration `011_client_safe_visual_preview.sql`,
  proposed but NOT applied in any current phase).

### Why no Cloudflare Worker export

Verified again in Phase 4G: Cloudflare Workers cannot run Puppeteer,
Playwright, native Chromium, `node-canvas`, or `sharp` (see
`docs/visual_asset_generation_plan.md` §7 +
`docs/html_css_visual_preview_system.md`). The Worker can only run
the **server-side React preview** that already ships in Phase 4C —
the export must live out-of-Worker. Phase 4G prepares for that
without changing where the Worker stops.

## Phase 4H — local exporter scaffold (still dry-run only)

Phase 4H restructures the Phase 4G CLI into a small dataclass-based
scaffold so the future real implementation has a stable seam to
plug into. The dashboard still only copies commands; the script
still refuses `--execute`. Manifest schema bumps to `v1`.

### New shape

```python
# scripts/export_visual_preview_stub.py
@dataclass(frozen=True)
class ExportRequest:    # validated, normalised CLI input
    ...
@dataclass(frozen=True)
class ExportResult:     # exit_code + manifest + errors
    ...

parse_args(argv)            # argparse Namespace
validate_request(args)      # -> (ExportRequest | None, errors[])
build_plan(req)             # pure manifest dict
run_dry_run(req)            # exit 0
run_execute_refused(req)    # exit 2
future_export_with_browser(req)
                            # placeholder; always exit 3
```

The future real exporter (Phase 4I+) lands behind
`future_export_with_browser` — the dashboard / argparse contract /
manifest schema do not change.

### New manifest fields

| Field | What it means |
|---|---|
| `schema: "yuvo.studio/visual_export_manifest/v1"` | bumped from `v0` |
| `phase: "4h_local_exporter_scaffold"` | self-identification |
| `planned_output_path` | deterministic `<output_dir>/<filename_suggestion>` |
| `html_snapshot_path` | operator-supplied target for the future HTML snapshot mode (string-validated only; no file written) |
| `session_requirements.needs_authenticated_browser_session: true` | the real exporter will need a logged-in dashboard session |
| `session_requirements.production_cookies_handled_by_this_script: false` | the script never touches the operator's browser cookies |
| `session_requirements.recommended_workflow` | step-by-step explainer surfaced inside the manifest |
| `safety.creates_generated_assets_row: false` | pinned alongside the existing safety flags |
| `safety.uploads / publishes / shares_with_client: false` | pinned |

### New CLI flags

| Flag | Default | Behaviour |
|---|---|---|
| `--html-snapshot-path PATH` | `null` | Optional `.html` / `.htm` path. String-validated only; no file is written. |
| `--json` | `false` | Emit a single machine-readable JSON object `{exit_code, manifest, errors}` on stdout. Drops the prose footer. Useful for future automation that consumes the CLI. |

### Stronger URL allowlist

The host regex now explicitly accepts multi-label subdomains under:

- `*.workers.dev` (covers `yuvo-dashboard.<account>.workers.dev`)
- `*.pages.dev`
- `*.yuvo.studio`
- `*.yuvostudio.com`

A dashboard-shaped URL pointing at any other path (e.g. `/api/...`,
`/client/...`) is rejected with exit 1. External hosts are rejected
unconditionally.

### Exit codes

- `0` — dry-run validated.
- `1` — validation failed.
- `2` — `--execute` refused (Phase 4H).
- `3` — `future_export_with_browser` reached (Phase 4I+ placeholder).

### Tests

`scripts/test_export_visual_preview_stub.py` now ships **50 tests**:

- 23 Phase 4G tests (carried forward unchanged in spirit; updated to
  pin schema `v1` + `phase_4h_local_exporter_scaffold`).
- 27 new Phase 4H tests:
  - `planned_output_path` present in manifest (+ honours `--output-dir`)
  - `--json` mode produces a single valid JSON object
  - `--json` skips the prose footer
  - `--json` works on validation-failure paths too
  - `*.workers.dev` / `*.pages.dev` hosts accepted
  - external host rejected
  - dashboard-shaped URL with a non-preview path rejected
  - `--html-snapshot-path` accepted, validated extension, traversal
    rejected, no file ever created
  - `ExportRequest` / `ExportResult` shape pinned
  - `build_plan` is pure (deterministic)
  - `future_export_with_browser` always exits 3
  - forbidden-import sweep covers `subprocess` (additional to the
    earlier list)
  - dedicated test: `urllib.request` must not appear in the stub's
    import graph (purge + reimport)

### Why this still doesn't execute

Phase 4H deliberately keeps every guardrail Phase 4G shipped:

- no browser-automation import
- no network call
- no file write
- no env read
- no subprocess spawn
- `--execute` refused
- no Supabase write of any kind
- no `generated_assets` row inserted
- no upload
- no client share
- no email / publishing

`future_export_with_browser` is a documented placeholder that
returns exit code 3 — never imported by anything in the dashboard,
and never spawned by any server action.

### Future Phase 4I — real local browser export

Plug-in point:

```python
result = future_export_with_browser(req)
```

The real implementation will:

1. Confirm the operator has a logged-in dashboard session (the
   stub will print this requirement up front; `session_requirements`
   on the manifest carries the recommended workflow).
2. Use a vendored or locally-installed browser-automation library
   (`playwright.sync_api` or `puppeteer-python`) to open
   `req.preview_url` with the operator's session cookie.
3. Wait for fonts + radial highlight to fully render.
4. Screenshot at `req.width × req.height` (the template's
   `exportSize`).
5. Save PNG/JPG to `req.output_dir / filename_suggestion(req)`
   (= `manifest.planned_output_path`).
6. Optionally write `req.html_snapshot_path` for archival.
7. NEVER auto-upload, NEVER auto-share, NEVER auto-publish.

Cloudflare R2 upload + `client_safe_visual_url` flip remain a
separate gated step in Phase 4I+.

## Why export remains disabled

Phase 4E ships the **operator-facing scaffold** for export but
deliberately stops short of producing pixels. Three reasons:

1. **Cloudflare Workers** can't run Puppeteer / Chromium / native
   image-processing modules (see
   `docs/visual_asset_generation_plan.md` §7). The real export
   needs an out-of-Worker runtime.
2. **Safety**: a paid runtime (Cloudflare Browser Rendering API,
   external service) must sit behind the same gate as Seedance
   (confirmation phrase + cost estimate). That gate is a Phase 4D
   build.
3. **Asset metadata**: the future `creative_assets` table
   (proposed in `docs/visual_asset_generation_plan.md` and
   `docs/client_safe_visual_preview_plan.md`) is the right home for
   the exported PNG URLs + their client-share lifecycle. Migration
   not applied yet.

The disabled `Export PNG (Phase 4D — coming soon)` button on the
preview shell carries this messaging in its tooltip.

## Future local export script (Phase 4D)

Stub today: `scripts/export_visual_preview_stub.py`. Real script
(Phase 4D) will:

1. Read the export manifest (operator pastes it or the script fetches
   the dashboard manifest endpoint).
2. Require an explicit confirmation phrase to run.
3. Open the dashboard preview URL in a headless Chromium with the
   operator's logged-in session cookie.
4. Screenshot at the template's `exportSize`.
5. Save PNGs using the manifest's `assetNamingSuggestion[]`.
6. **NEVER** auto-upload, auto-publish, or auto-share.

## Future storage / client-share lifecycle (Phase 4E migration)

See `docs/client_safe_visual_preview_plan.md` for the proposed
schema. Summary:

- **Option A** — extend `content_items` with
  `client_safe_visual_url` + `shared_with_visual_client`.
- **Option B** — new `creative_assets` table with per-slide /
  per-frame rows.

Two new operator-only server actions on the dashboard:

- `prepareClientVisualPreviewAction({contentItemId, visualUrl})` —
  writes the URL; **does not** flip share.
- `shareVisualPreviewWithClientAction({contentItemId})` — flips
  `shared_with_visual_client = true`; status-guarded.

Until that migration ships, the client portal does not project
anything visual.

## Safety rules (carried forward)

- No image generation API. No DALL·E / Imagen / Stable Diffusion /
  OpenAI / Anthropic.
- No `fetch()` to any provider from this code path.
- No paid call. Template renders are free; paid runtimes inherit the
  Seedance-style gate.
- No publishing. No email.
- No client visibility. The brief, approval, manifest, and QA
  checklist all live operator-side; the client portal view
  (`client_content_items_v`, migration 009) does not project
  `prompt_summary`, and no `client_safe_visual_url` exists yet.
- No `child_process` / `spawn` / `exec`. No server-side Puppeteer
  execution.
- The dashboard never auto-runs the export stub.
