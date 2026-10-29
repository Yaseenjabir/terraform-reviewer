resource "aws_dynamodb_table" "table" {
  name         = "eval-replace"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "order_id"

  attribute {
    name = "order_id"
    type = "S"
  }
}
