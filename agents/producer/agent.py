"""Producer worker - selects assets, compiles Kling brief, generates UGC video.

V1.3: Decouple Kling generation to bypass MCP's 60s timeout. The Producer
now uses an async submit/poll pattern:
  - generate_ugc_video       submits to Kling and returns task_id immediately
                             (logs entry with status='pending').
  - check_video_status       single-shot poll; on completion, downloads MP4
                             and flips the log entry to status='completed'.

The agent NEVER blocks waiting on Kling. The LLM (or the operator via
multiple MCP/Streamlit calls) drives the polling cadence.
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
from core.models import SUPPORTED_MODEL_IDS, validate_model_id
from core.state import AgentState


# Statuses Kling can report for an in-flight task. Anything outside this
# set + the SUCCESS_STATUSES / FAILURE_STATUSES known to the client is
# treated as "still pending" rather than crashing.
_NON_TERMINAL_STATUSES = {"pending", "processing", "queued", "running", "created", "submitted"}


SYSTEM_PROMPT = """You are the Producer for an AI performance marketing agency.

Client: {client_name}  (locale: {client_locale})
Brand context: {brand_context}

Inventory: {hooks_count} winning hooks, {motions_count} referral motions,
{constraints_count} negative constraints active.

Your workflow this turn:
  1. Call `read_master_context` to see winning_hooks, referral_motions, and
     negative_constraints. Pick a hook + (optional) motion that fits the
     user's brief.
  2. Call `list_available_assets` for 'characters' and 'products' to see
     what reference imagery is in the client silo.
  3. Call `generate_ugc_video` once per requested variation. The tool
     submits the job to Kling and IMMEDIATELY returns a task_id (it does
     NOT wait for the render). Each call is a paid Kling API invocation.
     Report the returned task_ids back to the user.
  4. When the user asks for status (e.g. "is task X done?" or "check
     status"), call `check_video_status(task_id)`:
       - "still rendering"  -> tell them to wait ~60 seconds and ask again.
       - the tool returned a file path  -> the MP4 was downloaded; report
         the path. The video is now visible in the UI's Generated videos tab.
       - "FAILED" message   -> surface the error to the user.

CRITICAL: Each `generate_ugc_video` call is a paid Kling API invocation. Do
NOT call it more than the user explicitly requested. If unsure, ask first.

CRITICAL: The Producer never waits for renders. If the user asks "make a
video and tell me when it's done", explain that you can submit the job and
they should follow up by asking for the status of the returned task_id."""


def _build_tools(ctx: ClientContext, kling_client: KlingClient):
    """Closure-based tools so the LLM never threads client_id through args."""

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
        pick what to combine."""
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

    @tool("generate_ugc_video")
    def generate_ugc_video(
        hook_id: str,
        character_asset: str,
        product_asset: str,
        motion_id: Optional[str] = None,
        duration: int = 10,
    ) -> str:
        """Submit a Kling Omni-Video generation. Returns IMMEDIATELY with
        the Kling task_id - does NOT wait for rendering. Use
        `check_video_status(task_id)` to poll for completion and trigger
        the local MP4 download.

        Each call is a paid Kling API invocation. The submitted job is
        logged to performance_log.json with status='pending'.

        Use motion_id when a strong referral motion exists (the Omni model
        will inherit pacing + camera from the referral video). Omit
        motion_id to generate from character + product images alone.
        """
        # V1.2 single-row SQL lookups
        hook = ctx.get_winning_hook(hook_id)
        if hook is None:
            raise ValueError(f"hook_id {hook_id!r} not found in winning_hooks.")

        motion = None
        if motion_id:
            motion = ctx.get_referral_motion(motion_id)
            if motion is None:
                raise ValueError(f"motion_id {motion_id!r} not found in referral_motions.")

        char_path = (ctx.root / character_asset).resolve()
        prod_path = (ctx.root / product_asset).resolve()
        if not char_path.exists():
            raise FileNotFoundError(f"Character asset not found: {char_path}")
        if not prod_path.exists():
            raise FileNotFoundError(f"Product asset not found: {prod_path}")

        # Brand from MASTER_CONTEXT.md; constraints from SQL.
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

        # Agency convention enforced HERE (not in the API client):
        # images[0] -> <<<image_1>>> = character
        # images[1] -> <<<image_2>>> = product
        images: list = [brief.character_image_path, brief.product_image_path]
        videos: Optional[list] = None
        if brief.reference_video_path is not None:
            ref_video = (ctx.root / brief.reference_video_path).resolve()
            if not ref_video.exists():
                raise FileNotFoundError(
                    f"Motion's reference video not found: {ref_video}"
                )
            videos = [ref_video]

        # SUBMIT ONLY. We do NOT wait for completion - bypasses the MCP 60s
        # timeout. Caller polls via check_video_status(task_id) later.
        task_id = kling_client.submit_omni_video(
            prompt=brief.prompt,
            images=images,
            videos=videos,
            negative_prompt=brief.negative_prompt,
            duration=brief.duration,
            aspect_ratio=brief.aspect_ratio,
            mode=brief.mode,
            cfg_scale=brief.cfg_scale,
        )

        ctx.append_performance_entry({
            "type": "video_generation",
            "status": "pending",
            "hook_id": hook_id,
            "motion_id": motion_id,
            "kling_task_id": task_id,
            "character_asset": character_asset,
            "product_asset": product_asset,
            "video_path": None,
            "enforced_constraint_ids": brief.enforced_constraint_ids,
            "duration": brief.duration,
            "aspect_ratio": brief.aspect_ratio,
            "mode": brief.mode,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        })

        return (
            f"Task submitted successfully. ID: {task_id}. "
            f"Kling rendering typically takes 1-5 minutes. Call "
            f"check_video_status('{task_id}') in a minute or two to poll "
            f"completion and trigger the MP4 download."
        )

    @tool("check_video_status")
    def check_video_status(task_id: str) -> str:
        """Single-shot poll of a previously-submitted Kling task.

        Three terminal possibilities:
          1. Still rendering  -> returns a 'wait and retry' status string.
          2. Failed           -> updates the performance_log entry to
                                 status='failed' and returns the error.
          3. Completed        -> downloads the MP4 to outputs/videos/,
                                 updates the performance_log entry to
                                 status='completed' with video_path, and
                                 returns the absolute local file path.

        This tool does NOT block. Each call performs one HTTP request to
        Kling. If still rendering, the caller (LLM or human) decides when
        to poll again.
        """
        task = kling_client.poll_task(task_id)
        data = task.get("data") or task
        status = str(data.get("task_status") or data.get("status") or "pending").lower()

        # ---- non-terminal: still rendering ----
        if status in _NON_TERMINAL_STATUSES:
            return (
                f"Task {task_id} still rendering (status: {status}). "
                f"Wait 30-60 seconds and call check_video_status('{task_id}') again."
            )

        # ---- terminal: failed ----
        if status in {"failed", "error"}:
            err = data.get("error") or data.get("message") or "unknown failure"
            ctx.update_performance_entry_by_task_id(task_id, {
                "status": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(err),
            })
            return f"Task {task_id} FAILED: {err}"

        # ---- terminal: success - download to outputs/videos/ ----
        # Pull hook_id/motion_id from the original performance_log entry so
        # the downloaded filename keeps the agency naming convention.
        log_entries = ctx.read_performance_log()
        entry = next(
            (e for e in log_entries if e.get("kling_task_id") == task_id),
            None,
        )
        hook_id = (entry or {}).get("hook_id", "unknown")
        motion_id = (entry or {}).get("motion_id")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"{hook_id}-{motion_id or 'images'}-{timestamp}.mp4"
        dest = ctx.root / "outputs" / "videos" / name
        kling_client.download_video(task, dest)

        rel_path = str(dest.relative_to(ctx.root))
        ctx.update_performance_entry_by_task_id(task_id, {
            "status": "completed",
            "video_path": rel_path,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        # Returning the absolute path lets the agent's artifact-extraction
        # code identify completion via Path(content).exists().
        return str(dest.resolve())

    return [
        list_available_assets,
        read_master_context,
        generate_ugc_video,
        check_video_status,
    ]


def _format_brand_context(fm) -> str:
    parts: list[str] = []
    if fm.brand.voice_attributes:
        parts.append(f"Voice: {', '.join(fm.brand.voice_attributes)}")
    if fm.brand.primary_products:
        parts.append(f"Products: {', '.join(fm.brand.primary_products)}")
    if fm.brand.forbidden_terms:
        parts.append(f"Forbidden: {', '.join(fm.brand.forbidden_terms)}")
    return " | ".join(parts) if parts else "(none recorded)"


def producer_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
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

    tools = _build_tools(ctx, kling_client)

    llm = ChatAnthropic(model=model_name, max_tokens=4096, temperature=0.2)

    system_prompt = SYSTEM_PROMPT.format(
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

    # V1.3 artifact extraction:
    #   - generate_ugc_video     submission messages -> producer_submitted_tasks
    #   - check_video_status     returning a real .mp4 path -> producer_videos
    #                            (so the UI's inline player still works)
    #   - check_video_status     non-path returns (pending/failed) ->
    #                            producer_status_checks (for the audit panel)
    submitted_tasks: list[str] = []
    completed_videos: list[str] = []
    status_messages: list[str] = []
    for msg in new_messages:
        if not hasattr(msg, "name"):
            continue
        if msg.name == "generate_ugc_video":
            submitted_tasks.append(str(msg.content))
        elif msg.name == "check_video_status":
            content = str(msg.content)
            candidate = Path(content)
            if candidate.suffix == ".mp4" and candidate.exists():
                completed_videos.append(content)
            else:
                status_messages.append(content)

    artifacts = dict(state.get("artifacts") or {})
    artifacts["model_used"] = model_name
    if submitted_tasks:
        artifacts["producer_submitted_tasks"] = submitted_tasks
    if completed_videos:
        artifacts["producer_videos"] = completed_videos
    if status_messages:
        artifacts["producer_status_checks"] = status_messages

    return {
        "messages": new_messages,
        "current_agent": "producer",
        "artifacts": artifacts,
    }
