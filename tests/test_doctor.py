"""Deterministic tests for scripts/doctor.py.

We deliberately only test the small, pure-Python helpers + the CheckRunner
accounting. The full main() orchestrator exercises filesystem, SQLite, and
network-less imports against the live repo; that's covered by running
`python scripts/doctor.py` manually (it's part of the production-readiness
deliverables in README.md) rather than reproduced inside pytest.

We intentionally do NOT assert on the exact contents of doctor's print
output, only on the data structures (failures / warnings lists). That
keeps the tests stable against cosmetic output edits.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCTOR_PATH = REPO_ROOT / "scripts" / "doctor.py"


@pytest.fixture(scope="module")
def doctor_module():
    """Load scripts/doctor.py as a module without importing scripts/ as a package.

    scripts/ has no __init__.py and is not meant to be importable as a
    package; loading via spec keeps the test self-contained.
    """
    spec = importlib.util.spec_from_file_location("doctor_under_test", DOCTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["doctor_under_test"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# find_repo_root
# --------------------------------------------------------------------------- #


def test_find_repo_root_locates_pyproject(doctor_module, tmp_path: Path):
    """find_repo_root walks upward until it finds a pyproject.toml."""
    root = tmp_path / "fakerepo"
    nested = root / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    found = doctor_module.find_repo_root(nested)
    assert found == root


def test_find_repo_root_raises_when_missing(doctor_module, tmp_path: Path):
    """When no ancestor has pyproject.toml we get a clear RuntimeError.

    NOTE: this test relies on tmp_path NOT being inside the marketing-agency
    repo. pytest's tmp_path lives under the OS tempdir, which is correct
    for that on every supported platform.
    """
    isolated = tmp_path / "isolated" / "deep"
    isolated.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="Could not find pyproject.toml"):
        doctor_module.find_repo_root(isolated)


# --------------------------------------------------------------------------- #
# CheckRunner
# --------------------------------------------------------------------------- #


def test_check_runner_accumulates(doctor_module, capsys):
    runner = doctor_module.CheckRunner()
    runner.passed("ok one")
    runner.passed("ok two")
    runner.warned("soft issue")
    runner.failed("hard issue")
    runner.info("informational note")

    assert runner.failures == ["hard issue"]
    assert runner.warnings == ["soft issue"]

    captured = capsys.readouterr().out
    # Output is in order, but we only assert on tag presence + counts to
    # remain stable against cosmetic edits.
    assert captured.count(doctor_module.PASS) == 2
    assert captured.count(doctor_module.WARN) == 1
    assert captured.count(doctor_module.FAIL) == 1
    assert captured.count(doctor_module.INFO) == 1


def test_check_runner_starts_clean(doctor_module):
    runner = doctor_module.CheckRunner()
    assert runner.failures == []
    assert runner.warnings == []


# --------------------------------------------------------------------------- #
# Constants sanity (catches accidental edits that would silently weaken
# the doctor's coverage)
# --------------------------------------------------------------------------- #


def test_required_env_var_names_cover_documented_keys(doctor_module):
    """The doctor must check at least the keys documented in .env.example
    as 'Required'. If we add a new required key to .env.example we want
    this test to nudge us to also add it here."""
    names = set(doctor_module.REQUIRED_ENV_VAR_NAMES)
    must_contain = {
        "ANTHROPIC_API_KEY",
        "TAVILY_API_KEY",
        "APIFY_API_TOKEN",
        "KLING_API_KEY",
        "KLING_API_SECRET",
        "META_ACCESS_TOKEN",
        "META_AD_ACCOUNT_ID",
    }
    assert must_contain.issubset(names), (
        f"REQUIRED_ENV_VAR_NAMES missing: {must_contain - names}"
    )


def test_required_top_level_dirs_include_clients_template(doctor_module):
    """Onboarding probe depends on clients/_template existing; if a future
    edit drops it from REQUIRED_TOP_LEVEL_DIRS the probe would still fail,
    but a clearer 'Missing directory' message helps triage."""
    assert "clients/_template" in doctor_module.REQUIRED_TOP_LEVEL_DIRS
    assert "clients" in doctor_module.REQUIRED_TOP_LEVEL_DIRS


# --------------------------------------------------------------------------- #
# PR A: new check functions for the persistent task store + daily summary
# --------------------------------------------------------------------------- #


def test_check_operator_task_store_passes_on_clean_repo(doctor_module):
    """Doctor's check_operator_task_store should produce a PASS line in
    a clean repo. Pinned so a future refactor that breaks the schema-init
    smoke test surfaces as a hard test failure."""
    runner = doctor_module.CheckRunner()
    doctor_module.check_operator_task_store(runner)
    assert runner.failures == [], (
        f"check_operator_task_store unexpectedly failed: {runner.failures}"
    )


def test_check_daily_summary_imports_passes_on_clean_repo(doctor_module):
    """Doctor's check_daily_summary_imports loads scripts/daily_summary.py
    via importlib.util and verifies main + render_summary are exposed.
    A failure here usually means a top-level import in daily_summary.py
    raised (and would also fail the daily summary at runtime)."""
    runner = doctor_module.CheckRunner()
    doctor_module.check_daily_summary_imports(runner)
    assert runner.failures == [], (
        f"check_daily_summary_imports unexpectedly failed: {runner.failures}"
    )
