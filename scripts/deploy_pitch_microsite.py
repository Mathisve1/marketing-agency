"""CLI to deploy one prospect's microsite to Cloudflare Pages.

Usage:
  py -3.11 scripts/deploy_pitch_microsite.py <prospect_id> [--dry-run] [--skip-build]

Required env vars (load via .env or set in the shell before running):
  CLOUDFLARE_ACCOUNT_ID
  CLOUDFLARE_API_TOKEN
  CLOUDFLARE_PAGES_PROJECT     (default project name: yuvo-pitches)

Optional:
  PITCH_BASE_URL               (default: https://yuvo-pitches.pages.dev)
  WRANGLER_BIN                 (default: wrangler on PATH)

Steps:
  1. Validate env.
  2. (Unless --skip-build) Rebuild the prospect microsite and the
     `build/pitches/p/<slug>/` deploy folder.
  3. Run `wrangler pages deploy build/pitches --project-name=<...>`
     unless --dry-run.
  4. Update the prospect manifest with status=deployed + deployed_at.
  5. Print the public URL (and the wrangler-reported deployment URL,
     if any).

The script never prints `CLOUDFLARE_API_TOKEN`. Wrangler reads it from
the child env, so the command line printed above is safe to share.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Project root on sys.path when invoked as a script (Python adds the
# script's parent dir, which is scripts/, by default; tests import the
# module directly so this guard only fires under CLI use).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agents.outreach.reporting.microsite_builder import (  # noqa: E402
    DEFAULT_PUBLIC_BASE_URL,
    PITCH_BASE_URL_ENV,
    build_deploy_package,
    build_microsite,
)
from services.pitch_deploy import (  # noqa: E402
    REQUIRED_DEPLOY_ENV,
    check_required_env,
    deploy_to_cloudflare_pages,
    mark_manifest_deployed,
)

log = logging.getLogger("pitch_deploy")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_dotenv_if_available() -> None:
    """Load `.env` so the script works the same as Streamlit/MCP entrypoints.

    No-op when python-dotenv is not installed; the operator can also
    pre-export the variables in their shell.
    """
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except Exception:
        return
    load_dotenv(_REPO_ROOT / ".env")


def _print_env_summary(stream) -> None:
    """Print which env vars are set, without ever echoing their values."""
    base = os.environ.get(PITCH_BASE_URL_ENV) or DEFAULT_PUBLIC_BASE_URL
    project = os.environ.get("CLOUDFLARE_PAGES_PROJECT") or "yuvo-pitches"
    print(f"PITCH_BASE_URL              = {base}", file=stream)
    print(f"CLOUDFLARE_PAGES_PROJECT    = {project}", file=stream)
    print(
        f"CLOUDFLARE_ACCOUNT_ID       = "
        f"{'(set)' if os.environ.get('CLOUDFLARE_ACCOUNT_ID') else '(MISSING)'}",
        file=stream,
    )
    print(
        f"CLOUDFLARE_API_TOKEN        = "
        f"{'(set, redacted)' if os.environ.get('CLOUDFLARE_API_TOKEN') else '(MISSING)'}",
        file=stream,
    )


def _read_manifest_public_url(prospect_id: str, prospects_root: Optional[Path]) -> Optional[str]:
    """Best-effort: read the existing manifest's public_url for --skip-build."""
    root = Path(prospects_root) if prospects_root else _REPO_ROOT / "prospects"
    manifest_path = root / prospect_id / "site" / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8")).get("public_url")
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# CLI entry
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deploy_pitch_microsite",
        description=(
            "Deploy a prospect microsite to Cloudflare Pages. Builds "
            "build/pitches/p/<slug>/ first unless --skip-build is given."
        ),
    )
    p.add_argument("prospect_id", help="Prospect ID under prospects/")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the wrangler command without contacting Cloudflare.",
    )
    p.add_argument(
        "--skip-build",
        action="store_true",
        help=(
            "Skip microsite + deploy-package rebuild. Use when "
            "build/pitches/p/<slug>/ is already up to date."
        ),
    )
    p.add_argument(
        "--prospects-root",
        type=Path,
        default=None,
        help="Override prospects/ root (defaults to <repo>/prospects).",
    )
    p.add_argument(
        "--build-root",
        type=Path,
        default=None,
        help="Override build/ root (defaults to <repo>/build).",
    )
    p.add_argument(
        "--branch",
        default="main",
        help="Cloudflare Pages branch / environment (default: main).",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="DEBUG-level logging.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    _load_dotenv_if_available()

    print("=" * 60)
    print(f"Deploying prospect: {args.prospect_id}")
    print("=" * 60)
    _print_env_summary(sys.stdout)

    missing = check_required_env()
    if missing:
        print("\nFATAL: missing required env vars:", file=sys.stderr)
        for v in missing:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nSet them in .env or your shell. See "
            "docs/microsite_deployment.md for the Cloudflare Pages setup.",
            file=sys.stderr,
        )
        print(
            f"\nThe complete list is: {', '.join(REQUIRED_DEPLOY_ENV)}",
            file=sys.stderr,
        )
        return 2

    # ── 1. Build (unless --skip-build) ────────────────────────────────────────
    prospects_root = args.prospects_root or (_REPO_ROOT / "prospects")
    build_root = args.build_root or (_REPO_ROOT / "build")

    if args.skip_build:
        manifest_public_url = _read_manifest_public_url(args.prospect_id, prospects_root)
        if manifest_public_url is None:
            print(
                f"FATAL: --skip-build given but no manifest at "
                f"{prospects_root / args.prospect_id / 'site' / 'manifest.json'}",
                file=sys.stderr,
            )
            return 3
        print(f"\n[skip-build] reusing existing manifest; public_url = {manifest_public_url}")
        manifest = {"public_url": manifest_public_url}
    else:
        print("\n[build] running build_microsite()...")
        # On a real (non-dry-run) deploy, render the HTML with the Live
        # banner before upload. Without this, the first deploy of a
        # fresh prospect ships the amber Draft banner because the
        # manifest only flips to "deployed" via mark_manifest_deployed
        # *after* upload - so the HTML in build/pitches/ would still
        # claim "not deployed yet". Dry-run intentionally leaves
        # intended_status=None so the local preview keeps the Draft
        # banner.
        build_intended_status = None if args.dry_run else "deployed"
        manifest = build_microsite(
            args.prospect_id,
            prospects_root=prospects_root,
            intended_status=build_intended_status,
        )
        print(f"  private_slug : {manifest['private_slug']}")
        print(f"  public_url   : {manifest['public_url']}")
        print(f"  assets       : {len(manifest['assets_used'])}")
        if manifest.get("watermarked_video"):
            print(f"  preview vid  : {manifest['preview_video_path']}")
        print("\n[build] running build_deploy_package()...")
        deploy_pkg = build_deploy_package(
            args.prospect_id,
            prospects_root=prospects_root,
            build_root=build_root,
        )
        print(f"  deploy dir   : {deploy_pkg}")

    # ── 2. Deploy to Cloudflare Pages (unless --dry-run) ─────────────────────
    deploy_root = build_root / "pitches"
    if not deploy_root.exists():
        print(
            f"FATAL: deploy folder {deploy_root} does not exist - run without "
            "--skip-build to create it.",
            file=sys.stderr,
        )
        return 4

    project_name = os.environ["CLOUDFLARE_PAGES_PROJECT"]
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_token = os.environ["CLOUDFLARE_API_TOKEN"]

    if args.dry_run:
        print()
        print("#" * 60)
        print("#  DRY-RUN MODE  -  NOTHING WILL BE UPLOADED TO CLOUDFLARE  #")
        print("#  The public URL below WILL 404 until you run this again  #")
        print("#  WITHOUT --dry-run.                                       #")
        print("#" * 60)
    else:
        print()
        print("=" * 60)
        print("  REAL DEPLOY  -  uploading deploy package to Cloudflare    ")
        print("  Pages now. Public URL should be reachable when this       ")
        print("  finishes successfully.                                    ")
        print("=" * 60)

    print("\n[deploy] calling wrangler...")
    result = deploy_to_cloudflare_pages(
        deploy_dir=deploy_root,
        project_name=project_name,
        account_id=account_id,
        api_token=api_token,
        branch=args.branch,
        dry_run=args.dry_run,
    )
    # Safe to print: argv carries no secrets (creds go via env to the child).
    print("  command      : " + " ".join(result.argv))
    print(f"  dry-run      : {result.dry_run}")
    print(f"  status       : {'OK' if result.ok else 'FAIL'}")
    print(f"  message      : {result.message}")

    if not result.ok:
        return 5

    # ── 3. Update manifest after a real deploy ────────────────────────────────
    if not args.dry_run:
        manifest_path = prospects_root / args.prospect_id / "site" / "manifest.json"
        if manifest_path.exists():
            merged = mark_manifest_deployed(
                manifest_path,
                public_url=manifest["public_url"],
                provider="cloudflare_pages",
                deployment_url=result.deployment_url,
            )
            print(
                f"\n[manifest] updated {manifest_path}:\n"
                f"  status            : {merged.get('status')}\n"
                f"  deployment_provider : {merged.get('deployment_provider')}\n"
                f"  deployed_at        : {merged.get('deployed_at')}\n"
                f"  deployment_url     : {merged.get('deployment_url')}\n"
                f"  public_url         : {merged.get('public_url')}"
            )
        else:
            print(
                f"WARN: manifest not found at {manifest_path}; "
                "deploy succeeded but provenance was not recorded.",
                file=sys.stderr,
            )

    print()
    print("=" * 60)
    if args.dry_run:
        print("DRY-RUN COMPLETE - NO URL IS LIVE.")
        print(
            "The 'public_url' below is the planned location; it returns "
            "404 until you re-run this script without --dry-run."
        )
    else:
        print("DEPLOY COMPLETE - prospect public URL should now be reachable.")
    print(f"  public_url   : {manifest.get('public_url', '(unknown)')}")
    if result.deployment_url:
        print(f"  wrangler url : {result.deployment_url}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
