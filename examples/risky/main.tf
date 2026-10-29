# A deliberately risky change, planned on every pull request by
# .github/workflows/review-example.yml to show the reviewer working end to end.
#
# Nothing is ever applied. The fake credentials and skip_* settings let `terraform plan`
# run without an AWS account: with -refresh=false and no data sources, planning new
# resources never calls AWS.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "example"
  secret_key                  = "example"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
}

resource "aws_security_group" "admin" {
  name        = "admin-access"
  description = "Remote administration"

  ingress {
    description = "SSH from anywhere"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_policy" "deploy" {
  name = "deploy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "*"
      Resource = "*"
    }]
  })
}

resource "aws_iam_role" "ci" {
  name = "ci"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "ci_admin" {
  role       = aws_iam_role.ci.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
