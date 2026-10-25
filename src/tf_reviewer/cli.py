"""Command-line entry point: `tf-reviewer plan.json [--cost-diff infracost.json]`."""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation

from tf_reviewer.checkov import CheckovError, load_checkov
from tf_reviewer.cost import CostDiffError, load_cost_diff
from tf_reviewer.plan import PlanError, load_plan
from tf_reviewer.rules import DEFAULT_COST_THRESHOLD, evaluate
from tf_reviewer.summary import format_cost, format_findings, format_summary


def _amount(text: str) -> Decimal:
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not an amount: {text!r}") from None
    if amount < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return amount


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tf-reviewer",
        description="Review a Terraform plan JSON file (from `terraform show -json <planfile>`).",
    )
    parser.add_argument("plan_file", help="path to the plan JSON file")
    parser.add_argument(
        "--cost-diff",
        metavar="FILE",
        help="Infracost diff JSON file (from `infracost diff --format json`), to check cost changes",
    )
    parser.add_argument(
        "--cost-threshold",
        type=_amount,
        default=DEFAULT_COST_THRESHOLD,
        metavar="AMOUNT",
        help=f"report a monthly cost increase of at least this much (default: {DEFAULT_COST_THRESHOLD})",
    )
    parser.add_argument(
        "--checkov",
        metavar="FILE",
        help="Checkov JSON output for the same plan (`checkov -f plan.json --framework terraform_plan -o json`)",
    )
    args = parser.parse_args(argv)

    # Plans and Infracost output can contain any Unicode (e.g. "t3.medium → t3.xlarge").
    # Some consoles default to a legacy encoding that can't print it, so always write UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    try:
        changes = load_plan(args.plan_file)
        cost = load_cost_diff(args.cost_diff) if args.cost_diff else None
        checkov = load_checkov(args.checkov) if args.checkov else None
    except (OSError, PlanError, CostDiffError, CheckovError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(format_summary(changes))
    if cost is not None:
        print(format_cost(cost))
    print()
    print(format_findings(evaluate(changes, cost=cost, cost_threshold=args.cost_threshold, checkov=checkov)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
