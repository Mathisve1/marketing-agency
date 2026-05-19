"""Repo-hygiene scanner for sensitive paths.

Even with `.gitignore` and `tests/test_security.py`, a contributor can
still stage a file the gitignore *doesn't* match (a screenshot at repo
root, a stray `.env.local`, a pitch PDF copied into a tmp dir, etc.) and
nothing flags it before commit.

This script is the active guard. It scans EITHER the staged file list
OR the full tracked file list against a curated pattern set and exits
non-zero if anything matches.

Modes:
  --staged    (default) scan `git diff --cached --name-only` -- the set
              of files about to be committed. Use as a pre-commit hook.
  --tracked   scan `git ls-files` -- everything currently in git's
              tracked-file list. Use in CI as a continuous guard
              against historical leaks.

NEVER reads file contents. NEVER prints potential secret values. The
script's job is to flag PATHS the operator must double-check; secret-
value scanning is a separate concern (gitleaks / GitHub secret
scanning) we explicitly defer.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Pattern set
#
# Each Rule has:
#   id       short identifier the operator can grep on
#   regex    full-path regex (case-insensitive). Forward slashes are
#            normalised in _normalise_path before the match.
#   reason   one-line explanation for the operator
#
# Anti-pattern: do NOT match by directory name alone (e.g. r"prospects").
# Always anchor with a slash so we don't false-positive on a contributor
# named "Prospects" appearing in a CHANGELOG line. Patterns are matched
# against forward-slashed relative paths only.
# --------------------------------------------------------------------------- #


class Rule(NamedTuple):
    id: str
    regex: re.Pattern[str]
    reason: str


_RULES: tuple[Rule, ...] = (
    # Per-client runtime data
    Rule(
        "client-sqlite",
        re.compile(r"^clients/(?!_template/)[^/]+/client_data\.db(-shm|-wal)?$", re.IGNORECASE),
        "Per-client SQLite database (winning hooks, plans, jobs). Customer commercial data.",
    ),
    Rule(
        "client-perflog",
        re.compile(r"^clients/(?!_template/)[^/]+/performance_log\.json$", re.IGNORECASE),
        "Legacy per-client performance log. May contain Kling task IDs.",
    ),
    Rule(
        "client-references",
        re.compile(r"^clients/(?!_template/)[^/]+/references/", re.IGNORECASE),
        "Customer-supplied character/product imagery. Often PII or pre-launch product.",
    ),
    Rule(
        "client-outputs",
        re.compile(r"^clients/(?!_template/)[^/]+/outputs/", re.IGNORECASE),
        "Generated MP4s + PDF reports. Commercially sensitive.",
    ),
    Rule(
        "client-env",
        re.compile(r"^clients/(?!_template/)[^/]+/\.env(\..+)?$", re.IGNORECASE),
        "Per-client API keys / secrets.",
    ),

    # Prospect data (lead pipeline)
    Rule(
        "prospects-tree",
        re.compile(r"^prospects/(?!\.gitkeep$)", re.IGNORECASE),
        "Outreach scrape data + pitch PDFs. Your sales pipeline.",
    ),

    # Process audit logs
    Rule(
        "logs-tree",
        re.compile(r"^logs/(?!\.gitkeep$)", re.IGNORECASE),
        "Kling API request/response audit log. Includes prompts.",
    ),
    Rule(
        "kling-jsonl-anywhere",
        re.compile(r"(^|/)kling-api\.jsonl$", re.IGNORECASE),
        "Kling audit log dropped outside logs/. Contains prompts.",
    ),

    # LangGraph + MCP runtime DBs
    Rule(
        "checkpoints-db",
        re.compile(r"(^|/)checkpoints\.db(-shm|-wal)?$", re.IGNORECASE),
        "LangGraph SqliteSaver checkpoint DB. Contains paused supervisor state.",
    ),
    Rule(
        "mcp-pending-db",
        re.compile(r"(^|/)mcp_pending_runs\.db(-shm|-wal)?$", re.IGNORECASE),
        "MCP pending-runs registry. Contains operator prompts + thread IDs.",
    ),

    # Manual eval data
    Rule(
        "eval-jsonl",
        re.compile(r"^evals/output_reviews\.jsonl$", re.IGNORECASE),
        "Manual output-quality grades. May reference real client/prospect names.",
    ),
    Rule(
        "eval-csv",
        re.compile(r"^evals/.+\.csv$", re.IGNORECASE),
        "Eval CSV exports. May reference real client/prospect names.",
    ),

    # V1.9 Pass 1 placeholders for Pass 2 services. The .db files don't
    # exist yet but reserving the patterns now means the moment Pass 2
    # ships nothing slips into git on the very first run.
    Rule(
        "operator-tasks-db",
        re.compile(r"(^|/)operator_tasks\.db(-shm|-wal)?$", re.IGNORECASE),
        "Operator persistent task store (Pass 2). May contain client/prospect names.",
    ),
    Rule(
        "cost-ledger-db",
        re.compile(r"(^|/)cost_ledger\.db(-shm|-wal)?$", re.IGNORECASE),
        "Cost / activity ledger (Pass 2). May contain client/prospect-attributed events.",
    ),
    Rule(
        "daily-summary-md",
        re.compile(r"^reports/daily-summary-.+\.(md|txt)$", re.IGNORECASE),
        "Daily summary export (Pass 2). May reference real client/prospect names.",
    ),

    # Stray .env files (excluding the tracked .env.example)
    Rule(
        "env-file-anywhere",
        re.compile(r"(^|/)\.env(\.[A-Za-z0-9_-]+)?$", re.IGNORECASE),
        "A .env file. Real keys must never reach git. Use .env.example as the only tracked variant.",
    ),

    # Pitch PDFs anywhere outside the docs / template
    Rule(
        "pitch-pdf-anywhere",
        re.compile(r"(^|/)pitch\.pdf$", re.IGNORECASE),
        "Pitch PDF. Generated artifact for outreach; should live under prospects/<id>/.",
    ),
)

# Allow-list overrides: paths matching ANY of these are NEVER flagged,
# even if a Rule above would match. Used for the .env.example seed and
# the .gitkeep markers we want to track inside otherwise-ignored dirs.
_ALLOW_LIST: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\.env\.example$", re.IGNORECASE),
    re.compile(r"^clients/_template/\.env\.example$", re.IGNORECASE),
    # The Next dashboard ships its own .env.example template; only
    # placeholder values + comments, no real keys (the real values live
    # in web/.env.local, which is gitignored). Tracked since
    # 4001fb9 "feat: add owner command center and agent workflow
    # foundation".
    re.compile(r"^web/\.env\.example$", re.IGNORECASE),
    re.compile(r"^evals/\.gitkeep$", re.IGNORECASE),
    re.compile(r"^prospects/\.gitkeep$", re.IGNORECASE),
    re.compile(r"^logs/\.gitkeep$", re.IGNORECASE),
    # The script itself references all these paths in regex sources, so
    # don't flag scripts/check_repo_hygiene.py against its own rules.
    re.compile(r"^scripts/check_repo_hygiene\.py$", re.IGNORECASE),
    # Tests pin the rule set against synthetic paths; the tests
    # themselves contain "prospects/", "logs/" etc. as STRINGS but not
    # as actual file locations. The rules above all anchor on "^...",
    # so test files at tests/test_check_repo_hygiene.py never match
    # anyway, but be explicit.
    re.compile(r"^tests/test_check_repo_hygiene\.py$", re.IGNORECASE),
)


# --------------------------------------------------------------------------- #
# Path normalisation + matching
# --------------------------------------------------------------------------- #


def _normalise_path(p: str) -> str:
    """Convert backslashes to forward slashes and strip a leading './'.
    Git emits forward slashes on Windows already but we belt-and-suspender."""
    p = p.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    return p


def _is_allowed(path: str) -> bool:
    return any(rx.search(path) for rx in _ALLOW_LIST)


class Hit(NamedTuple):
    path: str
    rule: Rule


def find_hits(paths: list[str]) -> list[Hit]:
    """Match each path against the rule set. Returns hits in input order."""
    hits: list[Hit] = []
    for raw in paths:
        if not raw:
            continue
        norm = _normalise_path(raw)
        if _is_allowed(norm):
            continue
        for rule in _RULES:
            if rule.regex.search(norm):
                hits.append(Hit(norm, rule))
                break  # one rule per path is enough; first hit wins
    return hits


# --------------------------------------------------------------------------- #
# Path collection (mode --staged / --tracked)
# --------------------------------------------------------------------------- #


def _git_paths(args: list[str]) -> list[str]:
    """Run git, return non-empty stdout lines. Empty list on git errors
    (treats "no git repo" as "nothing to scan", which is the right
    behaviour for CI on a checkout that somehow lost its .git)."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        # git binary missing on the host - return [] rather than crash.
        return []
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def staged_paths() -> list[str]:
    """Files staged for commit. Powers the pre-commit hook mode."""
    return _git_paths(["diff", "--cached", "--name-only"])


def tracked_paths() -> list[str]:
    """All currently-tracked files. Powers the CI mode."""
    return _git_paths(["ls-files"])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Scan repo paths for sensitive patterns. Does NOT read file "
            "contents and NEVER prints potential secret values."
        ),
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--staged",
        action="store_true",
        help="(default) Scan files staged for commit. Use as a pre-commit hook.",
    )
    mode.add_argument(
        "--tracked",
        action="store_true",
        help="Scan all currently-tracked files. Use in CI as a continuous guard.",
    )
    p.add_argument(
        "--paths",
        nargs="*",
        help=(
            "Override path source: scan exactly these paths instead of "
            "asking git. Useful for tests."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.paths is not None:
        paths = args.paths
        mode = "explicit"
    elif args.tracked:
        paths = tracked_paths()
        mode = "tracked"
    else:
        paths = staged_paths()
        mode = "staged"

    hits = find_hits(paths)

    if not hits:
        print(f"OK  repo hygiene clean ({len(paths)} path(s) scanned, mode={mode})")
        return 0

    print(f"FAIL  repo hygiene found {len(hits)} match(es) in mode={mode}:")
    for h in hits:
        print(f"  - {h.path}")
        print(f"      [{h.rule.id}] {h.rule.reason}")
    print()
    print(
        "These paths look sensitive. If a hit is a false positive, add it to "
        "_ALLOW_LIST in scripts/check_repo_hygiene.py and explain why in the "
        "commit. If it's real, unstage / git rm and verify .gitignore covers it."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
