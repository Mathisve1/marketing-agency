# Yuvo OS — Phase 1H: Job-run ingestion into Supabase

Phase 1G's `scripts/run_generation_job.py` drops every wire payload
under `prospects/pai-skincare/production/dashboard_job_runs/<job-id>/`.
Phase 1H adds the second half of the split-credential model: a separate
ingester that reads those artefacts and writes back to Supabase. Audio
Fixer remains manual and disabled; no paid API call is made by this
script.

## Architecture

Two scripts, two credential sets:

| Script | Credentials | Talks to | Blast radius |
|---|---|---|---|
| `scripts/run_generation_job.py` (Phase 1G) | `ENHANCOR_API_KEY` only | Enhancor API | Can spend credits |
| `scripts/ingest_generation_job_run.py` (Phase 1H) | `SUPABASE_SERVICE_ROLE_KEY` only | Supabase REST | Writes to DB |

The ingester never imports the Enhancor adapter package and never opens
an outbound connection except to Supabase. Re-running it on the same
artefact folder is a no-op thanks to deterministic UUIDv5 ids on the
event + asset rows.

### Why two scripts (not one `--ingest` flag)

- **Single-responsibility credentials.** Neither script holds both
  keys; an exploit in one path can't escalate into the other.
- **Idempotent re-runs.** The ingester can run repeatedly after a
  manual edit to the artefact JSON (e.g. fixing a typo) without any
  risk of re-submitting to Enhancor.
- **Failure containment.** A bad Supabase write doesn't roll back a
  successful Seedance generation; a bad poll doesn't corrupt a
  half-written ingest.
- **Operator mental model.** Two commands map cleanly to two phases
  ("did the API call succeed?" / "did the dashboard pick it up?").

## Artefact folder contract

```
prospects/pai-skincare/production/dashboard_job_runs/
└── <job-id>/
    ├── payload.json              # every --dry-run / --submit (Phase 1G)
    ├── submit_response.json      # successful --submit (Phase 1G)
    ├── submit_error_<UTC>.json   # failed --submit (Phase 1G)
    ├── poll_<UTC>.json           # every --poll (Phase 1G), idempotent
    ├── poll_error_<UTC>.json     # failed --poll (Phase 1G)
    ├── result.mp4                # successful --download (Phase 1G)
    └── result_meta.json          # successful --download (Phase 1G)
```

The ingester:

- **Requires** at least one of these files to be present, otherwise
  it exits with a FATAL.
- **Tolerates** any subset. Running `--dry-run` after only Phase 1G's
  `--dry-run` produces a single `raw_request_json` PATCH and no
  events (no `submitted` event yet because no submission happened).
- **Sorts** the `poll_*.json` files lexicographically (the UTC tag in
  the filename ensures chronological order) and treats the last one
  as the latest status.

## Commands

```bash
# Preview the planned mutations. No Supabase writes. Default + safe.
py -3.11 scripts/ingest_generation_job_run.py --job-id <jobId> --dry-run

# Apply. Requires NEXT_PUBLIC_SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
# in .env. Idempotent — re-running writes the same deterministic ids.
py -3.11 scripts/ingest_generation_job_run.py --job-id <jobId> --apply
```

`--dry-run` and `--apply` are mutually exclusive and **required**.
There is no default mode; running the script with neither is a CLI
error so the operator can't accidentally apply without thinking about
it.

## Supabase service-role safety

- `agents/producer/dashboard/supabase_jobs.py` reads
  `NEXT_PUBLIC_SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` from `.env`
  via `python-dotenv` (no argv).
- Every request goes out with `apikey:` + `Authorization: Bearer …`
  headers; both header names are added to the redaction list used by
  the existing `redact_api_key_headers` helper from
  `agents/producer/providers/base.py`. Debug logs never print the raw
  key.
- The service-role key is **server-only**. Nothing under `web/`
  imports `supabase_jobs.py`; it lives in the Python package because
  Python is where the operator already keeps the operator-only
  secrets. The TS supabase client (`web/lib/supabase/*`) continues to
  use the anon key.
- The ingester refuses to run `--apply` when the env vars are missing
  and points the operator at `web/.env.example`.

## Mutation map

| Disk artefact | Becomes | Notes |
|---|---|---|
| `payload.json` | `generation_jobs.raw_request_json` | PATCH |
| `submit_response.json` → `provider_job_id` | `generation_jobs.provider_request_id` | PATCH |
| `submit_response.json` → `raw_response` | `generation_jobs.raw_response_json` | PATCH |
| `submit_response.json` | `generation_job_events` row, `event_type='submitted'` | INSERT, deterministic id |
| `poll_*.json` (each) | `generation_job_events` row, `event_type='status_polled'` | INSERT per file |
| `poll_*.json` (latest) → `status` | `generation_jobs.status` (COMPLETED→completed / FAILED→failed / QUEUED→submitted / IN_PROGRESS→processing) | PATCH |
| `poll_*.json` (latest) → `result_url` / `thumbnail_url` / `cost` | `generation_jobs.result_url` / `thumbnail_url` / `actual_credits` | PATCH; cost rounded to int credits |
| `poll_*.json` (latest, FAILED) → `error_message` | `generation_jobs.error_message` | PATCH |
| Terminal poll | One `generation_job_events` row, `event_type='completed'` or `'failed'` | INSERT, deterministic id |
| `result.mp4` (+ ffprobe metadata) | `generated_assets` row, `kind='raw_video'`, with `generation_job_id` + content_item_id + byte_size + duration_sec + resolution + mime | INSERT, deterministic id |
| Newly inserted `generated_assets.id` | `generation_jobs.raw_asset_id` | PATCH |

`generation_jobs` columns we never overwrite with NULL — fields with
no on-disk evidence are simply left out of the PATCH body.

### Idempotency

Deterministic UUIDv5 ids derived from the per-job namespace `00…beef`:

```
event_uuid_for(job_id, source_path, event_type)
asset_uuid_for(job_id, kind, source_path)
```

Combined with PostgREST's `Prefer: resolution=ignore-duplicates`
header, re-running `--apply` against the same folder skips inserts
that already landed.

## MP4 metadata helper

`agents/producer/dashboard/mp4_meta.py::probe_mp4()` shells out to
`ffprobe` when it's on PATH. The result populates
`generated_assets.byte_size / duration_sec / resolution / mime` plus
the `generation_job_events.raw_payload` for the terminal event so the
operator can audit codec / width × height / audio-track presence in
the dashboard timeline.

When `ffprobe` is missing, only `byte_size` + `mime` are captured. The
ingester prints a NOTE and continues — the asset row is still
inserted, just with less metadata. Install `ffmpeg` (which bundles
`ffprobe`) to get the full record.

## Dashboard display updates

`/agency/jobs/[jobId]` gained:

- **Raw video asset (Phase 1H ingested)** card, shown only when a
  `generated_assets` row with `kind='raw_video'` and
  `generation_job_id = <this job>` exists. Lists asset id, kind,
  storage path, provider URL, size, duration, resolution, MIME.
- The Audio Fixer card now has two visibly distinct states:
  - Pre-completion: badge "Available (manual) — waiting on a completed
    raw video"; disabled "Run Audio Fixer later" button.
  - Post-ingestion (status=completed AND raw video present): badge
    "Available (manual) — unlocks in Phase 1I"; disabled "Run Audio
    Fixer in Phase 1I" button. The descriptive copy explicitly states
    that Phase 1H surfaces the option, Phase 1I will wire the (still
    manual) submission flow.
- The intro paragraph at the top of the detail page now points at
  `scripts/ingest_generation_job_run.py` for the ingestion side.

`web/lib/data/generation-jobs.ts` gained:

- New `GeneratedAsset` type + `GeneratedAssetKind` union.
- `listGeneratedAssetsForGenerationJob(jobId)` reader. Demo mode
  returns `[]`; supabase mode SELECTs from `generated_assets` filtered
  by `generation_job_id`. The detail page calls it in parallel with
  the other readers.

## Known limitations

1. **content_item_id still resolves through the demo catalogue.** The
   `generated_assets` insert needs a `content_item_id` (NOT NULL); the
   ingester looks it up via `agents/producer/dashboard/demo_jobs.py`
   today. Phase 1I will swap this for a Supabase SELECT against
   `generation_jobs.content_item_id`.
2. **No Supabase Storage upload.** `storage_path` is the operator's
   local FS path. The dashboard never serves the MP4. Clients only ever
   see the safe thumbnail URL on `content_items.client_safe_poster_url`,
   which Phase 1H does NOT touch.
3. **`event.raw_payload` is the only place the ffprobe report lands
   beyond the canonical columns.** The terminal `completed` event
   carries a compact metadata dict; the full ffprobe output stays on
   the operator's machine.
4. **No automatic schema migration.** The script trusts the Phase 1F
   schema (migration 005) is in place. Running against a project
   without migration 005 yields PostgREST 404s; the FATAL message is
   clear enough to send the operator to the migrations folder.
5. **No retries.** A single Supabase write failure aborts the run.
   Operators can re-run `--apply`; the deterministic ids guarantee
   idempotency.

## Audio Fixer remains manual

Phase 1H **does not**:

- Auto-create an `audio_fixer_jobs` row.
- Chain an Audio Fixer call after `--apply`.
- Re-enable the dashboard's "Run Audio Fixer later" button.

The wire-protocol adapter
(`agents/producer/providers/enhancor_audio_fixer.py`) remains
untouched. Phase 1I will wire a parallel CLI
(`run_audio_fixer_job.py`?) behind the same typed-`SUBMIT`
confirmation gates Phase 1G uses for Seedance.

## What Phase 1I will add

| Item | Why |
|---|---|
| Supabase-backed `find_supabase_job(job_id)` in `agents/producer/dashboard/` | Lets the ingester (and runner) read any `generation_jobs` row, not just the two seeded ones |
| Manual Audio Fixer flow: `scripts/run_audio_fixer_job.py` + dashboard panel re-enable | Same confirmation gates as Phase 1G; reads the raw video URL from the ingested `generated_assets` row |
| Supabase Storage upload of `result.mp4` | Replace the local FS `storage_path` with a bucket key + signed URL |
| `content_items.active_prompt_version_id` FK (rolled-over TODO from Phase 1E) | Lets operators pin a non-latest version |
| Per-row UPDATE policy on `content_items.status` for the client persona (rolled over from Phase 1D) | Drops the service-role escape hatch |

## Verification

```bash
cd web
npm run typecheck
npm run build

# Repo root
py -3.11 -m ruff check .
py -3.11 -m pytest tests/test_enhancor_providers.py tests/test_enhancor_smoke_payloads.py -v

# Safe dry-runs (no Enhancor call, no Supabase writes):
py -3.11 scripts/run_generation_job.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b --dry-run
py -3.11 scripts/ingest_generation_job_run.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b --dry-run
```

NO paid API call. NO deploy. NO commit. NO push.

## Exact commands for the real flow (when manually approved)

```bash
# 1) Submit (PAID — spends ≈2,646 credits for 15s @ 720p):
py -3.11 scripts/run_generation_job.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \
  --product-url https://<your-cdn>/pai-bottle.jpg \
  --influencer-url https://<your-cdn>/pai-creator.jpg \
  --webhook-url https://<your-hooks>/enhancor/seedance \
  --submit --confirm

# 2) Poll (no extra credits):
py -3.11 scripts/run_generation_job.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b --poll

# 3) Download result.mp4 (no extra credits):
py -3.11 scripts/run_generation_job.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b --download

# 4) Ingest into Supabase (no credits, idempotent):
py -3.11 scripts/ingest_generation_job_run.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b --apply
```

After step 4, `/agency/jobs/1b1b…` shows the Raw video asset card with
the ingested metadata, the cost panel reports the actual credits, the
timeline gains `submitted`, one or more `status_polled`, and a
terminal `completed` event, and the Audio Fixer panel flips into its
"unlocks in Phase 1I" state.
