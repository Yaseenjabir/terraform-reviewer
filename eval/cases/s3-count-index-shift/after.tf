variable "names" {
  default = ["images", "backups"]
}

resource "aws_s3_bucket" "b" {
  count  = length(var.names)
  bucket = "eval-shift-${var.names[count.index]}"
}
