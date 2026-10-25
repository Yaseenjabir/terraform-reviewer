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
