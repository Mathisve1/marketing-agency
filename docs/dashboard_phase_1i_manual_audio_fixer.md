# Yuvo OS — Phase 1I: Manual Audio Fixer + ingestion fallbacks

Phase 1I closes three loose ends from the Phase 1H real-run:

1. **Stdlib MP4 atom parser fallback** in `mp4_meta.probe_mp4`, so the
   ingester records `duration_sec` / `width` × `height` / `resolution` /
   `has_audio_track` / `video_codec` / `audio_codec` even when `ffprobe`
   isn't on PATH (the common operator-machine case).
2. **Manual Audio Fixer runner** (`scripts/run_audio_fixer_job.py`)
   following the same operator-driven CLI bridge pattern as the
   Seedance runner. Phase 1I exercises only `--dry-run`; the
   `--submit / --poll / --download` lifecycle is implemented for
   future operator use but **never invoked by Phase 1I itself**.
3. **Dashboard polish.** The job detail page's Audio Fixer card now
   surfaces the dry-run command + a typed-`AUDIO-FIXER` gate on the
   (still locked) paid command, mirroring Phase 1G's typed-`SUBMIT`
   pattern. The Raw video asset card stays unchanged because the
   ingester's `duration_sec` + `resolution` columns now populate
   correctly via the new fallback.

Phase 1I does **NOT**:

- Run Audio Fixer.
- Spend credits.
- Auto-regenerate the Seedance run.
- Touch the Phase 1D service-role escape hatch for the
  `content_items.status` flip.
- Apply ingest to Supabase — the operator's `.env` has no Supabase
  credentials, so the ingester stays dry-run-only.

## Phase 1H result summary

The Phase 1H real-run produced a usable internal-demo 720p UGC take:

| Field | Value |
|---|---|
| job_id | `1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b` |
| provider_request_id | `6a09e50d13b1573a3bc1cd17` |
| final status | `COMPLETED` (~11 min in flight) |
| actual credits | **2,128.5** (under the 2,646 upper-bound estimate) |
| result URL | `https://d2i9jqncnkplwq.cloudfront.net/videos/4e5a03d2-c5b7-418c-9897-90cf3cd60f2c.mp4` |
| thumbnail URL | `https://d2i9jqncnkplwq.cloudfront.net/thumbnails/0a8813b8-76de-4d8a-bd6a-540c59880251.webp` |
| local MP4 | `prospects/pai-skincare/production/dashboard_job_runs/1b1b…/result.mp4` |
| size / duration / res / audio | 5,501,657 B · 15.070 s · 720×1280 · AAC audio present |
| quality verdict | `usable_internal_demo` — one label-text hallucination ("TriPepttide") |

Native audio is already present in the raw mp4, so Phase 1I's
recommendation for this specific take is to **SKIP Audio Fixer** —
the label issue is a prompt-version concern, not an audio concern.

## Stdlib MP4 atom parser

`agents/producer/dashboard/mp4_meta.py::probe_mp4()` now tries three
probe paths in order:

1. **ffprobe** — fastest path, full codec metadata.
2. **stdlib_atoms** — Phase 1I addition. Walks `moov` → `mvhd` /
   `trak` / `tkhd` / `mdia` / `hdlr` / `stbl` / `stsd` atoms via the
   `struct` module. Handles version-0 and version-1 atom size headers.
   Recovers:
   - `duration_sec` (from `mvhd.duration / mvhd.timescale`)
   - `width` × `height` (last 8 bytes of `tkhd`, 16.16 fixed-point)
   - `has_audio_track` (presence of any `trak` with `hdlr.handler_type = 'soun'`)
   - `video_codec` / `audio_codec` (first FourCC inside the matching
     track's `stsd` — `avc1` / `hev1` / `av01` for video, `mp4a` /
     `opus` / `ec-3` for audio).
3. **byte_only** — last resort; only `byte_size` + `mime`.

`Mp4Meta` gained a `probe_source` field so the operator can tell at a
glance which fallback path produced the row.

The `_MAX_ATOM_PAYLOAD_BYTES = 64 MiB` cap protects against
accidentally loading a multi-gig file; real UGC mp4s are well under
the cap.

Tests landed in `tests/test_mp4_meta.py`:

- synthesised 720p mp4 round-trip
- video-only mp4 → `has_audio_track=False`
- 64-bit extended size header (`size=1`)
- malformed mvhd → graceful degrade
- file with no ftyp → `_atom_probe` returns `None`
- real Pai 720p artefact (`@pytest.mark.skipif` when missing)
- end-to-end `probe_mp4` returns `Mp4Meta` with `probe_source` populated

Run:

```bash
py -3.11 -m pytest tests/test_mp4_meta.py -v
```

## Supabase ingestion apply requirements

`scripts/ingest_generation_job_run.py --apply` writes to:

- `generation_jobs` (PATCH the row matching `--job-id`)
- `generation_job_events` (INSERT one per artefact, deterministic IDs)
- `generated_assets` (INSERT one `raw_video` row when `result.mp4` is on disk)

It requires both env vars in `.env` (or the operator's shell):

```
NEXT_PUBLIC_SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service role key>  # server-only; never to client
```

When either is missing, `--apply` exits with a FATAL pointing at the
missing var. Phase 1I keeps the dry-run path safe: it prints the full
plan but never opens an outbound connection.

**Phase 1I state:** both env vars are absent in `.env` on the
operator's machine; the ingester stays dry-run-only. The local
artefacts are intact in `prospects/pai-skincare/production/dashboard_job_runs/1b1b…/`
and `--apply` will be idempotent the first time the env vars land.

### Plan that would land (when --apply runs)

```
[1] generation_jobs PATCH id=1b1b…:
    status='completed', actual_credits=2128, raw_request_json=…,
    raw_response_json=…, provider_request_id='6a09e50d13b1573a3bc1cd17',
    result_url='https://d2i9j…/videos/…mp4',
    thumbnail_url='https://d2i9j…/thumbnails/…webp',
    raw_asset_id='<deterministic uuidv5>'

[2] generated_assets INSERT (raw_video):
    content_item_id='99999999-9999-9999-9999-999999999999'
    generation_job_id='1b1b…'
    kind='raw_video'
    storage_path='C:\…\result.mp4'  (local FS until Storage lands)
    public_url='https://d2i9j…/videos/…mp4'
    mime='video/mp4'
    byte_size=5_501_657
    duration_sec=15.07           ← Phase 1I stdlib fallback populates this
    resolution='720p'            ← Phase 1I stdlib fallback populates this

[3] generation_job_events × 11 inserts:
    submitted    × 1
    status_polled × 9
    completed    × 1  (raw_payload carries probe_source='stdlib_atoms'
                       + width/height/has_audio_track/codecs)
```

## Manual Audio Fixer workflow

```
Phase 1G: --submit → seedance generation completed.
Phase 1G: --poll   → terminal status reached.
Phase 1G: --download → result.mp4 on disk, native audio present.
Phase 1H: ingest --apply → generation_jobs.status=completed,
                            generated_assets row with raw_video kind.
Phase 1I: (you are here)
          → Dashboard surfaces the Audio Fixer command panel.
          → Operator REVIEWS the raw audio.
          → If audio is good (Pai case), SKIP. No credits spent.
          → If audio needs improvement: operator runs --dry-run,
            inspects the planned payload, then (manually, on
            explicit approval) runs --submit --confirm.
```

The dashboard panel exposes:

- A **dry-run** command (always copy-unlocked):

  ```
  py -3.11 scripts/run_audio_fixer_job.py \
    --generation-job-id <jobId> \
    --dry-run
  ```

- A **paid submit** command, gated behind:
  - Checkbox: *"I understand this would spend credits"*
  - Typed phrase: `AUDIO-FIXER` (case-sensitive)
  - The unlocked button still only copies the command — the
    dashboard never POSTs to Enhancor itself.

Audio Fixer artefacts land under
`prospects/pai-skincare/production/dashboard_job_runs/<jobId>/audio_fixer/`
so Phase 1H's existing run-folder ingester doesn't need to change to
pick them up later.

### Command examples

```bash
# Dry-run only (Phase 1I default):
py -3.11 scripts/run_audio_fixer_job.py \
  --generation-job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \
  --dry-run

# PAID submit — Phase 1I does NOT run this:
py -3.11 scripts/run_audio_fixer_job.py \
  --generation-job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \
  --webhook-url https://<your-hooks>/audio-fixer \
  --submit --confirm

# Poll once submitted:
py -3.11 scripts/run_audio_fixer_job.py \
  --generation-job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \
  --poll

# Download the audio-fixed mp4:
py -3.11 scripts/run_audio_fixer_job.py \
  --generation-job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \
  --download
```

## Why Audio Fixer stays manual

- It always costs additional credits (≈2,100 per 15-second clip).
- UGC mode output already carries native audio — the Pai 720p take
  proved this. The Audio Fixer becomes a cleanup pass for UGC, not a
  necessary step.
- Auto-running it would erase the operator's "is this audio actually
  bad?" review gate; the Phase 1G pattern is that no paid call is
  ever made without a deliberate operator decision.

For the Pai 720p take specifically, the recommended next step is
**not** to run Audio Fixer — the artefact ships with usable AAC audio
and the only quality concern (the "TriPepttide" label-text
hallucination) is a prompt-version issue. Phase 1E's prompt editor is
the right tool to address it: bump the prompt version, tighten the
negative-prompt label guard, and only then create a fresh mock job
and submit it.

## Next phase (Phase 1J — placeholder)

| Item | Why |
|---|---|
| Land Supabase env vars + run `--apply` against the Pai run | Surface the completed job in the live dashboard |
| Stop service-role escape hatch — per-row UPDATE policy on `content_items.status` for the client persona | Rolled-over Phase 1D TODO |
| `content_items.active_prompt_version_id` FK | Rolled-over Phase 1E TODO |
| Supabase Storage upload of `result.mp4` | Drop local FS dependency on `storage_path` |
| Prompt-version v2 for the Pai 720p take | Address the label-text hallucination before any future Pai re-submit |
| (Optional, post-approval) one manual Audio Fixer run, only if the raw audio is judged insufficient | Already wired by Phase 1I; just needs the operator green light |

## Verification

```bash
cd web
npm run typecheck
npm run build

# Repo root
py -3.11 -m ruff check .
py -3.11 -m pytest tests/test_enhancor_providers.py tests/test_enhancor_smoke_payloads.py tests/test_mp4_meta.py -v

# Safe dry-runs (no Enhancor call, no Supabase writes, no credits):
py -3.11 scripts/ingest_generation_job_run.py \
  --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b --dry-run
py -3.11 scripts/run_audio_fixer_job.py \
  --generation-job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b --dry-run
```

No paid API call. No deploy. No commit. No push.
