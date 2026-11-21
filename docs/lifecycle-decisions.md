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
