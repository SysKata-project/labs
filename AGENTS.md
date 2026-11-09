# SysKata agent working agreement

## Scope and source of truth

Follow this workflow for issue-driven work in every SysKata repository.
The [GitHub project](https://github.com/orgs/SysKata-project/projects/1) is the
source of truth for current status and priority. Issue acceptance criteria and
linked decisions define scope; local planning snapshots may be stale.

This agreement is also included in each repository's root `AGENTS.md` so it
applies to standalone clones. Keep the shared workflow consistent across copies;
put component-specific instructions in a clearly labeled additional section.

## 1. Pick up an issue

- Work the issue requested by the user. Otherwise inspect the live project and
  select the highest-priority Ready issue whose blocking dependencies are met;
  use project order to break ties. Do not duplicate another contributor's work.
- If no issue is Ready, reconcile backlog readiness using the completion rules
  below before selecting work. Do not treat a stale Backlog status alone as a
  blocker or promote an issue with unresolved blocking dependencies.
- Read the full issue, acceptance criteria, dependencies, linked decisions and
  relevant repository instructions before editing. Confirm the issue is open
  and check for existing branches or pull requests.
- Inspect the checkout and preserve unrelated or uncommitted work. Reuse a
  suitable task branch or create a `codex/<short-description>` branch from the
  current target branch. Never discard changes to obtain a clean checkout.
- Ask the user **one question at a time** when a product or technical decision
  needs their input. Record accepted decisions in the appropriate documentation;
  distinguish proposed choices from accepted ones.

## 2. Set work in progress

- Before implementation, set the issue's project Status to **In progress**.
  Keep the GitHub issue itself open. Confirm the project update succeeded.
- If the issue is absent from the project, add it when issue/project management
  is authorized by the task. Preserve existing priority and phase unless the
  task gives a reason to change them.
- If access prevents an update, report the exact limitation. Never claim a
  remote status changed without verifying it.

## 3. Work the changes

- Implement the acceptance criteria with focused changes and update relevant
  documentation/configuration examples. Keep secrets and learner data out of Git.
- Respect dependencies and agreed quality gates. In particular, complete
  [api #9](https://github.com/SysKata-project/api/issues/9) before backend feature
  implementation; the minimal scaffold needed to establish its tools is allowed.
- Add or adjust meaningful tests for behavioral changes. Run applicable build,
  test, lint, formatting, coverage and security checks required by the repository.
  For documentation-only changes, check links, consistency and whitespace.
- Record validation results and any checks that could not run, with reasons.
  Do not treat untested runtime behavior as verified.
- Keep the user informed of progress and material blockers. If work cannot
  proceed, set project Status to **Blocked**, identify what must be resolved,
  and keep the issue open. Return it to **In progress** when work resumes.
- Split newly discovered unrelated work into follow-up issues when authorized;
  do not silently remove acceptance criteria or expand the current scope.

## 4. Submit for review

- Inspect the final diff for unintended changes and ensure applicable checks
  pass before presenting the work as ready. Link the issue and relevant
  cross-repository changes in the pull request.
- Describe the problem, resulting behavior, validation evidence and remaining
  limitations. Use a draft pull request for incomplete work; it stays
  **In progress** (or **Blocked**) until ready for review.
- Set project Status to **In review** when the changes are ready and review has
  been requested. Keep the issue open while approval or merge is pending.
- Address feedback and rerun affected checks. Approval must cover the final
  changes, not a superseded revision. Do not self-approve on the user's behalf
  or merge without authorization; approval and merge are separate events.
- Use an automatic closing reference only when that pull request completes
  the entire issue. For partial or cross-repository work, use a non-closing
  reference until all required changes are ready.

## 5. Close only after approval and merge

- Verify that the final changes were approved by the user or authorized
  reviewer, required checks passed, and every required pull request was merged
  into its intended target branch. A local commit, open PR or approval alone
  does not complete the issue.
- Verify every acceptance criterion, including any required deployment or
  runtime validation. A merge with outstanding acceptance work is not Done.
- After those conditions hold, close the issue (or verify automatic closure)
  and set project Status to **Done**. Confirm both states on GitHub.
- Reconcile readiness in the live project after completion: review dependent
  Backlog issues and promote those whose blocking dependencies are all complete
  and whose scope is sufficiently defined to **Ready**. Verify dependency issue
  states and required merged changes; do not infer completion from a closed PR
  or a local planning snapshot.
- Preserve priority and phase, respect explicit deferrals and existing ownership,
  and leave coordination trackers or issues with unmet dependencies in their
  appropriate state. Partial scaffolding permission does not make an entire
  blocked deliverable Ready. If a material readiness decision is unresolved,
  ask the owner one question at a time.
- Confirm each project update succeeded and report promoted issues and the next
  eligible issue by priority and project order. If access prevents reconciliation,
  report the exact limitation; never claim an unverified status change.
- Report the issue/PR links, what changed, validation results and any explicitly
  deferred follow-up work. If approval, merge or validation is pending, state
  what remains and leave the issue open in the appropriate project state.

## Project status reference

| Status | Meaning |
| --- | --- |
| Backlog | Planned; not yet ready to start |
| Ready | Scoped and dependencies permit starting |
| In progress | Active implementation or review preparation |
| Blocked | Cannot proceed until a named dependency or decision is resolved |
| In review | Ready changes awaiting review, approval or merge |
| Done | Acceptance criteria met; final changes approved and merged; issue closed |

## Secret protection

- Run `sh scripts/install-hooks.sh` after cloning; keep pre-commit and pre-push
  scanning enabled. Follow [SECRET-PROTECTION.md](SECRET-PROTECTION.md).
- Never bypass a failed secret scan or print detected secret values. Remove the
  secret and rotate exposed credentials; review any false-positive exception.
- Before publishing changes, run `sh scripts/scan-secrets.sh history` as well as
  the repository's other validation checks.
