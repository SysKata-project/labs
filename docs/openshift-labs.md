# OpenShift lab execution design

Status: planned MVP with an ephemeral OpenShift 4.22.20 feasibility spike. See
[validation scope and evidence](feasibility.md); the gateway and durable runtime
provider remain unimplemented/unselected.

## Pilot environments

Support two scenarios: foundations (deploy/expose/repair an application) and administration (declarative resources, namespaced RBAC and quotas). The curriculum source baseline is OpenShift 4.22; prove the chosen runtime can execute the selected scenarios and document any version differences before enabling them.

A shared disposable training cluster with an isolated project per learner is a candidate for namespace-scoped tasks, subject to verified RBAC, network isolation and resource quotas. It is not sufficient isolation for arbitrary cluster-administrator access. Cluster identity configuration, Operators, security constraints and cluster updates require disposable dedicated environments or remain unavailable. Do not substitute a simulated terminal while claiming a real OpenShift lab.

An early feasibility spike selects hosting, access entitlements, startup latency, per-session resource budget, maximum concurrency, cost ceiling and teardown guarantees. It must check route exposure, image availability, terminal access and authorization tests. Keep credentials and restricted images out of repositories.

## Lifecycle and authorization

Use provisioning → ready → active → assessing → completed, with explicit expired, failed and closed states. Persist lifecycle state and reconcile orphaned resources after process restarts. API requests use a stable session/generation ID and idempotent provision/reset/close operations. Reset creates a new generation; stale assessment results cannot complete it.

A gateway provides a shell containing oc and short-lived credentials scoped to the assigned project and required verbs. Bind every connection to learner/session, verify expiry on connection/reconnection, and revoke credentials on teardown. Restrict egress, privilege escalation and cross-project access. Keep learner credentials separate from trusted evaluator credentials.

Apply TTL, idle policy, resource quotas, rate limits, concurrency limits and maximum log/artifact sizes. Cleanup must survive worker failure, partial provisioning and expired sessions. Cluster-wide changes are not allowed through this profile.

## Assessment

Content supplies objective/check declarations; labs supplies allowlisted evaluator implementations and versioned fixtures. Inspect actual resource state and relevant behavior, not exact command history. Check readiness, selectors, route reachability, effective role access and quota configuration as appropriate. Include positive, negative and alternate-valid-solution fixtures.

Return assessment ID, lab session/generation, exercise instance, content/evaluator versions, status, criterion codes and bounded evidence. Authenticate delivery to the API; duplicate callbacks are safe. Distinguish learner failure from provisioning, connectivity and evaluator errors. An infrastructure error never consumes a life or confirms a failed learning attempt.

The API owns progression and rewards; lab workers cannot directly mutate those tables. Persist assessment jobs, retry delivery and reconcile lost callbacks. Provide structured logs/correlation IDs, queue age and cleanup metrics without logging tokens.

See [shared architecture](https://github.com/SysKata-project/docs/blob/main/architecture/learning-platform.md) and [curriculum plan](https://github.com/SysKata-project/content/blob/main/docs/openshift-path.md).
