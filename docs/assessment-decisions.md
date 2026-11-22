# Assessment worker decisions

Tracking: [labs #3](https://github.com/SysKata-project/labs/issues/3).

## Accepted by the owner, 2026-11-22

Run trusted assessment in a separate Java/Quarkus process, sharing the labs
database and using its own restricted OpenShift identity. Do not inject evaluator
credentials or result-delivery credentials into the terminal gateway process.

The existing Java quality gates, two-session ceiling and zero incremental cost
limit remain in effect. API #5 retains learner authorization, dispatch assignment,
result application and reward deduplication. Content release activation is not
implied by implementing a worker.

## Validation dependency

The previous ephemeral test installation and its credentials were removed.
The owner authorized reusing the previous claim; authentication succeeded and it
reports OpenShift 4.22.22. The owner explicitly approved the temporary three-policy controller/evaluator
installation, namespace-scoped Roles, two service accounts with two-hour
credentials and complete owned-resource cleanup after automatic approval review
requested authorization.
Offline fixtures must not be reported as successful live OpenShift validation.
