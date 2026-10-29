resource "aws_dynamodb_table" "customer_orders" {
  name         = "eval-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}
