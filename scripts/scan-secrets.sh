#!/bin/sh
set -eu
cd "$(git rev-parse --show-toplevel)"
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "Gitleaks is required. See SECRET-PROTECTION.md." >&2
  exit 1
fi
required=$(cat .gitleaks-version)
if [ "$(gitleaks version)" != "$required" ]; then
  echo "Install Gitleaks $required; see SECRET-PROTECTION.md." >&2
  exit 1
fi
case "${1:-history}" in
  staged)
    exec gitleaks git --pre-commit --staged --redact=100 --no-banner --ignore-gitleaks-allow --config .gitleaks.toml . ;;
  history)
    exec gitleaks git --log-opts=--all --redact=100 --no-banner --ignore-gitleaks-allow --config .gitleaks.toml . ;;
  push)
    while read -r local_ref local_oid remote_ref remote_oid; do
      case "$local_oid" in *[!0]*) ;; *) continue ;; esac
      # Scan every reachable commit, including secrets removed in a later commit.
      git cat-file -e "$local_oid^{commit}" || exit 1
      gitleaks git --log-opts="$local_oid" --redact=100 --no-banner --ignore-gitleaks-allow --config .gitleaks.toml .
    done ;;
  *) echo "Usage: $0 staged|history|push" >&2; exit 2 ;;
esac
