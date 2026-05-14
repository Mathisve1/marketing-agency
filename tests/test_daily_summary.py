"""Tests for scripts/daily_summary.py (Pass 2.1 Initiative 2).

Pins the rendered output sections + the --out file path behavior.

The script is loaded via importlib.util because scripts/ has no
__init__.py and is not meant to be a Python package - same trick used by
tests/test_doctor.py for doctor.py.

All tests run against a tmp_path-scoped operator_tasks.db. The full
sync-from-inbox path is exercised manually only in the "no external
APIs called" test (with everything patched). The other tests use
--no-sync + a pre-seeded store so they don't depend on the inbox's
filesystem scan.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from services import operator_task_store
from services.operator_inbox import OperatorTask

REPO_ROOT = Path(__file__).resolve().parent.parent
DAILY_SUMMARY_PATH = REPO_ROOT / "scripts" / "daily_summary.py"
NOW = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def daily_summary_module():
    spec = importlib.util.spec_from_file_location(
        "daily_summary_under_test", DAILY_SUMMARY_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["daily_summary_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "operator_tasks.db"


def _inferred(
    fingerprint: str,
    *,
    priority: str = "medium",
    category: str = "follow_up",
    title: str = "Generic task",
    client_id: str | None = "acme",
    location_hint: str | None = None,
) -> OperatorTask:
    return OperatorTask(
        id=fingerprint,
        priority=priority,
        category=category,
        agent="producer",
        client_id=client_id,
        prospect_id=None,
        title=title,
        description="(test)",
        source_type="video_plan",
        source_id=fingerprint,
        action_hint=None,
        location_hint=location_hint,
        recommended_next_action="Do the thing.",
    )


# --------------------------------------------------------------------------- #
# render_summary - section grouping
# --------------------------------------------------------------------------- #


def test_render_empty_inbox_returns_clean_message(daily_summary_module):
    md = daily_summary_module.render_summary([], today="2026-05-14")
    assert "Daily summary — 2026-05-14" in md
    assert "No open operator tasks" in md
    assert "Inbox is clean" in md
    # No section headers when empty.
    assert "## Critical" not in md
    assert "## Approvals waiting" not in md


def test_render_groups_critical_before_high(
    daily_summary_module, db_path: Path,
):
    """Critical section must appear before High section in the output."""
    operator_task_store.sync_inferred_tasks(
        [
            _inferred("h", priority="high", title="High task"),
            _inferred("c", priority="critical", title="Critical task"),
        ],
        db_path=db_path, now=NOW,
    )
    stored = operator_task_store.list_open_tasks(db_path=db_path, now=NOW)

    md = daily_summary_module.render_summary(stored, today="2026-05-14")
    idx_crit = md.find("## Critical")
    idx_high = md.find("## High")
    assert idx_crit != -1
    assert idx_high != -1
    assert idx_crit < idx_high


def test_render_includes_approvals_section_when_approval_category_present(
    daily_summary_module, db_path: Path,
):
    operator_task_store.sync_inferred_tasks(
        [_inferred("a", category="approval", title="Approve VP-001")],
        db_path=db_path, now=NOW,
    )
    stored = operator_task_store.list_open_tasks(db_path=db_path, now=NOW)
    md = daily_summary_module.render_summary(stored, today="2026-05-14")
    assert "## Approvals waiting" in md
    assert "Approve VP-001" in md


def test_render_includes_failed_jobs_section_when_failure_category_present(
    daily_summary_module, db_path: Path,
):
    operator_task_store.sync_inferred_tasks(
        [_inferred("f", category="failure", title="Failed Kling job xyz")],
        db_path=db_path, now=NOW,
    )
    stored = operator_task_store.list_open_tasks(db_path=db_path, now=NOW)
    md = daily_summary_module.render_summary(stored, today="2026-05-14")
    assert "## Failed jobs" in md


def test_render_includes_review_section_when_review_category_present(
    daily_summary_module, db_path: Path,
):
    operator_task_store.sync_inferred_tasks(
        [_inferred("r", category="review", title="Review pitch PDF")],
        db_path=db_path, now=NOW,
    )
    stored = operator_task_store.list_open_tasks(db_path=db_path, now=NOW)
    md = daily_summary_module.render_summary(stored, today="2026-05-14")
    assert "## Review before sending" in md


def test_render_includes_ungraded_section_when_grading_category_present(
    daily_summary_module, db_path: Path,
):
    operator_task_store.sync_inferred_tasks(
        [_inferred("g", category="grading", title="Grade video xyz")],
        db_path=db_path, now=NOW,
    )
    stored = operator_task_store.list_open_tasks(db_path=db_path, now=NOW)
    md = daily_summary_module.render_summary(stored, today="2026-05-14")
    assert "## Ungraded outputs" in md


def test_render_includes_follow_ups_section_when_follow_up_category_present(
    daily_summary_module, db_path: Path,
):
    operator_task_store.sync_inferred_tasks(
        [_inferred("fu", category="follow_up", title="Stale pending job xyz")],
        db_path=db_path, now=NOW,
    )
    stored = operator_task_store.list_open_tasks(db_path=db_path, now=NOW)
    md = daily_summary_module.render_summary(stored, today="2026-05-14")
    assert "## Follow-ups" in md


# --------------------------------------------------------------------------- #
# main(): --out file path
# --------------------------------------------------------------------------- #


def test_write_to_file_creates_path_and_writes_markdown(
    daily_summary_module, tmp_path: Path,
):
    db = tmp_path / "operator_tasks.db"
    operator_task_store.sync_inferred_tasks(
        [_inferred("a", priority="critical", title="Approve VP-001")],
        db_path=db, now=NOW,
    )
    out_path = tmp_path / "reports" / "daily-summary-2026-05-14.md"

    rc = daily_summary_module.main([
        "--out", str(out_path),
        "--db-path", str(db),
        "--no-sync",  # skip the inbox scan; the store is pre-seeded
        "--today", "2026-05-14",
    ])
    assert rc == 0
    assert out_path.is_file()
    content = out_path.read_text(encoding="utf-8")
    assert "Daily summary — 2026-05-14" in content
    assert "Approve VP-001" in content


def test_main_default_prints_to_stdout(
    daily_summary_module, tmp_path: Path, capsys,
):
    db = tmp_path / "operator_tasks.db"
    operator_task_store.sync_inferred_tasks(
        [_inferred("a", priority="critical", title="Approve VP-001")],
        db_path=db, now=NOW,
    )
    rc = daily_summary_module.main([
        "--db-path", str(db),
        "--no-sync",
        "--today", "2026-05-14",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Daily summary — 2026-05-14" in captured.out
    assert "Approve VP-001" in captured.out


# --------------------------------------------------------------------------- #
# main(): no external API calls
# --------------------------------------------------------------------------- #


def test_no_external_apis_called(
    daily_summary_module, tmp_path: Path, capsys,
):
    """Pin the guarantee: daily_summary.main does not initiate network
    traffic. Patch the obvious HTTP libraries with raising stubs; if any
    code path inside main / render / sync attempts a request, the test
    fails."""
    db = tmp_path / "operator_tasks.db"
    operator_task_store.sync_inferred_tasks(
        [_inferred("a", priority="critical", title="Approve VP-001")],
        db_path=db, now=NOW,
    )

    def _boom(*args, **kwargs):
        raise AssertionError("daily_summary made an external HTTP call")

    with patch("requests.get", side_effect=_boom), \
         patch("requests.post", side_effect=_boom):
        rc = daily_summary_module.main([
            "--db-path", str(db),
            "--no-sync",
            "--today", "2026-05-14",
        ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Daily summary" in out


# --------------------------------------------------------------------------- #
# Bonus: snoozed rows do NOT appear in the daily summary
# --------------------------------------------------------------------------- #


def test_snoozed_tasks_excluded_from_summary(
    daily_summary_module, tmp_path: Path,
):
    """Practical operator expectation: 'I snoozed it for tomorrow, the
    daily summary should not yell at me about it today.' Pinned here
    because the section grouping is in daily_summary but the visibility
    rule is in operator_task_store - the contract is that
    list_open_tasks returns the right list."""
    db = tmp_path / "operator_tasks.db"
    operator_task_store.sync_inferred_tasks(
        [_inferred("snoozed-one", title="Should not appear")],
        db_path=db, now=NOW,
    )
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM operator_tasks WHERE fingerprint='snoozed-one'"
        ).fetchone()
    operator_task_store.snooze_task(
        row["id"], NOW + timedelta(days=1), db_path=db, now=NOW,
    )

    stored = operator_task_store.list_open_tasks(db_path=db, now=NOW)
    md = daily_summary_module.render_summary(stored, today="2026-05-14")
    assert "Should not appear" not in md
    assert "No open operator tasks" in md
