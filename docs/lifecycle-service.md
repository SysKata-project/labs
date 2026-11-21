# Lab lifecycle service and terminal gateway

Tracking: [labs #2](https://github.com/SysKata-project/labs/issues/2).
Scope: a namespace-scoped pilot service validated on ephemeral OpenShift 4.22.22.
[Accepted decisions](lifecycle-decisions.md) define the pilot limits.
See [validation evidence and outstanding acceptance](lifecycle-validation.md).

## Local verification

Use Java 25.0.5+8 and the committed Maven 3.9.16 wrapper. Quarkus is pinned to
3.40.1.1. Python 3.10+ and `oc` are needed by the isolated cluster adapter.

```sh
sh scripts/install-hooks.sh
sh scripts/test-database.sh start
./mvnw -B -ntp clean verify
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/cluster.py scripts/validate-openshift.py
git diff --check
sh scripts/scan-secrets.sh history
sh scripts/test-database.sh stop
```

The database fixture uses a bounded 256 MiB tmpfs, binds only to loopback, contains synthetic test data and
uses trust authentication solely for disposable tests. Never use that mode for
shared deployments. Production configuration requires explicit database secrets.
`./mvnw spotless:apply` formats Java. Verification enforces 80% line and branch
coverage, PMD including CPD at 100 tokens, Checkstyle and Spotless. Reports live
under `target/`; CI uploads reports for seven days. See the API's
[development conventions](https://github.com/SysKata-project/api/blob/main/docs/development.md).
No coverage exclusions or rule suppressions are configured.

## Service boundary

Run this process separately from the learning API, with a dedicated PostgreSQL
identity/database. It cannot access progress, rewards or answer-key tables.
Flyway initializes its schema. Expose `/q/health/live` and `/q/health/ready` only
on the operator network. Configure `.env` locally from `.env.example`; never
commit a populated file. Require TLS between services and WSS at the gateway.

`/internal` is accessible only to the API service and deletion operator, protected
by a random bearer credential of at least 32 characters and network isolation.
The API authenticates learner ownership and exercise eligibility before calling
these endpoints. Never route them directly to mobile clients. IDs and idempotency
keys are UUIDs. The internal DTO uses camelCase; the public API contract adapter
belongs to api #5. Each request is bounded by the HTTP server's configured limits.

| Endpoint | Input | Result |
| --- | --- | --- |
| POST `/internal/labs` | key, id, subject, scenario (`foundations` or `administration`) | Persisted provisioning record |
| GET `/internal/labs/{id}?subject=...` | Authenticated API's subject | Owned current state |
| POST `/internal/labs/{id}/reset` | key, subject, revision, generation | New generation, same lease deadline |
| POST `/internal/labs/{id}/close` | key, subject, revision, generation | Durable cleanup intent |
| POST `/internal/labs/{id}/tickets` | subject, generation, hash, expiresAt | Register API-generated ticket SHA-256 hash |
| POST `/internal/accounts/disable` | subject | Deny access and enqueue cleanup for all owned labs |

The API generates cryptographically random tickets of at least 256 bits and owns
idempotent plaintext delivery. Register the hash before returning the ticket.
Repeat registration preserves consumption; it cannot revive a redeemed ticket.
The gateway never stores plaintext tickets. `expiresAt` uses UTC ISO-8601 with
at most microsecond precision (PostgreSQL timestamp precision).

Failures return a bounded `code` and `retryable`; storage and cluster errors never
include raw commands, database messages, tokens or learner transcripts. An
infrastructure failure does not write a learner grade.

## Lifecycle and recovery

The database transaction serializes admission across replicas. At most two
sessions may have resources outstanding, including cleanup failures. Provision,
reset and close keys persist their original response; clients poll current state
rather than treating a replay as authorization. Reset increments generation once,
invalidates tickets and retains every old generation until cleanup succeeds.

A reconciler runs every two seconds, with a database advisory lock across workers.
It resumes persisted provisioning/reset/cleanup after restart. Namespace names are
deterministic from session UUID and generation, and deletion requires the exact
session ownership label. Teardown deletes the learner service account before waiting, then requests both
namespace deletions together. Only verified absence releases capacity. A failed cluster
request never counts as successful cleanup. Never remove finalizers automatically.

The 30-minute lease starts at admission. Ready sessions idle out after ten minutes
without terminal input; terminal output does not extend idle time. Reset does not
extend the lease. A gateway checks authorization on every input frame and every
200 ms while connected; database failure closes access. A copied Kubernetes token
can remain valid until service-account deletion or its server-issued expiry;
cluster unavailability can delay that revocation. This limitation requires explicit
live verification and is not a claim of instantaneous Kubernetes token revocation.

## Terminal transport v1

Connect to `/terminal/{lab UUID}` over WSS. The URL has no ticket or credentials.
Within five seconds send a JSON text frame:

```json
{"type":"auth","subject":"API-authenticated-subject","generation":1,"ticket":"API-issued-single-use-ticket"}
```

The ticket is bound to that subject, lab and generation. It expires within 60
seconds and before the lease ends. Reconnect needs a newly issued ticket.
Subsequent input uses `{"type":"stdin","data":"base64 bytes"}`; output uses
`{"type":"stdout","data":"base64 bytes"}`. Input is limited to 4096 decoded
bytes, frames to 16 KiB. Output is streamed in 4096-byte chunks with bounded send
time; it is never stored or logged. Invalid frames or failed authorization close
the connection. The initial stream is byte-oriented interactive bash, without
PTY resizing; mobile terminal integration remains app #4.

Shells execute in a separate terminal namespace, not on the API/gateway host and
not in the learner workload namespace. The terminal container receives only the
learner service-account kubeconfig over stdin, with mode 0600, and has no
controller/evaluator token or automatically mounted service-account token.
The terminal image must contain `oc`, bash and coreutils and be pinned by digest.

## Cluster boundary and remaining live work

An operator installs the versioned admission policy/binding generated by:

```sh
# Set LAB_ROUTE_DOMAIN to the assigned cluster apps domain.
python3 scripts/cluster.py policy > /private/tmp/syskata-lab-policy.json
# Review and install with the operator identity; never overwrite a foreign policy.
oc create -f /private/tmp/syskata-lab-policy.json
```

Provisioning verifies the expected policy before issuing credentials. Policies
select only `syskata.io/lab-managed=true` namespaces. They forbid pod token
mounts, Secret volume/environment references, non-default workload service accounts, external Services, arbitrary
route hosts and wildcard routes. Only `web-<learner namespace>.<apps domain>` with
edge TLS and HTTP redirect is allowed. Object-count quotas supplement compute
quotas. The learner cannot change quotas/policies/service accounts except the
explicit exercise quota. Terminal network egress allows the configured pod-visible API /32
on port 6443 and cluster DNS only. Resolve the API from inside the cluster: split DNS
can return a different address than the operator workstation. Readiness probes
the API from the terminal pod and refuses an unreachable endpoint. Workload policy follows the validated
[feasibility boundary](feasibility.md).

Do not use a cluster-admin kubeconfig as the service runtime identity. Render the scoped controller service account, RBAC and write-boundary policy with
`python3 scripts/controller-rbac.py syskata-system`. Review and install them as
an operator before generating a private kubeconfig for that service account.
The controller can read non-secret workload metadata cluster-wide because RBAC
cannot express dynamic namespace selectors; its admission policy restricts
writes to labeled, deterministically named lab/terminal namespaces. It cannot
read Secrets, impersonate identities, grant `cluster-admin`, or modify admission
policy. Validate this boundary before shared deployment. It needs namespace lifecycle, scoped resource management,
TokenRequest for learners, terminal exec, and read-only policy verification;
cluster-scoped namespace creation is a privileged controller responsibility.
Evaluator credentials remain separate and are outside this issue.

The tested cluster version, image digest, controller permissions, policy checks,
terminal scenarios and cleanup evidence are recorded in
[validation evidence](lifecycle-validation.md). API #5 supplies the public adapter;
labs #3 supplies trusted assessment. This bounded validation is not a production
launcher, a full adversarial audit or a mobile terminal acceptance test.

## Implementation references

- [Quarkus WebSockets Next](https://quarkus.io/guides/websockets-next-reference/)
- [Kubernetes validating admission policy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)

## Repeating the live lifecycle suite

Use an isolated loopback service/database and an operator-approved test cluster;
never point the fixture clock controls at learner data. Install
`requirements-live.txt` into a private virtual environment. Keep the runtime JSON
outside Git, mode 0600, with the variables from `.env.example` plus
`QUARKUS_HTTP_HOST=127.0.0.1`, `QUARKUS_HTTP_PORT=8089`, and a PATH containing
Python and oc. Set `LAB_DB_URL` to
`jdbc:postgresql://127.0.0.1:55433/syskata_labs` for this fixture only.

```sh
export SYSKATA_LIVE_RUNTIME_CONFIG=/private/path/runtime.json
# JAVA_HOME must point to the pinned JDK. Start the disposable database first.
python3 scripts/live-service.py start
python3 scripts/verify-lifecycle.py \
  --runtime-config "$SYSKATA_LIVE_RUNTIME_CONFIG" \
  --feasibility-report /private/path/passing-feasibility.json \
  --report /private/path/lifecycle-report.json \
  --service-control scripts/live-service.py
python3 scripts/live-service.py stop
```

The harness refuses to start sessions until feasibility and its cleanup passed.
It uses real WebSockets and oc shells, checks admission and capacity, and resets
sessions. Lease/idle checks deliberately advance only synthetic database fixture
timestamps; these are accelerated boundary tests, not 30-minute wall-clock soak
tests. A temporary adapter creates a real partial namespace, injects provisioning
and cleanup failures, then restores the normal adapter and restarts the service.
Reports contain bounded check names/results, never terminal transcripts or tokens.
The operator must verify final namespace absence and remove the approved test
controller/admission resources after the run. Revoke/remove private credentials.
