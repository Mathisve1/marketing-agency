"""Manual output-grading helper.

The deliberate gap V1.6 is closing: the system can produce structured
outputs (PDFs, videos, audits, hook tables) but "structured" is not
"commercially good". Until we have automated eval signals, the operator
must grade outputs by hand. This script is the friction-free way to do
that after every real workflow test.

NO external APIs are called. The script reads operator input from the
terminal (or from CLI flags in --non-interactive mode) and appends one
JSONL row to evals/output_reviews.jsonl.

Usage (interactive):
    python scripts/eval_review.py --agent producer --client-id acme \\
        --output-type kling_video \\
        --source clients/acme/outputs/videos/WH-003-RM-001-20260514.mp4

Usage (non-interactive, e.g. for CI smoke tests):
    python scripts/eval_review.py --agent strategist --client-id acme \\
        --output-type pdf_report --source clients/acme/outputs/reports/x.pdf \\
        --specificity 4 --accuracy 5 --usefulness 4 --sendable yes \\
        --notes "Hooks 3 and 5 are weak; rest land." --non-interactive
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVAL_PATH = REPO_ROOT / "evals" / "output_reviews.jsonl"

VALID_AGENTS = ("strategist", "producer", "analyst", "outreach")
SCORE_RANGE = (1, 5)


@dataclass
class Review:
    """One row of the evaluation log. Keep additive only - this file is
    JSONL append-only and old rows must keep parsing."""
    timestamp: str
    agent: str
    client_id: Optional[str]
    prospect_id: Optional[str]
    output_type: str
    source: Optional[str]
    specificity: int
    accuracy: int
    usefulness: int
    sendable: bool
    notes: str


# --------------------------------------------------------------------------- #
# Pure helpers (no I/O) - tested directly
# --------------------------------------------------------------------------- #


def _validate_score(label: str, value: int) -> int:
    lo, hi = SCORE_RANGE
    if not (lo <= value <= hi):
        raise ValueError(f"{label} must be in [{lo}, {hi}], got {value}")
    return value


def _validate_sendable(value: str) -> bool:
    v = value.strip().lower()
    if v in ("y", "yes", "true", "1"):
        return True
    if v in ("n", "no", "false", "0"):
        return False
    raise ValueError(f"sendable must be yes/no, got {value!r}")


def build_review(
    *,
    agent: str,
    output_type: str,
    specificity: int,
    accuracy: int,
    usefulness: int,
    sendable: bool,
    notes: str,
    client_id: Optional[str] = None,
    prospect_id: Optional[str] = None,
    source: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Review:
    """Build a Review with full validation. Pure - safe to call from tests."""
    if agent not in VALID_AGENTS:
        raise ValueError(f"agent must be one of {VALID_AGENTS}, got {agent!r}")
    _validate_score("specificity", specificity)
    _validate_score("accuracy", accuracy)
    _validate_score("usefulness", usefulness)
    return Review(
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        agent=agent,
        client_id=client_id,
        prospect_id=prospect_id,
        output_type=output_type,
        source=source,
        specificity=specificity,
        accuracy=accuracy,
        usefulness=usefulness,
        sendable=sendable,
        notes=notes,
    )


def append_review(review: Review, eval_path: Path = DEFAULT_EVAL_PATH) -> Path:
    """Atomic-append one JSONL row. Returns the path written to.

    JSONL chosen over CSV because (a) append is genuinely atomic on POSIX
    for writes < PIPE_BUF, (b) tolerates schema growth without breaking
    older rows, (c) no quoting headaches with notes containing commas /
    newlines.
    """
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(review), ensure_ascii=False)
    with eval_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    return eval_path


# --------------------------------------------------------------------------- #
# Interactive prompting (only used when --non-interactive is NOT passed)
# --------------------------------------------------------------------------- #


def _prompt_score(label: str) -> int:
    while True:
        raw = input(f"{label} ({SCORE_RANGE[0]}-{SCORE_RANGE[1]}): ").strip()
        try:
            return _validate_score(label, int(raw))
        except ValueError as e:
            print(f"  -> {e}")


def _prompt_sendable() -> bool:
    while True:
        raw = input("Sendable / usable to a real customer? (y/n): ")
        try:
            return _validate_sendable(raw)
        except ValueError as e:
            print(f"  -> {e}")


def _prompt_notes() -> str:
    return input("Notes (single line, free text; press Enter to skip): ").strip()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Append a manual quality grade for an agent output to "
            "evals/output_reviews.jsonl. No external APIs are called."
        ),
        epilog=(
            "Run AFTER every real workflow test. The operator's grades are "
            "the only ground-truth signal we have until automated evals exist."
        ),
    )
    p.add_argument("--agent", required=True, choices=VALID_AGENTS)
    p.add_argument("--client-id", default=None)
    p.add_argument("--prospect-id", default=None)
    p.add_argument(
        "--output-type",
        required=True,
        help="e.g. pdf_report, pitch_pdf, kling_video, winning_hook, audit_json",
    )
    p.add_argument("--source", default=None, help="Optional path to the artifact graded.")
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Take grades from CLI flags instead of prompting. Useful in tests / CI.",
    )
    p.add_argument("--specificity", type=int, default=None)
    p.add_argument("--accuracy", type=int, default=None)
    p.add_argument("--usefulness", type=int, default=None)
    p.add_argument("--sendable", default=None, help="yes / no")
    p.add_argument("--notes", default="")
    p.add_argument(
        "--eval-path",
        default=str(DEFAULT_EVAL_PATH),
        help="Override the JSONL output path (default: evals/output_reviews.jsonl)",
    )
    return p


def _resolve_grades(args: argparse.Namespace) -> dict:
    """In non-interactive mode all four grade fields are required; in
    interactive mode we prompt for any that weren't supplied."""
    if args.non_interactive:
        missing = [
            f for f in ("specificity", "accuracy", "usefulness", "sendable")
            if getattr(args, f) is None
        ]
        if missing:
            raise SystemExit(
                f"--non-interactive requires --{', --'.join(missing)}"
            )
        return {
            "specificity": int(args.specificity),
            "accuracy": int(args.accuracy),
            "usefulness": int(args.usefulness),
            "sendable": _validate_sendable(args.sendable),
            "notes": args.notes,
        }
    # Interactive: prompt for whatever wasn't pre-supplied.
    return {
        "specificity": (
            args.specificity if args.specificity is not None else _prompt_score("specificity")
        ),
        "accuracy": (
            args.accuracy if args.accuracy is not None else _prompt_score("accuracy")
        ),
        "usefulness": (
            args.usefulness if args.usefulness is not None else _prompt_score("usefulness")
        ),
        "sendable": (
            _validate_sendable(args.sendable)
            if args.sendable is not None
            else _prompt_sendable()
        ),
        "notes": args.notes if args.notes else _prompt_notes(),
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    grades = _resolve_grades(args)
    review = build_review(
        agent=args.agent,
        output_type=args.output_type,
        client_id=args.client_id,
        prospect_id=args.prospect_id,
        source=args.source,
        **grades,
    )
    written = append_review(review, eval_path=Path(args.eval_path))
    print(f"OK  appended review for agent={review.agent} output={review.output_type} -> {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
