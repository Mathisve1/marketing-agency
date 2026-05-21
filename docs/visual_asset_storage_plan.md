# Visual Asset Storage Plan (Phase 5B)

Status: **planning + scaffold only**. No upload happens in this
phase. No R2 binding added. No `creative_assets` migration applied.
No `generated_assets` row inserted. The dashboard does not gain a
file picker or upload button. The scaffold pins WHERE exported PNGs
will live and WHAT the canonical object key looks like, so the
future upload step (Phase 5C+) is a small, well-scoped delta.

## 1. Storage decision

**Recommended: Cloudflare R2.**

Why R2:

- Worker-native binding (`wrangler secret put` + `r2_buckets` in
  `wrangler.jsonc`); no new vendor; no extra billing surface.
- The dashboard already runs on Cloudflare Workers
  (`yuvo-dashboard`). The OpenNext bundle already supports R2
  bindings.
- Egress is free for traffic served via Workers, which lines up with
  the future client-safe visual preview lifecycle (the portal will
  load the asset through the dashboard, not directly from R2).

Why **not** Supabase Storage:

- Adds bandwidth to a tier that's already serving auth + Postgres,
  with stricter free-tier caps.
- The dashboard has zero existing Supabase Storage usage today, so
  picking it for the first visual asset would establish a new
  storage surface to maintain.

Why **not** local-only forever:

- Local files can't power the future `client_safe_visual_url` flow
  (Phase 5C). The portal can't read from an operator's laptop.

Why **not** the existing CloudFront pattern:

- CloudFront URLs appear only in demo data
  (`web/lib/demo-data.ts`) for sample MP4 / thumbnail seeds.
  Production video assets already live in `generated_assets`
  (Supabase row + storage_path). Adopting CloudFront for visuals
  would inherit AWS account ownership that the project does not
  have today.

**Phase 5B does NOT add the R2 binding.** A future phase (5C) adds
it under explicit operator approval, alongside applying migration
011 (or its successor) and shipping the prepare/share lifecycle.

## 2. Required R2 configuration (Phase 5C, NOT yet applied)

```jsonc
// wrangler.jsonc — PROPOSAL (Phase 5C, NOT applied in 5B)
{
  // … existing fields …
  "r2_buckets": [
    {
      "binding": "VISUAL_ASSETS_BUCKET",
      "bucket_name": "yuvo-visual-assets"
    }
  ]
}
```

- **Binding name (code-facing)**: `VISUAL_ASSETS_BUCKET`.
- **Bucket name (Cloudflare-facing)**: `yuvo-visual-assets`.
- **Region / jurisdiction**: default (auto). No jurisdictional
  pinning until a client compliance need surfaces.
- **Visibility**: bucket is PRIVATE. The dashboard signs / proxies
  reads — clients never get a direct R2 URL.

## 3. Object key convention

Every exported visual asset lives at:

```
visual-assets/{workspace_id}/{content_item_id}/{template_id}/{theme_id}/{filename}
```

`{filename}` is computed by `buildVisualAssetFilename` in
`web/lib/creative/visual-asset-paths.ts`:

```
{template_id|<mode>_default}-{content_id_stub}-{slide|frame}-{WxH}.{png|jpg}
```

Examples:

- `visual-assets/<ws>/<ci>/feed_post_neutral_v1/neutral/feed_post_neutral_v1-b920e5e2-1080x1350.png`
- `visual-assets/<ws>/<ci>/carousel_neutral_v1/editorial/carousel_neutral_v1-b920e5e2-slide03-1080x1350.png`
- `visual-assets/<ws>/<ci>/story_neutral_v1/premium_dark/story_neutral_v1-b920e5e2-frame02-1080x1920.png`

Properties enforced by the helper:

- `workspace_id` and `content_item_id` must be UUIDs (rejects
  traversal by construction).
- `template_id` / `theme_id` / `mode` are sanitised to
  `[a-z0-9_-]{1,80}`. Empty input falls back to a stable slug
  (`<mode>_default` for templates, `content` for ids).
- `slideNumber` and `frameNumber` are positive integers; not both.
- Extension is pinned to `png` or `jpg`.

## 4. File size / type constraints

- Allowed extensions: `.png`, `.jpg` (`.jpeg` accepted by the local
  guard, normalised on upload).
- Max file size (proposed, NOT enforced in 5B): **8 MB** per asset.
  Enough headroom for high-quality 1080×1920 PNGs without admitting
  multi-megabyte uncompressed full-page screenshots.
- Allowed MIME types: `image/png`, `image/jpeg`.
- Filename + key safety: `assertLocalUploadPathSafe` rejects `..`,
  `/etc`, `/root`, and any extension other than png/jpg/jpeg.

## 5. Local file → R2 object flow (Phase 5C+)

```
1. Operator runs scripts/export_visual_preview_stub.py
   (Phase 5A) with --execute + the confirmation phrase.
2. The future real exporter saves PNG/JPG to the
   manifest's planned_output_path.
3. Operator runs scripts/upload_visual_asset_stub.py
   (Phase 5B scaffold, dry-run today; Phase 5C real).
4. The future real uploader:
   a. Validates the local path (assertLocalUploadPathSafe).
   b. Validates the storage key against
      buildVisualAssetPath().
   c. PUTs the bytes to R2 via the Worker (no direct
      operator-to-R2 path — request goes through a gated
      server action so credentials never live on the
      operator machine).
   d. Inserts ONE creative_assets row (Phase 5C+),
      status="rendered_internal".
5. NO client share yet. The operator must explicitly run
   prepareClientVisualPreviewAction (Phase 5C, gated).
```

**Phase 5B implements step 3's dry-run only.** Steps 4 and 5 stay
unimplemented until the storage binding + migration + server
actions land under explicit approval.

## 6. Thumbnail strategy (future, not in 5B)

Each exported PNG gets a small thumbnail (~360×450 for 4:5,
~360×640 for 9:16) generated by the same export script, stored at
`visual-assets/.../thumbnails/<filename>`. The thumbnail powers:

- the dashboard list view (no need to load the full asset)
- the future `client_safe_visual_thumbnail_url` projection on the
  client portal view

Thumbnails are deferred until the full asset upload works.

## 7. `generated_assets` vs `creative_assets` (decision)

**`generated_assets` is unsuitable for visual previews.** Every row
in `generated_assets` carries a non-null `generation_job_id` (FK to
`generation_jobs`). Visual previews are brief-driven; there is no
upstream paid job. Forcing a fake `generation_job_id` would:

- couple the visual lifecycle to the video pipeline,
- pollute the jobs dashboard with non-jobs,
- and break the cost-attribution rollup in
  `getOwnerOverview.creditsByBrandId`.

The cleaner home is a new `creative_assets` table. Migration
proposal: `supabase/migrations/012_creative_assets_proposal.sql`
(NOT applied; see Phase 5B file). It supersedes the original
`creative_assets` sketch from
`docs/visual_asset_generation_plan.md` (Phase 4B) by adding the
storage-key column needed for the path convention above.

## 8. Relationship with `client_safe_visual_url`

The `creative_assets` row carries two distinct URL columns:

- `internal_asset_url` — operator-only. Whatever signed / direct
  URL the Worker uses to read the bytes from R2. Stored for
  internal audit; never projected to the client view.
- `client_safe_visual_url` — operator-prepared client-visible URL.
  Populated only by `prepareClientVisualPreviewAction` (Phase 5C).
  Stays null until the operator explicitly shares.

The future `client_creative_assets_v` view projects only
`client_safe_visual_url` (+ thumbnail) and the matched content item
id. It never projects storage keys, internal URLs, costs, brief
provenance, or `prompt_summary`.

## 9. Why no upload happens in Phase 5B

- The R2 binding is **not** in `wrangler.jsonc` yet.
- The `creative_assets` migration is **not** applied yet.
- No operator-gated upload server action exists yet.
- Phase 5A's real-export branch is still dependency-permit-locked,
  so no PNGs exist to upload anyway.
- Building any of those four pieces in 5B would mean a partially-
  exposed surface (R2 credentials in flight, DB rows pointing at
  storage keys that resolve to 404, etc).

Phase 5B is exclusively the scaffold: path helper, upload CLI
stub, migration proposal, UI placeholder, docs. Every piece
matures behind the same operator-approval gate when 5C starts.

## 10. Safety carry-forward

- No image generation. No `dall.?e|imagen|midjourney|openai|anthropic`.
- No paid call. No `fetch()` to a storage provider in 5B.
- No `generated_assets` insert. The Phase 5A "no DB write" claim
  is preserved.
- No client share. `client_safe_*` columns untouched.
- No publishing. No email.
- No subprocess. No `child_process`. No `spawn`. No `exec`.
- The local upload CLI stub follows the exact same refusal pattern
  as `export_visual_preview_stub.py`: `--execute` is refused
  outright (exit 2) until both the binding and the server action
  land.

## 11. Phase 5C handoff

When you start Phase 5C the build list will be:

1. Add the R2 binding to `wrangler.jsonc`. `wrangler secret put` an
   R2-access secret if the binding needs one.
2. Apply migration 011 (and the new 012 if you adopt
   `creative_assets`). Or merge 011+012 into a single migration
   under operator approval.
3. Wire the real-export Python code path (Phase 5A followup).
4. Wire the operator-only server action `uploadVisualAssetAction`
   that the local CLI calls (or that the dashboard exposes from a
   future "Upload" panel — both routes pass through the same
   action so credentials stay on the Worker).
5. Wire `prepareClientVisualPreviewAction` (+ share action) that
   flips `client_safe_visual_url`.
6. Extend `client_content_items_v` (or build a new
   `client_creative_assets_v`) to project the client-safe URL only
   when `shared_with_visual_client = true`.
7. Add the portal page section that renders the shared visual.

Phase 5D and 5E remain plan-only until 5C is verified clean.

## Phase 5C update (no R2 binding added)

Phase 5C **kept the R2 binding out of `wrangler.jsonc`** by design.
The decision tree is:

- Phase 5C ships the schema readiness detector + fail-soft server
  action stubs + the disabled UI panel.
- The detector reports `not_configured` until both migration 012 is
  applied AND the binding lands. Until then the dashboard cannot
  even attempt an upload — every action stub refuses cleanly.
- Adding the binding without the matching server action would
  surface an unused secret on the Worker; adding the action without
  the binding would crash at first call. Phase 5C avoids both.

Phase 5C+ next steps for storage (still gated on explicit approval):

1. `wrangler r2 bucket create yuvo-visual-assets`
2. Add the `r2_buckets` entry to `wrangler.jsonc` (binding
   `VISUAL_ASSETS_BUCKET`).
3. Apply migration 012 in Supabase SQL editor.
4. Land the real `uploadVisualAssetAction` server action behind the
   binding + operator persona gate.
5. Flip the `upload_visual_asset_stub.py` `--execute` path from
   refusal to "POST to /api/internal/upload-visual-asset" (or
   whatever the action's transport ends up being).
