"""Tests for services/operator_inbox.py.

Pins each inference rule against synthetic state in tmp dirs. NEVER
reads the real clients/, prospects/, mcp_pending_runs.db, or
evals/output_reviews.jsonl. NEVER calls a sub-agent or external API.

Conservative-by-design rules: a clean silo MUST produce zero tasks.
Better to surface 5 useful tasks than 30 noisy ones.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.outreach.prospect_store import ProspectAudit, ProspectStore
from core.client_context import ClientContext
from core.context_schema import (
    AddedBy,
    Confidence,
    JobStatus,
    NegativeConstraint,
    Severity,
    VideoJob,
    VideoPlan,
    WinningHook,
)
from services import mcp_pending_store, operator_inbox

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures - everything writes into tmp_path so the real repo is untouched.
# --------------------------------------------------------------------------- #


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    src = REPO_ROOT / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


@pytest.fixture
def prospects_root(tmp_path: Path) -> Path:
    return tmp_path / "prospects"


@pytest.fixture
def mcp_db(tmp_path: Path) -> Path:
    return tmp_path / "mcp_pending_runs.db"


@pytest.fixture
def eval_path(tmp_path: Path) -> Path:
    return tmp_path / "evals" / "output_reviews.jsonl"


@pytest.fixture
def acme(clients_root: Path) -> ClientContext:
    return ClientContext.onboard("acme", "Acme Corp", clients_root=clients_root)


def _new_plan(**overrides) -> VideoPlan:
    defaults = dict(
        hook_id="WH-001",
        character_asset="references/characters/c.png",
        product_asset="references/products/p.png",
        duration=10,
        aspect_ratio="9:16",
        mode="professional",
        cfg_scale=0.5,
        prompt="prompt",
        negative_prompt="neg",
        created_at=NOW,
    )
    defaults.update(overrides)
    return VideoPlan(**defaults)


def _new_job(plan_id: str, task_id: str, status: JobStatus, **kw) -> VideoJob:
    return VideoJob(
        kling_task_id=task_id,
        plan_id=plan_id,
        status=status,
        submitted_at=kw.get("submitted_at", NOW),
        completed_at=kw.get("completed_at"),
        video_path=kw.get("video_path"),
        error=kw.get("error"),
    )


def _collect(**roots) -> list[operator_inbox.OperatorTask]:
    """Helper that injects all the path overrides + the frozen NOW."""
    return operator_inbox.collect_operator_tasks(now=NOW, **roots)


# --------------------------------------------------------------------------- #
# Rule 1a: pending_approval plan -> critical approval task
# --------------------------------------------------------------------------- #


def test_pending_approval_plan_creates_critical_approval_task(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    pid = acme.create_video_plan(_new_plan())
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    matching = [t for t in tasks if t.source_id == pid and t.source_type == "video_plan"]
    assert len(matching) == 1
    t = matching[0]
    assert t.priority == "critical"
    assert t.category == "approval"
    assert t.agent == "producer"
    assert t.client_id == "acme"
    assert "approve" in t.recommended_next_action.lower()


def test_submitting_plan_with_submit_error_creates_critical_failure_task(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    pid = acme.create_video_plan(_new_plan())
    acme.claim_plan_for_submission(pid)
    acme.release_plan_after_submit_failure(pid, "TimeoutError: read timed out")
    # release_plan_after_submit_failure puts the plan back to
    # pending_approval, NOT submitting. So to test the submitting+error
    # path we need to claim again to land in the submitting state with
    # the error preserved on the row.
    acme.claim_plan_for_submission(pid)
    plan = acme.get_video_plan(pid)
    assert plan.submit_error and "Timeout" in plan.submit_error

    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    stuck = [t for t in tasks if t.id.startswith("plan-stuck:")]
    assert len(stuck) == 1
    assert stuck[0].priority == "critical"
    assert stuck[0].category == "failure"
    assert "Timeout" in stuck[0].description


def test_recently_rejected_plan_is_low_priority_follow_up(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    pid = acme.create_video_plan(_new_plan())
    acme.reject_video_plan(pid)
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    rejected = [t for t in tasks if t.id.startswith("plan-rejected:")]
    assert len(rejected) == 1
    assert rejected[0].priority == "low"
    assert rejected[0].category == "follow_up"


# --------------------------------------------------------------------------- #
# Rule 2: video_jobs
# --------------------------------------------------------------------------- #


def test_failed_video_job_creates_high_failure_task(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    pid = acme.create_video_plan(_new_plan())
    acme.create_video_job(_new_job(pid, "k-fail-1", JobStatus.FAILED, error="Kling 503"))
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    fail = [t for t in tasks if t.id.startswith("job-failed:")]
    assert len(fail) == 1
    assert fail[0].priority == "high"
    assert fail[0].category == "failure"
    assert "Kling 503" in fail[0].description


def test_stale_pending_job_creates_medium_followup(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    pid = acme.create_video_plan(_new_plan())
    long_ago = NOW - timedelta(hours=2)
    acme.create_video_job(_new_job(pid, "k-stale-1", JobStatus.PENDING, submitted_at=long_ago))
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    stale = [t for t in tasks if t.id.startswith("job-stale:")]
    assert len(stale) == 1
    assert stale[0].priority == "medium"


def test_fresh_pending_job_does_not_create_task(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    """A job submitted 2 minutes ago is the operator being impatient,
    not a real follow-up."""
    pid = acme.create_video_plan(_new_plan())
    fresh = NOW - timedelta(minutes=2)
    acme.create_video_job(_new_job(pid, "k-fresh-1", JobStatus.PENDING, submitted_at=fresh))
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    assert not any(t.id.startswith("job-stale:") for t in tasks)


def test_completed_ungraded_video_creates_grading_task(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    pid = acme.create_video_plan(_new_plan())
    acme.create_video_job(_new_job(
        pid, "k-done-1", JobStatus.COMPLETED,
        completed_at=NOW, video_path="outputs/videos/x.mp4",
    ))
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    grading = [t for t in tasks if t.id.startswith("job-ungraded:")]
    assert len(grading) == 1
    assert grading[0].category == "grading"
    assert grading[0].priority == "medium"


def test_completed_graded_video_does_not_create_task(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    """If the operator has already recorded a review for the video, no
    follow-up task should appear."""
    pid = acme.create_video_plan(_new_plan())
    acme.create_video_job(_new_job(
        pid, "k-done-graded", JobStatus.COMPLETED,
        completed_at=NOW, video_path="outputs/videos/y.mp4",
    ))
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    eval_path.write_text(json.dumps({
        "agent": "producer", "client_id": "acme",
        "source": "outputs/videos/y.mp4",
        "output_type": "kling_video", "specificity": 4,
        "accuracy": 5, "usefulness": 4, "sendable": True, "notes": "",
        "timestamp": NOW.isoformat(),
    }) + "\n", encoding="utf-8")
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    assert not any(t.id.startswith("job-ungraded:") for t in tasks)


# --------------------------------------------------------------------------- #
# Rule 3: MCP pending registry -> critical
# --------------------------------------------------------------------------- #


def test_mcp_pending_run_creates_critical_task(
    clients_root, prospects_root, mcp_db, eval_path,
):
    mcp_pending_store.record_pending(
        "mcp-acme-aaa", client_id="acme", model="claude-sonnet-4-6",
        prompt="Produce video using WH-001", task_type="produce",
        plan_id="VP-007",
        config={"configurable": {"thread_id": "mcp-acme-aaa", "model": "claude-sonnet-4-6"}},
        db_path=mcp_db,
    )
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    pending = [t for t in tasks if t.source_type == "mcp_pending_run"]
    assert len(pending) == 1
    assert pending[0].priority == "critical"
    assert pending[0].category == "approval"
    assert pending[0].client_id == "acme"
    assert "VP-007" in pending[0].description


# --------------------------------------------------------------------------- #
# Rule 4: prospects
# --------------------------------------------------------------------------- #


def _seed_prospect(prospects_root: Path, prospect_id: str, *, with_pitch: bool = False,
                   weakness_low_conf: bool = False) -> ProspectStore:
    store = ProspectStore(prospect_id, prospects_root=prospects_root)
    weaknesses: list = ["legacy weakness string"]
    if weakness_low_conf:
        weaknesses.append({
            "description": "Maybe weak audience signal",
            "evidence": ["body_text reads generic"],
            "confidence": "low",
        })
    store.save_audit(ProspectAudit(
        prospect_id=prospect_id,
        prospect_name=prospect_id.title(),
        niche="fitness", country="GB",
        weaknesses=weaknesses,
    ))
    if with_pitch:
        store.pitch_path.write_bytes(b"%PDF-1.3 fake pitch")
    return store


def test_prospect_audit_without_eval_creates_review_task(
    clients_root, prospects_root, mcp_db, eval_path,
):
    _seed_prospect(prospects_root, "gymshark")
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    audit_tasks = [t for t in tasks if t.id.startswith("prospect-audit-ungraded:")]
    assert len(audit_tasks) == 1
    assert audit_tasks[0].prospect_id == "gymshark"
    assert audit_tasks[0].priority == "medium"


def test_pitch_pdf_without_eval_creates_high_review_task(
    clients_root, prospects_root, mcp_db, eval_path,
):
    _seed_prospect(prospects_root, "on-running", with_pitch=True)
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    pitch_tasks = [t for t in tasks if t.id.startswith("prospect-pitch-unreviewed:")]
    assert len(pitch_tasks) == 1
    assert pitch_tasks[0].priority == "high"
    assert pitch_tasks[0].category == "review"


def test_prospect_with_low_confidence_weakness_creates_verify_task(
    clients_root, prospects_root, mcp_db, eval_path,
):
    _seed_prospect(prospects_root, "edge-co", weakness_low_conf=True)
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    weak = [t for t in tasks if t.id.startswith("prospect-weak-low:edge-co:")]
    assert len(weak) == 1
    assert weak[0].priority == "medium"
    assert weak[0].category == "review"


# --------------------------------------------------------------------------- #
# Rule 5: strategist + analyst grading reminders
# --------------------------------------------------------------------------- #


def test_hooks_without_strategist_eval_creates_grading_task(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    acme.add_winning_hook(WinningHook(
        pattern="P", description="D", days_active=42,
        confidence=Confidence.HIGH, added_by=AddedBy.STRATEGIST, added_at=NOW,
    ))
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    strat = [t for t in tasks if t.id == f"strategist-ungraded:{acme.client_id}"]
    assert len(strat) == 1
    assert strat[0].agent == "strategist"


def test_recent_analyst_constraint_without_eval_creates_grading_task(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    acme.add_negative_constraint(NegativeConstraint(
        rule="No alcohol depictions", reason="Q1 ROAS",
        severity=Severity.HARD, added_by=AddedBy.ANALYST, added_at=NOW,
    ))
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    analyst = [t for t in tasks if t.id == f"analyst-ungraded:{acme.client_id}"]
    assert len(analyst) == 1


# --------------------------------------------------------------------------- #
# Conservative: clean state -> zero tasks
# --------------------------------------------------------------------------- #


def test_clean_silo_produces_no_tasks(
    acme, clients_root, prospects_root, mcp_db, eval_path,
):
    """Onboarded but otherwise empty client. No plans, no jobs, no
    hooks, no constraints, no prospects, no MCP rows. Inbox MUST be
    empty - any spurious task here is a noisy false positive."""
    tasks = _collect(
        clients_root=clients_root,
        prospects_root=prospects_root,
        mcp_db_path=mcp_db,
        eval_path=eval_path,
    )
    assert tasks == []


# --------------------------------------------------------------------------- #
# collect_client_tasks scoping
# --------------------------------------------------------------------------- #


def test_collect_client_tasks_excludes_other_clients(
    clients_root, prospects_root, mcp_db, eval_path,
):
    a = ClientContext.onboard("acme", "Acme", clients_root=clients_root)
    b = ClientContext.onboard("zelda", "Zelda Co", clients_root=clients_root)
    a.create_video_plan(_new_plan(hook_id="A"))
    b.create_video_plan(_new_plan(hook_id="B"))

    only_acme = operator_inbox.collect_client_tasks(
        "acme", clients_root=clients_root, eval_path=eval_path, now=NOW,
    )
    assert all(t.client_id == "acme" for t in only_acme)
    assert len(only_acme) == 1


# --------------------------------------------------------------------------- #
# Grouping + summary helpers
# --------------------------------------------------------------------------- #


def test_group_tasks_by_priority_buckets_correctly():
    tasks = [
        operator_inbox.OperatorTask(
            id="a", priority="critical", category="approval", agent="producer",
            title="t1", description="d", source_type="x", source_id="1",
            recommended_next_action="r",
        ),
        operator_inbox.OperatorTask(
            id="b", priority="medium", category="grading", agent="producer",
            title="t2", description="d", source_type="x", source_id="2",
            recommended_next_action="r",
        ),
    ]
    grouped = operator_inbox.group_tasks_by_priority(tasks)
    assert [t.id for t in grouped["critical"]] == ["a"]
    assert [t.id for t in grouped["medium"]] == ["b"]
    assert grouped["high"] == []


def test_summarize_empty_inbox_returns_clean_message():
    md = operator_inbox.summarize_operator_tasks([])
    assert "No open operator tasks" in md


def test_summarize_renders_markdown_with_priority_sections():
    sample = [
        operator_inbox.OperatorTask(
            id="a", priority="critical", category="approval", agent="producer",
            client_id="acme", title="Approve plan VP-001",
            description="Plan awaiting approval.",
            source_type="video_plan", source_id="VP-001",
            recommended_next_action="Approve in Streamlit.",
            location_hint="Streamlit Run agent tab.",
        ),
    ]
    md = operator_inbox.summarize_operator_tasks(sample)
    assert "## Agency overview" in md
    assert "### CRITICAL" in md
    assert "Approve plan VP-001" in md
    assert "Streamlit Run agent tab." in md
