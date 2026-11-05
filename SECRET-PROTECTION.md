# Secret protection

GitHub secret scanning and push protection are enabled for the SysKata
organization repositories. They supplement the local and CI checks below.

## Local setup

Install the version in `.gitleaks-version`, then enable the versioned Git hooks:

```sh
go install -ldflags="-X github.com/zricethezav/gitleaks/v8/cmd.Version=8.27.2" github.com/zricethezav/gitleaks/v8@v8.27.2
export PATH="$(go env GOPATH)/bin:$PATH"
sh scripts/install-hooks.sh
sh scripts/scan-secrets.sh history
```

Repeat hook installation for each fresh clone. The scanner must be available in
the PATH used by your terminal or Git GUI. Missing or mismatched scanners block
commits and pushes. The installer refuses to replace existing custom hooks.

Pre-commit scans the staged diff. Pre-push scans the complete reachable history
of every pushed branch/tag pointing to a commit, including newly created branches
and secrets subsequently removed. Deletion-only pushes need no content scan.
Non-commit tags are blocked for manual inspection. CI scans all fetched history.
Findings are redacted; do not paste credentials or unredacted reports into issues.

## Handling findings

Remove secrets from staged content and use environment variables or a secret
manager. Commit placeholder-only examples. If a real secret was committed,
revoke/rotate it first and coordinate removal from history before pushing.
Deleting it in a later commit does not remove it from history.

Do not bypass hooks, use inline allow comments, or add broad exclusions to make
a scan pass. Document and review a narrowly scoped false-positive exception.
Keep tool pins and configuration aligned across repositories. The scanner uses
Gitleaks' default rules; no project-wide content exclusions are configured.

Git ignore rules reduce accidental staging; they do not protect already tracked
files or forced adds. Hooks can be bypassed, and scanners cannot recognize every
possible secret. GitHub push protection provides an additional server-side
check for supported secret patterns. The CI workflow becomes active once pushed;
making its check required for merging is a separate branch-rule setting.

References: [Gitleaks](https://github.com/gitleaks/gitleaks) and
[GitHub push protection](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection).
