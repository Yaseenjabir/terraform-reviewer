resource "aws_dynamodb_table" "events" {
  name           = "eval-capacity"
  billing_mode   = "PROVISIONED"
  read_capacity  = 200
  write_capacity = 200
  hash_key       = "id"

  attribute {
    name = "id"
    type = "S"
  }
}
