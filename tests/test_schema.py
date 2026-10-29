"""Tests for checking the LLM's fixes against a provider schema (schema.py) and the rewrite step."""

import json
from pathlib import Path

import pytest

from tf_reviewer import cli
from tf_reviewer.llm import Explanation, LLMError, build_request, explain
from tf_reviewer.rules import Finding, Risk
from tf_reviewer.schema import NameChecker, SchemaError, attribute_names, load_schema, named_attributes
from tf_reviewer.summary import format_findings

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = FIXTURES / "provider_schema.json"


def test_attribute_names_include_object_keys_and_nested_blocks():
    names = attribute_names(load_schema(SCHEMA), ["aws_security_group"])
    assert names == {
        "aws_security_group": ["cidr_blocks", "create", "from_port", "ingress", "name", "timeouts", "to_port"]
    }


def test_unknown_types_are_left_out():
    assert attribute_names(load_schema(SCHEMA), ["aws_s3_bucket", "aws_nothing"]) == {"aws_s3_bucket": ["bucket"]}


@pytest.mark.parametrize("content", ["{}", "[]", '{"provider_schemas": []}'])
def test_not_a_schema(tmp_path, content):
    path = tmp_path / "s.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SchemaError, match="not a provider schema"):
        load_schema(path)


def test_invalid_json(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(SchemaError, match="not valid JSON"):
        load_schema(path)


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ('Set `cidr_blocks = ["10.0.0.0/8"]`.', ["cidr_blocks"]),
        ("Add `lifecycle { prevent_destroy = true }`.", ["lifecycle", "prevent_destroy"]),
        ("Use `cidr_ipv6` instead.", ["cidr_ipv6"]),
        ("Change `0.0.0.0/0` to `10.0.0.0/8` on `aws_security_group.app`.", []),
        ("Compare with `==`: `a == b`.", []),
        ("No code here.", []),
    ],
)
def test_named_attributes(text, names):
    assert named_attributes(text) == names


CHECKER = NameChecker.from_schema(load_schema(SCHEMA))


@pytest.mark.parametrize(
    ("text", "unknown"),
    [
        ("Narrow `cidr_blocks` in `ingress`; keep `from_port = 22`.", []),
        ("Add `lifecycle { prevent_destroy = true }`.", []),
        ("Set `source_security_group_id` instead.", ["source_security_group_id"]),
        # an attribute of another resource type the text names
        ("Move it to an `aws_s3_bucket` with `bucket = \"x\"`.", []),
        ("Set `bucket = \"x\"`.", ["bucket"]),
    ],
)
def test_name_checker(text, unknown):
    assert CHECKER.unknown(text, "aws_security_group") == unknown


def test_name_checker_accepts_labels_and_ignores_unknown_types():
    assert CHECKER.unknown("Rename `web` with a `moved` block.", "aws_security_group", {"web"}) == []
    assert CHECKER.unknown("Set `anything`.", "aws_not_in_schema") == []


# --- the rewrite step ---------------------------------------------------------------------

FINDINGS = [
    Finding("open-to-internet", Risk.HIGH, "aws_security_group.web", "opens SSH."),
    Finding("data-loss", Risk.HIGH, "aws_s3_bucket.logs", "is deleted."),
]
TYPES = {"aws_security_group.web": "aws_security_group", "aws_s3_bucket.logs": "aws_s3_bucket"}


def check(finding, text):
    return CHECKER.unknown(text, TYPES[finding.address])


def answer(fix1, fix2="Add `lifecycle { prevent_destroy = true }`."):
    return json.dumps(
        {
            "summary": "Two risks.",
            "explanations": [{"id": 1, "why": "Open SSH.", "fix": fix1}, {"id": 2, "why": "Lost data.", "fix": fix2}],
        }
    )


class Scripted:
    """Returns the scripted answers in order and records the requests."""

    description = "scripted"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def complete(self, system, user):
        self.requests.append(json.loads(user))
        result = self.answers.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


GOOD = "Narrow `cidr_blocks`."
BAD = "Use `source_security_group_id`."


def test_good_fixes_need_one_request():
    backend = Scripted(answer(GOOD))
    review = explain(FINDINGS, backend, check)
    assert len(backend.requests) == 1 and review.requests == 1 and review.dropped_fixes == 0


def test_bad_fix_is_rewritten():
    backend = Scripted(answer(BAD), answer(GOOD, fix2="Something else entirely."))
    review = explain(FINDINGS, backend, check)
    assert backend.requests[1]["unknown_names"] == {"1": ["source_security_group_id"]}
    assert review.explanations[0] == Explanation("Open SSH.", GOOD)
    # Fixes that were fine the first time are kept, whatever the second answer says.
    assert review.explanations[1].fix == "Add `lifecycle { prevent_destroy = true }`."
    assert (review.requests, review.dropped_fixes) == (2, 0)


def test_fix_still_bad_after_rewrite_is_dropped():
    review = explain(FINDINGS, Scripted(answer(BAD), answer(BAD)), check)
    assert review.explanations[0] == Explanation("Open SSH.", "")
    assert review.dropped_fixes == 1


def test_failed_rewrite_request_drops_the_bad_fix():
    review = explain(FINDINGS, Scripted(answer(BAD), LLMError("rate limited")), check)
    assert review.explanations[0] == Explanation("Open SSH.", "") and review.dropped_fixes == 1


def test_broken_rewrite_answer_drops_the_bad_fix():
    review = explain(FINDINGS, Scripted(answer(BAD), "not json"), check)
    assert review.explanations[0].fix == "" and review.dropped_fixes == 1


def test_no_check_means_no_rewrite():
    backend = Scripted(answer(BAD))
    assert explain(FINDINGS, backend).explanations[0].fix == BAD
    assert len(backend.requests) == 1


def test_first_request_has_no_unknown_names():
    assert "unknown_names" not in json.loads(build_request(FINDINGS))


def test_dropped_fix_is_not_shown():
    review = explain(FINDINGS, Scripted(answer(BAD), answer(BAD)), check)
    text = format_findings(FINDINGS, review)
    assert "Why: Open SSH." in text and BAD not in text and text.count("Fix:") == 1


# --- the CLI ------------------------------------------------------------------------------


@pytest.fixture
def scripted(monkeypatch):
    def use(*answers):
        backend = Scripted(*answers)
        monkeypatch.setattr(cli, "make_backend", lambda config: backend)
        return backend

    return use


def _sg_answer(fix):
    # open_ports.json has four findings; only the first (aws_security_group.web) matters here.
    explanations = [{"id": i, "why": "w", "fix": fix if i == 1 else "Remove the rule."} for i in range(1, 5)]
    return json.dumps({"summary": "s", "explanations": explanations})


def test_cli_rewrites_with_provider_schema(scripted):
    backend = scripted(_sg_answer(BAD), _sg_answer(GOOD))
    assert cli.main([str(FIXTURES / "open_ports.json"), "--provider-schema", str(SCHEMA)]) == 0
    assert len(backend.requests) == 2


def test_cli_without_schema_does_not_check(scripted):
    backend = scripted(_sg_answer(BAD))
    cli.main([str(FIXTURES / "open_ports.json")])
    assert len(backend.requests) == 1


def test_cli_bad_schema_is_only_a_warning(scripted, tmp_path, capsys):
    backend = scripted(_sg_answer(BAD))
    bad = tmp_path / "schema.json"
    bad.write_text("{}", encoding="utf-8")
    assert cli.main([str(FIXTURES / "open_ports.json"), "--provider-schema", str(bad)]) == 0
    assert "warning: provider schema not used" in capsys.readouterr().err
    assert len(backend.requests) == 1


def test_cli_missing_schema_is_only_a_warning(scripted, tmp_path, capsys):
    scripted(_sg_answer(BAD))
    assert cli.main([str(FIXTURES / "open_ports.json"), "--provider-schema", str(tmp_path / "none.json")]) == 0
    assert "warning: provider schema not used" in capsys.readouterr().err
