"""Format a list of resource changes as a short, plain-text summary."""

from __future__ import annotations

from collections import Counter

from tf_reviewer.cost import CostDiff, format_money
from tf_reviewer.plan import Action, ResourceChange, format_path
from tf_reviewer.redact import redact
from tf_reviewer.llm import Review
from tf_reviewer.rules import Finding, overall_risk

# The same symbols Terraform uses in its human-readable plan.
_SYMBOLS = {
    Action.CREATE: "+",
    Action.UPDATE: "~",
    Action.DELETE: "-",
    Action.FORGET: "-",
}

# Order of the counts in the headline.
_HEADLINE = [
    (Action.CREATE, "to create"),
    (Action.UPDATE, "to update"),
    (Action.REPLACE, "to replace"),
    (Action.DELETE, "to delete"),
    (Action.FORGET, "to forget"),
]


def symbol(change: ResourceChange) -> str:
    """Terraform's symbol for the change: +, ~, -, -/+, +/- or -> (moved only)."""
    if change.action is Action.REPLACE:
        return "+/-" if change.creates_before_destroying else "-/+"
    if change.action is Action.NO_OP and change.was_moved:
        return "->"
    return _SYMBOLS[change.action]


def notes(change: ResourceChange) -> str:
    """'forced by: bucket; moved from aws_s3_bucket.old', or '' if there's nothing to add."""
    parts = []
    if change.replace_paths:
        parts.append("forced by: " + ", ".join(format_path(p) for p in change.replace_paths))
    if change.was_moved:
        parts.append(f"moved from {change.previous_address}")
    return redact("; ".join(parts))


def shown_changes(changes: list[ResourceChange]) -> list[ResourceChange]:
    """The resources worth listing: everything that changes or moves."""
    return [c for c in changes if c.is_change or c.was_moved]


def plan_headline(changes: list[ResourceChange]) -> str:
    """'1 to create, 2 to replace, 1 moved', or 'no changes'."""
    shown = shown_changes(changes)
    counts = Counter(c.action for c in shown)
    parts = [f"{counts[action]} {label}" for action, label in _HEADLINE if counts[action]]
    moved = sum(c.was_moved for c in shown)
    if moved:
        parts.append(f"{moved} moved")
    return ", ".join(parts) or "no changes"


def _line(change: ResourceChange) -> str:
    # Addresses can hold secrets (for_each keys), and this output ends up in CI logs.
    extra = notes(change)
    suffix = f"  ({extra})" if extra else ""
    return f"  {symbol(change):<3} {redact(change.address)}{suffix}"


def format_summary(changes: list[ResourceChange]) -> str:
    """A headline with counts, then one line per changed or moved resource."""
    shown = shown_changes(changes)
    if not shown:
        return "No changes."
    return "\n".join([f"Plan: {plan_headline(changes)}"] + [_line(c) for c in shown])


def format_cost(cost: CostDiff) -> str:
    """One line: monthly cost before and after, with the change."""
    before, after = format_money(cost.monthly_before, cost.currency), format_money(cost.monthly_after, cost.currency)
    if cost.monthly_change == 0:
        return f"Monthly cost: {after} (no change)"
    sign = "+" if cost.monthly_change > 0 else ""
    change = f"{sign}{format_money(cost.monthly_change, cost.currency)}"
    percent = cost.percent_change
    if percent is not None:
        change += f", {sign}{percent:.0f}%"
    return f"Monthly cost: {before} -> {after} ({change})"


def format_findings(findings: list[Finding], review: Review | None = None) -> str:
    """One line per finding, riskiest first, under a headline with the overall risk.

    With a review, the LLM's summary comes first and each finding gets its "why" and "fix"
    lines, all labelled as AI-written so readers know the verdict itself came from the rules.
    """
    risk = overall_risk(findings)
    if risk is None:
        return "No risky changes found."
    lines = []
    if review is not None:
        lines += [f"AI summary: {review.summary}", ""]
    lines.append(f"Findings (overall risk: {risk.name}):")
    for i, f in enumerate(findings):
        lines.append(f"  {f.risk.name:<6}  [{f.rule_id}] {f.message}")
        explanation = review.explanations[i] if review is not None else None
        if explanation is not None:
            lines.append(f"{'':10}Why: {explanation.why}")
            lines.append(f"{'':10}Fix: {explanation.fix}")
    if review is not None:
        lines += ["", f"(explanations written by {review.model}; check them before acting)"]
    return "\n".join(lines)
