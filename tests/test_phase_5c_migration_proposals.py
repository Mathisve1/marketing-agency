"""Phase 5C — static pins for the migration 011 + 012 proposals.

Deterministic, fast (no network, no Supabase). Pure file-content
assertions. Catches accidental drift on:

  - 011 carries the "SUPERSEDED BY MIGRATION 012" banner so the
    operator can't accidentally apply it thinking it's canonical.
  - 012 still carries `DO NOT APPLY — proposal only`.
  - 012 includes every column listed in the Phase 5C spec
    (`variant_number`, `approved_internal_at`, `client_shared_at`,
    `client_decision_status`).
  - 012's status CHECK constraint covers every lifecycle value
    documented in `docs/client_visual_review_lifecycle_plan.md`.
  - 012 proposes the `client_creative_assets_v` view AND the view is
    gated by `client_shared_at IS NOT NULL` so a prepared-but-not-
    shared row stays invisible to the portal.
  - 012's view DOES NOT project storage_key, internal_asset_url,
    template_id, theme_id, status, approved_internal_at, created_by.

No DDL is executed by these tests — they read the files as text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIG_011 = ROOT / "supabase" / "migrations" / "011_client_safe_visual_preview.sql"
MIG_012 = ROOT / "supabase" / "migrations" / "012_creative_assets_proposal.sql"


@pytest.fixture(scope="module")
def text_011() -> str:
    return MIG_011.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def text_012() -> str:
    return MIG_012.read_text(encoding="utf-8")


# -------------------- 011 — superseded banner --------------------


def test_011_marked_superseded(text_011: str) -> None:
    assert "SUPERSEDED BY MIGRATION 012" in text_011, (
        "Migration 011 must declare it has been superseded by 012 in "
        "its header so the operator does not apply it by mistake."
    )


def test_011_still_says_do_not_apply(text_011: str) -> None:
    assert "DO NOT APPLY" in text_011.upper(), (
        "Migration 011 must keep its DO-NOT-APPLY banner even though "
        "it is now the fallback path."
    )


# -------------------- 012 — proposal banner + safety --------------------


def test_012_says_do_not_apply(text_012: str) -> None:
    assert "DO NOT APPLY" in text_012.upper()
    # The exact wording matters because operators search for it.
    assert "proposal only" in text_012.lower()


def test_012_includes_every_phase_5c_column(text_012: str) -> None:
    required = (
        "variant_number",
        "approved_internal_at",
        "client_shared_at",
        "client_decision_status",
    )
    for col in required:
        assert col in text_012, (
            f"Migration 012 is missing the Phase 5C column `{col}`."
        )


def test_012_status_check_covers_full_lifecycle(text_012: str) -> None:
    # Pull the status check block; assert each canonical status
    # appears in the constraint body.
    canonical = (
        "draft",
        "rendered_internal",
        "approved_internal",
        "client_preview_prepared",
        "shared_with_client",
        "approved_by_client",
        "changes_requested_by_client",
        "archived",
    )
    m = re.search(
        r"creative_assets_status_check[\s\S]{0,1200}?check\s*\(([\s\S]+?)\)",
        text_012,
    )
    assert m is not None, (
        "creative_assets_status_check constraint not found in 012."
    )
    block = m.group(1)
    for status in canonical:
        assert f"'{status}'" in block, (
            f"status `{status}` missing from the 012 status CHECK."
        )


def test_012_client_decision_status_check_present(text_012: str) -> None:
    m = re.search(
        r"creative_assets_client_decision_check[\s\S]{0,500}?check\s*\(([\s\S]+?)\)",
        text_012,
    )
    assert m is not None
    block = m.group(1)
    for decision in ("approved", "changes_requested", "rejected"):
        assert f"'{decision}'" in block


def test_012_proposes_client_creative_assets_view(text_012: str) -> None:
    assert "create or replace view public.client_creative_assets_v" in text_012, (
        "Migration 012 must propose the client-safe view."
    )


def test_012_view_is_gated_by_client_shared_at(text_012: str) -> None:
    # The view section: extract everything between the `create or
    # replace view` header and the next `comment on view` (or EOF).
    m = re.search(
        r"create or replace view public\.client_creative_assets_v\s+as([\s\S]+?)(?=comment on view|\n-- ---|$)",
        text_012,
    )
    assert m is not None
    body = m.group(1).lower()
    assert "client_shared_at is not null" in body, (
        "client_creative_assets_v must filter on client_shared_at IS "
        "NOT NULL so prepared-but-not-shared rows stay invisible."
    )
    assert "client_safe_visual_url is not null" in body, (
        "client_creative_assets_v must also require a non-null "
        "client_safe_visual_url."
    )


def test_012_view_does_not_project_internal_columns(text_012: str) -> None:
    m = re.search(
        r"create or replace view public\.client_creative_assets_v\s+as([\s\S]+?)from public\.creative_assets",
        text_012,
    )
    assert m is not None
    select_clause = m.group(1).lower()
    # Match qualified `ca.<col>` references so substring collisions
    # (e.g. `status` inside `client_decision_status`) don't false-fire.
    forbidden = (
        "ca.storage_key",
        "ca.internal_asset_url",
        "ca.template_id",
        "ca.theme_id",
        "ca.status",
        "ca.approved_internal_at",
        "ca.created_by",
        "ca.prompt_summary",
    )
    # Tokenise on whitespace + commas to get standalone projections.
    tokens = re.split(r"[\s,]+", select_clause)
    for col in forbidden:
        assert col not in tokens, (
            f"client_creative_assets_v projects forbidden column "
            f"`{col}` — refuse to expose it to the client."
        )


# -------------------- Cross-file sanity --------------------


def test_012_status_aligns_with_status_lifecycle_doc() -> None:
    """The client_visual_review_lifecycle_plan doc must reference the
    same set of statuses as 012's CHECK constraint."""
    doc = (ROOT / "docs" / "client_visual_review_lifecycle_plan.md").read_text(
        encoding="utf-8"
    )
    canonical = (
        "draft",
        "exported_internal",
        "approved_internal",
        "client_preview_prepared",
        "shared_with_client",
        "approved_by_client",
        "changes_requested_by_client",
        "archived",
    )
    for status in canonical:
        assert status in doc, (
            f"Lifecycle doc is missing canonical status `{status}`."
        )
