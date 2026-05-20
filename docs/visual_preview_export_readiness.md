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
