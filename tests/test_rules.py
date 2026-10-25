from pathlib import Path

import pytest

from tf_reviewer.plan import Action, ResourceChange, load_plan
from tf_reviewer.rules import Finding, Risk, data_loss, evaluate, open_to_internet, overall_risk, resource_removed
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


# --- open-to-internet -------------------------------------------------------------------


def sg(*ingress: dict) -> dict:
    """Values of an aws_security_group with the given inline ingress rules."""
    return {"name": "web", "ingress": list(ingress)}


def tcp(port_from: int, port_to: int | None = None, cidr: str = "0.0.0.0/0") -> dict:
    return {
        "from_port": port_from,
        "to_port": port_from if port_to is None else port_to,
        "protocol": "tcp",
        "cidr_blocks": [cidr],
        "ipv6_cidr_blocks": [],
    }


def opened(before: dict | None, after: dict | None, address: str = "aws_security_group.web") -> list[tuple[str, str]]:
    action = Action.CREATE if before is None else Action.UPDATE
    findings = open_to_internet(change(address, action, before=before, after=after))
    return [(f.risk.name, f.message) for f in findings]


def test_open_ports_fixture_covers_all_three_styles():
    findings = evaluate(load_plan(FIXTURES / "open_ports.json"))
    assert [(f.address, f.risk) for f in findings] == [
        ("aws_security_group.web", Risk.HIGH),
        ("aws_security_group_rule.rdp", Risk.HIGH),
        ("aws_vpc_security_group_ingress_rule.everything_ipv6", Risk.HIGH),
        ("aws_vpc_security_group_ingress_rule.postgres", Risk.HIGH),
    ]
    assert findings[2].message.endswith("opens all ports to the whole internet (::/0).")


def test_ssh_added_to_existing_group():
    assert opened(sg(tcp(443)), sg(tcp(443), tcp(22))) == [
        ("HIGH", "aws_security_group.web opens SSH (port 22) to the whole internet (0.0.0.0/0)."),
    ]


def test_already_open_port_is_not_this_changes_fault():
    assert opened(sg(tcp(22)), sg(tcp(22), tcp(443))) == []


def test_closing_a_port_is_not_a_finding():
    assert opened(sg(tcp(22), tcp(443)), sg(tcp(443))) == []


def test_web_ports_are_fine():
    assert opened(None, sg(tcp(80), tcp(443))) == []


def test_private_range_is_fine():
    assert opened(None, sg(tcp(22, cidr="10.0.0.0/8"))) == []


def test_range_containing_a_sensitive_port():
    assert opened(None, sg(tcp(20, 25))) == [
        ("HIGH", "aws_security_group.web opens SSH (port 22) to the whole internet (0.0.0.0/0)."),
    ]


def test_full_tcp_range_is_all_ports():
    assert opened(None, sg(tcp(0, 65535))) == [
        ("HIGH", "aws_security_group.web opens all ports to the whole internet (0.0.0.0/0)."),
    ]


def test_other_ports_are_medium():
    assert opened(None, sg(tcp(8080), tcp(8000, 8100))) == [
        ("MEDIUM", "aws_security_group.web opens ports 8000-8100 to the whole internet (0.0.0.0/0)."),
        ("MEDIUM", "aws_security_group.web opens port 8080 to the whole internet (0.0.0.0/0)."),
    ]


def test_icmp_is_ignored():
    ping = {"from_port": 8, "to_port": 0, "protocol": "icmp", "cidr_blocks": ["0.0.0.0/0"]}
    assert opened(None, sg(ping)) == []


def test_egress_rules_are_ignored():
    rule = {"type": "egress", "from_port": 0, "to_port": 0, "protocol": "-1", "cidr_blocks": ["0.0.0.0/0"]}
    assert opened(None, rule, address="aws_security_group_rule.out") == []


def test_missing_ipv6_list_is_handled():
    rule = {"type": "ingress", "from_port": 22, "to_port": 22, "protocol": "tcp",
            "cidr_blocks": None, "ipv6_cidr_blocks": ["::/0"]}
    assert opened(None, rule, address="aws_security_group_rule.ssh") == [
        ("HIGH", "aws_security_group_rule.ssh opens SSH (port 22) to the whole internet (::/0)."),
    ]


def test_deleting_a_group_opens_nothing():
    assert open_to_internet(change("aws_security_group.web", Action.DELETE, before=sg(tcp(22)), after=None)) == []
