# Test fixtures

Real plan JSON files produced by Terraform 1.16.5 with the built-in `terraform_data` resource, so they need no cloud account or provider.

## `mixed.json`

One plan containing every kind of change. First this configuration was applied:

```hcl
resource "terraform_data" "keep" { input = "same" }
resource "terraform_data" "update" { input = "old" }
resource "terraform_data" "replace" { triggers_replace = "v1" }
resource "terraform_data" "replace_first" {
  triggers_replace = "v1"
  lifecycle { create_before_destroy = true }
}
resource "terraform_data" "remove" { input = "bye" }
resource "terraform_data" "old_name" { input = "renamed" }
```

Then it was changed to the following, and planned with `terraform plan -out=tfplan` and `terraform show -json tfplan`:

```hcl
resource "terraform_data" "keep" { input = "same" }
resource "terraform_data" "update" { input = "new" }
resource "terraform_data" "replace" { triggers_replace = "v2" }
resource "terraform_data" "replace_first" {
  triggers_replace = "v2"
  lifecycle { create_before_destroy = true }
}
resource "terraform_data" "new_name" { input = "renamed" }
moved {
  from = terraform_data.old_name
  to   = terraform_data.new_name
}
resource "terraform_data" "add" { input = "hello" }
```

| Address | Actions | Why |
| --- | --- | --- |
| `terraform_data.add` | `create` | new resource |
| `terraform_data.keep` | `no-op` | unchanged |
| `terraform_data.new_name` | `no-op` | renamed by `moved`, `previous_address` is set |
| `terraform_data.remove` | `delete` | removed from the configuration |
| `terraform_data.replace` | `delete`, `create` | `triggers_replace` changed |
| `terraform_data.replace_first` | `create`, `delete` | same, with `create_before_destroy` |
| `terraform_data.update` | `update` | `input` changed |

## `no_changes.json`

The plan after applying `mixed.json`: every resource is `no-op`.

## `open_ports.json`

A create-only AWS plan with inbound rules in all three Terraform styles. It was made offline with `terraform plan -refresh=false` (fake credentials, no AWS account), using hashicorp/aws v6.69.1:

| Address | Style | Opens to the internet |
| --- | --- | --- |
| `aws_security_group.web` | inline `ingress` blocks | 443 (fine) and 22 (SSH) |
| `aws_security_group_rule.rdp` | legacy separate rule, `type = "ingress"` | 3389 (RDP) |
| `aws_vpc_security_group_ingress_rule.postgres` | newer separate rule, `cidr_ipv4` | 5432 (PostgreSQL) |
| `aws_vpc_security_group_ingress_rule.internal_ssh` | newer separate rule | nothing (22 from `10.0.0.0/16` only) |
| `aws_vpc_security_group_ingress_rule.everything_ipv6` | newer separate rule, `ip_protocol = "-1"`, `cidr_ipv6` | all ports (`::/0`) |

## `iam_wildcards.json`

A create-only AWS plan with IAM policies, made offline the same way as `open_ports.json`. Policies are stored as JSON strings inside the plan JSON.

| Address | Grants | Expected |
| --- | --- | --- |
| `aws_iam_policy.admin` | `"*"` on `"*"` | HIGH |
| `aws_iam_role_policy.iam_admin` | `"iam:*"` on `"*"` | HIGH |
| `aws_iam_policy.s3_everything` | `"s3:*"` on `"*"` | MEDIUM |
| `aws_iam_role.app` | inline policy block, `"ec2:*"` on `"*"` | MEDIUM |
| `aws_iam_policy.read_one_bucket` | `s3:GetObject` on one bucket | nothing |
| `aws_iam_policy.deny_everything` | `Deny` `"*"` | nothing |

## `iam_attachments.json`

A create-only AWS plan attaching managed policies, made offline the same way.

| Address | Attaches | Expected |
| --- | --- | --- |
| `aws_iam_role_policy_attachment.admin` | `arn:aws:iam::aws:policy/AdministratorAccess` | HIGH |
| `aws_iam_policy_attachment.admins` | `AdministratorAccess` (to a list of roles) | HIGH |
| `aws_iam_user_policy_attachment.iam_full` | `IAMFullAccess` | HIGH |
| `aws_iam_group_policy_attachment.power_user` | `PowerUserAccess` | MEDIUM |
| `aws_iam_role_policy_attachment.read_only` | `ReadOnlyAccess` | nothing |
| `aws_iam_role_policy_attachment.own_policy` | a customer policy that happens to be named `AdministratorAccess` | nothing |

## `infracost_diff.json`

A real Infracost diff, made with Infracost v0.10.47 from a configuration with one `aws_instance` and one `aws_db_instance` (never applied). A baseline was saved with the instance as `t3.medium`, then the instance was changed to `t3.xlarge`:

```bash
infracost breakdown --path . --format json --out-file baseline.json
# change instance_type to "t3.xlarge"
infracost diff --path . --compare-to baseline.json --format json --out-file infracost_diff.json
```

Monthly cost goes from $48.808 to $139.912 (+$91.104). Infracost writes amounts as strings. In CI its `metadata` includes the commit author's name and email; this file was made outside git, so those fields are empty.

## `checkov_plan.json` and `checkov.json`

A create-only AWS plan with common mistakes (made offline like the others), and Checkov 3.3.30's scan of it:

```bash
checkov -f checkov_plan.json --framework terraform_plan -o json
```

The plan has an S3 bucket with every public-access block turned off, a public and unencrypted `aws_db_instance`, and a security group with SSH open to `0.0.0.0/0`. Checkov reported 23 failed checks on these 4 resources.

`checkov.json` is **trimmed** to the fields the reviewer reads (`check_id`, `check_name`, `resource`, `resource_address`, `guideline`) plus the summary. Checkov's full output also contains absolute file paths and code blocks, which were removed.

Scanning the other fixtures with Checkov showed which of its checks repeat the reviewer's own rules (listed in `COVERED_BY_OWN_RULES`), and that Checkov misses several things the reviewer's rules catch on the same plans: RDP opened with `aws_security_group_rule`, PostgreSQL and all-ports-over-IPv6 openings with `aws_vpc_security_group_ingress_rule`, an `ec2:*` inline role policy, and an attached `PowerUserAccess`.

## `provider_schema.json`

A small hand-written file in the format of `terraform providers schema -json`, with two resource types. `aws_security_group` declares `ingress` as an attribute of object type (as the real AWS provider does) and has a nested `timeouts` block, so the tests cover both ways a schema can hold attribute names. The real AWS schema is about 20 MB.
