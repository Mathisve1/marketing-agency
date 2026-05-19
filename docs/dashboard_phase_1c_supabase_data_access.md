# Yuvo OS — Phase 1C: Supabase data-access branch

Phase 1C wires the **runtime Supabase client** into the Next.js dashboard
under `web/`. After Phase 1C the same UI renders unchanged from either
the local Pai demo seed OR a live Supabase project — selected by a
single env var, `NEXT_PUBLIC_DATA_SOURCE`.

Phase 1C deliberately does **not** wire Supabase Auth. That is Phase
1D's scope. See "Known limits — no auth yet" below.

## What ships in Phase 1C

| Asset | Path | Purpose |
|---|---|---|
| Supabase JS dep | `web/package.json` | `@supabase/supabase-js` `^2.105.x` |
| Client factories | `web/lib/supabase/client.ts` | `getSupabaseAnonClient()`, `getSupabaseServerClient()` |
| Row types | `web/lib/supabase/types.ts` | Hand-rolled narrow projections per table — kept in sync with the SELECT strings in `web/lib/data/*` |
| Mappers | `web/lib/data/mappers.ts` | Row → TS DTO transformers; client mapper drops every operator-only field |
| Data branch — operator | `web/lib/data/brands.ts`, `campaigns.ts` | Demo branch retained as default; supabase branch added |
| Data branch — client | `web/lib/data/content.ts` | Same — uses `client_content_items_v` view (NOT the base table) |
| Env example | `web/.env.example` | `NEXT_PUBLIC_DATA_SOURCE`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, optional `SUPABASE_SERVICE_ROLE_KEY` |
| Updated guard | `web/lib/data/_source.ts` | Adds `SupabaseDataError`; legacy `unsupportedSupabasePath` kept as backstop only |

Function signatures are unchanged from Phase 1B — the supabase branch is
added inside each function behind `if (getDataSource() === "supabase")`.
No pages were touched.

## File map

```
web/
  lib/
    supabase/
      client.ts        ← getSupabaseAnonClient() + getSupabaseServerClient()
      types.ts         ← BrandRow, CampaignWithPortalRow, ContentItemRow,
                          ClientContentItemRow
    data/
      _source.ts       ← getDataSource() + SupabaseDataError (Phase 1C)
      brands.ts        ← getAgencyBrands, getBrandById   (demo + supabase)
      campaigns.ts     ← getCampaignById, getCampaignContentItems
      content.ts       ← getClientPortalBySlug, getClientVisibleContentItems,
                          getClientContentItem
      mappers.ts       ← brandRowToBrand, campaignRowToCampaign,
                          contentItemRowToContentItem,
                          clientContentRowToClientView
  .env.example         ← copy → .env.local, fill in your Supabase creds
```

## Setup

### Step 1 — Apply migrations + seed

Phase 1B's migrations (`supabase/migrations/001_initial_dashboard_schema.sql`
+ `002_rls_policies.sql`) and `supabase/seed.sql` must be applied to your
Supabase project first. See
[`dashboard_phase_1b_supabase.md`](dashboard_phase_1b_supabase.md) for
the three flows (local Postgres, Supabase CLI, hosted SQL editor).

### Step 2 — Configure env vars

Copy `web/.env.example` to `web/.env.local` and fill in:

```bash
NEXT_PUBLIC_DATA_SOURCE=supabase

NEXT_PUBLIC_SUPABASE_URL=https://<your-project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key from Project settings → API>

# OPTIONAL — bypasses RLS server-side until Phase 1D wires auth.
# Without this, every read returns empty because auth.uid() is null.
SUPABASE_SERVICE_ROLE_KEY=<service-role key from Project settings → API>
```

### Step 3 — Verify

```bash
cd web
npm run dev      # http://localhost:3000/agency
```

- With `DATA_SOURCE=demo` (or unset): renders Pai content from the local
  seed, no Supabase round-trips.
- With `DATA_SOURCE=supabase` + `SUPABASE_SERVICE_ROLE_KEY`: same render,
  but the data came from your Supabase project's `brands`,
  `campaigns`, `content_items`, `client_content_items_v` rows.
- With `DATA_SOURCE=supabase` and NO service-role key: every page renders
  empty lists. This is the expected behaviour until Phase 1D — RLS is
  doing its job.

## How the branch picks itself

```ts
// web/lib/data/_source.ts
export function getDataSource(): DataSource {
  const raw = process?.env.NEXT_PUBLIC_DATA_SOURCE;
  if (raw === "supabase") return "supabase";
  return "demo";
}
```

```ts
// web/lib/data/brands.ts
export async function getAgencyBrands(): Promise<Brand[]> {
  if (getDataSource() === "demo") return [...DEMO_BRANDS];
  const supabase = getSupabaseServerClient();
  const { data, error } = await supabase.from("brands").select(BRAND_SELECT);
  if (error) throw new SupabaseDataError("getAgencyBrands", error);
  return (data as BrandRow[]).map(brandRowToBrand);
}
```

Same pattern in `campaigns.ts` and `content.ts`. The function signature
(`async (): Promise<Brand[]>`) is unchanged; only the body forks.

## Operator vs client surface — defence in depth

The client-portal surface continues to be the only data path the
`web/app/client/[portalSlug]/*` pages import. The supabase branch
adds one more layer of belt-and-braces on top of the existing two:

1. **SQL view layer** — `client_content_items_v` (migration 001)
   physically does not project `prompt_summary`, `quality_tier`,
   `cost_*`, `internal_*_path`, or any `audio_fixer_*` column. Even a
   `SELECT *` against the view cannot return them.
2. **Data layer** — `web/lib/data/content.ts` reads from the view, not
   the base table. The select string `CLIENT_CONTENT_SELECT` is
   pin-named so a future PR adding columns to the base table cannot
   accidentally widen the client surface.
3. **Mapper layer** — `clientContentRowToClientView` re-projects into
   the existing `ClientContentView` shape and additionally returns
   `null` for any status outside `CLIENT_VISIBLE_STATUSES`.

The operator data layer (`brands.ts`, `campaigns.ts`) reads the base
tables and may safely include operator-only columns; it is never
imported under `web/app/client/*`.

## Mapper conventions

Mappers in `web/lib/data/mappers.ts` are pure functions:

- One mapper per `(row → TS type)` pair.
- Required text columns that are nullable in Phase 1B's schema (e.g.
  `niche`, `brand_tone`) default to `""` so the UI never crashes on a
  partial seed.
- `platforms`, `quality_tier`, `resolution`, `hook_source`, and `status`
  are runtime-validated against the allow-list from
  `web/lib/types.ts`. Unknown values degrade gracefully (e.g. unknown
  status → `draft`) rather than throwing.
- `comments` and `clientRequests` stay empty arrays — the
  `content_feedback` and `content_requests` reads are deferred (no UI
  surface consumes them yet).

## Known limits — no auth yet

Phase 1C is intentionally auth-free:

1. **RLS denies everything by default.** The helpers
   `app.current_profile_id()`, `app.is_workspace_member()`, and
   `app.is_portal_member()` all read `auth.uid()`, which is `null`
   without an authenticated session. Every policy fails closed → every
   `SELECT` returns zero rows.
2. **`SUPABASE_SERVICE_ROLE_KEY` is the dev/operator escape hatch.** When
   present, `getSupabaseServerClient()` uses it; service-role bypasses
   RLS. This is acceptable on the operator's own machine and is gated
   behind a server-only env var (no `NEXT_PUBLIC_` prefix → never
   bundled into the browser).
3. **Browser-side Supabase reads do not work yet.** `getSupabaseAnonClient()`
   exists for completeness, but until Auth lands and we issue
   per-session JWTs, every anon-key query is RLS-denied. Phase 1C's
   data layer therefore only ever calls `getSupabaseServerClient()`
   from server components.
4. **No writes.** Phase 1C only reads. Writes (operator status
   transitions, client approvals, feedback) land in Phase 1D / 1E
   together with Auth so we can policy-gate them properly.
5. **`comments` + `clientRequests` are stubs.** The Phase 1B seed has
   zero rows in `content_feedback` and `content_requests`, and the
   Phase 1A UI does not yet render those threads from the data layer.
   They join the data layer when the relevant pages need them.

## Phase 1D readiness — what unlocks when auth lands

When Supabase Auth is wired:

1. `app.current_profile_id()` starts returning real `auth.uid()` values
   → existing RLS policies start filtering by workspace / portal
   membership instead of fail-closed.
2. `getSupabaseServerClient()` can drop the service-role fallback for
   normal operator requests (keep it only for break-glass admin paths).
3. `getSupabaseAnonClient()` becomes usable from `"use client"` modules,
   which unlocks live realtime / optimistic UI for client portal
   approvals.
4. The five deferred TODOs in `supabase/migrations/002_rls_policies.sql`
   become actionable (owner-only writes, client `SELECT` strictly via
   the view, profile auto-create trigger, per-column update block,
   JWT-claim helper for share-link flow).

Until then, the supabase branch is **reachable, type-safe, and
self-consistent** with the demo branch — but practical use requires
either the service-role escape hatch or a follow-up auth phase.

## Verification commands

```bash
# TypeScript + bundle
cd web
npm run typecheck
npm run build

# Python lint + targeted tests (unchanged by Phase 1C)
py -3.11 -m ruff check .
py -3.11 -m pytest tests/test_enhancor_smoke_payloads.py -v

# Working tree
git status --short
```

The Phase 1C diff is additive: the only modified files are
`web/lib/data/_source.ts`, `brands.ts`, `campaigns.ts`, `content.ts`,
`web/package.json`, `web/package-lock.json`, and `web/README.md`. New
files: `web/lib/supabase/{client,types}.ts`,
`web/lib/data/mappers.ts`, `web/.env.example`, and this document.
