"""Cloudflare Pages deploy helper for prospect microsites.

Wraps the `wrangler pages deploy` subprocess call so the CLI script
(`scripts/deploy_pitch_microsite.py`) stays thin and the test layer can
mock the subprocess without touching argparse.

Design constraints:
  - Never echo the API token. Wrangler reads `CLOUDFLARE_API_TOKEN` and
    `CLOUDFLARE_ACCOUNT_ID` from the env, so the command line itself
    carries no secrets - we only have to be careful not to log the env.
  - Dry-run path returns the would-be argv WITHOUT spawning a process.
  - On success, `mark_manifest_deployed` flips the prospect's
    `status` to `"deployed"` and stamps `deployed_at` so the operator
    inbox can show "ready to share".

Wrangler installation lives in the operator's PATH, not in this repo
(npm-based, prone to global-state issues). If wrangler is missing, the
deploy returns a clear error rather than masking the failure.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #


WRANGLER_BIN_ENV = "WRANGLER_BIN"
"""Optional env var that overrides the wrangler executable name. Useful
for tests, or for operators who install via `npx wrangler`."""

DEFAULT_WRANGLER_CMD = "wrangler"
"""Fallback executable name when WRANGLER_BIN is unset."""

REQUIRED_DEPLOY_ENV = (
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_PAGES_PROJECT",
)
"""The three env vars the deploy script refuses to run without."""


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass
class DeployResult:
    """Outcome of a deploy call - returned even in dry-run mode."""
    ok: bool
    dry_run: bool
    argv: list[str] = field(default_factory=list)
    """The command we ran (or would have run). Safe to print: never
    contains the API token."""
    project_name: str = ""
    branch: str = ""
    deploy_dir: str = ""
    message: str = ""
    """Human-readable summary - 'dry-run', stderr tail, etc."""
    deployment_url: Optional[str] = None
    """The `.pages.dev` URL wrangler reports for the deployment. Wrangler
    only emits this on a real run; dry-run leaves it None."""


# --------------------------------------------------------------------------- #
# Env validation
# --------------------------------------------------------------------------- #


def check_required_env(env: Optional[dict] = None) -> list[str]:
    """Return the list of missing required env vars (empty when all set)."""
    e = env if env is not None else os.environ
    return [v for v in REQUIRED_DEPLOY_ENV if not (e.get(v) or "").strip()]


# --------------------------------------------------------------------------- #
# Deploy
# --------------------------------------------------------------------------- #


def _resolve_wrangler_cmd() -> str:
    """Pick the wrangler executable to spawn.

    Priority:
      1. `WRANGLER_BIN` env var (operator override; tests use this).
      2. On Windows, `shutil.which("wrangler.cmd")` then
         `shutil.which("wrangler")` so we resolve to the absolute path
         of the npm-installed batch shim. `subprocess.run` on Windows
         uses `CreateProcess`, which does not honour `PATHEXT`, so a
         bare `"wrangler"` argv fails to spawn even when
         `wrangler.cmd` is on `PATH`. `shutil.which` honours
         `PATHEXT`, so it returns the right absolute path.
      3. Plain `"wrangler"` — works on POSIX where the shell shim is
         directly executable.
    """
    explicit = os.environ.get(WRANGLER_BIN_ENV)
    if explicit:
        return explicit
    if sys.platform == "win32":
        for name in ("wrangler.cmd", DEFAULT_WRANGLER_CMD):
            resolved = shutil.which(name)
            if resolved:
                return resolved
    return DEFAULT_WRANGLER_CMD


def _build_argv(
    *,
    wrangler_cmd: str,
    deploy_dir: Path,
    project_name: str,
    branch: str,
) -> list[str]:
    """Construct the wrangler argv. No secrets here - safe to print."""
    return [
        wrangler_cmd,
        "pages",
        "deploy",
        str(deploy_dir),
        f"--project-name={project_name}",
        f"--branch={branch}",
    ]


def deploy_to_cloudflare_pages(
    *,
    deploy_dir: Path,
    project_name: str,
    account_id: str,
    api_token: str,
    branch: str = "main",
    dry_run: bool = False,
    wrangler_cmd: Optional[str] = None,
) -> DeployResult:
    """Push `deploy_dir` to the Cloudflare Pages project `project_name`.

    Args:
      deploy_dir: local folder whose contents become the site root. For
        prospects this is `build/pitches/` (so every prospect is one
        sub-route, `p/<slug>/`).
      project_name: matches the Cloudflare Pages project. Default is
        `yuvo-pitches`, which serves the free `*.pages.dev` URL.
      account_id, api_token: Cloudflare creds. Passed via env to the
        subprocess so they never appear on the command line.
      branch: which Pages environment to deploy to. `main` is the
        production branch by Pages convention.
      dry_run: when True, returns the would-be argv without spawning a
        process and without contacting Cloudflare.
      wrangler_cmd: explicit override for the wrangler executable. Tests
        use this; otherwise it falls back to `$WRANGLER_BIN` then
        `wrangler`.

    Returns a DeployResult. The caller is responsible for surfacing
    `result.message` to the operator and, on success, updating the
    prospect manifest via `mark_manifest_deployed`.
    """
    wrangler_cmd = wrangler_cmd or _resolve_wrangler_cmd()
    argv = _build_argv(
        wrangler_cmd=wrangler_cmd,
        deploy_dir=deploy_dir,
        project_name=project_name,
        branch=branch,
    )

    if dry_run:
        return DeployResult(
            ok=True,
            dry_run=True,
            argv=argv,
            project_name=project_name,
            branch=branch,
            deploy_dir=str(deploy_dir),
            message=(
                "dry-run: not contacting Cloudflare; would have run the "
                "command above with CLOUDFLARE_API_TOKEN + "
                "CLOUDFLARE_ACCOUNT_ID injected via env."
            ),
        )

    if shutil.which(wrangler_cmd) is None and not Path(wrangler_cmd).exists():
        return DeployResult(
            ok=False,
            dry_run=False,
            argv=argv,
            project_name=project_name,
            branch=branch,
            deploy_dir=str(deploy_dir),
            message=(
                f"wrangler executable not found: {wrangler_cmd!r}. "
                f"Install with `npm install -g wrangler` or set "
                f"{WRANGLER_BIN_ENV} to a full path."
            ),
        )

    # Inject creds via env. The subprocess inherits the rest of our env
    # too (some operators rely on PATH / Node config there).
    child_env = dict(os.environ)
    child_env["CLOUDFLARE_ACCOUNT_ID"] = account_id
    child_env["CLOUDFLARE_API_TOKEN"] = api_token

    try:
        # `encoding="utf-8", errors="replace"` is load-bearing: wrangler
        # prints UTF-8 box-drawing chars and emoji in its progress
        # output. Without an explicit encoding, Python falls back to
        # the platform default (cp1252 on Windows) and the reader
        # thread raises UnicodeDecodeError, which corrupts capture and
        # leaves `_parse_deployment_url` with no text to scan even
        # though wrangler itself exited 0.
        completed = subprocess.run(
            argv,
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as e:
        return DeployResult(
            ok=False,
            dry_run=False,
            argv=argv,
            project_name=project_name,
            branch=branch,
            deploy_dir=str(deploy_dir),
            message=f"failed to spawn wrangler: {e}",
        )

    # Defensive: redact the token if it somehow leaked into stdout/stderr.
    redacted_out = _redact(completed.stdout, api_token, account_id)
    redacted_err = _redact(completed.stderr, api_token, account_id)
    log.info("pitch_deploy: wrangler exited %d", completed.returncode)
    if redacted_out:
        log.debug("pitch_deploy stdout:\n%s", redacted_out)
    if redacted_err:
        log.debug("pitch_deploy stderr:\n%s", redacted_err)

    deployment_url = _parse_deployment_url(redacted_out + "\n" + redacted_err)
    ok = completed.returncode == 0
    summary_tail = (redacted_err or redacted_out or "").strip().splitlines()[-1:] or [""]
    return DeployResult(
        ok=ok,
        dry_run=False,
        argv=argv,
        project_name=project_name,
        branch=branch,
        deploy_dir=str(deploy_dir),
        deployment_url=deployment_url,
        message=(
            f"wrangler exit {completed.returncode}: {summary_tail[0]}"
            if not ok
            else "deploy ok"
        ),
    )


def _redact(text: str, *secrets: str) -> str:
    """Replace any secret substring with `***` so logs/messages stay safe."""
    if not text:
        return text or ""
    out = text
    for s in secrets:
        if s and len(s) >= 4 and s in out:
            out = out.replace(s, "***")
    return out


def _parse_deployment_url(stdout_stderr: str) -> Optional[str]:
    """Best-effort extraction of the `.pages.dev` URL wrangler prints.

    Wrangler's output format has changed over major versions; we look
    for the most recent shape (a line containing `https://...pages.dev`)
    and fall back to None when not found.
    """
    if not stdout_stderr:
        return None
    for line in stdout_stderr.splitlines():
        line = line.strip()
        if "https://" in line and "pages.dev" in line:
            for token in line.split():
                if token.startswith("https://") and "pages.dev" in token:
                    return token.rstrip(".,;)\"'")
    return None


# --------------------------------------------------------------------------- #
# Manifest update
# --------------------------------------------------------------------------- #


def mark_manifest_deployed(
    manifest_path: Path,
    *,
    public_url: str,
    provider: str = "cloudflare_pages",
    deployment_url: Optional[str] = None,
) -> dict:
    """Merge deploy metadata onto an existing site manifest and write it back.

    Sets:
      status              = "deployed"
      deployment_provider = `provider`
      deployed_at         = now (UTC ISO-8601)
      public_url          = `public_url`
      deployment_url      = wrangler's `.pages.dev` URL (when known)

    Returns the merged manifest dict. Raises FileNotFoundError when the
    manifest does not exist - call sites must run the microsite build
    first.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"mark_manifest_deployed: {manifest_path} does not exist - "
            "run microsite_builder.build_microsite() first."
        )
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"mark_manifest_deployed: manifest is not JSON ({e})") from e
    if not isinstance(data, dict):
        raise ValueError("mark_manifest_deployed: manifest root must be an object")

    data["status"] = "deployed"
    data["deployment_provider"] = provider
    data["deployed_at"] = datetime.now(timezone.utc).isoformat()
    data["public_url"] = public_url
    if deployment_url:
        data["deployment_url"] = deployment_url

    manifest_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    return data
