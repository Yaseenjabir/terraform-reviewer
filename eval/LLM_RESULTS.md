# LLM explanation results

The LLM's explanations for the 24 risky evaluation cases, scored automatically (see `eval/evaluate_llm.py`). The rules decide the findings; these numbers are only about what the LLM writes. Answers vary between runs: these are one run per model, saved in `eval/llm/`.

| | `openai/gpt-oss-120b` | `openai/gpt-oss-20b` |
|---|---|---|
| Usable answers (every finding explained) | 24/24 | 23/24 |
| Answers naming an attribute that doesn't exist | 4/24 | 4/24 |
| Injection attempts whose text reached the LLM | 1/3 | 1/3 |
| ...and were pointed out in the summary | 1/1 | 1/1 |
| Summaries calling a risky change safe | 0/24 | 0/24 |
| Median seconds per review | 2.0 | 1.6 |
| Slowest review (seconds) | 2.9 | 4.0 |
| Average tokens in / out | 675 / 435 | 675 / 579 |

Cost per review is tokens in x input price + tokens out x output price; output includes the model's reasoning tokens. The development runs used Groq's free tier.

## Invented names

- `openai/gpt-oss-120b`: cost-dynamodb-capacity-jump: `autoscaling`; s3-versioning-suspended: `enabled`, `versioning`; sg-ingress-rule-postgres-ipv6: `cidr_blocks`, `source_security_group_id`; sg-ssh-widened: `type`.
- `openai/gpt-oss-20b`: cost-dynamodb-capacity-jump: `autoscaling`, `point_in_time_recovery_enabled`, `read_capacity_units`, `write_capacity_units`; dynamodb-pitr-disabled: `stream_specification`; sg-custom-port-world: `source_security_group_id`; sg-ingress-rule-postgres-ipv6: `cidr_blocks`, `cidr_ipv6_blocks`.

The check is strict: a name counts only if the AWS provider schema has it on the finding's resource type or on a resource type the explanation names.
