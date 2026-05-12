"""Analyst worker - pulls Meta performance, evaluates per-hook ROAS/CTR,
writes HARD negative constraints into MASTER_CONTEXT.md.

Closes the learning loop: bad hooks (high spend, low ROAS/CTR) get blocked
so the Producer's Kling brief_compiler excludes them on future generations
via the negative_prompt field.

Architecture: LangGraph create_react_agent with four tools (pull_meta_insights,
evaluate_performance, write_negative_constraint, append_analyst_summary).
Model selection is explicit per run via config['configurable']['model'],
same contract as the Strategist and Producer.
"""
from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from agents.analyst.context_writer import (
    make_analyst_summary_writer,
    make_constraint_writer,
    make_evaluation_tool,
)
from agents.analyst.meta_insights import make_meta_insights_tool
from core.client_context import ClientContext
from core.models import SUPPORTED_MODEL_IDS, validate_model_id
from core.state import AgentState


SYSTEM_PROMPT = """You are the Analyst for an AI performance marketing agency.

Client: {client_name}  (locale: {client_locale})
Performance benchmarks:
  ROAS target: {roas_target}
  CTR target:  {ctr_target}

The client has {hooks_count} winning hooks in MASTER_CONTEXT.md and
{constraints_count} negative constraints already in force.

Your workflow this turn:
  1. Call `pull_meta_insights` with an appropriate time_preset (last_14d is
     the default; use last_28d or last_30d for more spend signal if needed).
  2. Pass the returned insights to `evaluate_performance`. Read the
     `evaluations` array carefully:
       - Hooks marked `failed: true` with a `proposed_rule` are underperformers
         that need a hard negative constraint.
       - Hooks marked `failed: true` but `already_constrained: true` are
         already covered - skip them.
       - Hooks marked `failed: false` with "insufficient spend" reasons need
         more spend before they can be evaluated; don't act.
  3. For each underperforming hook that is NOT already constrained, call
     `write_negative_constraint` with the proposed_rule and proposed_reason
     verbatim from evaluate_performance. This is a HARD constraint that the
     Producer will enforce on all future video generations.
  4. Call `append_analyst_summary` with a 2-4 sentence summary covering:
       - Time window analyzed
       - Total spend evaluated
       - Number of hooks failed / constraints added
       - Top performers worth highlighting
  5. Stop.

CRITICAL: Negative constraints are permanent (until manually removed) and
cause the Producer to refuse to use the constrained hook. Be conservative:
only constrain hooks that decisively underperform across meaningful spend.

If `evaluate_performance` returns a `warning` field (no benchmarks set),
surface that to the user and stop - don't write constraints in the dark."""


def analyst_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    model_name = (config or {}).get("configurable", {}).get("model")
    if not model_name:
        raise ValueError(
            "Model not selected. The Analyst requires an explicit model "
            "choice per run via config['configurable']['model']. "
            f"Supported: {', '.join(SUPPORTED_MODEL_IDS)}."
        )
    validate_model_id(model_name)

    ctx = ClientContext.load(state["client_id"])
    fm, _ = ctx.read()

    tools = [
        make_meta_insights_tool(),
        make_evaluation_tool(ctx),
        make_constraint_writer(ctx),
        make_analyst_summary_writer(ctx),
    ]

    llm = ChatAnthropic(model=model_name, max_tokens=4096, temperature=0.2)

    benchmarks = fm.performance_benchmarks
    system_prompt = SYSTEM_PROMPT.format(
        client_name=fm.client.name,
        client_locale=fm.client.locale,
        roas_target=benchmarks.roas_target if benchmarks.roas_target is not None else "(not set)",
        ctr_target=benchmarks.ctr_target if benchmarks.ctr_target is not None else "(not set)",
        hooks_count=len(fm.winning_hooks),
        constraints_count=len(fm.negative_constraints),
    )

    react = create_react_agent(llm, tools, prompt=system_prompt)
    result = react.invoke({"messages": state["messages"]})

    new_messages = result["messages"][len(state["messages"]):]

    # Surface the model used + IDs of any new constraints written this turn.
    artifacts = dict(state.get("artifacts") or {})
    artifacts["model_used"] = model_name
    new_constraint_ids: list[str] = []
    for msg in new_messages:
        if hasattr(msg, "name") and msg.name == "write_negative_constraint":
            content = str(msg.content)
            if content.startswith("NC-"):  # actual ID, not "SKIPPED: ..."
                new_constraint_ids.append(content)
    if new_constraint_ids:
        artifacts["analyst_new_constraints"] = new_constraint_ids

    return {
        "messages": new_messages,
        "current_agent": "analyst",
        "artifacts": artifacts,
    }
