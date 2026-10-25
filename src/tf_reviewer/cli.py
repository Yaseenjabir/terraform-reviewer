"""Command-line entry point: `tf-reviewer plan.json`."""

from __future__ import annotations

import argparse
import sys

from tf_reviewer.plan import PlanError, load_plan
from tf_reviewer.rules import evaluate
from tf_reviewer.summary import format_findings, format_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tf-reviewer",
        description="Summarize a Terraform plan JSON file (from `terraform show -json <planfile>`).",
    )
    parser.add_argument("plan_file", help="path to the plan JSON file")
    args = parser.parse_args(argv)

    try:
        changes = load_plan(args.plan_file)
    except (OSError, PlanError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(format_summary(changes))
    print()
    print(format_findings(evaluate(changes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
