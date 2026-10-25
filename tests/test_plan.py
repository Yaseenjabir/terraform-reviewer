import json
from pathlib import Path

import pytest

from tf_reviewer.plan import Action, PlanError, load_plan, parse_plan

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mixed():
    return {c.address: c for c in load_plan(FIXTURES / "mixed.json")}


def test_each_kind_of_change_gets_the_right_action(mixed):
    assert {address: c.action for address, c in mixed.items()} == {
        "terraform_data.add": Action.CREATE,
        "terraform_data.keep": Action.NO_OP,
        "terraform_data.new_name": Action.NO_OP,
        "terraform_data.remove": Action.DELETE,
        "terraform_data.replace": Action.REPLACE,
        "terraform_data.replace_first": Action.REPLACE,
        "terraform_data.update": Action.UPDATE,
    }


def test_replacement_order(mixed):
    assert not mixed["terraform_data.replace"].creates_before_destroying
    assert mixed["terraform_data.replace_first"].creates_before_destroying


def test_replacement_records_what_forced_it(mixed):
    assert mixed["terraform_data.replace"].replace_paths == (("triggers_replace",),)
    assert mixed["terraform_data.replace"].action_reason == "replace_because_cannot_update"


def test_moved_resource_keeps_its_old_address(mixed):
    moved = mixed["terraform_data.new_name"]
    assert moved.was_moved
    assert moved.previous_address == "terraform_data.old_name"
    assert not moved.is_change


def test_delete_reason(mixed):
    assert mixed["terraform_data.remove"].action_reason == "delete_because_no_resource_config"


def test_plan_without_changes():
    changes = load_plan(FIXTURES / "no_changes.json")
    assert changes
    assert not any(c.is_change for c in changes)


def test_plan_without_resource_changes_key():
    assert parse_plan({"format_version": "1.2", "planned_values": {}}) == []


def test_rejects_non_plan_json():
    with pytest.raises(PlanError, match="not a Terraform plan"):
        parse_plan({"hello": "world"})


def test_rejects_state_json():
    with pytest.raises(PlanError, match="looks like Terraform state"):
        parse_plan({"format_version": "1.0", "values": {}})


def test_rejects_unknown_actions():
    plan = {
        "format_version": "1.2",
        "planned_values": {},
        "resource_changes": [
            {"address": "x.y", "type": "x", "change": {"actions": ["teleport"]}},
        ],
    }
    with pytest.raises(PlanError, match="unsupported actions"):
        parse_plan(plan)


def test_rejects_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(PlanError, match="not valid JSON"):
        load_plan(bad)


def test_reads_file_with_byte_order_mark(tmp_path):
    # Windows PowerShell writes UTF-8 files with a BOM when output is redirected.
    with_bom = tmp_path / "plan.json"
    with_bom.write_bytes(b"\xef\xbb\xbf" + (FIXTURES / "mixed.json").read_bytes())
    assert len(load_plan(with_bom)) == 7


def test_fixtures_are_real_plans():
    for name in ("mixed.json", "no_changes.json"):
        assert "planned_values" in json.loads((FIXTURES / name).read_text(encoding="utf-8"))
