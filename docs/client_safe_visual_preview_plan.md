# Client-Safe Visual Preview Plan (Phase 4D5)

Status: **planning / proposal only**. No code, no migration applied,
no client share, no UI shipped. This doc proposes the schema and
lifecycle that Phase 4E will use to let an operator share a rendered
creative preview (carousel slides, story frames, feed/static post,
LinkedIn companion image) with the client portal — mirroring the
existing copy-preview pattern from Phase 2G.

## Goal

After Phase 4C (HTML/CSS previews) and Phase 4D1–4 (internal
approval + template metadata + preview UX polish + Export PNG
placeholder), the next missing piece is the **client-visible layer**.
A client should be able to see an approved visual preview without:

- exposing the brief, template id, render strategy, or any operator
  notes
- exposing provider IDs, costs, or render-engine details
- seeing anything before the operator explicitly clicks "Share"
- ever triggering a render / publish / paid action themselves

## Lifecycle (proposed, no schema yet)

Mirrors the copy-preview lifecycle from Phase 2G exactly:

```
[creative brief]                                                  Phase 4A
   → preview rendered server-side (HTML/CSS)                      Phase 4C
   → [creative brief approval] internal sign-off                  Phase 4D1
   → PNG/JPG exported via gated operator action                   Phase 4D / 4E
   → client_safe_visual_url PREPARED  (visible only via the
       client-scoped view; portal renders it; not shared yet)     Phase 4E
   → shared_with_visual_client = true (explicit operator click)   Phase 4E
   → portal displays it; client decides:                          Phase 4E
        APPROVED_BY_CLIENT
        CHANGES_REQUESTED_BY_CLIENT --> revision loop --> new brief
   → ARCHIVED
```

Every transition requires an explicit operator action. No automatic
publishing. No automatic share. No paid call without the existing
Seedance-style gate.

## Compared to copy preview (Phase 2G — already shipped)

| Stage | Copy (Phase 2G/2H/2I) | Visual (Phase 4E proposal) |
|---|---|---|
| Internal sign-off block | `[copy approval]` in `prompt_summary` | `[creative brief approval]` (Phase 4D1 — shipped) |
| Operator-only preview text/URL | `content_items.client_safe_copy_preview` | `content_items.client_safe_visual_url` (proposed) or `creative_assets.client_safe_visual_url` |
| Prepare action | `prepareClientCopyPreviewAction` | `prepareClientVisualPreviewAction` (proposed) |
| Share toggle column | `content_items.shared_with_client` | `content_items.shared_with_visual_client` (proposed) |
| Share action | `shareCopyPreviewWithClientAction` | `shareVisualPreviewWithClientAction` (proposed) |
| Client portal view | `client_content_items_v` (projects `client_safe_copy_preview` only) | extend to also project `client_safe_visual_url` + `shared_with_visual_client` (proposed) |
| Client decision | `content_approvals` row | `content_approvals` row (reused) |
| Change request | `regeneration_requests` / revise action | new "creative_revision_requests" row or reuse `regeneration_requests` |

Reusing the existing `content_approvals` table for the client
decision keeps the dashboard's approval ledger unified across copy
AND visual. This is the recommended path.

## Storage options

### Option A — extend `content_items` (simpler, single-asset items)

Add two columns to `public.content_items`:

```sql
-- supabase/migrations/0XX_client_safe_visual_preview.sql (PROPOSAL — DO NOT APPLY)
alter table public.content_items
  add column if not exists client_safe_visual_url text,
  add column if not exists shared_with_visual_client boolean not null default false;

comment on column public.content_items.client_safe_visual_url is
  'Phase 4E. Operator-prepared client-visible visual asset URL '
  'for non-video formats (carousel cover, story cover, feed/static '
  'post, LinkedIn companion). Only the client view projects this. '
  'NULL until the operator explicitly prepares it via '
  'prepareClientVisualPreviewAction.';

comment on column public.content_items.shared_with_visual_client is
  'Phase 4E. True iff the operator has explicitly clicked share. '
  'The client portal renders client_safe_visual_url only when this '
  'flag is true. Mirrors content_items.shared_with_client for copy.';
```

Then extend `client_content_items_v`:

```sql
create or replace view public.client_content_items_v as
select
  ci.id,
  -- … existing columns …
  ci.client_safe_copy_preview,
  ci.client_safe_visual_url,
  ci.shared_with_visual_client
from public.content_items ci;
```

**Trade-off**: simple, fits a one-asset-per-content-item model
(single carousel cover, single story cover, single feed post). Does
NOT model per-slide / per-frame assets for carousels and stories.

### Option B — new `creative_assets` table (Phase 4B proposal, recommended for multi-asset items)

Already drafted in `docs/visual_asset_generation_plan.md`. Adds:

```sql
-- subset of the Phase 4B-proposed creative_assets table
create table public.creative_assets (
  id                       uuid primary key default gen_random_uuid(),
  workspace_id             uuid not null references public.workspaces(id) on delete cascade,
  content_item_id          uuid not null references public.content_items(id) on delete cascade,
  asset_type               text not null,
  slide_number             int,
  frame_number             int,
  variant_number           int not null default 1,
  asset_url                text,
  client_safe_visual_url   text,
  shared_with_client       boolean not null default false,
  status                   text not null,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);
```

Plus a scoped view `client_creative_assets_v` that joins on
`content_items` and projects only:

- `content_item_id`
- `asset_type`
- `slide_number` / `frame_number`
- `client_safe_visual_url`
- (NEVER: `creative_brief_source`, `template_id`, `asset_url`
  internal, provider IDs, costs, render strategy)

**Trade-off**: cleaner data model, allows per-slide approval, but
adds a new table + view + RLS policy set.

### Recommendation

Start with **Option A** for Phase 4E if shipping the first share
flow quickly is the priority and the first format(s) are single-image
formats (feed_post / static_image / linkedin_image). Add Option B in
a follow-up when carousels and stories need per-slide preview.

Either way: **DO NOT apply the migration in Phase 4D5**. This document
is a proposal only.

## RLS rules (carry-forward)

- Workspace members can SELECT/INSERT/UPDATE rows on `content_items`
  via `app.is_workspace_member(workspace_id)` (already in place).
- Operators are workspace members; client portal users are NOT — they
  see only what the scoped client view projects.
- `client_safe_visual_url` is **only** projected when
  `shared_with_visual_client = true`. The view encodes this gate.
- No DELETE policy on `creative_assets` (mirrors `content_items`).
- The view never projects: `creative_brief_source`, `template_id`,
  `render_strategy`, `brief_json`, provider request ids, byte sizes,
  internal storage paths, costs, or anything from `prompt_summary`.

## Server actions (proposed)

`web/lib/actions/client-visual-preview.ts` (Phase 4E — proposal):

```ts
prepareClientVisualPreviewAction({
  contentItemId: string;
  visualUrl: string;          // R2-hosted PNG; written by Phase 4D export
  notes?: string;
}): Promise<{ ok: boolean; ... }>
```

- Operator-only persona gate.
- Requires the content item to be in an operator-editable status.
- Requires an existing `[creative brief approval]` block (internal
  sign-off must precede client share — same as the copy preview
  requires `[copy approval]`).
- Writes `content_items.client_safe_visual_url` (Option A) or a new
  `creative_assets` row (Option B) — does NOT yet flip
  `shared_with_visual_client`.

```ts
shareVisualPreviewWithClientAction({
  contentItemId: string;
}): Promise<{ ok: boolean; ... }>
```

- Operator-only.
- Status-guarded: requires `client_safe_visual_url IS NOT NULL`.
- Sets `shared_with_visual_client = true` and revalidates the
  client portal path.
- NEVER publishes anywhere. NEVER notifies the client via email
  (the existing copy-preview share also doesn't email — Phase 2G
  policy preserved).

```ts
resetVisualPreviewShareAction({ contentItemId }):
```

- Operator-only.
- Flips `shared_with_visual_client = false`; preserves
  `client_safe_visual_url` so re-sharing is a single click.

## Client portal additions (proposed, Phase 4E)

`web/app/client/[portalSlug]/content/[contentId]/page.tsx` — extend
to render `client_safe_visual_url` in a small card when
`shared_with_visual_client = true`. Pure read; the existing portal
auth gate (`requireClientPortalAccess`) is the only gate needed.

The portal will NEVER render any of:

- `prompt_summary` (already invisible via `client_content_items_v`
  not projecting it)
- `[creative brief]` block
- `[creative brief approval]` block
- template id, render strategy, render cost, provider request id,
  internal storage paths
- operator notes

## Safety gates (carry-forward + extension)

- No image generation reachable from this lifecycle. (Phase 4F is
  the earliest a paid render could land, and it stays behind the
  Seedance-style confirmation phrase + cost estimate.)
- No automatic publishing.
- No automatic share — `shared_with_visual_client` flips only via
  the explicit server action.
- No email — the client checks the portal.
- Operator can revoke a share at any time
  (`resetVisualPreviewShareAction`); the URL stays on file (no
  physical delete; mirrors the rest of the dashboard's "no DELETE
  policy" stance).

## What Phase 4D5 does NOT do

- It does NOT alter Supabase.
- It does NOT add `client_safe_visual_url` to any code path yet.
- It does NOT add a `shared_with_visual_client` flag.
- It does NOT touch `/client/*` pages.
- It does NOT add any server action.
- It is **planning only** — pinning the schema + action shape so the
  next implementing phase (4E) has a single decision tree to follow.

## Future order of implementation

1. **Phase 4D — PNG export pipe.** Operator-gated; recommended runtime
   = local operator Puppeteer script that hits the existing preview
   URL and uploads to R2. See `docs/visual_asset_generation_plan.md`.
2. **Phase 4E — client-safe visual preview lifecycle.** Apply EITHER
   Option A migration (extend `content_items`) OR Option B migration
   (new `creative_assets` table). Ship the two server actions above.
   Extend `client_content_items_v` and the portal page.
3. **Phase 4F — AI background-layer opt-in.** Only after 4E is
   trusted in production. Gated like Seedance.

Each step keeps the same contract: nothing reaches the client without
an explicit operator action; nothing paid runs without an explicit
operator confirmation phrase; nothing irreversible can happen by
loading a page.

## Phase 4F status (PROPOSAL ONLY — not applied)

Phase 4F shipped the **operator-side polish** that this plan
depends on:

- Persisted internal QA checklist
  (`[creative preview QA]` block in `prompt_summary`).
- Approval panel surfaces the manifest's `export_readiness` +
  `blockers[]` (informational only — approval is not blocked).
- "Copy local export command" button (clipboard only; references
  the stub script).

It did NOT ship the schema or the server actions documented above.
The migration file `supabase/migrations/011_client_safe_visual_preview.sql`
exists as a **proposal** that mirrors Option A:

```sql
alter table public.content_items
  add column if not exists client_safe_visual_url           text,
  add column if not exists client_safe_visual_thumbnail_url text,
  add column if not exists visual_preview_status            text;
```

The migration is idempotent (`if not exists` / `do $$ … if not
exists $$` for the CHECK constraint), but its header carries an
explicit "**DO NOT APPLY — proposal only**" banner. It is NOT applied
in Phase 4F. A separate, follow-up migration (proposed 012) would
extend `client_content_items_v` to also project the new fields; that
view extension is also NOT in scope for Phase 4F.

Application code in Phase 4F does NOT read or write the proposed
columns; they will be wired only after both the migration AND the
proposed server actions (`prepareClientVisualPreviewAction` /
`shareVisualPreviewWithClientAction`) ship in a later phase.
