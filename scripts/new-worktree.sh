#!/usr/bin/env bash
# Create a git worktree + branch for devlog-cli parallel development
# Usage: ./scripts/new-worktree.sh <branch-slug> [base-branch]
# Example: ./scripts/new-worktree.sh storage-refactor main
#   Creates ../devlog-cli-storage-refactor with branch 'storage-refactor' from 'main'

set -euo pipefail

BRANCH_SLUG="${1:-}"
BASE_BRANCH="${2:-main}"

if [[ -z "$BRANCH_SLUG" ]]; then
  echo "Usage: new-worktree.sh <branch-slug> [base-branch]"
  echo "Example: new-worktree.sh storage-refactor main"
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
WORKTREE_DIR="$(dirname "$REPO_ROOT")/devlog-cli-$BRANCH_SLUG"
BRANCH_NAME="$BRANCH_SLUG"

if [[ -d "$WORKTREE_DIR" ]]; then
  echo "Worktree already exists: $WORKTREE_DIR"
  exit 1
fi

if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
  echo "Branch '$BRANCH_NAME' already exists locally."
  echo "Options:"
  echo "  1. Remove worktree if stale: git worktree remove $WORKTREE_DIR"
  echo "  2. Use a different slug"
  exit 1
fi

if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH_NAME"; then
  echo "Remote branch 'origin/$BRANCH_NAME' exists. Will track it."
  git worktree add "$WORKTREE_DIR" -b "$BRANCH_NAME" "origin/$BRANCH_NAME"
else
  echo "Creating new branch '$BRANCH_NAME' from '$BASE_BRANCH'."
  git worktree add "$WORKTREE_DIR" -b "$BRANCH_NAME" "$BASE_BRANCH"
fi

echo "Created: $WORKTREE_DIR (branch: $BRANCH_NAME)"
echo ""
echo "To start working:"
echo "  cd $WORKTREE_DIR"
echo "  devlog-cli    # or: hermes --profile devlog-cli"
echo ""
echo "To remove when done:"
echo "  git worktree remove $WORKTREE_DIR"
echo "  git branch -d $BRANCH_NAME  # optional"