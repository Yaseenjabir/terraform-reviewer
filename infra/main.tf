resource "local_file" "hello" {
  filename = "${path.module}/hello.txt"
  content  = "Hello from CI"
}

resource "local_file" "goodbye" {
  filename = "${path.module}/goodbye.txt"
  content  = "Hello from goodbye action"
}

resource "local_file" "notes" {
  filename = "${path.module}/notes.txt"
  content  = "My first pull request"
}
