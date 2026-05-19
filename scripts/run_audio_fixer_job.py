"""Phase 1I operator-driven Audio Fixer runner.

Mirror of `scripts/run_generation_job.py`, scoped to the Audio Fixer
provider. Audio Fixer is MANUAL: this script never auto-runs and
Phase 1I exercises only the `--dry-run` path. The `--submit / --poll /
--download` lifecycle is implemented behind the same typed-confirmation
gates as the Seedance runner, but Phase 1I does NOT call those paths
itself — they exist for the operator to invoke later, on explicit
approval, only when raw audio quality demands it.

For the seeded Pai 720p result, the raw video already carries a native
AAC track (confirmed by Phase 1I's stdlib MP4 atom probe:
`audio_codec='mp4a', has_audio_track=True`). The dashboard recommends
SKIPPING Audio Fixer in that case.

Hard rules:
  - Default mode is --dry-run. The script refuses to do anything else
    until --submit / --poll / --download is selected explicitly.
  - --submit requires --confirm. The runner refuses placeholder webhook.
  - Loads ENHANCOR_API_KEY from .env (never an argv); never prints it.
  - All artefacts saved under
      prospects/pai-skincare/production/dashboard_job_runs/<generation-job-id>/audio_fixer/
    so the Phase 1H ingester can later pick them up without changing
    its run-folder discovery rules.
  - The runner does NOT mutate Supabase. Phase 1H+'s ingester is the
    only path that does.

Usage:

    # SAFE — no API call:
    py -3.11 scripts/run_audio_fixer_job.py \\
        --generation-job-id 1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b \\
        --dry-run

    # PAID — Phase 1I does NOT run this; for future operator use only:
    py -3.11 scripts/run_audio_fixer_job.py \\
        --generation-job-id <jobId> \\
        --webhook-url https://your-hooks/audio-fixer \\
        --submit --confirm
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

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.producer.providers.base import (  # noqa: E402
    ProviderError,
    ProviderJobRequest,
    ProviderStatus,
)
from agents.producer.providers.enhancor_audio_fixer import (  # noqa: E402
    EnhancorAudioFixerProvider,
    build_audio_fixer_payload,
)

log = logging.getLogger("yuvo.dashboard.run_audio_fixer_job")

# Audio Fixer flat estimate, from the Pai 15s reference run (logged
# 2,103.75; rounded to 2,104 in the seed; ≈2,100 in operator-facing
# copy). Phase 1I keeps the same number on the dashboard cost panel.
DEFAULT_AUDIO_FIXER_CREDITS_ESTIMATE = 2100

DEFAULT_RUNS_BASE = (
    _REPO_ROOT / "prospects" / "pai-skincare" / "production" / "dashboard_job_runs"
)
DEFAULT_WEBHOOK_URL = "https://example.com/webhooks/enhancor/audio-fixer"


# --------------------------------------------------------------------------- #
# Helpers (mirror run_generation_job.py for consistency)
# --------------------------------------------------------------------------- #


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    load_dotenv(_REPO_ROOT / ".env")


def _resolve_api_key() -> str:
    key = os.environ.get("ENHANCOR_API_KEY")
    if not key or not key.strip():
        raise SystemExit(
            "FATAL: ENHANCOR_API_KEY is not set. Add it to .env "
            "(see .env.example) or your shell, then re-run."
        )
    return key.strip()


def _utc_iso_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Locate the raw video URL for a given generation_job_id
# --------------------------------------------------------------------------- #


def _resolve_raw_video_url(generation_job_id: str, base: Path) -> tuple[str, Path]:
    """Read the parent generation job's run folder and return the result
    URL of the latest completed poll, plus the folder Path. Refuses if
    the job hasn't reached COMPLETED.
    """
    run_dir = base / generation_job_id
    if not run_dir.is_dir():
        raise SystemExit(
            f"FATAL: no generation job run folder at {run_dir}. The Audio "
            "Fixer can only run after a completed Seedance generation; run "
            "scripts/run_generation_job.py --submit/--poll/--download first."
        )

    # Prefer result_meta.json (compact); fall back to the latest poll.
    meta_path = run_dir / "result_meta.json"
    if meta_path.exists():
        meta = _read_json(meta_path)
        url = meta.get("result_url")
        if isinstance(url, str) and url:
            return url, run_dir

    poll_paths = sorted(run_dir.glob("poll_*.json"))
    for p in reversed(poll_paths):
        blob = _read_json(p)
        if blob.get("status") == ProviderStatus.COMPLETED.value:
            url = blob.get("result_url")
            if isinstance(url, str) and url:
                return url, run_dir

    raise SystemExit(
        f"FATAL: no completed result URL on file under {run_dir}. The latest "
        "Seedance poll is not COMPLETED yet, or result_meta.json was not "
        "written. Re-run scripts/run_generation_job.py --poll / --download."
    )


def _audio_fixer_out_dir(run_dir: Path) -> Path:
    out = run_dir / "audio_fixer"
    out.mkdir(parents=True, exist_ok=True)
    return out


# --------------------------------------------------------------------------- #
# Dry-run (Phase 1I default and only-supported flow today)
# --------------------------------------------------------------------------- #


def _do_dry_run(args: argparse.Namespace) -> int:
    input_video_url, run_dir = _resolve_raw_video_url(
        args.generation_job_id, args.out_base
    )
    try:
        payload = build_audio_fixer_payload(
            input_video_url=input_video_url,
            webhook_url=args.webhook_url,
        )
    except ValueError as e:
        raise SystemExit(f"FATAL: payload validation failed: {e}") from e

    out_dir = _audio_fixer_out_dir(run_dir)
    payload_path = out_dir / "payload.json"
    _write_json(payload_path, payload)

    warnings: list[str] = []
    if args.webhook_url == DEFAULT_WEBHOOK_URL:
        warnings.append(
            "Placeholder webhook URL — --submit would refuse until a real "
            "webhook is passed. Phase 1I doesn't run --submit anyway."
        )

    snapshot = {
        "generation_job_id": args.generation_job_id,
        "saved_to": str(payload_path),
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "provider": "enhancor_audio_fixer",
            "input_video_url": input_video_url,
            "webhook_url": args.webhook_url,
            "estimated_credits": DEFAULT_AUDIO_FIXER_CREDITS_ESTIMATE,
        },
        "warnings": warnings,
        "phase_policy": (
            "Phase 1I never runs --submit. Audio Fixer is manual, "
            "operator-driven CLI. Only run if raw audio needs improvement."
        ),
    }

    print("--- PHASE 1I AUDIO FIXER DRY-RUN -----------------------------")
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print("\n--- PAYLOAD (saved to %s) ---" % payload_path)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("\nNO HTTP call was made. NO credits spent.")
    return 0


# --------------------------------------------------------------------------- #
# Submit / Poll / Download — implemented for future use; Phase 1I never
# invokes them. The runner's CLI does not gate the modes (so an operator
# can run them later) but every one of them prints a one-line banner
# stating it is paid before doing anything.
# --------------------------------------------------------------------------- #


def _do_submit(args: argparse.Namespace) -> int:
    if not args.confirm:
        raise SystemExit(
            "FATAL: --submit requires --confirm. Re-run with --confirm to "
            "spend credits. (Or use --dry-run to validate the payload.)"
        )
    input_video_url, run_dir = _resolve_raw_video_url(
        args.generation_job_id, args.out_base
    )
    if args.webhook_url == DEFAULT_WEBHOOK_URL:
        raise SystemExit(
            "FATAL: refusing to --submit with the default placeholder "
            "webhook URL. Pass a real --webhook-url."
        )

    payload = build_audio_fixer_payload(
        input_video_url=input_video_url,
        webhook_url=args.webhook_url,
    )
    out_dir = _audio_fixer_out_dir(run_dir)
    _write_json(out_dir / "payload.json", payload)

    print("--- PHASE 1I AUDIO FIXER SUBMIT (PAID) -----------------------")
    print(f"generation_job_id: {args.generation_job_id}")
    print(f"input_video_url:   {input_video_url}")
    print(f"webhook_url:       {args.webhook_url}")
    print(f"estimated_credits: {DEFAULT_AUDIO_FIXER_CREDITS_ESTIMATE}")
    print("--------------------------------------------------------------")

    api_key = _resolve_api_key()
    provider = EnhancorAudioFixerProvider(api_key=api_key)
    request = ProviderJobRequest(
        provider=provider.name,
        job_type="audio-fix",
        payload=payload,
        correlation_id=args.generation_job_id,
    )
    try:
        response = provider.submit_job(request)
    except ProviderError as e:
        out_path = out_dir / f"submit_error_{_utc_iso_tag()}.json"
        _write_json(
            out_path,
            {
                "generation_job_id": args.generation_job_id,
                "error": str(e),
                "http_status": e.http_status,
                "raw_response": e.raw_response,
                "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise SystemExit(f"ProviderError on /queue: {e} (saved to {out_path})") from e

    payload_for_disk = asdict(response)
    payload_for_disk["submitted_at"] = response.submitted_at.isoformat()
    payload_for_disk["status"] = response.status.value
    _write_json(out_dir / "submit_response.json", payload_for_disk)

    print("\nSUBMITTED.")
    print(f"provider_request_id: {response.provider_job_id}")
    print(
        "\nNext step: py -3.11 scripts/run_audio_fixer_job.py "
        f"--generation-job-id {args.generation_job_id} --poll"
    )
    return 0


def _do_poll(args: argparse.Namespace) -> int:
    _, run_dir = _resolve_raw_video_url(args.generation_job_id, args.out_base)
    out_dir = _audio_fixer_out_dir(run_dir)
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
    provider = EnhancorAudioFixerProvider(api_key=api_key)
    try:
        snapshot = provider.poll_status(
            provider_request_id, correlation_id=args.generation_job_id,
        )
    except ProviderError as e:
        out_path = out_dir / f"poll_error_{_utc_iso_tag()}.json"
        _write_json(
            out_path,
            {
                "generation_job_id": args.generation_job_id,
                "provider_request_id": provider_request_id,
                "error": str(e),
                "http_status": e.http_status,
                "raw_response": e.raw_response,
                "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise SystemExit(f"ProviderError on /status: {e} (saved to {out_path})") from e

    snapshot_dict = EnhancorAudioFixerProvider.serialise_status(snapshot)
    poll_path = out_dir / f"poll_{_utc_iso_tag()}.json"
    _write_json(poll_path, snapshot_dict)

    print("--- PHASE 1I AUDIO FIXER POLL --------------------------------")
    print(f"generation_job_id:   {args.generation_job_id}")
    print(f"provider_request_id: {provider_request_id}")
    print(f"status:              {snapshot.status.value}")
    if snapshot.result_url:
        print(f"result_url:          {snapshot.result_url}")
    if snapshot.cost is not None:
        print(f"cost:                {snapshot.cost}")
    print(f"saved_to:            {poll_path}")
    if snapshot.status == ProviderStatus.COMPLETED:
        print("\nReady to --download.")
        return 0
    if snapshot.status == ProviderStatus.FAILED:
        print("\nJob FAILED. Inspect the raw response above.")
        return 2
    print("\nNot terminal yet. Re-run --poll later.")
    return 0


def _do_download(args: argparse.Namespace) -> int:
    _, run_dir = _resolve_raw_video_url(args.generation_job_id, args.out_base)
    out_dir = _audio_fixer_out_dir(run_dir)
    poll_paths = sorted(out_dir.glob("poll_*.json"))
    if not poll_paths:
        raise SystemExit(
            f"FATAL: no poll_*.json under {out_dir}. Run --poll first."
        )
    last_poll = _read_json(poll_paths[-1])
    if last_poll.get("status") != ProviderStatus.COMPLETED.value:
        raise SystemExit(
            f"FATAL: last poll status is {last_poll.get('status')!r}; "
            "only COMPLETED jobs can be downloaded."
        )
    result_url: Optional[str] = last_poll.get("result_url")
    if not result_url:
        raise SystemExit("FATAL: last poll has no result_url.")

    api_key = _resolve_api_key()
    provider = EnhancorAudioFixerProvider(api_key=api_key)
    dest = out_dir / "result.mp4"
    try:
        provider.download_result(result_url, dest)
    except ProviderError as e:
        raise SystemExit(f"ProviderError on download: {e}") from e

    meta_path = out_dir / "result_meta.json"
    _write_json(
        meta_path,
        {
            "generation_job_id": args.generation_job_id,
            "result_url": result_url,
            "cost": last_poll.get("cost"),
            "saved_to": str(dest),
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )

    print("--- PHASE 1I AUDIO FIXER DOWNLOAD ----------------------------")
    print(f"generation_job_id: {args.generation_job_id}")
    print(f"result_url:        {result_url}")
    print(f"saved_to:          {dest}")
    print(f"meta:              {meta_path}")
    if last_poll.get("cost") is not None:
        print(f"actual_cost:       {last_poll['cost']}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_audio_fixer_job",
        description=(
            "Phase 1I operator-driven Audio Fixer runner. The dashboard "
            "never makes the paid call itself; the operator runs this "
            "script with explicit --confirm gates. Phase 1I exercises "
            "only --dry-run; --submit / --poll / --download are present "
            "for future operator use but are not invoked by Phase 1I."
        ),
    )
    p.add_argument(
        "--generation-job-id",
        required=True,
        help=(
            "Parent generation_jobs.id (uuid). The Audio Fixer reads the "
            "raw video result URL from the matching run folder."
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + save the Audio Fixer payload. NO HTTP call.",
    )
    mode.add_argument(
        "--submit",
        action="store_true",
        help=(
            "PAID. POST the payload to Audio Fixer /queue. Requires "
            "--confirm and a real --webhook-url. Phase 1I does NOT run "
            "this — present for future operator use."
        ),
    )
    mode.add_argument(
        "--poll",
        action="store_true",
        help="POST to /status for a previously-submitted audio fixer job.",
    )
    mode.add_argument(
        "--download",
        action="store_true",
        help="Stream the terminal-success media to disk.",
    )

    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required for --submit. Confirms operator understands credit spend.",
    )
    p.add_argument(
        "--webhook-url",
        default=os.environ.get(
            "AUDIO_FIXER_WEBHOOK_URL",
            os.environ.get("WEBHOOK_URL", DEFAULT_WEBHOOK_URL),
        ),
        help=(
            "Public HTTPS webhook URL Audio Fixer calls on terminal state. "
            "Mandatory; --submit refuses to run with the default placeholder. "
            "Falls back to AUDIO_FIXER_WEBHOOK_URL or WEBHOOK_URL env."
        ),
    )
    p.add_argument(
        "--out-base",
        type=Path,
        default=DEFAULT_RUNS_BASE,
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
    parser.error("no mode selected")


if __name__ == "__main__":
    sys.exit(main())
