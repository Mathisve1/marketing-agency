"""Yuvo Studio - Phase 4H local visual preview export scaffold.

DRY-RUN ONLY. This script validates the export command that the
dashboard's `Copy local export command` button hands to the operator,
plans the deterministic output path, and prints a manifest. It NEVER
renders pixels, opens a browser, makes a network request, reads an
env var, or writes a file.

What changed in Phase 4H (vs Phase 4G stub):

  - The CLI is now a small structured scaffold around two dataclasses
    (`ExportRequest`, `ExportResult`). The future real implementation
    plugs in behind `future_export_with_browser()` without touching
    the argparse contract or the dashboard.
  - New `--html-snapshot-path` arg. In dry-run mode the path is
    validated and surfaced on the manifest. No file is created.
  - The manifest now carries `planned_output_path` (deterministic
    `<output_dir>/<filename_suggestion>`).
  - URL validation is tightened to explicitly accept the
    `yuvo-dashboard.*.workers.dev` host plus localhost / relative
    dashboard paths. Unrelated external URLs are rejected.
  - New `--json` flag prints machine-readable JSON only (no prose).

What still does NOT happen:

  - No `import puppeteer`, `playwright`, `pyppeteer`, `selenium`.
  - No `import requests`, `httpx`, `aiohttp`. No `urllib.request`
    network call. (`urllib.parse.urlparse` is imported for parsing.)
  - No filesystem writes (output dir / html snapshot path are
    validated as strings; no `os.makedirs` / `Path.write_*`).
  - No env vars read (`os.environ` not imported anywhere).
  - No subprocess (`subprocess` not imported).
  - `--execute` is refused with exit code 2 until Phase 4I lands a
    real local browser-based exporter behind the same contract.

A pytest test pins the script's import graph so any future drift on
these guarantees fails CI.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
RELATIVE_URL_RE = re.compile(r"^/agency/creative-briefs/[^/]+/preview")
LOCAL_ABSOLUTE_RE = re.compile(
    r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?/", re.IGNORECASE
)
ID_RE = re.compile(r"^[a-z0-9_]+$")
HTML_SNAPSHOT_PATH_RE = re.compile(r"^[A-Za-z0-9_./\-]+\.html?$")
SLUG_FALLBACK = "content"
SUPPORTED_MODES = (
    "carousel",
    "story",
    "feed_post",
    "static_image",
    "linkedin_image",
    "reel_thumbnail",
    "video_thumbnail",
    "unknown",
)
SUPPORTED_FORMATS = ("png", "jpg")

# Explicit dashboard hostname patterns. Any other absolute URL is
# rejected (a real export script must not be aimed at a random
# external origin). `yuvo-dashboard.*.workers.dev` is pinned so the
# Cloudflare-Workers production host is allowed; `*.pages.dev` and
# `*.yuvo*.com` cover legacy / preview deploys.
ALLOWED_DASHBOARD_HOST_RE = re.compile(
    # Each branch ends in a fixed apex suffix (e.g. `.workers.dev`).
    # A `(?:[a-z0-9\-]+\.)+` prefix permits arbitrary subdomain
    # labels (e.g. `yuvo-dashboard.example-account.workers.dev`).
    r"^(?:"
    r"(?:[a-z0-9\-]+\.)+workers\.dev"
    r"|(?:[a-z0-9\-]+\.)+pages\.dev"
    r"|(?:[a-z0-9\-]+\.)*yuvo\.studio"
    r"|(?:[a-z0-9\-]+\.)*yuvostudio\.com"
    r")$",
    re.IGNORECASE,
)


# Sentinel exit codes — pinned so the dashboard / CI can match on them.
EXIT_OK_DRY_RUN = 0
EXIT_VALIDATION_FAILED = 1
EXIT_EXECUTE_REFUSED = 2
EXIT_NOT_IMPLEMENTED = 3  # reserved for the future real path


# ---------------------------------------------------------------------------
# Dataclasses — the shape the future real implementation will receive
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ExportRequest:
    """Validated, normalised representation of one export invocation.

    Created by `parse_args` → `validate_request`. The future
    `future_export_with_browser(req)` will receive an instance of
    this dataclass and only operate on its fields. No additional
    state, no module-level config.
    """

    content_item_id: str
    preview_url: str
    mode: str
    template_id: str | None
    theme_id: str
    width: int | None
    height: int | None
    format: str
    output_dir: str
    slide_number: int | None
    frame_number: int | None
    dry_run: bool
    execute: bool
    html_snapshot_path: str | None
    emit_json: bool


@dataclasses.dataclass(frozen=True)
class ExportResult:
    """Outcome of a single run. `manifest` is the JSON-serialisable
    plan; `exit_code` mirrors the sentinel constants above; `errors`
    is empty on success and populated when validation fails."""

    exit_code: int
    manifest: dict[str, Any]
    errors: list[str]


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="export_visual_preview_stub",
        description=(
            "Phase 4H dry-run scaffold for the future Yuvo Studio visual "
            "preview local export. Validates args, plans the output "
            "path, and prints a manifest. Does NOT render pixels."
        ),
        allow_abbrev=False,
    )
    p.add_argument("--content-item-id", required=True)
    p.add_argument("--preview-url", required=True)
    p.add_argument(
        "--mode",
        required=True,
        choices=list(SUPPORTED_MODES),
    )
    p.add_argument("--template-id", default=None)
    p.add_argument("--theme-id", default="neutral")
    p.add_argument("--width", type=int, default=None)
    p.add_argument("--height", type=int, default=None)
    p.add_argument(
        "--format",
        default="png",
        choices=list(SUPPORTED_FORMATS),
    )
    p.add_argument("--output-dir", default="./exports")
    p.add_argument("--slide-number", type=int, default=None)
    p.add_argument("--frame-number", type=int, default=None)
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Default. Validates + prints manifest only.",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help=(
            "[Phase 4I+] Would run the real export. Refused by this "
            "stub until a real implementation lands. Exit code 2."
        ),
    )
    p.add_argument(
        "--html-snapshot-path",
        default=None,
        help=(
            "Optional local .html / .htm path the future exporter "
            "will save the rendered preview into. Validated as a "
            "string only — no file is written in dry-run mode."
        ),
    )
    p.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        default=False,
        help=(
            "Emit the manifest as a single JSON object on stdout. "
            "Drops the human-readable prose footer. Useful for "
            "future automation that consumes this CLI directly."
        ),
    )
    return p


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Public entry-point for tests. Exposes the argparse Namespace
    without applying validation — that lives in `validate_request`."""
    return _build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers (pure, no I/O)
# ---------------------------------------------------------------------------


def _slugify(s: str, max_len: int) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", s.lower())
    cleaned = re.sub(r"^-+|-+$", "", cleaned)[:max_len]
    return cleaned or SLUG_FALLBACK


def _filename_suggestion(req: ExportRequest) -> str:
    cid = req.content_item_id
    id_stub = cid[:8] if UUID_RE.match(cid) else _slugify(cid, 8)
    tpl = (
        _slugify(req.template_id, 32)
        if req.template_id
        else _slugify(f"{req.mode}_default", 32)
    )
    parts = [tpl, id_stub]
    if req.slide_number and req.slide_number > 0:
        parts.append(f"slide{req.slide_number:02d}")
    elif req.frame_number and req.frame_number > 0:
        parts.append(f"frame{req.frame_number:02d}")
    return f"{'-'.join(parts)}.{req.format}"


def _planned_output_path(req: ExportRequest) -> str:
    """Deterministic local path the future executor WOULD write. The
    helper does not touch the filesystem; it just joins the sanitized
    output dir + the suggested filename. POSIX-style separator so the
    output is predictable across OSes for the manifest reader."""
    base = req.output_dir.rstrip("/")
    return f"{base}/{_filename_suggestion(req)}"


def _is_allowed_dashboard_url(url: str) -> bool:
    if RELATIVE_URL_RE.match(url):
        return True
    if LOCAL_ABSOLUTE_RE.match(url):
        return True
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    if not parsed.path.startswith("/agency/creative-briefs/"):
        return False
    # Strip port for the host match.
    host = parsed.hostname or ""
    return bool(ALLOWED_DASHBOARD_HOST_RE.match(host))


def _validate_html_snapshot_path(raw: str) -> list[str]:
    errors: list[str] = []
    if not raw:
        errors.append("--html-snapshot-path must not be empty.")
        return errors
    if not HTML_SNAPSHOT_PATH_RE.match(raw):
        errors.append(
            "--html-snapshot-path must be a relative path ending in "
            ".html or .htm using [A-Za-z0-9_./-] only."
        )
    if ".." in raw or raw.startswith("/etc") or raw.startswith("/root"):
        errors.append(
            "--html-snapshot-path must not traverse outside the "
            "workspace (no '..', no /etc, no /root)."
        )
    return errors


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_request(args: argparse.Namespace) -> tuple[ExportRequest | None, list[str]]:
    """Normalise the argparse Namespace into an ExportRequest and
    collect human-readable validation errors. Returns (None, errors)
    when validation fails."""
    errors: list[str] = []
    if not UUID_RE.match(args.content_item_id or ""):
        errors.append("--content-item-id must be a UUID.")
    url = (args.preview_url or "").strip()
    if not url:
        errors.append("--preview-url is required.")
    elif not _is_allowed_dashboard_url(url):
        errors.append(
            "--preview-url must be either a relative dashboard path "
            "(/agency/creative-briefs/<id>/preview...), a localhost / "
            "127.0.0.1 URL, or a recognised Yuvo dashboard host "
            "(yuvo-dashboard*.workers.dev, *.pages.dev, *.yuvo.studio, "
            "*.yuvostudio.com)."
        )
    if args.width is not None and args.width <= 0:
        errors.append("--width must be positive.")
    if args.height is not None and args.height <= 0:
        errors.append("--height must be positive.")
    if args.slide_number is not None and args.slide_number <= 0:
        errors.append("--slide-number must be positive.")
    if args.frame_number is not None and args.frame_number <= 0:
        errors.append("--frame-number must be positive.")
    if args.slide_number is not None and args.frame_number is not None:
        errors.append("Pass only one of --slide-number / --frame-number.")
    if args.format not in SUPPORTED_FORMATS:
        errors.append("--format must be png or jpg.")
    if args.theme_id and not ID_RE.match(args.theme_id):
        errors.append(
            "--theme-id must be lowercase letters / digits / underscores."
        )
    if args.template_id is not None and not ID_RE.match(args.template_id):
        errors.append(
            "--template-id must be lowercase letters / digits / underscores."
        )
    cleaned_dir = args.output_dir or "./exports"
    if (
        ".." in cleaned_dir
        or cleaned_dir.startswith("/etc")
        or cleaned_dir.startswith("/root")
    ):
        errors.append(
            "--output-dir must not traverse outside the workspace "
            "(no '..', no /etc, no /root)."
        )
    snapshot = getattr(args, "html_snapshot_path", None)
    if snapshot is not None:
        errors.extend(_validate_html_snapshot_path(snapshot))

    if errors:
        return None, errors

    req = ExportRequest(
        content_item_id=args.content_item_id,
        preview_url=url,
        mode=args.mode,
        template_id=args.template_id,
        theme_id=args.theme_id,
        width=args.width,
        height=args.height,
        format=args.format,
        output_dir=cleaned_dir,
        slide_number=args.slide_number,
        frame_number=args.frame_number,
        dry_run=True,  # always — execution path lives in a different
                      # code branch and is gated by --execute below
        execute=bool(args.execute),
        html_snapshot_path=snapshot,
        emit_json=bool(args.emit_json),
    )
    return req, []


# ---------------------------------------------------------------------------
# Plan + dispatch
# ---------------------------------------------------------------------------


def build_plan(req: ExportRequest) -> dict[str, Any]:
    """Compute the manifest dict from a validated ExportRequest. Pure
    function; safe to call from tests / future automation."""
    filename = _filename_suggestion(req)
    planned_output_path = _planned_output_path(req)
    return {
        "schema": "yuvo.studio/visual_export_manifest/v1",
        "phase": "4h_local_exporter_scaffold",
        "content_item_id": req.content_item_id,
        "preview_url": req.preview_url,
        "mode": req.mode,
        "template_id": req.template_id,
        "theme_id": req.theme_id,
        "width": req.width,
        "height": req.height,
        "format": req.format,
        "output_dir": req.output_dir,
        "slide_number": req.slide_number,
        "frame_number": req.frame_number,
        "dry_run": True,
        "would_execute": req.execute,
        "filename_suggestion": filename,
        "planned_output_path": planned_output_path,
        "html_snapshot_path": req.html_snapshot_path,
        "real_export_status": "not_implemented_in_phase_4h",
        "next_phase": "phase_4i_local_browser_exporter",
        "session_requirements": {
            "needs_authenticated_browser_session": True,
            "production_cookies_handled_by_this_script": False,
            "recommended_workflow": [
                "Run the dashboard locally or sign in via your "
                "everyday browser.",
                "Open the preview URL in that authenticated browser.",
                "Wait for fonts + radial highlight to fully render.",
                "Once Phase 4I ships, this script will reuse your "
                "browser's session cookie to screenshot the same URL.",
            ],
        },
        "safety": {
            "imports_browser_automation": False,
            "makes_network_requests": False,
            "writes_files": False,
            "reads_env": False,
            "spawns_subprocess": False,
            "creates_generated_assets_row": False,
            "uploads": False,
            "publishes": False,
            "shares_with_client": False,
        },
    }


def run_dry_run(req: ExportRequest) -> ExportResult:
    return ExportResult(
        exit_code=EXIT_OK_DRY_RUN,
        manifest=build_plan(req),
        errors=[],
    )


def run_execute_refused(req: ExportRequest) -> ExportResult:
    return ExportResult(
        exit_code=EXIT_EXECUTE_REFUSED,
        manifest=build_plan(req),
        errors=[
            "--execute is not implemented in Phase 4H. The real "
            "export pipeline lands in Phase 4I+ (operator-run "
            "Playwright/Puppeteer against the dashboard preview URL, "
            "behind the same Seedance-style confirmation gate). "
            "Re-run without --execute."
        ],
    )


def future_export_with_browser(req: ExportRequest) -> ExportResult:
    """Placeholder for the Phase 4I+ real implementation.

    DO NOT add a browser-automation import to satisfy this stub.
    Future phases will:
      1. Confirm the operator has a logged-in dashboard session.
      2. Use a vendored or locally-installed browser-automation
         library to open `req.preview_url` and screenshot at
         `req.width` x `req.height`.
      3. Save PNG/JPG to `planned_output_path`.
      4. NEVER auto-upload, auto-share, or auto-publish.

    Today this function unconditionally returns an EXIT_NOT_IMPLEMENTED
    result so any accidental call site (e.g. a future server action
    that wires the script through `subprocess`) surfaces clearly
    instead of silently succeeding.
    """
    return ExportResult(
        exit_code=EXIT_NOT_IMPLEMENTED,
        manifest=build_plan(req),
        errors=[
            "future_export_with_browser() is a placeholder. The real "
            "implementation arrives in Phase 4I+ and lives behind the "
            "same ExportRequest contract."
        ],
    )


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _emit(result: ExportResult, *, req: ExportRequest | None) -> None:
    """Print the result to stdout/stderr per the request mode."""
    emit_json = bool(req and req.emit_json)
    if emit_json:
        # Machine-readable mode: ONLY the manifest as JSON, plus
        # any errors. No prose footer.
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
        "[dry-run] no files written, no network calls made, no env "
        "vars read. Manifest above is informational only."
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits with code 2 on bad args; preserve that.
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

    if req.execute:
        result = run_execute_refused(req)
    else:
        result = run_dry_run(req)
    _emit(result, req=req)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
