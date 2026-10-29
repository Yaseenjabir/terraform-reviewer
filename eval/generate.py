"""Regenerate the evaluation inputs from each case's Terraform files.

For every case in eval/cases/<name>/:
  1. apply before.tf (if any) to LocalStack, so the "existing infrastructure" really exists
  2. plan after.tf against it -> plan.json
  3. run Checkov on the plan -> checkov.json (trimmed: no file paths or code)
  4. if expected.yml has `cost: true`, price before.tf and after.tf with Infracost -> infracost.json
  5. destroy everything again

The outputs are committed, so the evaluation itself (evaluate.py) needs none of these tools.

Needs: LocalStack on localhost:4566, terraform, checkov, infracost (with an API key).

    python eval/generate.py                      # every case
    python eval/generate.py s3-bucket-deleted    # some cases
    python eval/generate.py --plugin-dir DIR     # use already-downloaded providers (offline)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

EVAL = Path(__file__).parent
CASES = EVAL / "cases"


def _run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    # Look the tool up on PATH first: on Windows some are .cmd/.bat wrappers that
    # subprocess can't start by bare name.
    program = shutil.which(args[0])
    if program is None:
        raise RuntimeError(f"{args[0]} is not installed (or not on PATH)")
    result = subprocess.run([program, *args[1:]], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def _init_once(plugin_dir: str | None) -> Path:
    """Run `terraform init` once and share its data directory with every case.

    Initialising per case would copy the AWS provider (hundreds of MB) every time.
    TF_DATA_DIR points every later terraform command at this one .terraform directory;
    each case's own state still lives in its own working directory.
    """
    shared = Path(tempfile.mkdtemp(prefix="tf-eval-init-"))
    shutil.copy(EVAL / "provider.tf", shared / "provider.tf")
    os.environ["TF_DATA_DIR"] = str(shared / ".terraform")
    init = ["terraform", "init", "-input=false"]
    if plugin_dir:
        init.append(f"-plugin-dir={plugin_dir}")
    _run(init, shared)
    return shared / ".terraform.lock.hcl"


def _terraform_dir(case: Path, tf_file: str, lock_file: Path) -> Path:
    work = Path(tempfile.mkdtemp(prefix=f"tf-eval-{case.name}-"))
    shutil.copy(EVAL / "provider.tf", work / "provider.tf")
    shutil.copy(lock_file, work / ".terraform.lock.hcl")
    if (case / tf_file).exists():
        shutil.copy(case / tf_file, work / "main.tf")
    return work


def _write_json(path: Path, data: object) -> None:
    # LF line endings on every OS, matching .gitattributes.
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")


def _trim_checkov(raw: str) -> dict:
    data = json.loads(raw)
    reports = data if isinstance(data, list) else [data]
    keep = ("check_id", "check_name", "resource", "resource_address", "guideline")
    failed = [
        {k: check.get(k) for k in keep}
        for report in reports
        for check in (report.get("results") or {}).get("failed_checks") or []
    ]
    summary = {"failed": len(failed)}
    return {"check_type": "terraform_plan", "results": {"failed_checks": failed}, "summary": summary}


def _trim_infracost(data: dict) -> dict:
    """Only the cost figures: the full output also holds paths and git metadata."""
    return {
        "currency": data.get("currency"),
        "pastTotalMonthlyCost": data.get("pastTotalMonthlyCost"),
        "totalMonthlyCost": data.get("totalMonthlyCost"),
        "diffTotalMonthlyCost": data.get("diffTotalMonthlyCost"),
        "projects": [
            {
                "diff": {
                    "resources": [
                        {
                            "name": r.get("name"),
                            "monthlyCost": r.get("monthlyCost"),
                            "costComponents": [
                                {"name": c.get("name"), "monthlyCost": c.get("monthlyCost")}
                                for c in r.get("costComponents") or []
                            ],
                        }
                        for r in (p.get("diff") or {}).get("resources") or []
                    ]
                }
            }
            for p in data.get("projects") or []
        ],
    }


def generate(case: Path, lock_file: Path) -> None:
    expected = yaml.safe_load((case / "expected.yml").read_text(encoding="utf-8"))
    work = _terraform_dir(case, "before.tf", lock_file)
    try:
        if (case / "before.tf").exists():
            _run(["terraform", "apply", "-input=false", "-auto-approve"], work)
            shutil.copy(case / "after.tf", work / "main.tf")
        else:
            shutil.copy(case / "after.tf", work / "main.tf")
        _run(["terraform", "plan", "-input=false", "-out=tfplan"], work)
        plan = _run(["terraform", "show", "-json", "tfplan"], work).stdout
        _write_json(case / "plan.json", json.loads(plan))
        (work / "plan.json").write_text(plan, encoding="utf-8")

        # Checkov exits 1 when checks fail; that's expected.
        checkov = _run(["checkov", "-f", "plan.json", "--framework", "terraform_plan", "-o", "json", "--quiet"], work, check=False)
        _write_json(case / "checkov.json", _trim_checkov(checkov.stdout))

        if expected.get("cost"):
            _price(case, work)
    finally:
        # Destroy what before.tf created (main.tf now holds after.tf, so restore before.tf).
        if (case / "before.tf").exists():
            shutil.copy(case / "before.tf", work / "main.tf")
            _run(["terraform", "destroy", "-input=false", "-auto-approve"], work, check=False)
        shutil.rmtree(work, ignore_errors=True)


def _price(case: Path, work: Path) -> None:
    before = Path(tempfile.mkdtemp(prefix=f"tf-eval-{case.name}-cost-before-"))
    after = Path(tempfile.mkdtemp(prefix=f"tf-eval-{case.name}-cost-after-"))
    try:
        for directory, tf_file in ((before, "before.tf"), (after, "after.tf")):
            shutil.copy(EVAL / "provider.tf", directory / "provider.tf")
            if (case / tf_file).exists():
                shutil.copy(case / tf_file, directory / "main.tf")
        baseline = before / "baseline.json"
        _run(["infracost", "breakdown", "--path", str(before), "--format", "json", "--out-file", str(baseline), "--no-color"], work)
        diff = after / "diff.json"
        _run(
            ["infracost", "diff", "--path", str(after), "--compare-to", str(baseline), "--format", "json",
             "--out-file", str(diff), "--no-color"],
            work,
        )
        data = json.loads(diff.read_text(encoding="utf-8"))
        _write_json(case / "infracost.json", _trim_infracost(data))
    finally:
        shutil.rmtree(before, ignore_errors=True)
        shutil.rmtree(after, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cases", nargs="*", help="case names (default: all)")
    parser.add_argument("--plugin-dir", help="directory of already-downloaded providers (terraform init -plugin-dir)")
    args = parser.parse_args()

    cases = [CASES / name for name in args.cases] if args.cases else sorted(p for p in CASES.iterdir() if p.is_dir())
    lock_file = _init_once(args.plugin_dir)
    failed = []
    for case in cases:
        print(f"{case.name} ...", end=" ", flush=True)
        try:
            generate(case, lock_file)
            print("ok")
        except Exception as e:  # report every broken case, then fail
            print("FAILED")
            print(f"  {e}", file=sys.stderr)
            failed.append(case.name)
    if failed:
        print(f"\n{len(failed)} case(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
