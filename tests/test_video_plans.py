"""V1.4 video_plans lifecycle tests.

The lifecycle is intentionally simple per spec:
    pending_approval -> rejected   OR
    pending_approval -> submitted

No auto-superseding; each plan_id is independently auditable.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.client_context import ClientContext
from core.context_schema import PlanStatus, VideoPlan


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


def _new_plan(**overrides) -> VideoPlan:
    defaults = dict(
        hook_id="WH-001",
        motion_id=None,
        character_asset="references/characters/c.png",
        product_asset="references/products/p.png",
        duration=10,
        aspect_ratio="9:16",
        mode="professional",
        cfg_scale=0.5,
        prompt="compiled prompt body",
        negative_prompt="universal negations",
        enforced_constraint_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return VideoPlan(**defaults)


def test_create_plan_assigns_id(ctx: ClientContext):
    pid = ctx.create_video_plan(_new_plan())
    assert pid == "VP-001"
    pid2 = ctx.create_video_plan(_new_plan())
    assert pid2 == "VP-002"


def test_get_plan_round_trip(ctx: ClientContext):
    pid = ctx.create_video_plan(_new_plan(
        enforced_constraint_ids=["NC-001", "NC-007"],
        prompt="Assets:\n- <<<image_1>>> = char\n9. layered",
    ))
    plan = ctx.get_video_plan(pid)
    assert plan is not None
    assert plan.id == pid
    assert plan.status == PlanStatus.PENDING_APPROVAL
    assert plan.enforced_constraint_ids == ["NC-001", "NC-007"]
    assert plan.prompt.startswith("Assets:")


def test_list_plans_filters_by_status(ctx: ClientContext):
    p1 = ctx.create_video_plan(_new_plan())
    p2 = ctx.create_video_plan(_new_plan())
    ctx.reject_video_plan(p1)
    pending = ctx.list_video_plans(status=PlanStatus.PENDING_APPROVAL)
    rejected = ctx.list_video_plans(status=PlanStatus.REJECTED)
    assert {p.id for p in pending} == {p2}
    assert {p.id for p in rejected} == {p1}


def test_reject_transitions_only_from_pending(ctx: ClientContext):
    pid = ctx.create_video_plan(_new_plan())
    assert ctx.reject_video_plan(pid) is True
    after = ctx.get_video_plan(pid)
    assert after.status == PlanStatus.REJECTED
    assert after.decided_at is not None
    assert after.decided_by == "human"
    # Re-rejecting an already-rejected plan must NOT silently mutate it.
    assert ctx.reject_video_plan(pid) is False


def test_mark_submitted_requires_submitting(ctx: ClientContext):
    """V1.4.1: mark_plan_submitted now requires status='submitting' (the
    claim must have happened first). A direct pending_approval ->
    submitted attempt MUST fail."""
    pid = ctx.create_video_plan(_new_plan())
    # No claim first - should be refused.
    assert ctx.mark_plan_submitted(pid) is False
    assert ctx.get_video_plan(pid).status == PlanStatus.PENDING_APPROVAL

    # Now claim, then mark - happy path.
    assert ctx.claim_plan_for_submission(pid) is True
    assert ctx.get_video_plan(pid).status == PlanStatus.SUBMITTING
    assert ctx.mark_plan_submitted(pid) is True
    assert ctx.get_video_plan(pid).status == PlanStatus.SUBMITTED

    # A rejected plan cannot be claimed nor submitted.
    p2 = ctx.create_video_plan(_new_plan())
    ctx.reject_video_plan(p2)
    assert ctx.claim_plan_for_submission(p2) is False
    assert ctx.mark_plan_submitted(p2) is False


def test_no_auto_superseding(ctx: ClientContext):
    """Per spec constraint A: each plan_id is independently auditable.
    Compiling a second plan must NOT touch the first plan's status."""
    p1 = ctx.create_video_plan(_new_plan(hook_id="WH-001"))
    p2 = ctx.create_video_plan(_new_plan(hook_id="WH-002"))
    p3 = ctx.create_video_plan(_new_plan(hook_id="WH-003"))
    for pid in (p1, p2, p3):
        assert ctx.get_video_plan(pid).status == PlanStatus.PENDING_APPROVAL


# --------------------------------------------------------------------------- #
# V1.4.1: atomic claim + failure-recording + stale-plan listing
# --------------------------------------------------------------------------- #


def test_claim_succeeds_then_blocks_double_claim(ctx: ClientContext):
    """Sequential claim attempts: first wins, second sees status=submitting
    and gets False without modifying the row."""
    pid = ctx.create_video_plan(_new_plan())
    assert ctx.claim_plan_for_submission(pid) is True
    after = ctx.get_video_plan(pid)
    assert after.status == PlanStatus.SUBMITTING
    assert after.submit_attempts == 1
    assert after.submit_attempted_at is not None

    # Second claim must fail - plan is already submitting.
    assert ctx.claim_plan_for_submission(pid) is False
    after2 = ctx.get_video_plan(pid)
    assert after2.submit_attempts == 1, "second failed claim must NOT increment counter"


def test_claim_atomic_under_concurrent_threads(ctx: ClientContext):
    """The whole point: 50 threads racing on the same plan. Exactly ONE
    must win the claim. SQLite's UPDATE serialization + the WHERE filter
    on source status guarantees this without explicit locks in Python."""
    import threading

    pid = ctx.create_video_plan(_new_plan())
    wins: list[bool] = []
    lock = threading.Lock()

    def attempt():
        won = ctx.claim_plan_for_submission(pid)
        with lock:
            wins.append(won)

    threads = [threading.Thread(target=attempt) for _ in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert wins.count(True) == 1, (
        f"exactly one thread must win the claim; got {wins.count(True)} winners"
    )
    final = ctx.get_video_plan(pid)
    assert final.status == PlanStatus.SUBMITTING
    assert final.submit_attempts == 1


def test_release_records_error_and_reverts(ctx: ClientContext):
    """release_plan_after_submit_failure: submitting -> pending_approval
    with submit_error preserved on the row."""
    pid = ctx.create_video_plan(_new_plan())
    ctx.claim_plan_for_submission(pid)
    err = "KlingAPIError: 503 service unavailable"
    assert ctx.release_plan_after_submit_failure(pid, err) is True
    after = ctx.get_video_plan(pid)
    assert after.status == PlanStatus.PENDING_APPROVAL
    assert after.submit_error == err
    assert after.submit_attempts == 1, "attempts persist across the failure"


def test_release_only_works_from_submitting(ctx: ClientContext):
    """release must NOT silently mutate a pending_approval plan (which would
    let an out-of-band caller plant a fake submit_error)."""
    pid = ctx.create_video_plan(_new_plan())
    # Never claimed - release should be a no-op.
    assert ctx.release_plan_after_submit_failure(pid, "fake") is False
    after = ctx.get_video_plan(pid)
    assert after.submit_error is None


def test_mark_submitted_clears_submit_error_on_retry_success(ctx: ClientContext):
    """A plan that failed once and succeeded on the second attempt must
    not carry the stale failure message after the success."""
    pid = ctx.create_video_plan(_new_plan())
    # First attempt fails.
    ctx.claim_plan_for_submission(pid)
    ctx.release_plan_after_submit_failure(pid, "first attempt timed out")
    assert ctx.get_video_plan(pid).submit_error == "first attempt timed out"
    # Second attempt succeeds.
    ctx.claim_plan_for_submission(pid)
    assert ctx.mark_plan_submitted(pid) is True
    final = ctx.get_video_plan(pid)
    assert final.status == PlanStatus.SUBMITTED
    assert final.submit_error is None
    assert final.submit_attempts == 2


def test_reject_works_on_submitting_too(ctx: ClientContext):
    """V1.4.1: operator may reject a stuck submitting plan via the UI
    stale-plan section. Caveat is documented in the method docstring,
    not blocked here."""
    pid = ctx.create_video_plan(_new_plan())
    ctx.claim_plan_for_submission(pid)
    assert ctx.get_video_plan(pid).status == PlanStatus.SUBMITTING
    assert ctx.reject_video_plan(pid) is True
    assert ctx.get_video_plan(pid).status == PlanStatus.REJECTED


def test_list_unresolved_returns_pending_and_submitting(ctx: ClientContext):
    """The UI stale-plan panel reads from this method."""
    p_pending = ctx.create_video_plan(_new_plan(hook_id="WH-1"))
    p_submitting = ctx.create_video_plan(_new_plan(hook_id="WH-2"))
    p_submitted = ctx.create_video_plan(_new_plan(hook_id="WH-3"))
    p_rejected = ctx.create_video_plan(_new_plan(hook_id="WH-4"))

    ctx.claim_plan_for_submission(p_submitting)  # -> submitting
    ctx.claim_plan_for_submission(p_submitted)
    ctx.mark_plan_submitted(p_submitted)         # -> submitted
    ctx.reject_video_plan(p_rejected)            # -> rejected

    unresolved = ctx.list_unresolved_video_plans()
    statuses = {p.id: p.status for p in unresolved}
    assert p_pending in statuses and statuses[p_pending] == PlanStatus.PENDING_APPROVAL
    assert p_submitting in statuses and statuses[p_submitting] == PlanStatus.SUBMITTING
    assert p_submitted not in statuses
    assert p_rejected not in statuses
