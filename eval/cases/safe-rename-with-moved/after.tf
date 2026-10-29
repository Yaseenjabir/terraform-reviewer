resource "aws_dynamodb_table" "customer_orders" {
  name         = "eval-moved"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

moved {
  from = aws_dynamodb_table.orders
  to   = aws_dynamodb_table.customer_orders
}
