"""Phase 1G operator-driven generation job runner.

The Yuvo OS dashboard (Next.js) never makes a paid Enhancor call directly.
Instead, the operator copies the command this script exposes into their
terminal, this script runs the paid call, and Phase 1H will ingest the
resulting JSON artefacts back into Supabase.

Why this is the safest minimum:
  - The dashboard process can never accidentally spend credits.
  - ENHANCOR_API_KEY stays in the operator's local .env; it never touches
    the Next.js runtime.
  - Every wire payload is saved to disk under
    prospects/pai-skincare/production/dashboard_job_runs/<job-id>/
    so the operator can audit before any further automation lands.

Lifecycle:

    --dry-run     : build + validate the Seedance payload, print + save it
                    to payload.json. NO HTTP call.
    --submit      : runs --dry-run, then POSTs to /queue. Saves the
                    response under submit_response.json. Requires both
                    --confirm and a real (non-placeholder) --product-url +
                    --influencer-url.
    --poll        : reads submit_response.json, POSTs to /status, saves
                    poll_<utc-iso>.json. Idempotent.
    --download    : reads the latest poll, downloads result.mp4 +
                    captures thumbnail metadata. Idempotent.

Audio Fixer is NOT supported by this script. Audio Fixer is manual and
will be wired in a later phase under a separate confirmation gate.

Hard rules:
  - Loads ENHANCOR_API_KEY from .env via python-dotenv.
  - Refuses to print the API key or any header that carries it.
  - Defaults to 720p / 15s / standard_720p — never auto-promotes to 1080p.
  - --submit refuses to run while any product/influencer URL is a
    placeholder, and requires --confirm.
  - --submit prints exactly which job + estimated credits will be spent
    before the call.

Usage:

    # SAFE — no API call:
    py -3.11 scripts/run_generation_job.py \\
        --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \\
        --dry-run

    # PAID — real credits will be spent:
    py -3.11 scripts/run_generation_job.py \\
        --job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \\
        --product-url https://your-cdn/pai-bottle.jpg \\
        --influencer-url https://your-cdn/creator.jpg \\
        --webhook-url https://your-hooks/enhancor/seedance \\
        --submit --confirm

    py -3.11 scripts/run_generation_job.py --job-id <id> --poll
    py -3.11 scripts/run_generation_job.py --job-id <id> --download
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Project-root sys.path shim (script invocation) ─────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.producer.dashboard import (  # noqa: E402
    build_seedance_payload_from_job,
    find_demo_job,
)
from agents.producer.dashboard.demo_jobs import is_placeholder_url  # noqa: E402
from agents.producer.providers.base import (  # noqa: E402
    ProviderError,
    ProviderJobRequest,
    ProviderStatus,
)
from agents.producer.providers.enhancor_seedance import (  # noqa: E402
    EnhancorSeedanceProvider,
)

log = logging.getLogger("yuvo.dashboard.run_generation_job")

DEFAULT_OUT_BASE = (
    _REPO_ROOT / "prospects" / "pai-skincare" / "production" / "dashboard_job_runs"
)
DEFAULT_WEBHOOK_URL = (
    "https://example.com/webhooks/enhancor/seedance"
)
"""Placeholder fallback webhook. --submit refuses to run with this URL
in place unless the operator explicitly passes --allow-placeholder-webhook
(undocumented; reserved for future polling-only flows)."""


# --------------------------------------------------------------------------- #
# Dotenv + API key plumbing (mirrors scripts/enhancor_smoke_test.py)
# --------------------------------------------------------------------------- #


def _load_dotenv_if_available() -> None:
    """Load .env without crashing if python-dotenv isn't installed."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    load_dotenv(_REPO_ROOT / ".env")


def _resolve_api_key() -> str:
    """Read ENHANCOR_API_KEY without ever printing it.

    We strip the value and refuse to log it. The error message describes
    the fix without echoing the existing (broken / empty) value.
    """
    key = os.environ.get("ENHANCOR_API_KEY")
    if not key or not key.strip():
        raise SystemExit(
            "FATAL: ENHANCOR_API_KEY is not set. Add it to .env "
            "(see .env.example) or your shell, then re-run."
        )
    return key.strip()


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #


def _job_out_dir(job_id: str, *, base: Optional[Path] = None) -> Path:
    """Where this job's artefacts live on disk."""
    base = base or DEFAULT_OUT_BASE
    out_dir = base / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _utc_iso_tag() -> str:
    """ISO8601-ish UTC timestamp, safe for filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, obj: Any) -> None:
    """JSON dump that preserves UTF-8 + 2-space indent + sorted keys."""
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
# Dry-run
# --------------------------------------------------------------------------- #


def _build_payload_or_die(
    *,
    job_id: str,
    webhook_url: str,
    product_urls: Optional[list[str]],
    influencer_urls: Optional[list[str]],
) -> dict[str, Any]:
    """Load the job + build the wire payload. Exits non-zero on bad input."""
    job = find_demo_job(job_id)
    if job is None:
        raise SystemExit(
            f"FATAL: job id {job_id!r} not found in the demo catalogue.\n"
            "Phase 1G only reads from agents/producer/dashboard/demo_jobs.py. "
            "Supabase-backed reads land in Phase 1H."
        )
    try:
        payload = build_seedance_payload_from_job(
            job,
            webhook_url=webhook_url,
            product_urls=product_urls,
            influencer_urls=influencer_urls,
        )
    except ValueError as e:
        raise SystemExit(f"FATAL: payload validation failed: {e}") from e
    # Strategic 720p default sanity check.
    if job.resolution == "1080p":
        log.warning(
            "Job %s targets 1080p — Phase 1G allows it but the strategic "
            "default is 720p. Confirm intent before --submit.",
            job_id,
        )
    return payload


def _do_dry_run(args: argparse.Namespace) -> int:
    """Build the payload, print + save it. NO HTTP call."""
    job = find_demo_job(args.job_id)
    if job is None:
        raise SystemExit(
            f"FATAL: job id {args.job_id!r} not found in the demo catalogue."
        )
    payload = _build_payload_or_die(
        job_id=args.job_id,
        webhook_url=args.webhook_url,
        product_urls=args.product_url,
        influencer_urls=args.influencer_url,
    )
    out_dir = _job_out_dir(args.job_id, base=args.out_base)
    payload_path = out_dir / "payload.json"
    _write_json(payload_path, payload)

    snapshot = {
        "job_id": args.job_id,
        "saved_to": str(payload_path),
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "job_summary": {
            "content_title": job.content_title,
            "prompt_label": job.prompt_label,
            "provider": job.provider,
            "provider_mode": job.provider_mode,
            "quality_tier": job.quality_tier,
            "resolution": job.resolution,
            "duration_seconds": job.duration_seconds,
            "estimated_credits": job.estimated_credits,
        },
        "warnings": [],
    }
    placeholders_present = any(
        is_placeholder_url(u) for u in payload.get("products", [])
    ) or any(is_placeholder_url(u) for u in payload.get("influencers", []))
    if placeholders_present:
        snapshot["warnings"].append(
            "Placeholder URLs present — --submit will refuse until "
            "--product-url and --influencer-url are real."
        )
    if args.webhook_url == DEFAULT_WEBHOOK_URL:
        snapshot["warnings"].append(
            "Default placeholder webhook URL — pass --webhook-url before --submit."
        )

    print("--- PHASE 1G DRY-RUN -----------------------------------------")
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print("\n--- PAYLOAD (saved to %s) ---" % payload_path)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("\nNO HTTP call was made. NO credits spent.")
    return 0


# --------------------------------------------------------------------------- #
# Submit (paid)
# --------------------------------------------------------------------------- #


def _do_submit(args: argparse.Namespace) -> int:
    """Real Seedance submission. Requires --confirm + real URLs."""
    if not args.confirm:
        raise SystemExit(
            "FATAL: --submit requires --confirm. Re-run with --confirm to "
            "spend credits. (Or use --dry-run to validate the payload.)"
        )

    payload = _build_payload_or_die(
        job_id=args.job_id,
        webhook_url=args.webhook_url,
        product_urls=args.product_url,
        influencer_urls=args.influencer_url,
    )

    # Refuse to submit with placeholder asset URLs in place.
    placeholder_assets = [
        u
        for u in (payload.get("products", []) + payload.get("influencers", []))
        if is_placeholder_url(u)
    ]
    if placeholder_assets:
        raise SystemExit(
            "FATAL: refusing to --submit — payload still contains "
            f"placeholder URLs: {placeholder_assets}. Pass real public HTTPS "
            "URLs via --product-url and --influencer-url."
        )
    if args.webhook_url == DEFAULT_WEBHOOK_URL:
        raise SystemExit(
            "FATAL: refusing to --submit with the default placeholder "
            "webhook URL. Pass a real --webhook-url."
        )

    job = find_demo_job(args.job_id)
    assert job is not None  # _build_payload_or_die already validated

    out_dir = _job_out_dir(args.job_id, base=args.out_base)
    _write_json(out_dir / "payload.json", payload)

    print("--- PHASE 1G SUBMIT (PAID) -----------------------------------")
    print(f"job_id:            {args.job_id}")
    print(f"provider:          {job.provider}")
    print(f"provider_mode:     {job.provider_mode}")
    print(f"quality_tier:      {job.quality_tier}")
    print(f"resolution:        {job.resolution}")
    print(f"duration_seconds:  {job.duration_seconds}")
    print(f"estimated_credits: {job.estimated_credits}")
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
        correlation_id=args.job_id,
    )
    try:
        response = provider.submit_job(request)
    except ProviderError as e:
        # ProviderError already redacts headers; print without exposing the key.
        out_path = out_dir / f"submit_error_{_utc_iso_tag()}.json"
        _write_json(
            out_path,
            {
                "job_id": args.job_id,
                "error": str(e),
                "http_status": e.http_status,
                "raw_response": e.raw_response,
                "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise SystemExit(f"ProviderError on /queue: {e} (saved to {out_path})") from e

    # Persist the accepted-handshake response. ProviderJobResponse is a
    # frozen dataclass; asdict() turns it into JSON-safe primitives.
    payload_for_disk = asdict(response)
    payload_for_disk["submitted_at"] = response.submitted_at.isoformat()
    payload_for_disk["status"] = response.status.value
    out_path = out_dir / "submit_response.json"
    _write_json(out_path, payload_for_disk)

    print("\nSUBMITTED.")
    print(f"provider_request_id: {response.provider_job_id}")
    print(f"saved_to:            {out_path}")
    print(
        "\nNext step: py -3.11 scripts/run_generation_job.py "
        f"--job-id {args.job_id} --poll"
    )
    return 0


# --------------------------------------------------------------------------- #
# Poll
# --------------------------------------------------------------------------- #


def _do_poll(args: argparse.Namespace) -> int:
    """One /status poll. Reads provider_request_id from submit_response.json."""
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
        raise SystemExit(f"ProviderError on /status: {e} (saved to {out_path})") from e

    snapshot_dict = EnhancorSeedanceProvider.serialise_status(snapshot)
    poll_path = out_dir / f"poll_{_utc_iso_tag()}.json"
    _write_json(poll_path, snapshot_dict)

    print("--- PHASE 1G POLL --------------------------------------------")
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
            "\nReady to --download. Audio Fixer is manual — Phase 1G "
            "does NOT run it."
        )
        return 0
    if snapshot.status == ProviderStatus.FAILED:
        print("\nJob FAILED. Inspect the raw response above + audit your prompt.")
        return 2
    print("\nNot terminal yet. Re-run --poll later.")
    return 0


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #


def _do_download(args: argparse.Namespace) -> int:
    """Stream the terminal-success media to disk from the last poll."""
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
            "only COMPLETED jobs can be downloaded."
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

    print("--- PHASE 1G DOWNLOAD ----------------------------------------")
    print(f"job_id:        {args.job_id}")
    print(f"result_url:    {result_url}")
    print(f"saved_to:      {dest}")
    print(f"meta:          {meta_path}")
    if poll_blob.get("thumbnail_url"):
        print(f"thumbnail_url: {poll_blob['thumbnail_url']}")
    if poll_blob.get("cost") is not None:
        print(f"actual_cost:   {poll_blob['cost']}")
    print(
        "\nPhase 1H will ingest this into Supabase generated_assets + "
        "generation_jobs.actual_credits."
    )
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_generation_job",
        description=(
            "Phase 1G operator-driven generation job runner. The Yuvo "
            "dashboard prints the exact command; the operator runs it "
            "here so the dashboard process never spends credits "
            "accidentally."
        ),
    )
    p.add_argument(
        "--job-id",
        required=True,
        help=(
            "generation_jobs.id (uuid). Phase 1G looks the job up in "
            "agents/producer/dashboard/demo_jobs.py."
        ),
    )

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + validate + save the payload. NO HTTP call.",
    )
    mode.add_argument(
        "--submit",
        action="store_true",
        help=(
            "PAID. POST the payload to Seedance /queue. Requires --confirm + "
            "real --product-url + --influencer-url + --webhook-url."
        ),
    )
    mode.add_argument(
        "--poll",
        action="store_true",
        help="POST to /status for the previously-submitted job.",
    )
    mode.add_argument(
        "--download",
        action="store_true",
        help="Stream the terminal-success media to disk after a completed --poll.",
    )

    p.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Required for --submit. Confirms the operator understands "
            "this will spend credits."
        ),
    )
    p.add_argument(
        "--product-url",
        action="append",
        default=None,
        help=(
            "Public HTTPS product image URL. Repeatable. Defaults to the "
            "job's placeholder; --submit refuses to run with placeholders."
        ),
    )
    p.add_argument(
        "--influencer-url",
        action="append",
        default=None,
        help=(
            "Public HTTPS influencer image URL. Repeatable. Defaults to the "
            "job's placeholder; --submit refuses to run with placeholders."
        ),
    )
    p.add_argument(
        "--webhook-url",
        default=os.environ.get("WEBHOOK_URL", DEFAULT_WEBHOOK_URL),
        help=(
            "Public HTTPS webhook URL Seedance calls on terminal state. "
            "Mandatory; --submit refuses to run with the placeholder default. "
            "Falls back to env WEBHOOK_URL."
        ),
    )
    p.add_argument(
        "--out-base",
        type=Path,
        default=DEFAULT_OUT_BASE,
        help=(
            "Where to write per-job artefacts. Default: "
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


def _force_utf8_stdout() -> None:
    """Windows defaults the console to cp1252; the seeded Pai prompt
    body uses en-dash + typographic apostrophes which the cp1252 codec
    can't encode. Force the stream to utf-8 on Python 3.7+ so the
    print() calls don't crash. No-op on platforms that already use utf-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                # Older Python or wrapped stream — ignore; the caller
                # will see a CharmapError instead of a silent corruption.
                pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _force_utf8_stdout()
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
    # mutually-exclusive required=True guarantees we never reach here.
    parser.error("no mode selected")


if __name__ == "__main__":
    sys.exit(main())
