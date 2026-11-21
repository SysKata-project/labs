# Lab lifecycle decisions

Tracking: [labs #2](https://github.com/SysKata-project/labs/issues/2).

## Accepted by the project owner on 2026-11-21

- Implement the separate lab lifecycle service and terminal gateway using
  Java/Quarkus with PostgreSQL, reusing the API tooling and conventions.
- Maximum lab lease: 30 minutes. Terminal idle timeout: 10 minutes.
  Reset never extends the original lease deadline.
- Preserve the previously accepted ceiling of two concurrent learner sessions
  and $0 incremental cost using the existing ephemeral cluster allocation.

## Existing contract constraints

Tickets are single-use, last at most 60 seconds, and never outlive the lab.
Authenticate the first WebSocket frame within five seconds. Reset, close,
expiry and account deletion revoke terminal access. Infrastructure failures
never become learner mistakes. API progression and assessment stay in their
respective implementation issues.

## Implementation policy

Reuse the API's Java 25, Quarkus 3.40, Maven wrapper, 80% line/branch coverage,
PMD/CPD, Checkstyle and Spotless gates. Lab state uses a separate PostgreSQL
schema/database identity; the service cannot write learning or reward tables.

## Replacement-cluster validation authorization, 2026-11-21

The owner supplied a replacement ephemeral workshops cluster and authorized
skipping TLS certificate verification for that test cluster only. This does not
change production TLS requirements.

The owner explicitly approved the temporary `syskata-labs-boundary-v1` and
`syskata-labs-controller-v1` admission policies/bindings, the controller
ClusterRole/binding and a service account in `sk-control-validation-1121` with
a two-hour credential. Controller metadata reads are cluster-wide; its write
admission boundary restricts changes to labeled test namespaces. All test-owned
resources must be removed after validation. No paid infrastructure is authorized.
Credentials and kubeconfigs remain outside the repository and reports.
