"""Read a Terraform plan (the output of `terraform show -json`) into a list of changes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class Action(str, Enum):
    """What will happen to one resource, as a single word."""

    CREATE = "create"
    UPDATE = "update"
    REPLACE = "replace"
    DELETE = "delete"
    READ = "read"
    FORGET = "forget"
    NO_OP = "no-op"


# Terraform describes each change as a list of actions. Map each list to one Action.
_ACTIONS: dict[tuple[str, ...], Action] = {
    ("create",): Action.CREATE,
    ("update",): Action.UPDATE,
    ("delete",): Action.DELETE,
    ("read",): Action.READ,
    ("forget",): Action.FORGET,
    ("no-op",): Action.NO_OP,
    ("delete", "create"): Action.REPLACE,
    ("create", "delete"): Action.REPLACE,
}


class PlanError(ValueError):
    """The input isn't a Terraform plan this code can read."""


@dataclass(frozen=True)
class ResourceChange:
    """One entry of the plan's `resource_changes`, reduced to what the reviewer needs."""

    address: str
    type: str
    action: Action
    actions: tuple[str, ...]
    module_address: str | None = None
    previous_address: str | None = None
    action_reason: str | None = None
    replace_paths: tuple[tuple[str | int, ...], ...] = ()

    @property
    def is_change(self) -> bool:
        """True if applying the plan would modify this resource."""
        return self.action not in (Action.NO_OP, Action.READ)

    @property
    def was_moved(self) -> bool:
        """True if a `moved` block renamed this resource."""
        return self.previous_address is not None

    @property
    def creates_before_destroying(self) -> bool:
        """True for a replacement that creates the new object first (`create_before_destroy`)."""
        return self.actions == ("create", "delete")


def format_path(path: tuple[str | int, ...]) -> str:
    """Turn a JSON path like ("ingress", 0, "cidr_blocks") into "ingress[0].cidr_blocks"."""
    text = ""
    for part in path:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else part)
    return text


def parse_plan(plan: dict[str, Any]) -> list[ResourceChange]:
    """Turn a decoded plan JSON document into a list of ResourceChange, in plan order."""
    if not isinstance(plan, dict) or "format_version" not in plan:
        raise PlanError("not a Terraform plan: expected the output of `terraform show -json <planfile>`")
    if "planned_values" not in plan:
        raise PlanError("this looks like Terraform state, not a plan: run `terraform show -json` on a saved plan file")

    changes = []
    for entry in plan.get("resource_changes", []):
        actions = tuple(entry["change"]["actions"])
        action = _ACTIONS.get(actions)
        if action is None:
            raise PlanError(f"{entry['address']}: unsupported actions {list(actions)}")
        changes.append(
            ResourceChange(
                address=entry["address"],
                type=entry["type"],
                action=action,
                actions=actions,
                module_address=entry.get("module_address"),
                previous_address=entry.get("previous_address"),
                action_reason=entry.get("action_reason"),
                replace_paths=tuple(tuple(path) for path in entry["change"].get("replace_paths") or ()),
            )
        )
    return changes


def load_plan(path: str | Path) -> list[ResourceChange]:
    """Read a plan JSON file and parse it."""
    # utf-8-sig also accepts a leading byte order mark, which PowerShell adds when redirecting output.
    with open(path, encoding="utf-8-sig") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise PlanError(f"{path} is not valid JSON: {e}") from e
    return parse_plan(data)
