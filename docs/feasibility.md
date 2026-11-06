# OpenShift feasibility spike

Tracking: [labs #1](https://github.com/SysKata-project/labs/issues/1).
This is a disposable runtime proof, not the lab lifecycle service or terminal
gateway planned in labs #2. A passing run does not establish production readiness.

## Accepted scope and budget

On 2026-11-06 the project owner supplied an ephemeral Red Hat workshops cluster,
accepted **2 concurrent learner sessions**, and selected **$0 incremental cost per
session using the existing allocation only**. Do not provision paid infrastructure.
The underlying allocation's cost is not measured or claimed to be zero. Its
expiration and continuing availability are outside this repository's control.
The owner may replace the cluster; every replacement must pass the checks again.
No learner state or persistent application data should be kept on this test cluster.
The selected feasibility provider is the supplied Red Hat workshops allocation;
no durable production provider is selected by this decision.

The current runtime reports OpenShift **4.22.20** and OVN-Kubernetes, in the same
minor release as the 4.22 curriculum source baseline. The image is pinned to a
public Red Hat UBI 9 Python 3.12 linux/amd64 digest in the script. No restricted
images are redistributed. Other CPU architectures need an appropriate digest.
No curriculum version adjustment is identified for the tested namespace-scoped
operations. This does not validate every 4.22 objective or the unfinished lessons.

## Running against any replacement cluster

Requirements: Python 3.10+, `oc`, `curl`, an authenticated **private kubeconfig
outside the checkout**, API/route connectivity, image pull connectivity, and an
operator allowed to create/delete namespaces, service accounts, roles, bindings,
quotas, network policies, deployments, services, routes, and TokenRequests. It also
creates a uniquely named ValidatingAdmissionPolicy and binding scoped by namespace
selector to this run. These are cluster-scoped resources; they do not select or
overwrite existing shared configuration. The operator must also read cluster version/network configuration and inspect pods.
Use the cluster CA for TLS verification. The owner explicitly authorized skipping
TLS verification for the supplied ephemeral cluster; this exception is not a
production policy. The `--insecure-routes` flag explicitly applies that exception
to HTTPS route probes; it is disabled by default. Never paste a password, token,
or raw kubeconfig into GitHub.

```sh
# Authenticate outside the repository using an interactive credential prompt.
# Substitute your API endpoint; prefer --certificate-authority with its trusted CA.
export KUBECONFIG=/private/tmp/syskata-private/cluster.kubeconfig
umask 077
mkdir -p /private/tmp/syskata-private
oc login https://api.example.invalid:6443 --username=admin

python3 scripts/validate-openshift.py \
  --kubeconfig "$KUBECONFIG" \
  --report /private/tmp/syskata-private/report.json \
  --concurrency 2
# For the explicitly authorized ephemeral cluster, add --insecure-routes.
```

The script creates uniquely named `sk-feas-*` namespaces, labels them with a run
ID, and deletes only namespaces bearing that exact run label. It captures command
output instead of printing it, builds learner kubeconfigs in a private temporary
directory, and emits only sanitized evidence. Temporary learner credentials are
removed on normal exit; remove the operator kubeconfig when finished with the
cluster. Do not run with shell tracing. The sanitized report is the only artifact
to consider publishing after review and secret scanning.

Expect at least eleven minutes plus cleanup: expiry is tested against the
server-issued expiration rather than inferred from a token's requested duration.
The initial run found that the API accepted a token five seconds after its JWT
expiration. Kubernetes uses go-jose's one-minute clock-skew allowance. The harness
therefore tests at expiration +65 seconds, while the service account and RBAC
still exist, and requires authentication rejection (not merely a Forbidden
authorization response). The future gateway must enforce its own session deadline
and close/reject connections at that deadline; JWT expiry alone is not exact TTL
enforcement. No API server configuration is changed to remove this allowance. Startup time is measured
from namespace creation through observed deployment availability, including
provisioning and sequential checks. It is a single-run observation, not a latency
SLO or percentile. Route propagation has a separate bounded 60-second retry.

## Isolation and assessment boundaries

Each session has a fixed operator-owned outer quota: 500m CPU / 512Mi memory
requests, 1 CPU / 1Gi memory limits, and 4 pods. At two sessions that permits at
most 1 CPU / 1Gi requests and 2 CPUs / 2Gi limits. The proof workload requests
50m / 64Mi and limits itself to 250m / 128Mi per session. The chosen concurrency
is an operating ceiling, not a claim that saturation or long-duration load was
measured. A future lifecycle service must enforce admission; this script does
not limit independent invocations or other workloads on the cluster.

The learner can deploy an app, expose a route, exec into its workload, create a
non-escalating namespaced reader role, bind it to a supplied identity, and create
an exercise quota. Learners cannot modify the fixed outer quota, network policies,
service accounts, or tokens, read secrets, impersonate users, or bind cluster-admin.
Kubernetes RBAC escalation checks still apply to role and rolebinding creation.
No trusted evaluator credentials or workloads belong in learner namespaces.
A deny admission policy requires explicit `automountServiceAccountToken: false`,
forbids projected service-account tokens, and restricts pods to the default
service account. This closes the indirect token-issuance path through pod creation,
even if a learner binds its existing permissions to the default service account.
The script tests all three restrictions using actual rejected pod requests.

The network policy allows same-namespace traffic, ingress-router traffic to port
8080, and DNS to the OpenShift DNS namespace. All other pod ingress/egress is
excluded by default. Workloads have no automatically mounted service-account
token, drop Linux capabilities, disallow privilege escalation, and use runtime
default seccomp. This is a bounded feasibility profile, not arbitrary shell access
or a full adversarial security audit. Namespaces do not isolate cluster-admin
exercises; Operators, cluster identity, cluster updates and privileged exercises
remain unavailable. Public test routes contain only an empty temporary directory
listing, never learner data or credentials.

The checks cover deployment, HTTPS route reachability, same-namespace traffic,
cross-project API and pod-network denial, restricted verbs, supplied-identity RBAC,
privilege-escalation rejection, quota enforcement, token expiry, and cleanup.
They do not implement terminal-gateway connection expiry/reconnection or evaluator
result delivery; those belong to later issues. Before exposing a general learner
shell, labs #2 must additionally constrain Service types/external IPs and route
exposure, bound API object counts, and enforce session TTL/admission. This proof
creates only ClusterIP services and no paid infrastructure; it is not an
unchanged production authorization profile. Infrastructure failures fail this
validation and must never be recorded as learner mistakes.

## Teardown and failure recovery

A `finally` block attempts cleanup after success or an ordinary failure, validates
ownership labels before deletion, waits for deletion, and checks `NotFound`.
After namespaces are verified absent, it deletes only the matching labeled
admission binding and policy. If namespace cleanup fails, those protections stay
in place. Connection errors do not count as successful cleanup or authorization
denial.
If interrupted forcibly, the host crashes, or the cluster becomes unreachable,
use the report's run ID to find survivors after connectivity returns:

```sh
oc --kubeconfig "$KUBECONFIG" get namespaces \
  -l syskata.io/feasibility-run=REPLACE_WITH_REPORT_RUN_ID
# Inspect the names AND labels above, then delete only those exact namespaces.
oc --kubeconfig "$KUBECONFIG" delete namespace REPLACE_WITH_EXACT_OWNED_NAMESPACE \
  --wait=true --timeout=180s
```

After verifying all run namespaces are absent, inspect the exact run-named
ValidatingAdmissionPolicyBinding and ValidatingAdmissionPolicy and their ownership
labels, then delete those two objects (binding first). Never delete a shared
admission policy.

Do not remove namespace finalizers or change shared cluster configuration to force
cleanup. Investigate failures and verify absence before declaring recovery. If the
entire ephemeral cluster is destroyed, record that fact instead of claiming a
namespace-deletion test passed. Deleting an owned namespace removes its scoped
identities and test routes. A replacement run gets new names and new credentials;
never reuse credentials from a previous cluster.

## Local verification

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/validate-openshift.py
git diff --check
sh scripts/scan-secrets.sh history
```

Offline tests verify defensive behavior and do not replace live evidence. CI runs
these checks without cluster credentials. Live validation is operator-triggered.

## Recorded validation: 2026-11-06

[Sanitized live report](evidence/2026-11-06-openshift.json) and
[workload security evidence](evidence/2026-11-06-workloads.json) record the run
against OpenShift 4.22.20 / Kubernetes v1.35.9.
The tested harness source is commit `4cc7954`.

- 33 live checks passed, followed by verified deletion of both namespaces
  and the run-owned admission binding/policy.
- The two deployment-readiness observations were 6.05 and 7.4 seconds.
  These are warm-image observations; an earlier run observed roughly 25 seconds.
- Both workloads used the pinned image digest, `restricted-v2`, and assigned
  non-root UIDs; see the workload evidence for the exact values.
- The 600-second credential was rejected at 65.7 seconds after
  its declared expiration, with its service account/RBAC still present. A prior
  +5-second test failed, exposing the clock-skew allowance documented above.
- Failure-path cleanup was also observed after the initial route-readiness failure
  and the earlier expiry-boundary failure. These are not represented as passing
  end-to-end runs.
- Ten offline tests, Python syntax, whitespace, and local Markdown links passed.
  Kubernetes reference links returned HTTP 200. Red Hat's documentation endpoint
  returned HTTP 403 to the automated link check; its search-indexed official
  reference was located, but full-page retrieval could not be verified.
- Local working-tree, staged, and history secret scans passed. CI also runs
  offline tests and secret scanning without cluster credentials.

## References

- [OpenShift 4.22 network policy](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/network_security/network-policy)
- [Kubernetes service accounts](https://kubernetes.io/docs/concepts/security/service-accounts/)
- [Token duration can differ from the request](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/)
- [Kubernetes validating admission policies](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)

- [Kubernetes service-account claim validation](https://github.com/kubernetes/kubernetes/blob/master/pkg/serviceaccount/claims.go)
- [go-jose default expiry leeway](https://github.com/go-jose/go-jose/blob/main/jwt/validation.go)
