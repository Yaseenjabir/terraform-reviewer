resource "aws_iam_policy" "deploy" {
  name = "eval-deploy"
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "s3:*", Resource = "*" }]
  })
}
