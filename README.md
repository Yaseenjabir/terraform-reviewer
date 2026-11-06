# Terraform PR Reviewer

[![Tests](https://github.com/Yaseenjabir/terraform-reviewer/actions/workflows/tests.yml/badge.svg)](https://github.com/Yaseenjabir/terraform-reviewer/actions/workflows/tests.yml)

A GitHub Action that reviews Terraform pull requests. It reads the `terraform plan`, finds risky changes, and posts one pull request comment that explains them in plain English. High-risk changes fail the check.

Part of a real comment from this repository's own end-to-end test (shortened):

> ### 🔴 Terraform review: HIGH risk
>
> **Plan:** 5 to create<br>
> Monthly cost: $31.17 -> $122.27 (+$91.10, +292%)
>
> > **AI summary:** The pull request introduces several high‑severity security risks: an IAM policy with unrestricted actions, a role granted full AdministratorAccess, and a security group that allows SSH from anywhere. Additionally, it raises monthly compute costs sharply. Review the necessity of these permissions and the SSH exposure, and verify the instance size change before merging.
>
> - 🔴 **HIGH** `open-to-internet`: aws_security_group.admin opens SSH (port 22) to the whole internet (0.0.0.0/0).
>   - **Why:** Opening port 22 to 0.0.0.0/0 allows anyone on the internet to attempt SSH connections, exposing the instance to brute‑force attacks and possible compromise.
>   - **Fix:** Restrict the inbound SSH rule to known IP ranges (e.g., your corporate CIDR) or use a bastion host; alternatively, remove the rule and rely on VPN access.
> - 🟠 **MEDIUM** `cost-increase`: Monthly cost rises by $91.10 (+292%, $31.17 -> $122.27). Biggest increase: aws_instance.app +$91.10 (Instance usage (Linux/UNIX, on-demand, t3.medium → t3.xlarge)).
>   - **Why:** Upgrading the instance from t3.medium to t3.xlarge increases hourly costs dramatically, which may be unnecessary and inflate the budget.
>   - **Fix:** Confirm that the larger instance size is required; if not, revert to the previous size or consider a cost‑optimized option such as a smaller instance, spot pricing, or autoscaling.
>
> <sub>Risk levels come from tf-reviewer's rules, not from AI. Explanations written by openai/gpt-oss-120b via openai-compatible: check them before acting.</sub>

## How it works

**Rules find the problems; an LLM only explains them.** Deterministic rules read the plan JSON and decide what is risky and how risky. The LLM receives the findings (never the plan) and writes a summary, a "why" and a "fix" for each one. It can't add, remove or re-rate a finding, so the verdict is reproducible and can't be argued away by text hidden in the Terraform code.

```
terraform plan ─► plan JSON ─┐
Infracost diff (optional) ───┼─► rules ─► findings ─► redact secrets ─► LLM (optional) ─► PR comment
Checkov results (optional) ──┘              │                                              + job summary
                                            └──────────── risk level ──────────────────► pass / fail check
```

What the rules check:

| Rule | Risk | Catches |
|---|---|---|
| `data-loss` | HIGH | A database, bucket, queue or other stateful resource is deleted or replaced: forced replacements, renames without a `moved` block, `count` index shifts |
| `resource-removed` | MEDIUM | Any other resource is deleted or replaced |
| `open-to-internet` | HIGH / MEDIUM | SSH, RDP, databases or all ports open to `0.0.0.0/0` or `::/0`, in all three security group styles |
| `iam-wildcard` | HIGH / MEDIUM | IAM policies allowing `*`, `iam:*` or `service:*` |
| `admin-policy-attached` | HIGH / MEDIUM | `AdministratorAccess`, `IAMFullAccess` or `PowerUserAccess` attached |
| `protection-disabled` | MEDIUM | Backups, versioning or deletion protection turned off |
| `cost-increase` | MEDIUM | Monthly cost rises by more than a threshold (with [Infracost](https://www.infracost.io/)) |
| `checkov` | depends on the check | Important [Checkov](https://www.checkov.io/) failures on the changed resources; the rest are folded into one LOW line |

## Results

Measured on an [evaluation suite](eval/) of 38 labelled Terraform changes (24 risky, 14 that look risky but are safe). Each is a real plan, made by applying the "before" configuration to [LocalStack](https://www.localstack.cloud/) and planning the "after".

| | tf-reviewer | Checkov alone |
|---|---|---|
| Risky changes caught | **24/24** | 13/24 |
| Safe changes with a false alarm | **0/14** | 11/14 |

Checkov only reads the planned end state, so it can't see deletions or replacements. The cases were written alongside the rules, so this shows the rules do what they were designed to do, not that they catch every mistake in real projects. Details: [eval/RESULTS.md](eval/RESULTS.md).

The LLM's explanations are evaluated too ([eval/LLM_RESULTS.md](eval/LLM_RESULTS.md)), with Groq's `openai/gpt-oss-120b` and `openai/gpt-oss-20b`:

- About 2 seconds and roughly 800 input and 500–850 output tokens per review.
- First answers named a Terraform attribute that doesn't exist in 2–4 of 24 reviews (for example `versioning { enabled = true }`, removed from the AWS provider). Listing the valid names in the prompt didn't help, so the reviewer checks every fix against the provider schema instead and asks for one rewrite; a fix that is still wrong is dropped.
- No summary called a risky change safe. Text hidden in tags or descriptions never reaches the LLM. Text in a `for_each` key does (it's part of the address), and the models pointed it out in 5 of 6 saved runs. The risk level comes from the rules either way.

## Usage

The reviewer needs the plan as JSON. Run it after `terraform plan` in your pull request workflow:

```yaml
name: Terraform
on:
  pull_request:

permissions:
  contents: read
  pull-requests: write # to comment

jobs:
  plan:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_wrapper: false

      # Configure your cloud credentials and remote state as for any CI plan.
      - name: Plan
        run: |
          terraform init -input=false
          terraform plan -input=false -out=tfplan
          terraform show -json tfplan > plan.json
          terraform providers schema -json > "$RUNNER_TEMP/provider-schema.json"

      - uses: Yaseenjabir/terraform-reviewer@v1
        with:
          plan-json: plan.json
          provider-schema: ${{ runner.temp }}/provider-schema.json # optional: checks the AI's fixes
        env:
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }} # optional: AI explanations, see Configuration
```

The comment is updated on every push instead of adding a new one, and the review is also written to the job summary.

To include cost, run Infracost (0.10.x) and pass its diff as `cost-diff`. To include Checkov, pass the output of `checkov -f plan.json --framework terraform_plan -o json` as `checkov`. [.github/workflows/review-example.yml](.github/workflows/review-example.yml) shows a full setup: it is this repository's own end-to-end test, run on every pull request.

### Inputs

| Input | Default | Description |
|---|---|---|
| `plan-json` | (required) | Plan JSON from `terraform show -json tfplan` |
| `config` | `.tf-reviewer.yml`, if present | Settings file |
| `cost-diff` | | Infracost diff JSON |
| `checkov` | | Checkov JSON output for the same plan |
| `provider-schema` | | `terraform providers schema -json` output, to check the AI's fixes |
| `fail-on` | `high` | Fail the check at this risk or above: `high`, `medium`, `low` or `never` |
| `comment` | `true` | Post the review as a pull request comment |
| `comment-author` | `github-actions[bot]` | Who posts the comment (change it only with a custom token) |
| `github-token` | `github.token` | Token used to comment; needs `pull-requests: write` |
| `working-directory` | `.` | Directory the paths are relative to |
| `python-version` | `3.13` | Python used to run the reviewer |

Outputs: `risk` (`none`, `low`, `medium` or `high`) and `findings` (a count).

## Configuration

Everything is optional. Put a `.tf-reviewer.yml` in the working directory; [.tf-reviewer.example.yml](.tf-reviewer.example.yml) documents every setting.

```yaml
cost:
  threshold: 100                   # report monthly increases of at least this much
rules:
  disable: [resource-removed]
stateful_types:
  add: [aws_memorydb_cluster]      # deleting these is data loss
sensitive_ports:
  add: {8080: Jenkins}             # never open to the internet
llm:
  backend: openai-compatible       # or anthropic, or none (the default)
  base_url: https://api.groq.com/openai/v1
  model: openai/gpt-oss-120b
  api_key_env: GROQ_API_KEY        # the name of the variable, never the key itself
```

LLM backends:

- `none` (default): rules only; nothing leaves the runner.
- `openai-compatible`: any server speaking the OpenAI chat API, such as Groq, OpenAI, Ollama, vLLM or an internal endpoint. Development used Groq's free tier. GitHub-hosted runners have no GPU, so for a local model use a self-hosted runner or an internal URL.
- `anthropic`: the Claude API (default model `claude-opus-5-5`, key in `ANTHROPIC_API_KEY`). Implemented, but not yet tested against the live API.

If the LLM fails (network, rate limit, unusable answer, missing key), the review continues without explanations.

## Security

- **No hosted service.** Everything runs in your own CI: the plan, state and credentials never leave the runner. Only the findings go to the LLM, and only if you configure one.
- **Secrets.** The plan JSON holds values such as database passwords in plain text. Values Terraform marks as sensitive are masked as soon as the plan is read, and every finding, resource address and LLM answer passes through a redaction step for keys, tokens and passwords before it is sent or posted.
- **Prompt injection.** The LLM can't change the verdict, sees only the findings (not tags or descriptions), and is told to treat all input as data. Comment text is escaped, so it can't mention people, add links or images, or inject HTML.
- **Pull requests from forks** get a read-only token and no secrets. The review still runs (rules only) and is written to the job summary; commenting fails with a warning. Don't work around this with `pull_request_target`: `terraform plan` runs code from the pull request.
- **The action itself** passes its inputs to scripts through environment variables, never by pasting them into shell code.

## Limitations

- AWS only, so far: the rules know AWS resource types.
- CI needs access to your real state (a remote backend). Without it, every resource looks new and deletions can't be seen.
- Explanations come from an LLM and can still be wrong; the comment says so. The verdict never depends on them.
- The evaluation cases were written by the same author as the rules; results on real-world pull requests may differ.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"          # .venv/Scripts/ on Windows
.venv/bin/python -m pytest -q              # tests, including the evaluation regression check
.venv/bin/tf-reviewer plan.json            # review a plan locally (--help for options)
python eval/evaluate.py                    # detection results
```

See [eval/README.md](eval/README.md) for how the evaluation cases are generated and scored.

## License

[MIT](LICENSE)
