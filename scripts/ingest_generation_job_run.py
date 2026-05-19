"""Phase 1H ingest the artefacts produced by `run_generation_job.py`
into Supabase.

This script is the second half of Phase 1H's split-credential model:

    scripts/run_generation_job.py        — uses ENHANCOR_API_KEY only
    scripts/ingest_generation_job_run.py — uses SUPABASE_SERVICE_ROLE_KEY only

The ingester reads only what's on disk under
    prospects/pai-skincare/production/dashboard_job_runs/<job-id>/
and never opens an outbound connection except to Supabase. It NEVER
calls Enhancor.

Hard rules:
  - Default is --dry-run. The script refuses to mutate Supabase
    without --apply. Re-running --apply on the same folder is a no-op
    thanks to deterministic UUIDv5 ids on the inserted rows.
  - When Supabase env vars are missing, --dry-run still works (prints
    the planned mutations); --apply exits with a clear FATAL.
  - The service-role key is never printed. The redaction helper from
    the Enhancor adapter layer is reused for header dumps.
  - No paid API call is made by this script. No credits are spent.

Usage:

    # Safe preview (default):
    py -3.11 scripts/ingest_generation_job_run.py --job-id <jobId> --dry-run

    # Real ingest (still no Enhancor call, but writes to Supabase):
    py -3.11 scripts/ingest_generation_job_run.py --job-id <jobId> --apply

What lands in Supabase on --apply:

    generation_jobs
      status                = COMPLETED | FAILED | (preserves draft/queued)
      provider_request_id   = from submit_response.json
      result_url            = from latest completed poll
      thumbnail_url         = from latest completed poll
      actual_credits        = from latest completed poll
      raw_request_json      = payload.json
      raw_response_json     = submit_response.raw_response
      error_message         = on FAILED only
      raw_asset_id          = id of the inserted generated_assets row

    generation_job_events
      one `submitted` event per submit_response.json (deterministic id)
      one `status_polled` event per poll_*.json (deterministic id)
      one terminal event (`completed` or `failed`) when the latest
        poll is terminal — including the MP4 metadata in raw_payload
        when ffprobe is available.

    generated_assets
      one `raw_video` row when result.mp4 exists, with storage_path =
      local FS path (Phase 1H placeholder until Supabase Storage lands),
      public_url = Enhancor result_url, plus ffprobe-derived
      byte_size / duration_sec / resolution / mime.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

# ── Project-root sys.path shim (script invocation) ─────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.producer.dashboard import (  # noqa: E402
    PlannedMutation,
    asset_uuid_for,
    event_uuid_for,
    find_demo_job,
    has_supabase_env,
    insert_generated_asset,
    insert_generation_job_event,
    probe_mp4,
    update_generation_job,
)
from agents.producer.dashboard.mp4_meta import meta_to_event_payload  # noqa: E402

log = logging.getLogger("yuvo.dashboard.ingest_generation_job_run")

DEFAULT_OUT_BASE = (
    _REPO_ROOT / "prospects" / "pai-skincare" / "production" / "dashboard_job_runs"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _force_utf8_stdout() -> None:
    """Windows cp1252 console falls over on en-dash + typographic
    apostrophes; force utf-8 so the artefact prints don't crash."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _load_dotenv_if_available() -> None:
    """Load .env without crashing if python-dotenv isn't installed."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    load_dotenv(_REPO_ROOT / ".env")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Artefact discovery
# --------------------------------------------------------------------------- #


class Artefacts:
    """In-memory snapshot of what was found on disk for a given job."""

    def __init__(
        self,
        *,
        run_dir: Path,
        payload: Optional[dict[str, Any]],
        submit: Optional[dict[str, Any]],
        polls: list[dict[str, Any]],
        poll_paths: list[Path],
        result_meta: Optional[dict[str, Any]],
        result_mp4: Optional[Path],
    ) -> None:
        self.run_dir = run_dir
        self.payload = payload
        self.submit = submit
        self.polls = polls
        self.poll_paths = poll_paths
        self.result_meta = result_meta
        self.result_mp4 = result_mp4

    @property
    def latest_poll(self) -> Optional[dict[str, Any]]:
        return self.polls[-1] if self.polls else None

    @property
    def latest_poll_path(self) -> Optional[Path]:
        return self.poll_paths[-1] if self.poll_paths else None

    @property
    def latest_terminal_status(self) -> Optional[str]:
        if not self.latest_poll:
            return None
        status = self.latest_poll.get("status")
        if isinstance(status, str) and status in ("COMPLETED", "FAILED"):
            return status
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir),
            "payload_present": self.payload is not None,
            "submit_present": self.submit is not None,
            "polls_found": len(self.polls),
            "latest_poll": (self.latest_poll_path.name if self.latest_poll_path else None),
            "latest_terminal_status": self.latest_terminal_status,
            "result_meta_present": self.result_meta is not None,
            "result_mp4_present": self.result_mp4 is not None,
        }


def discover_artefacts(job_id: str, base: Path) -> Artefacts:
    """Walk the run folder and load every JSON / MP4 we care about."""
    run_dir = base / job_id
    if not run_dir.is_dir():
        raise SystemExit(
            f"FATAL: no run folder at {run_dir}. Run "
            f"scripts/run_generation_job.py --job-id {job_id} --dry-run "
            "first (or --submit + --poll + --download for a real run)."
        )

    payload = None
    payload_path = run_dir / "payload.json"
    if payload_path.exists():
        payload = _read_json(payload_path)

    submit = None
    submit_path = run_dir / "submit_response.json"
    if submit_path.exists():
        submit = _read_json(submit_path)

    poll_paths = sorted(run_dir.glob("poll_*.json"))
    polls: list[dict[str, Any]] = []
    for p in poll_paths:
        polls.append(_read_json(p))

    result_meta = None
    result_meta_path = run_dir / "result_meta.json"
    if result_meta_path.exists():
        result_meta = _read_json(result_meta_path)

    result_mp4 = None
    result_mp4_path = run_dir / "result.mp4"
    if result_mp4_path.exists():
        result_mp4 = result_mp4_path

    return Artefacts(
        run_dir=run_dir,
        payload=payload,
        submit=submit,
        polls=polls,
        poll_paths=poll_paths,
        result_meta=result_meta,
        result_mp4=result_mp4,
    )


# --------------------------------------------------------------------------- #
# Plan builder
# --------------------------------------------------------------------------- #


def _resolve_content_item_id(job_id: str) -> str:
    """The generated_assets row needs content_item_id (NOT NULL).

    Phase 1H originally resolved this via the static demo_jobs.py
    catalogue. Phase 1T adds a Supabase fallback: when the demo lookup
    misses (e.g. for Phase 1R/1S multi-clip jobs that were created
    directly in Supabase, not through the demo catalog), we read
    `generation_jobs.content_item_id` over PostgREST using the
    service-role key already in env. Service-role only ever runs from
    this Python script — never from web/ — same posture as the rest of
    the ingester.
    """
    demo = find_demo_job(job_id)
    if demo is not None:
        return demo.content_item_id

    # --- Phase 1T Supabase fallback (READ-ONLY GET) -------------------- #
    url = (os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit(
            f"FATAL: job id {job_id!r} not in agents/producer/dashboard/demo_jobs.py "
            "and NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set, "
            "so the Phase 1T Supabase fallback cannot run either."
        )
    try:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(
            f"{url}/rest/v1/generation_jobs?id=eq.{job_id}&select=content_item_id",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise SystemExit(
            f"FATAL: Supabase GET generation_jobs/{job_id} -> HTTP {e.code} {body!r}"
        ) from e
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            f"FATAL: could not reach Supabase to resolve content_item_id for {job_id!r}: {e}"
        ) from e
    if not rows or not rows[0].get("content_item_id"):
        raise SystemExit(
            f"FATAL: job id {job_id!r} not found in Supabase generation_jobs either. "
            "Confirm the job row exists before ingesting."
        )
    return rows[0]["content_item_id"]


def _build_job_patch(
    artefacts: Artefacts,
    *,
    raw_asset_id: Optional[str],
) -> dict[str, Any]:
    """Decide which generation_jobs columns to PATCH from the artefact set.

    Only includes columns we have concrete values for — never overwrites
    a row's column with None.
    """
    patch: dict[str, Any] = {}
    if artefacts.payload is not None:
        patch["raw_request_json"] = artefacts.payload
    if artefacts.submit is not None:
        provider_request_id = artefacts.submit.get("provider_job_id")
        if isinstance(provider_request_id, str) and provider_request_id:
            patch["provider_request_id"] = provider_request_id
        raw_response = artefacts.submit.get("raw_response")
        if raw_response is not None:
            patch["raw_response_json"] = raw_response
    latest = artefacts.latest_poll
    if latest is not None:
        status = latest.get("status")
        # Map provider terminal states onto generation_jobs.status enum
        # (`completed` / `failed`) — keep submitted/processing visible
        # so the operator can see mid-flight state.
        if status == "COMPLETED":
            patch["status"] = "completed"
        elif status == "FAILED":
            patch["status"] = "failed"
        elif status == "QUEUED":
            patch["status"] = "submitted"
        elif status == "IN_PROGRESS":
            patch["status"] = "processing"

        result_url = latest.get("result_url")
        if isinstance(result_url, str) and result_url:
            patch["result_url"] = result_url
        thumb = latest.get("thumbnail_url")
        if isinstance(thumb, str) and thumb:
            patch["thumbnail_url"] = thumb
        cost = latest.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            patch["actual_credits"] = int(round(cost))
        err = latest.get("error_message")
        if isinstance(err, str) and err:
            patch["error_message"] = err

    if raw_asset_id is not None:
        patch["raw_asset_id"] = raw_asset_id
    return patch


def _build_event_plans(
    artefacts: Artefacts,
    job_id: str,
    *,
    mp4_event_payload: Optional[dict[str, Any]],
    dry_run: bool,
) -> list[PlannedMutation]:
    """One event per artefact: submitted (from submit_response.json),
    status_polled per poll, plus a terminal completed/failed."""
    plans: list[PlannedMutation] = []
    if artefacts.submit is not None:
        plans.append(
            insert_generation_job_event(
                job_id=job_id,
                event_id=event_uuid_for(job_id, "submit_response.json", "submitted"),
                event_type="submitted",
                message=(
                    "Submitted to Enhancor Seedance — provider_request_id="
                    f"{artefacts.submit.get('provider_job_id')!r}."
                ),
                raw_payload=artefacts.submit.get("raw_response"),
                dry_run=dry_run,
            )
        )
    for path, blob in zip(artefacts.poll_paths, artefacts.polls, strict=True):
        plans.append(
            insert_generation_job_event(
                job_id=job_id,
                event_id=event_uuid_for(job_id, path.name, "status_polled"),
                event_type="status_polled",
                message=f"Polled /status — provider returned {blob.get('status')!r}.",
                raw_payload=blob.get("raw_status_response"),
                dry_run=dry_run,
            )
        )

    terminal = artefacts.latest_terminal_status
    if terminal == "COMPLETED":
        plans.append(
            insert_generation_job_event(
                job_id=job_id,
                event_id=event_uuid_for(job_id, "terminal", "completed"),
                event_type="completed",
                message=(
                    "Provider reported COMPLETED. Result downloaded "
                    f"({artefacts.result_mp4.name if artefacts.result_mp4 else 'no mp4 yet'})."
                ),
                raw_payload=mp4_event_payload,
                dry_run=dry_run,
            )
        )
    elif terminal == "FAILED":
        last = artefacts.latest_poll or {}
        plans.append(
            insert_generation_job_event(
                job_id=job_id,
                event_id=event_uuid_for(job_id, "terminal", "failed"),
                event_type="failed",
                message=last.get("error_message")
                or "Provider reported FAILED. See raw_payload for the full /status response.",
                raw_payload=last.get("raw_status_response"),
                dry_run=dry_run,
            )
        )
    return plans


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def _do_run(args: argparse.Namespace) -> int:
    artefacts = discover_artefacts(args.job_id, args.out_base)
    summary = artefacts.summary()
    print("--- PHASE 1H INGEST -----------------------------------------")
    print(f"job_id: {args.job_id}")
    print(f"mode:   {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"supabase_env_present: {has_supabase_env()}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if not (
        artefacts.payload
        or artefacts.submit
        or artefacts.polls
        or artefacts.result_meta
        or artefacts.result_mp4
    ):
        raise SystemExit(
            "FATAL: no artefacts found. Run scripts/run_generation_job.py "
            "(--dry-run / --submit / --poll / --download) first."
        )

    if args.apply and not has_supabase_env():
        raise SystemExit(
            "FATAL: --apply requires NEXT_PUBLIC_SUPABASE_URL + "
            "SUPABASE_SERVICE_ROLE_KEY in .env. Use --dry-run to preview "
            "the planned mutations without those env vars."
        )

    # ---- generated_assets ------------------------------------------------ #
    raw_asset_id: Optional[str] = None
    mp4_event_payload: Optional[dict[str, Any]] = None
    asset_plan: Optional[PlannedMutation] = None

    if artefacts.result_mp4 is not None:
        meta = probe_mp4(artefacts.result_mp4)
        mp4_event_payload = meta_to_event_payload(meta)
        content_item_id = _resolve_content_item_id(args.job_id)
        result_url = (
            artefacts.latest_poll.get("result_url") if artefacts.latest_poll else None
        )
        asset_id = asset_uuid_for(args.job_id, "raw_video", str(artefacts.result_mp4))
        asset_plan = insert_generated_asset(
            asset_id=asset_id,
            content_item_id=content_item_id,
            generation_job_id=args.job_id,
            kind="raw_video",
            storage_path=str(artefacts.result_mp4),  # local FS path until Storage lands
            public_url=result_url if isinstance(result_url, str) else None,
            mime=meta.mime,
            byte_size=meta.byte_size,
            duration_sec=meta.duration_sec,
            resolution=meta.resolution_label,
            dry_run=args.dry_run,
        )
        raw_asset_id = asset_id
        if meta.probe_source == "byte_only":
            print(
                "NOTE: ffprobe not on PATH and the stdlib atom parser could not "
                "recover anything — only byte_size + mime were captured. Install "
                "ffmpeg or check the result.mp4 is a valid ISO-BMFF file."
            )
        elif meta.probe_source == "stdlib_atoms":
            print(
                f"NOTE: ffprobe not on PATH — using Phase 1I stdlib atom parser. "
                f"Recovered duration={meta.duration_sec}s, "
                f"{meta.width}x{meta.height} ({meta.resolution_label}), "
                f"audio_track={meta.has_audio_track}, codecs="
                f"{meta.video_codec!r}/{meta.audio_codec!r}."
            )

    # ---- generation_jobs PATCH ------------------------------------------ #
    patch = _build_job_patch(artefacts, raw_asset_id=raw_asset_id)
    job_plan: Optional[PlannedMutation] = None
    if patch:
        job_plan = update_generation_job(args.job_id, patch, dry_run=args.dry_run)

    # ---- generation_job_events inserts ---------------------------------- #
    event_plans = _build_event_plans(
        artefacts,
        args.job_id,
        mp4_event_payload=mp4_event_payload,
        dry_run=args.dry_run,
    )

    # ---- Plan + result printout ----------------------------------------- #
    print("\n--- PLANNED MUTATIONS ----------------------------------------")
    if not patch and asset_plan is None and not event_plans:
        print("(no plan — nothing to ingest)")
    if job_plan is not None:
        print("\n[1] generation_jobs PATCH:")
        print(job_plan.to_human())
    if asset_plan is not None:
        print("\n[2] generated_assets INSERT (raw_video):")
        print(asset_plan.to_human())
    for i, ep in enumerate(event_plans, start=1):
        print(f"\n[{2 if asset_plan is None else 3}.{i}] generation_job_events INSERT:")
        print(ep.to_human())

    if args.dry_run:
        print("\nDRY-RUN COMPLETE. No Supabase writes were issued.")
        print(
            "Re-run with --apply (and NEXT_PUBLIC_SUPABASE_URL + "
            "SUPABASE_SERVICE_ROLE_KEY in .env) to apply."
        )
        return 0

    print("\nAPPLIED. Supabase writes completed successfully.")
    print(
        f"  generation_jobs(id={args.job_id}) patched: "
        f"{', '.join(sorted(patch)) or '(no fields)'}"
    )
    if raw_asset_id:
        print(f"  generated_assets(id={raw_asset_id}) raw_video inserted")
    print(f"  generation_job_events: {len(event_plans)} rows (deterministic ids)")
    print(
        "\nPhase 1I unlocks the manual Audio Fixer flow; Audio Fixer "
        "remains opt-in and never auto-triggered."
    )
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ingest_generation_job_run",
        description=(
            "Phase 1H — ingest the JSON / MP4 artefacts produced by "
            "scripts/run_generation_job.py back into Supabase. Never "
            "calls Enhancor. Never spends credits."
        ),
    )
    p.add_argument(
        "--job-id",
        required=True,
        help="generation_jobs.id (uuid). Must match a run folder under --out-base.",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + print the planned mutations. NO Supabase writes.",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Issue the Supabase writes. Requires NEXT_PUBLIC_SUPABASE_URL "
            "and SUPABASE_SERVICE_ROLE_KEY in .env. Idempotent — re-running "
            "writes the same deterministic ids and PostgREST skips dupes."
        ),
    )
    p.add_argument(
        "--out-base",
        type=Path,
        default=DEFAULT_OUT_BASE,
        help=(
            "Where per-job artefacts live. Default: "
            "prospects/pai-skincare/production/dashboard_job_runs/"
        ),
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="DEBUG logging. Still redacts Supabase service-role key.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _force_utf8_stdout()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    _load_dotenv_if_available()
    return _do_run(args)


if __name__ == "__main__":
    sys.exit(main())
