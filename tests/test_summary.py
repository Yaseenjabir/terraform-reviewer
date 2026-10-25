from pathlib import Path

from tf_reviewer.cli import main
from tf_reviewer.plan import load_plan
from tf_reviewer.summary import format_path, format_summary

FIXTURES = Path(__file__).parent / "fixtures"


def test_summary_of_mixed_plan():
    assert format_summary(load_plan(FIXTURES / "mixed.json")) == "\n".join(
        [
            "Plan: 1 to create, 1 to update, 2 to replace, 1 to delete, 1 moved",
            "  +   terraform_data.add",
            "  ->  terraform_data.new_name  (moved from terraform_data.old_name)",
            "  -   terraform_data.remove",
            "  -/+ terraform_data.replace  (forced by: triggers_replace)",
            "  +/- terraform_data.replace_first  (forced by: triggers_replace)",
            "  ~   terraform_data.update",
        ]
    )


def test_summary_of_plan_without_changes():
    assert format_summary(load_plan(FIXTURES / "no_changes.json")) == "No changes."


def test_format_path():
    assert format_path(("triggers_replace",)) == "triggers_replace"
    assert format_path(("ingress", 0, "cidr_blocks")) == "ingress[0].cidr_blocks"


def test_cli_prints_summary(capsys):
    assert main([str(FIXTURES / "mixed.json")]) == 0
    assert capsys.readouterr().out.startswith("Plan: 1 to create")


def test_cli_reports_bad_input(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    assert main([str(missing)]) == 2
    assert capsys.readouterr().err.startswith("error:")
