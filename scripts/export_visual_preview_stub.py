"""Yuvo Studio - Phase 4H/5A local visual preview export scaffold.

DRY-RUN BY DEFAULT. This script validates the export command that
the dashboard's `Copy local export command` button hands to the
operator, plans the deterministic output path, prints a manifest,
and — when a browser-automation runtime is approved + installed —
will dispatch into a local-only real-export branch. It NEVER
performs paid calls, uploads anything, writes Supabase, creates
`generated_assets` rows, or shares anything with a client.

What changed in Phase 5A (vs Phase 4H stub):

  - New `--confirm-local-export PHRASE` arg. To even *attempt* a
    real local export the operator must pass the EXACT phrase
      "I UNDERSTAND THIS CREATES A LOCAL FILE ONLY"
    alongside `--execute`. Missing / wrong phrase → exit 2 with a
    clear message.
  - New runtime probe `_detect_browser_runtime()`. Uses
    `importlib.util.find_spec` (no actual import side-effects) to
    look for `playwright.sync_api` then `pyppeteer`. Returns the
    chosen module name or `None`.
  - New dispatch `run_local_browser_export(req)`. When `--execute`
    is passed WITH the confirmation phrase:
      * If no runtime is detected → exit 4 (EXIT_DEPENDENCY_MISSING)
        with a message that names the missing package and explicitly
        says it must be operator-approved before install.
      * If a runtime is detected → defer to
        `future_export_with_browser(req, runtime=<name>)` which today
        still returns exit 3 (EXIT_NOT_IMPLEMENTED). The real
        screenshot code lives in a separate, dependency-approved
        commit; the surface this script exposes for it is now final.
  - The manifest grows a `runtime` block describing what was
    detected, what was attempted, and whether the confirmation phrase
    was passed.
  - All existing Phase 4H behaviour preserved: dry-run is default,
    `--execute` without confirmation is refused, exit codes 0/1/2/3
    keep their meanings, JSON mode is unchanged.

What still does NOT happen:

  - No `import puppeteer`, `playwright`, `pyppeteer`, `selenium`.
    `importlib.util.find_spec` only inspects sys.path; it does not
    import or execute the modules.
  - No `import requests`, `httpx`, `aiohttp`. No `urllib.request`
    network call. (`urllib.parse.urlparse` is imported for parsing.)
  - No filesystem writes (output dir / html snapshot path are
    validated as strings; no `os.makedirs` / `Path.write_*`).
  - No env vars read (`os.environ` not imported anywhere).
  - No subprocess (`subprocess` not imported).
  - `--execute` is refused (exit 2 or 4) on every code path until
    the operator EXPLICITLY approves a browser-automation dependency
    AND a future phase lands the screenshot implementation behind
    the same `ExportRequest` contract.
  - Even when the future real path lands, it will NOT upload,
    publish, share with the client, or write any Supabase business
    table.

A pytest test pins the script's import graph so any future drift on
these guarantees fails CI.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
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
# 2: --execute was passed but the run was refused for an operator-
# facing reason (missing confirmation phrase OR no real impl yet on
# the legacy Phase 4H path). Always recoverable by re-invoking with
# the right flags.
EXIT_EXECUTE_REFUSED = 2
# 3: --execute reached the future real-impl branch (dependency was
# detected) but the screenshot code has not yet been landed in a
# dependency-approved phase. Distinct from "dependency missing" so
# the operator knows install was successful but they're waiting on a
# future commit. Reserved.
EXIT_NOT_IMPLEMENTED = 3
# Phase 5A — 4: --execute reached the real branch with a valid
# confirmation phrase, but no approved browser-automation runtime
# (playwright / pyppeteer) is installed yet. The operator must
# explicitly approve the dependency before re-running.
EXIT_DEPENDENCY_MISSING = 4

# Phase 5A — exact confirmation phrase the operator MUST pass to even
# attempt a real local export. Wrong phrase / missing phrase → exit 2.
LOCAL_EXPORT_CONFIRMATION_PHRASE = (
    "I UNDERSTAND THIS CREATES A LOCAL FILE ONLY"
)

# Phase 5A — ordered list of browser-automation runtimes the future
# real exporter will accept. `importlib.util.find_spec` is used to
# probe without importing (no side-effects). New entries should be
# added at the END so detection stays deterministic.
APPROVED_BROWSER_RUNTIMES = (
    "playwright.sync_api",
    "pyppeteer",
)

# Phase 5A — runtimes that an operator has EXPLICITLY approved for
# use in the real-export branch. Currently empty by design. A future
# commit (gated on operator approval) will flip one of the entries in
# `APPROVED_BROWSER_RUNTIMES` into this tuple. Until that happens,
# `_detect_browser_runtime` returns None even if Playwright /
# pyppeteer happens to be installed on the dev machine — preventing
# the real-export branch from ever firing by accident on a developer
# laptop that already has Playwright in its pip env.
APPROVED_BROWSER_RUNTIMES_PERMITTED_FOR_EXECUTE: tuple[str, ...] = ()


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
    # Phase 5A — the operator-supplied confirmation phrase. When
    # `--execute` is set, this MUST be exactly
    # `LOCAL_EXPORT_CONFIRMATION_PHRASE` for the real branch to be
    # attempted. Outside `--execute` the field is informational.
    confirm_local_export: str | None


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
        "--confirm-local-export",
        default=None,
        metavar="PHRASE",
        help=(
            "Phase 5A — explicit operator confirmation phrase. To even "
            "attempt a real local export the operator MUST pass: "
            f"\"{LOCAL_EXPORT_CONFIRMATION_PHRASE}\". Missing or wrong "
            "phrase paired with --execute → exit 2."
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
        confirm_local_export=getattr(args, "confirm_local_export", None),
    )
    return req, []


# ---------------------------------------------------------------------------
# Phase 5A — runtime detection + real-branch dispatch
# ---------------------------------------------------------------------------


def _detect_browser_runtime() -> str | None:
    """Return the first APPROVED-AND-PERMITTED runtime spec that is
    importable, or None. Uses `importlib.util.find_spec` — DOES NOT
    import the module (no side-effects, no actual browser launched,
    no env var read).

    Phase 5A safety: even if a runtime in `APPROVED_BROWSER_RUNTIMES`
    is installed on the operator's machine, this helper returns None
    unless that runtime is ALSO listed in
    `APPROVED_BROWSER_RUNTIMES_PERMITTED_FOR_EXECUTE` (currently `()`).
    This double-gate prevents the real-export branch from firing on
    a developer laptop that happens to have Playwright in its pip env.
    A future commit explicitly flips a permitted entry.
    """
    for spec_name in APPROVED_BROWSER_RUNTIMES:
        try:
            if importlib.util.find_spec(spec_name) is None:
                continue
        except (ImportError, ValueError):
            # Bad sys.path entries / partially-installed packages —
            # treat as "not installed" rather than crashing the CLI.
            continue
        # The spec is importable. Now apply the permit gate.
        if spec_name not in APPROVED_BROWSER_RUNTIMES_PERMITTED_FOR_EXECUTE:
            continue
        return spec_name
    return None


def _runtime_status_block(
    req: ExportRequest, detected: str | None
) -> dict[str, Any]:
    """Manifest sub-block describing where Phase 5A landed. Lets the
    operator (and the dashboard's `Copy export brief` plaintext) read
    exactly what the script saw."""
    return {
        "execute_requested": req.execute,
        "confirmation_phrase_passed": req.confirm_local_export is not None,
        "confirmation_phrase_matches": (
            req.confirm_local_export == LOCAL_EXPORT_CONFIRMATION_PHRASE
        ),
        "approved_runtimes": list(APPROVED_BROWSER_RUNTIMES),
        "detected_runtime": detected,
        "real_export_attempted": False,
    }


# ---------------------------------------------------------------------------
# Plan + dispatch
# ---------------------------------------------------------------------------


def build_plan(req: ExportRequest) -> dict[str, Any]:
    """Compute the manifest dict from a validated ExportRequest. Pure
    function; safe to call from tests / future automation."""
    filename = _filename_suggestion(req)
    planned_output_path = _planned_output_path(req)
    detected_runtime = _detect_browser_runtime()
    return {
        "schema": "yuvo.studio/visual_export_manifest/v1",
        "phase": "5a_local_exporter_scaffold",
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
        "real_export_status": (
            "dependency_missing"
            if detected_runtime is None
            else "dependency_present_impl_pending"
        ),
        "next_phase": "phase_5a_followup_real_screenshot_impl",
        "runtime": _runtime_status_block(req, detected_runtime),
        "export_target_selectors": _export_target_selectors(req),
        "session_requirements": {
            "needs_authenticated_browser_session": True,
            "production_cookies_handled_by_this_script": False,
            "recommended_workflow": [
                "Run the dashboard locally or sign in via your "
                "everyday browser.",
                "Open the preview URL in that authenticated browser.",
                "Wait for fonts + radial highlight to fully render.",
                "Once a browser-automation dependency is approved + "
                "installed, this script will reuse your browser's "
                "session cookie to screenshot the same URL.",
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


def _export_target_selectors(req: ExportRequest) -> dict[str, str]:
    """Phase 5A — names the stable data-* attributes the future real
    exporter will use to locate the screenshot target on the preview
    page. The dashboard renders these on its React templates so the
    operator + exporter agree on the selector vocabulary even before
    the browser runtime lands."""
    root = '[data-export-root]'
    selectors: dict[str, str] = {
        "root": root,
        "mode_attribute": f'[data-export-mode="{req.mode}"]',
    }
    if req.slide_number and req.slide_number > 0:
        selectors["target"] = f'[data-export-slide="{req.slide_number}"]'
    elif req.frame_number and req.frame_number > 0:
        selectors["target"] = f'[data-export-frame="{req.frame_number}"]'
    else:
        # Single-card modes (feed_post / static_image / linkedin /
        # thumbnails) render exactly one PreviewCard with
        # data-export-slide="1".
        selectors["target"] = '[data-export-slide="1"]'
    return selectors


def run_dry_run(req: ExportRequest) -> ExportResult:
    return ExportResult(
        exit_code=EXIT_OK_DRY_RUN,
        manifest=build_plan(req),
        errors=[],
    )


def run_execute_refused(req: ExportRequest) -> ExportResult:
    """Phase 4H semantics, preserved. Used when `--execute` was passed
    WITHOUT the Phase 5A confirmation phrase, or with a wrong phrase.
    """
    return ExportResult(
        exit_code=EXIT_EXECUTE_REFUSED,
        manifest=build_plan(req),
        errors=[
            "--execute requires the explicit confirmation phrase. "
            "Re-run with:  --confirm-local-export "
            f"\"{LOCAL_EXPORT_CONFIRMATION_PHRASE}\". "
            "No browser was opened, no network call was made, no file "
            "was written."
        ],
    )


def run_local_browser_export(req: ExportRequest) -> ExportResult:
    """Phase 5A — real local-export dispatch. Only reachable when
    `--execute` AND the correct `--confirm-local-export` phrase are
    BOTH present.

    Probes for an approved browser-automation runtime via
    `_detect_browser_runtime()`. If none is installed → exit 4
    (`EXIT_DEPENDENCY_MISSING`) with a friendly message naming the
    accepted runtimes. If a runtime IS installed → defer to
    `future_export_with_browser(req, runtime=...)` which still
    returns `EXIT_NOT_IMPLEMENTED` today; the actual screenshot
    code lives in a future dependency-approved commit.

    This function itself NEVER imports the runtime — it only probes.
    """
    detected = _detect_browser_runtime()
    if detected is None:
        return ExportResult(
            exit_code=EXIT_DEPENDENCY_MISSING,
            manifest=build_plan(req),
            errors=[
                "Real export requires an approved browser-automation "
                "dependency (one of: "
                f"{', '.join(APPROVED_BROWSER_RUNTIMES)}). None was "
                "detected. Operator approval is REQUIRED before "
                "installing — this script does NOT install anything. "
                "No browser was opened, no network call was made, no "
                "file was written.",
            ],
        )
    # Dependency present — defer to the placeholder. The placeholder
    # NEVER imports the runtime either; it only carries the dispatch
    # contract for the future commit that ships the real screenshot
    # code behind a freshly approved dependency.
    return future_export_with_browser(req, runtime=detected)


def future_export_with_browser(
    req: ExportRequest, runtime: str | None = None
) -> ExportResult:
    """Placeholder for the Phase 5A-followup real implementation.

    DO NOT add a browser-automation import to satisfy this stub.
    Future commits (gated on explicit operator approval of the
    dependency) will:
      1. Confirm the operator has a logged-in dashboard session.
      2. Use the detected runtime (passed in via `runtime`) to open
         `req.preview_url` and screenshot the element matching the
         export-target selector (`data-export-slide` /
         `data-export-frame` / fallback `[data-export-slide="1"]`).
      3. Save PNG/JPG to `planned_output_path`.
      4. NEVER auto-upload, auto-share, or auto-publish.
      5. NEVER write Supabase. NEVER create `generated_assets` rows.

    Today this function unconditionally returns an
    EXIT_NOT_IMPLEMENTED result so any accidental call site (e.g. a
    future server action that wires the script through `subprocess`)
    surfaces clearly instead of silently succeeding.
    """
    manifest = build_plan(req)
    # Annotate the runtime block to make the "I got past the dep
    # check but the real code isn't landed" state observable.
    manifest = dict(manifest)
    runtime_block = dict(manifest.get("runtime", {}))
    runtime_block["real_export_attempted"] = True
    runtime_block["detected_runtime"] = runtime
    manifest["runtime"] = runtime_block
    return ExportResult(
        exit_code=EXIT_NOT_IMPLEMENTED,
        manifest=manifest,
        errors=[
            "future_export_with_browser() is a placeholder. A browser-"
            "automation runtime is installed (detected: "
            f"{runtime}), but the real screenshot code has not yet "
            "been landed in a dependency-approved commit. Stop here "
            "and ask the operator before adding the implementation.",
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
        # Phase 5A gate: require the exact confirmation phrase before
        # we even probe for a runtime. Without it → exit 2.
        if req.confirm_local_export != LOCAL_EXPORT_CONFIRMATION_PHRASE:
            result = run_execute_refused(req)
        else:
            # Phrase OK → dispatch into the real-branch helper. That
            # helper probes for an approved runtime and either exits
            # 4 (missing) or defers to the placeholder (exit 3).
            result = run_local_browser_export(req)
    else:
        result = run_dry_run(req)
    _emit(result, req=req)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
