"""Tests for the CLI options the GitHub Action uses: --fail-on, --comment-file and step outputs."""

from pathlib import Path

import pytest
import yaml

from tf_reviewer import cli
from tf_reviewer.comment import MARKER

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent

HIGH_PLAN = str(FIXTURES / "open_ports.json")  # overall risk HIGH
MEDIUM_PLAN = str(FIXTURES / "mixed.json")  # overall risk MEDIUM
NO_CHANGES = str(FIXTURES / "no_changes.json")


@pytest.mark.parametrize(
    ("plan", "fail_on", "code"),
    [
        (HIGH_PLAN, "high", 1),
        (HIGH_PLAN, "never", 0),
        (MEDIUM_PLAN, "high", 0),
        (MEDIUM_PLAN, "medium", 1),
        (MEDIUM_PLAN, "low", 1),
        (NO_CHANGES, "low", 0),
    ],
)
def test_fail_on(plan, fail_on, code, capsys):
    assert cli.main([plan, "--fail-on", fail_on]) == code
    if code:
        assert f"risk found (--fail-on {fail_on})" in capsys.readouterr().err


def test_never_fails_by_default():
    assert cli.main([HIGH_PLAN]) == 0


def test_comment_file_alongside_text_output(tmp_path, capsys):
    comment_file = tmp_path / "comment.md"
    assert cli.main([HIGH_PLAN, "--comment-file", str(comment_file)]) == 0
    assert comment_file.read_text(encoding="utf-8").startswith(MARKER)
    assert "Findings (overall risk: HIGH):" in capsys.readouterr().out  # the log still gets text


def test_unwritable_comment_file(tmp_path, capsys):
    assert cli.main([HIGH_PLAN, "--comment-file", str(tmp_path / "missing" / "c.md")]) == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(("plan", "risk", "count"), [(HIGH_PLAN, "high", 4), (NO_CHANGES, "none", 0)])
def test_step_outputs_in_github_actions(plan, risk, count, tmp_path, monkeypatch):
    output = tmp_path / "github_output"
    output.write_text("earlier=1\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    cli.main([plan])
    assert output.read_text(encoding="utf-8") == f"earlier=1\nrisk={risk}\nfindings={count}\n"


def test_no_step_outputs_outside_github_actions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main([HIGH_PLAN])
    assert list(tmp_path.iterdir()) == []


# --- action.yml ---------------------------------------------------------------------------


def test_action_never_pastes_inputs_into_scripts():
    # ${{ }} inside `run:` is pasted into the script before it runs, so a crafted value could
    # run commands. Inputs must reach scripts through `env:` instead.
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    for step in action["runs"]["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_action_outputs_come_from_the_review_step():
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    assert {name: o["value"] for name, o in action["outputs"].items()} == {
        "risk": "${{ steps.review.outputs.risk }}",
        "findings": "${{ steps.review.outputs.findings }}",
    }
