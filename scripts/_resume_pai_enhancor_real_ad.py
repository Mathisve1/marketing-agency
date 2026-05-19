"""ONE-OFF resumer: poll the already-submitted Pai UGC Seedance task
through to completion, download raw + thumbnail, then chain Audio Fixer.

Background: the original `scripts/run_pai_enhancor_real_ad.py` submitted
the Seedance task `6a085de160c112e9e9384415` successfully but failed on
the very first status poll with HTTP 400 `"request_id is required"`
(the provider was sending camelCase only; the live `/status` endpoint
wants snake_case). The task is still running on Enhancor's side;
credits are already committed. This script reuses the same provider
adapter (now patched) to resume against the existing task_id rather
than starting a second paid generation.

This is NOT a retry of the generation. It's a resume of the same
in-flight task. No second Seedance slot is burned.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

load_dotenv(_REPO_ROOT / ".env")

from agents.producer.providers.base import (  # noqa: E402
    ProviderError,
    ProviderStatus,
)
from agents.producer.providers.enhancor_audio_fixer import (  # noqa: E402
    EnhancorAudioFixerProvider,
)
from agents.producer.providers.enhancor_seedance import (  # noqa: E402
    EnhancorSeedanceProvider,
)

PROSPECT_ID = "pai-skincare"
SEEDANCE_TASK_ID = "6a085de160c112e9e9384415"

# Operator-personal inspection bucket. Set the real value in .env via
# PAI_ENHANCOR_WEBHOOK_URL — never commit the live UUID to git.
WEBHOOK_URL = os.environ.get(
    "PAI_ENHANCOR_WEBHOOK_URL",
    "https://webhook.site/your-bucket-uuid-here",
)

CLIPS_DIR = _REPO_ROOT / "prospects" / PROSPECT_ID / "production" / "clips"
RAW_OUTPUT_PATH = CLIPS_DIR / "route_01_enhancor_ugc_raw_15s.mp4"
THUMB_OUTPUT_PATH = CLIPS_DIR / "route_01_enhancor_ugc_thumb.webp"
FIXED_OUTPUT_PATH = CLIPS_DIR / "route_01_enhancor_ugc_audiofixed_15s.mp4"

RUNS_ROOT = _REPO_ROOT / "prospects" / PROSPECT_ID / "production" / "enhancor_real_ad_runs"

POLL_INTERVAL_SEC = 15
TIMEOUT_MIN = 25     # higher than the original 20: the task has already been running


def _serialise(obj):
    d = asdict(obj)
    for k, v in list(d.items()):
        if isinstance(v, dt.datetime):
            d[k] = v.isoformat()
        elif isinstance(v, ProviderStatus):
            d[k] = v.value
    return d


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    api_key = os.environ.get("ENHANCOR_API_KEY")
    if not api_key:
        print("ABORT: ENHANCOR_API_KEY not set", file=sys.stderr)
        return 2

    run_dir = RUNS_ROOT / (
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        + uuid.uuid4().hex[:6]
        + "-resume"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"resume run_dir: {run_dir}")
    print(f"resuming seedance task_id: {SEEDANCE_TASK_ID}")

    seedance = EnhancorSeedanceProvider(api_key)
    fixer = EnhancorAudioFixerProvider(api_key)

    correlation_id = f"{PROSPECT_ID}-route_01-resume"

    # ---- Seedance: poll until terminal ---------------------------------- #
    print(f"[seedance] polling every {POLL_INTERVAL_SEC}s, timeout {TIMEOUT_MIN} min...")
    try:
        seedance_result = seedance.wait_for_completion(
            SEEDANCE_TASK_ID,
            correlation_id=correlation_id,
            poll_interval_sec=POLL_INTERVAL_SEC,
            timeout_sec=TIMEOUT_MIN * 60,
        )
    except ProviderError as e:
        print(f"FATAL: Seedance poll: {e}", file=sys.stderr)
        if getattr(e, "raw_response", None):
            _write_json(run_dir / "seedance_poll_error.json", e.raw_response)
        return 22
    _write_json(run_dir / "seedance_terminal_status.json", _serialise(seedance_result))
    print(f"  result_url   : {seedance_result.result_url}")
    print(f"  thumbnail    : {seedance_result.thumbnail_url}")
    print(f"  cost         : {seedance_result.cost}")

    # ---- Download raw + thumbnail --------------------------------------- #
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_OUTPUT_PATH.is_file():
        print(f"  raw already exists: {RAW_OUTPUT_PATH} -> skipping download")
    else:
        try:
            seedance.download_result(seedance_result.result_url, RAW_OUTPUT_PATH)
        except ProviderError as e:
            print(f"FATAL: download raw: {e}", file=sys.stderr)
            return 23
    print(f"  raw mp4      : {RAW_OUTPUT_PATH}  ({RAW_OUTPUT_PATH.stat().st_size:,} B)")

    if seedance_result.thumbnail_url and not THUMB_OUTPUT_PATH.is_file():
        try:
            seedance.download_result(seedance_result.thumbnail_url, THUMB_OUTPUT_PATH)
        except ProviderError as e:
            print(f"WARN: thumb download: {e}", file=sys.stderr)
    if THUMB_OUTPUT_PATH.is_file():
        print(f"  thumbnail    : {THUMB_OUTPUT_PATH}  ({THUMB_OUTPUT_PATH.stat().st_size:,} B)")

    # ---- Audio Fixer: submit + poll ------------------------------------- #
    print("[audio-fixer] submitting on Seedance result URL...")
    try:
        fixer_resp = fixer.submit_audio_fix(
            input_video_url=seedance_result.result_url,
            webhook_url=WEBHOOK_URL,
            correlation_id=correlation_id,
        )
    except ProviderError as e:
        print(f"FATAL: Audio Fixer submit: {e}", file=sys.stderr)
        if getattr(e, "raw_response", None):
            _write_json(run_dir / "audio_fixer_submit_error.json", e.raw_response)
        return 30
    _write_json(run_dir / "audio_fixer_submit_response.json", _serialise(fixer_resp))
    print(f"  task_id      : {fixer_resp.provider_job_id}")

    print(f"[audio-fixer] polling every {POLL_INTERVAL_SEC}s, timeout {TIMEOUT_MIN} min...")
    try:
        fixer_result = fixer.wait_for_completion(
            fixer_resp.provider_job_id,
            correlation_id=correlation_id,
            poll_interval_sec=POLL_INTERVAL_SEC,
            timeout_sec=TIMEOUT_MIN * 60,
        )
    except ProviderError as e:
        print(f"FATAL: Audio Fixer poll: {e}", file=sys.stderr)
        if getattr(e, "raw_response", None):
            _write_json(run_dir / "audio_fixer_poll_error.json", e.raw_response)
        return 31
    _write_json(run_dir / "audio_fixer_terminal_status.json", _serialise(fixer_result))
    print(f"  result_url   : {fixer_result.result_url}")
    print(f"  cost         : {fixer_result.cost}")

    # ---- Download fixed mp4 --------------------------------------------- #
    if FIXED_OUTPUT_PATH.is_file():
        print(f"  fixed already exists: {FIXED_OUTPUT_PATH} -> skipping download")
    else:
        try:
            fixer.download_result(fixer_result.result_url, FIXED_OUTPUT_PATH)
        except ProviderError as e:
            print(f"FATAL: download fixed: {e}", file=sys.stderr)
            return 32
    print(f"  fixed mp4    : {FIXED_OUTPUT_PATH}  ({FIXED_OUTPUT_PATH.stat().st_size:,} B)")

    # ---- Manifest ------------------------------------------------------- #
    manifest = {
        "run_kind": "resume",
        "resumed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "seedance": {
            "task_id": SEEDANCE_TASK_ID,
            "result_url": seedance_result.result_url,
            "thumbnail_url": seedance_result.thumbnail_url,
            "cost": seedance_result.cost,
            "raw_output_path": str(RAW_OUTPUT_PATH),
            "raw_output_bytes": RAW_OUTPUT_PATH.stat().st_size,
            "thumbnail_path": str(THUMB_OUTPUT_PATH) if THUMB_OUTPUT_PATH.is_file() else None,
        },
        "audio_fixer": {
            "task_id": fixer_resp.provider_job_id,
            "result_url": fixer_result.result_url,
            "cost": fixer_result.cost,
            "fixed_output_path": str(FIXED_OUTPUT_PATH),
            "fixed_output_bytes": FIXED_OUTPUT_PATH.stat().st_size,
        },
    }
    _write_json(run_dir / "run_manifest.json", manifest)
    print("=" * 70)
    print("DONE (resume)")
    print(f"  manifest: {run_dir / 'run_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
