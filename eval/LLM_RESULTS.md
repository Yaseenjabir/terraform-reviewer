# LLM explanation results

The LLM's explanations for the 24 risky evaluation cases, scored automatically (see `eval/evaluate_llm.py`). The rules decide the findings; these numbers are only about what the LLM writes. Answers vary between runs: each column is one run, saved in `eval/llm/`.

## Check and retry (what the reviewer does)

With `--provider-schema`, the reviewer checks every fix against the provider schema. A fix naming an attribute that doesn't exist is sent back once to be rewritten; if the rewrite still fails, the fix is dropped and only the "why" is shown. "First answer" scores the first reply of the same run, so the two columns differ only by the check. After the check, invented names are 0 by construction (the reviewer and this grader use the same schema check); what matters is how many bad fixes the rewrite repaired and how many had to be dropped. Rewrites were also read by hand: of 4 rewrites, 3 were correct (e.g. `versioning { enabled = true }` became `status = "Enabled"` on `aws_s3_bucket_versioning`) and 1 used valid names but added unrelated tagging advice.

| | `openai/gpt-oss-120b` first answer | `openai/gpt-oss-120b` after check | `openai/gpt-oss-20b` first answer | `openai/gpt-oss-20b` after check |
|---|---|---|---|---|
| Usable answers (every finding explained) | 24/24 | 24/24 | 23/24 | 23/24 |
| Answers naming an attribute that doesn't exist | 2/24 | 0/24 | 4/24 | 0/24 |
| Reviews that needed a rewrite (second request) | - | 2/24 | - | 4/24 |
| Fixes dropped (still wrong after the rewrite) | - | 1 | - | 1 |
| Injection attempts whose text reached the LLM | 1/3 | 1/3 | 1/3 | 1/3 |
| ...and were pointed out in the summary | 1/1 | 1/1 | 0/1 | 0/1 |
| Summaries calling a risky change safe | 0/24 | 0/24 | 0/24 | 0/24 |
| Median seconds per review | - | 2.0 | - | 1.8 |
| Slowest review (seconds) | - | 3.4 | - | 5.9 |
| Average tokens in / out | - | 800 / 484 | - | 869 / 840 |

Cost per review is tokens in x input price + tokens out x output price; output includes the model's reasoning tokens, and checked runs include the second request when there was one. The development runs used Groq's free tier.

## Earlier experiment: valid names in the prompt

Sending the valid attribute names of the findings' resource types along with the findings didn't reduce invented names (the model still used names missing from the list), cost about 13% more input and 30% more output tokens, and the difference was within run-to-run noise (the same plain setup gave 4/24 and 2/24 on two runs of `gpt-oss-20b`). So the reviewer checks answers instead.

| | `openai/gpt-oss-120b` | `openai/gpt-oss-120b` + names in prompt | `openai/gpt-oss-20b` | `openai/gpt-oss-20b` + names in prompt |
|---|---|---|---|---|
| Usable answers (every finding explained) | 24/24 | 24/24 | 23/24 | 24/24 |
| Answers naming an attribute that doesn't exist | 4/24 | 3/24 | 2/24 | 2/24 |
| Reviews that needed a rewrite (second request) | - | - | - | - |
| Fixes dropped (still wrong after the rewrite) | - | - | - | - |
| Injection attempts whose text reached the LLM | 1/3 | 1/3 | 1/3 | 1/3 |
| ...and were pointed out in the summary | 1/1 | 1/1 | 1/1 | 1/1 |
| Summaries calling a risky change safe | 0/24 | 0/24 | 0/24 | 0/24 |
| Median seconds per review | 1.8 | 2.1 | 1.9 | 1.6 |
| Slowest review (seconds) | 2.8 | 5.2 | 3.2 | 3.4 |
| Average tokens in / out | 732 / 450 | 826 / 598 | 732 / 597 | 826 / 713 |

## Invented names

- `openai/gpt-oss-120b` first answer: s3-versioning-suspended: `enabled`, `versioning`; sg-ingress-rule-postgres-ipv6: `cidr_blocks`.
- `openai/gpt-oss-120b` after check: none.
- `openai/gpt-oss-20b` first answer: dynamodb-forced-replacement: `stream_specification`; s3-versioning-suspended: `enabled`; sg-custom-port-world: `source_security_group_id`; sg-ingress-rule-postgres-ipv6: `source_security_group_id`.
- `openai/gpt-oss-20b` after check: none.
- `openai/gpt-oss-120b`: dynamodb-forced-replacement: `encrypt_at_rest`; iam-policy-star: `resources`; s3-versioning-suspended: `enabled`, `versioning`; sg-ingress-rule-postgres-ipv6: `cidr_blocks`.
- `openai/gpt-oss-120b` + names in prompt: iam-user-admin-attached: `aws_iam_user_mfa_device`; sg-custom-port-world: `source_security_group_id`; sg-ssh-widened: `source_security_group_id`.
- `openai/gpt-oss-20b`: dynamodb-forced-replacement: `sse_type`; injection-sg-description: `source_security_group_id`.
- `openai/gpt-oss-20b` + names in prompt: dynamodb-renamed-without-moved: `sse_type`; sg-custom-port-world: `security_group_rule`.

The check is strict: a name counts only if the AWS provider schema has it on the finding's resource type or on a resource type the explanation names. Every name listed was checked by hand.
