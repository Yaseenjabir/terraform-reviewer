# Terraform PR Reviewer

> Work in progress.

A GitHub Action that reviews Terraform pull requests. It reads the `terraform plan`, finds risky changes (deleted or replaced resources, security problems, cost increases), and posts one pull request comment explaining them in plain English, with a failing check for high-risk changes.

Risky changes are detected by deterministic rules. An LLM (Claude or any OpenAI-compatible provider, or none at all) only explains what the rules found.
