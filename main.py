"""CLI entry point - smoke-test the graph outside of Streamlit.

V1.6 safety: `produce` is intentionally NOT a valid `--task-type`. The
CLI has no UX to approve the Producer's HITL checkpoint, so allowing it
would mean the operator could trigger a paid Kling submission with zero
human review. Run Producer through Streamlit or MCP, both of which gate
the paid submission behind a compiled-plan approval.

The CLI is for non-spending agents only:
  - research   (Strategist)
  - analyze    (Analyst)
  - outreach   (Outreach -- spends Tavily/Apify, but no Kling, and the
                 Outreach node has its own per-run hard call caps).
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from core.models import SUPPORTED_MODEL_IDS
from core.supervisor import build_supervisor_graph, initial_state

# Producer ('produce') is deliberately omitted - see module docstring.
TASK_TYPE_CHOICES = ["research", "analyze", "outreach"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one supervisor turn from the command line. Useful for "
            "smoke-testing non-spending agents."
        ),
        epilog=(
            "Producer (--task-type produce) is intentionally NOT available "
            "from the CLI. Producer triggers paid Kling submissions and "
            "must be approved through the Streamlit UI or the MCP "
            "resume_agency_workflow tool, both of which present the "
            "exact compiled brief for human review before any paid call. "
            "See README 'CLI (debug)'."
        ),
    )
    parser.add_argument("--client", required=True, help="client_id (folder under clients/)")
    parser.add_argument("--prompt", required=True, help="user instruction for the supervisor")
    parser.add_argument(
        "--model",
        required=True,
        choices=list(SUPPORTED_MODEL_IDS),
        help="Anthropic model ID (explicit per-run selection required).",
    )
    parser.add_argument(
        "--task-type",
        choices=TASK_TYPE_CHOICES,
        default=None,
        help=(
            "Optional explicit task type. Omit to use the supervisor's "
            "keyword router. NOTE: 'produce' is not allowed from the CLI - "
            "see epilog."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    # The CLI has no HITL UX. Producer is excluded above at the choices
    # layer; we still build the graph WITH the gate set so an inferred
    # task_type from the keyword router that somehow lands on producer
    # is paused, not silently submitted.
    graph = build_supervisor_graph(interrupt_before=["producer_submit"])
    result = graph.invoke(
        initial_state(args.client, args.prompt, task_type=args.task_type),
        config={"configurable": {
            "thread_id": f"{args.client}-cli",
            "model": args.model,
        }},
    )

    if result.get("error"):
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return 1

    print(f"Task type: {result['task_type']}")
    print(f"Routed to:  {result['current_agent']}")
    print(f"Model:      {args.model}")
    print("---")
    for msg in result["messages"]:
        print(f"[{msg.__class__.__name__}] {msg.content}")
    if result.get("artifacts"):
        print("---")
        print(f"Artifacts: {result['artifacts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
