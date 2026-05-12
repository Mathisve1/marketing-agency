"""CLI entry point — smoke-test the graph outside of Streamlit."""
from __future__ import annotations

import argparse
import sys

from core.supervisor import build_supervisor_graph, initial_state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True, help="client_id (folder under clients/)")
    parser.add_argument("--prompt", required=True, help="user instruction for the supervisor")
    args = parser.parse_args()

    graph = build_supervisor_graph()
    result = graph.invoke(
        initial_state(args.client, args.prompt),
        config={"configurable": {"thread_id": f"{args.client}-cli"}},
    )

    if result.get("error"):
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return 1

    print(f"Task type: {result['task_type']}")
    print(f"Routed to:  {result['current_agent']}")
    print("---")
    for msg in result["messages"]:
        print(f"[{msg.__class__.__name__}] {msg.content}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
