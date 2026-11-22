# Assessment validation evidence

Date: 2026-11-22. Tracking: [labs #3](https://github.com/SysKata-project/labs/issues/3).
Approval and merge remain separate from this bounded validation.

## Local gates

Java 25.0.5+8, Maven 3.9.16, Quarkus 3.40.1.1 and PostgreSQL 17.11.
Final `./mvnw -B -ntp verify` passed 22 tests, JaCoCo (742/815 lines,
91.04%; 314/376 branches, 83.51%), PMD/CPD, Checkstyle, Spotless and packaging.
No coverage exclusions or suppressions were added. All 31 Python tests passed.
Python syntax, Markdown links, whitespace and redacted secret scans passed.

The container VM could not start another PostgreSQL fixture because of storage
errors. Tests instead used the existing loopback PostgreSQL 17.11 server with a
new, separate synthetic database, `syskata_labs_assessment_test`, selected via
`-Dquarkus.datasource.jdbc.url=jdbc:postgresql://127.0.0.1:55432/syskata_labs_assessment_test`.
Existing API databases and unrelated containers were not altered. Live fixtures
used a second synthetic database, `syskata_labs_assessment_live`.

Tests cover authenticated dispatch and ownership, immutable request/result replay,
conflicting requests, reset during observation, expiry/account disable, persistent
fault stages, incomplete/malformed adapter output, bounded subprocess output and
environment filtering, callback credentials, HTTPS-only endpoints, receipt binding
and durable retries. Local observer tests use controlled fixtures; real cluster
evidence is identified separately below.

## Live OpenShift and worker evidence

The owner authorized reuse of the previous claim, which reported **OpenShift
4.22.22**. The owner separately approved temporary learner/controller/evaluator
admission policies, RBAC and two service accounts with two-hour credentials in
the test control namespace, followed by complete owned-resource cleanup.
The existing two-session and zero incremental-cost limits were preserved.
The API kubeconfig retained the previously authorized test-only TLS exception;
workload Route HTTPS was verified using the cluster's public ingress CA.

The CLI terminal image was resolved from the cluster ImageStreamTag and pinned:

```text
quay.io/openshift-release-dev/ocp-v4.0-art-dev@sha256:dd774d7a11bb59a6500e5f24130cb3fe8e2fa6f15c85b9a44d52e1635b015699
```

Actual separate gateway and worker processes shared the synthetic labs database.
The restricted controller provisioned the environments; scoped learner commands
created/changed workloads; the restricted evaluator observed and probed them.

| Evidence | Result and scope |
| --- | --- |
| [Combined run](evidence/2026-11-22-assessment-combined.json) | All 34 foundations assertions passed: missing stage proof, initial success/fault, old replica rejection, manifest/imperative repair, worker restart, wrong Service selector/port, recreated UID rejection, reset and stale generation. The later administration privilege probe had a **harness syntax failure**, described below; the report preserves its overall failure flag and verified cleanup. |
| [Corrected administration run](evidence/2026-11-22-assessment-administration.json) | All 26 assertions passed on the final worker build: manifest/access/quota success, equivalent split Role, widened access/wrong subject/wrong quota/wrong manifest rejection, dry-run-only evaluator pod creation, denial of Secrets/exec/token access, learner privilege separation, no evaluator credential mounts/environment in the terminal, and session cleanup. |
| [HTTPS delivery](evidence/2026-11-22-assessment-delivery.json) | All 34 persisted evaluated/infrastructure outcomes validated against OpenAPI `TrustedResult`. Actual worker HTTPS transport completed 68 authenticated grants, retried identical results after deliberately lost acknowledgments, and persisted all duplicate receipts. OAuth/receiver are synthetic fixtures, not Keycloak or API #5 integration. |
| [Installation cleanup](evidence/2026-11-22-assessment-cleanup.json) | Owned learner/terminal namespaces, test admission/RBAC resources, control namespace and private credentials removed after verification. |

## Findings resolved during validation

- Fixed initial response-check initialization that prevented the HTTPS probe from
  running. Added a regression proving the actual probe is invoked.
- Handle null EndpointSlice ports as an unsatisfied backend criterion, rather than
  an infrastructure exception; added a regression fixture.
- Route propagation after workload changes produced transient infrastructure
  results. The harness verifies they contain no grade, then retries with a new
  synthetic attempt; the immutable old result is retained. No error is recast as
  a mistake or success.
- Corrected the harness's ambiguous `oc auth can-i create pods/exec` query to
  `oc auth can-i create pods --subresource=exec` (and the equivalent token query).
  The former queried a named pod, not the exec subresource. Explicit queries and
  the corrected live suite verified both denials. The original failed report is
  retained; it is not reported as an entirely passing run.
- Added fail-closed handling and tests for empty/non-object helper JSON. Extracted
  parsing from process management to satisfy the existing complexity rule.

Earlier failed runs verified session cleanup before retries. Reports contain no
credentials, kubeconfigs, learner transcripts, private route names or raw errors.

## Remaining integration boundaries

API #5 still owns public dispatch, real service-account assignment validation,
locked callback application, in-flight reset/callback races and exactly-once
progress/rewards. Content activation must register these profile/evaluator/check
IDs against immutable released assessment projections. Docs #5 owns installation
of the real Keycloak/client configuration and production secret mounts. This work
does not claim those later issues, a production release or a full adversarial audit
are complete. Learner-facing checkpoint activation remains gated on that work.
