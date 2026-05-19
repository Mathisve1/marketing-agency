"""Tests for the Kling Omni client surface.

Focus: the `mode` field is normalised at the wire boundary.

Kling Omni V3's API rejects `mode='professional'` with `code=1201`. The
client therefore aliases the human-readable label `"professional"` to
the literal short code `"pro"` (and `"standard"` to `"std"`) when
serialising the request body. The brief / preflight layer can keep the
readable label so docs and tests stay legible; the wire payload always
carries the short code.

These tests exercise the helper directly and assert it's used inside
`submit_omni_video()` so a regression that drops the alias would be
caught immediately.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.producer.kling.client import (
    _MODE_ALIASES,
    KlingClient,
    _normalize_mode,
)

# --------------------------------------------------------------------------- #
# _normalize_mode helper
# --------------------------------------------------------------------------- #


def test_normalize_mode_translates_professional_to_pro():
    """The historical human-readable label `"professional"` maps to
    Kling's short code `"pro"` — this is the bug that caused
    `code 1201 'mode value "professional" is invalid'` on first
    submit."""
    assert _normalize_mode("professional") == "pro"


def test_normalize_mode_translates_standard_to_std():
    """`"standard"` is also a human-readable label we accept and map
    to the Kling short code `"std"`."""
    assert _normalize_mode("standard") == "std"


def test_normalize_mode_is_case_insensitive_on_alias_lookup():
    """Operators may type `"Professional"` or `"PROFESSIONAL"`; the
    alias map is case-insensitive on the lookup side so both still
    normalise to `"pro"`."""
    assert _normalize_mode("Professional") == "pro"
    assert _normalize_mode("PROFESSIONAL") == "pro"
    assert _normalize_mode("STANDARD") == "std"


def test_normalize_mode_passes_through_short_codes():
    """The literal short codes the API accepts must round-trip
    unchanged — never aliased onto themselves and never modified."""
    assert _normalize_mode("pro") == "pro"
    assert _normalize_mode("std") == "std"


def test_normalize_mode_passes_through_unknown_value_verbatim():
    """An unknown mode value falls through verbatim so a future Kling
    schema change surfaces a clear API 400 instead of being silently
    rewritten to `"std"`. This is the 'don't hide validation errors'
    discipline."""
    assert _normalize_mode("ultra") == "ultra"
    assert _normalize_mode("") == ""


def test_normalize_mode_handles_non_string_defensively():
    """Defensive: non-string input is returned unchanged so the API
    layer produces a typed error instead of the normaliser crashing."""
    assert _normalize_mode(None) is None  # type: ignore[arg-type]
    assert _normalize_mode(123) == 123    # type: ignore[arg-type]


def test_mode_aliases_table_documents_known_translations():
    """The alias table is part of the public contract this module
    exposes — pin it so a future refactor doesn't quietly drop an
    entry."""
    assert _MODE_ALIASES == {
        "professional": "pro",
        "standard": "std",
    }


# --------------------------------------------------------------------------- #
# submit_omni_video applies the normalisation when building the body
# --------------------------------------------------------------------------- #


def _make_client() -> KlingClient:
    """Construct a KlingClient without going through the JWT mint path.
    We pass explicit access/secret keys so the env doesn't have to
    carry them, and we never actually hit the network in these tests
    (the `_post` method is patched per test)."""
    return KlingClient(access_key="test-access", secret_key="test-secret")


def test_submit_omni_video_normalises_professional_to_pro_in_body():
    """The body that goes to Kling must carry `mode='pro'` even when
    the caller passed the readable label `'professional'`."""
    client = _make_client()
    captured: dict = {}

    def fake_post(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"task_id": "task-xyz"}

    with patch.object(client, "_post", side_effect=fake_post):
        task_id = client.submit_omni_video(
            prompt="anything",
            mode="professional",
        )
    assert task_id == "task-xyz"
    assert captured["body"]["mode"] == "pro"


def test_submit_omni_video_normalises_standard_to_std_in_body():
    client = _make_client()
    captured: dict = {}

    with patch.object(client, "_post", side_effect=lambda p, b: captured.update(body=b) or {"task_id": "t"}):
        client.submit_omni_video(prompt="x", mode="standard")
    assert captured["body"]["mode"] == "std"


def test_submit_omni_video_passes_through_pro_unchanged():
    """When the caller already uses the short code, the wire body
    carries it verbatim. Same for `'std'`."""
    client = _make_client()
    captured: dict = {}

    with patch.object(client, "_post", side_effect=lambda p, b: captured.update(body=b) or {"task_id": "t"}):
        client.submit_omni_video(prompt="x", mode="pro")
    assert captured["body"]["mode"] == "pro"

    captured.clear()
    with patch.object(client, "_post", side_effect=lambda p, b: captured.update(body=b) or {"task_id": "t"}):
        client.submit_omni_video(prompt="x", mode="std")
    assert captured["body"]["mode"] == "std"


def test_submit_omni_video_default_mode_is_pro():
    """The new default — was `'professional'` (rejected by Kling), now
    `'pro'` (the literal API short code)."""
    client = _make_client()
    captured: dict = {}

    with patch.object(client, "_post", side_effect=lambda p, b: captured.update(body=b) or {"task_id": "t"}):
        client.submit_omni_video(prompt="x")  # no mode arg
    assert captured["body"]["mode"] == "pro"


def test_kling_video_brief_default_mode_is_pro():
    """The brief / preflight dataclass default — also flipped to the
    short code so a brief built with no explicit mode field already
    holds the value Kling will accept."""
    from agents.producer.brief_compiler import KlingVideoBrief
    brief = KlingVideoBrief(
        prompt="p", negative_prompt="n", duration=10,
    )
    assert brief.mode == "pro"


def test_kling_video_brief_accepts_professional_label():
    """The dataclass itself does not reject `'professional'` — the
    alias mapping lives at the Kling client wire boundary so brief
    documents and tests can keep the readable label."""
    from agents.producer.brief_compiler import KlingVideoBrief
    brief = KlingVideoBrief(
        prompt="p", negative_prompt="n", duration=10, mode="professional",
    )
    assert brief.mode == "professional"  # value preserved
    # And when submitted via the client it gets aliased:
    assert _normalize_mode(brief.mode) == "pro"


# --------------------------------------------------------------------------- #
# Reference smoke: existing tests use mode='professional'; that surface
# is not broken (the dataclass still accepts the label).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("readable_label", ["professional", "standard"])
def test_readable_labels_survive_round_trip_through_brief(readable_label: str):
    """A brief constructed with the readable label keeps the label on
    the dataclass (so docs / inbox / approval surfaces can show the
    human-readable form), but the Kling client translates it on send.
    This is the contract the rest of the test suite relies on when it
    passes `mode='professional'` to construct briefs."""
    from agents.producer.brief_compiler import KlingVideoBrief
    brief = KlingVideoBrief(
        prompt="p", negative_prompt="n", duration=10, mode=readable_label,
    )
    assert brief.mode == readable_label
    expected_short = {"professional": "pro", "standard": "std"}[readable_label]
    assert _normalize_mode(brief.mode) == expected_short
