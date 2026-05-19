"""Phase-0 smoke test for Enhancor Seedance 2.0 Full Access + UGC Audio Fixer.

Purpose
-------
Submit ONE tiny generation against the real Enhancor API, save the raw
request/response JSON, poll status until terminal state or timeout, and
optionally chain the result into the UGC Audio Fixer. The script is the
authoritative proof of life for both APIs before any provider adapter
goes into the agent layer.

Hard rules
----------
* Loads ``ENHANCOR_API_KEY`` from ``.env`` (never an argv).
* Refuses to print the API key or any header value derived from it.
* Defaults to the cheapest mode (text-to-video, 480p, 4 s, ``fast_mode=true``).
* Submits exactly one generation per run; never auto-retries.
* Saves every request / response / webhook JSON under
  ``tmp/enhancor_smoke/`` so the operator can audit the wire bytes.
* Polls ``/status`` as the fallback when a public ``webhook_url`` is not
  reachable from the operator's machine.
* Audio Fixer chain is opt-in via ``--run-audio-fixer``.
* ``--dry-run`` prints the payload without calling the API at all.

Docs that govern this script:
  docs/enhancor_dashboard_raw.md
  docs/enhancor_api_spec.md
  docs/enhancor_webhook_contract.md

Usage
-----
.. code-block:: bash

    # Dry-run (no API call, validates the payload):
    py -3.11 scripts/enhancor_smoke_test.py \
        --mode text-to-video \
        --webhook-url https://example.com/webhooks/enhancor/seedance \
        --dry-run

    # Real cheapest text-to-video smoke (uses one Enhancor credit unit):
    py -3.11 scripts/enhancor_smoke_test.py \
        --mode text-to-video \
        --webhook-url https://hooks.example.com/api/webhooks/enhancor/seedance

    # UGC smoke (requires public product + influencer URLs):
    py -3.11 scripts/enhancor_smoke_test.py \
        --mode ugc \
        --product-url https://example.com/product.jpg \
        --influencer-url https://example.com/influencer.jpg \
        --webhook-url https://hooks.example.com/api/webhooks/enhancor/seedance

    # Multi-reference smoke (public image + public motion-reference video):
    py -3.11 scripts/enhancor_smoke_test.py \
        --mode multi-reference \
        --image-url https://example.com/product.jpg \
        --video-url https://example.com/motion-reference-9s.mp4 \
        --webhook-url https://hooks.example.com/api/webhooks/enhancor/seedance

    # Add an Audio Fixer pass after generation completes:
    py -3.11 scripts/enhancor_smoke_test.py \
        --mode text-to-video \
        --webhook-url https://hooks.example.com/api/webhooks/enhancor/seedance \
        --run-audio-fixer
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Project-root sys.path shim (script invocation, mirrors other CLIs) ──
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

log = logging.getLogger("enhancor_smoke")


# --------------------------------------------------------------------------- #
# Constants pinned to the dashboard-confirmed contract
# --------------------------------------------------------------------------- #

SEEDANCE_BASE_URL = "https://apireq.enhancor.ai/api/enhancor-ugc-full-access/v1"
AUDIO_FIXER_BASE_URL = "https://apireq.enhancor.ai/api/fix-audio/v1"

QUEUE_PATH = "/queue"
STATUS_PATH = "/status"

API_KEY_HEADER = "x-api-key"

# Dashboard-confirmed allowed values
ALLOWED_RESOLUTIONS = {"480p", "720p", "1080p"}
ALLOWED_ASPECT_RATIOS = {"16:9", "9:16", "4:3", "3:4", "1:1", "21:9"}
ALLOWED_DURATIONS_SECONDS = list(range(4, 16))  # 4..15 inclusive
ALLOWED_IMAGE_TO_VIDEO_MODES = {
    "ugc",
    "multi_reference",
    "extend",
    "multi_frame",
    "lipsyncing",
    "voice_clone",
    "first_n_last_frames",
}

# Smoke-script defaults (cheapest path)
DEFAULT_DURATION_SEC = "4"
DEFAULT_RESOLUTION = "480p"
DEFAULT_ASPECT_RATIO = "9:16"
DEFAULT_FAST_MODE = True
DEFAULT_POLL_INTERVAL_SEC = 10
DEFAULT_TIMEOUT_MINUTES = 10

# Terminal-status classifier (the exact status enum for the Seedance video
# endpoint is UNKNOWN/NEEDS TEST; we treat any string containing 'complet'
# as success and 'fail' or 'error' as failure).
TERMINAL_SUCCESS_TOKENS = ("complet",)  # matches COMPLETED, completed, complete
TERMINAL_FAILURE_TOKENS = ("fail", "error")

# Local artifact root
ARTIFACTS_ROOT = _REPO_ROOT / "tmp" / "enhancor_smoke"


# --------------------------------------------------------------------------- #
# Payload builders (PURE; tested in tests/test_enhancor_smoke_payloads.py)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SmokeOptions:
    """All operator-facing knobs the payload builders consume.

    Frozen so the builders cannot mutate it - keeps tests deterministic and
    makes the audit log unambiguous.
    """

    mode: str  # "text-to-video" | "ugc" | "multi-reference"
    webhook_url: str
    prompt: Optional[str] = None
    product_url: Optional[str] = None
    influencer_url: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    duration_sec: str = DEFAULT_DURATION_SEC
    resolution: str = DEFAULT_RESOLUTION
    aspect_ratio: str = DEFAULT_ASPECT_RATIO
    fast_mode: bool = DEFAULT_FAST_MODE


def _default_prompt_for_mode(mode: str) -> str:
    """The smoke prompts kept deliberately bland - we are validating wire
    contract, not creative quality."""
    if mode == "text-to-video":
        return (
            "A simple product bottle on a neutral background, soft daylight, "
            "short cinematic test."
        )
    if mode == "ugc":
        return (
            "A real woman in soft daylight holding the product near her shoulder, "
            "talking calmly to camera, premium UGC register, short test."
        )
    if mode == "multi-reference":
        return (
            "Calm UGC pacing applied to the provided product image, soft daylight, "
            "9:16 vertical, short test."
        )
    raise ValueError(f"unknown mode: {mode!r}")


def _validate_common(opts: SmokeOptions) -> None:
    """Cheap field-shape validation that fails BEFORE any API call.

    The dashboard rules captured in ``docs/enhancor_dashboard_raw.md`` are
    the source of truth; this function only enforces what is unambiguous.
    """
    if not opts.webhook_url:
        raise ValueError("webhook_url is mandatory on every Enhancor submission")
    if not opts.webhook_url.lower().startswith("https://"):
        # Enhancor docs do not explicitly require https, but the conservative
        # default in webhook_contract.md is https-only.
        raise ValueError(
            "webhook_url must be an https:// URL (Enhancor delivers to a public "
            "endpoint; see docs/enhancor_webhook_contract.md)"
        )
    if opts.duration_sec not in {str(n) for n in ALLOWED_DURATIONS_SECONDS}:
        raise ValueError(
            f"duration must be one of {ALLOWED_DURATIONS_SECONDS!r}; got {opts.duration_sec!r}"
        )
    if opts.resolution not in ALLOWED_RESOLUTIONS:
        raise ValueError(
            f"resolution must be one of {sorted(ALLOWED_RESOLUTIONS)!r}; "
            f"got {opts.resolution!r}"
        )
    if opts.aspect_ratio not in ALLOWED_ASPECT_RATIOS:
        raise ValueError(
            f"aspect_ratio must be one of {sorted(ALLOWED_ASPECT_RATIOS)!r}; "
            f"got {opts.aspect_ratio!r}"
        )
    if opts.resolution == "1080p" and opts.fast_mode:
        raise ValueError(
            "resolution=1080p requires fast_mode=false "
            "(dashboard rule preserved in docs/enhancor_dashboard_raw.md § 5)"
        )


def build_text_to_video_payload(opts: SmokeOptions) -> dict[str, Any]:
    """Cheapest probe payload. MUST NOT include images / videos / audios.

    Matches the dashboard rule preserved in
    ``docs/enhancor_dashboard_raw.md`` § 5.
    """
    _validate_common(opts)
    if opts.mode != "text-to-video":
        raise ValueError(
            f"build_text_to_video_payload called with mode={opts.mode!r}"
        )
    return {
        "type": "text-to-video",
        "prompt": opts.prompt or _default_prompt_for_mode("text-to-video"),
        "webhook_url": opts.webhook_url,
        "duration": opts.duration_sec,
        "resolution": opts.resolution,
        "aspect_ratio": opts.aspect_ratio,
        "fast_mode": opts.fast_mode,
    }


def build_ugc_payload(opts: SmokeOptions) -> dict[str, Any]:
    """``image-to-video / ugc`` payload with at least one product + one influencer.

    Dashboard rule: ``len(products) + len(influencers) + len(images) <= 9``
    in UGC mode; we send 1 of each so the smoke is unambiguous.

    Dashboard rule: ``full_access=true`` whenever a human face will appear
    (UGC implies a human face).
    """
    _validate_common(opts)
    if opts.mode != "ugc":
        raise ValueError(f"build_ugc_payload called with mode={opts.mode!r}")
    if not opts.product_url:
        raise ValueError("--product-url is required for mode=ugc")
    if not opts.influencer_url:
        raise ValueError("--influencer-url is required for mode=ugc")
    return {
        "type": "image-to-video",
        "mode": "ugc",
        "prompt": opts.prompt or _default_prompt_for_mode("ugc"),
        "webhook_url": opts.webhook_url,
        "duration": opts.duration_sec,
        "resolution": opts.resolution,
        "aspect_ratio": opts.aspect_ratio,
        "fast_mode": opts.fast_mode,
        "full_access": True,
        "products": [opts.product_url],
        "influencers": [opts.influencer_url],
    }


def build_multi_reference_payload(opts: SmokeOptions) -> dict[str, Any]:
    """``image-to-video / multi_reference`` payload.

    Sends only the public URLs the operator actually supplied; never
    fabricates a placeholder URL.
    """
    _validate_common(opts)
    if opts.mode != "multi-reference":
        raise ValueError(
            f"build_multi_reference_payload called with mode={opts.mode!r}"
        )
    if not (opts.image_url or opts.video_url or opts.audio_url):
        raise ValueError(
            "mode=multi-reference requires at least one of "
            "--image-url / --video-url / --audio-url"
        )
    payload: dict[str, Any] = {
        "type": "image-to-video",
        "mode": "multi_reference",
        "prompt": opts.prompt or _default_prompt_for_mode("multi-reference"),
        "webhook_url": opts.webhook_url,
        "duration": opts.duration_sec,
        "resolution": opts.resolution,
        "aspect_ratio": opts.aspect_ratio,
        "fast_mode": opts.fast_mode,
    }
    if opts.image_url:
        payload["images"] = [opts.image_url]
    if opts.video_url:
        payload["videos"] = [opts.video_url]
    if opts.audio_url:
        payload["audios"] = [opts.audio_url]
    return payload


def build_audio_fixer_payload(*, input_video_url: str, webhook_url: str) -> dict[str, Any]:
    """UGC Audio Fixer queue payload (the only two required fields).

    Source of truth: ``docs/enhancor_api_spec.md § B``.
    """
    if not input_video_url:
        raise ValueError("input_video_url is required")
    if not webhook_url:
        raise ValueError("webhook_url is required on every Enhancor submission")
    if not webhook_url.lower().startswith("https://"):
        raise ValueError("webhook_url must be an https:// URL")
    return {
        "inputVideo": input_video_url,
        "webhook_url": webhook_url,
    }


def build_payload(opts: SmokeOptions) -> dict[str, Any]:
    """Dispatch on ``opts.mode``."""
    if opts.mode == "text-to-video":
        return build_text_to_video_payload(opts)
    if opts.mode == "ugc":
        return build_ugc_payload(opts)
    if opts.mode == "multi-reference":
        return build_multi_reference_payload(opts)
    raise ValueError(f"unknown mode: {opts.mode!r}")


# --------------------------------------------------------------------------- #
# Status classification (also pure / tested)
# --------------------------------------------------------------------------- #


def classify_status(status: Optional[str]) -> str:
    """Map a free-form provider status string into one of three states.

    Returns: ``"success"`` | ``"failure"`` | ``"in_flight"``.

    Why string-matching and not an enum: the exact ``status`` enum surfaced
    by the Seedance video endpoint is UNKNOWN / NEEDS TEST per
    ``docs/enhancor_api_spec.md § A``. The conservative classifier accepts
    every reasonable spelling rather than crash on an unexpected one.
    """
    if not status:
        return "in_flight"
    norm = status.strip().lower()
    for token in TERMINAL_SUCCESS_TOKENS:
        if token in norm:
            return "success"
    for token in TERMINAL_FAILURE_TOKENS:
        if token in norm:
            return "failure"
    return "in_flight"


# --------------------------------------------------------------------------- #
# Run-id + artifact paths
# --------------------------------------------------------------------------- #


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]


def _ensure_run_dir(run_id: str) -> Path:
    p = ARTIFACTS_ROOT / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Live HTTP client (only used when not --dry-run)
# --------------------------------------------------------------------------- #


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip the API key from any header dump."""
    out = {}
    for k, v in headers.items():
        if k.lower() == API_KEY_HEADER.lower():
            out[k] = "***redacted***"
        else:
            out[k] = v
    return out


def _post_json(
    *,
    url: str,
    body: dict[str, Any],
    api_key: str,
    timeout_sec: int = 60,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Single ``POST`` with JSON body. Returns (status_code, parsed_json, headers).

    Imports ``requests`` lazily so the dry-run / test paths can import this
    module without needing the dependency available.
    """
    import requests  # noqa: PLC0415 - lazy import (see docstring)

    headers = {
        "Content-Type": "application/json",
        API_KEY_HEADER: api_key,
        "Accept": "application/json",
    }
    resp = requests.post(url, json=body, headers=headers, timeout=timeout_sec)
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"_raw_text": resp.text[:4096]}
    return resp.status_code, data, dict(resp.headers)


def submit_and_poll(
    *,
    queue_url: str,
    status_url: str,
    payload: dict[str, Any],
    api_key: str,
    run_dir: Path,
    poll_interval_sec: int,
    timeout_sec: int,
    label: str,
) -> dict[str, Any]:
    """Submit a job, persist every wire round-trip, poll until terminal.

    Returns the final ``/status`` body. Raises ``RuntimeError`` on a
    terminal-failure status or wall-clock timeout.
    """
    # ── Submit ───────────────────────────────────────────────────────
    print(f"\n[{label}] POST {queue_url}")
    print("[smoke] request body:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    status_code, body, resp_headers = _post_json(
        url=queue_url, body=payload, api_key=api_key
    )
    submit_artifact = {
        "at": datetime.now(timezone.utc).isoformat(),
        "url": queue_url,
        "request_headers": _redact_headers({API_KEY_HEADER: "***redacted***", "Content-Type": "application/json"}),
        "request_body": payload,
        "response_status_code": status_code,
        "response_headers": _redact_headers(resp_headers),
        "response_body": body,
    }
    _write_json(run_dir / f"{label}_submit.json", submit_artifact)

    print(f"[{label}] response status: {status_code}")
    print(json.dumps(body, indent=2, ensure_ascii=False, default=str))

    if status_code >= 400:
        raise RuntimeError(
            f"{label}: queue submission failed with status {status_code} - "
            f"see {run_dir / f'{label}_submit.json'}"
        )
    if not body.get("success"):
        raise RuntimeError(
            f"{label}: queue submission did not return success=true - "
            f"see {run_dir / f'{label}_submit.json'}"
        )
    request_id = body.get("requestId") or body.get("request_id")
    if not request_id:
        raise RuntimeError(
            f"{label}: queue response did not include requestId - "
            f"see {run_dir / f'{label}_submit.json'}"
        )
    print(f"[{label}] requestId: {request_id}")

    # ── Poll status ─────────────────────────────────────────────────
    deadline = time.time() + timeout_sec
    poll_n = 0
    while True:
        poll_n += 1
        if time.time() >= deadline:
            raise RuntimeError(
                f"{label}: timed out after {timeout_sec}s waiting for terminal status "
                f"(last requestId={request_id})"
            )
        time.sleep(poll_interval_sec)
        status_code, status_body, status_headers = _post_json(
            url=status_url,
            body={"request_id": request_id, "requestId": request_id},
            api_key=api_key,
        )
        _write_json(
            run_dir / f"{label}_status_{poll_n:03d}.json",
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "url": status_url,
                "request_body": {"request_id": request_id, "requestId": request_id},
                "response_status_code": status_code,
                "response_headers": _redact_headers(status_headers),
                "response_body": status_body,
            },
        )
        status_str = (status_body or {}).get("status")
        verdict = classify_status(status_str)
        print(f"[{label}] poll #{poll_n}: http={status_code} status={status_str!r} verdict={verdict}")
        if verdict == "success":
            return status_body
        if verdict == "failure":
            raise RuntimeError(
                f"{label}: terminal-failure status={status_str!r} - "
                f"see {run_dir / f'{label}_status_{poll_n:03d}.json'}"
            )


def download_to(path: Path, url: str, *, timeout_sec: int = 120) -> int:
    """Stream-download ``url`` to ``path``; returns bytes written."""
    import requests  # noqa: PLC0415 - lazy import

    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout_sec) as resp:
        resp.raise_for_status()
        total = 0
        with path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                total += len(chunk)
    return total


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #


def _load_dotenv_if_available() -> None:
    """Mirrors scripts/deploy_pitch_microsite.py."""
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    load_dotenv(_REPO_ROOT / ".env")


def _resolve_api_key() -> str:
    """Read ENHANCOR_API_KEY from env; raise with a non-leaky message if absent."""
    key = os.environ.get("ENHANCOR_API_KEY")
    if not key or not key.strip():
        raise SystemExit(
            "FATAL: ENHANCOR_API_KEY is not set. Add it to .env "
            "(see .env.example) or your shell, then re-run."
        )
    return key.strip()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="enhancor_smoke_test",
        description=(
            "Phase-0 smoke test for Enhancor Seedance 2.0 Full Access + "
            "UGC Audio Fixer. Submits one tiny generation, polls status, "
            "saves every wire payload under tmp/enhancor_smoke/."
        ),
    )
    p.add_argument(
        "--mode",
        choices=["text-to-video", "ugc", "multi-reference"],
        default="text-to-video",
        help="Which Seedance smoke payload to build (default: text-to-video).",
    )
    p.add_argument(
        "--webhook-url",
        default=os.environ.get("WEBHOOK_URL"),
        help=(
            "Public HTTPS webhook URL Enhancor will POST to on terminal state. "
            "Mandatory; falls back to env WEBHOOK_URL. Polling still runs "
            "regardless of whether this URL is actually reachable from "
            "Enhancor (smoke test relies on polling)."
        ),
    )
    p.add_argument("--prompt", default=None, help="Override the default smoke prompt.")
    p.add_argument("--product-url", default=None, help="Public product image URL (mode=ugc).")
    p.add_argument("--influencer-url", default=None, help="Public influencer image URL (mode=ugc).")
    p.add_argument("--image-url", default=None, help="Public image URL (mode=multi-reference).")
    p.add_argument("--video-url", default=None, help="Public video URL (mode=multi-reference).")
    p.add_argument("--audio-url", default=None, help="Public audio URL (mode=multi-reference).")
    p.add_argument(
        "--duration",
        default=DEFAULT_DURATION_SEC,
        help=f"Seconds, '4'..'15'. Default {DEFAULT_DURATION_SEC}.",
    )
    p.add_argument(
        "--resolution",
        default=DEFAULT_RESOLUTION,
        choices=sorted(ALLOWED_RESOLUTIONS),
        help=f"Output resolution. Default {DEFAULT_RESOLUTION}.",
    )
    p.add_argument(
        "--aspect-ratio",
        default=DEFAULT_ASPECT_RATIO,
        choices=sorted(ALLOWED_ASPECT_RATIOS),
        help=f"Output aspect ratio. Default {DEFAULT_ASPECT_RATIO}.",
    )
    p.add_argument(
        "--no-fast-mode",
        dest="fast_mode",
        action="store_false",
        default=DEFAULT_FAST_MODE,
        help="Disable fast_mode (required for resolution=1080p).",
    )
    p.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SEC,
        help=f"Seconds between /status polls. Default {DEFAULT_POLL_INTERVAL_SEC}.",
    )
    p.add_argument(
        "--timeout-minutes",
        type=int,
        default=DEFAULT_TIMEOUT_MINUTES,
        help=f"Wall-clock timeout in minutes. Default {DEFAULT_TIMEOUT_MINUTES}.",
    )
    p.add_argument(
        "--run-audio-fixer",
        action="store_true",
        help="After the Seedance generation completes, submit the result mp4 "
             "to the UGC Audio Fixer endpoint and download the fixed file.",
    )
    p.add_argument(
        "--audio-fixer-only",
        action="store_true",
        help=(
            "Skip Seedance entirely and submit ONLY the UGC Audio Fixer call. "
            "Requires --input-video-url. Use this to repair an existing raw "
            "video (e.g. a prior Seedance CloudFront result) without burning "
            "a second Seedance credit. Mutually exclusive with --run-audio-fixer "
            "and with --mode (because we are not generating anything new)."
        ),
    )
    p.add_argument(
        "--input-video-url",
        default=None,
        help=(
            "Public HTTPS URL of an existing video to send to the UGC Audio "
            "Fixer. Required when --audio-fixer-only is set."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + print the payload(s), validate required fields, do NOT "
             "call the Enhancor API and do NOT spend any credits.",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging.")
    return p


def _run_audio_fixer_only(
    *,
    input_video_url: str,
    webhook_url: str,
    poll_interval_sec: int,
    timeout_sec: int,
    dry_run: bool,
) -> int:
    """Audio-Fixer-only path: skip Seedance, submit one Audio Fixer call
    against the supplied input URL, download the fixed output.

    Used by ``--audio-fixer-only --input-video-url <url>``. Reuses every
    shared helper (payload builder, submit_and_poll, download_to,
    artifact-writing) so the wire-log shape is identical to the Seedance
    path.
    """
    try:
        payload = build_audio_fixer_payload(
            input_video_url=input_video_url,
            webhook_url=webhook_url,
        )
    except ValueError as e:
        print(f"FATAL: invalid Audio Fixer payload: {e}", file=sys.stderr)
        return 2

    run_id = _new_run_id()
    run_dir = _ensure_run_dir(run_id)
    print(f"[smoke] run_id: {run_id}")
    print(f"[smoke] artifacts: {run_dir}")
    print("[smoke] resolved Audio Fixer payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if dry_run:
        _write_json(run_dir / "dry_run_audio_fixer_payload.json", payload)
        print(
            f"\n[smoke] DRY-RUN: no API call made. Payload saved to "
            f"{run_dir / 'dry_run_audio_fixer_payload.json'}."
        )
        return 0

    api_key = _resolve_api_key()
    queue_url = AUDIO_FIXER_BASE_URL + QUEUE_PATH
    status_url = AUDIO_FIXER_BASE_URL + STATUS_PATH
    try:
        final = submit_and_poll(
            queue_url=queue_url,
            status_url=status_url,
            payload=payload,
            api_key=api_key,
            run_dir=run_dir,
            poll_interval_sec=poll_interval_sec,
            timeout_sec=timeout_sec,
            label="audio_fixer",
        )
    except RuntimeError as e:
        print(f"\n[smoke] Audio Fixer FAIL ({e})", file=sys.stderr)
        return 8

    cost = final.get("cost")
    result_url = final.get("result")
    print(f"\n[smoke] Audio Fixer OK · cost={cost} · result={result_url}")
    if not isinstance(result_url, str) or not result_url.startswith(("http://", "https://")):
        print(
            f"[smoke] WARN: Audio Fixer final status had no usable result URL: "
            f"{result_url!r}",
            file=sys.stderr,
        )
        return 9

    fixed_path = run_dir / "audio_fixed.mp4"
    try:
        n_bytes = download_to(fixed_path, result_url)
    except Exception as e:  # noqa: BLE001
        print(
            f"[smoke] WARN: failed to download Audio Fixer output: {e}",
            file=sys.stderr,
        )
        return 10
    print(f"[smoke] downloaded {n_bytes} B to {fixed_path}")

    stable_fixed = ARTIFACTS_ROOT / "audio_fixed.mp4"
    try:
        stable_fixed.write_bytes(fixed_path.read_bytes())
        print(f"[smoke] also wrote stable mirror at {stable_fixed}")
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] WARN: stable mirror write failed: {e}", file=sys.stderr)

    print("\n[smoke] Audio Fixer done.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:  # noqa: PLR0911 - explicit early returns clearer than nested ifs
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    _load_dotenv_if_available()

    if not args.webhook_url:
        print(
            "FATAL: --webhook-url is required (or set env WEBHOOK_URL). "
            "Enhancor requires webhook_url on every submission; see "
            "docs/enhancor_webhook_contract.md.",
            file=sys.stderr,
        )
        return 2

    # ── Audio-Fixer-only branch ─────────────────────────────────────
    # Short-circuit BEFORE the Seedance payload is built so we never
    # accidentally enqueue a fresh Seedance generation while the operator
    # just wants to repair an existing video.
    if args.audio_fixer_only:
        if args.run_audio_fixer:
            print(
                "FATAL: --audio-fixer-only and --run-audio-fixer are mutually "
                "exclusive (the first runs Audio Fixer only; the second runs "
                "Seedance THEN Audio Fixer).",
                file=sys.stderr,
            )
            return 2
        if not args.input_video_url:
            print(
                "FATAL: --audio-fixer-only requires --input-video-url "
                "(public HTTPS URL of the existing raw video).",
                file=sys.stderr,
            )
            return 2
        return _run_audio_fixer_only(
            input_video_url=args.input_video_url,
            webhook_url=args.webhook_url,
            poll_interval_sec=args.poll_interval,
            timeout_sec=max(args.timeout_minutes * 60, args.poll_interval * 2),
            dry_run=args.dry_run,
        )

    opts = SmokeOptions(
        mode=args.mode,
        webhook_url=args.webhook_url,
        prompt=args.prompt,
        product_url=args.product_url,
        influencer_url=args.influencer_url,
        image_url=args.image_url,
        video_url=args.video_url,
        audio_url=args.audio_url,
        duration_sec=str(args.duration),
        resolution=args.resolution,
        aspect_ratio=args.aspect_ratio,
        fast_mode=args.fast_mode,
    )

    try:
        payload = build_payload(opts)
    except ValueError as e:
        print(f"FATAL: invalid payload: {e}", file=sys.stderr)
        return 2

    run_id = _new_run_id()
    run_dir = _ensure_run_dir(run_id)
    print(f"[smoke] run_id: {run_id}")
    print(f"[smoke] artifacts: {run_dir}")
    print("[smoke] resolved payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.dry_run:
        _write_json(run_dir / "dry_run_payload.json", payload)
        print(f"\n[smoke] DRY-RUN: no API call made. Payload saved to {run_dir / 'dry_run_payload.json'}.")
        return 0

    api_key = _resolve_api_key()

    # ── Seedance ─────────────────────────────────────────────────────
    queue_url = SEEDANCE_BASE_URL + QUEUE_PATH
    status_url = SEEDANCE_BASE_URL + STATUS_PATH
    timeout_sec = max(args.timeout_minutes * 60, args.poll_interval * 2)
    try:
        final = submit_and_poll(
            queue_url=queue_url,
            status_url=status_url,
            payload=payload,
            api_key=api_key,
            run_dir=run_dir,
            poll_interval_sec=args.poll_interval,
            timeout_sec=timeout_sec,
            label="seedance",
        )
    except RuntimeError as e:
        print(f"\n[smoke] FAIL ({e})", file=sys.stderr)
        return 5

    cost = final.get("cost")
    result_url = final.get("result")
    print(f"\n[smoke] Seedance OK · cost={cost} · result={result_url}")
    if not isinstance(result_url, str) or not result_url.startswith(("http://", "https://")):
        print(
            f"[smoke] WARN: final status had no usable result URL: {result_url!r}",
            file=sys.stderr,
        )
        return 6

    raw_video_path = run_dir / "raw_generation.mp4"
    try:
        n_bytes = download_to(raw_video_path, result_url)
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] WARN: failed to download Seedance output: {e}", file=sys.stderr)
        return 7
    print(f"[smoke] downloaded {n_bytes} B to {raw_video_path}")

    # Also mirror to a stable filename outside run-dir for the docs commands.
    stable_path = ARTIFACTS_ROOT / "raw_generation.mp4"
    try:
        stable_path.write_bytes(raw_video_path.read_bytes())
        print(f"[smoke] also wrote stable mirror at {stable_path}")
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] WARN: stable mirror write failed: {e}", file=sys.stderr)

    if not args.run_audio_fixer:
        print("\n[smoke] done. Skipping Audio Fixer (pass --run-audio-fixer to enable).")
        return 0

    # ── UGC Audio Fixer ──────────────────────────────────────────────
    fixer_payload = build_audio_fixer_payload(
        input_video_url=result_url,
        webhook_url=args.webhook_url,
    )
    fixer_queue = AUDIO_FIXER_BASE_URL + QUEUE_PATH
    fixer_status = AUDIO_FIXER_BASE_URL + STATUS_PATH
    try:
        fixer_final = submit_and_poll(
            queue_url=fixer_queue,
            status_url=fixer_status,
            payload=fixer_payload,
            api_key=api_key,
            run_dir=run_dir,
            poll_interval_sec=args.poll_interval,
            timeout_sec=timeout_sec,
            label="audio_fixer",
        )
    except RuntimeError as e:
        print(f"\n[smoke] Audio Fixer FAIL ({e})", file=sys.stderr)
        return 8

    fx_cost = fixer_final.get("cost")
    fx_result_url = fixer_final.get("result")
    print(f"\n[smoke] Audio Fixer OK · cost={fx_cost} · result={fx_result_url}")
    if not isinstance(fx_result_url, str) or not fx_result_url.startswith(("http://", "https://")):
        print(
            f"[smoke] WARN: Audio Fixer status had no usable result URL: {fx_result_url!r}",
            file=sys.stderr,
        )
        return 9

    fixed_path = run_dir / "audio_fixed.mp4"
    try:
        n_bytes = download_to(fixed_path, fx_result_url)
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] WARN: failed to download Audio Fixer output: {e}", file=sys.stderr)
        return 10
    print(f"[smoke] downloaded {n_bytes} B to {fixed_path}")
    stable_fixed = ARTIFACTS_ROOT / "audio_fixed.mp4"
    try:
        stable_fixed.write_bytes(fixed_path.read_bytes())
        print(f"[smoke] also wrote stable mirror at {stable_fixed}")
    except Exception as e:  # noqa: BLE001
        print(f"[smoke] WARN: stable mirror write failed: {e}", file=sys.stderr)

    print("\n[smoke] all done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
