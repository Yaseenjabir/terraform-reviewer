#!/usr/bin/env bash
# Post the review as a pull request comment, or update the one posted before.
#
# Usage: comment.sh COMMENT_FILE
# Needs GH_TOKEN, REPO (owner/name), PR_NUMBER and AUTHOR (the login that posts the comment).
#
# Our comment is found by its hidden marker *and* its author: anyone can write a comment that
# starts with the marker, and we must not try to edit theirs. Commenting can fail for pull
# requests from forks, which get a read-only token; that's a warning, not a failed review.
set -euo pipefail

file="$1"
marker="<!-- tf-reviewer -->"

if ! [[ "$AUTHOR" =~ ^[A-Za-z0-9-]+(\[bot\])?$ ]]; then
  echo "::error::comment-author '$AUTHOR' is not a GitHub login"
  exit 1
fi

# The comment body as a JSON request, so any character in the Markdown is sent as is.
request="$(python -c 'import json, sys; print(json.dumps({"body": open(sys.argv[1], encoding="utf-8").read()}))' "$file")"

existing="$(gh api --paginate "repos/$REPO/issues/$PR_NUMBER/comments" \
  --jq ".[] | select(.user.login == \"$AUTHOR\" and (.body | startswith(\"$marker\"))) | .id" | tail -n 1)" || {
  echo "::warning::Couldn't read the pull request comments; the review is in the job summary."
  exit 0
}

if [ -n "$existing" ]; then
  if echo "$request" | gh api --method PATCH "repos/$REPO/issues/comments/$existing" --input - > /dev/null; then
    echo "Updated the review comment ($existing)."
    exit 0
  fi
elif echo "$request" | gh api --method POST "repos/$REPO/issues/$PR_NUMBER/comments" --input - > /dev/null; then
  echo "Posted the review comment."
  exit 0
fi

echo "::warning::Couldn't comment on the pull request. Pull requests from forks get a read-only token," \
  "and the job needs 'permissions: pull-requests: write'. The review is in the job summary."
