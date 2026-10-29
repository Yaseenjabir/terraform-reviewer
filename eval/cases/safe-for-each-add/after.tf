resource "aws_s3_bucket" "b" {
  for_each = toset(["logs", "images", "backups"])
  bucket   = "eval-foreach-${each.key}"
}
