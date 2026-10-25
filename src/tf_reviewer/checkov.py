"""Read Checkov results (the output of `checkov -f plan.json --framework terraform_plan -o json`).

Only the fields the reviewer needs are kept. Checkov's output also contains absolute file
paths and code blocks, which must never end up in a PR comment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CheckovError(ValueError):
    """The input isn't Checkov JSON output this code can read."""


@dataclass(frozen=True)
class CheckovFailure:
    """One failed Checkov check on one resource."""

    check_id: str
    check_name: str
    address: str
    guideline: str | None = None


def parse_checkov(data: dict[str, Any] | list[dict[str, Any]]) -> list[CheckovFailure]:
    """Every failed check in Checkov's JSON output.

    Checkov prints one report (a dict) for a single framework, or a list of reports for several.
    """
    reports = data if isinstance(data, list) else [data]
    failures = []
    for report in reports:
        if not isinstance(report, dict) or "summary" not in report:
            raise CheckovError("not Checkov output: expected the output of `checkov ... -o json`")
        for check in (report.get("results") or {}).get("failed_checks") or []:
            failures.append(
                CheckovFailure(
                    check_id=check["check_id"],
                    check_name=check.get("check_name") or "",
                    address=check.get("resource_address") or check["resource"],
                    guideline=check.get("guideline"),
                )
            )
    return failures


def load_checkov(path: str | Path) -> list[CheckovFailure]:
    """Read a Checkov JSON file and parse it."""
    with open(path, encoding="utf-8-sig") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise CheckovError(f"{path} is not valid JSON: {e}") from e
    return parse_checkov(data)
