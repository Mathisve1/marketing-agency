"""Tests for scripts/eval_review.py.

Pins the JSONL contract:
  - build_review() validates inputs (agent enum, score range, sendable bool).
  - append_review() round-trips through JSON and is genuinely appendable.
  - The CLI's non-interactive mode produces an identical row to the
    programmatic API.

No external APIs called. The CLI's interactive prompts are NOT exercised
here - they would require a TTY emulator. The non-interactive path is
the one CI cares about anyway.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ isn't on sys.path by default; add the repo root and import.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_review  # noqa: E402

# --------------------------------------------------------------------------- #
# build_review() validation
# --------------------------------------------------------------------------- #


def test_build_review_happy_path():
    r = eval_review.build_review(
        agent="strategist",
        output_type="pdf_report",
        client_id="acme",
        source="clients/acme/outputs/reports/x.pdf",
        specificity=4,
        accuracy=5,
        usefulness=4,
        sendable=True,
        notes="hooks 3 and 5 are weak",
    )
    assert r.agent == "strategist"
    assert r.client_id == "acme"
    assert r.prospect_id is None
    assert r.specificity == 4
    assert r.sendable is True
    # ISO-8601 UTC timestamp
    assert r.timestamp.endswith("+00:00") or r.timestamp.endswith("Z")


def test_build_review_rejects_unknown_agent():
    with pytest.raises(ValueError, match="agent must be one of"):
        eval_review.build_review(
            agent="not-an-agent",
            output_type="x",
            specificity=3, accuracy=3, usefulness=3,
            sendable=False, notes="",
        )


@pytest.mark.parametrize("score", [0, -1, 6, 99])
def test_build_review_rejects_score_out_of_range(score: int):
    with pytest.raises(ValueError, match="must be in"):
        eval_review.build_review(
            agent="producer", output_type="kling_video",
            specificity=score, accuracy=3, usefulness=3,
            sendable=True, notes="",
        )


@pytest.mark.parametrize("raw,expected", [
    ("y", True), ("yes", True), ("YES", True), ("1", True), ("true", True),
    ("n", False), ("no", False), ("NO", False), ("0", False), ("false", False),
])
def test_validate_sendable_accepts_common_forms(raw: str, expected: bool):
    assert eval_review._validate_sendable(raw) is expected


def test_validate_sendable_rejects_garbage():
    with pytest.raises(ValueError, match="sendable must be yes/no"):
        eval_review._validate_sendable("maybe")


# --------------------------------------------------------------------------- #
# append_review() round-trip + append semantics
# --------------------------------------------------------------------------- #


def test_append_review_writes_one_line(tmp_path: Path):
    target = tmp_path / "evals" / "output_reviews.jsonl"
    r = eval_review.build_review(
        agent="outreach", output_type="pitch_pdf", prospect_id="gymshark",
        source="prospects/gymshark/pitch.pdf",
        specificity=5, accuracy=4, usefulness=5, sendable=True,
        notes="Strong hook list; CTA needs softening.",
    )
    written = eval_review.append_review(r, eval_path=target)
    assert written == target
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["agent"] == "outreach"
    assert parsed["prospect_id"] == "gymshark"
    assert parsed["sendable"] is True
    assert parsed["notes"].startswith("Strong hook")


def test_append_review_appends_without_overwriting(tmp_path: Path):
    """The second append must not clobber the first row, and the file
    must remain valid JSONL (one JSON object per line, all parseable)."""
    target = tmp_path / "evals" / "output_reviews.jsonl"
    for i in range(3):
        r = eval_review.build_review(
            agent="analyst", output_type="constraint",
            client_id="acme",
            specificity=3, accuracy=3, usefulness=3,
            sendable=False, notes=f"row {i}",
        )
        eval_review.append_review(r, eval_path=target)
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert [row["notes"] for row in rows] == ["row 0", "row 1", "row 2"]


def test_append_review_creates_parent_directory(tmp_path: Path):
    """A fresh clone won't have evals/; the appender must mkdir for the
    operator instead of crashing on FileNotFoundError."""
    deeply_nested = tmp_path / "fresh" / "clone" / "evals" / "output_reviews.jsonl"
    assert not deeply_nested.parent.exists()
    r = eval_review.build_review(
        agent="producer", output_type="kling_video",
        specificity=2, accuracy=2, usefulness=2, sendable=False, notes="",
    )
    eval_review.append_review(r, eval_path=deeply_nested)
    assert deeply_nested.exists()


# --------------------------------------------------------------------------- #
# CLI non-interactive path - reachable from CI without a TTY
# --------------------------------------------------------------------------- #


def test_cli_non_interactive_writes_expected_row(tmp_path: Path):
    target = tmp_path / "out.jsonl"
    rc = eval_review.main([
        "--agent", "strategist",
        "--client-id", "acme",
        "--output-type", "pdf_report",
        "--source", "clients/acme/outputs/reports/x.pdf",
        "--specificity", "4",
        "--accuracy", "5",
        "--usefulness", "4",
        "--sendable", "yes",
        "--notes", "Solid; hooks 3+5 weak.",
        "--non-interactive",
        "--eval-path", str(target),
    ])
    assert rc == 0
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["agent"] == "strategist"
    assert rows[0]["specificity"] == 4
    assert rows[0]["sendable"] is True
    assert rows[0]["notes"] == "Solid; hooks 3+5 weak."


def test_cli_non_interactive_requires_all_grade_flags(tmp_path: Path):
    """In --non-interactive mode the CLI cannot prompt; missing grades
    must fail loudly rather than silently writing zeros."""
    with pytest.raises(SystemExit) as exc:
        eval_review.main([
            "--agent", "producer",
            "--output-type", "kling_video",
            "--non-interactive",
            "--eval-path", str(tmp_path / "x.jsonl"),
            # Missing all grade flags.
        ])
    assert "specificity" in str(exc.value)
