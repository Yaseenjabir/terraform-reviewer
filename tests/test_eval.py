"""Keeps the evaluation suite (eval/) honest and catches accuracy regressions.

A rule or settings change that stops catching a case the suite used to catch, or that raises
a new false alarm on a safe case, fails these tests. Improvements are welcome: regenerate
eval/baseline.json and eval/RESULTS.md (see eval/README.md) to lock them in.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

from tf_reviewer.config import RULE_IDS
from tf_reviewer.rules import Risk

EVAL = Path(__file__).parent.parent / "eval"
sys.path.insert(0, str(EVAL))
import evaluate  # noqa: E402

CASES = sorted(p for p in (EVAL / "cases").iterdir() if p.is_dir())


@pytest.fixture(scope="module")
def results():
    return evaluate.evaluate_all()


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_case_is_complete(case):
    expected = yaml.safe_load((case / "expected.yml").read_text(encoding="utf-8"))
    assert set(expected) <= {"description", "category", "safe", "cost", "expect"}
    assert expected["description"] and expected["category"]
    for name in ("after.tf", "plan.json", "checkov.json"):
        assert (case / name).is_file(), f"{name} missing: run eval/generate.py {case.name}"
    assert (case / "infracost.json").is_file() == bool(expected.get("cost"))
    entries = expected["expect"]
    if expected["safe"]:
        assert entries == [] and expected["category"] == "safe"
    else:
        assert entries
    for entry in entries:
        assert set(entry) <= {"address", "risk", "rule"}
        assert entry["risk"] in Risk.__members__
        if "rule" in entry:
            assert entry["rule"] in RULE_IDS


def test_no_case_that_was_caught_is_missed(results):
    baseline = json.loads((EVAL / "baseline.json").read_text(encoding="utf-8"))
    summary = evaluate.summarise(results)
    assert set(baseline["caught"]) - set(summary["caught"]) == set()


def test_no_new_false_alarms(results):
    baseline = json.loads((EVAL / "baseline.json").read_text(encoding="utf-8"))
    summary = evaluate.summarise(results)
    assert set(summary["false_alarms"]) - set(baseline["false_alarms"]) == set()


def test_results_file_is_up_to_date(results):
    assert (EVAL / "RESULTS.md").read_text(encoding="utf-8") == evaluate.format_markdown(results), (
        "regenerate it: python eval/evaluate.py --markdown eval/RESULTS.md --json eval/baseline.json"
    )
