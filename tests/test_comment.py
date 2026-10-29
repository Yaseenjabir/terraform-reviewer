"""Tests for the pull request comment (comment.py)."""

from pathlib import Path

import pytest

from tf_reviewer import cli, comment
from tf_reviewer.comment import MARKER, _code, _text, format_comment
from tf_reviewer.cost import load_cost_diff
from tf_reviewer.llm import Explanation, Review
from tf_reviewer.plan import Action, ResourceChange, load_plan
from tf_reviewer.redact import REDACTED
from tf_reviewer.rules import Finding, Risk, evaluate
from tf_reviewer.summary import format_summary

FIXTURES = Path(__file__).parent / "fixtures"

ZWSP = "​"


def _deleted(address, type_="aws_s3_bucket"):
    return ResourceChange(address=address, type=type_, action=Action.DELETE, actions=("delete",))


# --- the whole comment --------------------------------------------------------------------


def test_comment_for_mixed_plan():
    changes = load_plan(FIXTURES / "mixed.json")
    cost = load_cost_diff(FIXTURES / "infracost_diff.json")
    findings = evaluate(changes, cost=cost)
    text = format_comment(changes, findings, cost=cost)
    assert text.startswith(MARKER + "\n### 🟠 Terraform review: MEDIUM risk\n")
    assert "**Plan:** 1 to create, 1 to update, 2 to replace, 1 to delete, 1 moved  \n" in text
    assert "Monthly cost: \\$48.81 -&gt; \\$139.91 \\(+\\$91.10, +187%\\)" in text
    assert "- 🟠 **MEDIUM** `resource-removed`: terraform\\_data.remove will be deleted." in text
    assert "- `-/+ terraform_data.replace` (forced by: triggers\\_replace)" in text
    assert "<summary>Plan changes (6)</summary>" in text
    assert "AI summary" not in text and "Explanations written by" not in text
    assert text.endswith("<sub>Risk levels come from tf-reviewer's rules, not from AI.</sub>\n")


def test_comment_with_review():
    findings = [
        Finding("data-loss", Risk.HIGH, "aws_db_instance.main", "aws_db_instance.main will be deleted."),
        Finding("checkov", Risk.LOW, "aws_s3_bucket.b", "aws_s3_bucket.b: 3 other checks failed."),
    ]
    review = Review(
        summary="A database is deleted.",
        explanations=(Explanation("The data is lost.", "Add `prevent_destroy = true`."), None),
        model="m via fake",
    )
    text = format_comment([_deleted("aws_db_instance.main", "aws_db_instance")], findings, review)
    assert "> **AI summary:** A database is deleted." in text
    assert (
        "- 🔴 **HIGH** `data-loss`: aws\\_db\\_instance.main will be deleted.\n"
        "  - **Why:** The data is lost.\n"
        "  - **Fix:** Add `prevent_destroy = true`."
    ) in text
    # LOW findings are folded away
    assert "<summary>1 low-risk finding</summary>\n\n- 🟡 **LOW** `checkov`" in text
    assert "Explanations written by m via fake: check them before acting." in text


def test_comment_without_findings():
    text = format_comment(load_plan(FIXTURES / "no_changes.json"), [])
    assert "### ✅ Terraform review: no risky changes" in text
    assert "**Plan:** no changes" in text
    assert "<details>" not in text


def test_marker_appears_once_even_if_text_contains_it():
    findings = [Finding("resource-removed", Risk.MEDIUM, "x", f"x {MARKER} will be deleted.")]
    review = Review(MARKER, (Explanation(MARKER, MARKER),), MARKER)
    assert format_comment([], findings, review).count(MARKER) == 1


def test_long_comments_are_cut_to_fit():
    findings = [Finding("resource-removed", Risk.MEDIUM, f"r{i}", f"r{i} " + "x" * 1000 + " will be deleted.") for i in range(100)]
    text = format_comment([], findings)
    assert len(text) <= comment.MAX_COMMENT
    assert "more findings not shown here (comment too long)" in text
    assert "r0 " in text  # riskiest (first) findings are kept


def test_long_plans_are_cut():
    changes = [_deleted(f"aws_s3_bucket.b{i}") for i in range(comment.MAX_PLAN_LINES + 5)]
    text = format_comment(changes, [])
    assert "- ...and 5 more" in text


# --- untrusted text -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ping @octocat now", f"ping @{ZWSP}octocat now"),
        ("see https://evil.example/x", f"see https:{ZWSP}//evil.example/x"),
        ("go to www.evil.example", f"go to www{ZWSP}.evil.example"),
        ("![x](http://t.example/p.png)", f"\\!\\[x\\]\\(http:{ZWSP}//t.example/p.png\\)"),
        ("<img src=x onerror=alert(1)>", "&lt;img src=x onerror=alert\\(1\\)&gt;"),
        ("</details> <!-- tf-reviewer -->", "&lt;/details&gt; &lt;\\!-- tf-reviewer --&gt;"),
        ("**bold** _it_ ~~gone~~ a|b", "\\*\\*bold\\*\\* \\_it\\_ \\~\\~gone\\~\\~ a\\|b"),
        ("costs $5 and $6", "costs \\$5 and \\$6"),
        ("line one\n# Heading\n- item", "line one \\# Heading - item"),
        ("- starts like a list", "\\- starts like a list"),
        ("1. starts like a list", "\\1. starts like a list"),
        ("set `prevent_destroy = true` here", "set `prevent_destroy = true` here"),
        ("code `@user <b>` stays literal", "code `@user <b>` stays literal"),
        ("one ` backtick", "one \\` backtick"),
    ],
)
def test_untrusted_text_is_escaped(text, expected):
    assert _text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("aws_s3_bucket.b", "`aws_s3_bucket.b`"),
        ('aws_s3_bucket.b["a`b"]', '``aws_s3_bucket.b["a`b"]``'),
        ("`edge`", "`` `edge` ``"),
        ("two\nlines", "`two lines`"),
    ],
)
def test_code_spans_hold_any_text(text, expected):
    assert _code(text) == expected


def test_injected_markdown_in_an_address_stays_inside_code():
    address = 'aws_s3_bucket.b["`](https://evil.example) @octocat"]'
    text = format_comment([_deleted(address)], [])
    assert '- ``- aws_s3_bucket.b["`](https://evil.example) @octocat"]``' in text


def test_plan_addresses_are_redacted():
    changes = [_deleted('aws_s3_bucket.b["AKIAIOSFODNN7EXAMPLE"]')]
    assert "AKIA" not in format_comment(changes, [])
    assert "AKIA" not in format_summary(changes)
    assert REDACTED in format_summary(changes)


# --- the CLI ------------------------------------------------------------------------------


def test_cli_markdown_format(capsys):
    assert cli.main([str(FIXTURES / "open_ports.json"), "--format", "markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(MARKER + "\n### 🔴 Terraform review: HIGH risk")
    assert "Plan: 5 to create" not in out  # no plain-text summary mixed in
