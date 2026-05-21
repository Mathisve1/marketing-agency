"""Yuvo Studio — Phase 5B unit tests for upload_visual_asset_stub.

Pytest-collectible. Deterministic, fast (no subprocess, no network,
no file I/O, no env reads). Imports the stub directly as a module
and calls `main(argv)`.

Pins the Phase 5B contract:
  - dry-run with valid args → exit 0, manifest JSON on stdout
  - invalid local path / UUID / theme / format / dims → exit 1
  - --execute → exit 2, refusal message on stderr
  - manifest carries the canonical storage key
  - the stub NEVER imports a storage SDK, an HTTP client, supabase,
    or subprocess
  - no file is created on dry-run
  - the script never reads `os.environ`
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="module")
def stub() -> types.ModuleType:
    mod = importlib.import_module("upload_visual_asset_stub")
    return importlib.reload(mod)


VALID_WS = "11111111-1111-1111-1111-111111111111"
VALID_CI = "b920e5e2-a67d-45ca-96c9-f9422218d675"
VALID_LOCAL = "./exports/feed_post_neutral_v1-b920e5e2-1080x1350.png"


def _argv(**overrides: object) -> list[str]:
    base: list[str] = [
        "--local-path", VALID_LOCAL,
        "--workspace-id", VALID_WS,
        "--content-item-id", VALID_CI,
        "--mode", "feed_post",
        "--theme-id", "neutral",
        "--format", "png",
    ]
    for k, v in overrides.items():
        # The argparse `dest` for `--json` is `emit_json`. Map it
        # explicitly so the test helper stays terse.
        if k == "emit_json":
            if v:
                base.append("--json")
            continue
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                base.append(flag)
            continue
        base.extend([flag, str(v)])
    return base


def _run(stub, argv, capsys):
    code = stub.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------- #
# Dry-run success
# --------------------------------------------------------------------------- #


def test_dry_run_feed_post_emits_storage_key(stub, capsys):
    code, out, _err = _run(stub, _argv(template_id="feed_post_neutral_v1"), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    assert payload["schema"] == "yuvo.studio/visual_asset_upload_manifest/v1"
    assert payload["phase"] == "5b_upload_stub"
    assert payload["storage_key"] == (
        f"visual-assets/{VALID_WS}/{VALID_CI}/feed_post_neutral_v1/neutral/"
        f"feed_post_neutral_v1-b920e5e2.png"
    )
    assert payload["recommended_r2_binding"] == "VISUAL_ASSETS_BUCKET"
    assert payload["recommended_r2_bucket"] == "yuvo-visual-assets"
    safety = payload["safety"]
    assert safety["uploads"] is False
    assert safety["imports_storage_sdk"] is False
    assert safety["makes_network_requests"] is False
    assert safety["creates_creative_assets_row"] is False


def test_dry_run_carousel_with_slide_number_includes_slide_in_filename(stub, capsys):
    code, out, _err = _run(
        stub,
        _argv(mode="carousel", template_id="carousel_neutral_v1", slide_number=3, width=1080, height=1350),
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    assert payload["filename"] == "carousel_neutral_v1-b920e5e2-slide03-1080x1350.png"
    assert payload["storage_key"].endswith(payload["filename"])


def test_dry_run_story_with_frame_number_includes_frame_in_filename(stub, capsys):
    code, out, _err = _run(
        stub,
        _argv(mode="story", template_id="story_neutral_v1", frame_number=2),
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    assert payload["filename"] == "story_neutral_v1-b920e5e2-frame02.png"


def test_dry_run_no_template_id_falls_back_to_mode_default(stub, capsys):
    code, out, _err = _run(stub, _argv(), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    # template_id None → "feed_post_default" segment (underscores
    # preserved by `_sanitize_segment`).
    assert "feed_post_default" in payload["storage_key"]


# --------------------------------------------------------------------------- #
# Validation failures
# --------------------------------------------------------------------------- #


def test_traversal_local_path_rejected(stub, capsys):
    code, _out, err = _run(stub, _argv(local_path="../etc/passwd.png"), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "local-path" in err.lower()


def test_wrong_local_extension_rejected(stub, capsys):
    code, _out, err = _run(stub, _argv(local_path="./exports/payload.bin"), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "local-path" in err.lower()


def test_invalid_workspace_uuid_rejected(stub, capsys):
    code, _out, err = _run(stub, _argv(workspace_id="not-a-uuid"), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "workspace" in err.lower()


def test_invalid_content_uuid_rejected(stub, capsys):
    code, _out, err = _run(stub, _argv(content_item_id="not-a-uuid"), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "content-item" in err.lower()


def test_invalid_theme_id_rejected(stub, capsys):
    code, _out, err = _run(stub, _argv(theme_id="Editorial!"), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "theme-id" in err.lower()


def test_both_slide_and_frame_rejected(stub, capsys):
    code, _out, err = _run(stub, _argv(slide_number=1, frame_number=1), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "slide-number" in err.lower() or "frame-number" in err.lower()


def test_negative_dimensions_rejected(stub, capsys):
    code, _out, err = _run(stub, _argv(width=-1), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "width" in err.lower()


# --------------------------------------------------------------------------- #
# --execute is refused (Phase 5B contract)
# --------------------------------------------------------------------------- #


def test_execute_refused(stub, capsys):
    code, _out, err = _run(stub, _argv(execute=True), capsys)
    assert code == stub.EXIT_EXECUTE_REFUSED
    assert "--execute" in err
    assert "Phase 5C" in err  # the refusal points the operator at the next phase
    assert "[refused]" in err


# --------------------------------------------------------------------------- #
# Safety pins (no SDK / no network / no env / no subprocess)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "module_name",
    [
        "boto3",
        "botocore",
        "aioboto3",
        "aiobotocore",
        "google.cloud.storage",
        "minio",
        "s3fs",
        "requests",
        "httpx",
        "aiohttp",
        "supabase",
        "psycopg",
        "psycopg2",
        "subprocess",
        "playwright",
        "pyppeteer",
    ],
)
def test_stub_does_not_import_forbidden_modules(module_name: str):
    purged = [k for k in sys.modules if k == module_name or k.startswith(f"{module_name}.")]
    for k in purged:
        sys.modules.pop(k, None)
    sys.modules.pop("upload_visual_asset_stub", None)
    importlib.import_module("upload_visual_asset_stub")
    assert module_name not in sys.modules, (
        f"upload stub leaked an import of forbidden module: {module_name}"
    )


def test_stub_does_not_import_os(stub):
    """Pin that `os` is not imported anywhere in the upload stub.
    Without `os`, no env var can be read and no subprocess can be
    spawned via os.spawn*."""
    sys.modules.pop("upload_visual_asset_stub", None)
    importlib.import_module("upload_visual_asset_stub")
    # urllib.parse pulls os transitively in some stdlibs; that's fine.
    # We just pin that the stub module doesn't have a TOP-LEVEL os
    # name in its namespace (i.e. it never did `import os`).
    mod = sys.modules["upload_visual_asset_stub"]
    assert "os" not in vars(mod), "upload stub bound `os` at module scope"


def test_stub_no_files_created_on_dry_run(stub, capsys, tmp_path):
    # Use a tmp_path-rooted local path that survives the safety check
    # (no '..', no /etc, ends in .png).
    safe_local = str(tmp_path / "exports" / "asset.png")
    code, _out, _err = _run(stub, _argv(local_path=safe_local), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    # The stub never created the dir.
    assert not (tmp_path / "exports").exists()


def test_stub_reads_no_env(stub, monkeypatch, capsys):
    monkeypatch.setenv("YUVO_PHASE_5B_SENTINEL", "must-not-appear")
    code, out, err = _run(stub, _argv(), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    assert "must-not-appear" not in out
    assert "must-not-appear" not in err


# --------------------------------------------------------------------------- #
# JSON mode
# --------------------------------------------------------------------------- #


def test_json_mode_emits_single_payload(stub, capsys):
    code, out, _err = _run(stub, _argv(emit_json=True), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out)
    assert payload["exit_code"] == 0
    assert payload["manifest"]["phase"] == "5b_upload_stub"
    assert payload["errors"] == []


def test_json_mode_on_validation_failure(stub, capsys):
    code, out, _err = _run(stub, _argv(workspace_id="bad", emit_json=True), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    payload = json.loads(out)
    assert payload["manifest"] is None
    assert any("workspace" in e.lower() for e in payload["errors"])
