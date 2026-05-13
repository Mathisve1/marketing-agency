"""Producer worker - V1.4 plan/submit split.

The Producer is now TWO LangGraph nodes with a HITL pause between:

  producer_plan_node    LLM ReAct agent with tools that ONLY plan.
                        compile_video_plan persists a deterministic Kling
                        brief into the video_plans SQL table with
                        status='pending_approval' and returns the plan_id.
                        NO tool in this node calls Kling. Cannot spend money.
                        Sets state['plan_id'] from the tool's last result.

                        check_video_status is also exposed here so the LLM
                        can poll a previously-submitted job (downloads MP4
                        on completion). This call is not paid Kling spend
                        - it's a poll + bandwidth.

  ----------- HITL PAUSE: interrupt_before=['producer_submit'] -----------
  Operator reviews the EXACT compiled brief (hook, motion, asset paths,
  full prompt, negative_prompt, duration, aspect_ratio, mode). Approving
  resumes the graph; rejecting cancels and the plan stays pending forever
  (audit-friendly per the V1.4 lifecycle: pending_approval -> rejected
  OR submitted, no auto-superseding).

  producer_submit_node  Pure Python (no LLM). Reads the plan from SQL,
                        submits to Kling, creates the video_jobs row,
                        marks the plan submitted. ONE submission per turn
                        - if the LLM compiled multiple plans, only the
                        last one in state['plan_id'] gets submitted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from agents.producer.brief_compiler import compile_brief
from agents.producer.kling.client import KlingClient
from core.client_context import ClientContext
from core.context_schema import JobStatus, VideoJob, VideoPlan
from core.models import SUPPORTED_MODEL_IDS, validate_model_id
from core.state import AgentState


# Kling task statuses still considered "in flight" (mirrors kling/client.py).
_NON_TERMINAL_STATUSES = {"pending", "processing", "queued", "running", "created", "submitted"}

# V1.4 hard cap: at most ONE plan compiled per planner turn. The HITL gate
# inspects exactly one plan before approval, and producer_submit submits
# exactly one plan. If the LLM tries to compile a second plan in the same
# turn, it gets the SAFETY LIMIT string and must stop. To create more
# variations, the operator submits multiple supervisor turns - each with
# its own approval.
MAX_PLANS_PER_TURN = 1


# --------------------------------------------------------------------------- #
# Plan formatter - shared by Streamlit HITL panel + MCP pause message
# --------------------------------------------------------------------------- #


def format_plan_summary(plan: VideoPlan) -> str:
    """Single source of truth for rendering a plan to text. Used by the MCP
    pause message and (indirectly via tested fields) the Streamlit HITL
    panel. Keep deterministic - a regression test pins the included keys."""
    lines = [
        f"Plan ID:         {plan.id}",
        f"Status:          {plan.status.value}",
        f"Hook:            {plan.hook_id}",
        f"Motion:          {plan.motion_id or '(none - images-only generation)'}",
        f"Character asset: {plan.character_asset}",
        f"Product asset:   {plan.product_asset}",
        f"Duration:        {plan.duration}s",
        f"Aspect ratio:    {plan.aspect_ratio}",
        f"Mode:            {plan.mode}",
        f"cfg_scale:       {plan.cfg_scale}",
        f"Enforced HARD constraints: {plan.enforced_constraint_ids or '(none)'}",
        "",
        "--- Compiled Kling prompt ---",
        plan.prompt,
        "",
        "--- Negative prompt ---",
        plan.negative_prompt,
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tools for the planner LLM
# --------------------------------------------------------------------------- #


def _build_planner_tools(ctx: ClientContext, kling_client: KlingClient):
    """Closure-based tools. The LLM never threads client_id through args.

    No tool here calls Kling.submit_omni_video. compile_video_plan only
    persists; check_video_status only polls + (on completion) downloads.
    """

    # Per-turn counter - resets every fresh producer_plan_node invocation
    # since each invocation creates a new closure.
    _plans_compiled_this_turn = 0

    @tool("list_available_assets")
    def list_available_assets(kind: str) -> list[str]:
        """List reference assets in the client silo. `kind` must be one of
        'characters', 'products', 'referral_videos'. Returns relative paths."""
        if kind not in {"characters", "products", "referral_videos"}:
            raise ValueError(
                f"Invalid kind {kind!r}; must be 'characters', 'products', or 'referral_videos'."
            )
        return [str(p.relative_to(ctx.root)) for p in ctx.list_assets(kind)]

    @tool("read_master_context")
    def read_master_context() -> dict:
        """Return winning_hooks, referral_motions, negative_constraints (all
        from client_data.db) and brand (from MASTER_CONTEXT.md) so you can
        pick what to combine in compile_video_plan."""
        fm, _ = ctx.read()
        return {
            "winning_hooks": [
                h.model_dump(mode="json") for h in ctx.get_winning_hooks()
            ],
            "referral_motions": [
                m.model_dump(mode="json") for m in ctx.get_referral_motions()
            ],
            "negative_constraints": [
                c.model_dump(mode="json") for c in ctx.get_negative_constraints()
            ],
            "brand": fm.brand.model_dump(mode="json"),
        }

    @tool("compile_video_plan")
    def compile_video_plan(
        hook_id: str,
        character_asset: str,
        product_asset: str,
        motion_id: Optional[str] = None,
        duration: int = 10,
    ) -> str:
        """Compile a deterministic Kling brief and persist it as a plan
        awaiting human approval. RETURNS the plan_id and a summary; does
        NOT submit anything to Kling.

        After this tool runs, the planner node ends and the graph pauses
        for human review. The operator approves the compiled brief in the
        Streamlit UI or via the MCP resume tool. Only after approval does
        the producer_submit node call Kling.

        Use motion_id when a strong referral motion exists (the Omni model
        will inherit pacing + camera). Omit motion_id for character +
        product images alone.
        """
        nonlocal _plans_compiled_this_turn
        # V1.4 circuit breaker: at most one plan per planner turn. If the
        # LLM hallucinates a loop, the second call returns SAFETY LIMIT
        # and the LLM is told to stop and report the existing plan_id.
        if _plans_compiled_this_turn >= MAX_PLANS_PER_TURN:
            return (
                f"SAFETY LIMIT: Maximum of {MAX_PLANS_PER_TURN} plan(s) per "
                f"planner turn. STOP calling compile_video_plan. Report the "
                f"existing plan to the user; if they want another variation, "
                f"they must submit a fresh request."
            )

        # ---- Validate inputs against client silo ----
        hook = ctx.get_winning_hook(hook_id)
        if hook is None:
            return f"ERROR: hook_id {hook_id!r} not found in winning_hooks."

        motion = None
        if motion_id:
            motion = ctx.get_referral_motion(motion_id)
            if motion is None:
                return f"ERROR: motion_id {motion_id!r} not found in referral_motions."

        char_path = (ctx.root / character_asset).resolve()
        prod_path = (ctx.root / product_asset).resolve()
        if not char_path.exists():
            return f"ERROR: character asset not found at {character_asset}"
        if not prod_path.exists():
            return f"ERROR: product asset not found at {product_asset}"

        # ---- Deterministic compile ----
        fm, _ = ctx.read()
        brief = compile_brief(
            hook=hook,
            motion=motion,
            character_image_path=char_path,
            product_image_path=prod_path,
            negative_constraints=ctx.get_negative_constraints(),
            brand=fm.brand,
            duration=duration,
        )

        # ---- Persist as pending_approval ----
        plan = VideoPlan(
            hook_id=hook_id,
            motion_id=motion_id,
            character_asset=character_asset,
            product_asset=product_asset,
            duration=brief.duration,
            aspect_ratio=brief.aspect_ratio,
            mode=brief.mode,
            cfg_scale=brief.cfg_scale,
            prompt=brief.prompt,
            negative_prompt=brief.negative_prompt,
            enforced_constraint_ids=list(brief.enforced_constraint_ids),
            created_at=datetime.now(timezone.utc),
        )
        plan_id = ctx.create_video_plan(plan)
        _plans_compiled_this_turn += 1

        return (
            f"PLAN_COMPILED plan_id={plan_id} "
            f"hook={hook_id} motion={motion_id or 'none'} "
            f"duration={brief.duration}s aspect={brief.aspect_ratio} "
            f"prompt_chars={len(brief.prompt)} "
            f"hard_constraints_enforced={len(brief.enforced_constraint_ids)}. "
            f"The graph will now PAUSE for human approval. STOP calling tools "
            f"and tell the user the plan_id is ready for review."
        )

    @tool("check_video_status")
    def check_video_status(task_id: str) -> str:
        """Single-shot poll of a previously-submitted Kling task.

        Three terminal possibilities:
          1. Still rendering  -> returns a 'wait and retry' status string.
          2. Failed           -> updates the video_jobs row to status='failed'
                                 and returns the error.
          3. Completed        -> downloads the MP4 to outputs/videos/,
                                 updates the video_jobs row to
                                 status='completed' with video_path, and
                                 returns the absolute local file path.

        This is NOT a Kling submission - no new credits are spent. It only
        polls and (on completion) downloads. Safe to call from the planner.
        """
        task = kling_client.poll_task(task_id)
        data = task.get("data") or task
        status = str(data.get("task_status") or data.get("status") or "pending").lower()

        if status in _NON_TERMINAL_STATUSES:
            return (
                f"Task {task_id} still rendering (status: {status}). "
                f"Wait 30-60 seconds and call check_video_status('{task_id}') again."
            )

        if status in {"failed", "error"}:
            err = data.get("error") or data.get("message") or "unknown failure"
            ctx.update_video_job_by_task_id(
                task_id,
                status=JobStatus.FAILED,
                error=str(err),
                completed_at=datetime.now(timezone.utc),
            )
            return f"Task {task_id} FAILED: {err}"

        # Terminal success: pull plan/hook/motion off the parent plan row
        # so the downloaded filename keeps the agency naming convention.
        job = ctx.get_video_job(task_id)
        plan = ctx.get_video_plan(job.plan_id) if job else None
        hook_id = plan.hook_id if plan else "unknown"
        motion_id = plan.motion_id if plan else None

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"{hook_id}-{motion_id or 'images'}-{timestamp}.mp4"
        dest = ctx.root / "outputs" / "videos" / name
        kling_client.download_video(task, dest)

        rel_path = str(dest.relative_to(ctx.root))
        ctx.update_video_job_by_task_id(
            task_id,
            status=JobStatus.COMPLETED,
            video_path=rel_path,
            completed_at=datetime.now(timezone.utc),
        )
        return str(dest.resolve())

    return [
        list_available_assets,
        read_master_context,
        compile_video_plan,
        check_video_status,
    ]


# --------------------------------------------------------------------------- #
# System prompt for the planner
# --------------------------------------------------------------------------- #


PLANNER_SYSTEM_PROMPT = """You are the Producer-Planner for an AI performance marketing agency.

Client: {client_name}  (locale: {client_locale})
Brand context: {brand_context}

Inventory: {hooks_count} winning hooks, {motions_count} referral motions,
{constraints_count} negative constraints active.

You can do EXACTLY ONE of these per turn:

  (A) COMPILE A PLAN for a NEW video. Sequence:
      1. Call read_master_context to see winning_hooks, referral_motions,
         negative_constraints.
      2. Call list_available_assets for 'characters' and 'products' to see
         what reference imagery is in the client silo.
      3. Call compile_video_plan ONCE with the chosen hook_id,
         character_asset, product_asset, optional motion_id, duration.
         This persists the EXACT compiled Kling brief into the video_plans
         table with status='pending_approval'. The plan ID looks like VP-NNN.
      4. Tell the user the plan_id and STOP. The graph will pause and a
         human reviews the exact prompt before any Kling spend.

  (B) CHECK STATUS of a previously-submitted Kling task by ID.
      Call check_video_status(task_id). Report the result.

CRITICAL: compile_video_plan does NOT submit to Kling. The submission only
happens AFTER a human approves the compiled plan in the UI / MCP. You
cannot trigger paid spend from this turn.

CRITICAL: at most ONE compile_video_plan call per turn (enforced in code).
If the user wants multiple variations, instruct them to submit one
supervisor request per variation so each gets its own approval gate."""


# --------------------------------------------------------------------------- #
# Node 1: planner (LLM)
# --------------------------------------------------------------------------- #


def _format_brand_context(fm) -> str:
    parts: list[str] = []
    if fm.brand.voice_attributes:
        parts.append(f"Voice: {', '.join(fm.brand.voice_attributes)}")
    if fm.brand.primary_products:
        parts.append(f"Products: {', '.join(fm.brand.primary_products)}")
    if fm.brand.forbidden_terms:
        parts.append(f"Forbidden: {', '.join(fm.brand.forbidden_terms)}")
    return " | ".join(parts) if parts else "(none recorded)"


def _extract_plan_id_from_messages(new_messages) -> Optional[str]:
    """Scan tool ToolMessages for a compile_video_plan result. The success
    string starts with 'PLAN_COMPILED plan_id=...'. Returns the LAST plan_id
    found (defensive in case the LLM somehow called the tool more than once
    despite the SAFETY LIMIT)."""
    found: Optional[str] = None
    for msg in new_messages:
        if not hasattr(msg, "name") or msg.name != "compile_video_plan":
            continue
        content = str(msg.content)
        if not content.startswith("PLAN_COMPILED"):
            continue
        # Extract the plan_id token: "PLAN_COMPILED plan_id=VP-001 ..."
        for token in content.split():
            if token.startswith("plan_id="):
                found = token.split("=", 1)[1]
    return found


def producer_plan_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """LLM ReAct agent. Compiles at most one plan; never calls Kling."""
    model_name = (config or {}).get("configurable", {}).get("model")
    if not model_name:
        raise ValueError(
            "Model not selected. The Producer requires an explicit model "
            "choice per run via config['configurable']['model']. "
            f"Supported: {', '.join(SUPPORTED_MODEL_IDS)}."
        )
    validate_model_id(model_name)

    ctx = ClientContext.load(state["client_id"])
    fm, _ = ctx.read()

    audit_log = (ctx.clients_root.parent / "logs" / "kling-api.jsonl").resolve()
    kling_client = KlingClient(audit_log_path=audit_log)

    tools = _build_planner_tools(ctx, kling_client)

    llm = ChatAnthropic(model=model_name, max_tokens=4096, temperature=0.2)
    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        client_name=fm.client.name,
        client_locale=fm.client.locale,
        brand_context=_format_brand_context(fm),
        hooks_count=len(ctx.get_winning_hooks()),
        motions_count=len(ctx.get_referral_motions()),
        constraints_count=len(ctx.get_negative_constraints()),
    )

    react = create_react_agent(llm, tools, prompt=system_prompt)
    result = react.invoke({"messages": state["messages"]})
    new_messages = result["messages"][len(state["messages"]):]

    # ---- artifact extraction ----
    completed_videos: list[str] = []
    status_messages: list[str] = []
    for msg in new_messages:
        if not hasattr(msg, "name") or msg.name != "check_video_status":
            continue
        content = str(msg.content)
        candidate = Path(content)
        if candidate.suffix == ".mp4" and candidate.exists():
            completed_videos.append(content)
        else:
            status_messages.append(content)

    plan_id = _extract_plan_id_from_messages(new_messages)

    artifacts = dict(state.get("artifacts") or {})
    artifacts["model_used"] = model_name
    if plan_id:
        artifacts["producer_plan_id"] = plan_id
    if completed_videos:
        artifacts["producer_videos"] = completed_videos
    if status_messages:
        artifacts["producer_status_checks"] = status_messages

    return {
        "messages": new_messages,
        "current_agent": "producer_plan",
        "artifacts": artifacts,
        "plan_id": plan_id,  # None if no plan was compiled this turn
    }


# --------------------------------------------------------------------------- #
# Node 2: submitter (pure Python)
# --------------------------------------------------------------------------- #


# Substrings that strongly suggest the request MAY have reached Kling
# even though our process didn't see a clean response. If any of these
# appear in the exception or its type name, the operator gets a duplicate-
# submission warning when the failure is recorded.
_TIMEOUT_INDICATORS = ("timeout", "timed out", "connectionerror", "readtimeout",
                       "connecttimeout", "remotedisconnected", "incompleteread")


def _classify_kling_failure(exc: BaseException) -> tuple[str, bool]:
    """Returns (error_string_with_warning, is_ambiguous_timeout)."""
    base = f"{type(exc).__name__}: {exc}"
    needle = (type(exc).__name__ + " " + str(exc)).lower()
    is_timeout = any(t in needle for t in _TIMEOUT_INDICATORS)
    if is_timeout:
        base += (
            " | TIMEOUT_WARNING: Kling MAY have accepted this submission "
            "despite the local error. Before re-approving this plan, check "
            "the Kling dashboard / kling-api.jsonl audit log to confirm no "
            "task was actually created. Re-approval will create a duplicate "
            "submission if the original was accepted."
        )
    return base, is_timeout


def producer_submit_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Pure Python. Atomic-claims the plan, then submits to Kling exactly once.

    Reached only when state['plan_id'] is set AND the human resumed past
    the interrupt_before=['producer_submit'] gate.

    Lifecycle (V1.4.1):
      pending_approval ──claim──▶ submitting ──Kling success──▶ submitted
                                            │
                                            └──Kling failure──▶ pending_approval
                                              (with submit_error preserved
                                               and submit_attempts incremented)

    The claim step is the cost-control invariant: if the atomic CAS
    transition pending_approval -> submitting affects 0 rows, we know
    another process either (a) already won the claim and is about to
    spend, or (b) the plan is in some non-claimable state (rejected,
    submitted, missing). Either way we MUST refuse to call Kling.
    """
    from langchain_core.messages import AIMessage  # local import - avoids
                                                    # cycle when planner-only
                                                    # users import the module

    plan_id = state.get("plan_id")
    if not plan_id:
        return {
            "messages": [AIMessage(content="ERROR: producer_submit reached without plan_id.")],
            "current_agent": "producer_submit",
            "error": "producer_submit reached without plan_id",
        }

    ctx = ClientContext.load(state["client_id"])

    # ---- ATOMIC CLAIM (the cost-control invariant) ----
    # MUST succeed before any Kling call. If 0 rows affected, another
    # process won the race or the plan is in a non-claimable state.
    claimed = ctx.claim_plan_for_submission(plan_id, claimed_by="human")
    if not claimed:
        plan_now = ctx.get_video_plan(plan_id)
        actual = plan_now.status.value if plan_now else "(missing)"
        return {
            "messages": [AIMessage(
                content=(
                    f"REFUSED: could not claim plan {plan_id} for submission. "
                    f"Current status: {actual!r}. Either another submitter "
                    f"already claimed this plan, or it has been "
                    f"rejected/submitted. NO Kling call was made."
                )
            )],
            "current_agent": "producer_submit",
            "error": f"could not claim plan {plan_id} (status={actual})",
        }

    # We hold the claim. Re-read the plan to get the now-current row.
    plan = ctx.get_video_plan(plan_id)
    if plan is None:
        # Vanishingly rare: claimed a plan that disappeared. Best we can do.
        return {
            "messages": [AIMessage(content=f"ERROR: plan {plan_id} vanished after claim.")],
            "current_agent": "producer_submit",
            "error": f"plan {plan_id} vanished after claim",
        }

    char_path = (ctx.root / plan.character_asset).resolve()
    prod_path = (ctx.root / plan.product_asset).resolve()
    images: list = [char_path, prod_path]
    videos: Optional[list] = None
    if plan.motion_id:
        motion = ctx.get_referral_motion(plan.motion_id)
        if motion is not None:
            ref_video = (ctx.root / motion.reference_path).resolve()
            if ref_video.exists():
                videos = [ref_video]

    audit_log = (ctx.clients_root.parent / "logs" / "kling-api.jsonl").resolve()
    kling_client = KlingClient(audit_log_path=audit_log)

    # ---- The single paid call (we hold the claim) ----
    try:
        task_id = kling_client.submit_omni_video(
            prompt=plan.prompt,
            images=images,
            videos=videos,
            negative_prompt=plan.negative_prompt,
            duration=plan.duration,
            aspect_ratio=plan.aspect_ratio,
            mode=plan.mode,
            cfg_scale=plan.cfg_scale,
        )
    except Exception as e:
        # Revert submitting -> pending_approval and persist the error.
        # Includes a TIMEOUT_WARNING when the failure shape suggests Kling
        # may have accepted the request anyway.
        error_str, is_timeout = _classify_kling_failure(e)
        ctx.release_plan_after_submit_failure(plan_id, error_str)
        return {
            "messages": [AIMessage(
                content=(
                    f"ERROR: Kling submission failed for plan {plan_id}. "
                    f"Plan reverted to pending_approval (attempts="
                    f"{plan.submit_attempts + 1}). submit_error recorded:\n"
                    f"{error_str}"
                )
            )],
            "current_agent": "producer_submit",
            "error": f"Kling submit failed: {e}" + (" [TIMEOUT]" if is_timeout else ""),
        }

    # ---- Success: persist job + transition plan ----
    ctx.create_video_job(VideoJob(
        kling_task_id=task_id,
        plan_id=plan_id,
        status=JobStatus.PENDING,
        submitted_at=datetime.now(timezone.utc),
    ))
    ctx.mark_plan_submitted(plan_id, decided_by="human")

    artifacts = dict(state.get("artifacts") or {})
    artifacts["producer_submitted_task_id"] = task_id

    return {
        "messages": [AIMessage(
            content=(
                f"Plan {plan_id} approved and submitted to Kling. "
                f"Task ID: {task_id}. Render typically takes 1-5 minutes. "
                f"Check status from the Streamlit UI's Generated videos tab "
                f"or by asking the Producer to call check_video_status('{task_id}')."
            )
        )],
        "current_agent": "producer_submit",
        "artifacts": artifacts,
    }
