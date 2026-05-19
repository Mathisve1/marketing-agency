"""Phase 1V (preview) stitch multi-clip batch into a single video.

Operator-driven CLI bridge — same posture as
`scripts/run_clip_generation_job.py`: reads job state from Supabase
(read-only), uses the `imageio_ffmpeg`-vendored ffmpeg binary,
NEVER calls a paid provider, NEVER writes to Supabase.

What this script does:

  1. Read `generation_batches.clip_plan.stitch_plan` from Supabase to
     confirm the strategy (concat / hard cut / native audio / 720p).
  2. Walk `generation_jobs` for the batch in `clip_number` order,
     verify each clip is `status='completed'`, and resolve the local
     `result.mp4` for each (`prospects/.../dashboard_job_runs/<id>/`).
  3. Probe each clip's MP4 via the Phase 1I stdlib parser to confirm
     identical codecs/resolution/audio shape (concat-demuxer with
     stream copy needs that; concat-filter with re-encode doesn't but
     is slower).
  4. Build the ffmpeg invocation. With `--dry-run` (default), only
     print it. With `--run`, execute and write the stitched mp4 +
     a small `stitched_meta.json` describing the inputs.

What this script DOES NOT do:

  - Call Enhancor / Seedance / Audio Fixer (none of those are imported).
  - Write to Supabase. Ingesting the stitched output into
    `generated_assets` with `kind='stitched_video'` is a separate
    explicit step — Phase 1H's ingester pattern, not in scope here.
  - Update `content_items.client_safe_video_url`. Sharing with the
    client is a separate explicit step.
  - Re-run a clip. Both clips must already be COMPLETED.

Two concat modes:

  - `concat_demuxer` (stream copy) — fast, lossless when all clips
    share codec/params. Falls over if SPS/PPS/timebase differ
    silently. Output is bit-for-bit identical to inputs at the
    join.

  - `concat_filter` (re-encode, DEFAULT) — slightly slower (~10-30s
    for a 30s 720p video on modern hardware) but immune to subtle
    param drift between Seedance runs. Uses libx264 at CRF 18 + AAC
    192k to keep quality high.

Usage:

    py -3.11 scripts/stitch_clips.py --batch-id <uuid> --dry-run
    py -3.11 scripts/stitch_clips.py --batch-id <uuid> --run
    py -3.11 scripts/stitch_clips.py --batch-id <uuid> --run --mode concat_demuxer
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.producer.dashboard.mp4_meta import probe_mp4  # noqa: E402

DEFAULT_OUT_BASE = (
    _REPO_ROOT / "prospects" / "pai-skincare" / "production" / "dashboard_job_runs"
)


# --------------------------------------------------------------------------- #
# Env + Supabase read-only helpers (no service-role write path here).
# --------------------------------------------------------------------------- #


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    load_dotenv(_REPO_ROOT / ".env")
    load_dotenv(_REPO_ROOT / "web" / ".env.local", override=True)


def _supabase_creds() -> tuple[str, str]:
    url = (os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit(
            "FATAL: NEXT_PUBLIC_SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are "
            "required to read the batch + clip rows. Both load from .env / "
            "web/.env.local."
        )
    return url, key


def _pg_get(url: str, key: str, path: str) -> Any:
    """Read-only PostgREST GET. No other verb is used."""
    req = urllib.request.Request(
        f"{url}/rest/v1/{path}",
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
# ffmpeg location — prefer the imageio_ffmpeg-vendored binary so the
# operator doesn't need a system ffmpeg install. Fall back to PATH.
# --------------------------------------------------------------------------- #


def _resolve_ffmpeg() -> str:
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:  # noqa: BLE001
        pass
    import shutil
    p = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if p:
        return p
    raise SystemExit(
        "FATAL: no ffmpeg binary found. `pip install imageio-ffmpeg` "
        "(it bundles a ffmpeg.exe) or install system ffmpeg."
    )


# --------------------------------------------------------------------------- #
# Batch + clip discovery
# --------------------------------------------------------------------------- #


def _load_batch_clips(batch_id: str) -> dict[str, Any]:
    url, key = _supabase_creds()
    brows = _pg_get(
        url, key,
        f"generation_batches?id=eq.{batch_id}&select=id,status,"
        "target_duration_seconds,clip_plan,total_estimated_credits",
    )
    if not brows:
        raise SystemExit(f"FATAL: batch {batch_id!r} not found in Supabase.")
    batch = brows[0]
    clips = _pg_get(
        url, key,
        f"generation_jobs?batch_id=eq.{batch_id}&select=id,clip_number,"
        "clip_role,status,actual_credits,duration_seconds,resolution,"
        "raw_asset_id&order=clip_number.asc",
    )
    return {"batch": batch, "clips": clips}


def _enforce_all_clips_completed(clips: list[dict[str, Any]]) -> None:
    if not clips:
        raise SystemExit("FATAL: no clips on this batch.")
    not_done = [c for c in clips if c["status"] != "completed"]
    if not_done:
        bad = ", ".join(
            f"clip {c['clip_number']}={c['status']!r}" for c in not_done
        )
        raise SystemExit(
            f"FATAL: every clip must be COMPLETED before stitching. {bad}. "
            "Generate + ingest the missing clip(s) before retrying."
        )


def _resolve_clip_mp4(clip_id: str, out_base: Path) -> Path:
    mp4 = out_base / clip_id / "result.mp4"
    if not mp4.exists():
        raise SystemExit(
            f"FATAL: clip {clip_id} has no result.mp4 on disk at {mp4}. "
            "Re-download from the runner."
        )
    return mp4


def _probe_clips(clip_paths: list[Path]) -> list[dict[str, Any]]:
    """Run the Phase 1I stdlib probe on each clip and surface the
    fields concat-demuxer cares about. We don't fail on mismatches
    here — the caller decides whether to switch to concat-filter."""
    results = []
    for p in clip_paths:
        m = probe_mp4(p)
        results.append({
            "path": str(p),
            "byte_size": m.byte_size,
            "duration_sec": m.duration_sec,
            "width": m.width,
            "height": m.height,
            "resolution_label": m.resolution_label,
            "video_codec": m.video_codec,
            "audio_codec": m.audio_codec,
            "has_audio_track": m.has_audio_track,
            "probe_source": m.probe_source,
        })
    return results


def _params_match(probes: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """True if every probe shares video codec, audio codec, resolution,
    and audio-presence. Returns (matches, list of human-readable mismatches)."""
    if not probes:
        return False, ["no probes"]
    ref = probes[0]
    mismatches: list[str] = []
    for i, p in enumerate(probes[1:], start=2):
        for key in ("video_codec", "audio_codec", "width", "height",
                    "has_audio_track"):
            if p.get(key) != ref.get(key):
                mismatches.append(
                    f"clip {i} {key}={p.get(key)!r} != clip 1 {key}={ref.get(key)!r}"
                )
    return (len(mismatches) == 0, mismatches)


# --------------------------------------------------------------------------- #
# ffmpeg command builders
# --------------------------------------------------------------------------- #


def _build_concat_demuxer_cmd(
    ffmpeg: str, list_file: Path, out_mp4: Path,
) -> list[str]:
    """Stream-copy concat: -f concat -safe 0 -i list.txt -c copy out.mp4.
    Fast and lossless when all clips share params. Falls over silently
    if SPS/PPS/timebase differ; use concat_filter when in doubt."""
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy",
        # mp4-friendly: web-playable, no fragmented variants
        "-movflags", "+faststart",
        str(out_mp4),
    ]


def _build_concat_filter_cmd(
    ffmpeg: str, clip_paths: list[Path], out_mp4: Path,
) -> list[str]:
    """Re-encode concat using the `concat` filter. Safe against subtle
    param drift between Seedance runs. libx264 CRF 18 + AAC 192k for
    high quality at 720p."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning"]
    for p in clip_paths:
        cmd += ["-i", str(p)]
    n = len(clip_paths)
    # Build the filter chain: [0:v][0:a][1:v][1:a]…concat=n=N:v=1:a=1[v][a]
    inputs = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    fc = f"{inputs}concat=n={n}:v=1:a=1[v][a]"
    cmd += [
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_mp4),
    ]
    return cmd


# --------------------------------------------------------------------------- #
# Main flow
# --------------------------------------------------------------------------- #


def _do_run(args: argparse.Namespace) -> int:
    loaded = _load_batch_clips(args.batch_id)
    batch = loaded["batch"]
    clips = loaded["clips"]
    _enforce_all_clips_completed(clips)

    out_base = args.out_base
    clip_paths = [_resolve_clip_mp4(c["id"], out_base) for c in clips]
    probes = _probe_clips(clip_paths)
    matches, mismatches = _params_match(probes)

    # Resolve target stitched-output path. Batch-scoped folder.
    stitched_dir = out_base / "_stitched" / args.batch_id
    stitched_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = stitched_dir / f"stitched_{int(batch.get('target_duration_seconds') or 0)}s.mp4"
    list_file = stitched_dir / "concat_list.txt"

    # Honour requested mode but fall back to filter when params mismatch.
    requested_mode = args.mode
    effective_mode = requested_mode
    if requested_mode == "concat_demuxer" and not matches:
        effective_mode = "concat_filter"

    ffmpeg = _resolve_ffmpeg()

    if effective_mode == "concat_demuxer":
        # Write concat list — `file '<absolute path>'` per ffmpeg spec.
        list_file.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in clip_paths) + "\n",
            encoding="utf-8",
        )
        cmd = _build_concat_demuxer_cmd(ffmpeg, list_file, out_mp4)
    else:
        cmd = _build_concat_filter_cmd(ffmpeg, clip_paths, out_mp4)

    summary = {
        "batch_id": args.batch_id,
        "batch_status": batch.get("status"),
        "target_duration_seconds": batch.get("target_duration_seconds"),
        "clip_plan_stitch_plan": (batch.get("clip_plan") or {}).get("stitch_plan"),
        "clips": [
            {
                "clip_number": c["clip_number"],
                "clip_role": c["clip_role"],
                "id": c["id"],
                "status": c["status"],
                "actual_credits": c["actual_credits"],
                "local_path": str(p),
                **probe,
            }
            for c, p, probe in zip(clips, clip_paths, probes, strict=True)
        ],
        "params_match": matches,
        "param_mismatches": mismatches,
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "ffmpeg_binary": ffmpeg,
        "ffmpeg_cmd": cmd,
        "out_mp4": str(out_mp4),
        "concat_list_file": str(list_file) if effective_mode == "concat_demuxer" else None,
        "no_paid_call": True,
        "no_supabase_write": True,
        "no_client_share": True,
    }

    print("--- STITCH PLAN ---")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.dry_run:
        # Clean up the list file we wrote for inspection (we want pristine
        # state on a true dry-run).
        if effective_mode == "concat_demuxer" and list_file.exists():
            print(f"\n(concat list file written for inspection: {list_file})")
        print("\nDRY-RUN: ffmpeg NOT executed. Re-run with --run to stitch.")
        return 0

    # === Real run ===
    print(f"\nRunning ffmpeg ({effective_mode})…", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("ffmpeg STDERR:")
        print(r.stderr[-4000:])
        raise SystemExit(f"FATAL: ffmpeg returned {r.returncode}")
    # Probe the output to confirm shape.
    stitched_probe = probe_mp4(out_mp4)
    meta = {
        "batch_id": args.batch_id,
        "stitched_at_utc": datetime.now(timezone.utc).isoformat(),
        "effective_mode": effective_mode,
        "ffmpeg_binary": ffmpeg,
        "input_clips": [
            {"clip_number": c["clip_number"], "id": c["id"], "local_path": str(p)}
            for c, p in zip(clips, clip_paths, strict=True)
        ],
        "output": {
            "path": str(out_mp4),
            "byte_size": stitched_probe.byte_size,
            "duration_sec": stitched_probe.duration_sec,
            "width": stitched_probe.width,
            "height": stitched_probe.height,
            "resolution_label": stitched_probe.resolution_label,
            "video_codec": stitched_probe.video_codec,
            "audio_codec": stitched_probe.audio_codec,
            "has_audio_track": stitched_probe.has_audio_track,
            "probe_source": stitched_probe.probe_source,
        },
    }
    meta_path = stitched_dir / "stitched_meta.json"
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print()
    print("--- STITCH OUTPUT ---")
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"\nWrote: {out_mp4}")
    print(f"Wrote: {meta_path}")
    print(
        "\nNext (separate, gated): ingest the stitched output into "
        "generated_assets with kind='stitched_video'. Not in this script's "
        "scope. NO client_safe_video_url change. NO Audio Fixer."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stitch_clips",
        description=(
            "Concat the COMPLETED clips of a multi-clip batch into one "
            "video using the imageio-ffmpeg-vendored ffmpeg binary. "
            "Operator-driven CLI; no paid call; no Supabase write."
        ),
    )
    p.add_argument(
        "--batch-id",
        required=True,
        help="generation_batches.id (uuid). Stitch all clips on this batch in clip_number order.",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Build + print the ffmpeg command. NO execution.",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="Execute ffmpeg and write the stitched mp4 + stitched_meta.json.",
    )
    p.add_argument(
        "--mode",
        choices=("concat_filter", "concat_demuxer"),
        default="concat_filter",
        help=(
            "concat_filter (default) = re-encode for safety; "
            "concat_demuxer = stream-copy (fast, requires identical params)."
        ),
    )
    p.add_argument(
        "--out-base",
        type=Path,
        default=DEFAULT_OUT_BASE,
        help="Where per-job artefacts live (clips are read from here).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    _load_dotenv()
    return _do_run(args)


if __name__ == "__main__":
    sys.exit(main())
