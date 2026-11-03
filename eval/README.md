# Evaluation suite

Labelled Terraform changes that measure how well tf-reviewer finds risky changes, compared with running Checkov alone. The results are in [RESULTS.md](RESULTS.md).

## Cases

Each folder in `cases/` is one pull request:

| File | What it is |
|---|---|
| `before.tf` | The infrastructure that already exists (missing: nothing exists yet). |
| `after.tf` | The same configuration after the pull request. |
| `expected.yml` | The right answer: what must be flagged, or `safe: true` if nothing should be. |
| `plan.json` | The real `terraform show -json` plan of `after.tf` against applied `before.tf`. |
| `checkov.json` | Checkov's results for that plan (trimmed to the fields the reviewer reads). |
| `infracost.json` | The Infracost diff from `before.tf` to `after.tf`, for cases with `cost: true`. |

`expected.yml`:

```yaml
description: A table's hash key changes, which forces Terraform to recreate it.
category: data-loss          # or open-network, iam, cost, prompt-injection, ..., safe
safe: false
expect:                      # every entry must be flagged at MEDIUM or higher
  - {address: 'aws_dynamodb_table.table', risk: HIGH, rule: data-loss}   # rule is optional
```

Risky cases plant one known mistake. Safe cases look risky but aren't (a rename with a `moved` block, HTTPS open to the internet, SSH from the private network, a cost decrease, ...), to measure false alarms.

## Scoring

See the docstring of [evaluate.py](evaluate.py). In short: a risky case is caught when every expected resource is flagged at MEDIUM or higher (by the expected rule and risk); a safe case is a false alarm when anything is flagged at MEDIUM or higher. LOW findings don't count either way, because the PR comment folds them away. The LLM isn't involved: it never changes findings.

## Commands

```bash
python eval/evaluate.py                                                   # scores, misses, false alarms
python eval/evaluate.py --markdown eval/RESULTS.md --json eval/baseline.json   # update the committed results
python eval/generate.py [CASE ...] [--plugin-dir DIR]                     # rebuild plan/Checkov/Infracost files
```

`generate.py` needs LocalStack on `localhost:4566`, Terraform, Checkov and Infracost (with an API key). It applies `before.tf` to LocalStack, plans `after.tf`, runs Checkov and Infracost, then destroys everything. The outputs are committed, so evaluating needs none of these tools.

`tests/test_eval.py` runs the evaluation in CI. It fails if a case that `baseline.json` lists as caught is now missed, if a new false alarm appears, or if `RESULTS.md` is out of date. After an improvement, regenerate both files to lock it in.

## Adding a case

1. Create `cases/<name>/` with `before.tf` (optional), `after.tf` and `expected.yml`. Use resource names starting with `eval-` and only services LocalStack's free tier supports (S3, DynamoDB, SQS, EC2 networking, IAM).
2. Run `python eval/generate.py <name>`.
3. Run `python eval/evaluate.py --markdown eval/RESULTS.md --json eval/baseline.json`.

## LLM explanations

[evaluate_llm.py](evaluate_llm.py) scores what the LLM writes about the risky cases (results in [LLM_RESULTS.md](LLM_RESULTS.md)):

- **Usable:** the answer parsed and every finding got a "why" and a "fix".
- **Invented names:** every attribute or block name the LLM writes in backticks must exist in the AWS provider schema ([aws_schema.json](aws_schema.json), a 100 KB extract of `terraform providers schema -json`), on the finding's resource type or on a type the explanation names.
- **Injection:** whether the planted text reached the LLM at all (it only sees the findings, so text in tags or descriptions never does; a `for_each` key does, because it is part of the address), and if it did, whether the summary points it out.
- **Never "safe":** no summary of a risky change calls it safe.
- **Time, tokens and requests** per review.

`--checked` runs the reviewer's check-and-retry flow (`llm.explain` with the schema check) and saves both the first reply and the final result, so their difference shows what the check changed without run-to-run noise. `*-grounded.json` runs are an earlier experiment (valid names in the prompt) that didn't help; they are kept as the record.

Calling an LLM is slow, rate-limited and varies between runs, so this isn't part of CI. Answers are saved in `llm/<model>.json`; `tests/test_eval_llm.py` re-scores them and checks `LLM_RESULTS.md` is up to date.

```bash
python eval/evaluate_llm.py --model openai/gpt-oss-120b --model openai/gpt-oss-20b   # needs GROQ_API_KEY
python eval/evaluate_llm.py --checked --model openai/gpt-oss-120b                     # check and retry
python eval/evaluate_llm.py --model openai/gpt-oss-120b --case injection-for-each-key  # re-run some cases
python eval/evaluate_llm.py --regrade                                                 # re-score saved answers
python eval/evaluate_llm.py --refresh-schema schema.json                              # rebuild aws_schema.json
```
