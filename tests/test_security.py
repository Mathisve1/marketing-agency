"""Security & data-safety regression tests.

Three concerns:
  1. Path traversal: client_id / prospect_id regex must reject any input
     that could escape the clients/ or prospects/ root.
  2. Onboarding completeness: ClientContext.onboard must produce a silo
     where every directory the Producer reads/writes already exists.
  3. .gitignore drift: the patterns guarding runtime data must stay in
     place. A casual `.gitignore` cleanup must not silently re-enable
     committing client databases or prospect data.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agents.outreach.prospect_store import ProspectStore
from core.client_context import ClientContext


REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    """Copy the real _template into a tmp dir so tests don't touch the repo."""
    src = REPO_ROOT / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


# --------------------------------------------------------------------------- #
# Path-traversal: client_id
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_id", [
    "../escape",
    "..",
    ".",
    "/etc/passwd",
    "client/with/slash",
    "client\\with\\backslash",
    "UPPER",
    "MixedCase",
    "-leading-dash",
    "_leading-underscore",
    "with space",
    "",
    "a" * 65,         # over the 64-char cap
    "client;rm -rf",  # shell metacharacter
    "client$x",
])
def test_invalid_client_id_rejected(clients_root: Path, bad_id: str):
    with pytest.raises(ValueError):
        ClientContext.onboard(bad_id, "x", clients_root=clients_root)


def test_template_id_is_reserved(clients_root: Path):
    with pytest.raises(ValueError):
        ClientContext.onboard("_template", "x", clients_root=clients_root)


# --------------------------------------------------------------------------- #
# Path-traversal: prospect_id
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_id", [
    "../escape",
    "..",
    "/etc/passwd",
    "prospect/with/slash",
    "prospect\\back",
    "UPPER",
    "-leading",
    "",
])
def test_invalid_prospect_id_rejected(tmp_path: Path, bad_id: str):
    with pytest.raises(ValueError):
        ProspectStore(bad_id, prospects_root=tmp_path)


# --------------------------------------------------------------------------- #
# Onboard: every required subdir exists
# --------------------------------------------------------------------------- #


REQUIRED_SUBDIRS = (
    "references/characters",
    "references/products",
    "references/referral_videos",
    "outputs/videos",
    "outputs/reports",
)


def test_onboard_creates_every_required_subdir(clients_root: Path):
    """Even if the template lost its empty subdirs to a fresh git clone,
    onboard() must still produce a silo the Producer can write into."""
    ctx = ClientContext.onboard(
        "acme-test", "Acme Test Co", clients_root=clients_root
    )
    for subdir in REQUIRED_SUBDIRS:
        target = ctx.root / subdir
        assert target.is_dir(), f"onboard() did not create {subdir}"


def test_onboard_works_when_template_subdirs_missing(tmp_path: Path):
    """Simulates a fresh clone: copy MASTER_CONTEXT.md + performance_log.json
    only. Onboard must still succeed."""
    minimal_template = tmp_path / "clients" / "_template"
    minimal_template.mkdir(parents=True)
    src = REPO_ROOT / "clients" / "_template"
    shutil.copy2(src / "MASTER_CONTEXT.md", minimal_template / "MASTER_CONTEXT.md")
    shutil.copy2(
        src / "performance_log.json", minimal_template / "performance_log.json"
    )

    ctx = ClientContext.onboard(
        "fresh-clone-client", "Fresh Clone", clients_root=tmp_path / "clients"
    )
    for subdir in REQUIRED_SUBDIRS:
        assert (ctx.root / subdir).is_dir(), (
            f"onboard() failed to create {subdir} from a minimal template"
        )


# --------------------------------------------------------------------------- #
# .gitignore: critical patterns are present
# --------------------------------------------------------------------------- #


REQUIRED_GITIGNORE_PATTERNS = (
    ".env",
    "clients/*/.env",
    "clients/*/client_data.db",
    "clients/*/client_data.db-shm",
    "clients/*/client_data.db-wal",
    "clients/*/performance_log.json",
    "clients/*/references/",
    "clients/*/outputs/",
    "prospects/",
    "logs/",
)


def test_gitignore_blocks_all_runtime_paths():
    """Guards against accidental .gitignore edits that would re-enable
    committing runtime data. If you intentionally drop one of these
    rules, update this test in the same commit and document why in
    SECURITY.md."""
    gitignore_path = REPO_ROOT / ".gitignore"
    assert gitignore_path.exists(), ".gitignore must exist at repo root"
    contents = gitignore_path.read_text(encoding="utf-8")
    lines = {line.strip() for line in contents.splitlines() if line.strip()}

    missing = [p for p in REQUIRED_GITIGNORE_PATTERNS if p not in lines]
    assert not missing, (
        f".gitignore is missing required patterns: {missing}. "
        f"See SECURITY.md before removing any of these."
    )


def test_gitignore_preserves_template():
    """The seed template must NOT be gitignored - it's the source of truth
    cloned by ClientContext.onboard for every new client."""
    gitignore_path = REPO_ROOT / ".gitignore"
    contents = gitignore_path.read_text(encoding="utf-8")
    lines = {line.strip() for line in contents.splitlines() if line.strip()}

    assert "!clients/_template/" in lines, (
        "Template directory must be re-included via negation"
    )
    assert "!clients/_template/**" in lines, (
        "Template contents must be re-included via negation"
    )
