from pathlib import Path

import pytest

from tf_reviewer.plan import Action, ResourceChange, load_plan
from tf_reviewer.rules import Finding, Risk, data_loss, evaluate, overall_risk, resource_removed
from tf_reviewer.summary import format_findings

FIXTURES = Path(__file__).parent / "fixtures"

_ACTIONS = {
    Action.CREATE: ("create",),
    Action.UPDATE: ("update",),
    Action.DELETE: ("delete",),
    Action.REPLACE: ("delete", "create"),
    Action.NO_OP: ("no-op",),
}


def change(address: str, action: Action, **kwargs) -> ResourceChange:
    """A ResourceChange for tests; the type is taken from the address."""
    return ResourceChange(
        address=address,
        type=address.split(".")[-2],
        action=action,
        actions=_ACTIONS[action],
        **kwargs,
    )


@pytest.mark.parametrize("action", [Action.DELETE, Action.REPLACE])
def test_data_loss_for_stateful_resources(action):
    findings = data_loss(change("aws_db_instance.main", action))
    assert [(f.rule_id, f.risk) for f in findings] == [("data-loss", Risk.HIGH)]


@pytest.mark.parametrize("action", [Action.CREATE, Action.UPDATE, Action.NO_OP])
def test_no_data_loss_when_nothing_is_removed(action):
    assert data_loss(change("aws_db_instance.main", action)) == []


def test_data_loss_ignores_stateless_resources():
    assert data_loss(change("aws_instance.web", Action.DELETE)) == []


def test_data_loss_message_says_what_forced_the_replacement():
    [finding] = data_loss(change("aws_s3_bucket.logs", Action.REPLACE, replace_paths=(("bucket",),)))
    assert finding.message == (
        "aws_s3_bucket.logs will be replaced (forced by: bucket). "
        "It stores data, which is lost unless it's backed up."
    )


def test_data_loss_inside_a_module():
    [finding] = data_loss(change("module.storage.aws_s3_bucket.this", Action.DELETE))
    assert finding.address == "module.storage.aws_s3_bucket.this"


def test_resource_removed_for_stateless_resources():
    [finding] = resource_removed(change("aws_instance.web", Action.DELETE))
    assert (finding.rule_id, finding.risk, finding.message) == (
        "resource-removed",
        Risk.MEDIUM,
        "aws_instance.web will be deleted.",
    )


def test_each_removal_reported_by_exactly_one_rule():
    findings = evaluate([change("aws_s3_bucket.logs", Action.DELETE), change("aws_instance.web", Action.DELETE)])
    assert [(f.address, f.rule_id) for f in findings] == [
        ("aws_s3_bucket.logs", "data-loss"),
        ("aws_instance.web", "resource-removed"),
    ]


def test_findings_sorted_riskiest_first():
    findings = evaluate(
        [
            change("aws_instance.a", Action.DELETE),
            change("aws_s3_bucket.z", Action.DELETE),
        ]
    )
    assert [f.risk for f in findings] == [Risk.HIGH, Risk.MEDIUM]


def test_moved_resource_is_not_a_finding():
    assert evaluate([change("aws_s3_bucket.new", Action.NO_OP, previous_address="aws_s3_bucket.old")]) == []


def test_overall_risk():
    assert overall_risk([]) is None
    assert overall_risk([Finding("a", Risk.LOW, "x", ""), Finding("b", Risk.HIGH, "y", "")]) is Risk.HIGH


def test_findings_for_mixed_fixture():
    findings = evaluate(load_plan(FIXTURES / "mixed.json"))
    assert format_findings(findings) == "\n".join(
        [
            "Findings (overall risk: MEDIUM):",
            "  MEDIUM  [resource-removed] terraform_data.remove will be deleted.",
            "  MEDIUM  [resource-removed] terraform_data.replace will be replaced (forced by: triggers_replace).",
            "  MEDIUM  [resource-removed] terraform_data.replace_first will be replaced (forced by: triggers_replace).",
        ]
    )


def test_no_findings_message():
    assert format_findings([]) == "No risky changes found."
