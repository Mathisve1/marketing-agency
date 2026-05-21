"""Yuvo Studio — Phase 4G unit tests for export_visual_preview_stub.

Pytest-collectible. Deterministic, fast (no subprocess, no network,
no file I/O, no env reads). Imports the stub as a Python module and
calls `main(argv)` directly.

Validates the Phase 4G CLI contract:
  - dry-run with valid args → exit 0, manifest JSON on stdout
  - invalid UUID / format / URL / dimensions / theme id → exit 1
  - --execute → exit 2, refusal message on stderr
  - --slide-number AND --frame-number → exit 1
  - the stub never imports puppeteer / playwright / chromium /
    requests / httpx (proven by an importlib check)
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

# Make the scripts/ directory importable as if it were a package root.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="module")
def stub() -> types.ModuleType:
    # Import (or re-import) the stub fresh so module-level constants
    # are exercised inside the test's coverage window.
    mod = importlib.import_module("export_visual_preview_stub")
    return importlib.reload(mod)


VALID_UUID = "b920e5e2-a67d-45ca-96c9-f9422218d675"
VALID_REL_URL = (
    "/agency/creative-briefs/b920e5e2-a67d-45ca-96c9-f9422218d675/preview"
    "?template=feed_post_editorial_v1&theme=editorial"
)
VALID_LOCALHOST_URL = (
    "http://localhost:3000/agency/creative-briefs/"
    "b920e5e2-a67d-45ca-96c9-f9422218d675/preview"
)


def _base_argv(**overrides: object) -> list[str]:
    """Build a minimal valid argv with optional overrides."""
    args: list[str] = [
        "--content-item-id",
        VALID_UUID,
        "--preview-url",
        VALID_REL_URL,
        "--mode",
        "feed_post",
        "--theme-id",
        "editorial",
        "--format",
        "png",
        "--output-dir",
        "./exports",
    ]
    for k, v in overrides.items():
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                args.append(flag)
            continue
        args.extend([flag, str(v)])
    return args


def _run(stub: types.ModuleType, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = stub.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ------------------------------------------------------------------ #
# Dry-run (success) paths
# ------------------------------------------------------------------ #


def test_dry_run_relative_url_exits_ok(stub, capsys):
    code, out, err = _run(stub, _base_argv(), capsys)
    assert code == stub.EXIT_OK_DRY_RUN, err
    payload = json.loads(out.split("\n\n")[0])
    # Phase 5A re-tagged the manifest phase to advertise the new
    # gated real-branch dispatch. Pin both `v1` and the phase tag so
    # future drift surfaces clearly.
    assert payload["schema"] == "yuvo.studio/visual_export_manifest/v1"
    assert payload["phase"] == "5a_local_exporter_scaffold"
    assert payload["content_item_id"] == VALID_UUID
    assert payload["preview_url"] == VALID_REL_URL
    assert payload["mode"] == "feed_post"
    assert payload["format"] == "png"
    assert payload["dry_run"] is True
    assert payload["would_execute"] is False
    # Phase 5A — the manifest now reflects detection state. With the
    # PERMITTED-list double-gate (currently empty), the status is
    # always `dependency_missing` even on dev machines that already
    # have Playwright installed.
    assert payload["real_export_status"] == "dependency_missing"
    assert payload["next_phase"] == "phase_5a_followup_real_screenshot_impl"
    safety = payload["safety"]
    assert safety["imports_browser_automation"] is False
    assert safety["makes_network_requests"] is False
    assert safety["writes_files"] is False
    assert safety["reads_env"] is False
    assert safety["spawns_subprocess"] is False
    assert "[dry-run] no files written" in out


def test_dry_run_localhost_absolute_url_exits_ok(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv() + ["--preview-url", VALID_LOCALHOST_URL],
        capsys,
    )
    # argparse keeps only the last --preview-url; verify directly.
    assert code == stub.EXIT_OK_DRY_RUN, err


def test_dry_run_with_slide_number_emits_filename(stub, capsys):
    code, out, _err = _run(stub, _base_argv(slide_number=3), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    assert payload["slide_number"] == 3
    assert payload["filename_suggestion"].endswith("-slide03.png")


def test_dry_run_with_frame_number_emits_filename(stub, capsys):
    code, out, _err = _run(stub, _base_argv(frame_number=2, mode="story"), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    assert payload["frame_number"] == 2
    assert payload["filename_suggestion"].endswith("-frame02.png")


# ------------------------------------------------------------------ #
# Validation failures
# ------------------------------------------------------------------ #


def test_invalid_uuid_fails(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv() + ["--content-item-id", "not-a-uuid"],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "content-item-id" in err.lower()


def test_invalid_format_rejected_by_argparse(stub, capsys):
    # argparse raises SystemExit(2) for invalid choices; main() maps
    # that to its own exit. We just assert non-zero exit + the format
    # complaint surfaces.
    code, _out, err = _run(
        stub,
        _base_argv() + ["--format", "webp"],
        capsys,
    )
    assert code != stub.EXIT_OK_DRY_RUN
    assert "format" in err.lower()


def test_invalid_preview_url_fails(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv() + ["--preview-url", "https://evil.example.com/x"],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "preview-url" in err.lower()


def test_invalid_dimensions_fail(stub, capsys):
    code, _out, err = _run(stub, _base_argv(width=-1), capsys)
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "width" in err.lower()


def test_both_slide_and_frame_fail(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv(slide_number=1, frame_number=1),
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "slide-number" in err.lower() or "frame-number" in err.lower()


def test_traversal_output_dir_fails(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv() + ["--output-dir", "../../etc"],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "output-dir" in err.lower()


def test_invalid_theme_id_fails(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv() + ["--theme-id", "Editorial!"],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "theme-id" in err.lower()


# ------------------------------------------------------------------ #
# --execute is refused
# ------------------------------------------------------------------ #


def test_execute_refused_with_validation_pass(stub, capsys):
    """Phase 5A — `--execute` without the confirmation phrase still
    exits 2 (`EXIT_EXECUTE_REFUSED`). The refusal message now points
    the operator at `--confirm-local-export`."""
    code, _out, err = _run(stub, _base_argv() + ["--execute"], capsys)
    assert code == stub.EXIT_EXECUTE_REFUSED
    assert "--execute" in err
    assert "--confirm-local-export" in err
    assert "[refused]" in err


# ------------------------------------------------------------------ #
# Safety: stub must not import browser automation / network libs
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    "module_name",
    [
        "puppeteer",
        "pyppeteer",
        "playwright",
        "playwright.sync_api",
        "playwright.async_api",
        "selenium",
        "requests",
        "httpx",
        "aiohttp",
    ],
)
def test_stub_does_not_import_forbidden_modules(module_name: str):
    # Strip any pre-import so we test the stub's own import graph.
    purged = [k for k in sys.modules if k == module_name or k.startswith(f"{module_name}.")]
    for k in purged:
        sys.modules.pop(k, None)
    sys.modules.pop("export_visual_preview_stub", None)
    importlib.import_module("export_visual_preview_stub")
    assert module_name not in sys.modules, (
        f"stub leaked an import of forbidden module: {module_name}"
    )


def test_stub_no_files_created_on_dry_run(stub, capsys, tmp_path):
    # Use tmp_path as the suggested output-dir; the stub must NOT create
    # it. We assert that tmp_path is empty before and after.
    assert list(tmp_path.iterdir()) == []
    code, _out, _err = _run(
        stub,
        _base_argv() + ["--output-dir", str(tmp_path / "exports")],
        capsys,
    )
    # Note: a traversal check rejects ".." or absolute /etc /root paths
    # but `tmp_path` is a benign absolute path under the OS tmp dir.
    # The stub still must not touch it.
    assert code == stub.EXIT_OK_DRY_RUN, _err
    assert list(tmp_path.iterdir()) == []


def test_stub_reads_no_env(stub, monkeypatch, capsys):
    # Set a sentinel env var; the stub must not surface it in its output.
    monkeypatch.setenv("YUVO_PHASE_4G_SENTINEL", "must-not-appear")
    code, out, err = _run(stub, _base_argv(), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    assert "must-not-appear" not in out
    assert "must-not-appear" not in err


# =========================================================================== #
# Phase 4H additions
# =========================================================================== #


VALID_DASHBOARD_URL = (
    "https://yuvo-dashboard.example-account.workers.dev/agency/"
    "creative-briefs/b920e5e2-a67d-45ca-96c9-f9422218d675/preview"
    "?template=feed_post_neutral_v1&theme=neutral"
)
VALID_PAGES_DEV_URL = (
    "https://yuvo-pitches.pages.dev/agency/creative-briefs/"
    "b920e5e2-a67d-45ca-96c9-f9422218d675/preview"
)
EXTERNAL_HOSTILE_URL = (
    "https://attacker.example.com/agency/creative-briefs/"
    "b920e5e2-a67d-45ca-96c9-f9422218d675/preview"
)


# --- planned_output_path / manifest schema ---


def test_planned_output_path_in_manifest(stub, capsys):
    code, out, _err = _run(stub, _base_argv(), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    assert "planned_output_path" in payload
    assert payload["planned_output_path"].endswith(".png")
    assert payload["planned_output_path"].startswith("./exports/")
    assert payload["schema"] == "yuvo.studio/visual_export_manifest/v1"
    assert payload["phase"] == "5a_local_exporter_scaffold"


def test_planned_output_path_combines_outputdir_and_filename(stub, capsys):
    code, out, _err = _run(
        stub,
        _base_argv() + ["--output-dir", "./out/yuvo"],
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    assert payload["output_dir"] == "./out/yuvo"
    assert payload["planned_output_path"].startswith("./out/yuvo/")
    assert payload["planned_output_path"].endswith(
        payload["filename_suggestion"]
    )


# --- --json mode ---


def test_json_mode_emits_single_payload(stub, capsys):
    code, out, _err = _run(stub, _base_argv() + ["--json"], capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out)  # the whole stdout is one JSON object
    assert payload["exit_code"] == 0
    assert payload["manifest"]["content_item_id"] == VALID_UUID
    assert payload["errors"] == []


def test_json_mode_does_not_emit_prose_footer(stub, capsys):
    code, out, _err = _run(stub, _base_argv() + ["--json"], capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    assert "[dry-run] no files written" not in out


def test_json_mode_on_validation_failure(stub, capsys):
    code, out, _err = _run(
        stub,
        _base_argv() + ["--json", "--content-item-id", "not-a-uuid"],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    payload = json.loads(out)
    assert payload["exit_code"] == 1
    assert payload["manifest"] is None
    assert any("content-item-id" in e.lower() for e in payload["errors"])


# --- URL allow / reject ---


def test_yuvo_dashboard_workers_dev_url_accepted(stub, capsys):
    code, _out, _err = _run(
        stub,
        _base_argv() + ["--preview-url", VALID_DASHBOARD_URL],
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN


def test_pages_dev_url_accepted(stub, capsys):
    code, _out, _err = _run(
        stub,
        _base_argv() + ["--preview-url", VALID_PAGES_DEV_URL],
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN


def test_external_host_rejected(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv() + ["--preview-url", EXTERNAL_HOSTILE_URL],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "preview-url" in err.lower()


def test_dashboard_url_with_wrong_path_rejected(stub, capsys):
    """A dashboard-shaped URL pointing to a non-preview path must be
    rejected — we don't want to aim the future exporter at /api or
    /client/* by accident."""
    code, _out, err = _run(
        stub,
        _base_argv() + [
            "--preview-url",
            "https://yuvo-dashboard.example-account.workers.dev/api/some-thing",
        ],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "preview-url" in err.lower()


# --- --html-snapshot-path ---


def test_html_snapshot_path_accepted_in_dry_run(stub, capsys):
    code, out, _err = _run(
        stub,
        _base_argv() + ["--html-snapshot-path", "./snapshots/yana-feed.html"],
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    assert payload["html_snapshot_path"] == "./snapshots/yana-feed.html"


def test_html_snapshot_path_invalid_extension_rejected(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv() + ["--html-snapshot-path", "./snapshots/yana-feed.txt"],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "html-snapshot-path" in err.lower()


def test_html_snapshot_path_traversal_rejected(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv()
        + ["--html-snapshot-path", "../../etc/passwd.html"],
        capsys,
    )
    assert code == stub.EXIT_VALIDATION_FAILED
    assert "html-snapshot-path" in err.lower()


def test_html_snapshot_dry_run_creates_no_file(stub, capsys, tmp_path):
    target = tmp_path / "yana-feed.html"
    # Pass a relative path string (the validator only inspects the
    # text). The stub must not interpret it as a side effect.
    code, _out, _err = _run(
        stub,
        _base_argv() + ["--html-snapshot-path", "./snapshots/yana-feed.html"],
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


# --- dataclasses (ExportRequest / ExportResult) ---


def test_validate_request_returns_export_request(stub):
    ns = stub.parse_args(_base_argv())
    req, errors = stub.validate_request(ns)
    assert errors == []
    assert req is not None
    assert req.content_item_id == VALID_UUID
    assert req.dry_run is True
    assert req.execute is False
    assert req.emit_json is False


def test_build_plan_is_pure(stub):
    ns = stub.parse_args(_base_argv())
    req, _ = stub.validate_request(ns)
    plan_a = stub.build_plan(req)
    plan_b = stub.build_plan(req)
    assert plan_a == plan_b  # deterministic
    assert plan_a["safety"]["imports_browser_automation"] is False
    assert plan_a["safety"]["creates_generated_assets_row"] is False


def test_future_export_with_browser_refuses(stub):
    ns = stub.parse_args(_base_argv())
    req, _ = stub.validate_request(ns)
    result = stub.future_export_with_browser(req)
    assert result.exit_code == stub.EXIT_NOT_IMPLEMENTED
    assert result.errors
    assert "placeholder" in result.errors[0].lower()


# --- forbidden imports: no urllib.request, no env reads, … ---


@pytest.mark.parametrize(
    "module_name",
    [
        "puppeteer",
        "pyppeteer",
        "playwright",
        "playwright.sync_api",
        "playwright.async_api",
        "selenium",
        "requests",
        "httpx",
        "aiohttp",
        "subprocess",
    ],
)
def test_phase_4h_stub_does_not_import_forbidden_modules(module_name: str):
    # Drop any preceding state so we test the stub's own import graph.
    purged = [k for k in sys.modules if k == module_name or k.startswith(f"{module_name}.")]
    for k in purged:
        sys.modules.pop(k, None)
    sys.modules.pop("export_visual_preview_stub", None)
    importlib.import_module("export_visual_preview_stub")
    assert module_name not in sys.modules, (
        f"stub leaked an import of forbidden module: {module_name}"
    )


def test_phase_4h_stub_does_not_import_urllib_request():
    """urllib.parse is allowed (for parsing the preview URL).
    urllib.request is the network-I/O side and must stay out of the
    stub's import graph. We purge any pre-existing reference (pytest
    or other deps may have loaded urllib.request long before this
    test ran) and then reload the stub to inspect its own surface."""
    purged = [
        k
        for k in list(sys.modules)
        if k == "urllib.request" or k.startswith("urllib.request.")
    ]
    for k in purged:
        sys.modules.pop(k, None)
    sys.modules.pop("export_visual_preview_stub", None)
    importlib.import_module("export_visual_preview_stub")
    assert "urllib.request" not in sys.modules, (
        "stub leaked an import of urllib.request — that surface would "
        "let future drift add a network call without changing the "
        "visible import line."
    )


# =========================================================================== #
# Phase 5A additions
# =========================================================================== #


CORRECT_CONFIRMATION_PHRASE = "I UNDERSTAND THIS CREATES A LOCAL FILE ONLY"
WRONG_CONFIRMATION_PHRASE = "i understand this creates a local file only"  # case-sensitive


def test_phase_5a_constants_present(stub):
    """Pin the new sentinel exit code + confirmation phrase + runtime
    list so accidental drift in the contract surfaces clearly."""
    assert stub.EXIT_DEPENDENCY_MISSING == 4
    assert stub.LOCAL_EXPORT_CONFIRMATION_PHRASE == CORRECT_CONFIRMATION_PHRASE
    assert isinstance(stub.APPROVED_BROWSER_RUNTIMES, tuple)
    assert "playwright.sync_api" in stub.APPROVED_BROWSER_RUNTIMES
    assert "pyppeteer" in stub.APPROVED_BROWSER_RUNTIMES


def test_execute_without_confirmation_refused(stub, capsys):
    code, out, err = _run(stub, _base_argv() + ["--execute"], capsys)
    assert code == stub.EXIT_EXECUTE_REFUSED
    assert "--confirm-local-export" in err
    # Manifest is still emitted on stdout; pull the runtime block and
    # confirm no real-branch side effects.
    payload = json.loads(out.split("\n\n")[0])
    rt = payload["runtime"]
    assert rt["execute_requested"] is True
    assert rt["confirmation_phrase_passed"] is False
    assert rt["confirmation_phrase_matches"] is False
    assert rt["real_export_attempted"] is False


def test_execute_with_wrong_confirmation_refused(stub, capsys):
    code, _out, err = _run(
        stub,
        _base_argv() + ["--execute", "--confirm-local-export", WRONG_CONFIRMATION_PHRASE],
        capsys,
    )
    assert code == stub.EXIT_EXECUTE_REFUSED, err
    assert "--confirm-local-export" in err


def test_execute_with_correct_confirmation_no_runtime_exits_dependency_missing(
    stub, capsys, monkeypatch
):
    """Force `_detect_browser_runtime` to return None so the test is
    deterministic even if a runtime is later installed on the dev
    machine. Asserts the new EXIT_DEPENDENCY_MISSING path."""
    monkeypatch.setattr(stub, "_detect_browser_runtime", lambda: None)
    code, out, err = _run(
        stub,
        _base_argv() + [
            "--execute",
            "--confirm-local-export",
            CORRECT_CONFIRMATION_PHRASE,
        ],
        capsys,
    )
    assert code == stub.EXIT_DEPENDENCY_MISSING, err
    assert "browser-automation dependency" in err.lower()
    # Manifest still printed; pull the runtime block out.
    payload = json.loads(out.split("\n\n")[0])
    rt = payload["runtime"]
    assert rt["execute_requested"] is True
    assert rt["confirmation_phrase_passed"] is True
    assert rt["confirmation_phrase_matches"] is True
    assert rt["detected_runtime"] is None
    assert rt["real_export_attempted"] is False


def test_execute_with_correct_confirmation_runtime_present_defers_to_placeholder(
    stub, capsys, monkeypatch
):
    """Simulate a future state where Playwright IS installed. The CLI
    must NOT silently succeed — it defers to
    `future_export_with_browser` which exits 3 (NOT_IMPLEMENTED) until
    the real screenshot code is landed in a dependency-approved
    commit."""
    monkeypatch.setattr(
        stub, "_detect_browser_runtime", lambda: "playwright.sync_api"
    )
    code, out, err = _run(
        stub,
        _base_argv() + [
            "--execute",
            "--confirm-local-export",
            CORRECT_CONFIRMATION_PHRASE,
        ],
        capsys,
    )
    assert code == stub.EXIT_NOT_IMPLEMENTED, err
    assert "future_export_with_browser" in err
    payload = json.loads(out.split("\n\n")[0])
    rt = payload["runtime"]
    assert rt["detected_runtime"] == "playwright.sync_api"
    assert rt["real_export_attempted"] is True


def test_detect_browser_runtime_returns_none_today(stub):
    """Today no approved runtime is installed in the repo. Pinned so a
    future accidental install fires this test instead of silently
    flipping the runtime branch."""
    detected = stub._detect_browser_runtime()
    assert detected is None, (
        "An approved browser-automation runtime appears to be "
        f"installed ({detected!r}). This is a real safety event: the "
        "next commit must NOT enable the real-screenshot branch "
        "without operator approval."
    )


def test_export_target_selectors_carousel_slide(stub, capsys):
    code, out, _err = _run(
        stub,
        _base_argv() + ["--mode", "carousel", "--slide-number", "3"],
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    sel = payload["export_target_selectors"]
    assert sel["root"] == "[data-export-root]"
    assert sel["mode_attribute"] == '[data-export-mode="carousel"]'
    assert sel["target"] == '[data-export-slide="3"]'


def test_export_target_selectors_story_frame(stub, capsys):
    code, out, _err = _run(
        stub,
        _base_argv() + ["--mode", "story", "--frame-number", "2"],
        capsys,
    )
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    sel = payload["export_target_selectors"]
    assert sel["mode_attribute"] == '[data-export-mode="story"]'
    assert sel["target"] == '[data-export-frame="2"]'


def test_export_target_selectors_single_card_modes_default_to_slide_one(stub, capsys):
    for mode in ("feed_post", "static_image", "linkedin_image", "reel_thumbnail", "video_thumbnail"):
        code, out, err = _run(stub, _base_argv() + ["--mode", mode], capsys)
        assert code == stub.EXIT_OK_DRY_RUN, f"{mode}: {err}"
        payload = json.loads(out.split("\n\n")[0])
        sel = payload["export_target_selectors"]
        assert sel["mode_attribute"] == f'[data-export-mode="{mode}"]'
        assert sel["target"] == '[data-export-slide="1"]', mode


def test_dry_run_runtime_block_reports_no_execute(stub, capsys):
    code, out, _err = _run(stub, _base_argv(), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    payload = json.loads(out.split("\n\n")[0])
    rt = payload["runtime"]
    assert rt["execute_requested"] is False
    assert rt["confirmation_phrase_passed"] is False
    assert rt["confirmation_phrase_matches"] is False
    assert rt["real_export_attempted"] is False


def test_real_export_status_reflects_detection(stub, capsys, monkeypatch):
    # No runtime → "dependency_missing"
    monkeypatch.setattr(stub, "_detect_browser_runtime", lambda: None)
    code, out, _err = _run(stub, _base_argv(), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    assert json.loads(out.split("\n\n")[0])["real_export_status"] == "dependency_missing"
    # Simulated dep → "dependency_present_impl_pending"
    monkeypatch.setattr(stub, "_detect_browser_runtime", lambda: "pyppeteer")
    code, out, _err = _run(stub, _base_argv(), capsys)
    assert code == stub.EXIT_OK_DRY_RUN
    assert (
        json.loads(out.split("\n\n")[0])["real_export_status"]
        == "dependency_present_impl_pending"
    )


def test_runtime_detection_uses_find_spec_not_import(stub, monkeypatch):
    """Pin that `_detect_browser_runtime` does NOT import the
    candidate modules — it only calls `importlib.util.find_spec`. We
    verify by patching `find_spec` to track calls and asserting the
    module is not added to sys.modules."""
    calls: list[str] = []

    def fake_find_spec(name: str) -> object | None:
        calls.append(name)
        return None  # always missing

    monkeypatch.setattr(
        "importlib.util.find_spec", fake_find_spec, raising=True
    )
    # Make sure we start clean.
    sys.modules.pop("playwright", None)
    sys.modules.pop("playwright.sync_api", None)
    sys.modules.pop("pyppeteer", None)

    detected = stub._detect_browser_runtime()
    assert detected is None
    # Every approved runtime spec was probed via find_spec.
    for spec_name in stub.APPROVED_BROWSER_RUNTIMES:
        assert spec_name in calls
    # And nothing was actually imported.
    assert "playwright" not in sys.modules
    assert "playwright.sync_api" not in sys.modules
    assert "pyppeteer" not in sys.modules


def test_execute_with_correct_confirmation_no_runtime_writes_no_file(
    stub, capsys, monkeypatch, tmp_path
):
    """Belt-and-braces: even on the dependency-missing branch, the
    output dir must remain untouched."""
    monkeypatch.setattr(stub, "_detect_browser_runtime", lambda: None)
    out_dir = tmp_path / "exports"
    assert not out_dir.exists()
    code, _out, _err = _run(
        stub,
        _base_argv() + [
            "--output-dir",
            str(out_dir),
            "--execute",
            "--confirm-local-export",
            CORRECT_CONFIRMATION_PHRASE,
        ],
        capsys,
    )
    assert code == stub.EXIT_DEPENDENCY_MISSING, _err
    assert not out_dir.exists()

