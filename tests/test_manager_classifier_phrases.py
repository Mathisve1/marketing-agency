"""V1.9 phrase-coverage tests for the Manager classifier.

The existing test_manager_service.py covers the structural classifier
contract (kinds, fields, refusal paths). This module is a wider safety
net of operator phrasings - the way a solo operator naturally talks
to the Manager, including the 22 example phrases the user explicitly
listed in the V1.9 brief plus additional adjacent variants (capitalisation,
trailing punctuation, alternate verbs).

Format: every row is `(prompt, expected_kind, expected_task_type)`.
- expected_kind: "overview" / "client_overview" / "dispatch" / "approve"
                 / "reject" / "locate" / "clarify"
- expected_task_type: only checked when expected_kind == "dispatch";
                     ignored otherwise.

Discipline: the classifier is deterministic + keyword-based by design.
If a prompt is genuinely ambiguous, the test should expect "clarify",
not bend the classifier to guess.
"""
from __future__ import annotations

import pytest

from services.manager_service import classify_manager_request

KNOWN_CLIENTS = ["acme", "zelda"]


# --------------------------------------------------------------------------- #
# 1. The 22 phrases from the user's V1.9 brief (must all classify correctly)
# --------------------------------------------------------------------------- #


_BRIEF_PHRASES = [
    # Overview / inbox
    ("What do I need to approve today?", "overview", None),
    ("What is waiting on me?", "overview", None),
    ("What should I do next?", "overview", None),
    ("Give me my daily overview.", "overview", None),
    ("Show open tasks.", "overview", None),

    # Client overview (overview phrasing + client name in prompt)
    ("Show tasks for client acme.", "client_overview", None),

    # Outreach
    ("Start prospecting for Belgian restaurants.", "dispatch", "outreach"),
    ("Find new leads in fitness.", "dispatch", "outreach"),
    ("Run outreach for skincare brands.", "dispatch", "outreach"),

    # Strategist (research)
    ("Research competitors for client acme.", "dispatch", "research"),
    ("Look into the market for client acme.", "dispatch", "research"),
    ("Find candidate hooks for client acme.", "dispatch", "research"),

    # Producer
    ("Create a video plan for client acme.", "dispatch", "produce"),
    ("Make a new creative concept for client acme.", "dispatch", "produce"),
    ("Prepare a video for client acme.", "dispatch", "produce"),

    # Analyst
    ("Analyze performance for client acme.", "dispatch", "analyze"),
    ("Check Meta results for client acme.", "dispatch", "analyze"),
    ("Review what is underperforming for client acme.", "dispatch", "analyze"),

    # Approve / reject (with VP-### token)
    ("Approve VP-004 for acme.", "approve", None),
    ("Reject VP-004 for acme.", "reject", None),

    # Locate
    ("Where is the pitch PDF for prospect gymshark?", "locate", None),
    ("Where can I find the output for this task?", "locate", None),
]


@pytest.mark.parametrize("prompt,expected_kind,expected_task_type", _BRIEF_PHRASES)
def test_brief_phrases_classify_correctly(
    prompt: str, expected_kind: str, expected_task_type: str | None,
):
    intent = classify_manager_request(prompt, known_clients=KNOWN_CLIENTS)
    assert intent.kind == expected_kind, (
        f"prompt={prompt!r} kind={intent.kind!r} expected={expected_kind!r}"
    )
    if expected_task_type is not None:
        assert intent.task_type == expected_task_type, (
            f"prompt={prompt!r} task_type={intent.task_type!r} "
            f"expected={expected_task_type!r}"
        )


# --------------------------------------------------------------------------- #
# 2. Adjacent variants: capitalisation, punctuation, leading filler.
#    These exist to catch regressions where someone tightens the keyword
#    matching (e.g. word-boundary strict regex) and breaks natural typing.
# --------------------------------------------------------------------------- #


_VARIANT_PHRASES = [
    # Casing
    ("WHAT DO I NEED TO APPROVE TODAY?", "overview", None),
    ("show TASKS for client acme", "client_overview", None),
    # No trailing punctuation
    ("what is waiting on me", "overview", None),
    ("show open tasks", "overview", None),
    # Trailing question mark vs period
    ("Show tasks for client acme?", "client_overview", None),
    # Leading filler that should NOT block the keyword match
    ("hey, give me my daily overview please", "overview", None),
    ("ok so prepare a video for client acme", "dispatch", "produce"),
    ("could you please run outreach for skincare brands", "dispatch", "outreach"),
    # Contractions / colloquial
    ("what's underperforming for client acme", "dispatch", "analyze"),
    ("what's open right now", "overview", None),
    # Both client + outreach keyword (outreach should win - prospects, not silo work)
    ("find new leads for the fitness niche", "dispatch", "outreach"),
]


@pytest.mark.parametrize("prompt,expected_kind,expected_task_type", _VARIANT_PHRASES)
def test_phrasing_variants_classify_correctly(
    prompt: str, expected_kind: str, expected_task_type: str | None,
):
    intent = classify_manager_request(prompt, known_clients=KNOWN_CLIENTS)
    assert intent.kind == expected_kind, (
        f"prompt={prompt!r} kind={intent.kind!r} expected={expected_kind!r}"
    )
    if expected_task_type is not None:
        assert intent.task_type == expected_task_type


# --------------------------------------------------------------------------- #
# 3. Genuinely ambiguous prompts MUST classify as 'clarify'.
#    The classifier is deterministic; if a prompt could plausibly mean
#    several things, it must NOT guess.
# --------------------------------------------------------------------------- #


_AMBIGUOUS_PROMPTS = [
    "hello",
    "ok",
    "?",
    "do something",
    "client acme",                # name alone, no verb
    "VP-004",                     # plan_id alone, no verb
    "stuff for tomorrow",
    "are we good",
]


@pytest.mark.parametrize("prompt", _AMBIGUOUS_PROMPTS)
def test_ambiguous_prompts_classify_as_clarify(prompt: str):
    intent = classify_manager_request(prompt, known_clients=KNOWN_CLIENTS)
    assert intent.kind == "clarify", (
        f"prompt={prompt!r} should have been clarify but was {intent.kind!r}"
    )


# --------------------------------------------------------------------------- #
# 4. Plan-id + client extraction from approve/reject prompts.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prompt,expected_plan_id,expected_client", [
    ("Approve plan VP-001 for client acme", "VP-001", "acme"),
    ("Approve VP-042 for acme", "VP-042", "acme"),
    ("approve vp-7 for acme",  "VP-7",   "acme"),
    ("Reject VP-001 for acme", "VP-001", "acme"),
    ("reject plan VP-13 for client zelda", "VP-13", "zelda"),
    # No client in prompt, no explicit_client_id - approve still classifies
    # but client_id stays None (manager will then ask for clarification)
    ("approve plan VP-007", "VP-007", None),
])
def test_approve_reject_extracts_plan_id_and_client(
    prompt: str, expected_plan_id: str, expected_client: str | None,
):
    intent = classify_manager_request(prompt, known_clients=KNOWN_CLIENTS)
    assert intent.kind in ("approve", "reject")
    assert intent.plan_id == expected_plan_id
    assert intent.client_id == expected_client


# --------------------------------------------------------------------------- #
# 5. Sub-agent dispatch precedence.
#    A prompt that contains keywords for multiple workers should resolve to
#    the worker with the highest keyword count, mirroring core/router.py.
# --------------------------------------------------------------------------- #


def test_outreach_keywords_beat_research_when_outreach_dominant():
    intent = classify_manager_request(
        "find new leads in fitness; this is a market research task",
        known_clients=KNOWN_CLIENTS,
    )
    # "find new leads" + "leads in" = 2 outreach hits; "market research" = 1 research hit
    assert intent.kind == "dispatch"
    assert intent.task_type == "outreach"


def test_explicit_task_type_overrides_keyword_inference():
    """Per the existing manager_service contract: explicit_task_type wins
    even if the prompt looks like an overview question."""
    intent = classify_manager_request(
        "what is waiting on me",
        explicit_task_type="produce",
    )
    assert intent.kind == "dispatch"
    assert intent.task_type == "produce"


# --------------------------------------------------------------------------- #
# 6. Anti-regression: the new keywords must not silently turn benign
#    prompts into dispatches.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prompt", [
    "show me the file at clients/acme/outputs/x.mp4",  # locate, not dispatch
    "what is open right now",                           # overview, not dispatch
    "what should i do next",                            # overview, not dispatch
])
def test_new_keywords_do_not_misroute_to_dispatch(prompt: str):
    intent = classify_manager_request(prompt, known_clients=KNOWN_CLIENTS)
    assert intent.kind != "dispatch", (
        f"prompt={prompt!r} should NOT have routed to dispatch; got {intent.kind!r}"
    )
