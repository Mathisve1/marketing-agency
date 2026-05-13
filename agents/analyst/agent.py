"""Analyst worker - pulls Meta performance, evaluates per-hook ROAS/CTR,
writes HARD negative constraints into MASTER_CONTEXT.md.

V1.1 refactor: single combined tool analyze_campaign_performance replaces
the old two-tool flow (pull_meta_insights + evaluate_performance). The
LLM no longer sees raw insights JSON - the tool returns a compressed
text summary with verdicts + ready-to-copy proposed_rule/reason pairs.

Architecture: LangGraph create_react_agent with three tools (down from
four): analyze_campaign_performance, write_negative_constraint,
append_analyst_summary. Model selection is explicit per run via
config['configurable']['model'], same contract as the Strategist.
"""
from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent

from agents.analyst.context_writer import (
    make_analyst_summary_writer,
    make_analyze_campaign_performance_tool,
    make_constraint_writer,
)
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
  1. Call `analyze_campaign_performance` (default time_preset='last_14d';
     use 'last_28d' for more spend signal if you don't see enough volume).
  2. The tool returns a compressed text summary with three sections:
       - FAILED hooks (each with a proposed_rule + proposed_reason ready
         to copy, plus an `already_constrained=YES/NO` flag).
       - PASSING hooks (no action needed).
       - INSUFFICIENT SPEND hooks (skip - too little signal to judge).
  3. For each FAILED hook with `already_constrained=NO`, call
     `write_negative_constraint` with the proposed_rule and proposed_reason
     verbatim from the summary. Do NOT modify the rule or reason - they
     are precisely formatted so the Producer's regex guard works.
  4. Call `append_analyst_summary` with a 2-4 sentence summary covering:
       - Time window analyzed
       - Total hooks analyzed / failed / constrained this run
       - Top performers worth highlighting from the PASSING list
  5. Stop.

CRITICAL: Negative constraints are permanent (until manually removed) and
cause the Producer to refuse to use the constrained hook. Trust the
analyze_campaign_performance verdict - the tool has already applied the
min_spend threshold and benchmark math. Do NOT second-guess the
arithmetic; just act on the FAILED list.

If analyze_campaign_performance returns an ERROR line (benchmarks unset,
no ads found), surface that to the user and stop - don't write
constraints in the dark."""


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
        make_analyze_campaign_performance_tool(ctx),
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

    artifacts = dict(state.get("artifacts") or {})
    artifacts["model_used"] = model_name
    new_constraint_ids: list[str] = []
    for msg in new_messages:
        if hasattr(msg, "name") and msg.name == "write_negative_constraint":
            content = str(msg.content)
            if content.startswith("NC-"):
                new_constraint_ids.append(content)
    if new_constraint_ids:
        artifacts["analyst_new_constraints"] = new_constraint_ids

    return {
        "messages": new_messages,
        "current_agent": "analyst",
        "artifacts": artifacts,
    }
