"""Phase 1S — per-clip DRY-RUN payload builder for multi-clip Supabase jobs.

This is the safe, read-only counterpart to scripts/run_generation_job.py
for the Phase 1R multi-clip draft jobs that live in Supabase (the
`run_generation_job.py` runner only reads the static demo catalogue).

Hard guarantees — by construction this script CANNOT spend credits:
  - There is NO --submit mode. The ONLY thing it does is build + validate
    + print the Seedance wire payload. No HTTP call to any provider.
  - It never imports or constructs an EnhancorSeedanceProvider.
  - It only issues read-only PostgREST GETs against Supabase (service
    role) to load the job + prompt + batch + sibling clips. It performs
    NO INSERT / UPDATE / DELETE.
  - Product / influencer URLs are forced to the demo PLACEHOLDER values,
    so even the printed payload is non-submittable (the paid runner
    refuses placeholders). Real asset URLs never touch this path.
  - Webhook is the placeholder default.

Clip-sequencing rule (Phase 1S):
  - clip_number == 1 (open_loop) may always dry-run.
  - clip_number N > 1 is BLOCKED until clip N-1 in the same batch has
    status == 'completed'. If clip N-1 failed/cancelled/draft, clip N
    stays blocked and NO payload is built.
  - Only status == 'draft' jobs are eligible.

Usage (always safe, no API call):

    py -3.11 scripts/clip_dry_run.py --job-id <generation_jobs.id>
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.producer.dashboard.demo_jobs import (  # noqa: E402
    DemoGenerationJob,
    is_placeholder_url,
)
from agents.producer.dashboard.payload_builder import (  # noqa: E402
    NEGATIVE_PROMPT_MAX_CHARS,
    build_seedance_payload_from_job,
)

_PLACEHOLDER_PRODUCT_URL = (
    "https://example.com/pai-skincare/PLACEHOLDER-product.jpg"
)
_PLACEHOLDER_INFLUENCER_URL = (
    "https://example.com/pai-skincare/PLACEHOLDER-influencer.jpg"
)
_PLACEHOLDER_WEBHOOK = "https://example.com/webhooks/enhancor/seedance"

_OUT_BASE = (
    _REPO_ROOT / "prospects" / "pai-skincare" / "production"
    / "dashboard_job_runs"
)


def _load_env() -> tuple[str, str]:
    env_path = _REPO_ROOT / "web" / ".env.local"
    env: dict[str, str] = {}
    for ln in env_path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            env[k] = v.strip().strip('"')
    url = env["NEXT_PUBLIC_SUPABASE_URL"].rstrip("/")
    key = env["SUPABASE_SERVICE_ROLE_KEY"]
    return url, key


def _get(url: str, key: str, path: str) -> Any:
    """Read-only PostgREST GET. No other HTTP verb is ever used here."""
    u = f"{url}/rest/v1/{path}"
    req = urllib.request.Request(
        u, headers={"apikey": key, "Authorization": f"Bearer {key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"FATAL: Supabase GET {path} -> {e.code} {e.read().decode()[:300]}"
        ) from e


def _fail(payload: dict[str, Any], code: int) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="clip_dry_run",
        description=(
            "Phase 1S read-only per-clip dry-run. Builds + validates the "
            "Seedance payload for a Supabase multi-clip draft job. NEVER "
            "calls a provider; NEVER writes to the database."
        ),
    )
    p.add_argument("--job-id", required=True, help="generation_jobs.id (uuid)")
    args = p.parse_args(argv)

    url, key = _load_env()

    jrows = _get(
        url, key,
        f"generation_jobs?id=eq.{args.job_id}&select=id,batch_id,"
        "content_item_id,prompt_version_id,provider,provider_mode,"
        "quality_tier,resolution,duration_seconds,status,estimated_credits,"
        "actual_credits,clip_number,clip_role,provider_request_id,"
        "raw_request_json,raw_response_json",
    )
    if not jrows:
        return _fail(
            {"result": "error",
             "error": f"job {args.job_id} not found in Supabase"}, 2)
    job = jrows[0]

    pv = _get(
        url, key,
        f"prompt_versions?id=eq.{job['prompt_version_id']}&select=id,label,"
        "version_number,status,hook,prompt_body,negative_prompt,scene_plan,"
        "creator_direction,product_constraints",
    )
    prompt = pv[0] if pv else None
    brows = _get(
        url, key,
        f"generation_batches?id=eq.{job['batch_id']}&select=id,label,status,"
        "target_duration_seconds,clip_plan,total_estimated_credits",
    )
    batch = brows[0] if brows else None
    siblings = _get(
        url, key,
        f"generation_jobs?batch_id=eq.{job['batch_id']}&select=id,"
        "clip_number,clip_role,status&order=clip_number.asc",
    )

    clip_number = job.get("clip_number")
    clip_role = job.get("clip_role")
    base_info = {
        "job_id": job["id"],
        "batch_id": job["batch_id"],
        "clip_number": clip_number,
        "clip_role": clip_role,
        "duration_seconds": job["duration_seconds"],
        "resolution": job["resolution"],
        "estimated_credits": job["estimated_credits"],
        "status": job["status"],
        "target_duration_seconds": (
            batch.get("target_duration_seconds") if batch else None
        ),
        "provider": job["provider"],
        "no_paid_call": True,
    }

    # --- Gate 1: only draft jobs are eligible ---------------------------
    if job["status"] != "draft":
        return _fail({
            "result": "blocked", "reason": "status_not_draft",
            "message": (
                f"Job status is {job['status']!r}; only 'draft' clips can "
                "be dry-run / submitted."
            ),
            **base_info,
        }, 3)

    # --- Gate 2: clip sequencing ---------------------------------------
    if clip_number is not None and clip_number > 1:
        prior = next(
            (s for s in siblings if s.get("clip_number") == clip_number - 1),
            None,
        )
        prior_status = prior["status"] if prior else None
        if prior_status != "completed":
            return _fail({
                "result": "blocked", "reason": "prior_clip_not_completed",
                "message": (
                    f"Clip {clip_number} is blocked: clip {clip_number - 1} "
                    f"status is {prior_status!r} (must be 'completed' before "
                    f"this clip can dry-run or submit). open_loop clip 1 "
                    f"must be generated and reviewed first."
                ),
                "prior_clip": prior,
                **base_info,
            }, 3)

    if prompt is None:
        return _fail({
            "result": "error", "error": "prompt_version not found",
            **base_info,
        }, 2)

    # --- Build the wire payload (placeholder assets ONLY) ---------------
    demo_job = DemoGenerationJob(
        id=job["id"],
        batch_id=job["batch_id"],
        content_item_id=job["content_item_id"],
        prompt_version_id=job["prompt_version_id"],
        provider=job["provider"],
        provider_mode=job["provider_mode"] or "ugc",
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
        placeholder_product_urls=(_PLACEHOLDER_PRODUCT_URL,),
        placeholder_influencer_urls=(_PLACEHOLDER_INFLUENCER_URL,),
    )

    try:
        payload = build_seedance_payload_from_job(
            demo_job,
            webhook_url=_PLACEHOLDER_WEBHOOK,
            product_urls=None,
            influencer_urls=None,
        )
    except ValueError as e:
        return _fail({
            "result": "error",
            "error": f"payload validation failed: {e}",
            **base_info,
        }, 2)

    # --- Validation checks ---------------------------------------------
    products = payload.get("products", [])
    influencers = payload.get("influencers", [])
    neg = ""
    if "\n\nNegative: " in payload.get("prompt", ""):
        neg = payload["prompt"].split("\n\nNegative: ", 1)[1]
    checks = {
        "no_video_inputs": "videos" not in payload,
        "no_audio_inputs": "audios" not in payload,
        "resolution_720p": payload.get("resolution") == "720p",
        "duration_15s": payload.get("duration") == "15",
        "aspect_ratio_9_16": payload.get("aspect_ratio") == "9:16",
        "type_image_to_video": payload.get("type") == "image-to-video",
        "mode_ugc": payload.get("mode") == "ugc",
        "has_product_url": len(products) >= 1,
        "has_influencer_url": len(influencers) >= 1,
        "product_urls_all_https": all(
            u.startswith("https://") for u in products
        ),
        "influencer_urls_all_https": all(
            u.startswith("https://") for u in influencers
        ),
        "prompt_has_hook": "Hook:" in payload.get("prompt", ""),
        "prompt_has_body": "Prompt body:" in payload.get("prompt", ""),
        "negative_within_500": len(neg) <= NEGATIVE_PROMPT_MAX_CHARS,
    }
    placeholder_assets = [
        u for u in (products + influencers) if is_placeholder_url(u)
    ]

    out_dir = _OUT_BASE / job["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_path = out_dir / "dry_run_payload.json"
    payload_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    all_pass = all(checks.values())
    summary = {
        "result": "ok" if all_pass else "checks_failed",
        **base_info,
        "prompt_version": {
            "id": prompt["id"],
            "label": prompt.get("label"),
            "version_number": prompt.get("version_number"),
            "status": prompt.get("status"),
        },
        "batch": {
            "id": batch["id"] if batch else None,
            "status": batch.get("status") if batch else None,
            "target_duration_seconds": (
                batch.get("target_duration_seconds") if batch else None
            ),
        },
        "siblings": siblings,
        "checks": checks,
        "all_checks_pass": all_pass,
        "negative_prompt_chars": len(neg),
        "placeholder_assets": placeholder_assets,
        "placeholder_note": (
            "products/influencers are PLACEHOLDER URLs by design — this "
            "payload is non-submittable. Paid submit is a separate, "
            "operator-driven CLI step (not wired in Phase 1S)."
        ),
        "saved_payload_to": str(payload_path),
        "audio_fixer": "NOT run (manual, never auto-triggered)",
    }
    print("--- PHASE 1S CLIP DRY-RUN (NO API CALL, NO DB WRITE) ---------")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n--- PAYLOAD PREVIEW (saved to %s) ---" % payload_path)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("\nNO HTTP call was made. NO credits spent. NO DB write.")
    return 0 if all_pass else 4


if __name__ == "__main__":
    sys.exit(main())
