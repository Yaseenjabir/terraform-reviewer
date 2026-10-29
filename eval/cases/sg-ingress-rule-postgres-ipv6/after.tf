resource "aws_vpc" "main" {
  cidr_block                       = "10.0.0.0/16"
  assign_generated_ipv6_cidr_block = true
}

resource "aws_security_group" "app" {
  name   = "app"
  vpc_id = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "db" {
  security_group_id = aws_security_group.app.id
  cidr_ipv6         = "::/0"
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
}
