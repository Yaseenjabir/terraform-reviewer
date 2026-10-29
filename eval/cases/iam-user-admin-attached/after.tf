resource "aws_iam_user" "ci" {
  name = "eval-ci"
}

resource "aws_iam_user_policy_attachment" "admin" {
  user       = aws_iam_user.ci.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
