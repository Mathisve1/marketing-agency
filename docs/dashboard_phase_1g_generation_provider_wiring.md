# Yuvo OS — Phase 1G: Generation provider wiring (operator-driven)

Phase 1G connects the Phase 1F `generation_jobs` row to a real Enhancor
Seedance submission — **but only via the operator's terminal**, never
from the Next.js dashboard process. The dashboard surfaces the exact
copy-paste command for each lifecycle stage; the operator runs it; the
script writes every wire payload to disk; Phase 1H will ingest those
artefacts back into Supabase.

## Backend bridge — chosen approach

**Hybrid of Option A (CLI bridge) and Option C (command-assisted).**

| Approach | Why we picked this one |
|---|---|
| Option C — dashboard prints the command, operator runs it locally | Picked. Smallest safe step. The dashboard process never spends credits accidentally; `ENHANCOR_API_KEY` stays in the operator's local `.env`. |
| Option A — Next.js server action calls Python via `child_process` | Rejected. A single misrouted route turns into spent credits. We don't want any path in the dashboard process whose execution implies a paid call. |
| Option B — FastAPI backend endpoint | Rejected for Phase 1G. Adds a long-running Python server, auth surface, deployment story. We'll revisit when the workload outgrows the CLI. |

The dashboard's job-detail page shows three commands: dry-run (always
unlocked, never spends credits), submit (locked behind two confirmation
gates), poll, and download.

## Script usage

```
# Inspect + validate the payload — NO API call:
py -3.11 scripts/run_generation_job.py --job-id <jobId> --dry-run

# PAID — real credits will be spent:
py -3.11 scripts/run_generation_job.py \
  --job-id <jobId> \
  --product-url https://your-cdn/pai-bottle.jpg \
  --influencer-url https://your-cdn/creator.jpg \
  --webhook-url https://your-hooks/enhancor/seedance \
  --submit --confirm

# Poll until terminal:
py -3.11 scripts/run_generation_job.py --job-id <jobId> --poll

# Download the result.mp4 + thumbnail metadata:
py -3.11 scripts/run_generation_job.py --job-id <jobId> --download
```

Mutually-exclusive required flags: `--dry-run | --submit | --poll | --download`.
Running the script with no mode is a CLI error — there is no "default
mode" that could surprise the operator.

### What lands on disk

Every run writes under
`prospects/pai-skincare/production/dashboard_job_runs/<job-id>/`:

| File | When |
|---|---|
| `payload.json` | every `--dry-run` and `--submit` |
| `submit_response.json` | every successful `--submit` |
| `submit_error_<UTC>.json` | every failed `--submit` |
| `poll_<UTC>.json` | every `--poll` (idempotent) |
| `poll_error_<UTC>.json` | every failed `--poll` |
| `result.mp4` | every successful `--download` |
| `result_meta.json` | every successful `--download` |

The folder is operator-only — never linked from the client portal.

## Dry-run flow

1. Operator clicks into `/agency/jobs/[jobId]`.
2. The page shows the dry-run command in the **Submit to Enhancor**
   card. The Copy button is always enabled for the dry-run line.
3. Operator pastes the command in a terminal.
4. The script loads `agents/producer/dashboard/demo_jobs.py`, finds the
   job, composes the Seedance UGC payload via
   `build_seedance_payload_from_job`, validates HTTPS / asset caps /
   duration enum / 1080p-fast_mode rule, prints the payload, saves it
   to `payload.json`.
5. No `ENHANCOR_API_KEY` is required for `--dry-run`. **No HTTP call.**

The dry-run prints a `warnings` array up front noting:

- "Placeholder URLs present — `--submit` will refuse until `--product-url`
  and `--influencer-url` are real."
- "Default placeholder webhook URL — pass `--webhook-url` before `--submit`."

## Submit flow

1. Operator ticks **I understand this will spend credits** and types
   `SUBMIT` in the dashboard's Submit-to-Enhancor card. Only with both
   gates passed does the Copy button unlock.
2. The displayed command embeds three placeholder URLs the operator
   must overwrite with real public HTTPS URLs (product, influencer,
   webhook).
3. Operator runs the command in their terminal.
4. The script:
   - Loads `ENHANCOR_API_KEY` from `.env` via `python-dotenv`.
   - Re-builds the payload from the job + real CLI URLs.
   - **Refuses to submit** if any URL contains
     `/PLACEHOLDER-` or if `--webhook-url` is the default placeholder.
   - **Refuses to submit** if `--confirm` is missing.
   - Prints the about-to-spend summary
     (`job_id / provider / quality_tier / resolution / duration /
     estimated_credits / products / influencers / webhook_url`).
   - POSTs to Seedance `/queue`. Header redacted via
     `redact_api_key_headers`.
   - On 2xx: saves `submit_response.json` with the `provider_job_id`
     (the Seedance `requestId`).
   - On non-2xx: saves `submit_error_<UTC>.json` and exits non-zero
     without printing the API key.

A successful `--submit` exits with a "Next step:" line printing the
`--poll` command.

## Poll flow

1. Operator clicks **Copy command** in the Poll & download card on the
   job detail page (unlocked the moment a `provider_request_id` is
   present + status is not terminal).
2. Operator runs the command.
3. The script reads `submit_response.json` to recover
   `provider_request_id`, POSTs to Seedance `/status`, and saves
   `poll_<UTC>.json`.
4. The script prints `status / result_url / thumbnail_url / cost`. Exit
   code is `0` for COMPLETED / non-terminal, `2` for FAILED.

The poll command is idempotent — running it twice writes two files.
Phase 1H will ingest the latest poll file back into
`generation_job_events` (status_polled) and update
`generation_jobs.status` + `actual_credits`.

## Download flow

1. After a `--poll` that reports `COMPLETED` with a `result_url`, the
   Download button on the dashboard unlocks.
2. Operator runs the `--download` command.
3. The script streams the result MP4 to `result.mp4` in the job folder
   via `EnhancorSeedanceProvider.download_result()`. Saves
   `result_meta.json` with the result/thumbnail URLs + provider cost.
4. Audio Fixer is NOT chained. The exit message reminds the operator
   that Phase 1H will ingest the downloaded artefacts into
   `generated_assets`.

## Confirmation policy

Three gates protect every paid call:

1. **`--submit` requires `--confirm`.** A bare `--submit` exits with a
   FATAL message explaining how to confirm.
2. **`--submit` refuses placeholder URLs.** The script imports
   `is_placeholder_url` from `agents/producer/dashboard/demo_jobs.py`
   and rejects any product/influencer URL containing
   `/PLACEHOLDER-`. The default webhook URL
   `https://example.com/webhooks/enhancor/seedance` is also refused.
3. **The dashboard's Copy-submit-command button stays locked** unless
   the operator both ticks "I understand this will spend credits" AND
   types `SUBMIT` (exact, case-sensitive) into the confirmation field.

Copying the command is not enough on its own — the operator still has
to paste it, replace the placeholder URLs, and press Enter. There is no
single click path from dashboard to spent credit.

## 720p default policy

`getEstimatedSeedanceCredits(qualityTier, durationSec)` (Phase 1F)
remains the cost helper. The dashboard's prompt editor defaults
`quality_tier` to `standard_720p`; the mock generation job inherits the
prompt version's tier; the runner re-emits the tier as the payload's
`resolution` field.

The script logs a WARNING when it sees `resolution == "1080p"` so the
operator double-checks intent before `--submit`. There is no auto-
promotion path from 720p to 1080p.

Reference numbers for a 15-second take:

| Tier | Credits |
|---|---|
| `draft_480p` | 1,230 |
| `standard_720p` | **2,646** (default) |
| `premium_1080p` | 5,940 |

## Manual Audio Fixer policy

Audio Fixer remains **opt-in and operator-driven**. Phase 1G does NOT:

- Auto-create an `audio_fixer_jobs` row.
- Chain an Audio Fixer call after a successful `--download`.
- Re-enable the "Run Audio Fixer later" button on the dashboard.

The dashboard's Audio Fixer card still shows:

- Status: `Available (manual)` when no `audio_fixer_jobs` row exists.
- The flat **≈2,100 credit estimate** (Pai 15s reference; real run was
  2,103.75 → 2,104 rounded), reported as a separate line in the cost
  panel and never added to the base total.
- The "Run Audio Fixer later" button **stays disabled**.

A separate, equally explicit confirmation flow will land in a later
phase. The wire-protocol adapter
(`agents/producer/providers/enhancor_audio_fixer.py`) already exists
and remains untouched.

## Demo limitations

Phase 1G's Python side reads jobs from
`agents/producer/dashboard/demo_jobs.py` only. Two jobs are mirrored
from `web/lib/data/generation-jobs.ts` and `supabase/seed.sql`:

- `0a0a0a0a-…` — historical 1080p Pai run (`completed`).
- `1b1b1b1b-…` — mock 720p next-week job (`draft`).

Phase 1H will add the Supabase adapter behind the same
`find_demo_job(job_id) -> DemoGenerationJob` return shape, so the runner
keeps working unchanged when the data source flips.

## What Phase 1H will add

| Item | Why |
|---|---|
| Python → Supabase reader (`find_supabase_job`) | Lets the runner read arbitrary `generation_jobs` rows, not just the seeded two |
| Python → Supabase writer (`ingest_artefacts`) | After `--submit` / `--poll` / `--download`, write `provider_request_id` / `raw_request_json` / `actual_credits` / `result_url` / `thumbnail_url` back to the rows and append matching `generation_job_events` |
| `generated_assets` insertion on download | A row per result MP4 + thumbnail with `generation_job_id` set |
| Manual Audio Fixer flow | Re-enable the "Run Audio Fixer later" button behind a confirmation modal mirroring Phase 1G's gates |
| `content_items.active_prompt_version_id` FK | Rolled-over TODO from Phase 1E |
| Per-row UPDATE policy on `content_items.status` for the client persona | Rolled-over TODO from Phase 1D |

## Verification

```bash
cd web
npm run typecheck
npm run build

# Repo root
py -3.11 -m ruff check .
py -3.11 -m pytest tests/test_enhancor_providers.py tests/test_enhancor_smoke_payloads.py -v

# Safe dry-run (no API call, no credits spent):
py -3.11 scripts/run_generation_job.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b --dry-run
```

NO paid API call. NO deploy. NO commit. NO push.

## Exact command to run when we want to actually submit

Only after **manual approval**, with real Pai product + influencer
public-HTTPS URLs ready and a reachable webhook URL:

```bash
py -3.11 scripts/run_generation_job.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \
  --product-url https://<your-cdn>/pai-bioregenerate-bottle.jpg \
  --influencer-url https://<your-cdn>/pai-creator-handsonly.jpg \
  --webhook-url https://<your-hooks>/enhancor/seedance \
  --submit --confirm
```

Expected cost: **2,646 credits** (15 s × 176.4 cr/s at `standard_720p`).
