"""Performance evaluation + negative-constraint writer for the Analyst.

Pure functions: aggregate_by_hook, evaluate_hooks.
LangChain tool factories: make_evaluation_tool, make_constraint_writer,
make_analyst_summary_writer.

Failure rule: a hook fails when total_spend >= min_spend_usd (default $50)
AND (ROAS < roas_target OR CTR < ctr_target). Thresholds are read from
MASTER_CONTEXT.md's performance_benchmarks at evaluation time, not hard-coded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from langchain_core.tools import tool

from core.client_context import ClientContext
from core.context_schema import (
    AddedBy,
    NegativeConstraint,
    Severity,
)


DEFAULT_MIN_SPEND_USD = 50.0

# Convention enforced by write_negative_constraint below: rule always contains
# the hook ID. We parse it back out to detect "already constrained" hooks
# without a schema change.
_HOOK_ID_IN_RULE_RX = re.compile(r"\b(WH-\d+)\b")


@dataclass
class HookPerformance:
    hook_id: str
    total_spend: float
    weighted_roas: Optional[float]
    weighted_ctr: float
    ad_count: int
    impressions: int
    clicks: int


@dataclass
class HookEvaluation:
    hook_id: str
    failed: bool
    reasons: list[str]
    metrics: dict
    proposed_rule: Optional[str] = None
    proposed_reason: Optional[str] = None


def aggregate_by_hook(insights: list[dict]) -> dict[str, HookPerformance]:
    """Group ad-level insights by hook_id; spend-weighted ROAS + summed CTR.

    Rows without a parseable hook_id are skipped (e.g. brand-awareness boosts
    where the media buyer didn't follow the WH-/RM- convention).
    """
    bucket: dict[str, list[dict]] = {}
    for row in insights:
        hook_id = row.get("hook_id")
        if not hook_id:
            continue
        bucket.setdefault(hook_id, []).append(row)

    out: dict[str, HookPerformance] = {}
    for hook_id, rows in bucket.items():
        total_spend = sum(float(r.get("spend") or 0) for r in rows)
        total_impressions = sum(int(r.get("impressions") or 0) for r in rows)
        total_clicks = sum(int(r.get("inline_link_clicks") or 0) for r in rows)

        # Spend-weighted ROAS - only ads with a reported ROAS contribute.
        roas_rows = [r for r in rows if r.get("purchase_roas") is not None]
        roas_spend = sum(float(r["spend"]) for r in roas_rows)
        weighted_roas: Optional[float] = None
        if roas_rows and roas_spend > 0:
            weighted_roas = sum(
                float(r["purchase_roas"]) * float(r["spend"]) for r in roas_rows
            ) / roas_spend

        weighted_ctr = (total_clicks / total_impressions) if total_impressions else 0.0

        out[hook_id] = HookPerformance(
            hook_id=hook_id,
            total_spend=round(total_spend, 2),
            weighted_roas=round(weighted_roas, 4) if weighted_roas is not None else None,
            weighted_ctr=round(weighted_ctr, 6),
            ad_count=len(rows),
            impressions=total_impressions,
            clicks=total_clicks,
        )
    return out


def evaluate_hooks(
    performance: dict[str, HookPerformance],
    roas_target: Optional[float],
    ctr_target: Optional[float],
    min_spend_usd: float = DEFAULT_MIN_SPEND_USD,
    skip_hook_ids: Optional[set[str]] = None,
) -> list[HookEvaluation]:
    """Apply the failure rule to each hook. Returns one HookEvaluation per hook."""
    skip_hook_ids = skip_hook_ids or set()
    results: list[HookEvaluation] = []

    for hook_id, perf in performance.items():
        metrics = {
            "total_spend": perf.total_spend,
            "weighted_roas": perf.weighted_roas,
            "weighted_ctr": perf.weighted_ctr,
            "ad_count": perf.ad_count,
            "impressions": perf.impressions,
            "clicks": perf.clicks,
        }

        # Insufficient signal: skip pass/fail call.
        if perf.total_spend < min_spend_usd:
            results.append(HookEvaluation(
                hook_id=hook_id, failed=False,
                reasons=[f"insufficient spend ${perf.total_spend:.2f} < ${min_spend_usd:.2f}"],
                metrics=metrics,
            ))
            continue

        reasons: list[str] = []
        if (
            roas_target is not None
            and perf.weighted_roas is not None
            and perf.weighted_roas < roas_target
        ):
            reasons.append(f"ROAS {perf.weighted_roas:.2f} < target {roas_target:.2f}")
        if ctr_target is not None and perf.weighted_ctr < ctr_target:
            reasons.append(f"CTR {perf.weighted_ctr:.4f} < target {ctr_target:.4f}")

        failed = bool(reasons)
        proposed_rule: Optional[str] = None
        proposed_reason: Optional[str] = None
        if failed and hook_id not in skip_hook_ids:
            proposed_rule = f"Do not use Hook {hook_id} for this client"
            roas_str = (
                f"{perf.weighted_roas:.2f}" if perf.weighted_roas is not None else "n/a"
            )
            proposed_reason = (
                f"Historical performance over ${perf.total_spend:.2f} spend "
                f"across {perf.ad_count} ad(s): ROAS {roas_str} "
                f"(target {roas_target}), CTR {perf.weighted_ctr:.4f} "
                f"(target {ctr_target})."
            )

        results.append(HookEvaluation(
            hook_id=hook_id,
            failed=failed,
            reasons=reasons,
            metrics=metrics,
            proposed_rule=proposed_rule,
            proposed_reason=proposed_reason,
        ))
    return results


def _existing_constrained_hook_ids(ctx: ClientContext) -> set[str]:
    """Scan MASTER_CONTEXT.md's negative_constraints for hook IDs already covered."""
    fm, _ = ctx.read()
    out: set[str] = set()
    for c in fm.negative_constraints:
        match = _HOOK_ID_IN_RULE_RX.search(c.rule)
        if match:
            out.add(match.group(1))
    return out


# --------------------------------------------------------------------------- #
# Tool factories (closure-based, same pattern as Strategist/Producer)
# --------------------------------------------------------------------------- #


def make_evaluation_tool(ctx: ClientContext):
    """LangChain tool: aggregate Meta insights by hook + apply pass/fail rules."""

    @tool("evaluate_performance")
    def evaluate_performance(
        insights: list[dict],
        min_spend_usd: float = DEFAULT_MIN_SPEND_USD,
    ) -> dict:
        """Aggregate per-hook spend/ROAS/CTR from a list of Meta insight rows
        (output of `pull_meta_insights`), then compare against MASTER_CONTEXT.md's
        roas_target and ctr_target. Returns:
          {
            "benchmarks_used": {roas_target, ctr_target, min_spend_usd},
            "evaluations": [{hook_id, failed, reasons, metrics,
                            proposed_rule, proposed_reason, already_constrained}],
            "warning": "..."  # only if benchmarks aren't configured
          }
        Failed hooks come with a proposed_rule + proposed_reason ready to feed
        into write_negative_constraint."""
        fm, _ = ctx.read()
        benchmarks = fm.performance_benchmarks
        skip = _existing_constrained_hook_ids(ctx)
        performance = aggregate_by_hook(insights)
        evaluations = evaluate_hooks(
            performance,
            roas_target=benchmarks.roas_target,
            ctr_target=benchmarks.ctr_target,
            min_spend_usd=min_spend_usd,
            skip_hook_ids=skip,
        )

        result: dict = {
            "benchmarks_used": {
                "roas_target": benchmarks.roas_target,
                "ctr_target": benchmarks.ctr_target,
                "min_spend_usd": min_spend_usd,
            },
            "evaluations": [
                {
                    "hook_id": ev.hook_id,
                    "failed": ev.failed,
                    "reasons": ev.reasons,
                    "metrics": ev.metrics,
                    "proposed_rule": ev.proposed_rule,
                    "proposed_reason": ev.proposed_reason,
                    "already_constrained": ev.hook_id in skip,
                }
                for ev in evaluations
            ],
        }
        if benchmarks.roas_target is None and benchmarks.ctr_target is None:
            result["warning"] = (
                "No performance benchmarks set in MASTER_CONTEXT.md. "
                "Cannot evaluate performance until roas_target and/or "
                "ctr_target are configured in performance_benchmarks."
            )
        return result

    return evaluate_performance


def make_constraint_writer(ctx: ClientContext):
    """LangChain tool: write a HARD negative constraint into MASTER_CONTEXT.md."""

    @tool("write_negative_constraint")
    def write_negative_constraint(
        hook_id: str,
        rule: str,
        reason: str,
    ) -> str:
        """Persist a HARD negative constraint into MASTER_CONTEXT.md. The
        Producer will enforce it on all future video generations via the
        Kling negative_prompt. Returns the auto-assigned constraint ID
        (e.g. NC-007), or a SKIPPED message if a constraint for this hook
        already exists.

        hook_id: e.g. 'WH-003' - the hook this constraint targets.
        rule:    short rule text; must contain the hook_id substring so the
                 'already constrained' guard can detect duplicates.
        reason:  justification with metrics, typically the proposed_reason
                 returned by evaluate_performance.
        """
        existing = _existing_constrained_hook_ids(ctx)
        if hook_id in existing:
            return f"SKIPPED: {hook_id} already has a negative constraint."
        # Guard the convention - the duplicate-detection regex relies on the
        # hook ID appearing in the rule text.
        if hook_id not in rule:
            rule = f"{rule} ({hook_id})"
        constraint = NegativeConstraint(
            rule=rule,
            reason=reason,
            severity=Severity.HARD,
            added_by=AddedBy.ANALYST,
            added_at=datetime.now(timezone.utc),
        )
        return ctx.add_negative_constraint(constraint)

    return write_negative_constraint


def make_analyst_summary_writer(ctx: ClientContext):
    """LangChain tool: append a one-paragraph analyst summary to MASTER_CONTEXT.md."""

    @tool("append_analyst_summary")
    def append_analyst_summary(summary: str) -> str:
        """Append a 2-4 sentence summary of this analyst run to the
        'Recent Strategic Notes' section of MASTER_CONTEXT.md."""
        ctx.append_narrative("Recent Strategic Notes", summary)
        return "OK"

    return append_analyst_summary
