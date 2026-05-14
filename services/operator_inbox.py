"""Read-only inbox of open operator tasks, inferred from existing state.

The system already stores everything needed to know "what does the
operator have to do next" - it just stores it across many places:
video_plans, video_jobs, mcp_pending_runs, prospects/<id>/audit.json,
prospects/<id>/pitch.pdf, evals/output_reviews.jsonl, and the
client_data.db hooks/constraints. The Manager Agent needs ONE read of
all of that to answer "what do I need to approve today?".

This module is the inference layer. It does NOT write anything. It
does NOT call sub-agents. It does NOT call external APIs. Everything
is pure SQLite/JSON/filesystem reads against locations the system
already owns.

Inference is deliberately CONSERVATIVE: false negatives over false
positives. Better to surface 5 useful tasks than 30 noisy ones.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from agents.outreach.prospect_store import (
    DEFAULT_PROSPECTS_ROOT,
    ProspectStore,
)
from core.client_context import (
    DEFAULT_CLIENTS_ROOT,
    ClientContext,
    list_clients,
)
from core.context_schema import JobStatus, PlanStatus
from services import mcp_pending_store

# --------------------------------------------------------------------------- #
# Inference thresholds (tuned to be conservative)
# --------------------------------------------------------------------------- #

# How long a video_jobs row can sit in 'pending' before we surface a
# follow-up task. Below this, the operator is just impatient; above it,
# something probably needs polling.
_PENDING_JOB_STALE_AFTER = timedelta(minutes=30)

# How long a 'rejected' video plan stays in the inbox as a low-info
# follow-up. Older rejections are pure history.
_REJECTED_PLAN_RECENT_WINDOW = timedelta(hours=24)

# How long until "client X has hooks but no eval review" surfaces again.
_GRADING_REMINDER_WINDOW = timedelta(days=7)

VALID_PRIORITIES = ("critical", "high", "medium", "low")
VALID_CATEGORIES = ("approval", "failure", "review", "grading", "follow_up")
VALID_AGENTS = ("manager", "producer", "strategist", "analyst", "outreach", "system")


# --------------------------------------------------------------------------- #
# Task model
# --------------------------------------------------------------------------- #


@dataclass
class OperatorTask:
    """One unit of operator attention.

    `id` is stable across re-runs of the inbox so an external task
    tracker (if added later) can dedupe. We construct it from the
    source_type + source_id + relevant client/prospect, which is
    enough for the inferences this module makes.

    `recommended_next_action` is the short verb the operator should do
    next ("approve in Streamlit", "call resume_agency_workflow",
    "grade in tab_eval"). The Manager renders this verbatim.
    """
    id: str
    priority: str
    category: str
    agent: str
    title: str
    description: str
    source_type: str
    source_id: str
    recommended_next_action: str
    client_id: Optional[str] = None
    prospect_id: Optional[str] = None
    created_at: Optional[str] = None
    action_hint: Optional[str] = None
    location_hint: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Eval-review lookup (used by several inference rules)
# --------------------------------------------------------------------------- #


@dataclass
class _EvalIndex:
    """Lightweight index over evals/output_reviews.jsonl so each task-
    inference rule can ask "is there a review for this artifact" without
    re-reading the file."""
    rows: list[dict] = field(default_factory=list)

    def has_review(
        self,
        *,
        agent: Optional[str] = None,
        client_id: Optional[str] = None,
        prospect_id: Optional[str] = None,
        output_type: Optional[str] = None,
        source: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> bool:
        """True if any review row matches all non-None filters."""
        for r in self.rows:
            if agent is not None and r.get("agent") != agent:
                continue
            if client_id is not None and r.get("client_id") != client_id:
                continue
            if prospect_id is not None and r.get("prospect_id") != prospect_id:
                continue
            if output_type is not None and r.get("output_type") != output_type:
                continue
            if source is not None and r.get("source") != source:
                continue
            if since is not None:
                ts = r.get("timestamp") or ""
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when < since:
                    continue
            return True
        return False


def _load_eval_index(eval_path: Path) -> _EvalIndex:
    if not eval_path.exists():
        return _EvalIndex(rows=[])
    rows: list[dict] = []
    try:
        for line in eval_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip malformed rows rather than fail the whole inbox.
                continue
    except OSError:
        return _EvalIndex(rows=[])
    return _EvalIndex(rows=rows)


# --------------------------------------------------------------------------- #
# Per-source inference rules
# --------------------------------------------------------------------------- #


def _tasks_from_video_plans(
    ctx: ClientContext,
    *,
    now: datetime,
) -> list[OperatorTask]:
    out: list[OperatorTask] = []

    # Rule 1a: pending_approval -> critical approval task
    for plan in ctx.list_video_plans(status=PlanStatus.PENDING_APPROVAL):
        out.append(OperatorTask(
            id=f"plan-pending:{ctx.client_id}:{plan.id}",
            priority="critical",
            category="approval",
            agent="producer",
            client_id=ctx.client_id,
            title=f"Approve Producer plan {plan.id} for {ctx.client_id}",
            description=(
                f"Plan {plan.id} (hook={plan.hook_id}, "
                f"motion={plan.motion_id or 'none'}, duration={plan.duration}s) "
                f"is awaiting human approval before Kling submission."
            ),
            source_type="video_plan",
            source_id=plan.id,
            created_at=plan.created_at.isoformat() if plan.created_at else None,
            action_hint="approve_or_reject",
            location_hint=(
                "Streamlit: open Run agent tab and the HITL panel will "
                "render the compiled prompt. Or via MCP: find thread_id "
                "in mcp_pending_runs, call resume_agency_workflow."
            ),
            recommended_next_action="Review compiled brief, then approve or reject.",
        ))

    # Rule 1b: submitting + submit_error -> critical failure task
    for plan in ctx.list_video_plans(status=PlanStatus.SUBMITTING):
        if not plan.submit_error:
            continue
        out.append(OperatorTask(
            id=f"plan-stuck:{ctx.client_id}:{plan.id}",
            priority="critical",
            category="failure",
            agent="producer",
            client_id=ctx.client_id,
            title=f"Inspect stuck plan {plan.id} for {ctx.client_id}",
            description=(
                f"Plan {plan.id} is in 'submitting' with submit_error: "
                f"{plan.submit_error}. The provider may have accepted the "
                f"request despite the local error - verify the Kling "
                f"dashboard before re-approving to avoid duplicate spend."
            ),
            source_type="video_plan",
            source_id=plan.id,
            created_at=plan.created_at.isoformat() if plan.created_at else None,
            action_hint="manual_inspect",
            location_hint="Kling dashboard + Streamlit Performance log tab.",
            recommended_next_action=(
                "Verify with Kling, then either reject the plan in Streamlit "
                "or fix and re-approve through MCP."
            ),
        ))

    # Rule 1c: rejected -> low-priority follow-up only if recent
    cutoff = now - _REJECTED_PLAN_RECENT_WINDOW
    for plan in ctx.list_video_plans(status=PlanStatus.REJECTED, limit=20):
        if plan.decided_at is None:
            continue
        if plan.decided_at < cutoff:
            continue
        out.append(OperatorTask(
            id=f"plan-rejected:{ctx.client_id}:{plan.id}",
            priority="low",
            category="follow_up",
            agent="producer",
            client_id=ctx.client_id,
            title=f"Recently rejected plan {plan.id} ({ctx.client_id})",
            description=(
                f"Plan {plan.id} was rejected at {plan.decided_at.isoformat()}. "
                f"Consider what to compile next."
            ),
            source_type="video_plan",
            source_id=plan.id,
            created_at=plan.created_at.isoformat() if plan.created_at else None,
            action_hint="info_only",
            location_hint="Streamlit Performance log tab.",
            recommended_next_action=(
                "If still useful, re-dispatch the Producer with a refined brief."
            ),
        ))

    return out


def _tasks_from_video_jobs(
    ctx: ClientContext,
    *,
    eval_index: _EvalIndex,
    now: datetime,
) -> list[OperatorTask]:
    out: list[OperatorTask] = []

    # Rule 2a: failed -> high failure task
    for job in ctx.list_video_jobs(status=JobStatus.FAILED, limit=20):
        out.append(OperatorTask(
            id=f"job-failed:{ctx.client_id}:{job.kling_task_id}",
            priority="high",
            category="failure",
            agent="producer",
            client_id=ctx.client_id,
            title=f"Failed Kling job {job.kling_task_id} for {ctx.client_id}",
            description=(
                f"Kling reported failure for task {job.kling_task_id}. "
                f"Error: {job.error or '(none recorded)'}"
            ),
            source_type="video_job",
            source_id=job.kling_task_id,
            created_at=(
                job.completed_at.isoformat() if job.completed_at
                else job.submitted_at.isoformat()
            ),
            action_hint="manual_inspect",
            location_hint="Streamlit Generated videos tab (Failed jobs section).",
            recommended_next_action=(
                "Read the error, decide whether to dispatch a fresh Producer run."
            ),
        ))

    # Rule 2b: pending older than threshold -> medium follow-up
    stale_cutoff = now - _PENDING_JOB_STALE_AFTER
    for job in ctx.list_video_jobs(status=JobStatus.PENDING, limit=50):
        if job.submitted_at >= stale_cutoff:
            continue
        out.append(OperatorTask(
            id=f"job-stale:{ctx.client_id}:{job.kling_task_id}",
            priority="medium",
            category="follow_up",
            agent="producer",
            client_id=ctx.client_id,
            title=f"Stale pending job {job.kling_task_id} for {ctx.client_id}",
            description=(
                f"Kling task {job.kling_task_id} has been 'pending' since "
                f"{job.submitted_at.isoformat()}. Likely needs a status poll."
            ),
            source_type="video_job",
            source_id=job.kling_task_id,
            created_at=job.submitted_at.isoformat(),
            action_hint="poll_status",
            location_hint="Streamlit Generated videos tab (Pending Kling tasks).",
            recommended_next_action="Click 'Check Status' in the Streamlit panel.",
        ))

    # Rule 2c: completed but no eval review -> medium grading task
    for job in ctx.list_video_jobs(status=JobStatus.COMPLETED, limit=50):
        if not job.video_path:
            continue
        if eval_index.has_review(
            agent="producer",
            client_id=ctx.client_id,
            source=job.video_path,
        ):
            continue
        out.append(OperatorTask(
            id=f"job-ungraded:{ctx.client_id}:{job.kling_task_id}",
            priority="medium",
            category="grading",
            agent="producer",
            client_id=ctx.client_id,
            title=f"Grade video {job.kling_task_id} for {ctx.client_id}",
            description=(
                f"Completed Kling video at {job.video_path} has no quality "
                f"grade recorded in evals/output_reviews.jsonl yet."
            ),
            source_type="video_job",
            source_id=job.kling_task_id,
            created_at=(
                job.completed_at.isoformat() if job.completed_at else None
            ),
            action_hint="grade_output",
            location_hint=f"Streamlit Grade output tab (source={job.video_path}).",
            recommended_next_action=(
                "Open the Grade output tab and record specificity / accuracy "
                "/ usefulness / sendable for this video."
            ),
        ))

    return out


def _tasks_from_mcp_pending(*, mcp_db_path: Optional[Path]) -> list[OperatorTask]:
    out: list[OperatorTask] = []
    pending = (
        mcp_pending_store.list_pending(db_path=mcp_db_path)
        if mcp_db_path is not None
        else mcp_pending_store.list_pending()
    )
    for row in pending:
        thread_id = row.get("thread_id") or "(unknown thread)"
        client_id = row.get("client_id")
        plan_id = row.get("plan_id")
        out.append(OperatorTask(
            id=f"mcp-pending:{thread_id}",
            priority="critical",
            category="approval",
            agent="manager",
            client_id=client_id,
            title=f"Resume or reject paused MCP workflow ({thread_id})",
            description=(
                f"MCP-dispatched workflow paused for HITL approval. "
                f"client_id={client_id}, plan_id={plan_id}, "
                f"task_type={row.get('task_type')}."
            ),
            source_type="mcp_pending_run",
            source_id=thread_id,
            created_at=row.get("created_at"),
            action_hint="approve_or_reject",
            location_hint=(
                f"MCP: resume_agency_workflow(thread_id='{thread_id}', "
                f"approve=True/False)."
            ),
            recommended_next_action=(
                "Decide approve or reject. Approve will spend Kling credits."
            ),
        ))
    return out


def _tasks_from_prospects(
    *,
    prospects_root: Path,
    eval_index: _EvalIndex,
) -> list[OperatorTask]:
    out: list[OperatorTask] = []
    if not prospects_root.exists():
        return out

    for prospect_id in ProspectStore.list_prospects(prospects_root=prospects_root):
        store = ProspectStore(prospect_id, prospects_root=prospects_root)
        audit = store.read_audit()
        if audit is None:
            # Prospect folder exists but no audit JSON - probably an in-flight
            # outreach run. Don't surface as a task; would be noisy.
            continue

        # Rule 4a: audit exists and no eval review -> medium task
        if not eval_index.has_review(
            agent="outreach",
            prospect_id=prospect_id,
            output_type="audit_json",
        ):
            out.append(OperatorTask(
                id=f"prospect-audit-ungraded:{prospect_id}",
                priority="medium",
                category="review",
                agent="outreach",
                prospect_id=prospect_id,
                title=f"Review outreach audit for {prospect_id}",
                description=(
                    f"Audit JSON exists at prospects/{prospect_id}/audit.json "
                    f"but has no quality grade. Review the strategic gaps + "
                    f"candidate hooks before promoting or sending the pitch."
                ),
                source_type="prospect_audit",
                source_id=prospect_id,
                created_at=audit.audited_at or None,
                action_hint="grade_output",
                location_hint=(
                    f"prospects/{prospect_id}/audit.json + Streamlit "
                    f"Grade output tab (set agent=outreach)."
                ),
                recommended_next_action=(
                    "Read audit.json, then grade in Streamlit."
                ),
            ))

        # Rule 4b: pitch.pdf exists and no sendable evaluation -> high
        if store.pitch_path.exists():
            if not eval_index.has_review(
                output_type="pitch_pdf",
                prospect_id=prospect_id,
            ):
                out.append(OperatorTask(
                    id=f"prospect-pitch-unreviewed:{prospect_id}",
                    priority="high",
                    category="review",
                    agent="outreach",
                    prospect_id=prospect_id,
                    title=f"Review pitch PDF for {prospect_id} before sending",
                    description=(
                        f"Pitch PDF exists at prospects/{prospect_id}/pitch.pdf "
                        f"but has no sendability grade. Do not cold-send "
                        f"unreviewed."
                    ),
                    source_type="prospect_pitch",
                    source_id=prospect_id,
                    action_hint="grade_output",
                    location_hint=str(store.pitch_path),
                    recommended_next_action=(
                        "Open the PDF, then grade with sendable=yes/no in "
                        "Streamlit Grade output tab."
                    ),
                ))

        # Rule 4c: low-confidence weakness -> medium verify task
        for w in audit.weaknesses:
            if isinstance(w, dict) and (w.get("confidence") or "").lower() == "low":
                out.append(OperatorTask(
                    id=(
                        f"prospect-weak-low:{prospect_id}:"
                        f"{(w.get('description') or '')[:40]}"
                    ),
                    priority="medium",
                    category="review",
                    agent="outreach",
                    prospect_id=prospect_id,
                    title=f"Verify low-confidence weakness for {prospect_id}",
                    description=(
                        f"Weakness has confidence='low': "
                        f"{w.get('description') or '(no description)'}"
                    ),
                    source_type="prospect_audit",
                    source_id=prospect_id,
                    action_hint="manual_verify",
                    location_hint=str(store.audit_path),
                    recommended_next_action=(
                        "Verify the claim in the original ad library, then "
                        "either upgrade confidence or drop the claim before "
                        "sending the pitch."
                    ),
                ))
                break  # one task per prospect even if multiple low-confidence

    return out


def _tasks_from_strategist_analyst(
    ctx: ClientContext,
    *,
    eval_index: _EvalIndex,
    now: datetime,
) -> list[OperatorTask]:
    out: list[OperatorTask] = []
    cutoff = now - _GRADING_REMINDER_WINDOW

    # Rule 5a: hooks exist + no recent strategist eval -> medium grading
    hooks = ctx.get_winning_hooks()
    if hooks and not eval_index.has_review(
        agent="strategist", client_id=ctx.client_id, since=cutoff,
    ):
        out.append(OperatorTask(
            id=f"strategist-ungraded:{ctx.client_id}",
            priority="medium",
            category="grading",
            agent="strategist",
            client_id=ctx.client_id,
            title=f"Grade Strategist candidate hooks for {ctx.client_id}",
            description=(
                f"{len(hooks)} candidate hook(s) recorded; no Strategist "
                f"eval review in the last {_GRADING_REMINDER_WINDOW.days} days."
            ),
            source_type="strategist_output",
            source_id=ctx.client_id,
            action_hint="grade_output",
            location_hint="Streamlit Grade output tab (set agent=strategist).",
            recommended_next_action=(
                "Skim the hooks, grade specificity/accuracy/usefulness."
            ),
        ))

    # Rule 5b: recent analyst-added negative constraints + no analyst eval
    constraints = ctx.get_negative_constraints()
    recent_analyst = [
        c for c in constraints
        if c.added_by.value == "analyst" and c.added_at >= cutoff
    ]
    if recent_analyst and not eval_index.has_review(
        agent="analyst", client_id=ctx.client_id, since=cutoff,
    ):
        out.append(OperatorTask(
            id=f"analyst-ungraded:{ctx.client_id}",
            priority="medium",
            category="grading",
            agent="analyst",
            client_id=ctx.client_id,
            title=f"Review Analyst recommendations for {ctx.client_id}",
            description=(
                f"{len(recent_analyst)} negative constraint(s) added by the "
                f"Analyst in the last {_GRADING_REMINDER_WINDOW.days} days; "
                f"no Analyst eval review in the same window."
            ),
            source_type="analyst_output",
            source_id=ctx.client_id,
            action_hint="grade_output",
            location_hint="Streamlit Negative constraints tab + Grade output tab.",
            recommended_next_action=(
                "Read the new constraints + grade Analyst usefulness."
            ),
        ))

    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


_PRIORITY_ORDER = {p: i for i, p in enumerate(VALID_PRIORITIES)}


def _sort_key(task: OperatorTask) -> tuple:
    return (_PRIORITY_ORDER.get(task.priority, 99), task.client_id or "", task.title)


def collect_operator_tasks(
    *,
    clients_root: Optional[Path] = None,
    prospects_root: Optional[Path] = None,
    mcp_db_path: Optional[Path] = None,
    eval_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> list[OperatorTask]:
    """Scan all clients + prospects + MCP pending registry + eval log
    and return inferred operator tasks, critical first.

    Read-only. No agent run. No external API call. Cheap at current
    scale (a handful of clients) - re-scans on every call rather than
    caching.
    """
    if clients_root is None:
        clients_root = DEFAULT_CLIENTS_ROOT
    if prospects_root is None:
        prospects_root = DEFAULT_PROSPECTS_ROOT
    if eval_path is None:
        # Local import to avoid circular at module-load time.
        from scripts.eval_review import DEFAULT_EVAL_PATH  # type: ignore
        eval_path = DEFAULT_EVAL_PATH
    if now is None:
        now = datetime.now(timezone.utc)

    eval_index = _load_eval_index(eval_path)
    out: list[OperatorTask] = []

    out.extend(_tasks_from_mcp_pending(mcp_db_path=mcp_db_path))

    for client_id in list_clients(clients_root=clients_root):
        try:
            ctx = ClientContext.load(client_id, clients_root=clients_root)
        except Exception:
            # Broken silo - surface as a system task rather than crash.
            out.append(OperatorTask(
                id=f"system-bad-silo:{client_id}",
                priority="high",
                category="failure",
                agent="system",
                client_id=client_id,
                title=f"Could not load client silo {client_id}",
                description="ClientContext.load failed; check the silo on disk.",
                source_type="client_silo",
                source_id=client_id,
                action_hint="manual_inspect",
                location_hint=str((clients_root / client_id).resolve()),
                recommended_next_action="Inspect the silo manually; run scripts/doctor.py.",
            ))
            continue
        out.extend(_tasks_from_video_plans(ctx, now=now))
        out.extend(_tasks_from_video_jobs(ctx, eval_index=eval_index, now=now))
        out.extend(_tasks_from_strategist_analyst(ctx, eval_index=eval_index, now=now))

    out.extend(_tasks_from_prospects(prospects_root=prospects_root, eval_index=eval_index))

    out.sort(key=_sort_key)
    return out


def collect_client_tasks(
    client_id: str,
    *,
    clients_root: Optional[Path] = None,
    eval_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> list[OperatorTask]:
    """Per-client subset - excludes prospects + MCP rows for other clients."""
    if clients_root is None:
        clients_root = DEFAULT_CLIENTS_ROOT
    if eval_path is None:
        from scripts.eval_review import DEFAULT_EVAL_PATH  # type: ignore
        eval_path = DEFAULT_EVAL_PATH
    if now is None:
        now = datetime.now(timezone.utc)

    eval_index = _load_eval_index(eval_path)
    try:
        ctx = ClientContext.load(client_id, clients_root=clients_root)
    except Exception as e:
        return [OperatorTask(
            id=f"system-bad-silo:{client_id}",
            priority="high",
            category="failure",
            agent="system",
            client_id=client_id,
            title=f"Could not load client silo {client_id}",
            description=f"ClientContext.load failed: {e}",
            source_type="client_silo",
            source_id=client_id,
            action_hint="manual_inspect",
            location_hint=str((clients_root / client_id).resolve()),
            recommended_next_action="Inspect the silo manually; run scripts/doctor.py.",
        )]

    out: list[OperatorTask] = []
    out.extend(_tasks_from_video_plans(ctx, now=now))
    out.extend(_tasks_from_video_jobs(ctx, eval_index=eval_index, now=now))
    out.extend(_tasks_from_strategist_analyst(ctx, eval_index=eval_index, now=now))
    out.sort(key=_sort_key)
    return out


# --------------------------------------------------------------------------- #
# Grouping + summary helpers
# --------------------------------------------------------------------------- #


def group_tasks_by_priority(
    tasks: list[OperatorTask],
) -> dict[str, list[OperatorTask]]:
    out: dict[str, list[OperatorTask]] = {p: [] for p in VALID_PRIORITIES}
    for t in tasks:
        out.setdefault(t.priority, []).append(t)
    return out


def group_tasks_by_client(
    tasks: list[OperatorTask],
) -> dict[Optional[str], list[OperatorTask]]:
    """Tasks with no client_id (e.g. prospect tasks) live under the None key."""
    out: dict[Optional[str], list[OperatorTask]] = {}
    for t in tasks:
        out.setdefault(t.client_id, []).append(t)
    return out


def summarize_operator_tasks(tasks: list[OperatorTask]) -> str:
    """Operator-facing markdown. Critical first, then grouped by client/prospect."""
    if not tasks:
        return (
            "## Agency overview\n\n"
            "**No open operator tasks.** Inbox is clean. "
            "Dispatch a new agent run when ready."
        )

    by_priority = group_tasks_by_priority(tasks)
    lines = [
        "## Agency overview",
        "",
        f"**{len(tasks)} open task(s).** Critical first.",
        "",
    ]

    for priority in VALID_PRIORITIES:
        bucket = by_priority.get(priority) or []
        if not bucket:
            continue
        lines.append(f"### {priority.upper()}  ({len(bucket)})")
        for t in bucket:
            scope = (
                f"client `{t.client_id}`"
                if t.client_id else (
                    f"prospect `{t.prospect_id}`" if t.prospect_id else "system"
                )
            )
            lines.append(f"- **{t.title}**  _(scope: {scope}, agent: {t.agent})_")
            lines.append(f"  - {t.description}")
            if t.location_hint:
                lines.append(f"  - **Where:** {t.location_hint}")
            lines.append(f"  - **Next:** {t.recommended_next_action}")
        lines.append("")

    return "\n".join(lines)
