"""LangGraph Supervisor wiring Strategist, Producer, Analyst, and Outreach workers.

Per-turn flow:
  1. Decide task_type (explicit from state, else infer via keyword router).
  2. For client-scoped task types (research/produce/analyze): validate client_id
     and load the client silo. Outreach is global - skip this pre-flight.
  3. Dispatch via conditional edge to the chosen worker.
  4. Worker returns; end after one hop. Future phases may loop back to the
     supervisor for multi-step plans.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.analyst.agent import analyst_node
from agents.outreach.agent import outreach_node
from agents.producer.agent import producer_node
from agents.strategist.agent import strategist_node
from core.client_context import ClientContext
from core.router import route_task
from core.state import AgentState


# Task types that require a valid client silo (read/write MASTER_CONTEXT.md).
# Outreach is intentionally absent - it operates on prospects/, not clients/.
_CLIENT_SCOPED = {"research", "produce", "analyze"}


def supervisor_node(state: AgentState) -> dict[str, Any]:
    """Pre-flight: pick task_type, validate client only when needed."""
    task_type = state.get("task_type") or route_task(state["messages"])
    client_id = state.get("client_id")

    if task_type in _CLIENT_SCOPED:
        if not client_id:
            return {"error": f"task_type={task_type!r} requires client_id"}
        try:
            ClientContext.load(client_id)  # existence + Pydantic validation
        except (FileNotFoundError, ValueError) as e:
            return {"error": f"client load failed: {e}"}
    # Outreach is global - no client_id, no ClientContext load.

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
        "outreach": "outreach",
    }.get(state.get("current_agent"), END)


def build_supervisor_graph(checkpointer: Optional[MemorySaver] = None):
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("strategist", strategist_node)
    g.add_node("producer", producer_node)
    g.add_node("analyst", analyst_node)
    g.add_node("outreach", outreach_node)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", _select_worker)
    g.add_edge("strategist", END)
    g.add_edge("producer", END)
    g.add_edge("analyst", END)
    g.add_edge("outreach", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def initial_state(
    client_id: Optional[str],
    user_message: str,
    task_type: Optional[str] = None,
) -> AgentState:
    """Helper for callers (CLI, Streamlit) to build a starting state.

    Pass client_id=None + task_type='outreach' for global outreach runs.
    Pass client_id='<slug>' for any client-scoped task type.
    """
    return {
        "messages": [HumanMessage(content=user_message)],
        "client_id": client_id,
        "task_type": task_type,
        "current_agent": None,
        "artifacts": {},
        "error": None,
    }
