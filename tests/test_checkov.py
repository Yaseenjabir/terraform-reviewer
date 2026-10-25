import json
from pathlib import Path

import pytest

from tf_reviewer.checkov import CheckovError, CheckovFailure, load_checkov, parse_checkov
from tf_reviewer.cli import main
from tf_reviewer.plan import Action, ResourceChange, load_plan
from tf_reviewer.rules import Risk, checkov_findings, evaluate

FIXTURES = Path(__file__).parent / "fixtures"


def created(address: str) -> ResourceChange:
    return ResourceChange(address=address, type=address.split(".")[-2], action=Action.CREATE, actions=("create",))


def unchanged(address: str) -> ResourceChange:
    return ResourceChange(address=address, type=address.split(".")[-2], action=Action.NO_OP, actions=("no-op",))


def failure(check_id: str, address: str) -> CheckovFailure:
    return CheckovFailure(check_id=check_id, check_name="", address=address)


# --- reading Checkov output -------------------------------------------------------------


def test_reads_real_checkov_output():
    failures = load_checkov(FIXTURES / "checkov.json")
    assert len(failures) == 23
    assert {f.address for f in failures} == {
        "aws_db_instance.main",
        "aws_s3_bucket.uploads",
        "aws_s3_bucket_public_access_block.uploads",
        "aws_security_group.web",
    }


def test_reads_list_of_reports():
    report = {"check_type": "terraform_plan", "summary": {}, "results": {"failed_checks": [
        {"check_id": "CKV_AWS_17", "check_name": "x", "resource": "aws_db_instance.a"},
    ]}}
    assert [f.address for f in parse_checkov([report, report])] == ["aws_db_instance.a", "aws_db_instance.a"]


def test_report_without_results():
    assert parse_checkov({"summary": {"passed": 0, "failed": 0}}) == []


def test_prefers_full_resource_address():
    report = {"summary": {}, "results": {"failed_checks": [
        {"check_id": "CKV_AWS_17", "check_name": "x", "resource": "aws_db_instance.a",
         "resource_address": "module.db.aws_db_instance.a"},
    ]}}
    assert parse_checkov(report)[0].address == "module.db.aws_db_instance.a"


def test_rejects_non_checkov_json():
    with pytest.raises(CheckovError, match="not Checkov output"):
        parse_checkov({"format_version": "1.2"})


def test_rejects_invalid_json(tmp_path):
    bad = tmp_path / "checkov.json"
    bad.write_text("[", encoding="utf-8")
    with pytest.raises(CheckovError, match="not valid JSON"):
        load_checkov(bad)


def test_fixture_contains_no_file_paths():
    text = (FIXTURES / "checkov.json").read_text(encoding="utf-8")
    assert "file_abs_path" not in text and "file_path" not in text


# --- turning failures into findings ----------------------------------------------------


def test_real_plan_and_checkov_output():
    findings = checkov_findings(load_checkov(FIXTURES / "checkov.json"), load_plan(FIXTURES / "checkov_plan.json"))
    assert [(f.address, f.risk) for f in findings] == [
        ("aws_db_instance.main", Risk.HIGH),
        ("aws_s3_bucket_public_access_block.uploads", Risk.HIGH),
        ("(several resources)", Risk.LOW),
    ]
    assert findings[0].message == (
        "aws_db_instance.main: database storage is not encrypted (CKV_AWS_16); "
        "database is publicly accessible (CKV_AWS_17)."
    )
    assert findings[2].message.startswith("16 other Checkov best-practice check(s) failed on 3 changed resource(s): ")
    assert findings[2].message.endswith(" and 8 more.")


def test_ssh_check_is_left_to_our_own_rule():
    findings = checkov_findings([failure("CKV_AWS_24", "aws_security_group.web")], [created("aws_security_group.web")])
    assert findings == []


def test_unchanged_resources_are_not_this_changes_fault():
    findings = checkov_findings([failure("CKV_AWS_17", "aws_db_instance.old")], [unchanged("aws_db_instance.old")])
    assert findings == []


def test_resources_not_in_the_plan_are_ignored():
    assert checkov_findings([failure("CKV_AWS_17", "aws_db_instance.other")], [created("aws_db_instance.main")]) == []


def test_medium_only_checks():
    [finding] = checkov_findings([failure("CKV_AWS_16", "aws_db_instance.a")], [created("aws_db_instance.a")])
    assert finding.risk is Risk.MEDIUM


def test_same_check_reported_twice_is_listed_once():
    failures = [failure("CKV_AWS_17", "aws_db_instance.a"), failure("CKV_AWS_17", "aws_db_instance.a")]
    [finding] = checkov_findings(failures, [created("aws_db_instance.a")])
    assert finding.message == "aws_db_instance.a: database is publicly accessible (CKV_AWS_17)."


def test_other_checks_on_one_resource():
    failures = [failure("CKV_AWS_118", "aws_db_instance.a"), failure("CKV_AWS_129", "aws_db_instance.a")]
    [finding] = checkov_findings(failures, [created("aws_db_instance.a")])
    assert (finding.risk, finding.address) == (Risk.LOW, "aws_db_instance.a")
    assert finding.message == (
        "2 other Checkov best-practice check(s) failed on 1 changed resource(s): CKV_AWS_118, CKV_AWS_129."
    )


def test_evaluate_with_checkov():
    findings = evaluate(load_plan(FIXTURES / "checkov_plan.json"), checkov=load_checkov(FIXTURES / "checkov.json"))
    assert [f.rule_id for f in findings] == ["checkov", "checkov", "open-to-internet", "checkov"]


def test_cli_with_checkov(capsys):
    assert main([str(FIXTURES / "checkov_plan.json"), "--checkov", str(FIXTURES / "checkov.json")]) == 0
    out = capsys.readouterr().out
    assert "[checkov] aws_db_instance.main: database storage is not encrypted" in out
    assert out.count("SSH (port 22)") == 1


def test_cli_rejects_plan_as_checkov(capsys):
    assert main([str(FIXTURES / "checkov_plan.json"), "--checkov", str(FIXTURES / "mixed.json")]) == 2
    assert "not Checkov output" in capsys.readouterr().err


def test_fixture_is_a_terraform_plan_scan():
    assert json.loads((FIXTURES / "checkov.json").read_text(encoding="utf-8"))["check_type"] == "terraform_plan"
