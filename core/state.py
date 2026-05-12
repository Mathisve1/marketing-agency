"""Shared state passed through the LangGraph supervisor graph."""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Blackboard shared by Supervisor and Workers.

    Workers append to `messages` and `artifacts`; the Supervisor reads
    `task_type` / `current_agent` to control flow.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    client_id: str
    task_type: Optional[str]       # "research" | "produce" | "analyze"
    current_agent: Optional[str]   # set by Supervisor per turn
    artifacts: dict[str, str]      # name -> absolute path of produced files
    error: Optional[str]
