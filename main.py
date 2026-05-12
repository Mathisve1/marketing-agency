"""CLI entry point - smoke-test the graph outside of Streamlit."""
from __future__ import annotations

import argparse
import sys

from core.models import SUPPORTED_MODEL_IDS
from core.supervisor import build_supervisor_graph, initial_state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True, help="client_id (folder under clients/)")
    parser.add_argument("--prompt", required=True, help="user instruction for the supervisor")
    parser.add_argument(
        "--model",
        required=True,
        choices=list(SUPPORTED_MODEL_IDS),
        help="Anthropic model ID (explicit per-run selection required).",
    )
    args = parser.parse_args()

    graph = build_supervisor_graph()
    result = graph.invoke(
        initial_state(args.client, args.prompt),
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
