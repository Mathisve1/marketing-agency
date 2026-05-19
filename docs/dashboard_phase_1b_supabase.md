# Yuvo OS — Phase 1B: Supabase schema, RLS, Pai seed

Phase 1B adds the **persistence layer** under the Phase 1A dashboard
scaffold. It does **not** wire a runtime Supabase client into the Next.js
app — that lands in Phase 1C alongside Supabase Auth. After Phase 1B you
have:

- A migrations folder you can apply to any Supabase project to stand up the
  agency-operator + client-portal schema.
- Row-Level-Security policies enforcing the operator vs client persona
  separation at the database layer.
- A Pai Skincare seed that mirrors `web/lib/demo-data.ts` row-for-row, so
  Phase 1C can flip `NEXT_PUBLIC_DATA_SOURCE=supabase` and render the same
  UI without changing any pages.
- A `web/lib/data/*` data-access layer that the UI now reads through
  exclusively — the demo path stays the default; the supabase branch is a
  stub that throws a clear error until Phase 1C.

## What ships in Phase 1B

| Asset | Path | Purpose |
|---|---|---|
| Initial schema | `supabase/migrations/001_initial_dashboard_schema.sql` | 14 tables, helper schema `app`, client-safe view |
| RLS policies | `supabase/migrations/002_rls_policies.sql` | RLS enabled on every table; operator + client policies + 5 deferred TODOs |
| Pai seed | `supabase/seed.sql` | Workspace → Brand → Campaign → 2 content items + 3 generated_assets |
| Data abstraction | `web/lib/data/{brands,campaigns,content}.ts` + `_source.ts` | `NEXT_PUBLIC_DATA_SOURCE` flag; default `demo` |
| Client-safe surface | `web/lib/data/content.ts` | `getClientPortalBySlug`, `getClientVisibleContentItems`, `getClientContentItem` |

What is **out of scope** for Phase 1B (deferred to 1C / 1G):

- `generation_jobs`, `audio_fixer_jobs`, and the cost-ledger table
- `auth.users` foreign keys and the profile-auto-create trigger
- A real `@supabase/supabase-js` client in `web/`
- Storage buckets and signed URLs
- Owner-only operator policies (every workspace member is currently
  treated as "operator")
- The "share via magic link" unauthenticated flow

## Schema overview

```
workspaces ─┬─ workspace_members ─ profiles
            ├─ brands ─┬─ brand_assets
            │          └─ clients ─ client_portals ─┬─ client_portal_members
            │                                       └─ campaigns ─┬─ content_calendars
            │                                                      └─ content_items ─┬─ generated_assets
            │                                                                        ├─ content_feedback
            │                                                                        └─ content_approvals
            └─ content_requests (per portal)
```

Tables (14):

1. `workspaces`
2. `profiles` *(mirrors `auth.users` for display metadata)*
3. `workspace_members` *(role: owner / operator / viewer)*
4. `brands`
5. `brand_assets` *(logo, product_image, reference_video, palette, other)*
6. `clients`
7. `client_portals` *(slug is the public URL key)*
8. `client_portal_members`
9. `campaigns`
10. `content_calendars`
11. `content_items` *(the operator's full record — see below)*
12. `generated_assets`
13. `content_feedback`
14. `content_approvals`
15. `content_requests` *(backs the "what would you like next week?" form)*

Plus one view: `client_content_items_v` — the client-safe projection.

### Content item — the security-critical row

`content_items` is the only table that mixes operator-only and
client-safe fields. The split is enforced two ways:

1. **Schema layer.** The `client_content_items_v` view is created
   `with (security_invoker = true)` and projects away every operator-only
   column (`prompt_summary`, `quality_tier`, `cost_*`, `internal_*_path`,
   `audio_fixer_*`). Even if a client somehow obtained `SELECT` on the
   base table, the view would not leak those columns through any API
   surface that uses it.
2. **Row layer.** A `CHECK` constraint
   `content_items_shared_flag_consistent` enforces that
   `shared_with_client = true` whenever `status` is in
   `('shared_with_client', 'approved_by_client', 'changes_requested_by_client')`.
   The client RLS policy filters on **both** flag + status, so a status
   change without the flag won't accidentally expose a row.

Status enum (matches `web/lib/types.ts`):

```
draft, generating, raw_ready, audio_fixer_pending, audio_fixed,
ready_for_client_review, shared_with_client, approved_by_client,
changes_requested_by_client, failed
```

Client-visible subset: the last three only (see
`CLIENT_VISIBLE_STATUSES`).

Quality tiers (matches `web/lib/quality-tiers.ts`):

```
draft_480p       82.0 cr/s   ≈ 1,230 cr for 15s
standard_720p   176.4 cr/s   ≈ 2,646 cr for 15s   ← DEFAULT
premium_1080p   396.0 cr/s   ≈ 5,940 cr for 15s
```

Audio Fixer is manual-only and costs ≈2,100 credits per 15s clip
(2,103.75 rounded to 2,104 in the seed). It never auto-runs.

## RLS model — operator vs client

Every table has `enable row level security` from day one. Two helper
functions in the `app` schema do the heavy lifting:

```sql
app.is_workspace_member(target_workspace uuid)
app.is_portal_member(target_portal uuid)
```

Both are `security definer` so policies don't recurse into themselves.
Both read `app.current_profile_id()` which today returns `auth.uid()` —
the same primitive Supabase Auth provides once Phase 1C wires it.

| Persona | Reads | Writes |
|---|---|---|
| **Operator** (workspace_member of the row's workspace) | Every column of every workspace-owned row | Operator-owned tables (brands, campaigns, content_items, generated_assets, etc.) |
| **Client** (portal_member of the row's portal) | `client_content_items_v` only; `generated_assets` filtered to `kind = 'thumbnail'`; their own feedback + approvals + requests | Insert into `content_feedback`, `content_approvals`, `content_requests` only — never modify content_items directly |

Five deferred TODOs documented at the bottom of `002_rls_policies.sql`:

1. Owner-only enforcement on workspace-level mutations (currently any
   workspace_member can write).
2. Move client `SELECT` from `content_items` over to
   `client_content_items_v` once the API layer is in place.
3. Profile auto-creation trigger on `auth.users` insert.
4. Per-column update block on operator-only columns (defense in depth on
   top of the view).
5. JWT-claim helper for unauthenticated "share via magic link" flow.

### Why the client RLS policy currently sits on the base table

Phase 1B attaches the client `SELECT` policy to `content_items` filtered
by `shared_with_client = true AND status IN (…)` rather than to the view.
This is intentional: the view is the schema-level guarantee
(operator-only columns are absent), and the base-table policy is the
row-level guarantee. Phase 1C will tighten this so clients can only
`SELECT` from the view, not the base table.

## Local run

Phase 1B does not require a running Supabase instance — the migrations
and seed are SQL files you can apply on your own schedule. Two flows are
supported.

### Flow 1 — Local Postgres (recommended for schema review)

```bash
# Start any local Postgres (you can use Docker, or `supabase start`
# if you have the CLI installed).
psql "$DATABASE_URL" -f supabase/migrations/001_initial_dashboard_schema.sql
psql "$DATABASE_URL" -f supabase/migrations/002_rls_policies.sql
psql "$DATABASE_URL" -f supabase/seed.sql
```

Sanity-check counts (also documented at the bottom of `seed.sql`):

```sql
select count(*) from public.workspaces;              -- 1
select count(*) from public.brands;                  -- 1
select count(*) from public.campaigns;               -- 1
select count(*) from public.content_items;           -- 2
select count(*) from public.client_content_items_v;  -- 1 (the shared one only)
select count(*) from public.generated_assets;        -- 3
```

### Flow 2 — Supabase CLI (when available)

`supabase` CLI is not on the developer's PATH today, so the migrations
are hand-authored SQL files rather than CLI-generated stubs. Once the
CLI is installed:

```bash
supabase db reset                  # applies migrations + seed
supabase db push --linked          # to a hosted project
```

`seed.sql` lives at `supabase/seed.sql`, which is the CLI's default seed
location, so `supabase db reset` picks it up automatically.

### Flow 3 — Hosted Supabase project (Phase 1C readiness)

Apply the migrations in order via the SQL editor, then run `seed.sql`.
The seed uses fixed UUID literals and `on conflict (id) do nothing`, so
it is safe to re-run; to **fully** reset, truncate downstream tables
first (the comment at the top of `seed.sql` has the exact `truncate`
statement).

## Pai seed details

| Entity | UUID | Notes |
|---|---|---|
| workspace | `11111111-1111-1111-1111-111111111111` | "Yuvo Studio" |
| operator profile | `22222222-2222-2222-2222-222222222222` | placeholder until Phase 1C |
| brand | `33333333-3333-3333-3333-333333333333` | Pai Skincare, niche=premium organic skincare |
| client | `44444444-4444-4444-4444-444444444444` | Pai Skincare contracting entity |
| client_portal | `55555555-5555-5555-5555-555555555555` | slug `pai-skincare-demo` |
| campaign | `66666666-6666-6666-6666-666666666666` | "Route 01 — UGC ingredient-led sensitive-skin serum" |
| content_calendar | `77777777-7777-7777-7777-777777777777` | "Week of 20 May 2026" |
| content_item 1 | `88888888-8888-8888-8888-888888888888` | `shared_with_client`, `premium_1080p`, 5,940 cr + 2,104 audio fixer |
| content_item 2 | `99999999-9999-9999-9999-999999999999` | `draft`, `standard_720p`, 2,646 cr estimate |
| generated_assets | `a…`, `b…`, `c…` | raw / audio_fixed / thumbnail for item 1 |

Item 1's `internal_*_path` fields point at the **existing local**
filesystem clips under
`prospects/pai-skincare/production/clips/route_01_enhancor_ugc_*`. When
Phase 1C wires Supabase Storage these paths become bucket keys + signed
URLs and the `public_url` columns flip from `null` to the signed URL.

## Demo path vs Supabase path

The UI now imports every data fetch through `web/lib/data/*`. The Phase
1A `demo-data.ts` module is still the source of record for the default
`demo` branch — TASK 7 introduced an indirection, not a replacement.

```
NEXT_PUBLIC_DATA_SOURCE=demo       (default, current)
  ├─ Reads from web/lib/demo-data.ts
  └─ Same render output as Phase 1A

NEXT_PUBLIC_DATA_SOURCE=supabase   (Phase 1C target)
  └─ Currently throws a clear error:
     "[yuvo-data] <fn>() against Supabase is wired in Phase 1C…"
```

Client-safe surface in `web/lib/data/content.ts` exposes **only** these
three functions to `app/client/*`:

- `getClientPortalBySlug(slug)` → portal context (brand display name only;
  no `brand_tone`, no `website_url`, no `audience_assumption`)
- `getClientVisibleContentItems(campaignId)` → `ClientContentView[]`
  (filtered through `toClientContentView`)
- `getClientContentItem(portalSlug, contentId)` → `ClientContentView | null`
  (validates portal ownership internally)

All four client pages (`layout.tsx`, `page.tsx`, `calendar/page.tsx`,
`content/[contentId]/page.tsx`) have been repointed to these. No
`@/lib/demo-data` imports remain under `web/app/client/`.

Operator-side pages under `web/app/agency/` continue to import
`@/lib/demo-data` directly; they will migrate to `web/lib/data/brands`
and `web/lib/data/campaigns` in Phase 1C when the Supabase branch is
implemented for them.

## Phase 1C next steps

In order:

1. Install `@supabase/supabase-js` and add `web/lib/supabase/{client,server}.ts`.
2. Implement the `supabase` branch in `web/lib/data/{brands,campaigns,content}.ts`
   — same signatures, real DB reads.
3. Wire Supabase Auth (email magic link for clients, password for operators).
4. Add the `handle_new_user` trigger on `auth.users` to auto-create
   `public.profiles` rows.
5. Switch the client `SELECT` policy from `content_items` to
   `client_content_items_v`.
6. Migrate `web/app/agency/*` operator pages off `@/lib/demo-data`.
7. Add `generation_jobs` + `audio_fixer_jobs` + `cost_ledger` tables when
   Phase 2A starts wiring real Enhancor submissions (Phase 1G for cost
   accounting).
8. Add Supabase Storage buckets for raw / audio-fixed / thumbnail; move
   the Pai test clips out of `prospects/` and into the bucket.
