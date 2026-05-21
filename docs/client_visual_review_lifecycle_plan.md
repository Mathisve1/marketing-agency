# Client Visual Review Lifecycle Plan (Phase 5C + 5D)

Status: **planning + Phase 5C fail-soft scaffold shipped**. The
client portal does not yet render any visual asset. The dashboard
exposes the lifecycle controls in a disabled state so the operator
can see exactly what's missing.

## The full lifecycle

```
[creative brief]                                            Phase 4A
   ↓
HTML/CSS preview (operator-only)                            Phase 4C / 4E
   ↓
[creative brief approval] internal sign-off                 Phase 4D1
   ↓
local PNG export (operator-run, dependency-permit-locked)   Phase 5A (real impl pending)
   ↓
upload to R2 bucket `yuvo-visual-assets`                    Phase 5C+ (no R2 binding yet)
   ↓
creative_assets row INSERT (status='exported_internal')     Phase 5C+ (migration 012 not applied)
   ↓
prepareClientVisualPreviewAction                            Phase 5D (action exists, refuses)
   • sets client_safe_visual_url + thumbnail_url
   • status='client_preview_prepared'
   ↓
shareVisualPreviewWithClientAction                          Phase 5D
   • sets client_shared_at
   • status='shared_with_client'
   ↓
client portal reads via client_creative_assets_v            Phase 5C+ (view in 012, not applied)
   ↓
client decides:
   ├── content_approvals row (decision='approved')          existing table (reused)
   │   creative_assets.client_decision_status='approved'
   │   creative_assets.status='approved_by_client'
   │
   └── content_approvals row (decision='changes_requested') existing table (reused)
       creative_assets.client_decision_status='changes_requested'
       creative_assets.status='changes_requested_by_client'
       ↓
       operator revises the brief / re-exports / re-uploads /
       re-prepares / re-shares (new row OR re-prepare same row)
   ↓
archived (status='archived', no DELETE)
```

## Reuses the existing approval ledger

The client decision lands in the existing `content_approvals` table
(same table used by copy + video review). One ledger across all
content surfaces.

`creative_assets.client_decision_status` is denormalised onto the
asset row so the dashboard's queue reader doesn't have to join the
approvals table just to render badges. The base of truth remains
`content_approvals`.

## Revision loop

When a client requests changes, the operator has three options:

1. **Re-draft the creative brief**, regenerate the preview, run the
   export pipeline again. The new export INSERTs a new
   `creative_assets` row with `variant_number = previous + 1`. The
   prior row's `status` flips to `archived`.
2. **Tweak the template or theme**, re-export, re-upload, re-prepare
   the same content item with a different `template_id`/`theme_id`.
   Again a new row.
3. **Abandon**, mark the asset `archived` and stop.

There is no "rewrite" path on a shared row. Every client-visible
decision sticks to the row that earned it; new attempts get fresh
rows. This keeps the audit trail intact.

## Status enum (canonical)

Allowed values for `creative_assets.status` (see migration 012):

| Status | Meaning | Operator action that sets it |
|---|---|---|
| `draft` | Row exists but no export yet | INSERT during upload prep |
| `exported_internal` | PNG/JPG uploaded to R2 | upload action |
| `approved_internal` | Operator signed off internally | approve action |
| `client_preview_prepared` | `client_safe_visual_url` set, not shared | `prepareClientVisualPreviewAction` |
| `shared_with_client` | `client_shared_at` set | `shareVisualPreviewWithClientAction` |
| `approved_by_client` | Client picked "approve" | `recordClientVisualDecisionAction` (future) |
| `changes_requested_by_client` | Client picked "request changes" | same |
| `archived` | Operator retired this variant | `archiveCreativeAssetAction` (future) |

## Schema readiness gate (Phase 5C, shipped)

`web/lib/data/visual-preview-schema.ts` is the single source of
truth for "is this lifecycle even possible right now?". It probes
PostgREST and returns one of:

- `creative_assets` — preferred; 012 applied
- `content_items_extension` — fallback; only 011 applied
- `not_configured` — nothing applied (this is the state today)

Every Phase 5D server action will gate on this detector. Today, the
three Phase 5C stub actions already do:

- `prepareClientVisualPreviewAction`
- `shareVisualPreviewWithClientAction`
- `resetClientVisualPreviewAction`

All three return a structured `ClientVisualPreviewActionResult` with
`ok: false` until the detector reports `ready: true` AND a future
phase replaces the "not implemented in Phase 5C" body.

## What does NOT exist yet

- The `client_creative_assets_v` view is proposed in 012 but not
  applied. The client portal cannot read visuals.
- The R2 binding `VISUAL_ASSETS_BUCKET` is not in `wrangler.jsonc`.
- The upload action `uploadVisualAssetAction` is not implemented;
  `scripts/upload_visual_asset_stub.py --execute` refuses.
- The real Playwright/Puppeteer export branch is permit-locked
  (`APPROVED_BROWSER_RUNTIMES_PERMITTED_FOR_EXECUTE` is `()`).
- No client portal page renders visual assets.
- No publishing path exists.

## Phase 5D entry checklist

Before starting Phase 5D, the operator must explicitly approve:

1. Applying migration 012 (and the view block at the end of it).
2. Adding the R2 binding to `wrangler.jsonc`.
3. Flipping the Phase 5A permit list (`APPROVED_BROWSER_RUNTIMES_PERMITTED_FOR_EXECUTE`)
   to include `playwright.sync_api` or `pyppeteer`.
4. Landing the real `uploadVisualAssetAction` server action behind
   the binding + the existing operator persona gate.

Phase 5D then ships the real bodies of the three Phase 5C stubs +
the client-portal renderer. Phase 5E (publishing) remains untouched
until 5D is verified clean in production.
