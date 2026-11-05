#!/bin/sh
set -eu
cd "$(git rev-parse --show-toplevel)"
current=$(git config --get core.hooksPath || true)
if [ -n "$current" ] && [ "$current" != .githooks ]; then
  echo "Existing hooksPath detected; integrate existing hooks before installation." >&2
  exit 1
fi
if [ -z "$current" ]; then
  for hook in pre-commit pre-push; do
    if [ -f "$(git rev-parse --git-path "hooks/$hook")" ]; then
      echo "Existing $hook detected; integrate it before installation." >&2
      exit 1
    fi
  done
fi
chmod +x .githooks/pre-commit .githooks/pre-push scripts/scan-secrets.sh
git config --local core.hooksPath .githooks
echo "Installed pre-commit and pre-push secret checks."
