resource "aws_dynamodb_table" "orders" {
  name         = "eval-moved"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}
