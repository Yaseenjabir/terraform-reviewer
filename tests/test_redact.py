"""Tests for secret masking (plan.py) and redaction (redact.py).

All "secrets" here are fake values in the formats real ones use.
"""

from pathlib import Path

import pytest

from tf_reviewer.plan import SENSITIVE_VALUE, Action, ResourceChange, _mask_sensitive, load_plan, parse_plan
from tf_reviewer.redact import REDACTED, redact
from tf_reviewer.rules import evaluate

FIXTURES = Path(__file__).parent / "fixtures"


# --- layer 1: values Terraform marks as sensitive -----------------------------------------


def test_database_password_from_real_plan_is_masked():
    db = {c.address: c for c in load_plan(FIXTURES / "checkov_plan.json")}["aws_db_instance.main"]
    assert db.after["password"] == SENSITIVE_VALUE
    assert db.after["username"] == "app"  # not sensitive, kept


def test_masking_nested_values():
    value = {"a": "keep", "b": "hide", "c": {"d": "hide", "e": "keep"}, "f": ["x", "hide"], "g": None}
    sensitive = {"b": True, "c": {"d": True}, "f": [False, True], "g": True}
    assert _mask_sensitive(value, sensitive) == {
        "a": "keep",
        "b": SENSITIVE_VALUE,
        "c": {"d": SENSITIVE_VALUE, "e": "keep"},
        "f": ["x", SENSITIVE_VALUE],
        "g": None,
    }


def test_whole_value_marked_sensitive():
    assert _mask_sensitive({"a": 1}, True) == SENSITIVE_VALUE


def test_no_sensitive_information_keeps_values():
    assert _mask_sensitive({"a": 1}, None) == {"a": 1}
    assert _mask_sensitive({"a": 1}, False) == {"a": 1}


def test_before_and_after_are_both_masked():
    plan = {
        "format_version": "1.2",
        "planned_values": {},
        "resource_changes": [
            {
                "address": "aws_db_instance.main",
                "type": "aws_db_instance",
                "change": {
                    "actions": ["update"],
                    "before": {"password": "old-secret"},
                    "after": {"password": "new-secret"},
                    "before_sensitive": {"password": True},
                    "after_sensitive": {"password": True},
                },
            }
        ],
    }
    [db] = parse_plan(plan)
    assert db.before == {"password": SENSITIVE_VALUE} and db.after == {"password": SENSITIVE_VALUE}


# --- layer 2: text that looks like a secret -----------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("key AKIAIOSFODNN7EXAMPLE in use", f"key {REDACTED} in use"),
        ("temporary ASIAY34FZKBOKMUTVV7A", f"temporary {REDACTED}"),
        ("ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", REDACTED),
        ("github_pat_11ABCDEFG0123456789_abcdefghijklmnop", REDACTED),
        ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz", REDACTED),
        ("sk-proj-abcdefghijklmnopqrstuvwx", REDACTED),
        ("gsk_abcdefghijklmnopqrstuvwxyz012345", REDACTED),
        ("AIzaSyA-abcdefghijklmnopqrstuvwxyz012345", REDACTED),
        ("xoxb-1234567890-abcdefghij", REDACTED),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", REDACTED),
        ("postgres://admin:S3cr3tPass@db:5432/app", f"postgres://admin:{REDACTED}@db:5432/app"),
        ('password = "hunter2-real!"', f'password = "{REDACTED}"'),
        ("api_key: abcd1234efgh", f"api_key: {REDACTED}"),
        ("client_secret=xyz98765", f"client_secret={REDACTED}"),
        ('{"token": "abcdef123456"}', f'{{"token": "{REDACTED}"}}'),
    ],
)
def test_secrets_are_redacted(text, expected):
    assert redact(text) == expected


def test_private_key_block():
    text = "key: -----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY----- end"
    assert redact(text) == f"key: {REDACTED} end"


def test_truncated_private_key_is_still_hidden():
    assert redact("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg") == REDACTED


@pytest.mark.parametrize(
    "text",
    [
        "aws_security_group.web opens SSH (port 22) to the whole internet (0.0.0.0/0).",
        "arn:aws:iam::aws:policy/AdministratorAccess",
        "aws_db_instance.main will be replaced (forced by: identifier).",
        "Monthly cost rises by $91.10 (+187%, $48.81 -> $139.91).",
        "https://docs.example.com/guide",
        "tokens are counted per request",
        f"password: {SENSITIVE_VALUE}",
        "skip_final_snapshot = true",
    ],
)
def test_ordinary_text_is_untouched(text):
    assert redact(text) == text


def test_redaction_is_idempotent():
    once = redact('password="hunter2-real!" url=https://u:p4ss@h')
    assert redact(once) == once


# --- findings are redacted before anyone sees them -----------------------------------------


def test_findings_never_carry_secrets():
    # for_each keys can be any string, so a secret can end up in a resource address.
    address = 'aws_s3_bucket.b["AKIAIOSFODNN7EXAMPLE"]'
    deleted = ResourceChange(address=address, type="aws_s3_bucket", action=Action.DELETE, actions=("delete",))
    [finding] = evaluate([deleted])
    assert "AKIA" not in finding.address and "AKIA" not in finding.message
    assert finding.address == f'aws_s3_bucket.b["{REDACTED}"]'


def test_fixture_password_never_reaches_findings():
    for finding in evaluate(load_plan(FIXTURES / "checkov_plan.json")):
        assert "not-a-real-password" not in finding.message


def test_longer_than_standard_keys_leave_no_fragment():
    assert redact("AIza" + "a" * 40) == REDACTED
    assert redact("AKIA" + "A" * 20) == REDACTED
