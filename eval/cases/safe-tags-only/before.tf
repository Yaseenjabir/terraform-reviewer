resource "aws_s3_bucket" "data" {
  bucket = "eval-tags"

  tags = {
    team = "data"
  }
}
