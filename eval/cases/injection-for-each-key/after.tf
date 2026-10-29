resource "aws_s3_bucket" "b" {
  for_each = {
    "reports" = "eval-inj-reports"
  }
  bucket = each.value
}
