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
