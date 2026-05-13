"""Tests for scripts/check_repo_hygiene.py.

Pins the rule set: bad paths are flagged, good paths are not. Runs
entirely against synthetic path lists via the `--paths` flag - never
shells out to git, never reads file contents.

The CI invocation is `python scripts/check_repo_hygiene.py --tracked`
which scans `git ls-files` output. We don't reproduce that here; we
test the matcher directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_repo_hygiene as hygiene  # noqa: E402

# --------------------------------------------------------------------------- #
# Matcher: bad paths
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path,expected_rule_id", [
    # Per-client runtime
    ("clients/acme/client_data.db", "client-sqlite"),
    ("clients/acme/client_data.db-wal", "client-sqlite"),
    ("clients/acme/client_data.db-shm", "client-sqlite"),
    ("clients/foo-corp/performance_log.json", "client-perflog"),
    ("clients/acme/references/characters/face.png", "client-references"),
    ("clients/acme/references/products/box.jpg", "client-references"),
    ("clients/acme/outputs/videos/x.mp4", "client-outputs"),
    ("clients/acme/outputs/reports/report.pdf", "client-outputs"),
    ("clients/acme/.env", "client-env"),
    ("clients/acme/.env.local", "client-env"),

    # Prospects
    ("prospects/gymshark/audit.json", "prospects-tree"),
    ("prospects/on-running/pitch.pdf", "prospects-tree"),

    # Logs
    ("logs/kling-api.jsonl", "logs-tree"),
    ("logs/something-else.log", "logs-tree"),

    # Stray Kling log outside logs/
    ("kling-api.jsonl", "kling-jsonl-anywhere"),
    ("scratch/kling-api.jsonl", "kling-jsonl-anywhere"),

    # Checkpoints + MCP DBs at root
    ("checkpoints.db", "checkpoints-db"),
    ("checkpoints.db-wal", "checkpoints-db"),
    ("mcp_pending_runs.db", "mcp-pending-db"),
    ("mcp_pending_runs.db-shm", "mcp-pending-db"),

    # Eval data
    ("evals/output_reviews.jsonl", "eval-jsonl"),
    ("evals/export-2026-05-14.csv", "eval-csv"),

    # Stray .env files
    (".env", "env-file-anywhere"),
    (".env.local", "env-file-anywhere"),
    ("subdir/.env.production", "env-file-anywhere"),

    # Pitch PDF anywhere
    ("pitch.pdf", "pitch-pdf-anywhere"),
    ("Downloads/pitch.pdf", "pitch-pdf-anywhere"),
])
def test_bad_paths_are_flagged(path: str, expected_rule_id: str):
    hits = hygiene.find_hits([path])
    assert len(hits) == 1, f"expected one hit for {path!r}, got {hits}"
    assert hits[0].rule.id == expected_rule_id, (
        f"{path!r} matched rule {hits[0].rule.id!r}, expected {expected_rule_id!r}"
    )


# --------------------------------------------------------------------------- #
# Matcher: explicitly safe paths (must NOT trip)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", [
    # Source code
    "agents/producer/agent.py",
    "core/client_context.py",
    "services/hitl_service.py",
    "ui/app.py",
    "tests/test_security.py",

    # Repo metadata
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    ".gitignore",
    ".github/workflows/ci.yml",

    # The seed template files we DO want tracked
    ".env.example",
    "clients/_template/.env.example",
    "clients/_template/MASTER_CONTEXT.md",
    "clients/_template/performance_log.json",
    "clients/_template/references/characters/.gitkeep",
    "clients/_template/references/products/.gitkeep",
    "clients/_template/references/referral_videos/.gitkeep",

    # The evals/.gitkeep marker
    "evals/.gitkeep",

    # Hygiene script itself + its test (allow-listed)
    "scripts/check_repo_hygiene.py",
    "tests/test_check_repo_hygiene.py",

    # Generic strings that contain rule keywords but are not sensitive paths
    "docs/architecture/prospects-flow.md",       # contains 'prospects' but not under prospects/
    "docs/observability/logs.md",                 # contains 'logs' but not under logs/
])
def test_safe_paths_are_not_flagged(path: str):
    hits = hygiene.find_hits([path])
    assert hits == [], f"{path!r} unexpectedly flagged: {hits}"


# --------------------------------------------------------------------------- #
# Allow-list takes precedence
# --------------------------------------------------------------------------- #


def test_template_env_example_not_flagged_despite_dotenv_rule():
    """The generic .env-anywhere rule would otherwise match
    clients/_template/.env.example -- the allow-list must trump it."""
    hits = hygiene.find_hits(["clients/_template/.env.example"])
    assert hits == []


def test_template_subdirs_never_flagged():
    """The client-references / client-outputs / client-env rules carve
    out _template via (?!_template/). Belt-and-suspenders test."""
    for p in (
        "clients/_template/references/characters/.gitkeep",
        "clients/_template/references/products/.gitkeep",
        "clients/_template/references/referral_videos/.gitkeep",
    ):
        assert hygiene.find_hits([p]) == [], p


# --------------------------------------------------------------------------- #
# CLI: --paths mode
# --------------------------------------------------------------------------- #


def test_cli_paths_mode_clean_exits_zero(capsys):
    rc = hygiene.main(["--paths", "agents/producer/agent.py", "README.md"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_cli_paths_mode_dirty_exits_one_and_lists_hits(capsys):
    rc = hygiene.main(["--paths", "prospects/x/audit.json", "logs/kling-api.jsonl"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out
    assert "prospects/x/audit.json" in out
    assert "logs/kling-api.jsonl" in out
    # Rule IDs are surfaced so the operator can grep on them.
    assert "[prospects-tree]" in out
    assert "[logs-tree]" in out


def test_cli_does_not_print_file_contents():
    """Defensive smoke check on the public surface: the CLI never opens
    files. Inspecting the source guarantees we can't leak by accident
    in the future. (Not a runtime behaviour test - just a code-shape
    pin against a future regression.)"""
    src = (REPO_ROOT / "scripts" / "check_repo_hygiene.py").read_text(encoding="utf-8")
    # No file-open calls of any kind in the scanner module.
    forbidden = ("open(", "Path.read_text", ".read_bytes(", ".read_text(")
    for tok in forbidden:
        assert tok not in src, (
            f"check_repo_hygiene.py contains {tok!r} - the scanner must NOT "
            "read file contents. See module docstring."
        )


# --------------------------------------------------------------------------- #
# Path normalisation - Windows backslashes / leading ./
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw,expected", [
    ("clients\\acme\\client_data.db", "clients/acme/client_data.db"),
    ("./prospects/x/audit.json", "prospects/x/audit.json"),
    (".\\evals\\output_reviews.jsonl", "evals/output_reviews.jsonl"),
])
def test_normalise_path_handles_windows_and_relative(raw: str, expected: str):
    assert hygiene._normalise_path(raw) == expected


def test_windows_style_path_still_flagged():
    """git on Windows usually emits forward slashes, but if a contributor
    pipes in a backslash path manually it must still be caught."""
    hits = hygiene.find_hits(["clients\\acme\\client_data.db"])
    assert len(hits) == 1
    assert hits[0].rule.id == "client-sqlite"
