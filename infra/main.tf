resource "local_file" "hello" {
  filename = "${path.module}/hello.txt"
  content  = "Hello from CI"
}
