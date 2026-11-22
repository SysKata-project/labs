# Trusted checkpoint assessment v1

Tracking: [labs #3](https://github.com/SysKata-project/labs/issues/3).
Status: bounded runtime and transport validation passed; see [evidence](assessment-validation.md).
No production content activation or API progression integration is claimed. See [decisions](assessment-decisions.md).

## Process and credential boundary

Build the existing Java artifact with the existing quality gates. Run a second
process from the repository root with `-Dquarkus.profile=assessment` before
`-jar target/quarkus-app/quarkus-run.jar`. The assessment profile disables HTTP,
domain sockets and lifecycle reconciliation; the gateway process does not run
the assessment scheduler. Mount the evaluator kubeconfig and Keycloak client
secret **only** in the worker. Never mount the controller kubeconfig in it.
Both processes use the labs database; neither can access learning/reward tables.
The deployment operator must keep the process profiles and secret mounts separate.

Worker configuration (supply real secrets outside Git):

```text
LAB_DB_URL=jdbc:postgresql://localhost:55433/syskata_labs
LAB_DB_USER=REPLACE_LOCALLY
LAB_DB_PASSWORD=REPLACE_LOCALLY
LAB_ROUTE_DOMAIN=apps.example.invalid
ASSESSMENT_KUBECONFIG=/private/path/evaluator.kubeconfig
ASSESSMENT_RESULT_URL=https://api.example.invalid
ASSESSMENT_TOKEN_URL=https://identity.example.invalid/realms/syskata/protocol/openid-connect/token
ASSESSMENT_CLIENT_ID=REPLACE_WITH_ASSIGNED_WORKER_CLIENT
ASSESSMENT_CLIENT_SECRET=REPLACE_LOCALLY
```

The Keycloak client must have audience `syskata-assessment-results` and role
`assessment-result-writer`; api #5 validates its principal against dispatch
assignment. Configure trusted TLS chains; there is no insecure callback option.
`LAB_ROUTE_CA` optionally names the trusted PEM CA for the workload Route.
Python 3.10+ and `oc` must be on PATH. The adapter gets only PATH, its kubeconfig,
route domain and optional route CA, never database or result-delivery secrets.

The default gateway profile needs no assessment secrets. Set its
`LAB_EVALUATOR_NAMESPACE` to the control namespace after the operator installs
the evaluator identity. New sessions then receive namespace-scoped evaluator
Roles and bindings during provisioning. Existing sessions must be reset after
this installation; no new privileges are silently retrofitted into live labs.

## Operator installation

Render `python3 scripts/evaluator_rbac.py CONTROL_NAMESPACE` and the updated
`scripts/controller-rbac.py` output for operator review. Install the evaluator
admission policy and binding before granting its RBAC. Do not overwrite shared
objects. The evaluator verifies its service-account identity and the installed
policy/binding before observing a lab. Its cluster role permits namespace metadata
GET and verification of its own policy; resource reads and probes are bound only
in assigned learner/terminal namespaces. It cannot read Secrets, mint tokens,
exec into terminals, impersonate users, or grant roles.

Its namespace permissions include read-only Deployments/ReplicaSets, pods,
Services, EndpointSlices, Routes and quotas; patch of Deployment `web`; and
LocalSubjectAccessReview creation. Pod creation is allowed only with server
dry-run by its admission boundary. The controller gains the corresponding
permissions needed to create these non-escalating Roles; it never receives
evaluator credentials. The test setup must be removed after live validation.

## Dispatch and fault stage

All routes use the existing internal service authentication and operator network
boundary. They are not learner-facing. The API owns subject, prerequisite,
attempt and immutable content binding validation before dispatch.

`POST /internal/assessments` accepts this camelCase internal envelope:

```json
{
  "id": "00000000-0000-0000-0000-000000000001",
  "labId": "00000000-0000-0000-0000-000000000002",
  "subject": "API-authenticated-subject",
  "generation": 1,
  "sessionId": "00000000-0000-0000-0000-000000000003",
  "instanceId": "00000000-0000-0000-0000-000000000004",
  "attemptId": "00000000-0000-0000-0000-000000000005",
  "versions": {"schema_version":"1.0.0","bundle_version":"0.3.0","policy_version":"1.0.0"},
  "exerciseRevision": 1,
  "evaluator": "openshift.foundations",
  "evaluatorVersion": 1
}
```

The example bundle version is synthetic: neither checkpoint is currently an
activated production bundle. API #5/content activation must bind these evaluator
IDs and exact criterion sets to a released assessment projection before offering
the checkpoints to learners. Version 1 rejects other evaluator/exercise revisions.
The caller cannot select commands, arbitrary parameters, URLs or criterion sets.

GET `/internal/assessments/{id}?subject=...` returns persisted request, state,
result and delivery status. Submission persists before returning HTTP 200; the
public API's asynchronous 202 adapter belongs to api #5. Repeating an identical
assessment ID returns the stored job; changing its bindings returns 409. Only one
pending assessment per lab generation is admitted, including concurrent requests.

Foundations requires POST `/internal/labs/{id}/fault-stage` with `subject` and
`generation`, after the learner requests the controlled fault. GET the same path
with those query parameters to poll. The worker first verifies initial deployment,
backend and HTTPS response; it durably records UID/generation before patching the
image to the specified `.invalid` registry. A UID/resourceVersion JSON Patch
precondition prevents patching a replaced or concurrently changed Deployment.
Only a later observation of that exact fault generation's image-pull failure
completes the stage. Restart resumes the persisted intent without inventing proof.
If the learner repairs before the worker records the fault, or deletes/recreates
the Deployment, reset and repeat; this fails closed rather than inferring history.
Reset/expiry invalidate unfinished stage work. No stage evidence comes from the
terminal or the learner's request body.

## Versioned criteria and observations

Profiles are in `assessment/profiles/`, derived from content checkpoint revision 1
in drafts 0.2.0/0.3.0 at content commit
`327a10f552549ae5b80ea4bf7dd78632aa951d75`. They pin the supplied Deployment/image and are immutable
within evaluator version 1; behavioral changes need a new evaluator version.

| Evaluator | Criterion suffixes (each prefixed `criterion.`) |
| --- | --- |
| `openshift.foundations` v1 | context, deployment, service, route, response, repair |
| `openshift.administration` v1 | context, manifest, access, quota |

Checks inspect current resource generations and follow controller UIDs through
ReplicaSets, pods, Service-owned EndpointSlices and admitted Routes. Matching
labels or a matching HTTP body alone cannot prove success. Foundations requires
the same Deployment UID across initial/fault/repaired generations. Manifest checks
allow server defaults and equivalent resource quantities. Administration checks
effective supplied-reader access (including service-account groups), not Role
spelling, and requires a dry-run rejection explicitly naming `exercise-budget`.
An RBAC denial is not proof of a quota rejection. Alternate split Role rules are
valid if effective permissions are unchanged.

Verified missing/wrong resources produce boolean criterion failures. API failures,
expired evaluator credentials, approved-image pull failures, route connectivity
failures and malformed/bounded adapter output produce infrastructure errors with
no criteria and no learner grade. Raw manifests, HTTP bodies, cluster errors,
commands and tokens are never included in results. Persisted stage evidence is
limited to phase, Deployment UID and generation numbers.

## Persistence, delivery and integration limits

A database advisory lock serializes workers across replicas. Evaluation performs
external observation outside database transactions, then rechecks the current
lab generation, readiness, lease and disabled-account state before publishing.
Interrupted jobs remain pending and resume after process restart. Published
results never change: retries resend the same JSON, assessment ID and idempotency
key. Delivery uses Keycloak client credentials and HTTPS, accepts only an exact
assessment ID with an allowed receipt, and retries failures with bounded backoff.
An unavailable API does not cause regrading. In-flight reset/callback races are
finally resolved by api #5's locked generation checks and receipt deduplication.

This service never writes XP or progress. API #5 owns the real callback receiver,
single reward semantics, prerequisite enforcement, content activation binding and
account-deletion integration. Deletion must include assessment request/result and
stage rows once active lab cleanup is verified. This worker does not claim those
cross-repository acceptance tests have passed.

References: [Kubernetes access reviews](https://kubernetes.io/docs/reference/kubernetes-api/definitions/local-subject-access-review-v1-authorization/),
[server dry-run](https://kubernetes.io/docs/reference/using-api/api-concepts/#dry-run),
[Quarkus HTTP configuration](https://quarkus.io/guides/http-reference/).

## Reproducing live verification

Use the pinned JDK, an approved ephemeral allocation and private JSON containing
worker/gateway variables above. For the harness only, use the dedicated synthetic
`syskata_labs_assessment_live` database on loopback port 55432. The harness refuses
other database URLs. Do not reuse a learner database. Keep the JSON mode 0600.
Install the rendered admission/RBAC resources before running. Obtain the public
Route CA rather than disabling route certificate verification.

```sh
python3 scripts/verify-assessment.py --runtime-config /private/path/runtime.json \
  --report /private/path/assessment.json
python3 scripts/verify-delivery.py --runtime-config /private/path/runtime.json \
  --directory /private/path/delivery --report /private/path/delivery.json \
  --openapi ../api/contracts/openapi.yaml
```

The first harness launches separate gateway/worker processes, uses actual scoped
learner commands, tests both outcomes and errors, and closes all its lab sessions
before stopping the processes. It records bounded infrastructure retries without
reclassifying them as mistakes. If cleanup fails, retain the admission protections
and verify the outstanding owned namespaces before removing the test installation.
The operator removes the test controller/evaluator identities, their cluster
resources and private credential files only after namespace absence is verified.

The second harness runs a synthetic OAuth/receipt service over actual local TLS.
It validates credentials, exact persisted result JSON and idempotency headers,
intentionally loses the first acknowledgment, then returns duplicate receipts.
It advances retry timestamps only in the dedicated synthetic database. Its
one-day certificate/private key and truststore are removed afterward. This proves
worker transport and durable retry, not the future API receiver or actual Keycloak
realm configuration. OpenSSL, the pinned JDK's keytool and psql are required. Install
`requirements-assessment-validation.txt` into a private virtual environment for
the delivery harness's OpenAPI/schema validation; these pins match content tooling.
