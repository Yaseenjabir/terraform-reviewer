"""Format the review as one Markdown comment for the pull request.

The comment starts with a hidden marker (MARKER) so the Action can find and update its own
comment instead of posting a new one on every push.

Resource addresses, finding messages and everything the LLM writes are untrusted: they come
from the pull request author or from a model reading their text. In Markdown such text could
mention people (@user), add links or tracking images, inject HTML or a fake MARKER, or break
the layout. So untrusted text is always escaped (_text) or put in a code span (_code), where
GitHub renders nothing.
"""

from __future__ import annotations

import re

from tf_reviewer.cost import CostDiff
from tf_reviewer.llm import Review
from tf_reviewer.plan import ResourceChange
from tf_reviewer.redact import redact
from tf_reviewer.rules import Finding, Risk, overall_risk
from tf_reviewer.summary import format_cost, notes, plan_headline, shown_changes, symbol

MARKER = "<!-- tf-reviewer -->"

# GitHub rejects comments over 65,536 characters; keep some room.
MAX_COMMENT = 60_000
MAX_PLAN_LINES = 200

_ICONS = {Risk.HIGH: "🔴", Risk.MEDIUM: "🟠", Risk.LOW: "🟡"}

_CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
# "$" too: GitHub renders text between two dollar signs as a math formula.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]()#|!~$])")
_LINE_START = re.compile(r"^(?=[-+=]|\d+[.)])")


def _code(text: str) -> str:
    """Text as an inline code span, whatever backticks it contains."""
    text = " ".join(text.split())
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _escape(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _MARKDOWN_SPECIAL.sub(r"\\\1", text)
    # A zero-width space stops @mentions and automatic links without changing what people see.
    return text.replace("@", "@​").replace("://", ":​//").replace("www.", "www​.")


def _text(text: str) -> str:
    """Untrusted text as one line of plain Markdown. Paired backticks stay code spans."""
    text = " ".join(text.split())
    parts, pos = [], 0
    for match in _CODE_SPAN.finditer(text):
        parts += [_escape(text[pos : match.start()]), _code(match.group(2))]
        pos = match.end()
    parts.append(_escape(text[pos:]))
    return _LINE_START.sub("\\\\", "".join(parts))


def _finding(finding: Finding, review: Review | None, index: int) -> str:
    lines = [f"- {_ICONS[finding.risk]} **{finding.risk.name}** {_code(finding.rule_id)}: {_text(finding.message)}"]
    explanation = review.explanations[index] if review is not None else None
    if explanation is not None:
        lines.append(f"  - **Why:** {_text(explanation.why)}")
        lines.append(f"  - **Fix:** {_text(explanation.fix)}")
    return "\n".join(lines)


def _details(summary: str, body: list[str]) -> list[str]:
    return ["<details>", f"<summary>{summary}</summary>", "", *body, "", "</details>"]


def _plan_section(changes: list[ResourceChange]) -> list[str]:
    shown = shown_changes(changes)
    if not shown:
        return []
    lines = []
    for change in shown[:MAX_PLAN_LINES]:
        extra = notes(change)
        address = redact(change.address)  # for_each keys can hold secrets
        lines.append(f"- {_code(f'{symbol(change)} {address}')}" + (f" ({_text(extra)})" if extra else ""))
    if len(shown) > MAX_PLAN_LINES:
        lines.append(f"- ...and {len(shown) - MAX_PLAN_LINES} more")
    return _details(f"Plan changes ({len(shown)})", lines)


def _render(
    changes: list[ResourceChange],
    findings: list[Finding],
    review: Review | None,
    cost: CostDiff | None,
    shown: int,
) -> str:
    risk = overall_risk(findings)
    title = f"{_ICONS[risk]} Terraform review: {risk.name} risk" if risk else "✅ Terraform review: no risky changes"
    lines = [MARKER, f"### {title}", "", f"**Plan:** {_text(plan_headline(changes))}"]
    if cost is not None:
        lines[-1] += "  "  # Markdown line break
        lines.append(_text(format_cost(cost)))

    if review is not None:
        lines += ["", f"> **AI summary:** {_text(review.summary)}"]

    numbered = list(enumerate(findings))[:shown]
    main = [_finding(f, review, i) for i, f in numbered if f.risk > Risk.LOW]
    low = [_finding(f, review, i) for i, f in numbered if f.risk == Risk.LOW]
    if main:
        lines += ["", "**Findings**", "", *main]
    if low:
        lines += [""] + _details(f"{len(low)} low-risk finding{'s' if len(low) > 1 else ''}", low)
    if shown < len(findings):
        lines += ["", f"_...and {len(findings) - shown} more findings not shown here (comment too long). See the workflow log._"]

    plan = _plan_section(changes)
    if plan:
        lines += [""] + plan

    footer = "Risk levels come from tf-reviewer's rules, not from AI."
    if review is not None:
        footer += f" Explanations written by {_text(review.model)}: check them before acting."
    lines += ["", "---", f"<sub>{footer}</sub>"]
    return "\n".join(lines) + "\n"


def format_comment(
    changes: list[ResourceChange],
    findings: list[Finding],
    review: Review | None = None,
    cost: CostDiff | None = None,
) -> str:
    """The whole PR comment, riskiest findings first, within GitHub's size limit."""
    shown = len(findings)
    comment = _render(changes, findings, review, cost, shown)
    while len(comment) > MAX_COMMENT and shown > 0:
        shown -= 1
        comment = _render(changes, findings, review, cost, shown)
    return comment
