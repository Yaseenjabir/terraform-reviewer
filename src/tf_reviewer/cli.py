"""Command-line entry point: `tf-reviewer plan.json [--cost-diff infracost.json] [--checkov checkov.json] [--no-llm]`."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from decimal import Decimal, InvalidOperation

from tf_reviewer.checkov import CheckovError, load_checkov
from tf_reviewer.config import CONFIG_FILE_NAME, DEFAULT_CONFIG, ConfigError, find_config, load_config
from tf_reviewer.cost import CostDiffError, load_cost_diff
from tf_reviewer.llm import LLMError, explain, make_backend
from tf_reviewer.plan import PlanError, load_plan
from tf_reviewer.rules import evaluate
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
        "--config",
        metavar="FILE",
        help=f"settings file (default: {CONFIG_FILE_NAME} in the current directory, if it exists)",
    )
    parser.add_argument(
        "--cost-diff",
        metavar="FILE",
        help="Infracost diff JSON file (from `infracost diff --format json`), to check cost changes",
    )
    parser.add_argument(
        "--cost-threshold",
        type=_amount,
        metavar="AMOUNT",
        help=f"report a monthly cost increase of at least this much (overrides the config; default: "
        f"{DEFAULT_CONFIG.cost_threshold})",
    )
    parser.add_argument(
        "--checkov",
        metavar="FILE",
        help="Checkov JSON output for the same plan (`checkov -f plan.json --framework terraform_plan -o json`)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="don't ask an LLM for explanations, even if the config sets one up (rules-only review)",
    )
    args = parser.parse_args(argv)

    # Plans and Infracost output can contain any Unicode (e.g. "t3.medium → t3.xlarge").
    # Some consoles default to a legacy encoding that can't print it, so always write UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    try:
        config_file = args.config or find_config()
        config = load_config(config_file) if config_file else DEFAULT_CONFIG
        changes = load_plan(args.plan_file)
        cost = load_cost_diff(args.cost_diff) if args.cost_diff else None
        checkov = load_checkov(args.checkov) if args.checkov else None
    except (OSError, ConfigError, PlanError, CostDiffError, CheckovError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.cost_threshold is not None:
        config = dataclasses.replace(config, cost_threshold=args.cost_threshold)

    findings = evaluate(changes, config=config, cost=cost, checkov=checkov)

    # The LLM only adds explanations. If it fails, the review goes on without them: the
    # findings, and so the verdict, never depend on it.
    review = None
    if not args.no_llm:
        try:
            backend = make_backend(config)
            if backend is not None:
                review = explain(findings, backend)
        except LLMError as e:
            print(f"warning: no AI explanations: {e}", file=sys.stderr)

    print(format_summary(changes))
    if cost is not None:
        print(format_cost(cost))
    print()
    print(format_findings(findings, review))
    if config_file:
        print(f"\n(settings from {config_file})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
