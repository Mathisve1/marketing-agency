"""Performance evaluation + negative-constraint writer for the Analyst.

V1.1 refactor: deprecated the separate pull_meta_insights and
evaluate_performance tools. The Analyst LLM no longer routes raw
insights JSON between tools.

V1.2 refactor: dynamic lists moved to SQL. _existing_constrained_hook_ids
now reads negative_constraints via ctx.get_negative_constraints() instead
of fm.negative_constraints (the YAML field no longer exists).

Pure functions (internal):
  - aggregate_by_hook        spend-weighted ROAS, summed CTR per hook
  - evaluate_hooks           apply pass/fail rule per hook
  - _format_evaluation_summary  compressed text for the LLM

LangChain tool factories (exposed to the LLM):
  - make_analyze_campaign_performance_tool   data routing + scoring + format
  - make_constraint_writer                   writes a HARD NegativeConstraint
  - make_analyst_summary_writer              appends a narrative note
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from langchain_core.tools import tool

from agents.analyst.meta_insights import fetch_meta_insights_data
from core.client_context import ClientContext
from core.context_schema import (
    AddedBy,
    NegativeConstraint,
    Severity,
)

DEFAULT_MIN_SPEND_USD = 50.0
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
    already_constrained: bool = False


# --------------------------------------------------------------------------- #
# Pure functions (used internally by analyze_campaign_performance)
# --------------------------------------------------------------------------- #


def aggregate_by_hook(insights: list[dict]) -> dict[str, HookPerformance]:
    """Group ad-level insights by hook_id; spend-weighted ROAS + summed CTR."""
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
    """Apply the failure rule per hook. Returns one HookEvaluation per hook."""
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

        if perf.total_spend < min_spend_usd:
            results.append(HookEvaluation(
                hook_id=hook_id, failed=False,
                reasons=[f"insufficient spend ${perf.total_spend:.2f} < ${min_spend_usd:.2f}"],
                metrics=metrics,
                already_constrained=hook_id in skip_hook_ids,
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
            already_constrained=hook_id in skip_hook_ids,
        ))
    return results


def _existing_constrained_hook_ids(ctx: ClientContext) -> set[str]:
    """Scan SQL negative_constraints for hook IDs already covered.

    V1.2: reads from client_data.db via ctx.get_negative_constraints()
    rather than the YAML field that no longer exists.
    """
    out: set[str] = set()
    for c in ctx.get_negative_constraints():
        match = _HOOK_ID_IN_RULE_RX.search(c.rule)
        if match:
            out.add(match.group(1))
    return out


def _format_evaluation_summary(
    evaluations: list[HookEvaluation],
    time_preset: str,
    roas_target: Optional[float],
    ctr_target: Optional[float],
    min_spend_usd: float,
) -> str:
    """Render the evaluation results as a compact text block for the LLM."""
    failed = [e for e in evaluations if e.failed]
    passing = [e for e in evaluations if not e.failed and not e.reasons]
    insufficient = [e for e in evaluations if not e.failed and e.reasons]

    targets_line = (
        f"targets: ROAS>={roas_target if roas_target is not None else 'n/a'}, "
        f"CTR>={ctr_target if ctr_target is not None else 'n/a'}, "
        f"min_spend=${min_spend_usd:.2f}"
    )

    lines = [
        f"Campaign analysis (window: {time_preset}, {targets_line})",
        "",
    ]

    if not evaluations:
        lines.append(
            "No hooks with parseable ad_name found in the window. Either no "
            "ads ran, or the media buyer did not follow the "
            "[Audience]_[WH-xxx]_[RM-yyy] convention."
        )
        return "\n".join(lines)

    if failed:
        lines.append("FAILED (need negative constraint unless already_constrained):")
        for e in failed:
            roas_str = (
                f"{e.metrics['weighted_roas']:.2f}"
                if e.metrics.get("weighted_roas") is not None
                else "n/a"
            )
            ctr_str = f"{e.metrics['weighted_ctr']:.4f}"
            spend_str = f"${e.metrics['total_spend']:.2f}"
            constrained = "YES (skip)" if e.already_constrained else "NO"
            lines.append(
                f"- {e.hook_id}: {spend_str} spend, ROAS {roas_str}, "
                f"CTR {ctr_str}, {e.metrics['ad_count']} ad(s) | "
                f"already_constrained={constrained}"
            )
            if not e.already_constrained and e.proposed_rule:
                lines.append(f'    rule:   "{e.proposed_rule}"')
                lines.append(f'    reason: "{e.proposed_reason}"')
        lines.append("")

    if passing:
        lines.append("PASSING (no action needed):")
        for e in passing:
            roas_str = (
                f"{e.metrics['weighted_roas']:.2f}"
                if e.metrics.get("weighted_roas") is not None
                else "n/a"
            )
            ctr_str = f"{e.metrics['weighted_ctr']:.4f}"
            spend_str = f"${e.metrics['total_spend']:.2f}"
            lines.append(
                f"- {e.hook_id}: {spend_str} spend, ROAS {roas_str}, CTR {ctr_str}"
            )
        lines.append("")

    if insufficient:
        lines.append(f"INSUFFICIENT SPEND (need >=${min_spend_usd:.2f} to evaluate):")
        for e in insufficient:
            lines.append(f"- {e.hook_id}: ${e.metrics['total_spend']:.2f} spend")
        lines.append("")

    actionable = [e for e in failed if not e.already_constrained]
    if actionable:
        lines.append(
            f"NEXT: call write_negative_constraint for each of the "
            f"{len(actionable)} FAILED hook(s) NOT already constrained, "
            f"using the rule + reason verbatim from above."
        )
    else:
        lines.append(
            "NEXT: no constraints to add this run. Call append_analyst_summary and stop."
        )

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tool factories
# --------------------------------------------------------------------------- #


def make_analyze_campaign_performance_tool(ctx: ClientContext):
    """LangChain tool: fetch Meta insights, aggregate, evaluate, return summary."""

    @tool("analyze_campaign_performance")
    def analyze_campaign_performance(
        time_preset: str = "last_14d",
        min_spend_usd: float = DEFAULT_MIN_SPEND_USD,
    ) -> str:
        """Fetch the client's Meta ad performance for `time_preset`, aggregate
        by hook_id, and score each hook against MASTER_CONTEXT.md's
        performance_benchmarks. Returns a compressed text summary listing
        FAILED / PASSING / INSUFFICIENT SPEND hooks with the metrics that
        justify each verdict.

        Failed hooks include a proposed_rule + proposed_reason that you can
        pass verbatim into write_negative_constraint.
        """
        # V1.3 hardening (Initiative 3): Meta API timeouts/HTTP errors raise
        # RuntimeError from fetch_meta_insights_data. Without this guard the
        # exception bubbles through the LangChain tool runtime and crashes
        # the whole graph step, losing the agent's in-flight state. Catching
        # here lets the LLM read the failure as a tool result and decide
        # whether to retry, switch time windows, or report back to the user.
        try:
            fm, _ = ctx.read()
            benchmarks = fm.performance_benchmarks

            if benchmarks.roas_target is None and benchmarks.ctr_target is None:
                return (
                    "ERROR: No performance benchmarks set in MASTER_CONTEXT.md. "
                    "Configure performance_benchmarks.roas_target and/or "
                    "performance_benchmarks.ctr_target before running the Analyst."
                )

            insights = fetch_meta_insights_data(time_preset=time_preset)
            if not insights:
                return (
                    f"No ads found in the Meta account for window={time_preset}. "
                    f"Either no ads ran, or META_AD_ACCOUNT_ID is misconfigured."
                )

            performance = aggregate_by_hook(insights)
            skip = _existing_constrained_hook_ids(ctx)
            evaluations = evaluate_hooks(
                performance,
                roas_target=benchmarks.roas_target,
                ctr_target=benchmarks.ctr_target,
                min_spend_usd=min_spend_usd,
                skip_hook_ids=skip,
            )

            return _format_evaluation_summary(
                evaluations,
                time_preset=time_preset,
                roas_target=benchmarks.roas_target,
                ctr_target=benchmarks.ctr_target,
                min_spend_usd=min_spend_usd,
            )
        except Exception as e:
            return f"API ERROR: {str(e)}"

    return analyze_campaign_performance


def make_constraint_writer(ctx: ClientContext):
    """LangChain tool: write a HARD negative constraint into client_data.db."""

    @tool("write_negative_constraint")
    def write_negative_constraint(
        hook_id: str,
        rule: str,
        reason: str,
    ) -> str:
        """Persist a HARD negative constraint into client_data.db. The
        Producer will enforce it on all future video generations via the
        Kling negative_prompt. Returns the auto-assigned constraint ID
        (e.g. NC-007), or a SKIPPED message if a constraint for this hook
        already exists.
        """
        existing = _existing_constrained_hook_ids(ctx)
        if hook_id in existing:
            return f"SKIPPED: {hook_id} already has a negative constraint."
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
