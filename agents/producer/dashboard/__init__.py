"""Dashboard-side glue between the operator console (Next.js) and the
producer-side providers (Python).

Phase 1G shipped the payload builder + demo job catalogue. Phase 1H
adds the Supabase write helper (`supabase_jobs`) and the local MP4
probe (`mp4_meta`). Together they let the operator ingest artefacts
produced by `scripts/run_generation_job.py` back into Supabase via a
separate `scripts/ingest_generation_job_run.py` CLI.

Importing this package MUST NOT trigger any HTTP call. It is pure data
+ pure helpers; the network only opens when callers explicitly invoke
the mutation primitives in `supabase_jobs`.
"""

from __future__ import annotations

from .demo_jobs import (
    DEMO_GENERATION_JOBS,
    DemoGenerationJob,
    find_demo_job,
)
from .mp4_meta import Mp4Meta, probe_mp4
from .payload_builder import build_seedance_payload_from_job
from .supabase_jobs import (
    PlannedMutation,
    asset_uuid_for,
    event_uuid_for,
    has_supabase_env,
    insert_generated_asset,
    insert_generation_job_event,
    update_generation_job,
)

__all__ = [
    "DEMO_GENERATION_JOBS",
    "DemoGenerationJob",
    "Mp4Meta",
    "PlannedMutation",
    "asset_uuid_for",
    "build_seedance_payload_from_job",
    "event_uuid_for",
    "find_demo_job",
    "has_supabase_env",
    "insert_generated_asset",
    "insert_generation_job_event",
    "probe_mp4",
    "update_generation_job",
]
