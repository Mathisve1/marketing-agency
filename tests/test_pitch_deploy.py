"""Tests for the Cloudflare Pages deploy automation.

Covers:
  - URL composition uses the free `.pages.dev` base by default
  - PITCH_BASE_URL env var override + custom domain support
  - wrangler command line uses the project name flag
  - --dry-run never calls subprocess.run
  - API token is never printed (stdout/stderr capture check)
  - missing env vars exit non-zero with a clear message
  - successful (mocked) deploy updates the manifest
  - custom domain is optional, not required
  - Windows wrangler.cmd auto-resolution via shutil.which
  - subprocess.run uses utf-8 + errors=replace
  - deployment_url parser tolerates emoji / box-drawing output
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agents.outreach.reporting.microsite_builder import (
    DEFAULT_PUBLIC_BASE_URL,
    PITCH_BASE_URL_ENV,
    build_microsite,
    resolve_public_base_url,
)
from scripts import deploy_pitch_microsite as cli
from services import pitch_deploy as pitch_deploy_module
from services.pitch_deploy import (
    REQUIRED_DEPLOY_ENV,
    WRANGLER_BIN_ENV,
    _parse_deployment_url,
    _resolve_wrangler_cmd,
    check_required_env,
    deploy_to_cloudflare_pages,
    mark_manifest_deployed,
)
from tests.test_html_deck_builder import _make_synthetic_brief

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _setup_prospect(tmp_path: Path, prospect_id: str = "acme-skincare"):
    prospect_root = tmp_path / "prospects" / prospect_id
    prospect_root.mkdir(parents=True, exist_ok=True)
    brief = _make_synthetic_brief(prospect_root)
    return prospect_root, brief


def _set_deploy_env(monkeypatch, *, project: str = "yuvo-pitches"):
    """Set the three required env vars to fake values for one test."""
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account-id-deadbeef")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "supersecret-token-DO-NOT-LEAK")
    monkeypatch.setenv("CLOUDFLARE_PAGES_PROJECT", project)


# --------------------------------------------------------------------------- #
# Base URL: .pages.dev default + env override
# --------------------------------------------------------------------------- #


def test_default_base_url_uses_pages_dev():
    """MVP default must be the free Cloudflare Pages domain."""
    assert DEFAULT_PUBLIC_BASE_URL == "https://yuvo-pitches.pages.dev"
    assert resolve_public_base_url() == "https://yuvo-pitches.pages.dev"


def test_generated_public_url_uses_pages_dev_base(tmp_path: Path):
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    assert manifest["public_url"].startswith("https://yuvo-pitches.pages.dev/p/")
    assert manifest["public_url"].endswith("/")
    assert manifest["private_slug"] in manifest["public_url"]


def test_pitch_base_url_env_override(monkeypatch, tmp_path: Path):
    """`PITCH_BASE_URL=...` env var swaps the base without code changes."""
    monkeypatch.setenv(PITCH_BASE_URL_ENV, "https://pitch.yuvostudio.com")
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    assert manifest["public_url"].startswith("https://pitch.yuvostudio.com/p/")


def test_custom_domain_is_optional_not_required(monkeypatch, tmp_path: Path):
    """When PITCH_BASE_URL is empty/unset, microsite build still succeeds and
    uses the free pages.dev domain - no custom domain configuration required.
    """
    monkeypatch.delenv(PITCH_BASE_URL_ENV, raising=False)
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    assert "yuvo-pitches.pages.dev" in manifest["public_url"]


def test_pitch_base_url_explicit_kwarg_overrides_env(monkeypatch, tmp_path: Path):
    """Explicit `public_base_url=` wins over the env var."""
    monkeypatch.setenv(PITCH_BASE_URL_ENV, "https://something.else")
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite(
        "acme-skincare",
        brief=brief,
        public_base_url="https://yuvo-pitches.pages.dev",
    )
    assert manifest["public_url"].startswith("https://yuvo-pitches.pages.dev/p/")


# --------------------------------------------------------------------------- #
# wrangler argv
# --------------------------------------------------------------------------- #


def test_deploy_command_uses_pages_project_name():
    """The wrangler argv must carry --project-name=<env var>."""
    result = deploy_to_cloudflare_pages(
        deploy_dir=Path("build/pitches"),
        project_name="yuvo-pitches",
        account_id="x",
        api_token="y",
        branch="main",
        dry_run=True,
    )
    assert "--project-name=yuvo-pitches" in result.argv
    assert "pages" in result.argv
    assert "deploy" in result.argv
    # Branch flag wired through.
    assert "--branch=main" in result.argv


def test_deploy_command_uses_custom_branch():
    result = deploy_to_cloudflare_pages(
        deploy_dir=Path("build/pitches"),
        project_name="yuvo-pitches",
        account_id="x",
        api_token="y",
        branch="staging",
        dry_run=True,
    )
    assert "--branch=staging" in result.argv


def test_deploy_argv_does_not_carry_secrets():
    """The command line we hand to subprocess must never contain the token."""
    result = deploy_to_cloudflare_pages(
        deploy_dir=Path("build/pitches"),
        project_name="yuvo-pitches",
        account_id="account-id-1234",
        api_token="token-secret-abcdef",
        dry_run=True,
    )
    flat = " ".join(result.argv)
    assert "token-secret-abcdef" not in flat
    assert "account-id-1234" not in flat


# --------------------------------------------------------------------------- #
# Dry-run does not call wrangler
# --------------------------------------------------------------------------- #


def test_dry_run_does_not_call_subprocess(monkeypatch):
    """Dry-run path must never spawn a wrangler process."""
    called: list = []

    def fake_run(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("subprocess.run should not be invoked in dry-run")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = deploy_to_cloudflare_pages(
        deploy_dir=Path("build/pitches"),
        project_name="yuvo-pitches",
        account_id="x",
        api_token="y",
        dry_run=True,
    )
    assert called == []
    assert result.ok is True
    assert result.dry_run is True


# --------------------------------------------------------------------------- #
# Env validation
# --------------------------------------------------------------------------- #


def test_required_env_list_is_three_vars():
    assert set(REQUIRED_DEPLOY_ENV) == {
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_PAGES_PROJECT",
    }


def test_check_required_env_returns_missing(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_PAGES_PROJECT", raising=False)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "x")
    missing = check_required_env()
    assert "CLOUDFLARE_API_TOKEN" in missing
    assert "CLOUDFLARE_PAGES_PROJECT" in missing
    assert "CLOUDFLARE_ACCOUNT_ID" not in missing


def test_cli_missing_env_vars_exit_clearly(monkeypatch, capsys):
    """Running the script without env vars must exit 2 with a clear message."""
    for v in REQUIRED_DEPLOY_ENV:
        monkeypatch.delenv(v, raising=False)
    # Also block .env auto-load so the test stays hermetic.
    monkeypatch.setattr(cli, "_load_dotenv_if_available", lambda: None)
    rc = cli.main(["someprospect", "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "missing required env vars" in captured.err.lower()
    for v in REQUIRED_DEPLOY_ENV:
        assert v in captured.err


# --------------------------------------------------------------------------- #
# CLI: API token never printed
# --------------------------------------------------------------------------- #


def test_cli_does_not_print_api_token(monkeypatch, capsys, tmp_path: Path):
    _set_deploy_env(monkeypatch)
    monkeypatch.setattr(cli, "_load_dotenv_if_available", lambda: None)

    # Stub out the build + deploy so the CLI can complete without doing
    # real work. We're just inspecting captured stdout/stderr for the token.
    fake_manifest = {
        "private_slug": "acme-skincare-aaaa11",
        "public_url": "https://yuvo-pitches.pages.dev/p/acme-skincare-aaaa11/",
        "assets_used": [],
    }
    monkeypatch.setattr(cli, "build_microsite", lambda *a, **kw: fake_manifest)
    monkeypatch.setattr(cli, "build_deploy_package", lambda *a, **kw: tmp_path / "deploy")
    # Make the deploy folder exist so the CLI doesn't bail early.
    (tmp_path / "build" / "pitches").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        cli,
        "deploy_to_cloudflare_pages",
        lambda **kw: type("R", (), {
            "ok": True, "dry_run": True,
            "argv": ["wrangler", "pages", "deploy", "build/pitches",
                     "--project-name=yuvo-pitches", "--branch=main"],
            "deployment_url": None,
            "message": "dry-run",
        })(),
    )

    rc = cli.main([
        "acme-skincare", "--dry-run",
        "--prospects-root", str(tmp_path / "prospects"),
        "--build-root", str(tmp_path / "build"),
    ])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # The fake token from _set_deploy_env must never appear.
    assert "supersecret-token-DO-NOT-LEAK" not in combined
    assert rc == 0


# --------------------------------------------------------------------------- #
# Mocked successful deploy updates the manifest
# --------------------------------------------------------------------------- #


def test_successful_mocked_deploy_updates_manifest(tmp_path: Path, monkeypatch):
    """A real deploy (not dry-run) flips the manifest to status=deployed."""
    prospect_root, brief = _setup_prospect(tmp_path)
    manifest = build_microsite(
        "acme-skincare",
        brief=brief,
        prospects_root=tmp_path / "prospects",
    )
    assert manifest["status"] == "draft"

    manifest_path = prospect_root / "site" / "manifest.json"
    updated = mark_manifest_deployed(
        manifest_path,
        public_url=manifest["public_url"],
        provider="cloudflare_pages",
        deployment_url="https://abc123.yuvo-pitches.pages.dev",
    )
    assert updated["status"] == "deployed"
    assert updated["deployment_provider"] == "cloudflare_pages"
    assert "deployed_at" in updated
    assert updated["deployment_url"] == "https://abc123.yuvo-pitches.pages.dev"
    # And the file on disk reflects the change.
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "deployed"


def test_mark_manifest_deployed_requires_existing_manifest(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        mark_manifest_deployed(
            tmp_path / "does-not-exist.json",
            public_url="https://example.com/p/x/",
        )


def test_cli_dry_run_does_not_update_manifest(monkeypatch, tmp_path: Path, capsys):
    """`--dry-run` must NOT flip the manifest status to deployed."""
    _set_deploy_env(monkeypatch)
    monkeypatch.setattr(cli, "_load_dotenv_if_available", lambda: None)
    prospect_root, brief = _setup_prospect(tmp_path)
    # Run a real local build so the manifest + deploy package exist.
    build_microsite("acme-skincare", brief=brief, prospects_root=tmp_path / "prospects")
    manifest_path = prospect_root / "site" / "manifest.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert before["status"] == "draft"

    # Make build/pitches exist so the CLI's deploy-folder check passes.
    (tmp_path / "build" / "pitches").mkdir(parents=True, exist_ok=True)

    # Stub deploy to "succeed" in dry-run shape; skip the rebuild step.
    monkeypatch.setattr(
        cli,
        "deploy_to_cloudflare_pages",
        lambda **kw: type("R", (), {
            "ok": True, "dry_run": True,
            "argv": ["wrangler", "pages", "deploy"],
            "deployment_url": None,
            "message": "dry-run",
        })(),
    )

    rc = cli.main([
        "acme-skincare", "--dry-run", "--skip-build",
        "--prospects-root", str(tmp_path / "prospects"),
        "--build-root", str(tmp_path / "build"),
    ])
    assert rc == 0
    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Dry-run must not flip status.
    assert after["status"] == "draft"


# --------------------------------------------------------------------------- #
# wrangler bin override + missing binary
# --------------------------------------------------------------------------- #


def test_wrangler_bin_env_override(monkeypatch):
    monkeypatch.setenv(WRANGLER_BIN_ENV, "my-wrangler")
    result = deploy_to_cloudflare_pages(
        deploy_dir=Path("build/pitches"),
        project_name="yuvo-pitches",
        account_id="x",
        api_token="y",
        dry_run=True,
    )
    assert result.argv[0] == "my-wrangler"


def test_missing_wrangler_fails_cleanly(monkeypatch):
    """When wrangler is not on PATH (real run), surface a clear message."""
    # Force shutil.which to report wrangler missing.
    monkeypatch.setattr("services.pitch_deploy.shutil.which", lambda *a, **kw: None)
    # Also force the file-exists fallback off by pointing at a fake bin.
    result = deploy_to_cloudflare_pages(
        deploy_dir=Path("build/pitches"),
        project_name="yuvo-pitches",
        account_id="x",
        api_token="y",
        dry_run=False,
        wrangler_cmd="not-a-real-wrangler",
    )
    assert result.ok is False
    assert "wrangler executable not found" in result.message.lower()


# --------------------------------------------------------------------------- #
# Windows wrangler.cmd auto-resolution
# --------------------------------------------------------------------------- #


def test_resolve_wrangler_uses_env_override_when_set(monkeypatch):
    """`WRANGLER_BIN` env var takes priority over the platform fallback."""
    monkeypatch.setenv(WRANGLER_BIN_ENV, "/explicit/path/wrangler.cmd")
    monkeypatch.setattr(pitch_deploy_module.sys, "platform", "win32")
    # shutil.which should NEVER be called when the env override wins,
    # but if it were called it would resolve a different path.
    monkeypatch.setattr(
        pitch_deploy_module.shutil,
        "which",
        lambda *a, **kw: "/somewhere/else/wrangler.cmd",
    )
    assert _resolve_wrangler_cmd() == "/explicit/path/wrangler.cmd"


def test_resolve_wrangler_autoresolves_on_windows_when_unset(monkeypatch):
    """On Windows, with WRANGLER_BIN unset, fall back to shutil.which so the
    absolute path of wrangler.cmd (PATHEXT-resolved) is what subprocess
    actually receives. Without this, CreateProcess on the bare string
    "wrangler" fails with WinError 2."""
    monkeypatch.delenv(WRANGLER_BIN_ENV, raising=False)
    monkeypatch.setattr(pitch_deploy_module.sys, "platform", "win32")
    monkeypatch.setattr(
        pitch_deploy_module.shutil,
        "which",
        lambda name: "C:/Users/op/AppData/Roaming/npm/wrangler.cmd"
        if name == "wrangler"
        else None,
    )
    assert _resolve_wrangler_cmd() == "C:/Users/op/AppData/Roaming/npm/wrangler.cmd"


def test_resolve_wrangler_windows_falls_back_when_which_returns_none(monkeypatch):
    """On Windows when shutil.which can't find wrangler at all, the deploy
    keeps the bare string so the downstream existence check produces a
    clear "wrangler executable not found" error rather than crashing
    inside subprocess."""
    monkeypatch.delenv(WRANGLER_BIN_ENV, raising=False)
    monkeypatch.setattr(pitch_deploy_module.sys, "platform", "win32")
    monkeypatch.setattr(pitch_deploy_module.shutil, "which", lambda *a, **kw: None)
    assert _resolve_wrangler_cmd() == "wrangler"


def test_resolve_wrangler_posix_does_not_call_which(monkeypatch):
    """On POSIX the shell shim is directly executable, so the bare
    `wrangler` works in subprocess. We deliberately do NOT call
    shutil.which because doing so would surface the shim's absolute
    path and is unnecessary on this platform."""
    monkeypatch.delenv(WRANGLER_BIN_ENV, raising=False)
    monkeypatch.setattr(pitch_deploy_module.sys, "platform", "linux")
    called: list = []
    monkeypatch.setattr(
        pitch_deploy_module.shutil,
        "which",
        lambda *a, **kw: called.append(a) or "/usr/local/bin/wrangler",
    )
    assert _resolve_wrangler_cmd() == "wrangler"
    assert called == []


# --------------------------------------------------------------------------- #
# subprocess.run uses utf-8 + errors=replace
# --------------------------------------------------------------------------- #


def test_subprocess_uses_utf8_decoding_with_replace_errors(monkeypatch, tmp_path: Path):
    """Wrangler emits UTF-8 box-drawing chars and emoji in its progress
    output. Without `encoding="utf-8", errors="replace"`, Python falls
    back to the platform default (cp1252 on Windows) and the stdout
    reader thread raises UnicodeDecodeError, corrupting the capture
    even when wrangler itself returns 0. This test pins the safe
    decoding contract on `subprocess.run`."""

    captured: dict = {}

    class _FakeCompleted:
        returncode = 0
        stdout = "ok\nhttps://abc123.yuvo-pitches.pages.dev\n"
        stderr = ""

    def fake_run(argv, *, env, capture_output, text, encoding, errors, check):
        captured["argv"] = argv
        captured["encoding"] = encoding
        captured["errors"] = errors
        captured["text"] = text
        captured["capture_output"] = capture_output
        captured["check"] = check
        return _FakeCompleted()

    monkeypatch.setattr(pitch_deploy_module.subprocess, "run", fake_run)
    # Make the wrangler-existence check pass without touching the FS.
    monkeypatch.setattr(pitch_deploy_module.shutil, "which", lambda *a, **kw: "wrangler")

    result = deploy_to_cloudflare_pages(
        deploy_dir=tmp_path / "build" / "pitches",
        project_name="yuvo-pitches",
        account_id="x",
        api_token="y",
        dry_run=False,
        wrangler_cmd="wrangler",
    )
    assert result.ok is True
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["text"] is True


def test_parse_deployment_url_tolerates_box_drawing_and_emoji():
    """When wrangler stdout contains box-drawing / emoji that survive as
    U+FFFD (replacement char) under errors='replace', the URL parser
    still finds the pages.dev URL on the line that has one."""
    raw = (
        # First line: classic wrangler progress, riddled with box-drawing
        # which Python turned into the replacement char after the
        # errors='replace' decode.
        "�� � Uploading 12 files (deployment in progress)\n"
        "✨ Deployment complete!\n"
        "https://abc1234.yuvo-pitches.pages.dev\n"
        "✅ yuvo-pitches.pages.dev"
    )
    url = _parse_deployment_url(raw)
    assert url == "https://abc1234.yuvo-pitches.pages.dev"


def test_parse_deployment_url_returns_none_on_pure_garbage():
    """No pages.dev token anywhere -> None, no exception."""
    assert _parse_deployment_url("") is None
    assert _parse_deployment_url("��� no urls here �") is None
    assert _parse_deployment_url("https://example.com just a non-pages url") is None


# --------------------------------------------------------------------------- #
# Real deploy renders Live banner before upload; dry-run keeps Draft
# --------------------------------------------------------------------------- #


def test_real_deploy_passes_intended_status_deployed(monkeypatch, tmp_path: Path, capsys):
    """The deploy script must call `build_microsite(intended_status="deployed")`
    on a real (non-dry-run) deploy so the HTML uploaded to Cloudflare
    already carries the Live banner. Without this, the first deploy of
    a fresh prospect ships the amber Draft banner and a second deploy
    is needed to fix it."""
    _set_deploy_env(monkeypatch)
    monkeypatch.setattr(cli, "_load_dotenv_if_available", lambda: None)
    prospect_root, brief = _setup_prospect(tmp_path)

    seen: dict = {}

    def fake_build_microsite(prospect_id, **kwargs):
        seen["prospect_id"] = prospect_id
        seen["intended_status"] = kwargs.get("intended_status")
        return {
            "private_slug": f"{prospect_id}-aaaa11",
            "public_url": f"https://yuvo-pitches.pages.dev/p/{prospect_id}-aaaa11/",
            "assets_used": [],
        }

    monkeypatch.setattr(cli, "build_microsite", fake_build_microsite)
    monkeypatch.setattr(cli, "build_deploy_package", lambda *a, **kw: tmp_path / "deploy")
    (tmp_path / "build" / "pitches").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        cli,
        "deploy_to_cloudflare_pages",
        lambda **kw: type("R", (), {
            "ok": True, "dry_run": False,
            "argv": ["wrangler", "pages", "deploy"],
            "deployment_url": "https://abc.yuvo-pitches.pages.dev",
            "message": "deploy ok",
        })(),
    )
    # Stub mark_manifest_deployed so the test doesn't need a real
    # manifest on disk for this assertion.
    monkeypatch.setattr(
        cli,
        "mark_manifest_deployed",
        lambda *a, **kw: {
            "status": "deployed",
            "deployment_provider": "cloudflare_pages",
            "deployed_at": "2026-05-15T11:43:07.396851+00:00",
            "deployment_url": "https://abc.yuvo-pitches.pages.dev",
            "public_url": kw.get("public_url"),
        },
    )

    rc = cli.main([
        "acme-skincare",
        "--prospects-root", str(tmp_path / "prospects"),
        "--build-root", str(tmp_path / "build"),
    ])
    assert rc == 0
    assert seen["intended_status"] == "deployed"
    assert seen["prospect_id"] == "acme-skincare"


def test_dry_run_does_not_pass_intended_status_deployed(monkeypatch, tmp_path: Path):
    """Dry-run must NOT force the Live banner - the local preview that
    operators eyeball before deploying should still say Draft."""
    _set_deploy_env(monkeypatch)
    monkeypatch.setattr(cli, "_load_dotenv_if_available", lambda: None)
    _setup_prospect(tmp_path)

    seen: dict = {}

    def fake_build_microsite(prospect_id, **kwargs):
        seen["intended_status"] = kwargs.get("intended_status")
        return {
            "private_slug": f"{prospect_id}-aaaa11",
            "public_url": f"https://yuvo-pitches.pages.dev/p/{prospect_id}-aaaa11/",
            "assets_used": [],
        }

    monkeypatch.setattr(cli, "build_microsite", fake_build_microsite)
    monkeypatch.setattr(cli, "build_deploy_package", lambda *a, **kw: tmp_path / "deploy")
    (tmp_path / "build" / "pitches").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        cli,
        "deploy_to_cloudflare_pages",
        lambda **kw: type("R", (), {
            "ok": True, "dry_run": True,
            "argv": ["wrangler", "pages", "deploy"],
            "deployment_url": None,
            "message": "dry-run",
        })(),
    )

    rc = cli.main([
        "acme-skincare", "--dry-run",
        "--prospects-root", str(tmp_path / "prospects"),
        "--build-root", str(tmp_path / "build"),
    ])
    assert rc == 0
    # The override must NOT be "deployed" on dry-run; either None
    # (current implementation) or "draft" is acceptable.
    assert seen["intended_status"] != "deployed"


# --------------------------------------------------------------------------- #
# No public-index hygiene fence stays intact
# --------------------------------------------------------------------------- #


def test_no_public_prospect_index_after_deploy_automation(monkeypatch):
    """Adding deploy automation must not introduce a public prospect index."""
    repo_root = Path(__file__).resolve().parents[1]
    for relative in (
        "prospects/index.html",
        "build/pitches/index.html",
        "build/pitches/p/index.html",
    ):
        candidate = repo_root / relative
        assert not candidate.exists(), (
            f"refusing to ship a tracked public prospect index at {relative}"
        )
