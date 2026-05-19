"""Phase 1H Supabase ingestion helper.

Talks to Supabase REST (PostgREST) directly via the existing `requests`
dependency — we don't add `supabase-py` for the same reason the rest of
the repo avoids vendor SDKs that move fast: one HTTP shape, one set of
docs, predictable in CI.

Hard rules:
  - Loads NEXT_PUBLIC_SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY from .env.
  - NEVER prints or logs the service role key. The redaction helper from
    the Enhancor provider layer is reused for header dumps.
  - The service-role key is a server-only credential; this module is
    imported only by Python scripts run on the operator's machine. It
    MUST NOT be imported from anything under `web/` — Python and TS live
    in different runtimes, so there is no accidental wiring path, but
    the docstring is the explicit contract.
  - Every mutating function accepts `dry_run: bool`. When True the
    function returns the planned mutation (verb, table, body) without
    issuing the HTTP call. Callers print the plan and exit.

Idempotency:
  - `update_generation_job` is idempotent on its own (same SET values).
  - `insert_generation_job_event` and `insert_generated_asset` use
    PostgREST's `Prefer: resolution=ignore-duplicates` header plus
    deterministic UUIDv5 ids derived from the artefact path so re-runs
    do not double-insert.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import requests

from agents.producer.providers.base import redact_api_key_headers

log = logging.getLogger("yuvo.dashboard.supabase_jobs")

# UUIDv5 namespace for deterministic ingest ids. Stable across runs so
# `Prefer: resolution=ignore-duplicates` lands on the same row.
_INGEST_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-00000000beef")

# Header name we redact when logging. Supabase REST sends the service
# role key in both `apikey:` and `Authorization: Bearer …`.
_REDACT_HEADERS = ("apikey", "authorization")


@dataclass(frozen=True)
class PlannedMutation:
    """Dry-run record. Printed when --apply is NOT passed.

    The mutation is described by the verb (PATCH / POST), the target
    table, the path-level filter, and the body the script WOULD send.
    """

    verb: str  # "PATCH" or "POST"
    table: str
    where: dict[str, str]  # PostgREST query params like {"id": "eq.<uuid>"}
    body: dict[str, Any]

    def to_human(self) -> str:
        where_str = ", ".join(f"{k}={v}" for k, v in self.where.items()) or "(no filter)"
        return (
            f"{self.verb} {self.table} "
            f"WHERE {where_str}\n"
            f"  body: {json.dumps(self.body, indent=2, ensure_ascii=False)}"
        )


# --------------------------------------------------------------------------- #
# Env + client plumbing
# --------------------------------------------------------------------------- #


def _read_env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def has_supabase_env() -> bool:
    """True iff both the URL and the SERVICE-ROLE key are present.

    The anon key is NOT enough — ingestion writes to operator-only
    tables where the RLS policies still expect a workspace member.
    Until Phase 1H lands a per-row UPDATE policy for the operator
    persona, the service-role key is the only credential that can flip
    `generation_jobs.status`.
    """
    return (
        _read_env("NEXT_PUBLIC_SUPABASE_URL") is not None
        and _read_env("SUPABASE_SERVICE_ROLE_KEY") is not None
    )


def _require_env() -> tuple[str, str]:
    url = _read_env("NEXT_PUBLIC_SUPABASE_URL")
    key = _read_env("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit(
            "FATAL: Supabase env vars are not set. Add NEXT_PUBLIC_SUPABASE_URL "
            "and SUPABASE_SERVICE_ROLE_KEY to .env (see web/.env.example), then "
            "re-run with --apply. Use --dry-run if you only want to preview the "
            "planned mutations."
        )
    return url, key


def _headers(key: str, *, prefer: Optional[str] = None) -> dict[str, str]:
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _log_request(method: str, url: str, headers: dict[str, str]) -> None:
    log.debug(
        "supabase %s %s headers=%s",
        method,
        url,
        redact_api_key_headers(dict(headers), api_key_header_names=_REDACT_HEADERS),
    )


# --------------------------------------------------------------------------- #
# Deterministic ids
# --------------------------------------------------------------------------- #


def event_uuid_for(job_id: str, source_path: str, event_type: str) -> str:
    """Stable id for a generation_job_events row tied to a specific
    artefact file. Re-running the ingester yields the same id."""
    return str(
        uuid.uuid5(_INGEST_NAMESPACE, f"event|{job_id}|{event_type}|{source_path}")
    )


def asset_uuid_for(job_id: str, kind: str, source_path: str) -> str:
    """Stable id for a generated_assets row tied to a specific artefact."""
    return str(uuid.uuid5(_INGEST_NAMESPACE, f"asset|{job_id}|{kind}|{source_path}"))


# --------------------------------------------------------------------------- #
# Mutation primitives
# --------------------------------------------------------------------------- #


def update_generation_job(
    job_id: str,
    patch: dict[str, Any],
    *,
    dry_run: bool,
) -> PlannedMutation:
    """PATCH /rest/v1/generation_jobs?id=eq.<job_id> with `patch`.

    Returns the planned mutation. When `dry_run=False`, also issues the
    HTTP call and raises if the response is non-2xx or no rows were
    updated (PostgREST returns an empty array on no match).
    """
    plan = PlannedMutation(
        verb="PATCH",
        table="generation_jobs",
        where={"id": f"eq.{job_id}"},
        body=patch,
    )
    if dry_run:
        return plan
    url_base, key = _require_env()
    url = f"{url_base.rstrip('/')}/rest/v1/generation_jobs?id=eq.{job_id}"
    headers = _headers(key, prefer="return=representation")
    _log_request("PATCH", url, headers)
    resp = requests.patch(url, headers=headers, json=patch, timeout=30)
    if not resp.ok:
        raise SystemExit(
            f"FATAL: Supabase PATCH generation_jobs returned HTTP {resp.status_code}: "
            f"{resp.text[:512]}"
        )
    rows = resp.json() if resp.text else []
    if not isinstance(rows, list) or len(rows) == 0:
        raise SystemExit(
            f"FATAL: Supabase PATCH generation_jobs WHERE id={job_id} matched 0 rows. "
            "Either the job id is wrong or the service role key is misconfigured."
        )
    return plan


def insert_generation_job_event(
    job_id: str,
    event_id: str,
    event_type: str,
    message: Optional[str],
    raw_payload: Optional[Any],
    *,
    dry_run: bool,
) -> PlannedMutation:
    """POST /rest/v1/generation_job_events with a deterministic id.

    `Prefer: resolution=ignore-duplicates` makes the insert a no-op when
    a row with the same id already exists. Combined with the
    `event_uuid_for(...)` helper, re-running the ingester on the same
    artefact folder never creates duplicate events.
    """
    body = {
        "id": event_id,
        "generation_job_id": job_id,
        "event_type": event_type,
        "message": message,
        "raw_payload": raw_payload,
    }
    plan = PlannedMutation(
        verb="POST",
        table="generation_job_events",
        where={},
        body=body,
    )
    if dry_run:
        return plan
    url_base, key = _require_env()
    url = f"{url_base.rstrip('/')}/rest/v1/generation_job_events"
    headers = _headers(key, prefer="resolution=ignore-duplicates")
    _log_request("POST", url, headers)
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    # PostgREST returns 201 on insert, 200 + empty body on ignored dupe.
    if resp.status_code not in (200, 201):
        raise SystemExit(
            f"FATAL: Supabase POST generation_job_events HTTP {resp.status_code}: "
            f"{resp.text[:512]}"
        )
    return plan


def insert_generated_asset(
    asset_id: str,
    content_item_id: str,
    generation_job_id: str,
    kind: str,
    storage_path: str,
    *,
    public_url: Optional[str],
    mime: Optional[str],
    byte_size: Optional[int],
    duration_sec: Optional[float],
    resolution: Optional[str],
    dry_run: bool,
) -> PlannedMutation:
    """POST /rest/v1/generated_assets with a deterministic id.

    The schema requires `content_item_id` + `storage_path` to be NOT
    NULL. The Phase 1F migration extended the table with a nullable
    `generation_job_id` FK; we always populate it so the dashboard can
    JOIN cheaply.
    """
    body = {
        "id": asset_id,
        "content_item_id": content_item_id,
        "generation_job_id": generation_job_id,
        "kind": kind,
        "storage_path": storage_path,
        "public_url": public_url,
        "mime": mime,
        "byte_size": byte_size,
        "duration_sec": duration_sec,
        "resolution": resolution,
    }
    plan = PlannedMutation(
        verb="POST",
        table="generated_assets",
        where={},
        body=body,
    )
    if dry_run:
        return plan
    url_base, key = _require_env()
    url = f"{url_base.rstrip('/')}/rest/v1/generated_assets"
    headers = _headers(key, prefer="resolution=ignore-duplicates")
    _log_request("POST", url, headers)
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    if resp.status_code not in (200, 201):
        raise SystemExit(
            f"FATAL: Supabase POST generated_assets HTTP {resp.status_code}: "
            f"{resp.text[:512]}"
        )
    return plan


__all__ = [
    "PlannedMutation",
    "asset_uuid_for",
    "event_uuid_for",
    "has_supabase_env",
    "insert_generated_asset",
    "insert_generation_job_event",
    "update_generation_job",
]
