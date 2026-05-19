# Yuvo OS — Phase 1F: Generation job tables + job dashboard (dry-run only)

Phase 1F adds the **job tracking system** that Phase 1G will fill with
real Enhancor / Seedance submissions. After Phase 1F:

- Every approved prompt version can be turned into a `generation_jobs`
  row from a single operator click in the prompt editor.
- The operator has a dedicated `/agency/jobs` dashboard listing every
  generation job, with status, estimated/actual credits, provider mode,
  brand/campaign/content item, and per-row Cancel + Mark queued (mock)
  controls.
- Each job has a detail page (`/agency/jobs/[jobId]`) showing the prompt
  version summary, raw request placeholder, status timeline,
  cost estimate, Audio Fixer panel (disabled, manual-only), and a notes
  composer.
- The schema includes the full surface area Phase 1G will need:
  `generation_batches`, `generation_jobs`, `generation_job_events`,
  `audio_fixer_jobs`, plus three new `generated_assets` kinds
  (`static_image`, `caption_pack`, `post_creative`).

Phase 1F is **DRY-RUN ONLY**. Every action in
`web/lib/actions/generation-jobs.ts` only writes to the new tables —
none of them performs a paid Enhancor / Seedance / Audio Fixer call.

## What ships in Phase 1F

| Asset | Path | Purpose |
|---|---|---|
| Migration | `supabase/migrations/005_generation_jobs.sql` | Four new tables + extended `generated_assets` + three helper functions + RLS |
| Seed | `supabase/seed.sql` (extended) | One historical 1080p Pai batch/job (completed) + one mock 720p next-week batch/job (ready/draft) + 1 audio_fixer_jobs row |
| Reader | `web/lib/data/generation-jobs.ts` | `listAllGenerationJobs`, `listGenerationJobsForCampaign`, `listGenerationJobsForContentItem`, `listGenerationJobsForPromptVersion`, `getGenerationJob`, `getGenerationBatch`, `listGenerationJobEvents`, `listAudioFixerJobsForGenerationJob`, demo store |
| Actions | `web/lib/actions/generation-jobs.ts` | `createMockGenerationJobFromPromptVersionAction`, `markGenerationJobQueuedAction`, `cancelGenerationJobAction`, `addGenerationJobNoteAction` |
| Component | `web/components/content/generation-job-actions.tsx` | `JobRowActions`, `JobMarkQueuedButton`, `JobCancelButton`, `JobNoteForm` |
| Component | `web/components/content/create-mock-generation-job-button.tsx` | Prompt-editor button: visible only when status=`approved_for_generation` |
| Route | `web/app/agency/jobs/page.tsx` | List of all jobs across the workspace |
| Route | `web/app/agency/jobs/[jobId]/page.tsx` | Job detail (prompt summary + timeline + cost panel + Audio Fixer placeholder) |
| Helper | `web/lib/quality-tiers.ts` (extended) | `getEstimatedSeedanceCredits`, `getEstimatedAudioFixerCredits` |
| Sidebar | `web/components/layout/agency-sidebar.tsx` | New "Generation jobs" nav link |
| Prompt editor | `web/app/agency/campaigns/[campaignId]/content/[contentId]/prompt/page.tsx` | New "Generation jobs" card (Create mock job + jobs-from-this-version list) |

## Data model

### `generation_batches`

One operator action ↔ one batch. Phase 1F creates exactly one job per
batch; Phase 1G+ may fan out to multiple resolutions / platform variants.

```
id                       uuid pk
workspace_id             uuid → workspaces(id)
brand_id                 uuid → brands(id)
campaign_id              uuid → campaigns(id)
created_by               uuid → profiles(id) nullable
label                    text
status                   'draft' | 'ready' | 'queued' | 'processing' |
                         'completed' | 'failed' | 'cancelled'
total_estimated_credits  integer
total_actual_credits     integer
created_at, updated_at   timestamptz
```

### `generation_jobs`

One requested generation from one prompt version. `raw_asset_id` is the
generated_assets row that holds the resulting raw video once Phase 1G
writes it (null in Phase 1F except for the historical seed).

```
id                    uuid pk
batch_id              uuid → generation_batches(id) on delete cascade
content_item_id       uuid → content_items(id) on delete restrict
prompt_version_id     uuid → prompt_versions(id) on delete restrict
provider              'enhancor_seedance' | 'enhancor_audio_fixer' |
                      'enhancor_other' | 'mock'
provider_mode         text
quality_tier          'draft_480p' | 'standard_720p' | 'premium_1080p'
resolution            '480p' | '720p' | '1080p' nullable
duration_seconds      integer nullable
status                'draft' | 'queued' | 'submitted' | 'processing' |
                      'completed' | 'failed' | 'cancelled'
estimated_credits     integer
actual_credits        integer
provider_request_id   text nullable
result_url            text nullable
thumbnail_url         text nullable
raw_asset_id          uuid → generated_assets(id) nullable
error_message         text
raw_request_json      jsonb
raw_response_json     jsonb
created_at, updated_at timestamptz
```

### `generation_job_events`

Append-only timeline. No update / delete path in the Phase 1F actions.

```
id                  uuid pk
generation_job_id   uuid → generation_jobs(id) on delete cascade
event_type          'created' | 'queued' | 'submitted' | 'status_polled' |
                    'completed' | 'failed' | 'cancelled' | 'operator_note'
message             text
raw_payload         jsonb
created_at          timestamptz
```

### `audio_fixer_jobs` (manual-only)

Audio Fixer is opt-in. An `audio_fixer_jobs` row only exists when the
operator explicitly clicks the Audio Fixer button in a later phase.
Phase 1F never auto-creates one; the seed inserts a single completed
row for parity with the historical 1080p Pai run.

```
id                    uuid pk
generation_job_id     uuid → generation_jobs(id) on delete cascade
input_asset_id        uuid → generated_assets(id) nullable
provider              'enhancor_audio_fixer' | 'mock'
status                'not_needed' | 'available' | 'queued' | 'submitted' |
                      'processing' | 'completed' | 'failed' |
                      'skipped_by_operator'
estimated_credits     integer
actual_credits        integer
provider_request_id   text
result_url            text
output_asset_id       uuid → generated_assets(id) nullable
error_message         text
raw_request_json      jsonb
raw_response_json     jsonb
created_at, updated_at timestamptz
```

### `generated_assets` (extended)

Three new kinds added to the CHECK enum (no rows yet; Phase 1G writes
the first ones):

```
- static_image  — single-frame product still
- caption_pack  — packaged set of platform captions (text only)
- post_creative — assembled post creative (video + caption pack)
```

Two new nullable FK columns:

```
generation_job_id    uuid → generation_jobs(id) nullable
audio_fixer_job_id   uuid → audio_fixer_jobs(id) nullable
```

The Phase 1F seed backfills these on the existing Pai 1080p artefacts
so the job detail page shows the asset linkage out of the box.

## Statuses

| Status | What it means | Reachable from | Phase 1F behaviour |
|---|---|---|---|
| `draft` | Operator created the job; nothing has happened yet. | `createMockGenerationJobFromPromptVersionAction` | Default for every new job. |
| `queued` | Operator clicked Mark queued (mock). | `markGenerationJobQueuedAction` | No paid call. Phase 1G replaces this transition with the real submission. |
| `submitted` | Provider acknowledged the request. | (Phase 1G) | Never set in Phase 1F. |
| `processing` | Provider is rendering. | (Phase 1G) | Never set in Phase 1F. |
| `completed` | Result + thumbnail recorded. | (Phase 1G; seed has one historical row) | Only the historical Pai 1080p seed row uses this. |
| `failed` | Provider returned an error. | (Phase 1G) | Never set in Phase 1F. |
| `cancelled` | Operator cancelled. | `cancelGenerationJobAction` | Allowed from any non-terminal status. |

## Dry-run / mock scope

The four operator actions in `web/lib/actions/generation-jobs.ts`:

1. `createMockGenerationJobFromPromptVersionAction(promptVersionId)`
   - Requires the source prompt version to be `approved_for_generation`.
   - Creates a `generation_batches` row with `status='ready'` and a
     single `generation_jobs` row with `status='draft'`.
   - Estimates credits via `getEstimatedSeedanceCredits(qualityTier, durationSec)`.
   - Appends a `created` event to `generation_job_events`.
   - **No paid call.**

2. `markGenerationJobQueuedAction(jobId)`
   - Flips status `draft → queued`.
   - Appends a `queued` event.
   - **No paid call.**

3. `cancelGenerationJobAction(jobId, reason?)`
   - Flips status to `cancelled` from any non-terminal status.
   - Appends a `cancelled` event with the reason.

4. `addGenerationJobNoteAction(jobId, message)`
   - Appends an `operator_note` event. Append-only — no update or delete.

## Why no paid calls yet

The job rows are the *first* place Phase 1G can write a
`provider_request_id` and `raw_request_json`. Landing the schema +
dashboard + UX in Phase 1F means Phase 1G's diff is small and surgical:
flip the `markGenerationJobQueuedAction` body from "update status to
queued" to "POST to Enhancor, then update status to submitted + store
provider_request_id". Everything else — the timeline, the cost panel,
the Audio Fixer placeholder, the asset linkage — is already plumbed.

It also keeps the failure modes contained. With Phase 1F shipped:

- The operator can stress-test the dashboard against the Pai historical
  seed before any credits are at risk.
- The status-transition rules can be exercised end-to-end (draft →
  queued → cancelled) without a network round trip.
- The dry-run "Create mock generation job" button gives QA a stable
  starting state for the prompt-version → job linkage.

## Quality-tier cost model

Phase 1F adds two named helpers in `web/lib/quality-tiers.ts`:

```
getEstimatedSeedanceCredits(qualityTier, durationSec)
  draft_480p     15s →  1230
  standard_720p  15s →  2646  (default)
  premium_1080p  15s →  5940

getEstimatedAudioFixerCredits(durationSec)
  ≈ 2100 (flat, per the Pai 15s reference — real run 2103.75 → 2104 rounded)
```

`getEstimatedSeedanceCredits` is the canonical estimator for Phase 1F+
because the resulting numbers feed `generation_jobs.estimated_credits`.
`estimateGenerationCredits` (Phase 1A) is kept as the underlying
implementation; the Phase 1F alias just gives the operator-side code a
provider-named entry point.

`getEstimatedAudioFixerCredits` is intentionally NOT added on top of
the Seedance estimate in the cost panel. Audio Fixer is manual; it
appears as a separate line item.

## Manual Audio Fixer policy

Phase 1F renders Audio Fixer as a separate side-panel on the job detail
page, with:

- A status badge (`Available (manual)` when no `audio_fixer_jobs` row
  exists for the job, otherwise the row's actual status).
- The flat 2,100-credit estimate, surfaced as a separate line below
  the Seedance estimate.
- A disabled "Run Audio Fixer later" button. The real runner ships
  with Phase 1G+ and remains manual-only — the dashboard never
  auto-creates an audio_fixer_jobs row.

## RLS posture

Every new table:

- RLS enabled.
- Operator full access via workspace gate
  (`app.is_workspace_member(app.workspace_id_for_*())`).
- **No client policy.** The four new tables are operator-side; the
  client portal never reads them.

`generated_assets` keeps its existing migration-002 policies unchanged.
The new kinds (`static_image`, `caption_pack`, `post_creative`) and
the new nullable FK columns do not expand client visibility — the
client policy still filters on `kind = 'thumbnail'`.

Three new SECURITY DEFINER helpers (all `set search_path = public`):

```sql
app.workspace_id_for_generation_batch(target uuid) returns uuid
app.workspace_id_for_generation_job(target uuid) returns uuid
app.workspace_id_for_audio_fixer_job(target uuid) returns uuid
```

Each walks one FK chain back to `generation_batches.workspace_id`. They
are created **after** the tables (not before) so PostgreSQL's default
`check_function_bodies = on` validates them cleanly.

## Service-role footprint

Phase 1F **adds** service-role writes to:

- `generation_batches` (insert + update)
- `generation_jobs` (insert + update)
- `generation_job_events` (insert)

The pattern matches Phase 1D / 1E: the action validates the persona is
operator + reads the FK chain to prove workspace ownership, then
writes via service-role. Once per-row UPDATE policies for the operator
persona land in a future migration (rolled-over TODO from Phase 1B),
these writes can move off service-role.

## Demo limitations

In demo mode (`NEXT_PUBLIC_DATA_SOURCE !== "supabase"`):

- `generation_batches`, `generation_jobs`, `generation_job_events`,
  `audio_fixer_jobs` are seeded in-memory in
  `web/lib/data/generation-jobs.ts` with the same two batches +
  two jobs + three events + one audio_fixer_job that
  `supabase/seed.sql` writes.
- Created jobs from the prompt editor push into the same in-memory
  store; they reset on dev server restart.
- All operator-side actions succeed without auth in demo mode (the
  persona resolver returns null and the actions skip the operator
  check). This is documented inline.
- Job-detail brand/campaign lookups go through `web/lib/demo-data.ts`
  in both modes — Phase 1G will move them to the real content_items
  data layer.

## Local testing notes

Without the Supabase CLI installed, `005_generation_jobs.sql` has not
been applied locally. To apply against a hosted project:

1. Open the Supabase SQL editor.
2. Paste + run `005_generation_jobs.sql`.
3. Verify the tables + helper functions exist:
   ```sql
   select count(*) from public.generation_batches;       -- 0 on fresh apply
   select count(*) from public.generation_jobs;          -- 0
   select count(*) from public.generation_job_events;    -- 0
   select count(*) from public.audio_fixer_jobs;         -- 0
   select pg_get_functiondef('app.workspace_id_for_generation_job'::regproc);
   ```
4. Re-run `seed.sql`. The `on conflict (id) do nothing` guards make
   re-applying safe.

## End-to-end demo script

```bash
cd web
npm run dev
```

1. Visit `/agency/jobs`. You see two seeded jobs:
   - `historical-pai-1080p-15s` → **Completed** · 5,940 cr actual.
   - the mock 720p next-week job → **Draft** · 2,646 cr estimate.
2. Click **View job** on the draft. The detail page renders the prompt
   version summary, the empty raw-request placeholder, the timeline
   (one `created` event), the cost panel (2,646 cr base · 2,100 cr
   Audio Fixer optional), and the disabled "Run Audio Fixer later"
   button.
3. Click **Mark queued (mock)** in the side panel. Status flips to
   *Queued (mock)* and a `queued` event is appended.
4. Add a note ("Watch label rendering on first take") and click
   **Add note** — the event appears at the bottom of the timeline.
5. Click **Cancel job** — status flips to *Cancelled* and a final
   `cancelled` event is appended.
6. Open the prompt editor at
   `/agency/campaigns/camp_pai_route01/content/content_pai_route02_draft/prompt`.
   The 720p version v1 is in `operator_editing`; the new
   **Generation jobs** card explains the button is locked until
   approve.
7. Click **Mark approved for generation**. Refresh — the **Create
   mock generation job** button is now visible. Click it; a flash
   appears with an *Open job →* link. Follow it to the freshly-
   created job's detail page.

## What Phase 1G will add

| Item | Why |
|---|---|
| Real Enhancor / Seedance submission inside `markGenerationJobQueuedAction` | The dry-run flip becomes a real provider call. status: draft → submitted, raw_request_json + provider_request_id written. |
| Status-polling webhook handler + polling action | Update status to `processing` / `completed` / `failed`, append `status_polled` + `completed` events. |
| Real `audio_fixer_jobs` action (still manual-only) | The disabled "Run Audio Fixer later" button gets a real onClick. |
| Cost ledger table | Aggregate `actual_credits` across `generation_jobs` + `audio_fixer_jobs` per workspace per month (Phase 1G or 1H). |
| `content_items.active_prompt_version_id` FK | Rolled-over TODO from Phase 1E. |
| Per-row UPDATE policy on `content_items.status` for the client persona | Rolled-over TODO from Phase 1D. |
| Owner-only enforcement on `workspace_members` writes | Rolled-over TODO from Phase 1B. |
| Realtime updates on `generation_jobs` | Dashboard reflects provider polls without manual refresh. |

## Verification

```bash
cd web
npm run typecheck
npm run build

# Repo root
py -3.11 -m ruff check .
py -3.11 -m pytest tests/test_enhancor_providers.py tests/test_enhancor_smoke_payloads.py -v
```

No paid API calls. No deploy. No commit.
