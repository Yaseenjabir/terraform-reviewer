"""Measure how well tf-reviewer finds risky changes, using the labelled cases in eval/cases.

Each case has a real plan (plan.json), Checkov's results for it (checkov.json), sometimes an
Infracost diff (infracost.json), and the right answer (expected.yml). generate.py makes the
inputs; this script only reads them, so it needs no cloud, LocalStack or LLM.

Two reviewers are scored on every case:
- tf-reviewer, as the Action runs it: rules + Checkov + cost, default settings, no LLM
  (the LLM never changes findings, so it can't change these numbers).
- Checkov alone, the baseline: what a team gets from running Checkov on the plan.

Scoring:
- A finding *flags* a resource if it is MEDIUM or HIGH. LOW findings are folded away in the
  PR comment, so they don't count as catching anything (or as false alarms).
- An expected entry is *met* when a finding flags its address with at least the expected risk,
  from the expected rule if one is named. A risky case is *caught* when every entry is met.
- A safe case raises a *false alarm* when anything is flagged.
- Checkov has no risk levels: it catches a case when it fails some check on every expected
  address, and raises a false alarm on a safe case when it fails any check on a resource.

    python eval/evaluate.py                  # summary and every miss / false alarm
    python eval/evaluate.py --markdown FILE  # also write the results as Markdown
    python eval/evaluate.py --json FILE      # also write the results as JSON
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tf_reviewer.checkov import load_checkov
from tf_reviewer.cost import load_cost_diff
from tf_reviewer.plan import load_plan
from tf_reviewer.rules import Finding, Risk, evaluate

CASES = Path(__file__).parent / "cases"


@dataclass
class CaseResult:
    name: str
    category: str
    safe: bool
    description: str
    expected: list[dict]
    flagged: list[Finding]  # tf-reviewer's MEDIUM/HIGH findings
    missed: list[dict] = field(default_factory=list)  # expected entries tf-reviewer didn't meet
    extra: list[Finding] = field(default_factory=list)  # flagged, but not expected
    checkov_failed: dict[str, list[str]] = field(default_factory=dict)  # address -> check IDs
    checkov_missed: list[dict] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def caught(self) -> bool:
        return not self.safe and not self.missed

    @property
    def false_alarm(self) -> bool:
        return self.safe and bool(self.flagged)

    @property
    def checkov_caught(self) -> bool:
        return not self.safe and not self.checkov_missed

    @property
    def checkov_false_alarm(self) -> bool:
        return self.safe and bool(self.checkov_failed)


def _met(entry: dict, findings: list[Finding]) -> bool:
    return any(
        f.address == entry["address"]
        and f.risk >= Risk[entry["risk"]]
        and ("rule" not in entry or f.rule_id == entry["rule"])
        for f in findings
    )


def evaluate_case(case: Path) -> CaseResult:
    expected = yaml.safe_load((case / "expected.yml").read_text(encoding="utf-8"))
    entries = expected.get("expect") or []

    start = time.perf_counter()
    changes = load_plan(case / "plan.json")
    checkov = load_checkov(case / "checkov.json")
    cost = load_cost_diff(case / "infracost.json") if (case / "infracost.json").exists() else None
    findings = evaluate(changes, cost=cost, checkov=checkov)
    seconds = time.perf_counter() - start

    flagged = [f for f in findings if f.risk >= Risk.MEDIUM]
    expected_addresses = {e["address"] for e in entries}

    # Checkov alone, on resources only (its provider checks fire on LocalStack's fake keys).
    resources = {c.address for c in changes}
    checkov_failed: dict[str, list[str]] = defaultdict(list)
    for failure in checkov:
        if failure.address in resources:
            checkov_failed[failure.address].append(failure.check_id)

    return CaseResult(
        name=case.name,
        category=expected["category"],
        safe=bool(expected.get("safe")),
        description=expected["description"],
        expected=entries,
        flagged=flagged,
        missed=[e for e in entries if not _met(e, findings)],
        extra=[f for f in flagged if f.address not in expected_addresses],
        checkov_failed=dict(checkov_failed),
        checkov_missed=[e for e in entries if e["address"] not in checkov_failed],
        seconds=seconds,
    )


def evaluate_all(cases_dir: Path = CASES) -> list[CaseResult]:
    return [evaluate_case(case) for case in sorted(p for p in cases_dir.iterdir() if p.is_dir())]


def _percent(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "-"


def summarise(results: list[CaseResult]) -> dict:
    risky = [r for r in results if not r.safe]
    safe = [r for r in results if r.safe]
    by_category = defaultdict(list)
    for r in risky:
        by_category[r.category].append(r)
    return {
        "cases": len(results),
        "risky": len(risky),
        "safe": len(safe),
        "caught": sorted(r.name for r in risky if r.caught),
        "false_alarms": sorted(r.name for r in safe if r.false_alarm),
        "checkov_caught": sorted(r.name for r in risky if r.checkov_caught),
        "checkov_false_alarms": sorted(r.name for r in safe if r.checkov_false_alarm),
        "extra_findings": sum(len(r.extra) for r in risky),
        "categories": {
            category: {
                "cases": len(rs),
                "caught": sum(r.caught for r in rs),
                "checkov_caught": sum(r.checkov_caught for r in rs),
            }
            for category, rs in sorted(by_category.items())
        },
    }


def format_markdown(results: list[CaseResult]) -> str:
    s = summarise(results)
    lines = [
        "# Evaluation results",
        "",
        f"{s['cases']} labelled Terraform changes: {s['risky']} risky, {s['safe']} safe. "
        "Generated by `python eval/evaluate.py --markdown eval/RESULTS.md`; see eval/README.md.",
        "",
        "| | tf-reviewer | Checkov alone |",
        "|---|---|---|",
        f"| Risky changes caught | {_percent(len(s['caught']), s['risky'])} | "
        f"{_percent(len(s['checkov_caught']), s['risky'])} |",
        f"| Safe changes with a false alarm | {_percent(len(s['false_alarms']), s['safe'])} | "
        f"{_percent(len(s['checkov_false_alarms']), s['safe'])} |",
        "",
        "Checkov alone is scored generously: a risky change counts as caught when Checkov fails *any* check on "
        "the right resource, even an unrelated one (for example, an encryption check on a table whose cost "
        "jumped). It can't see deletions or replacements at all, because it only reads the planned end state.",
        "",
        "## Caught, by kind of risk",
        "",
        "| Kind | Cases | tf-reviewer | Checkov alone |",
        "|---|---|---|---|",
    ]
    for category, c in s["categories"].items():
        lines.append(f"| {category} | {c['cases']} | {c['caught']} | {c['checkov_caught']} |")

    misses = [r for r in results if r.missed]
    lines += ["", "## Missed by tf-reviewer", ""]
    lines += [
        f"- **{r.name}**: {r.description} Expected {', '.join(_entry(e) for e in r.missed)}; "
        f"flagged: {_flagged(r.flagged)}."
        for r in misses
    ] or ["None."]

    alarms = [r for r in results if r.false_alarm]
    lines += ["", "## False alarms from tf-reviewer", ""]
    lines += [f"- **{r.name}**: {r.description} Flagged: {_flagged(r.flagged)}." for r in alarms] or ["None."]

    extra = [r for r in results if not r.safe and r.extra]
    lines += ["", "## Extra findings on risky changes", "", "Flagged resources the case didn't list (not always wrong).", ""]
    lines += [f"- **{r.name}**: {_flagged(r.extra)}." for r in extra] or ["None."]

    lines += [
        "",
        "## Every case",
        "",
        "| Case | Kind | tf-reviewer | Checkov alone |",
        "|---|---|---|---|",
    ]
    for r in results:
        if r.safe:
            ours = "false alarm" if r.false_alarm else "quiet"
            theirs = f"false alarm ({sum(map(len, r.checkov_failed.values()))} checks)" if r.checkov_false_alarm else "quiet"
        else:
            ours = "caught" if r.caught else "missed"
            theirs = "caught" if r.checkov_caught else "missed"
        lines.append(f"| {r.name} | {r.category} | {ours} | {theirs} |")
    return "\n".join(lines) + "\n"


def _entry(e: dict) -> str:
    rule = f" from {e['rule']}" if "rule" in e else ""
    return f"`{e['address']}` at {e['risk']}{rule}"


def _flagged(findings: list[Finding]) -> str:
    if not findings:
        return "nothing"
    return "; ".join(f"`{f.address}` {f.risk.name} ({f.rule_id})" for f in findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markdown", metavar="FILE", help="write the results as Markdown")
    parser.add_argument("--json", metavar="FILE", help="write the summary as JSON")
    args = parser.parse_args()

    results = evaluate_all()
    s = summarise(results)
    print(f"Risky changes caught:   tf-reviewer {_percent(len(s['caught']), s['risky'])}, "
          f"Checkov alone {_percent(len(s['checkov_caught']), s['risky'])}")
    print(f"False alarms on safe:   tf-reviewer {_percent(len(s['false_alarms']), s['safe'])}, "
          f"Checkov alone {_percent(len(s['checkov_false_alarms']), s['safe'])}")
    for r in results:
        if r.missed:
            print(f"  missed      {r.name}: expected {', '.join(_entry(e) for e in r.missed)}; flagged {_flagged(r.flagged)}")
        if r.false_alarm:
            print(f"  false alarm {r.name}: {_flagged(r.flagged)}")
    average_ms = 1000 * sum(r.seconds for r in results) / len(results)
    print(f"Average review time per case (no LLM): {average_ms:.1f} ms")
    if args.markdown:
        Path(args.markdown).write_text(format_markdown(results), encoding="utf-8", newline="\n")
    if args.json:
        Path(args.json).write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
