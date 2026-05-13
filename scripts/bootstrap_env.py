"""Bootstrap the on-disk layout of clients/_template/references/.

Why this exists: git does not track empty directories. The Producer reads
character/product/referral_videos asset folders from clients/<id>/references/
which are seeded from clients/_template/references/. On a fresh checkout
those subdirectories don't exist, so the Producer crashes with
FileNotFoundError before the LLM ever runs.

This script is idempotent: it creates each missing subdirectory and drops
an empty .gitkeep so the directory is committable. Safe to re-run.

Usage:
    python scripts/bootstrap_env.py
"""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_REFERENCES = REPO_ROOT / "clients" / "_template" / "references"
REQUIRED_SUBDIRS = ("characters", "products", "referral_videos")


def ensure_dir_with_gitkeep(directory: Path) -> bool:
    """Ensure `directory` exists and contains a .gitkeep. Returns True if
    anything was created (dir or file), False if both already existed."""
    created = False
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        created = True
    gitkeep = directory / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()
        created = True
    return created


def main() -> int:
    if not TEMPLATE_REFERENCES.parent.exists():
        print(
            f"ERROR: template root missing at {TEMPLATE_REFERENCES.parent}. "
            f"Are you running this from inside the repo?",
            file=sys.stderr,
        )
        return 1

    TEMPLATE_REFERENCES.mkdir(parents=True, exist_ok=True)

    any_created = False
    for name in REQUIRED_SUBDIRS:
        target = TEMPLATE_REFERENCES / name
        if ensure_dir_with_gitkeep(target):
            print(f"[created] {target.relative_to(REPO_ROOT)}")
            any_created = True
        else:
            print(f"[ok]      {target.relative_to(REPO_ROOT)}")

    if not any_created:
        print("All template reference directories already in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
