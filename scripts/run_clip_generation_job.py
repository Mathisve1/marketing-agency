"""Phase 1T per-clip generation job runner (Supabase-backed).

The Phase 1G `scripts/run_generation_job.py` reads from the static
`agents/producer/dashboard/demo_jobs.py` catalogue and CANNOT see the
Phase 1R/1S multi-clip jobs that live in Supabase. This script is the
Supabase-aware counterpart: same lifecycle (--dry-run / --submit /
--poll / --download), but the job + prompt are loaded from
`generation_jobs` + `prompt_versions` via PostgREST.

Hard guarantees:

  - Clip sequencing (Phase 1S): clip N is BLOCKED until clip N-1 in the
    same batch has status='completed'. Enforced on EVERY mode, including
    --dry-run. Clip 1 (open_loop) is always eligible. Clip 2+ requires
    its predecessor to be terminal-completed; --submit refuses
    otherwise.
  - Only `status='draft'` clips are eligible for --submit. Re-submitting
    a clip that's already `queued` / `submitted` / `processing` /
    `completed` is refused — that path is reserved for a future "retry"
    flow with an explicit gate.
  - `--submit` requires `--confirm` AND real (non-placeholder)
    `--product-url` + `--influencer-url` + `--webhook-url`.
  - ENHANCOR_API_KEY is loaded from `.env` and NEVER printed.
  - No Audio Fixer. Ever. This script does not import the audio-fixer
    provider; it physically cannot trigger one.
  - No `content_items.client_safe_video_url` update. Sharing with the
    client is a separate operator decision.
  - No stitching. Each clip is downloaded as its own `result.mp4` under
    `prospects/.../dashboard_job_runs/<job-id>/`.

Lifecycle:

    --dry-run     : load job + prompt from Supabase, run the sequencing
                    gate, build + validate the Seedance payload, save it
                    to payload.json. NO HTTP call to Enhancor.

    --submit      : --dry-run + sequencing gate + URL validation, then
                    POST to /queue. Saves submit_response.json. Requires
                    --confirm and real URLs.

    --poll        : reads submit_response.json, POSTs to /status, saves
                    poll_<UTC>.json. Idempotent.

    --download    : reads the latest poll, streams the MP4 + writes
                    result_meta.json. Idempotent.

This runner DOES NOT write to Supabase. Once a clip's lifecycle
completes, `scripts/ingest_generation_job_run.py` (Phase 1H) ingests
the artefacts back into Supabase. The ingester is idempotent.

Usage:

    # SAFE — no API call:
    py -3.11 scripts/run_clip_generation_job.py \\
        --job-id c732ae50-... \\
        --product-url https://your-cdn/pai-bottle-9x16.jpg \\
        --influencer-url https://your-cdn/creator-9x16.jpg \\
        --webhook-url https://your-hooks/seedance \\
        --dry-run

    # PAID — exactly one Seedance call:
    py -3.11 scripts/run_clip_generation_job.py \\
        --job-id c732ae50-... \\
        --product-url <...> --influencer-url <...> --webhook-url <...> \\
        --submit --confirm

    py -3.11 scripts/run_clip_generation_job.py --job-id <id> --poll
    py -3.11 scripts/run_clip_generation_job.py --job-id <id> --download
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Project-root sys.path shim ────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.producer.dashboard.demo_jobs import (  # noqa: E402
    DemoGenerationJob,
    is_placeholder_url,
)
from agents.producer.dashboard.payload_builder import (  # noqa: E402
    build_seedance_payload_from_job,
)
from agents.producer.providers.base import (  # noqa: E402
    ProviderError,
    ProviderJobRequest,
    ProviderStatus,
)
from agents.producer.providers.enhancor_seedance import (  # noqa: E402
    EnhancorSeedanceProvider,
)

log = logging.getLogger("yuvo.dashboard.run_clip_generation_job")

DEFAULT_OUT_BASE = (
    _REPO_ROOT / "prospects" / "pai-skincare" / "production" / "dashboard_job_runs"
)
DEFAULT_WEBHOOK_URL = "https://example.com/webhooks/enhancor/seedance"


# --------------------------------------------------------------------------- #
# Env loading — pulls from .env and web/.env.local so both Phase 1G/1H and
# Phase 1S conventions work.
# --------------------------------------------------------------------------- #


def _load_dotenv_if_available() -> None:
    """Load .env then web/.env.local without crashing if python-dotenv
    isn't installed. web/.env.local takes precedence (matches the Next
    runtime ordering)."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(_REPO_ROOT / "web" / ".env.local", override=True)


def _resolve_api_key() -> str:
    """ENHANCOR_API_KEY without ever printing the value."""
    key = os.environ.get("ENHANCOR_API_KEY")
    if not key or not key.strip():
        raise SystemExit(
            "FATAL: ENHANCOR_API_KEY is not set. Add it to .env "
            "(see .env.example) or your shell, then re-run."
        )
    return key.strip()


def _resolve_supabase_creds() -> tuple[str, str]:
    """NEXT_PUBLIC_SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY. The URL is
    a public hostname; only the key is sensitive. Neither is logged."""
    url = (os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url:
        raise SystemExit(
            "FATAL: NEXT_PUBLIC_SUPABASE_URL is not set in .env / web/.env.local."
        )
    if not key:
        raise SystemExit(
            "FATAL: SUPABASE_SERVICE_ROLE_KEY is not set. The clip runner "
            "needs service-role to read the Supabase job + prompt rows."
        )
    return url, key


# --------------------------------------------------------------------------- #
# PostgREST helpers — READS ONLY.
# --------------------------------------------------------------------------- #


def _pg_get(url: str, key: str, path: str) -> Any:
    """Read-only PostgREST GET. The runner NEVER POST/PATCH/DELETEs.
    Ingestion writes are exclusively Phase 1H's ingester."""
    u = f"{url}/rest/v1/{path}"
    req = urllib.request.Request(
        u,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise SystemExit(
            f"FATAL: Supabase GET {path} -> HTTP {e.code} {body!r}"
        ) from e


# --------------------------------------------------------------------------- #
# Job loading + sequencing gate
# --------------------------------------------------------------------------- #


def _load_clip_job(job_id: str) -> dict[str, Any]:
    """Fetch the generation_jobs row, its prompt_version, its sibling
    clips on the same batch. Returns a dict with everything needed to
    build a DemoGenerationJob + check the sequencing gate."""
    url, key = _resolve_supabase_creds()

    jrows = _pg_get(
        url, key,
        f"generation_jobs?id=eq.{job_id}&select=id,batch_id,content_item_id,"
        "prompt_version_id,provider,provider_mode,quality_tier,resolution,"
        "duration_seconds,status,estimated_credits,actual_credits,clip_number,"
        "clip_role,provider_request_id",
    )
    if not jrows:
        raise SystemExit(
            f"FATAL: generation_jobs row {job_id!r} not found in Supabase."
        )
    job = jrows[0]

    prows = _pg_get(
        url, key,
        f"prompt_versions?id=eq.{job['prompt_version_id']}&select=id,label,"
        "version_number,status,hook,prompt_body,negative_prompt,scene_plan,"
        "creator_direction,product_constraints",
    )
    if not prows:
        raise SystemExit(
            f"FATAL: prompt_version {job['prompt_version_id']!r} not found."
        )
    prompt = prows[0]

    siblings = _pg_get(
        url, key,
        f"generation_jobs?batch_id=eq.{job['batch_id']}&select=id,clip_number,"
        "clip_role,status&order=clip_number.asc",
    )
    return {"job": job, "prompt": prompt, "siblings": siblings}


def _enforce_sequencing_or_die(loaded: dict[str, Any], *, mode: str) -> None:
    """Phase 1S clip-sequencing gate. Same rule for --dry-run / --submit:
    clip 1 always eligible; clip N>1 requires clip N-1 to be 'completed'.
    Refusing here is the safety mechanism that keeps clip 2 blocked while
    clip 1 is in flight or has failed.

    `mode` is purely for the error message wording.
    """
    job = loaded["job"]
    clip_number = job.get("clip_number")
    if clip_number is None or clip_number <= 1:
        return
    prior = next(
        (s for s in loaded["siblings"] if s.get("clip_number") == clip_number - 1),
        None,
    )
    prior_status = prior["status"] if prior else None
    if prior_status != "completed":
        raise SystemExit(
            f"FATAL: clip {clip_number} is BLOCKED — prior clip "
            f"{clip_number - 1} has status {prior_status!r} "
            f"(must be 'completed' before this clip can {mode}). "
            f"open_loop clip 1 must be generated, downloaded, reviewed, "
            f"and ingested first. Prior clip row: {prior}"
        )


def _enforce_status_draft_or_die(loaded: dict[str, Any], *, mode: str) -> None:
    """Only 'draft' clips proceed. Re-running a non-draft job is refused."""
    status = loaded["job"]["status"]
    if status != "draft":
        raise SystemExit(
            f"FATAL: clip job status is {status!r}; only 'draft' clips can "
            f"{mode}. Refusing to overwrite an in-flight or completed job."
        )


# --------------------------------------------------------------------------- #
# Payload composition — re-uses Phase 1G's builder via a synthesized
# DemoGenerationJob so the wire shape is identical to all prior real runs.
# --------------------------------------------------------------------------- #


def _build_payload_from_loaded(
    loaded: dict[str, Any],
    *,
    webhook_url: str,
    product_urls: Optional[list[str]],
    influencer_urls: Optional[list[str]],
) -> dict[str, Any]:
    job = loaded["job"]
    prompt = loaded["prompt"]
    demo_job = DemoGenerationJob(
        id=job["id"],
        batch_id=job["batch_id"],
        content_item_id=job["content_item_id"],
        prompt_version_id=job["prompt_version_id"],
        provider=job["provider"],
        provider_mode=job.get("provider_mode") or "ugc",
        quality_tier=job["quality_tier"],
        resolution=job["resolution"],
        duration_seconds=int(job["duration_seconds"]),
        status=job["status"],
        estimated_credits=int(job["estimated_credits"]),
        prompt_label=prompt.get("label"),
        prompt_hook=prompt.get("hook"),
        prompt_body=prompt.get("prompt_body"),
        prompt_negative=prompt.get("negative_prompt"),
        prompt_scene_plan=prompt.get("scene_plan"),
        prompt_creator_direction=prompt.get("creator_direction"),
        prompt_product_constraints=prompt.get("product_constraints"),
        content_title=None,
        content_caption_draft=None,
        placeholder_product_urls=tuple(),
        placeholder_influencer_urls=tuple(),
    )
    try:
        return build_seedance_payload_from_job(
            demo_job,
            webhook_url=webhook_url,
            product_urls=product_urls,
            influencer_urls=influencer_urls,
        )
    except ValueError as e:
        raise SystemExit(f"FATAL: payload validation failed: {e}") from e


# --------------------------------------------------------------------------- #
# Output helpers — same convention as run_generation_job.py so the
# ingester picks up clip artefacts without changes.
# --------------------------------------------------------------------------- #


def _job_out_dir(job_id: str, *, base: Optional[Path] = None) -> Path:
    base = base or DEFAULT_OUT_BASE
    out_dir = base / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _utc_iso_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _last_poll_path(out_dir: Path) -> Optional[Path]:
    polls = sorted(out_dir.glob("poll_*.json"))
    return polls[-1] if polls else None


# --------------------------------------------------------------------------- #
# Mode handlers
# --------------------------------------------------------------------------- #


def _do_dry_run(args: argparse.Namespace) -> int:
    loaded = _load_clip_job(args.job_id)
    _enforce_status_draft_or_die(loaded, mode="dry-run")
    _enforce_sequencing_or_die(loaded, mode="dry-run")

    payload = _build_payload_from_loaded(
        loaded,
        webhook_url=args.webhook_url,
        product_urls=args.product_url,
        influencer_urls=args.influencer_url,
    )
    out_dir = _job_out_dir(args.job_id, base=args.out_base)
    payload_path = out_dir / "payload.json"
    _write_json(payload_path, payload)

    warnings: list[str] = []
    placeholders = [
        u
        for u in (payload.get("products", []) + payload.get("influencers", []))
        if is_placeholder_url(u)
    ]
    if placeholders:
        warnings.append(
            "Placeholder URLs present — --submit will refuse until "
            "real public HTTPS URLs are passed via --product-url and "
            "--influencer-url."
        )
    if args.webhook_url == DEFAULT_WEBHOOK_URL:
        warnings.append(
            "Default placeholder webhook URL — pass --webhook-url before --submit."
        )

    job = loaded["job"]
    prompt = loaded["prompt"]
    snapshot = {
        "job_id": job["id"],
        "batch_id": job["batch_id"],
        "clip_number": job.get("clip_number"),
        "clip_role": job.get("clip_role"),
        "saved_to": str(payload_path),
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "job_summary": {
            "provider": job["provider"],
            "provider_mode": job.get("provider_mode") or "ugc",
            "quality_tier": job["quality_tier"],
            "resolution": job["resolution"],
            "duration_seconds": job["duration_seconds"],
            "estimated_credits": job["estimated_credits"],
            "prompt_label": prompt.get("label"),
            "prompt_version_number": prompt.get("version_number"),
        },
        "siblings": loaded["siblings"],
        "warnings": warnings,
        "audio_fixer": "NOT run (manual, never auto-triggered)",
    }
    print("--- PHASE 1T CLIP DRY-RUN ------------------------------------")
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print("\n--- PAYLOAD (saved to %s) ---" % payload_path)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("\nNO HTTP call was made. NO credits spent.")
    return 0


def _do_submit(args: argparse.Namespace) -> int:
    if not args.confirm:
        raise SystemExit(
            "FATAL: --submit requires --confirm. Re-run with --confirm to "
            "spend credits. (Or use --dry-run to validate the payload.)"
        )
    if args.webhook_url == DEFAULT_WEBHOOK_URL:
        raise SystemExit(
            "FATAL: refusing to --submit with the default placeholder "
            "webhook URL. Pass a real --webhook-url."
        )

    loaded = _load_clip_job(args.job_id)
    _enforce_status_draft_or_die(loaded, mode="submit")
    _enforce_sequencing_or_die(loaded, mode="submit")

    payload = _build_payload_from_loaded(
        loaded,
        webhook_url=args.webhook_url,
        product_urls=args.product_url,
        influencer_urls=args.influencer_url,
    )
    placeholder_assets = [
        u
        for u in (payload.get("products", []) + payload.get("influencers", []))
        if is_placeholder_url(u)
    ]
    if placeholder_assets:
        raise SystemExit(
            "FATAL: refusing to --submit — payload contains placeholder URLs: "
            f"{placeholder_assets}. Pass real public HTTPS URLs via "
            "--product-url and --influencer-url."
        )

    job = loaded["job"]
    out_dir = _job_out_dir(args.job_id, base=args.out_base)
    _write_json(out_dir / "payload.json", payload)

    print("--- PHASE 1T CLIP SUBMIT (PAID) ------------------------------")
    print(f"job_id:            {job['id']}")
    print(f"batch_id:          {job['batch_id']}")
    print(f"clip_number:       {job.get('clip_number')}")
    print(f"clip_role:         {job.get('clip_role')}")
    print(f"provider:          {job['provider']}")
    print(f"provider_mode:     {job.get('provider_mode') or 'ugc'}")
    print(f"quality_tier:      {job['quality_tier']}")
    print(f"resolution:        {job['resolution']}")
    print(f"duration_seconds:  {job['duration_seconds']}")
    print(f"estimated_credits: {job['estimated_credits']}")
    print(f"products:          {payload.get('products')}")
    print(f"influencers:       {payload.get('influencers')}")
    print(f"webhook_url:       {args.webhook_url}")
    print("--------------------------------------------------------------")

    api_key = _resolve_api_key()
    provider = EnhancorSeedanceProvider(api_key=api_key)
    request = ProviderJobRequest(
        provider=provider.name,
        job_type="ugc",
        payload=payload,
        correlation_id=job["id"],
    )
    try:
        response = provider.submit_job(request)
    except ProviderError as e:
        out_path = out_dir / f"submit_error_{_utc_iso_tag()}.json"
        _write_json(
            out_path,
            {
                "job_id": job["id"],
                "error": str(e),
                "http_status": e.http_status,
                "raw_response": e.raw_response,
                "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise SystemExit(
            f"ProviderError on /queue: {e} (saved to {out_path})"
        ) from e

    payload_for_disk = asdict(response)
    payload_for_disk["submitted_at"] = response.submitted_at.isoformat()
    payload_for_disk["status"] = response.status.value
    out_path = out_dir / "submit_response.json"
    _write_json(out_path, payload_for_disk)

    print("\nSUBMITTED.")
    print(f"provider_request_id: {response.provider_job_id}")
    print(f"saved_to:            {out_path}")
    print(
        "\nNext step: py -3.11 scripts/run_clip_generation_job.py "
        f"--job-id {job['id']} --poll"
    )
    return 0


def _do_poll(args: argparse.Namespace) -> int:
    out_dir = _job_out_dir(args.job_id, base=args.out_base)
    submit_path = out_dir / "submit_response.json"
    if not submit_path.exists():
        raise SystemExit(
            f"FATAL: no submit_response.json under {out_dir}. Run --submit first."
        )
    submit_blob = _read_json(submit_path)
    provider_request_id: Optional[str] = submit_blob.get("provider_job_id")
    if not provider_request_id:
        raise SystemExit(
            "FATAL: submit_response.json has no provider_job_id field."
        )

    api_key = _resolve_api_key()
    provider = EnhancorSeedanceProvider(api_key=api_key)
    try:
        snapshot = provider.poll_status(
            provider_request_id, correlation_id=args.job_id,
        )
    except ProviderError as e:
        out_path = out_dir / f"poll_error_{_utc_iso_tag()}.json"
        _write_json(
            out_path,
            {
                "job_id": args.job_id,
                "provider_request_id": provider_request_id,
                "error": str(e),
                "http_status": e.http_status,
                "raw_response": e.raw_response,
                "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise SystemExit(
            f"ProviderError on /status: {e} (saved to {out_path})"
        ) from e

    snapshot_dict = EnhancorSeedanceProvider.serialise_status(snapshot)
    poll_path = out_dir / f"poll_{_utc_iso_tag()}.json"
    _write_json(poll_path, snapshot_dict)

    print("--- PHASE 1T CLIP POLL ---------------------------------------")
    print(f"job_id:              {args.job_id}")
    print(f"provider_request_id: {provider_request_id}")
    print(f"status:              {snapshot.status.value}")
    if snapshot.result_url:
        print(f"result_url:          {snapshot.result_url}")
    if snapshot.thumbnail_url:
        print(f"thumbnail_url:       {snapshot.thumbnail_url}")
    if snapshot.cost is not None:
        print(f"cost:                {snapshot.cost}")
    if snapshot.error_message:
        print(f"error_message:       {snapshot.error_message}")
    print(f"saved_to:            {poll_path}")
    if snapshot.status == ProviderStatus.COMPLETED:
        print(
            "\nReady to --download. Audio Fixer is manual — this runner "
            "does NOT trigger it."
        )
        return 0
    if snapshot.status == ProviderStatus.FAILED:
        print("\nClip FAILED. Inspect the raw response above + audit your prompt.")
        return 2
    print("\nNot terminal yet. Re-run --poll later.")
    return 0


def _do_download(args: argparse.Namespace) -> int:
    out_dir = _job_out_dir(args.job_id, base=args.out_base)
    last = _last_poll_path(out_dir)
    if last is None:
        raise SystemExit(
            f"FATAL: no poll_*.json under {out_dir}. Run --poll first."
        )
    poll_blob = _read_json(last)
    if poll_blob.get("status") != ProviderStatus.COMPLETED.value:
        raise SystemExit(
            f"FATAL: last poll status is {poll_blob.get('status')!r}; "
            "only COMPLETED clips can be downloaded."
        )
    result_url: Optional[str] = poll_blob.get("result_url")
    if not result_url:
        raise SystemExit(
            "FATAL: last poll has no result_url. Re-run --poll, then --download."
        )

    api_key = _resolve_api_key()
    provider = EnhancorSeedanceProvider(api_key=api_key)
    dest = out_dir / "result.mp4"
    try:
        provider.download_result(result_url, dest)
    except ProviderError as e:
        raise SystemExit(f"ProviderError on download: {e}") from e

    meta_path = out_dir / "result_meta.json"
    _write_json(
        meta_path,
        {
            "job_id": args.job_id,
            "result_url": result_url,
            "thumbnail_url": poll_blob.get("thumbnail_url"),
            "cost": poll_blob.get("cost"),
            "saved_to": str(dest),
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )

    print("--- PHASE 1T CLIP DOWNLOAD -----------------------------------")
    print(f"job_id:        {args.job_id}")
    print(f"result_url:    {result_url}")
    print(f"saved_to:      {dest}")
    print(f"meta:          {meta_path}")
    if poll_blob.get("thumbnail_url"):
        print(f"thumbnail_url: {poll_blob['thumbnail_url']}")
    if poll_blob.get("cost") is not None:
        print(f"actual_cost:   {poll_blob['cost']}")
    print(
        "\nNext step (Phase 1H, idempotent, no paid call):\n"
        "  py -3.11 scripts/ingest_generation_job_run.py --job-id "
        f"{args.job_id} --dry-run"
    )
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_clip_generation_job",
        description=(
            "Supabase-aware per-clip Seedance runner. Same lifecycle as "
            "run_generation_job.py (--dry-run / --submit / --poll / "
            "--download) but reads the job + prompt from Supabase and "
            "enforces the Phase 1S clip-sequencing gate."
        ),
    )
    p.add_argument(
        "--job-id",
        required=True,
        help="generation_jobs.id (uuid) — the clip to operate on.",
    )

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + validate the Seedance payload. NO HTTP call.",
    )
    mode.add_argument(
        "--submit",
        action="store_true",
        help=(
            "PAID. Validate the payload + sequencing, then POST to "
            "Seedance /queue. Requires --confirm and real URLs."
        ),
    )
    mode.add_argument(
        "--poll",
        action="store_true",
        help=(
            "POST to /status for a previously-submitted clip and save "
            "poll_<utc>.json under the clip's job folder."
        ),
    )
    mode.add_argument(
        "--download",
        action="store_true",
        help="Stream the terminal-success MP4 to disk + write result_meta.json.",
    )

    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required for --submit. Confirms the operator understands credits will be spent.",
    )
    p.add_argument(
        "--product-url",
        action="append",
        default=None,
        help=(
            "Public HTTPS URL of a product reference image. Repeat for "
            "multiple. Required for --submit; the runner refuses "
            "placeholder URLs."
        ),
    )
    p.add_argument(
        "--influencer-url",
        action="append",
        default=None,
        help=(
            "Public HTTPS URL of an influencer reference image. Repeat "
            "for multiple. Required for --submit."
        ),
    )
    p.add_argument(
        "--webhook-url",
        default=os.environ.get("WEBHOOK_URL", DEFAULT_WEBHOOK_URL),
        help=(
            "Public HTTPS webhook URL Seedance calls when the job hits a "
            "terminal state. Required for --submit; the runner refuses "
            "the default placeholder."
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
        help="DEBUG logging (still redacts the API key).",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Windows cp1252 console mangles em-dashes and é in the payload; force
    # UTF-8 so the prompt prints faithfully.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    _load_dotenv_if_available()

    if args.dry_run:
        return _do_dry_run(args)
    if args.submit:
        return _do_submit(args)
    if args.poll:
        return _do_poll(args)
    if args.download:
        return _do_download(args)
    parser.error("no mode selected")


if __name__ == "__main__":
    sys.exit(main())
