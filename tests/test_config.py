from decimal import Decimal
from pathlib import Path

import pytest

from tf_reviewer.checkov import CheckovFailure, load_checkov
from tf_reviewer.cli import main
from tf_reviewer.config import (
    DEFAULT_CONFIG,
    DEFAULT_SENSITIVE_PORTS,
    DEFAULT_STATEFUL_TYPES,
    DEFAULT_WEB_PORTS,
    RULE_IDS,
    ConfigError,
    load_config,
    parse_config,
)
from tf_reviewer.cost import load_cost_diff
from tf_reviewer.plan import Action, ResourceChange, load_plan
from tf_reviewer.rules import Risk, checkov_findings, data_loss, evaluate, open_to_internet, resource_removed

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def change(address: str, action: Action, **kwargs) -> ResourceChange:
    actions = {Action.CREATE: ("create",), Action.DELETE: ("delete",)}[action]
    return ResourceChange(address=address, type=address.split(".")[-2], action=action, actions=actions, **kwargs)


def sg_opening(port: int) -> dict:
    return {"ingress": [{"from_port": port, "to_port": port, "protocol": "tcp", "cidr_blocks": ["0.0.0.0/0"]}]}


# --- reading the config -----------------------------------------------------------------


def test_defaults():
    assert DEFAULT_CONFIG.cost_threshold == Decimal(50)
    assert DEFAULT_CONFIG.stateful_types == DEFAULT_STATEFUL_TYPES
    assert DEFAULT_CONFIG.disabled_rules == frozenset()


@pytest.mark.parametrize("data", [None, {}])
def test_empty_config_means_defaults(data):
    assert parse_config(data) == DEFAULT_CONFIG


def test_example_config_file():
    config = load_config(ROOT / ".tf-reviewer.example.yml")
    assert config.cost_threshold == Decimal(100)
    assert "aws_memorydb_cluster" in config.stateful_types
    assert config.sensitive_ports[8080] == "Jenkins"
    assert config.web_ports == DEFAULT_WEB_PORTS


def test_add_and_remove():
    config = parse_config(
        {
            "rules": {"disable": ["resource-removed"]},
            "stateful_types": {"add": ["aws_memorydb_cluster"], "remove": ["aws_sqs_queue"]},
            "sensitive_ports": {"add": {8080: "Jenkins", "9000": "SonarQube"}, "remove": [9200]},
            "web_ports": {"add": [8443], "remove": [80]},
            "checkov": {"skip": ["CKV_AWS_16"]},
        }
    )
    assert config.disabled_rules == {"resource-removed"}
    assert config.stateful_types == (DEFAULT_STATEFUL_TYPES | {"aws_memorydb_cluster"}) - {"aws_sqs_queue"}
    assert config.sensitive_ports[8080] == "Jenkins" and config.sensitive_ports[9000] == "SonarQube"
    assert 9200 not in config.sensitive_ports and 22 in config.sensitive_ports
    assert config.web_ports == {443, 8443}
    assert config.skipped_checkov_checks == {"CKV_AWS_16"}
    assert DEFAULT_SENSITIVE_PORTS[9200] == "Elasticsearch"  # defaults are untouched


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (["a list"], "must be a mapping of settings"),
        ({"costs": {}}, "unknown setting 'costs'"),
        ({"cost": {"limit": 5}}, "unknown setting 'cost.limit'"),
        ({"cost": []}, "'cost' must be a mapping"),
        ({"cost": {"threshold": -5}}, "non-negative"),
        ({"cost": {"threshold": "lots"}}, "not an amount"),
        ({"cost": {"threshold": True}}, "non-negative amount"),
        ({"rules": {"disable": ["data-los"]}}, "unknown rule 'data-los'"),
        ({"rules": {"disable": "data-loss"}}, "must be a list of names"),
        ({"stateful_types": {"add": "aws_x"}}, "must be a list of names"),
        ({"sensitive_ports": {"add": [8080]}}, "must map port numbers to names"),
        ({"sensitive_ports": {"add": {8080: ""}}}, "port 8080 needs a name"),
        ({"sensitive_ports": {"add": {99999: "x"}}}, "99999 is not a port number"),
        ({"web_ports": {"add": ["http"]}}, "'http' is not a port number"),
        ({"web_ports": {"add": [True]}}, "is not a port number"),
        ({"version": 2}, "unsupported config version 2"),
    ],
)
def test_invalid_configs(data, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_invalid_yaml(tmp_path):
    bad = tmp_path / ".tf-reviewer.yml"
    bad.write_text("cost: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)


def test_errors_name_the_file(tmp_path):
    bad = tmp_path / ".tf-reviewer.yml"
    bad.write_text("rules:\n  disable: [nope]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"\.tf-reviewer\.yml: 'rules.disable'"):
        load_config(bad)


def test_rule_ids_match_the_rules():
    plans = [load_plan(p) for p in FIXTURES.glob("*.json") if p.name not in ("infracost_diff.json", "checkov.json")]
    plans.append([change("aws_s3_bucket.logs", Action.DELETE)])  # no fixture deletes a stateful resource
    cost, checkov = load_cost_diff(FIXTURES / "infracost_diff.json"), load_checkov(FIXTURES / "checkov.json")
    seen = {f.rule_id for changes in plans for f in evaluate(changes, cost=cost, checkov=checkov)}
    assert seen == RULE_IDS


# --- rules follow the config ------------------------------------------------------------


def test_added_stateful_type_counts_as_data_loss():
    config = parse_config({"stateful_types": {"add": ["aws_memorydb_cluster"]}})
    deleted = change("aws_memorydb_cluster.cache", Action.DELETE)
    assert data_loss(deleted) == []
    assert data_loss(deleted, config)[0].risk is Risk.HIGH
    assert resource_removed(deleted, config) == []


def test_removed_stateful_type_is_only_a_removal():
    config = parse_config({"stateful_types": {"remove": ["aws_sqs_queue"]}})
    deleted = change("aws_sqs_queue.jobs", Action.DELETE)
    assert data_loss(deleted, config) == []
    assert resource_removed(deleted, config)[0].risk is Risk.MEDIUM


def test_added_sensitive_port_is_high():
    config = parse_config({"sensitive_ports": {"add": {8080: "Jenkins"}}})
    opened = change("aws_security_group.ci", Action.CREATE, after=sg_opening(8080))
    assert open_to_internet(opened)[0].risk is Risk.MEDIUM
    [finding] = open_to_internet(opened, config)
    assert (finding.risk, finding.message) == (
        Risk.HIGH,
        "aws_security_group.ci opens Jenkins (port 8080) to the whole internet (0.0.0.0/0).",
    )


def test_added_web_port_is_allowed():
    config = parse_config({"web_ports": {"add": [8443]}})
    assert open_to_internet(change("aws_security_group.web", Action.CREATE, after=sg_opening(8443)), config) == []


def test_disabled_rule_is_left_out():
    config = parse_config({"rules": {"disable": ["resource-removed"]}})
    findings = evaluate([change("aws_instance.web", Action.DELETE)], config=config)
    assert findings == []


def test_skipped_checkov_check():
    config = parse_config({"checkov": {"skip": ["CKV_AWS_17"]}})
    findings = checkov_findings(
        [CheckovFailure("CKV_AWS_17", "", "aws_db_instance.a")], [change("aws_db_instance.a", Action.CREATE)], config
    )
    assert findings == []


def test_cost_threshold_from_config():
    cost = load_cost_diff(FIXTURES / "infracost_diff.json")
    assert evaluate([], cost=cost)  # +$91 is over the default $50
    assert evaluate([], cost=cost, config=parse_config({"cost": {"threshold": 100}})) == []


# --- the command line ---------------------------------------------------------------------


def test_cli_finds_config_in_current_directory(tmp_path, monkeypatch, capsys):
    (tmp_path / ".tf-reviewer.yml").write_text("rules:\n  disable: [resource-removed]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main([str(FIXTURES / "mixed.json")]) == 0
    out = capsys.readouterr().out
    assert "No risky changes found." in out
    assert "(settings from .tf-reviewer.yml)" in out


def test_cli_without_config_uses_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([str(FIXTURES / "mixed.json")]) == 0
    out = capsys.readouterr().out
    assert "[resource-removed]" in out and "settings from" not in out


def test_cli_explicit_config_and_threshold_override(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "team.yml"
    config.write_text("cost:\n  threshold: 500\n", encoding="utf-8")
    args = [str(FIXTURES / "mixed.json"), "--cost-diff", str(FIXTURES / "infracost_diff.json"), "--config", str(config)]
    assert main(args) == 0
    assert "[cost-increase]" not in capsys.readouterr().out
    assert main([*args, "--cost-threshold", "10"]) == 0
    assert "[cost-increase]" in capsys.readouterr().out


def test_cli_reports_invalid_config(tmp_path, monkeypatch, capsys):
    (tmp_path / ".tf-reviewer.yml").write_text("rules:\n  disable: [nope]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main([str(FIXTURES / "mixed.json")]) == 2
    assert "unknown rule 'nope'" in capsys.readouterr().err
