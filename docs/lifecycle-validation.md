# Lab lifecycle validation evidence

Date: 2026-11-21. Tracking: [labs #2](https://github.com/SysKata-project/labs/issues/2).
The bounded namespace-scoped implementation passed local and live validation.
Approval and merge remain separate from these test results.

## Passing local checks

- Java 25.0.5+8, Maven Wrapper 3.9.16, Quarkus 3.40.1.1, PostgreSQL 17.11.
- `./mvnw -B -ntp clean verify`: 14 tests passed, including real PostgreSQL
  transactions and actual HTTP/WebSocket connections. JaCoCo: 410/453 lines
  (90.51%) and 187/224 branches (83.48%); both 80% thresholds passed.
- PMD, CPD, Checkstyle, Spotless and packaging passed without exclusions or
  suppressions. Reset is denied after idle expiry even before reconciliation.
- `python3 -m unittest discover -s tests -v`: 21 tests passed, including
  cleanup ownership and live-harness bookkeeping regressions. Deliberate denial
  fixtures in the feasibility harness print `FAIL` when rejection is the expected
  assertion; the unittest result is passing.
- Python compilation, local Markdown links, whitespace and secret scans passed.

Local worker tests inject a fake cluster adapter and echo terminal process.
They complement the actual cluster tests below; they do not substitute for them.

## Live cluster evidence

The owner-authorized replacement workshops allocation reported OpenShift
**4.22.22**, with OVN-Kubernetes. It matches the 4.22 curriculum minor baseline.
The existing two-session, $0 incremental-cost limit was preserved; no paid
infrastructure was provisioned. TLS verification bypass was explicitly authorized
for this ephemeral cluster only.

The terminal image was discovered from the cluster's `openshift/cli:latest`
ImageStreamTag, then pinned and tested by digest:

```text
quay.io/openshift-release-dev/ocp-v4.0-art-dev@sha256:dd774d7a11bb59a6500e5f24130cb3fe8e2fa6f15c85b9a44d52e1635b015699
```

| Evidence | Passing checks | Coverage |
| --- | --- | --- |
| [Replacement feasibility](evidence/2026-11-21-feasibility.json) | 33 | Workloads/routes, RBAC, cross-project API/network denial, escalation, quota, actual token expiry and cleanup |
| [Lifecycle service](evidence/2026-11-21-lifecycle.json) | 36 | Idempotency, capacity, real oc shell, ownership, Service/Route limits, reset/reconnect/replay, credential revocation, lease/idle expiry, partial provisioning and restart recovery |
| [Final boundary and account disable](evidence/2026-11-21-account-disable.json) | 19 | Final build and policy: direct/indirect token/Secret restrictions, Service/Route and object quotas, terminal disconnect, disabled-account denial and cleanup |
| [Final installation cleanup](evidence/2026-11-21-lab-cleanup.json) | All recorded assertions | No learner namespaces; test admission policies/bindings, controller RBAC and controller namespace removed; controller credential files removed |

The restricted controller, not the operator identity, provisioned and removed the
learner/terminal environments. Additional authorization probes returned `no` for
Secret reads and user impersonation; a server-dry-run namespace creation outside
the allowed name/label boundary was denied by its admission policy. The policies
had no server type-check warnings after correction.

The lifecycle test used actual service restarts and an injected adapter that
created a real partial learner namespace, then failed provisioning and cleanup.
The service retained outstanding cleanup until the normal adapter was restored;
restart reconciliation removed the resource and released capacity.

Lease/idle tests deliberately advanced timestamps in the isolated synthetic
PostgreSQL fixture. They test actual gateway/reconciler boundary behavior, not a
30-minute wall-clock soak. Separately, feasibility waited for the actual
server-issued ten-minute Kubernetes credential lifetime plus 65 seconds and
verified authentication rejection while the identity still existed. Reset also
verified rejection of a credential from the old generation after teardown.

## Findings resolved during validation

- Corrected the controller policy's invalid wildcard resource combination and
  mixed-kind CEL type warnings.
- Configured the API address visible inside the cluster, which differed from
  workstation DNS; added a terminal-pod API reachability readiness check.
- Used OpenShift's automatically assigned `web-<namespace>.<apps domain>` route
  hostname, avoiding custom-host privileges. External IPs were rejected by the
  platform before the custom policy; the harness accepts that explicit denial.
- Revoked the learner service account before waiting and requested both namespace
  deletions together, while retaining ownership checks and verified absence.
- Denied indirect Secret access through pod volumes/environment references,
  including init/ephemeral-container environment references in the policy.
- Corrected live-harness cleanup bookkeeping to match session ID rather than a
  mutable response snapshot; added an offline regression test.
- Used bounded tmpfs for the disposable database after its container VM exhausted
  disk space. No unrelated container data was removed.

Earlier failed runs were cleaned up and are not counted as passing evidence.
Reports contain no credentials, kubeconfigs, terminal transcripts or learner data.

## Scope limits and follow-up integration

The transport is a byte-oriented interactive bash stream; it does not yet support
PTY resizing. Mobile terminal UX/interoperability remains app #4. Public API
orchestration and deletion integration remain api #5; trusted outcome assessment
remains labs #3. Production installation remains docs #5. These are existing
cross-repository deliverables, not claims made by this lab-service validation.

Gateway authorization fails closed on expiry, reset, disable or database failure.
Kubernetes credential revocation still depends on API reachability or server-issued
expiry, including clock skew; an unreachable cluster cannot be claimed to have
revoked a copied token. The service retries teardown and retains capacity until
absence is verified. This is bounded pilot validation, not arbitrary cluster-admin
isolation, a full adversarial audit or production readiness.
