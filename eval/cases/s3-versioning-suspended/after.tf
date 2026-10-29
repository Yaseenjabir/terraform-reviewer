resource "aws_s3_bucket" "data" {
  bucket = "eval-versioning"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Suspended"
  }
}
