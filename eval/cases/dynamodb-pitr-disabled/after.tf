resource "aws_dynamodb_table" "table" {
  name         = "eval-pitr"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}
