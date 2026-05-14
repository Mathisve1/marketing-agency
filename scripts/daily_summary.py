"""Generate a daily summary of open operator tasks. Pass 2 Initiative 2.

Runs the inferred-task sync once, then renders the resulting persistent
inbox as concise markdown grouped by both priority (critical / high) and
category (approvals / failures / review / ungraded / follow-ups).

By design:
- NO external API call.
- NO agent dispatch.
- NO long-running background loop. This is a one-shot script the
  operator invokes manually (or via Windows Task Scheduler / cron).

Usage:
    py -3.11 scripts/daily_summary.py
    py -3.11 scripts/daily_summary.py --out reports/daily-summary-2026-05-14.md

Output path note: `reports/daily-summary-*.md` and `.txt` are both
gitignored (`.gitignore` line 82-83) and flagged by the repo hygiene
scanner (`scripts/check_repo_hygiene.py` rule `daily-summary-md`). The
script will create `reports/` on demand.

Exit codes:
    0  - rendered and emitted successfully (including the empty-inbox case)
    1  - I/O or sync failure
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _scope(task) -> str:
    if task.client_id:
        return f"client `{task.client_id}`"
    if task.prospect_id:
        return f"prospect `{task.prospect_id}`"
    return "system"


def _render_task_line(task) -> list[str]:
    """Two-line block per task: title + scope, then recommended action."""
    return [
        f"- **{task.title}**  _({_scope(task)}, agent: {task.agent})_",
        f"  - Next: {task.recommended_next_action}",
    ]


def render_summary(stored_tasks: list, *, today: Optional[str] = None) -> str:
    """Render the daily-summary markdown.

    `stored_tasks` is a list of `services.operator_task_store.StoredTask`.
    `today` defaults to today's ISO date. Output is deterministic given
    the input list + today value, which makes the file path collisionless
    and tests trivially round-trippable.
    """
    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not stored_tasks:
        return (
            f"# Daily summary — {today}\n\n"
            f"**No open operator tasks.** Inbox is clean. "
            f"Dispatch a new agent run when ready.\n"
        )

    lines: list[str] = [
        f"# Daily summary — {today}",
        "",
        f"**{len(stored_tasks)} open task(s).**",
        "",
    ]

    # Priority cross-cuts. Render only non-empty buckets so a clean
    # critical/high state isn't visual noise.
    crit = [t for t in stored_tasks if t.priority == "critical"]
    high = [t for t in stored_tasks if t.priority == "high"]

    if crit:
        lines.append(f"## Critical ({len(crit)})")
        for t in crit:
            lines.extend(_render_task_line(t))
        lines.append("")

    if high:
        lines.append(f"## High ({len(high)})")
        for t in high:
            lines.extend(_render_task_line(t))
        lines.append("")

    # Category buckets. Same task may appear in BOTH a priority bucket
    # above AND a category bucket below - that's intentional, the
    # operator wants to see "what's urgent" AND "what kind of work".
    sections = [
        ("Approvals waiting", "approval"),
        ("Failed jobs", "failure"),
        ("Review before sending", "review"),
        ("Ungraded outputs", "grading"),
        ("Follow-ups", "follow_up"),
    ]
    for header, category in sections:
        bucket = [t for t in stored_tasks if t.category == category]
        if not bucket:
            continue
        lines.append(f"## {header} ({len(bucket)})")
        for t in bucket:
            lines.extend(_render_task_line(t))
            if t.location_hint:
                lines.append(f"  - Where: {t.location_hint}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a daily summary of open operator tasks. "
            "Reads from the persistent task store after syncing the "
            "inferred inbox. Never makes external API calls."
        ),
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help=(
            "Optional output path. If given, the markdown is written to "
            "this path (parent dirs created on demand). Otherwise printed "
            "to stdout. Recommended layout: "
            "reports/daily-summary-YYYY-MM-DD.md"
        ),
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Override the operator_tasks.db path. Used by tests.",
    )
    parser.add_argument(
        "--clients-root",
        type=str,
        default=None,
        help="Override the clients/ root for sync. Used by tests.",
    )
    parser.add_argument(
        "--prospects-root",
        type=str,
        default=None,
        help="Override the prospects/ root for sync. Used by tests.",
    )
    parser.add_argument(
        "--mcp-db-path",
        type=str,
        default=None,
        help="Override the mcp_pending_runs.db path for sync. Used by tests.",
    )
    parser.add_argument(
        "--eval-path",
        type=str,
        default=None,
        help="Override the evals/output_reviews.jsonl path for sync. Used by tests.",
    )
    parser.add_argument(
        "--today",
        type=str,
        default=None,
        help=(
            "Override today's ISO date in the output header. Used by "
            "tests so the file path is deterministic."
        ),
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help=(
            "Skip the inferred-task sync. Reads the persistent store as-is. "
            "Useful when running back-to-back from another script that "
            "already synced."
        ),
    )

    args = parser.parse_args(argv)

    # Local imports so a missing dependency surfaces here, not at script
    # import time (keeps doctor's `check_daily_summary_imports` smoke test
    # honest).
    from services import operator_task_store

    db_path = Path(args.db_path) if args.db_path else operator_task_store.DEFAULT_DB_PATH
    clients_root = Path(args.clients_root) if args.clients_root else None
    prospects_root = Path(args.prospects_root) if args.prospects_root else None
    mcp_db_path = Path(args.mcp_db_path) if args.mcp_db_path else None
    eval_path = Path(args.eval_path) if args.eval_path else None

    try:
        if not args.no_sync:
            operator_task_store.sync_from_inbox(
                clients_root=clients_root,
                prospects_root=prospects_root,
                mcp_db_path=mcp_db_path,
                eval_path=eval_path,
                db_path=db_path,
            )
        stored_tasks = operator_task_store.list_open_tasks(db_path=db_path)
    except Exception as e:
        print(
            f"ERROR: daily_summary failed during sync/list: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1

    markdown = render_summary(stored_tasks, today=args.today)

    if args.out is None:
        sys.stdout.write(markdown)
        return 0

    out_path = Path(args.out)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
    except OSError as e:
        print(f"ERROR: could not write {out_path}: {e}", file=sys.stderr)
        return 1

    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
