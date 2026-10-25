"""Read an Infracost diff (the output of `infracost diff --format json`).

Infracost writes money as strings (e.g. "91.104") to avoid rounding errors, so amounts
are read as Decimal. The file's metadata can include the commit author's name and email
in CI, so only the cost figures are kept.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class CostDiffError(ValueError):
    """The input isn't an Infracost diff this code can read."""


@dataclass(frozen=True)
class ResourceCost:
    """How one resource's monthly cost changes."""

    address: str
    monthly_change: Decimal
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class CostDiff:
    """The monthly cost before and after the change, across all projects."""

    currency: str
    monthly_before: Decimal
    monthly_after: Decimal
    resources: tuple[ResourceCost, ...] = ()

    @property
    def monthly_change(self) -> Decimal:
        return self.monthly_after - self.monthly_before

    @property
    def percent_change(self) -> Decimal | None:
        """Change as a percentage of the old cost, or None if the old cost was zero."""
        if self.monthly_before == 0:
            return None
        return self.monthly_change / self.monthly_before * 100


def _amount(value: Any) -> Decimal:
    """An Infracost amount ("91.104", or null when it can't be estimated) as a Decimal."""
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except InvalidOperation as e:
        raise CostDiffError(f"not a cost amount: {value!r}") from e


def parse_cost_diff(data: dict[str, Any]) -> CostDiff:
    """Turn a decoded Infracost diff into a CostDiff."""
    if not isinstance(data, dict) or "projects" not in data or "totalMonthlyCost" not in data:
        raise CostDiffError("not an Infracost diff: expected the output of `infracost diff --format json`")

    resources = []
    for project in data["projects"]:
        for resource in (project.get("diff") or {}).get("resources") or []:
            change = _amount(resource.get("monthlyCost"))
            if change == 0:
                continue
            details = tuple(
                component["name"]
                for component in resource.get("costComponents") or []
                if _amount(component.get("monthlyCost")) != 0
            )
            resources.append(ResourceCost(resource["name"], change, details))

    return CostDiff(
        currency=data.get("currency") or "USD",
        monthly_before=_amount(data.get("pastTotalMonthlyCost")),
        monthly_after=_amount(data.get("totalMonthlyCost")),
        resources=tuple(sorted(resources, key=lambda r: (-r.monthly_change, r.address))),
    )


def load_cost_diff(path: str | Path) -> CostDiff:
    """Read an Infracost diff JSON file and parse it."""
    with open(path, encoding="utf-8-sig") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise CostDiffError(f"{path} is not valid JSON: {e}") from e
    return parse_cost_diff(data)


def format_money(amount: Decimal, currency: str) -> str:
    """$91.10 for USD, otherwise "91.10 EUR"."""
    text = f"{abs(amount):,.2f}"
    text = f"${text}" if currency == "USD" else f"{text} {currency}"
    return f"-{text}" if amount < 0 else text
