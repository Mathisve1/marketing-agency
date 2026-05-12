"""Smoke tests for the Analyst's aggregation + evaluation logic.

Covers:
- Spend-weighted ROAS math (basic, ignoring null-ROAS rows, single-ad case)
- Aggregation grouping + dropping rows without hook_id
- Pass/fail rules with both targets, ROAS-only, CTR-only, none
- Insufficient spend short-circuit
- skip_hook_ids suppresses proposed_rule on failed hooks
- _parse_ad_name extracts hook_id and motion_id from the convention
- _extract_purchase_roas priority across action_type variants
"""
from __future__ import annotations

from agents.analyst.context_writer import (
    HookPerformance,
    aggregate_by_hook,
    evaluate_hooks,
)
from agents.analyst.meta_insights import _extract_purchase_roas, _parse_ad_name


def _row(
    hook_id: str | None = "WH-001",
    motion_id: str | None = "RM-001",
    spend: float = 50.0,
    impressions: int = 1000,
    clicks: int = 20,
    roas: float | None = 2.0,
    ad_id: str = "ad_1",
) -> dict:
    return {
        "ad_id": ad_id,
        "ad_name": f"Audience_{hook_id}_{motion_id}_v1" if hook_id else "BrandAwareness_v1",
        "spend": spend,
        "impressions": impressions,
        "inline_link_clicks": clicks,
        "ctr": clicks / impressions if impressions else 0.0,
        "purchase_roas": roas,
        "audience_name": "Audience",
        "hook_id": hook_id,
        "motion_id": motion_id,
    }


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def test_aggregate_groups_by_hook_id():
    insights = [
        _row(hook_id="WH-001", spend=100, ad_id="a"),
        _row(hook_id="WH-001", spend=200, ad_id="b"),
        _row(hook_id="WH-002", spend=50, ad_id="c"),
    ]
    agg = aggregate_by_hook(insights)
    assert set(agg.keys()) == {"WH-001", "WH-002"}
    assert agg["WH-001"].total_spend == 300
    assert agg["WH-001"].ad_count == 2
    assert agg["WH-002"].total_spend == 50


def test_aggregate_skips_rows_with_no_hook_id():
    insights = [
        _row(hook_id="WH-001", spend=100),
        _row(hook_id=None, motion_id=None, spend=999, ad_id="brand"),
    ]
    agg = aggregate_by_hook(insights)
    assert "WH-001" in agg
    assert len(agg) == 1
    assert agg["WH-001"].total_spend == 100


def test_spend_weighted_roas_basic():
    """Ad A: $100 @ ROAS 1.0, Ad B: $300 @ ROAS 4.0.
    Weighted ROAS = (100*1 + 300*4) / (100+300) = 1300/400 = 3.25"""
    insights = [
        _row(hook_id="WH-001", spend=100, roas=1.0, ad_id="A"),
        _row(hook_id="WH-001", spend=300, roas=4.0, ad_id="B"),
    ]
    agg = aggregate_by_hook(insights)
    assert agg["WH-001"].weighted_roas == 3.25


def test_spend_weighted_roas_ignores_rows_with_no_roas():
    """ROAS-less rows count in total_spend but not in weighted ROAS."""
    insights = [
        _row(hook_id="WH-001", spend=100, roas=2.0, ad_id="A"),
        _row(hook_id="WH-001", spend=400, roas=None, ad_id="B"),
    ]
    agg = aggregate_by_hook(insights)
    assert agg["WH-001"].total_spend == 500
    assert agg["WH-001"].weighted_roas == 2.0  # Only A contributes


def test_weighted_ctr_summed_correctly():
    insights = [
        _row(hook_id="WH-001", impressions=1000, clicks=10, ad_id="A"),
        _row(hook_id="WH-001", impressions=2000, clicks=30, ad_id="B"),
    ]
    agg = aggregate_by_hook(insights)
    # Total impressions 3000, total clicks 40 -> CTR 40/3000 = 0.013333
    assert abs(agg["WH-001"].weighted_ctr - (40 / 3000)) < 1e-6


def test_aggregate_empty_input_returns_empty():
    assert aggregate_by_hook([]) == {}


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _perf(
    spend: float = 200,
    roas: float | None = 3.0,
    ctr: float = 0.02,
    hook_id: str = "WH-001",
) -> HookPerformance:
    return HookPerformance(
        hook_id=hook_id, total_spend=spend, weighted_roas=roas,
        weighted_ctr=ctr, ad_count=2, impressions=10000, clicks=200,
    )


def test_evaluate_passes_hook_meeting_both_targets():
    results = evaluate_hooks(
        {"WH-001": _perf(roas=3.0, ctr=0.02)},
        roas_target=2.0, ctr_target=0.015,
    )
    assert results[0].failed is False
    assert results[0].proposed_rule is None


def test_evaluate_fails_hook_below_roas_target():
    results = evaluate_hooks(
        {"WH-001": _perf(roas=1.0, ctr=0.02)},
        roas_target=2.0, ctr_target=0.015,
    )
    assert results[0].failed is True
    assert "ROAS 1.00 < target 2.00" in results[0].reasons
    assert results[0].proposed_rule == "Do not use Hook WH-001 for this client"
    assert "WH-001" in results[0].proposed_reason


def test_evaluate_fails_hook_below_ctr_target():
    results = evaluate_hooks(
        {"WH-001": _perf(roas=3.0, ctr=0.005)},
        roas_target=2.0, ctr_target=0.015,
    )
    assert results[0].failed is True
    assert any("CTR" in r for r in results[0].reasons)


def test_insufficient_spend_short_circuits():
    """Below min_spend_usd -> failed=False even with terrible metrics."""
    results = evaluate_hooks(
        {"WH-001": _perf(spend=10, roas=0.1, ctr=0.0001)},
        roas_target=2.0, ctr_target=0.015,
    )
    assert results[0].failed is False
    assert "insufficient spend" in results[0].reasons[0]
    assert results[0].proposed_rule is None


def test_skip_hook_ids_suppresses_proposed_constraint():
    results = evaluate_hooks(
        {"WH-001": _perf(roas=1.0, ctr=0.005)},
        roas_target=2.0, ctr_target=0.015,
        skip_hook_ids={"WH-001"},
    )
    assert results[0].failed is True            # still failed
    assert results[0].proposed_rule is None     # but no proposed write


def test_no_roas_target_means_no_roas_check():
    results = evaluate_hooks(
        {"WH-001": _perf(roas=0.5, ctr=0.02)},
        roas_target=None, ctr_target=0.015,
    )
    assert results[0].failed is False


def test_no_targets_passes_everything():
    results = evaluate_hooks(
        {"WH-001": _perf(roas=0.1, ctr=0.001)},
        roas_target=None, ctr_target=None,
    )
    assert results[0].failed is False


def test_null_roas_does_not_trigger_roas_failure():
    """If purchase_roas is None (no purchases tracked), don't fail on ROAS."""
    results = evaluate_hooks(
        {"WH-001": _perf(roas=None, ctr=0.02)},
        roas_target=2.0, ctr_target=0.015,
    )
    assert results[0].failed is False


# --------------------------------------------------------------------------- #
# Ad-name parsing (meta_insights helpers)
# --------------------------------------------------------------------------- #


def test_parse_ad_name_extracts_hook_and_motion():
    audience, hook, motion = _parse_ad_name("BelgianFamilies_WH-003_RM-002_v1")
    assert audience == "BelgianFamilies"
    assert hook == "WH-003"
    assert motion == "RM-002"


def test_parse_ad_name_missing_motion_ok():
    audience, hook, motion = _parse_ad_name("Audience_WH-007_notes")
    assert audience == "Audience"
    assert hook == "WH-007"
    assert motion is None


def test_parse_ad_name_no_convention_returns_nones():
    audience, hook, motion = _parse_ad_name("BrandAwareness")
    assert hook is None and motion is None


def test_parse_ad_name_empty_string():
    assert _parse_ad_name("") == (None, None, None)


def test_extract_purchase_roas_prefers_omni_purchase():
    raw = [
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "1.5"},
        {"action_type": "omni_purchase", "value": "2.8"},
    ]
    assert _extract_purchase_roas(raw) == 2.8


def test_extract_purchase_roas_falls_through_to_other_types():
    raw = [{"action_type": "purchase", "value": "3.2"}]
    assert _extract_purchase_roas(raw) == 3.2


def test_extract_purchase_roas_handles_empty_and_none():
    assert _extract_purchase_roas(None) is None
    assert _extract_purchase_roas([]) is None
