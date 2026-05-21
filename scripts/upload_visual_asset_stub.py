"""Yuvo Studio — Phase 5B local visual asset upload stub.

DRY-RUN ONLY. This script validates an upload-plan for one exported
visual file and prints a manifest. It NEVER uploads anything,
NEVER reads Cloudflare credentials, NEVER reads any env var, NEVER
opens a network connection, NEVER writes a Supabase row, NEVER
imports a storage SDK.

Phase 5B's contract:

  - The script is the operator-facing shape of the future upload
    action. The dashboard and the local export script (Phase 5A)
    both eventually hand the operator the exact `upload_visual_
    asset_stub.py …` invocation with all flags pre-filled.
  - The argparse shape is locked NOW so Phase 5C only has to flip
    the dispatch (from `run_dry_run` → a real `uploadVisualAsset
    Action` server-action proxy) without changing operator habits.

Hard guarantees (pinned by `scripts/test_upload_visual_asset_stub.py`):

  - No `import boto3 | botocore | aioboto3 | aiobotocore`.
  - No `import google.cloud.storage | minio | s3fs`.
  - No `import requests | httpx | aiohttp | urllib.request`.
  - No `import supabase`. No `psycopg`. No `psycopg2`.
  - No `import subprocess`. No `import os.environ` (`os` is not
    imported at all).
  - `--execute` is refused with exit code 2 until Phase 5C's
    operator-approved upload action lands.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import PurePosixPath
from typing import Any

# ---------------------------------------------------------------------------
# Constants (mirror web/lib/creative/visual-asset-paths.ts)
# ---------------------------------------------------------------------------

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
ID_RE = re.compile(r"^[a-z0-9_\-]{1,80}$")
ALLOWED_FORMATS = ("png", "jpg")
ALLOWED_LOCAL_EXTENSIONS = (".png", ".jpg", ".jpeg")
# Mirrors `buildVisualAssetPath`: visual-assets/{ws}/{ci}/{tpl}/{theme}/{filename}
STORAGE_KEY_PREFIX = "visual-assets"

# Max upload size (proposed, used in dry-run validation). 8 MiB.
MAX_FILE_SIZE_BYTES = 8 * 1024 * 1024

# Exit codes — mirror the export stub's sentinels for consistency.
EXIT_OK_DRY_RUN = 0
EXIT_VALIDATION_FAILED = 1
EXIT_EXECUTE_REFUSED = 2

# The operator-facing binding name pinned in
# docs/visual_asset_storage_plan.md §2.
RECOMMENDED_R2_BINDING = "VISUAL_ASSETS_BUCKET"
RECOMMENDED_R2_BUCKET_NAME = "yuvo-visual-assets"

# Control characters (C0 + DEL). Stored as a set so the check is
# escape-immune (writing `[\x00-\x1f]` in a regex literal got mangled
# by the tooling — escape-free approach is safer).
_CONTROL_CHARS = frozenset(chr(i) for i in range(0, 32)) | {chr(127)}


def _has_control_chars(s: str) -> bool:
    return any(ch in _CONTROL_CHARS for ch in s)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class UploadRequest:
    """Validated representation of a single dry-run upload invocation."""

    local_path: str
    workspace_id: str
    content_item_id: str
    mode: str
    template_id: str | None
    theme_id: str
    format: str
    width: int | None
    height: int | None
    slide_number: int | None
    frame_number: int | None
    execute: bool
    emit_json: bool


@dataclasses.dataclass(frozen=True)
class UploadResult:
    exit_code: int
    manifest: dict[str, Any]
    errors: list[str]


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="upload_visual_asset_stub",
        description=(
            "Phase 5B dry-run scaffold for the future Yuvo Studio "
            "visual asset upload. Validates args + storage key + "
            "local file path constraints. Does NOT upload, does NOT "
            "open a network connection, does NOT read env vars."
        ),
        allow_abbrev=False,
    )
    p.add_argument("--local-path", required=True)
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--content-item-id", required=True)
    p.add_argument(
        "--mode",
        required=True,
        choices=[
            "carousel",
            "story",
            "feed_post",
            "static_image",
            "linkedin_image",
            "reel_thumbnail",
            "video_thumbnail",
        ],
    )
    p.add_argument("--template-id", default=None)
    p.add_argument("--theme-id", default="neutral")
    p.add_argument(
        "--format",
        default="png",
        choices=list(ALLOWED_FORMATS),
    )
    p.add_argument("--width", type=int, default=None)
    p.add_argument("--height", type=int, default=None)
    p.add_argument("--slide-number", type=int, default=None)
    p.add_argument("--frame-number", type=int, default=None)
    p.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help=(
            "[Phase 5C+] Would call the real uploadVisualAssetAction. "
            "Refused by this stub until the action + R2 binding land. "
            "Exit code 2."
        ),
    )
    p.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        default=False,
    )
    return p


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers (pure, no I/O)
# ---------------------------------------------------------------------------


def _sanitize_segment(raw: str, max_len: int) -> str:
    # Preserve `_` and existing `-` (template ids use them as
    # semantic separators); collapse anything else to a single `-`.
    cleaned = re.sub(r"[^a-z0-9_\-]+", "-", raw.lower())
    cleaned = re.sub(r"^-+|-+$", "", cleaned)[:max_len]
    return cleaned or "content"


def _validate_local_path(raw: str) -> list[str]:
    errors: list[str] = []
    if not raw or not isinstance(raw, str):
        errors.append("--local-path must be a non-empty string.")
        return errors
    if ".." in raw or raw.startswith("/etc") or raw.startswith("/root"):
        errors.append(
            "--local-path must not traverse outside the workspace "
            "(no '..', no /etc, no /root)."
        )
    lower = raw.lower()
    if not any(lower.endswith(ext) for ext in ALLOWED_LOCAL_EXTENSIONS):
        errors.append(
            "--local-path must end in one of "
            f"{', '.join(ALLOWED_LOCAL_EXTENSIONS)}."
        )
    # Reject NUL / control chars. Uses a precomputed set instead of a
    # regex literal because `\x00-\x1f` in a regex string got mangled
    # by the file-writer tooling.
    if _has_control_chars(raw):
        errors.append("--local-path must not contain control characters.")
    return errors


def _filename_suggestion(req: UploadRequest) -> str:
    """Mirrors `buildVisualAssetFilename` in
    `web/lib/creative/visual-asset-paths.ts`. Kept structurally
    identical so the dashboard + CLI agree on naming."""
    cid_stub = req.content_item_id[:8]
    template = (
        _sanitize_segment(req.template_id, 40)
        if req.template_id
        else _sanitize_segment(f"{req.mode}_default", 40)
    )
    parts = [template, cid_stub]
    if req.slide_number and req.slide_number > 0:
        parts.append(f"slide{req.slide_number:02d}")
    elif req.frame_number and req.frame_number > 0:
        parts.append(f"frame{req.frame_number:02d}")
    if (
        req.width
        and req.height
        and req.width > 0
        and req.height > 0
    ):
        parts.append(f"{req.width}x{req.height}")
    return f"{'-'.join(parts)}.{req.format}"


def _storage_key(req: UploadRequest) -> str:
    template = (
        _sanitize_segment(req.template_id, 40)
        if req.template_id
        else _sanitize_segment(f"{req.mode}_default", 40)
    )
    theme = _sanitize_segment(req.theme_id, 30)
    key = PurePosixPath(
        STORAGE_KEY_PREFIX,
        req.workspace_id,
        req.content_item_id,
        template,
        theme,
        _filename_suggestion(req),
    )
    return str(key)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_request(args: argparse.Namespace) -> tuple[UploadRequest | None, list[str]]:
    errors: list[str] = []
    if not UUID_RE.match(args.workspace_id or ""):
        errors.append("--workspace-id must be a UUID.")
    if not UUID_RE.match(args.content_item_id or ""):
        errors.append("--content-item-id must be a UUID.")
    errors.extend(_validate_local_path(args.local_path or ""))
    if args.template_id is not None and not ID_RE.match(args.template_id):
        errors.append("--template-id must match [a-z0-9_-]{1,80} after lowercasing.")
    if not ID_RE.match(args.theme_id or ""):
        errors.append("--theme-id must match [a-z0-9_-]{1,80} after lowercasing.")
    if args.format not in ALLOWED_FORMATS:
        errors.append("--format must be png or jpg.")
    for name, value in (
        ("--width", args.width),
        ("--height", args.height),
        ("--slide-number", args.slide_number),
        ("--frame-number", args.frame_number),
    ):
        if value is not None and value <= 0:
            errors.append(f"{name} must be positive when set.")
    if args.slide_number is not None and args.frame_number is not None:
        errors.append("Pass only one of --slide-number / --frame-number.")
    if errors:
        return None, errors

    req = UploadRequest(
        local_path=args.local_path,
        workspace_id=args.workspace_id,
        content_item_id=args.content_item_id,
        mode=args.mode,
        template_id=args.template_id,
        theme_id=args.theme_id,
        format=args.format,
        width=args.width,
        height=args.height,
        slide_number=args.slide_number,
        frame_number=args.frame_number,
        execute=bool(args.execute),
        emit_json=bool(args.emit_json),
    )
    return req, []


# ---------------------------------------------------------------------------
# Plan + dispatch
# ---------------------------------------------------------------------------


def build_plan(req: UploadRequest) -> dict[str, Any]:
    """Compute the manifest dict from a validated UploadRequest. Pure
    function; safe to call from tests / future automation."""
    key = _storage_key(req)
    return {
        "schema": "yuvo.studio/visual_asset_upload_manifest/v1",
        "phase": "5b_upload_stub",
        "local_path": req.local_path,
        "workspace_id": req.workspace_id,
        "content_item_id": req.content_item_id,
        "mode": req.mode,
        "template_id": req.template_id,
        "theme_id": req.theme_id,
        "format": req.format,
        "width": req.width,
        "height": req.height,
        "slide_number": req.slide_number,
        "frame_number": req.frame_number,
        "would_execute": req.execute,
        "storage_key": key,
        "directory_prefix": key.rsplit("/", 1)[0],
        "filename": _filename_suggestion(req),
        "recommended_r2_binding": RECOMMENDED_R2_BINDING,
        "recommended_r2_bucket": RECOMMENDED_R2_BUCKET_NAME,
        "max_file_size_bytes": MAX_FILE_SIZE_BYTES,
        "next_phase": "phase_5c_real_upload_action",
        "safety": {
            "imports_storage_sdk": False,
            "imports_http_client": False,
            "imports_supabase": False,
            "reads_env": False,
            "makes_network_requests": False,
            "writes_files": False,
            "creates_generated_assets_row": False,
            "creates_creative_assets_row": False,
            "uploads": False,
            "publishes": False,
            "shares_with_client": False,
        },
    }


def run_dry_run(req: UploadRequest) -> UploadResult:
    return UploadResult(
        exit_code=EXIT_OK_DRY_RUN,
        manifest=build_plan(req),
        errors=[],
    )


def run_execute_refused(req: UploadRequest) -> UploadResult:
    return UploadResult(
        exit_code=EXIT_EXECUTE_REFUSED,
        manifest=build_plan(req),
        errors=[
            "--execute is not implemented in Phase 5B. The real "
            "upload pipeline arrives in Phase 5C+ behind the "
            "operator-only `uploadVisualAssetAction` server action, "
            "the R2 binding `VISUAL_ASSETS_BUCKET`, and the "
            "`creative_assets` migration. Re-run without --execute.",
        ],
    )


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _emit(result: UploadResult, *, req: UploadRequest | None) -> None:
    emit_json = bool(req and req.emit_json)
    if emit_json:
        payload = {
            "exit_code": result.exit_code,
            "manifest": result.manifest,
            "errors": result.errors,
        }
        print(json.dumps(payload, indent=2))
        return
    print(json.dumps(result.manifest, indent=2))
    if result.errors:
        print()
        for e in result.errors:
            print(f"[refused] {e}", file=sys.stderr)
        return
    print()
    print(
        "[dry-run] no upload was attempted, no network call was made, "
        "no env var was read, no file was written."
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1

    req, errors = validate_request(args)
    if req is None:
        emit_json = bool(getattr(args, "emit_json", False))
        if emit_json:
            payload = {
                "exit_code": EXIT_VALIDATION_FAILED,
                "manifest": None,
                "errors": errors,
            }
            print(json.dumps(payload, indent=2))
        else:
            print("VALIDATION FAILED:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
        return EXIT_VALIDATION_FAILED

    result = run_execute_refused(req) if req.execute else run_dry_run(req)
    _emit(result, req=req)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
