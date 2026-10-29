resource "aws_sqs_queue" "jobs" {
  name                       = "eval-visibility"
  visibility_timeout_seconds = 60
}
