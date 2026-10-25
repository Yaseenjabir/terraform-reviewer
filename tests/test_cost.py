from decimal import Decimal
from pathlib import Path

import pytest

from tf_reviewer.cli import main
from tf_reviewer.cost import CostDiff, CostDiffError, ResourceCost, format_money, load_cost_diff, parse_cost_diff
from tf_reviewer.plan import load_plan
from tf_reviewer.rules import Risk, cost_increase, evaluate
from tf_reviewer.summary import format_cost

FIXTURES = Path(__file__).parent / "fixtures"


def diff(before: str, after: str, *resources: ResourceCost, currency: str = "USD") -> CostDiff:
    return CostDiff(currency, Decimal(before), Decimal(after), resources)


# --- reading the Infracost diff ---------------------------------------------------------


def test_reads_real_infracost_diff():
    cost = load_cost_diff(FIXTURES / "infracost_diff.json")
    assert (cost.currency, cost.monthly_before, cost.monthly_after) == ("USD", Decimal("48.808"), Decimal("139.912"))
    assert cost.monthly_change == Decimal("91.104")
    assert round(cost.percent_change) == 187
    [web] = cost.resources
    assert (web.address, web.monthly_change) == ("aws_instance.web", Decimal("91.104"))
    assert "t3.medium → t3.xlarge" in web.details[0]


def test_null_amounts_count_as_zero():
    data = {
        "currency": "USD",
        "pastTotalMonthlyCost": None,
        "totalMonthlyCost": "10",
        "projects": [{"diff": {"resources": [{"name": "a.b", "monthlyCost": None}]}}],
    }
    cost = parse_cost_diff(data)
    assert (cost.monthly_before, cost.monthly_after, cost.resources) == (0, 10, ())
    assert cost.percent_change is None


def test_resources_sorted_biggest_increase_first():
    data = {
        "totalMonthlyCost": "30",
        "pastTotalMonthlyCost": "0",
        "projects": [
            {"diff": {"resources": [{"name": "a.small", "monthlyCost": "5"}, {"name": "a.big", "monthlyCost": "25"}]}}
        ],
    }
    assert [r.address for r in parse_cost_diff(data).resources] == ["a.big", "a.small"]


def test_rejects_non_infracost_json():
    with pytest.raises(CostDiffError, match="not an Infracost diff"):
        parse_cost_diff({"format_version": "1.2"})


def test_rejects_bad_amount():
    with pytest.raises(CostDiffError, match="not a cost amount"):
        parse_cost_diff({"projects": [], "totalMonthlyCost": "lots"})


def test_rejects_invalid_json(tmp_path):
    bad = tmp_path / "cost.json"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(CostDiffError, match="not valid JSON"):
        load_cost_diff(bad)


@pytest.mark.parametrize(
    ("amount", "currency", "text"),
    [
        ("91.104", "USD", "$91.10"),
        ("1234.5", "USD", "$1,234.50"),
        ("-12", "USD", "-$12.00"),
        ("7", "EUR", "7.00 EUR"),
    ],
)
def test_format_money(amount, currency, text):
    assert format_money(Decimal(amount), currency) == text


# --- the cost-increase rule -------------------------------------------------------------


def test_increase_over_threshold():
    web = ResourceCost("aws_instance.web", Decimal("91.104"), ("t3.medium to t3.xlarge",))
    [finding] = cost_increase(diff("48.808", "139.912", web))
    assert (finding.rule_id, finding.risk, finding.address) == ("cost-increase", Risk.MEDIUM, "aws_instance.web")
    assert finding.message == (
        "Monthly cost rises by $91.10 (+187%, $48.81 -> $139.91). "
        "Biggest increase: aws_instance.web +$91.10 (t3.medium to t3.xlarge)."
    )


def test_increase_under_threshold():
    assert cost_increase(diff("10", "40")) == []


def test_increase_exactly_at_threshold():
    assert len(cost_increase(diff("0", "50"))) == 1


def test_decrease_is_not_a_finding():
    assert cost_increase(diff("200", "100")) == []


def test_custom_threshold():
    assert cost_increase(diff("10", "40"), threshold=Decimal(20))
    assert cost_increase(diff("10", "40"), threshold=Decimal(100)) == []


def test_increase_from_zero_has_no_percentage():
    [finding] = cost_increase(diff("0", "60"))
    assert finding.message == "Monthly cost rises by $60.00 ($0.00 -> $60.00)."
    assert finding.address == "(whole plan)"


def test_evaluate_includes_cost_and_sorts():
    findings = evaluate(load_plan(FIXTURES / "mixed.json"), cost=load_cost_diff(FIXTURES / "infracost_diff.json"))
    assert [f.rule_id for f in findings] == ["cost-increase", "resource-removed", "resource-removed", "resource-removed"]


def test_evaluate_without_cost_has_no_cost_findings():
    assert all(f.rule_id != "cost-increase" for f in evaluate(load_plan(FIXTURES / "mixed.json")))


# --- output -----------------------------------------------------------------------------


def test_format_cost():
    assert format_cost(diff("48.808", "139.912")) == "Monthly cost: $48.81 -> $139.91 (+$91.10, +187%)"
    assert format_cost(diff("100", "75")) == "Monthly cost: $100.00 -> $75.00 (-$25.00, -25%)"
    assert format_cost(diff("20", "20")) == "Monthly cost: $20.00 (no change)"


def test_cli_with_cost_diff(capsys):
    assert main([str(FIXTURES / "mixed.json"), "--cost-diff", str(FIXTURES / "infracost_diff.json")]) == 0
    out = capsys.readouterr().out
    assert "Monthly cost: $48.81 -> $139.91 (+$91.10, +187%)" in out
    assert "[cost-increase]" in out


def test_cli_cost_threshold(capsys):
    args = [str(FIXTURES / "mixed.json"), "--cost-diff", str(FIXTURES / "infracost_diff.json"), "--cost-threshold", "100"]
    assert main(args) == 0
    assert "[cost-increase]" not in capsys.readouterr().out


def test_cli_rejects_plan_as_cost_diff(capsys):
    assert main([str(FIXTURES / "mixed.json"), "--cost-diff", str(FIXTURES / "mixed.json")]) == 2
    assert "not an Infracost diff" in capsys.readouterr().err
