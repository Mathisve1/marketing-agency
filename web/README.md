# Yuvo Studio — `web/` (Phase 1A/1B/1C/1D/1E/1F/1G/1H/1I)

Internal **agency operator dashboard** + private **client approval portal**
for the Yuvo Creative OS. Phase 1A is a **visual shell only** — local-state
buttons, mocked Pai demo data, no paid API calls. Phase 1B adds the
Supabase **schema, RLS policies, and Pai seed** plus a data-access
abstraction (`lib/data/*`). Phase 1C wires the **runtime Supabase
client**. Phase 1D wires **Supabase Auth (magic-link)** and
**persisted client approvals + comments**. Phase 1E adds the
**prompt-versioning UI**, the **regenerate-request workflow**, and
wires the previously-inert **next-week content request form**. Phase 1F
adds the **generation job tracking system** (tables + dashboard) —
still **dry-run / mock only**: no paid Enhancor / Seedance / Audio
Fixer call is made from the dashboard process. Phase 1G **wires the
Enhancor Seedance provider behind an operator-driven CLI bridge**: the
dashboard prints the exact `py -3.11 scripts/run_generation_job.py …`
command, gated behind a typed `SUBMIT` confirmation; the operator
runs it locally; the script saves every wire payload to disk for
Phase 1H to ingest. Phase 1H **adds the Python → Supabase ingester**:
a separate `scripts/ingest_generation_job_run.py` reads the per-job
artefact folder, probes the downloaded MP4 via ffprobe (with
graceful fallback), and writes back to `generation_jobs` +
`generation_job_events` + `generated_assets`. The ingester defaults
to dry-run, never calls Enhancor, and is idempotent via
deterministic UUIDv5 ids. Phase 1I **lands a stdlib MP4 atom-parser
fallback** in `agents/producer/dashboard/mp4_meta.py` so duration /
resolution / audio-track / codec metadata populate even without
ffprobe, and **adds the manual Audio Fixer runner**
(`scripts/run_audio_fixer_job.py`) following the same operator-driven
CLI bridge pattern as Phase 1G — Phase 1I exercises only `--dry-run`,
never `--submit`.

## Status

- ✅ Routes scaffolded
- ✅ Pai demo data seeded (`lib/demo-data.ts`)
- ✅ Quality tiers wired to the dashboard (default = Standard 720p)
- ✅ Audio Fixer is **manual-only** in the UI
- ✅ Client portal is cost-free and internals-free
- ✅ Supabase migrations + RLS + Pai seed authored (Phase 1B — see
  `../docs/dashboard_phase_1b_supabase.md`)
- ✅ Data-access layer `lib/data/{brands,campaigns,content}.ts` with
  `NEXT_PUBLIC_DATA_SOURCE` feature flag (default `demo`)
- ✅ Runtime `@supabase/supabase-js` client + supabase branch of data
  layer (Phase 1C — see
  `../docs/dashboard_phase_1c_supabase_data_access.md`)
- ✅ Supabase Auth (magic-link) for operators + clients; client
  approvals, change requests, and comments persist to Supabase
  (Phase 1D — see `../docs/dashboard_phase_1d_auth_feedback.md`)
- ✅ Prompt-versioning UI + regenerate-request workflow + next-week
  request form wired (Phase 1E — see
  `../docs/dashboard_phase_1e_prompt_regeneration.md`)
- ✅ Generation job tables (`generation_batches`, `generation_jobs`,
  `generation_job_events`, `audio_fixer_jobs`) + `/agency/jobs`
  dashboard (Phase 1F — see
  `../docs/dashboard_phase_1f_generation_jobs.md`) — DRY-RUN ONLY,
  no paid call.
- ✅ Operator-driven Enhancor Seedance wiring via
  `scripts/run_generation_job.py` (Phase 1G — see
  `../docs/dashboard_phase_1g_generation_provider_wiring.md`).
  Dashboard prints the command behind a typed-`SUBMIT` confirmation;
  operator runs it locally. **No paid call is ever initiated by the
  Next.js process.**
- ✅ Python → Supabase ingestion of job-run artefacts via
  `scripts/ingest_generation_job_run.py` (Phase 1H — see
  `../docs/dashboard_phase_1h_job_ingestion.md`). Defaults to
  `--dry-run`; `--apply` requires
  `SUPABASE_SERVICE_ROLE_KEY` in `.env`; idempotent via
  deterministic UUIDv5 ids on every inserted row.
- ✅ Dashboard surfaces ingested raw video assets in a new card on
  `/agency/jobs/[jobId]` once an ingest has landed.
- ✅ Stdlib MP4 atom-parser fallback in `mp4_meta.probe_mp4()` so
  `duration_sec` / `width` × `height` / `resolution` /
  `has_audio_track` / video+audio codec populate without `ffprobe`
  on PATH (Phase 1I — see
  `../docs/dashboard_phase_1i_manual_audio_fixer.md`).
- ✅ Manual Audio Fixer runner `scripts/run_audio_fixer_job.py`
  (Phase 1I). `--dry-run` only in this phase; `--submit / --poll /
  --download` are implemented for future operator use but Phase 1I
  never invokes them. The dashboard's Audio Fixer card now exposes
  the dry-run command unconditionally + a typed-`AUDIO-FIXER` gate
  on the (still locked) paid command.
- ❌ Per-policy UPDATE on `content_items.status` for the client
  persona — Phase 1D uses the service-role escape hatch for the
  status flip; rolled forward as a Phase 1J TODO
- ❌ Audio Fixer paid call (stays manual; never auto-triggered)

## Env vars

| Var | Required | Notes |
|---|---|---|
| `NEXT_PUBLIC_DATA_SOURCE` | no | `demo` (default) or `supabase` |
| `NEXT_PUBLIC_SUPABASE_URL` | only in supabase mode | Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | only in supabase mode | Public anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | required in supabase mode for writes | Server-only; bypasses RLS. Phase 1D needs it for the feedback write path |
| `NEXT_PUBLIC_SITE_URL` | recommended in supabase mode | Origin for magic-link callback. Defaults to `http://localhost:3000` |

See `.env.example` for the canonical copy.

## Auth status

- Operator and client login both use **magic-link OTP** via
  `supabase.auth.signInWithOtp`.
- Routes: `/login` (operator), `/client/<portalSlug>/login` (client).
- Callback: `/auth/callback?code=…&next=…` exchanges the code and sets
  the Supabase session cookie.
- In demo mode the login pages render a "demo mode" notice and the
  app keeps working without auth.

## How to test the client approval flow

### Demo mode (no Supabase needed)

```bash
cd web
npm install
npm run dev
```

1. Open `/client/pai-skincare-demo/content/<id>` (link from the portal home).
2. Click **Approve**, **Request changes** + reason, **Add comment**.
3. The feedback panel persists to an in-memory store for the current
   server process. Open `/agency/campaigns/<id>/outputs` in another
   tab to see the same items reflected in the `ClientFeedbackSummary`.

### Supabase mode

1. Apply migrations 001 + 002 + 003 and `seed.sql` to your Supabase
   project (SQL editor flow documented in
   `../docs/dashboard_phase_1d_auth_feedback.md`).
2. Copy `.env.example` to `.env.local`, fill in the Supabase env vars +
   `SUPABASE_SERVICE_ROLE_KEY` + `NEXT_PUBLIC_SITE_URL`, set
   `NEXT_PUBLIC_DATA_SOURCE=supabase`.
3. Insert a pending portal member with your email:
   ```sql
   insert into public.client_portal_members (portal_id, invite_email)
   values ('55555555-5555-5555-5555-555555555555', 'you@example.com');
   ```
4. `npm run dev` → visit `/client/pai-skincare-demo/login`.
5. Enter your email, click the magic-link in your inbox.
6. After landing, click Approve / Request changes / Comment — the rows
   land in `content_approvals` + `content_feedback`.
7. Sign in to `/login` as the operator and visit
   `/agency/campaigns/.../outputs` to see the client's decision +
   thread in the feedback summary.

## Data source

The UI reads every entity through `lib/data/*`, not from
`lib/demo-data.ts` directly. The `NEXT_PUBLIC_DATA_SOURCE` env var picks
the backend:

| Value | Behaviour |
|---|---|
| `demo` (default) | Reads from `lib/demo-data.ts` — same render as Phase 1A |
| `supabase` | Live reads via `@supabase/supabase-js` against the schema in `supabase/migrations/*` |

Copy `.env.example` to `.env.local` and fill in `NEXT_PUBLIC_SUPABASE_URL`
+ `NEXT_PUBLIC_SUPABASE_ANON_KEY` (and optionally
`SUPABASE_SERVICE_ROLE_KEY` to bypass RLS until Phase 1D wires auth).

Client portal pages under `app/client/*` are restricted to the three
client-safe functions in `lib/data/content.ts`:
`getClientPortalBySlug`, `getClientVisibleContentItems`,
`getClientContentItem`. Operator-only fields (`cost_*`,
`prompt_summary`, internal asset paths, `quality_tier`, audio_fixer
details) can never leak to that surface — they are projected away by
`toClientContentView` and, on the DB side, by the
`client_content_items_v` view.

## Quick start

```bash
cd web
npm install      # one-time, ~1 minute
npm run dev      # http://localhost:3000
```

Then:

- **Operator view:** `http://localhost:3000/agency`
- **Client view:** `http://localhost:3000/client/pai-skincare-demo`

## Layout

| Path | View |
|---|---|
| `/` | landing + role-switch (agency / client) |
| `/agency` | operator home |
| `/agency/brands` | brand list |
| `/agency/brands/[brandId]` | brand detail |
| `/agency/campaigns/[campaignId]/calendar` | campaign content calendar |
| `/agency/campaigns/[campaignId]/outputs` | generated outputs |
| `/agency/campaigns/[campaignId]/content/[contentId]/prompt` | operator-only prompt-version editor (Phase 1E) |
| `/agency/jobs` | operator-only generation jobs dashboard (Phase 1F) |
| `/agency/jobs/[jobId]` | operator-only generation job detail (Phase 1F) |
| `/client/[portalSlug]` | client portal home |
| `/client/[portalSlug]/calendar` | client calendar |
| `/client/[portalSlug]/content/[contentId]` | one content item for review |

## What the operator can do

- Pick quality tier — Draft 480p / **Standard 720p** *(default)* / Premium 1080p
- See per-tier credit estimate (cost math from the Enhancor docs)
- Run Audio Fixer (manual only — never auto)
- Skip Audio Fixer
- Regenerate
- Share with client
- Approve / Request changes (mirrors the client side)
- Edit a content item's **prompt version** (hook, script, prompt body,
  negative prompt, scene plan, creator direction, product constraints,
  quality tier, notes) in the new prompt editor route. Save draft
  iterates the operator-side; **Mark approved for generation** records
  intent — Phase 1E does **not** trigger any paid Enhancor / Seedance /
  Audio Fixer call (Phase 1E — see
  `../docs/dashboard_phase_1e_prompt_regeneration.md`)
- See an **operator-only regenerate-request queue** per content item on
  the outputs page. Each client change-request opens a structured
  `regeneration_request` the operator can Accept, Dismiss (with an
  optional note back to the client thread), or fork into a new prompt
  version with one click.
- See incoming **next-week client requests** on the agency dashboard.
- See every **generation job** under `/agency/jobs` (status, brand,
  campaign, content, quality tier, estimated/actual credits, provider
  mode). Click into `/agency/jobs/[jobId]` for the timeline,
  cost-estimate panel, raw-request placeholder, and Audio Fixer side
  panel. From the prompt editor (when the latest version is
  *approved for generation*), click **Create mock generation job** to
  spawn a new `generation_jobs` row — Phase 1F never makes a paid
  call (see `../docs/dashboard_phase_1f_generation_jobs.md`).
- **Submit a real Enhancor Seedance generation** via the Phase 1G
  Submit-to-Enhancor panel on `/agency/jobs/[jobId]`. The dashboard
  prints the exact `py -3.11 scripts/run_generation_job.py …` command;
  the operator ticks "I understand this will spend credits", types
  `SUBMIT` to unlock the Copy button, replaces the placeholder URLs
  with real public HTTPS URLs, and runs the command in their terminal.
  Poll and Download commands appear once a `provider_request_id` is
  present. **No paid call is ever initiated by the Next.js process.**
  See `../docs/dashboard_phase_1g_generation_provider_wiring.md`.
- **Ingest a completed run back into Supabase** via Phase 1H's
  separate `scripts/ingest_generation_job_run.py`. After `--download`
  lands `result.mp4` + `result_meta.json`, run
  `py -3.11 scripts/ingest_generation_job_run.py --job-id <jobId> --dry-run`
  to preview the planned mutations, then `--apply` (requires
  `SUPABASE_SERVICE_ROLE_KEY` in `.env`) to write back. The dashboard
  picks the result up automatically — the new **Raw video asset**
  card shows storage path, size, duration, resolution, and MIME; the
  cost panel reports the actual credits; the timeline gains
  `submitted` / `status_polled` / `completed` events. See
  `../docs/dashboard_phase_1h_job_ingestion.md`.

## What the client can do

- See their calendar
- Play a content item
- **Approve**, **Request changes**, **Comment** — and now (Phase 1E)
  **Request next week's content** is actually wired: the textarea
  on the portal home and the per-item page write to
  `content_requests` and surface on the agency dashboard.
- See their own recent next-week requests echoed back on the portal
  home so they know what they've already sent us.
- Nothing else — no costs, no provider names, no internals, no
  prompt-version visibility, no operator-only regenerate-queue state.

## What we deliberately did NOT add

- shadcn CLI / Radix install — primitives are hand-written (≤50 lines each)
- Per-policy client UPDATE on `content_items` (rolled to Phase 1H)
- Any **dashboard-initiated** paid Enhancor / Seedance / Audio Fixer
  call. Phase 1G wires real Seedance submissions but exclusively
  through the operator's terminal via `scripts/run_generation_job.py`;
  the Next.js process never POSTs to a paid endpoint.
- Audio Fixer paid call (still manual; deferred past Phase 1G).
- next/image with optimisation (no Sharp dep)
- Video file moves — only the 48 KB Pai thumbnail copied into `public/demo/`
- npm install is the only step that requires the network; the source is complete first.
