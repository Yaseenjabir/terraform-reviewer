"""Format a list of resource changes as a short, plain-text summary."""

from __future__ import annotations

from collections import Counter

from tf_reviewer.plan import Action, ResourceChange

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


def format_path(path: tuple[str | int, ...]) -> str:
    """Turn a JSON path like ("ingress", 0, "cidr_blocks") into "ingress[0].cidr_blocks"."""
    text = ""
    for part in path:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else part)
    return text


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
