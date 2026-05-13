"""Shared state passed through the LangGraph supervisor graph."""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Blackboard shared by Supervisor and Workers.

    Workers append to `messages` and `artifacts`; the Supervisor reads
    `task_type` / `current_agent` to control flow.

    `client_id` is Optional because the Outreach worker operates outside
    any client silo (it hunts for new prospects rather than servicing
    existing ones). Strategist / Producer / Analyst all require a non-None
    client_id and the Supervisor enforces this in its pre-flight.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    client_id: Optional[str]
    task_type: Optional[str]       # "research" | "produce" | "analyze" | "outreach"
    current_agent: Optional[str]   # set by Supervisor per turn
    artifacts: dict[str, str]      # name -> absolute path of produced files
    error: Optional[str]
    # V1.4: set by producer_plan when compile_video_plan succeeds; consumed
    # by producer_submit. None means the planner did not produce a plan
    # (e.g. the LLM only checked status), so no submission should occur.
    plan_id: Optional[str]
