# Lab lifecycle validation evidence

Date: 2026-11-21. Tracking: [labs #2](https://github.com/SysKata-project/labs/issues/2).
Status: draft implementation; live acceptance remains blocked on replacement-cluster access.

## Passing local checks

- Java 25.0.5+8, Maven Wrapper 3.9.16, Quarkus 3.40.1.1, PostgreSQL 17.11.
- `./mvnw -B -ntp clean verify`: 14 tests passed, including real PostgreSQL
  transactions and real HTTP/WebSocket connections. JaCoCo: 409/452 lines
  (90.49%) and 185/222 branches (83.33%); both 80% thresholds passed.
- PMD, CPD, Checkstyle and Spotless passed without exclusions or suppressions.
- `python3 -m unittest discover -s tests -v`: 19 tests passed. Deliberate denial
  fixtures in the older feasibility harness print `FAIL` when rejection is the
  expected assertion; the unittest result is passing.
- Python compilation, whitespace and working-directory/history secret scans passed.

Tests cover concurrent capacity admission, idempotency conflict/replay, ownership,
reset without lease extension, single-use and expired tickets, account disable,
idle/lease expiry, cleanup retry, HTTP authorization and WebSocket authentication
expiry, streaming, replay rejection and reset/deletion disconnect. Worker tests
inject cluster failures and use a fake cluster adapter; terminal tests use an echo
process. These tests do not establish OpenShift compatibility or pod isolation.

## Required live acceptance

The owner selected a replacement cluster rather than the previously configured
allocation. No old cluster was accessed or modified during this implementation.
Await its authenticated kubeconfig path outside Git. Then:

1. Re-run feasibility on the replacement allocation; record actual version,
   networking, permissions and the existing two-session/$0 incremental-cost cap.
2. Select and verify a digest-pinned terminal image containing oc/bash/coreutils.
3. Install the operator admission/RBAC manifests without overwriting shared
   configuration, then prove the service runs with the controller service account.
4. Verify policy type checks, learner pod-token denial, external-Service/route
   denial, object quotas and terminal network reachability with actual requests.
5. Exercise both learner namespaces and terminal shells. Prove cross-learner
   denial, reconnect, reset, lease/idle expiry and account-disable disconnects.
6. Inject partial provisioning and cleanup failures; restart the service and
   confirm every owned namespace is removed before capacity is released.
7. Record credential expiry/revocation behavior, including Kubernetes clock skew
   and the effect of cluster unavailability. Do not claim an unreachable API has
   revoked a previously copied token.
8. Review final changes, pass CI and request approval only after outstanding
   acceptance work is resolved. Keep the issue open and PR draft meanwhile.

The byte-stream shell currently does not support PTY resizing. Public API adapter
integration belongs to api #5 and mobile transport integration belongs to app #4.
No trusted assessment or progression implementation is claimed by this work.
