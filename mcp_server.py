"""MCP server exposing the LangGraph marketing agency to Claude Desktop.

Single tool: run_agency_agent. Wraps build_supervisor_graph().invoke() so
the operator can run the agency natively from Claude Desktop without
spinning up Streamlit.

Run standalone:  python mcp_server.py
Or wire into Claude Desktop via claude_desktop_config.json (see README).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Ensure we run from the repo root so relative paths (clients/, prospects/,
# logs/) resolve correctly regardless of how Claude Desktop spawns us.
REPO_ROOT = Path(__file__).resolve().parent
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load .env before any agent imports so API keys are available without
# having to duplicate them in claude_desktop_config.json's `env` block.
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass  # python-dotenv is optional; env vars can also come from the host config.

from mcp.server.fastmcp import FastMCP

from core.models import SUPPORTED_MODEL_IDS
from core.supervisor import build_supervisor_graph, initial_state


# Hidden default - Sonnet 4.6 is the cheaper/faster tier. Override per call
# via the `model` arg if a heavier reasoning pass is worth the cost.
DEFAULT_MCP_MODEL = "claude-sonnet-4-6"


mcp = FastMCP("marketing-agency")


@mcp.tool()
def run_agency_agent(
    prompt: str,
    client_id: Optional[str] = None,
    task_type: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Run the marketing agency LangGraph supervisor and return the result.

    The supervisor routes to one of four workers:
      - 'research' (Strategist)  - competitor scrape + winning hook extraction. Needs client_id.
      - 'produce'  (Producer)    - Kling Omni-Video generation. Needs client_id.
      - 'analyze'  (Analyst)     - Meta Insights -> negative constraints. Needs client_id.
      - 'outreach' (Outreach)    - prospect discovery + pitch PDFs. NO client_id.

    Args:
        prompt: The instruction (e.g. 'Find 5 fitness apparel brands in the UK',
                'Produce a video using hook WH-003 with our default character').
        client_id: Optional client slug for client-scoped work. Required when
                   task_type is research/produce/analyze. Must be None (or
                   omitted) when task_type is outreach.
        task_type: Optional explicit routing ('research' | 'produce' | 'analyze'
                   | 'outreach'). When omitted, the supervisor's keyword router
                   infers from the prompt.
        model: Optional Anthropic model ID. Defaults to 'claude-sonnet-4-6'.
               Pass 'claude-opus-4-7' for premium reasoning (~3x cost).

    Returns:
        Formatted text: route + model used, every agent message produced
        this turn, and any artifacts (video paths, pitch PDFs, etc.).
    """
    chosen_model = model or DEFAULT_MCP_MODEL
    if chosen_model not in SUPPORTED_MODEL_IDS:
        return (
            f"ERROR: Unsupported model {chosen_model!r}. "
            f"Choose one of: {', '.join(SUPPORTED_MODEL_IDS)}"
        )

    try:
        graph = build_supervisor_graph()
        result = graph.invoke(
            initial_state(client_id=client_id, user_message=prompt, task_type=task_type),
            config={"configurable": {
                "thread_id": f"mcp-{client_id or 'global'}",
                "model": chosen_model,
            }},
        )
    except Exception as e:
        # Catch-all so the MCP server never crashes mid-tool-call; surface
        # the error back to Claude Desktop as text.
        return f"ERROR running agency graph: {type(e).__name__}: {e}"

    if result.get("error"):
        return f"ERROR: {result['error']}"

    lines: list[str] = [
        f"Routed to: {result.get('current_agent', 'unknown')}",
        f"Task type: {result.get('task_type', 'unknown')}",
        f"Model:     {chosen_model}",
        "---",
    ]
    for msg in result.get("messages", []):
        role = msg.__class__.__name__
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"[{role}] {content}")

    artifacts = result.get("artifacts") or {}
    if artifacts:
        lines.append("---")
        lines.append("Artifacts:")
        for key, value in artifacts.items():
            lines.append(f"  {key}: {value}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
