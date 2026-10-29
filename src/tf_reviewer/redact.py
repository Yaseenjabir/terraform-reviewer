"""Remove things that look like secrets from text before it leaves the runner.

Values Terraform marks as sensitive are already masked when the plan is read (plan.py). This is
the second layer: secrets nobody marked, for example an API key written into a tag, a
description or a resource name. rules.evaluate() passes every finding's text through redact(),
so no finding can carry a secret to an LLM request or a PR comment.

The patterns aim at known secret formats and obvious "name = secret" pairs. They can't catch
every secret, so they're a safety net, not a guarantee.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Each pattern's text (or only its "secret" group, if it has one) is replaced with REDACTED.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.DOTALL),
    ),
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16,}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("Groq API key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}")),
    # Fixed-length formats still match greedily ({35,}, not {35}) so no fragment is left behind.
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35,}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    # scheme://user:password@host -> keep the user, hide the password
    ("password in a URL", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:(?P<secret>[^/\s@]+)@")),
    # password = "...", api_key: ..., client_secret=... -> hide the value
    (
        "named secret",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|client_secret|api[_-]?key|access[_-]?key|secret[_-]?key|"
            r"auth[_-]?token|access[_-]?token|token)\b[\"']?\s*[:=]\s*[\"']?"
            # The value can't start with a bracket, so already-masked text such as
            # "(sensitive value)" or "[REDACTED]" is left alone.
            r"(?P<secret>[^\s\"',;()\[\]][^\s\"',;)]{3,})"
        ),
    ),
]


def _replace(match: re.Match[str]) -> str:
    if "secret" not in match.re.groupindex:
        return REDACTED
    start, end = match.span("secret")
    whole = match.group(0)
    offset = match.start()
    return whole[: start - offset] + REDACTED + whole[end - offset :]


def redact(text: str) -> str:
    """The text with anything that looks like a secret replaced by REDACTED."""
    for _, pattern in _PATTERNS:
        text = pattern.sub(_replace, text)
    return text
