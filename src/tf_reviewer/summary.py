"""Format a list of resource changes as a short, plain-text summary."""

from __future__ import annotations

from collections import Counter

from tf_reviewer.cost import CostDiff, format_money
from tf_reviewer.plan import Action, ResourceChange, format_path
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


def _symbol(change: ResourceChange) -> str:
    if change.action is Action.REPLACE:
        return "+/-" if change.creates_before_destroying else "-/+"
    if change.action is Action.NO_OP and change.was_moved:
        return "->"
    return _SYMBOLS[change.action]


def _line(change: ResourceChange) -> str:
    notes = []
    if change.replace_paths:
        notes.append("forced by: " + ", ".join(format_path(p) for p in change.replace_paths))
    if change.was_moved:
        notes.append(f"moved from {change.previous_address}")
    suffix = f"  ({'; '.join(notes)})" if notes else ""
    return f"  {_symbol(change):<3} {change.address}{suffix}"


def format_summary(changes: list[ResourceChange]) -> str:
    """A headline with counts, then one line per changed or moved resource."""
    shown = [c for c in changes if c.is_change or c.was_moved]
    if not shown:
        return "No changes."

    counts = Counter(c.action for c in shown)
    parts = [f"{counts[action]} {label}" for action, label in _HEADLINE if counts[action]]
    moved = sum(c.was_moved for c in shown)
    if moved:
        parts.append(f"{moved} moved")

    return "\n".join([f"Plan: {', '.join(parts)}"] + [_line(c) for c in shown])


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


def format_findings(findings: list[Finding]) -> str:
    """One line per finding, riskiest first, under a headline with the overall risk."""
    risk = overall_risk(findings)
    if risk is None:
        return "No risky changes found."
    lines = [f"Findings (overall risk: {risk.name}):"]
    lines += [f"  {f.risk.name:<6}  [{f.rule_id}] {f.message}" for f in findings]
    return "\n".join(lines)
