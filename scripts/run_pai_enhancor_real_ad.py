"""Run ONE real-ad-quality Pai UGC generation through the Enhancor stack.

Pipeline:

    Seedance UGC (image-to-video)  ->  Audio Fixer (cleanup pass)

This is a deliberate paid-quality run, NOT a smoke test. The Phase-0
smoke confirmations (UGC mode native audio, Audio Fixer accepts the
Seedance result URL directly, both providers return a `cost` integer)
are the baseline; this script exercises them at real ad settings
(1080p, 15 s, fast_mode=false).

Usage:
    py -3.11 scripts/run_pai_enhancor_real_ad.py --dry-run
    py -3.11 scripts/run_pai_enhancor_real_ad.py

Rules enforced before any HTTP call:
  - ENHANCOR_API_KEY in env
  - product / influencer URLs are HTTPS and reachable (HTTP 200)
  - duration / resolution / aspect_ratio / fast_mode obey the dashboard
    rules (1080p requires fast_mode=false)
  - prompt is non-empty
  - negative prompt is <= 500 chars (provider limit guard)
  - none of the four output paths already exist (refuse to overwrite)

Runs at most:
  1 Seedance submission + N status polls + 1 download + 1 thumbnail download
  1 Audio Fixer submission + N status polls + 1 download

If either provider fails, the script exits with a non-zero code and
prints the smallest useful diagnostic; it does NOT retry.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

load_dotenv(_REPO_ROOT / ".env")

from agents.producer.providers.base import (  # noqa: E402
    ProviderError,
    ProviderJobRequest,
    ProviderStatus,
)
from agents.producer.providers.enhancor_audio_fixer import (  # noqa: E402
    EnhancorAudioFixerProvider,
    build_audio_fixer_payload,
)
from agents.producer.providers.enhancor_seedance import (  # noqa: E402
    EnhancorSeedanceProvider,
    build_ugc_payload,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("pai_enhancor_real_ad")


# --------------------------------------------------------------------------- #
# Production inputs
# --------------------------------------------------------------------------- #

PROSPECT_ID = "pai-skincare"
ROUTE_ID = "route_01"

PRODUCT_URL = (
    "https://yuvo-pitches.pages.dev/p/pai-skincare-p9wybu/refs/"
    "renewal-serum-primary-packshot-9x16.jpg"
)
INFLUENCER_URL = (
    "https://yuvo-pitches.pages.dev/p/pai-skincare-p9wybu/refs/"
    "test-influencer-synthetic.jpg"
)

# Reuse the existing smoke webhook (operator-controlled inbox; harmless
# if not picked up — the Enhancor callbacks land there and the status
# poll is the source of truth in this script).
# Operator-personal inspection bucket. Set the real value in .env via
# PAI_ENHANCOR_WEBHOOK_URL — never commit the live UUID to git.
WEBHOOK_URL = os.environ.get(
    "PAI_ENHANCOR_WEBHOOK_URL",
    "https://webhook.site/your-bucket-uuid-here",
)

# Real-ad settings (not the smoke defaults).
DURATION_SEC = "15"
RESOLUTION = "1080p"
ASPECT_RATIO = "9:16"
FAST_MODE = False        # 1080p requires fast_mode=false per dashboard rule
FULL_ACCESS = True

# Paid-quality prompt + negative prompt.
PROMPT_TEXT = """Create a realistic 15-second vertical UGC skincare ad for a premium sensitive-skin serum brand.

Use the product reference image as the exact product inspiration: a minimalist white-and-amber serum bottle with a clean premium skincare feel. Preserve the product as a premium skincare bottle. The label should remain simple and believable; do not invent long fake readable text.

Use the influencer reference image as the creator identity reference. Keep her face consistent, natural, adult, realistic, and premium. She should feel like a real customer filming a polished UGC ad at home, not a model and not an overexcited influencer.

Scene and pacing:
0-2s: The creator is framed in a warm modern bathroom or bedroom with soft natural daylight. She looks into camera and holds the serum bottle naturally at chest height. Calm, friendly, premium energy.
2-5s: She speaks naturally while bringing the product slightly closer to camera. Subtle hand movement. The product remains visible and believable.
5-8s: Cutaway or close-up moment of the serum/product handling: bottle, pump, texture, or application on the back of the hand. Real skin texture, natural hands, no perfect airbrushed look.
8-12s: Back to creator. She gives a calm trust-building line and a small natural smile. Keep her expression understated and believable.
12-15s: Clean product-focused ending with the bottle on a neutral surface or held still near camera. Leave visual space for a later CTA overlay.

Dialogue:
\"I like that this feels simple. One serum, a calm routine, and ingredients I can actually understand.\"

Audio:
Generate natural synced speech, soft room tone, and subtle product handling sounds. Voice should sound real, calm, premium, human, and not robotic. No loud music. No exaggerated influencer voice.

Style:
Premium skincare UGC. Soft daylight. Realistic phone-shot feeling but polished. Calm British or European beauty-ad tone. Gentle handheld camera. Natural blinking, natural mouth movement, natural hands. Understated, trustworthy, not TikTok hype.

Compliance:
No medical claims. No before-and-after. No claims about curing, treating, healing, fixing eczema, rosacea, acne, or sensitive skin. No dermatologist office. No white coat. No clinical promise. No aggressive results claim."""

NEGATIVE_PROMPT = (
    "competitor branding, Aurelia, copied competitor ad, medical claims, "
    "before and after, eczema cure, rosacea cure, acne cure, dermatologist "
    "office, white coat, syringe, prescription, fake readable label text, "
    "warped bottle label, distorted product, extra fingers, deformed hands, "
    "plastic skin, uncanny face, robotic mouth movement, bad lip sync, loud "
    "music, overexcited influencer, cartoon, CGI, blurry product, low quality"
)
NEGATIVE_PROMPT_MAX_CHARS = 500


# --------------------------------------------------------------------------- #
# Output paths
# --------------------------------------------------------------------------- #

CLIPS_DIR = _REPO_ROOT / "prospects" / PROSPECT_ID / "production" / "clips"
RAW_OUTPUT_PATH = CLIPS_DIR / "route_01_enhancor_ugc_raw_15s.mp4"
THUMB_OUTPUT_PATH = CLIPS_DIR / "route_01_enhancor_ugc_thumb.webp"
FIXED_OUTPUT_PATH = CLIPS_DIR / "route_01_enhancor_ugc_audiofixed_15s.mp4"

RUNS_ROOT = _REPO_ROOT / "prospects" / PROSPECT_ID / "production" / "enhancor_real_ad_runs"

# Browser-style UA for the pre-submit HEAD probes. Cloudflare Pages
# rejects Python's default urllib UA with 403; Enhancor's fetcher uses
# a normal HTTP client UA.
_PROBE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _new_run_id() -> str:
    return (
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        + uuid.uuid4().hex[:6]
    )


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


def _serialise_dataclass(obj: Any) -> dict[str, Any]:
    """Turn a frozen dataclass into a JSON-friendly dict (datetimes ->
    iso strings, enums -> values)."""
    d = asdict(obj)
    # Walk one level; the dataclasses we serialise here are flat.
    for k, v in list(d.items()):
        if isinstance(v, dt.datetime):
            d[k] = v.isoformat()
        elif isinstance(v, ProviderStatus):
            d[k] = v.value
    return d


def _probe_url(url: str) -> dict[str, Any]:
    """HEAD the URL and return a structured probe result. Raises
    `RuntimeError` on non-2xx."""
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": _PROBE_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {
                "url": url,
                "http_status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "content_length": resp.headers.get("Content-Length", ""),
                "verified_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} HEAD returned HTTP {e.code} {e.reason}") from e


def _validate_inputs() -> None:
    """Hard gates BEFORE any paid call."""
    if not os.environ.get("ENHANCOR_API_KEY"):
        raise RuntimeError("ENHANCOR_API_KEY not set in env / .env")
    if len(NEGATIVE_PROMPT) > NEGATIVE_PROMPT_MAX_CHARS:
        raise RuntimeError(
            f"NEGATIVE_PROMPT is {len(NEGATIVE_PROMPT)} chars > "
            f"{NEGATIVE_PROMPT_MAX_CHARS} (provider limit)"
        )
    if not PROMPT_TEXT.strip():
        raise RuntimeError("PROMPT_TEXT is empty")
    for p in (RAW_OUTPUT_PATH, THUMB_OUTPUT_PATH, FIXED_OUTPUT_PATH):
        if p.is_file():
            raise RuntimeError(
                f"refusing to overwrite existing output: {p}",
            )


def _print_summary(*, mode: str, run_dir: Path) -> None:
    print("=" * 78)
    print(f"Pai Skincare · Route 01 · Enhancor real-ad pipeline  ({mode})")
    print("=" * 78)
    print(f"  run_dir              : {run_dir}")
    print(f"  product_url          : {PRODUCT_URL}")
    print(f"  influencer_url       : {INFLUENCER_URL}")
    print(f"  webhook_url          : {WEBHOOK_URL}")
    print(f"  duration             : {DURATION_SEC} s")
    print(f"  resolution           : {RESOLUTION}")
    print(f"  aspect_ratio         : {ASPECT_RATIO}")
    print(f"  fast_mode            : {FAST_MODE}")
    print(f"  full_access          : {FULL_ACCESS}")
    print(f"  prompt chars         : {len(PROMPT_TEXT)}")
    print(f"  negative prompt chars: {len(NEGATIVE_PROMPT)}  (cap {NEGATIVE_PROMPT_MAX_CHARS})")
    print(f"  raw output           : {RAW_OUTPUT_PATH}")
    print(f"  thumbnail            : {THUMB_OUTPUT_PATH}")
    print(f"  audio-fixed output   : {FIXED_OUTPUT_PATH}")
    print()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_pai_enhancor_real_ad",
        description=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + validate the payload but make no HTTP calls.",
    )
    parser.add_argument(
        "--poll-interval-sec", type=int, default=15,
        help="Seconds between status polls (default 15).",
    )
    parser.add_argument(
        "--timeout-min", type=int, default=20,
        help="Max wait per provider in minutes (default 20).",
    )
    args = parser.parse_args(argv)

    run_id = _new_run_id()
    run_dir = _ensure_dir(RUNS_ROOT / run_id)
    _print_summary(mode="DRY-RUN" if args.dry_run else "LIVE", run_dir=run_dir)

    # ---- pre-flight gates ---------------------------------------------- #
    try:
        _validate_inputs()
    except RuntimeError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2

    print("[preflight] HEAD-probing product + influencer URLs...")
    try:
        product_probe = _probe_url(PRODUCT_URL)
        influencer_probe = _probe_url(INFLUENCER_URL)
    except RuntimeError as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 3
    print(f"  product   : HTTP {product_probe['http_status']}  "
          f"{product_probe['content_type']}")
    print(f"  influencer: HTTP {influencer_probe['http_status']}  "
          f"{influencer_probe['content_type']}")
    _write_json(run_dir / "00_preflight_url_probes.json", {
        "product": product_probe,
        "influencer": influencer_probe,
    })

    # ---- build the Seedance UGC payload via the provider builder ------- #
    try:
        ugc_payload = build_ugc_payload(
            prompt=PROMPT_TEXT,
            webhook_url=WEBHOOK_URL,
            products=[PRODUCT_URL],
            influencers=[INFLUENCER_URL],
            duration_sec=DURATION_SEC,
            resolution=RESOLUTION,
            aspect_ratio=ASPECT_RATIO,
            fast_mode=FAST_MODE,
            full_access=FULL_ACCESS,
        )
    except ValueError as e:
        print(f"ABORT: Seedance payload validation failed: {e}", file=sys.stderr)
        return 4
    # Seedance Omni accepts `negative_prompt` as a sibling of `prompt`.
    # The provider's `build_ugc_payload` doesn't surface a negative-prompt
    # kwarg yet (smoke didn't need one); we splice it in here, ABOVE the
    # provider's submit hop, so the wire body carries it.
    ugc_payload["negative_prompt"] = NEGATIVE_PROMPT

    _write_json(run_dir / "01_seedance_queue_payload.json", ugc_payload)

    # ---- dry-run early exit -------------------------------------------- #
    if args.dry_run:
        print("[dry-run] Seedance UGC payload built + persisted; no HTTP call.")
        print()
        print("DRY-RUN summary:")
        for k in (
            "type", "mode", "duration", "resolution", "aspect_ratio",
            "fast_mode", "full_access",
        ):
            print(f"  {k:18s} : {ugc_payload[k]!r}")
        print(f"  products         : {ugc_payload['products']}")
        print(f"  influencers      : {ugc_payload['influencers']}")
        print(f"  prompt chars     : {len(ugc_payload['prompt'])}")
        print(f"  negative chars   : {len(ugc_payload['negative_prompt'])}")
        print()
        print(f"  payload saved at : {run_dir / '01_seedance_queue_payload.json'}")
        # API-key safety check: never include the key in the persisted artefact.
        try:
            txt = (run_dir / "01_seedance_queue_payload.json").read_text(encoding="utf-8")
            if os.environ.get("ENHANCOR_API_KEY") and os.environ["ENHANCOR_API_KEY"] in txt:
                print(
                    "FATAL: API key leaked into the dry-run payload artefact",
                    file=sys.stderr,
                )
                return 5
        except OSError:
            pass
        return 0

    # ---- LIVE: Seedance submit ----------------------------------------- #
    api_key = os.environ["ENHANCOR_API_KEY"]
    seedance = EnhancorSeedanceProvider(api_key)
    fixer = EnhancorAudioFixerProvider(api_key)

    seedance_request = ProviderJobRequest(
        provider=seedance.name,
        job_type="ugc",
        payload=ugc_payload,
        correlation_id=f"{PROSPECT_ID}-{ROUTE_ID}-{run_id}",
    )

    print("[seedance] Submitting UGC job...")
    try:
        seedance_resp = seedance.submit_job(seedance_request)
    except ProviderError as e:
        print(f"\nFATAL: Seedance submit failed: {e}", file=sys.stderr)
        if getattr(e, "raw_response", None) is not None:
            _write_json(run_dir / "02_seedance_submit_error.json", e.raw_response)
        return 20
    except Exception as e:
        print(f"\nFATAL: Seedance submit raised {type(e).__name__}: {e}",
              file=sys.stderr)
        return 21
    _write_json(run_dir / "02_seedance_submit_response.json",
                _serialise_dataclass(seedance_resp))
    print(f"  task_id     : {seedance_resp.provider_job_id}")
    print(f"  status      : {seedance_resp.status.value}")
    print(f"  submitted_at: {seedance_resp.submitted_at.isoformat()}")
    print()

    # ---- Seedance poll -------------------------------------------------- #
    print(f"[seedance] Polling status (every {args.poll_interval_sec}s; "
          f"timeout {args.timeout_min} min)...")
    try:
        seedance_result = seedance.wait_for_completion(
            seedance_resp.provider_job_id,
            correlation_id=seedance_request.correlation_id,
            poll_interval_sec=args.poll_interval_sec,
            timeout_sec=args.timeout_min * 60,
        )
    except ProviderError as e:
        print(f"\nFATAL: Seedance polling failed: {e}", file=sys.stderr)
        if getattr(e, "raw_response", None) is not None:
            _write_json(run_dir / "03_seedance_poll_error.json", e.raw_response)
        return 22
    _write_json(run_dir / "03_seedance_terminal_status.json",
                _serialise_dataclass(seedance_result))
    print("  status      : COMPLETED")
    print(f"  result_url  : {seedance_result.result_url}")
    print(f"  thumbnail   : {seedance_result.thumbnail_url}")
    print(f"  cost        : {seedance_result.cost}")
    print()

    # ---- Seedance download (raw mp4 + thumbnail) ----------------------- #
    print("[seedance] Downloading raw MP4 + thumbnail...")
    _ensure_dir(CLIPS_DIR)
    try:
        seedance.download_result(seedance_result.result_url, RAW_OUTPUT_PATH)
    except ProviderError as e:
        print(f"\nFATAL: Seedance MP4 download failed: {e}", file=sys.stderr)
        return 23

    if seedance_result.thumbnail_url:
        try:
            seedance.download_result(seedance_result.thumbnail_url, THUMB_OUTPUT_PATH)
        except ProviderError as e:
            print(f"WARN: thumbnail download failed: {e}", file=sys.stderr)

    raw_size = RAW_OUTPUT_PATH.stat().st_size
    thumb_size = THUMB_OUTPUT_PATH.stat().st_size if THUMB_OUTPUT_PATH.is_file() else 0
    print(f"  raw mp4     : {RAW_OUTPUT_PATH}  ({raw_size:,} B)")
    print(f"  thumb       : {THUMB_OUTPUT_PATH}  ({thumb_size:,} B)")
    print()

    # ---- Audio Fixer submit -------------------------------------------- #
    print("[audio-fixer] Submitting audio-fix job on Seedance result URL...")
    fixer_payload = build_audio_fixer_payload(
        input_video_url=seedance_result.result_url,
        webhook_url=WEBHOOK_URL,
    )
    _write_json(run_dir / "04_audio_fixer_queue_payload.json", fixer_payload)
    try:
        fixer_resp = fixer.submit_audio_fix(
            input_video_url=seedance_result.result_url,
            webhook_url=WEBHOOK_URL,
            correlation_id=seedance_request.correlation_id,
        )
    except ProviderError as e:
        print(f"\nFATAL: Audio Fixer submit failed: {e}", file=sys.stderr)
        if getattr(e, "raw_response", None) is not None:
            _write_json(run_dir / "05_audio_fixer_submit_error.json", e.raw_response)
        return 30
    _write_json(run_dir / "05_audio_fixer_submit_response.json",
                _serialise_dataclass(fixer_resp))
    print(f"  task_id     : {fixer_resp.provider_job_id}")
    print(f"  status      : {fixer_resp.status.value}")
    print()

    # ---- Audio Fixer poll ---------------------------------------------- #
    print(f"[audio-fixer] Polling status (every {args.poll_interval_sec}s; "
          f"timeout {args.timeout_min} min)...")
    try:
        fixer_result = fixer.wait_for_completion(
            fixer_resp.provider_job_id,
            correlation_id=seedance_request.correlation_id,
            poll_interval_sec=args.poll_interval_sec,
            timeout_sec=args.timeout_min * 60,
        )
    except ProviderError as e:
        print(f"\nFATAL: Audio Fixer polling failed: {e}", file=sys.stderr)
        if getattr(e, "raw_response", None) is not None:
            _write_json(run_dir / "06_audio_fixer_poll_error.json", e.raw_response)
        return 31
    _write_json(run_dir / "06_audio_fixer_terminal_status.json",
                _serialise_dataclass(fixer_result))
    print("  status      : COMPLETED")
    print(f"  result_url  : {fixer_result.result_url}")
    print(f"  cost        : {fixer_result.cost}")
    print()

    # ---- Audio Fixer download ------------------------------------------ #
    print("[audio-fixer] Downloading muxed MP4...")
    try:
        fixer.download_result(fixer_result.result_url, FIXED_OUTPUT_PATH)
    except ProviderError as e:
        print(f"\nFATAL: Audio Fixer download failed: {e}", file=sys.stderr)
        return 32
    fixed_size = FIXED_OUTPUT_PATH.stat().st_size
    print(f"  fixed mp4   : {FIXED_OUTPUT_PATH}  ({fixed_size:,} B)")
    print()

    # ---- Final per-run manifest ---------------------------------------- #
    manifest = {
        "run_id": run_id,
        "prospect_id": PROSPECT_ID,
        "route_id": ROUTE_ID,
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "seedance": {
            "task_id": seedance_resp.provider_job_id,
            "status": seedance_result.raw_completed_response.get("status")
                        if seedance_result.raw_completed_response else None,
            "result_url": seedance_result.result_url,
            "thumbnail_url": seedance_result.thumbnail_url,
            "cost": seedance_result.cost,
            "raw_output_path": str(RAW_OUTPUT_PATH),
            "raw_output_bytes": raw_size,
            "thumbnail_path": str(THUMB_OUTPUT_PATH) if THUMB_OUTPUT_PATH.is_file() else None,
        },
        "audio_fixer": {
            "task_id": fixer_resp.provider_job_id,
            "status": fixer_result.raw_completed_response.get("status")
                        if fixer_result.raw_completed_response else None,
            "result_url": fixer_result.result_url,
            "cost": fixer_result.cost,
            "fixed_output_path": str(FIXED_OUTPUT_PATH),
            "fixed_output_bytes": fixed_size,
        },
    }
    _write_json(run_dir / "07_run_manifest.json", manifest)

    print("=" * 78)
    print("DONE")
    print("=" * 78)
    print(f"  run manifest      : {run_dir / '07_run_manifest.json'}")
    print(f"  Seedance cost     : {seedance_result.cost}")
    print(f"  Audio Fixer cost  : {fixer_result.cost}")
    print(f"  raw output        : {RAW_OUTPUT_PATH}")
    print(f"  thumbnail         : {THUMB_OUTPUT_PATH}")
    print(f"  audio-fixed output: {FIXED_OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
