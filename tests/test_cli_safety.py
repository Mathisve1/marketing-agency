"""V1.6 CLI safety tests.

The CLI (`main.py`) has no UX to approve a paused HITL checkpoint, so it
must NOT be able to dispatch the Producer agent. Pinning two invariants:

  1. argparse rejects `--task-type produce` at the choices layer -
     before any graph is constructed and certainly before any paid Kling
     submission could happen.

  2. The legal CLI choices list contains exactly the non-spending agents
     plus outreach (which has its own per-run hard call caps). If a
     future maintainer adds 'produce' back, this test fails loudly.

We test the parser layer specifically because it's the single chokepoint
that prevents the unsafe path - all real protection lives there.
"""
from __future__ import annotations

import pytest

from main import TASK_TYPE_CHOICES, _build_parser

# --------------------------------------------------------------------------- #
# Parser invariants
# --------------------------------------------------------------------------- #


def test_produce_is_not_a_valid_cli_task_type():
    """The whole point of this pass: the CLI cannot route to the
    Producer node. If it could, an operator could spend Kling credits
    with no approval UX."""
    assert "produce" not in TASK_TYPE_CHOICES, (
        "produce must NOT be a CLI task type. Producer triggers paid "
        "Kling submissions and the CLI has no approval UX. Run it "
        "through Streamlit or MCP."
    )


def test_legal_cli_task_types_are_the_non_spending_set():
    """Pin the explicit allow-list. If a future change adds a new
    paid-spend agent to the CLI, this test forces the maintainer to
    update the assertion AND think about HITL UX at the same time."""
    assert set(TASK_TYPE_CHOICES) == {"research", "analyze", "outreach"}


# --------------------------------------------------------------------------- #
# argparse behaviour - the runtime guard
# --------------------------------------------------------------------------- #


def test_argparse_rejects_task_type_produce():
    """The argparse parser must SystemExit when --task-type produce is
    passed. This is what stops the unsafe path at runtime, even if a
    future caller bypasses TASK_TYPE_CHOICES somehow."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--client", "acme",
            "--prompt", "Run producer",
            "--model", "claude-sonnet-4-6",
            "--task-type", "produce",
        ])


def test_argparse_accepts_safe_task_types():
    """Sanity check: the legal task types still parse cleanly."""
    parser = _build_parser()
    for tt in ("research", "analyze", "outreach"):
        ns = parser.parse_args([
            "--client", "acme",
            "--prompt", "x",
            "--model", "claude-sonnet-4-6",
            "--task-type", tt,
        ])
        assert ns.task_type == tt


def test_argparse_accepts_omitted_task_type():
    """Auto-detect (no --task-type) is fine - the supervisor's keyword
    router may infer one of the safe types. Even if it infers
    'produce', the supervisor graph is built with
    interrupt_before=['producer_submit'] in main.py so paid spend
    cannot happen without the (absent) HITL approval."""
    parser = _build_parser()
    ns = parser.parse_args([
        "--client", "acme",
        "--prompt", "research competitors",
        "--model", "claude-sonnet-4-6",
    ])
    assert ns.task_type is None
