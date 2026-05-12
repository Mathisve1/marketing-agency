"""LangGraph Supervisor wiring Strategist, Producer, and Analyst workers.

Per-turn flow:
  1. Validate client_id and load the client silo (fail fast if missing/invalid).
  2. If task_type is unset, infer via the router.
  3. Dispatch via conditional edge to the chosen worker.
  4. Worker returns; Phase 1 ends after one hop. Future phases may loop back
     to the supervisor for multi-step plans.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.analyst.agent import analyst_node
from agents.producer.agent import producer_node
from agents.strategist.agent import strategist_node
from core.client_context import ClientContext
from core.router import route_task
from core.state import AgentState


def supervisor_node(state: AgentState) -> dict[str, Any]:
    """Pre-flight: validate client, set task_type + current_agent."""
    client_id = state.get("client_id")
    if not client_id:
        return {"error": "client_id is required in initial state"}

    try:
        ClientContext.load(client_id)  # existence + Pydantic validation
    except (FileNotFoundError, ValueError) as e:
        return {"error": f"client load failed: {e}"}

    task_type = state.get("task_type") or route_task(state["messages"])
    return {
        "task_type": task_type,
        "current_agent": task_type,
        "error": None,
    }


def _select_worker(state: AgentState):
    """Maps the Supervisor's chosen task_type to a worker node name."""
    if state.get("error"):
        return END
    return {
        "research": "strategist",
        "produce": "producer",
        "analyze": "analyst",
    }.get(state.get("current_agent"), END)


def build_supervisor_graph(checkpointer: Optional[MemorySaver] = None):
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("strategist", strategist_node)
    g.add_node("producer", producer_node)
    g.add_node("analyst", analyst_node)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", _select_worker)
    g.add_edge("strategist", END)
    g.add_edge("producer", END)
    g.add_edge("analyst", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def initial_state(client_id: str, user_message: str) -> AgentState:
    """Helper for callers (CLI, Streamlit) to build a starting state."""
    return {
        "messages": [HumanMessage(content=user_message)],
        "client_id": client_id,
        "task_type": None,
        "current_agent": None,
        "artifacts": {},
        "error": None,
    }
