"""Producer worker - selects assets, compiles Kling brief, generates UGC video.

V1.2: hook + motion lookups use ctx.get_winning_hook(hook_id) /
ctx.get_referral_motion(motion_id) single-row SQL queries instead of
scanning fm.winning_hooks / fm.referral_motions lists.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
  3. Call `generate_ugc_video` once per requested variation with:
       hook_id          - e.g. 'WH-003'
       character_asset  - relative path from list_available_assets('characters')
       product_asset    - relative path from list_available_assets('products')
       motion_id        - optional 'RM-002' to inherit pacing + camera from a
                          referral video; omit to generate from images alone
       duration         - 5 to 15 seconds (default 10)
  4. Each `generate_ugc_video` call blocks 1-5 minutes while Kling renders.
     Report each output path back to the user when done.

CRITICAL: Each `generate_ugc_video` call is a paid Kling API invocation. Do
NOT call it more than the user explicitly requested. If unsure, ask first."""


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
        """Generate one UGC video: compile a Kling Omni-Video brief from
        hook + motion + assets, submit, poll, save MP4 to outputs/videos/.
        Returns absolute path to the saved video.

        Use motion_id when a strong referral motion exists (the Omni model
        will inherit pacing + camera from the referral video). Omit motion_id
        to generate from character + product images alone.
        """
        # V1.2: single-row SQL lookups
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

        # Brand still lives in MASTER_CONTEXT.md; constraints come from SQL.
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

        task = kling_client.wait_for_completion(task_id)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"{hook_id}-{motion_id or 'images'}-{timestamp}.mp4"
        dest = ctx.root / "outputs" / "videos" / name
        kling_client.download_video(task, dest)

        ctx.append_performance_entry({
            "type": "video_generation",
            "hook_id": hook_id,
            "motion_id": motion_id,
            "kling_task_id": task_id,
            "video_path": str(dest.relative_to(ctx.root)),
            "enforced_constraint_ids": brief.enforced_constraint_ids,
            "duration": brief.duration,
            "aspect_ratio": brief.aspect_ratio,
            "mode": brief.mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return str(dest)

    return [list_available_assets, read_master_context, generate_ugc_video]


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

    # V1.2: counts via SQL
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

    artifacts = dict(state.get("artifacts") or {})
    artifacts["model_used"] = model_name
    videos: list[str] = []
    for msg in new_messages:
        if hasattr(msg, "name") and msg.name == "generate_ugc_video":
            videos.append(str(msg.content))
    if videos:
        artifacts["producer_videos"] = videos

    return {
        "messages": new_messages,
        "current_agent": "producer",
        "artifacts": artifacts,
    }
