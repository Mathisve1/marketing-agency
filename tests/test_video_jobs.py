"""V1.4 video_jobs lifecycle + concurrent-update tests.

The whole point of moving off performance_log.json was to eliminate the
read-modify-write window where two writers (the LLM tool + the UI button)
could clobber each other. This test pins that the SQL update is atomic
under concurrent writers.
"""
from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.client_context import ClientContext
from core.context_schema import JobStatus, PlanStatus, VideoJob, VideoPlan


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def clients_root(tmp_path: Path) -> Path:
    src = REPO_ROOT / "clients" / "_template"
    dst = tmp_path / "clients" / "_template"
    shutil.copytree(src, dst)
    return tmp_path / "clients"


@pytest.fixture
def ctx(clients_root: Path) -> ClientContext:
    return ClientContext.onboard("acme", "Acme", clients_root=clients_root)


def _seed_plan(ctx: ClientContext) -> str:
    return ctx.create_video_plan(VideoPlan(
        hook_id="WH-001",
        character_asset="references/characters/c.png",
        product_asset="references/products/p.png",
        duration=10,
        aspect_ratio="9:16",
        mode="professional",
        cfg_scale=0.5,
        prompt="prompt",
        negative_prompt="neg",
        created_at=datetime.now(timezone.utc),
    ))


def _new_job(plan_id: str, task_id: str = "kling-task-001") -> VideoJob:
    return VideoJob(
        kling_task_id=task_id,
        plan_id=plan_id,
        status=JobStatus.PENDING,
        submitted_at=datetime.now(timezone.utc),
    )


def test_create_and_get_video_job(ctx: ClientContext):
    pid = _seed_plan(ctx)
    ctx.create_video_job(_new_job(pid))
    job = ctx.get_video_job("kling-task-001")
    assert job is not None
    assert job.plan_id == pid
    assert job.status == JobStatus.PENDING
    assert job.video_path is None


def test_list_pending_jobs_filters_by_status(ctx: ClientContext):
    pid = _seed_plan(ctx)
    ctx.create_video_job(_new_job(pid, task_id="t-1"))
    ctx.create_video_job(_new_job(pid, task_id="t-2"))
    ctx.update_video_job_by_task_id("t-2", status=JobStatus.COMPLETED)
    pending = ctx.list_video_jobs(status=JobStatus.PENDING)
    completed = ctx.list_video_jobs(status=JobStatus.COMPLETED)
    assert {j.kling_task_id for j in pending} == {"t-1"}
    assert {j.kling_task_id for j in completed} == {"t-2"}


def test_update_completed_writes_path(ctx: ClientContext):
    pid = _seed_plan(ctx)
    ctx.create_video_job(_new_job(pid, task_id="t-3"))
    ok = ctx.update_video_job_by_task_id(
        "t-3",
        status=JobStatus.COMPLETED,
        video_path="outputs/videos/x.mp4",
        completed_at=datetime.now(timezone.utc),
    )
    assert ok is True
    job = ctx.get_video_job("t-3")
    assert job.status == JobStatus.COMPLETED
    assert job.video_path == "outputs/videos/x.mp4"
    assert job.completed_at is not None


def test_update_failed_writes_error(ctx: ClientContext):
    pid = _seed_plan(ctx)
    ctx.create_video_job(_new_job(pid, task_id="t-4"))
    ctx.update_video_job_by_task_id(
        "t-4",
        status=JobStatus.FAILED,
        error="timed out at Kling",
        completed_at=datetime.now(timezone.utc),
    )
    job = ctx.get_video_job("t-4")
    assert job.status == JobStatus.FAILED
    assert job.error == "timed out at Kling"


def test_update_unknown_task_returns_false(ctx: ClientContext):
    assert ctx.update_video_job_by_task_id("nope", status=JobStatus.COMPLETED) is False


def test_concurrent_updates_atomic(ctx: ClientContext):
    """The whole point of moving off JSON: two writers may not lose updates.

    Sets up 50 jobs and has two threads flip them between FAILED and
    COMPLETED concurrently. After the storm, every job must have one of
    those terminal statuses (no row left as PENDING from a clobbered
    write, no SQL exception escaped).
    """
    pid = _seed_plan(ctx)
    task_ids = [f"t-conc-{i}" for i in range(50)]
    for tid in task_ids:
        ctx.create_video_job(_new_job(pid, task_id=tid))

    def hammer(target_status: JobStatus):
        for tid in task_ids:
            ctx.update_video_job_by_task_id(tid, status=target_status)

    t1 = threading.Thread(target=hammer, args=(JobStatus.COMPLETED,))
    t2 = threading.Thread(target=hammer, args=(JobStatus.FAILED,))
    t1.start(); t2.start(); t1.join(); t2.join()

    for tid in task_ids:
        job = ctx.get_video_job(tid)
        assert job is not None
        assert job.status in {JobStatus.COMPLETED, JobStatus.FAILED}, (
            f"{tid} ended up at {job.status} - SQL UPDATE was not atomic"
        )


def test_plan_links_via_kling_task_id(ctx: ClientContext):
    """Job -> plan FK lets the UI render hook/motion/asset metadata
    without duplicating those fields onto the job row."""
    pid = ctx.create_video_plan(VideoPlan(
        hook_id="WH-077",
        motion_id="RM-003",
        character_asset="references/characters/c.png",
        product_asset="references/products/p.png",
        duration=10, aspect_ratio="9:16", mode="professional", cfg_scale=0.5,
        prompt="x", negative_prompt="y",
        created_at=datetime.now(timezone.utc),
    ))
    ctx.create_video_job(_new_job(pid, task_id="t-fk"))
    job = ctx.get_video_job("t-fk")
    plan = ctx.get_video_plan(job.plan_id)
    assert plan.hook_id == "WH-077"
    assert plan.motion_id == "RM-003"
