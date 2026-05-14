"""LangGraph Supervisor wiring Strategist, Producer, Analyst, and Outreach workers.

Per-turn flow:
  1. Decide task_type (explicit from state, else infer via keyword router).
  2. For client-scoped task types (research/produce/analyze): validate client_id
     and load the client silo. Outreach is global - skip this pre-flight.
  3. Dispatch via conditional edge to the chosen worker.
  4. Worker returns; end after one hop.

V1.3 (2/N): adds run_supervisor_async() for asyncio.run() dispatch.
V1.3 (3/N): adds optional interrupt_before plumbing + resume_supervisor_async()
            + get_pending_node() so the UI can gate Producer behind a Human-
            in-the-Loop approval. Worker nodes still sync; MCP/CLI keep
            no-interrupt behavior by passing interrupt_before=None.
V1.5:       default checkpointer is now SqliteSaver (./checkpoints.db),
            so pending HITL approvals survive Streamlit and MCP-server
            restarts. The DB is intentionally isolated from per-client
            client_data.db files - different lifecycle, different concerns.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from agents.analyst.agent import analyst_node
from agents.outreach.agent import outreach_node
from agents.producer.agent import producer_plan_node, producer_submit_node
from agents.strategist.agent import strategist_node
from core.client_context import ClientContext
from core.router import route_task
from core.state import AgentState

_CLIENT_SCOPED = {"research", "produce", "analyze"}


# V1.5: default location for the SqliteSaver. Isolated from any
# clients/<id>/client_data.db; the checkpoint DB tracks ephemeral graph
# state (paused HITLs, in-flight turns) and is regenerated on demand.
# Override via CHECKPOINT_DB_PATH env var (e.g. tests pin to tmp_path).
DEFAULT_CHECKPOINT_DB_PATH = Path(os.getenv("CHECKPOINT_DB_PATH", "checkpoints.db"))


def supervisor_node(state: AgentState) -> dict[str, Any]:
    """Pre-flight: pick task_type, validate client only when needed."""
    task_type = state.get("task_type") or route_task(state["messages"])
    client_id = state.get("client_id")

    if task_type in _CLIENT_SCOPED:
        if not client_id:
            return {"error": f"task_type={task_type!r} requires client_id"}
        try:
            ClientContext.load(client_id)
        except (FileNotFoundError, ValueError) as e:
            return {"error": f"client load failed: {e}"}

    return {
        "task_type": task_type,
        "current_agent": task_type,
        "error": None,
    }


def _select_worker(state: AgentState):
    if state.get("error"):
        return END
    return {
        "research": "strategist",
        "produce": "producer_plan",
        "analyze": "analyst",
        "outreach": "outreach",
    }.get(state.get("current_agent"), END)


def _after_plan(state: AgentState):
    """Conditional edge from producer_plan: pause for approval only when a
    plan was actually compiled. If the LLM only checked task status (no
    compile_video_plan call), state['plan_id'] is None and we end the run
    without triggering the HITL gate."""
    if state.get("plan_id"):
        return "producer_submit"
    return END


def _build_default_checkpointer(db_path: Path) -> SqliteSaver:
    """Open a SqliteSaver-backed connection at db_path.

    - check_same_thread=False: LangGraph dispatches saver methods from a
      thread pool under invoke; SQLite would otherwise refuse cross-thread
      use of the connection.
    - PRAGMA journal_mode=WAL: readers do not block writers and vice versa,
      so the UI inspecting graph state cannot deadlock with a graph turn
      mid-write. Same justification as client_data.db.
    - The connection is owned by the returned SqliteSaver and lives as long
      as the compiled graph. Streamlit caches the graph in session_state;
      MCP server caches it at module scope. Process exit closes the conn.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(conn)


def build_supervisor_graph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
    interrupt_before: Optional[list[str]] = None,
    checkpoint_db_path: Optional[Path] = None,
):
    """Compile the supervisor graph.

    checkpointer: explicit BaseCheckpointSaver instance. Tests typically
                  pass MemorySaver() here for isolation. When None, builds
                  a SqliteSaver at checkpoint_db_path (default
                  ./checkpoints.db, overridable via CHECKPOINT_DB_PATH env).
    interrupt_before: optional list of node names to pause BEFORE executing.
                  Default None = no interrupts (sync behavior, CLI).
                  V1.4: pass ['producer_submit'] to gate paid Kling
                  submissions behind a HITL approval that reviews the
                  EXACT compiled brief in SQL.
    checkpoint_db_path: where the default SqliteSaver writes. Ignored when
                  `checkpointer` is provided explicitly.

    The returned compiled graph supports both invoke() (sync) and ainvoke()
    (async). Use run_supervisor_async / resume_supervisor_async helpers.
    """
    if checkpointer is None:
        checkpointer = _build_default_checkpointer(
            checkpoint_db_path or DEFAULT_CHECKPOINT_DB_PATH
        )

    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("strategist", strategist_node)
    g.add_node("producer_plan", producer_plan_node)
    g.add_node("producer_submit", producer_submit_node)
    g.add_node("analyst", analyst_node)
    g.add_node("outreach", outreach_node)

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", _select_worker)
    g.add_edge("strategist", END)
    g.add_conditional_edges("producer_plan", _after_plan)
    g.add_edge("producer_submit", END)
    g.add_edge("analyst", END)
    g.add_edge("outreach", END)

    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before or [],
    )


def initial_state(
    client_id: Optional[str],
    user_message: str,
    task_type: Optional[str] = None,
) -> AgentState:
    return {
        "messages": [HumanMessage(content=user_message)],
        "client_id": client_id,
        "task_type": task_type,
        "current_agent": None,
        "artifacts": {},
        "error": None,
        "plan_id": None,
    }


async def run_supervisor_async(
    graph,
    state: AgentState,
    *,
    config: Optional[dict] = None,
) -> dict[str, Any]:
    """Async dispatch wrapper around graph.ainvoke.

    Pair with get_pending_node(graph, config=config) after invocation
    to detect whether interrupt_before paused the graph.
    """
    return graph.invoke(state, config=config or {})


async def resume_supervisor_async(
    graph,
    *,
    config: dict,
) -> dict[str, Any]:
    """Resume a paused supervisor graph after a HITL approval.

    Pass None as the input - LangGraph picks up from the checkpoint
    identified by config['configurable']['thread_id']. The config must
    match the one used in the original run_supervisor_async call that
    caused the pause.
    """
    return graph.invoke(None, config=config)


def get_pending_node(graph, *, config: dict) -> Optional[str]:
    """Returns the name of the next node about to execute, or None when
    the graph has completed (snapshot.next is empty).

    The UI calls this immediately after run_supervisor_async to detect
    an interrupt_before pause.
    """
    snapshot = graph.get_state(config=config)
    if snapshot.next:
        return snapshot.next[0]
    return None
