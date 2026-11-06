# SysKata labs

Practice environment and assessment tooling for SysKata's hands-on technical challenges.

## Intended scope

- Reproducible environments for Linux administration and automation exercises.
- Session provisioning, readiness checks, reset, expiry, and teardown.
- Assessment runners that inspect observable task outcomes.
- Structured results with actionable feedback for learners.
- Environment definitions and reusable assessment utilities.

## Component boundaries

The [API](https://github.com/SysKata-project/api) requests sessions and records learner progress. The [content repository](https://github.com/SysKata-project/content) defines challenge objectives, setup requirements, and assessment criteria. This repository owns the machinery to provision and assess labs, including environment-specific execution adapters.

## Design requirements

Learners need freedom to practice administrative operations inside isolated, disposable environments. The architecture must define privilege boundaries, network access, resource limits, session expiry, and reliable cleanup. Container versus virtual machine execution remains an open decision, based on the skills each exercise requires.

## Proposed first milestone

Provision a single reproducible exercise environment, run an outcome-based assessment, return structured feedback, and tear the environment down. Document required images and access prerequisites without redistributing restricted software or credentials.

## Development and contribution

An ephemeral OpenShift feasibility harness is available; no lab launcher exists yet. Proposals should include the skills supported, environment lifecycle, assessment behavior, and failure recovery. Coordinate contracts in [docs](https://github.com/SysKata-project/docs).

## Implementation design

- [OpenShift lab execution](docs/openshift-labs.md)
- [Repeatable runtime feasibility and accepted test budget](docs/feasibility.md)

## Status

Feasibility validation is in progress. The lifecycle service, terminal gateway and assessment workers remain planned.

## MVP implementation tracking

See the [OpenShift MVP tracker](https://github.com/SysKata-project/docs/issues/1) and [cross-repository backlog](https://github.com/SysKata-project/docs/blob/main/plans/mvp-backlog.md).

## License

Copyright 2026 SysKata contributors.

This repository, including its code, documentation, and learning content, is licensed under the [Apache License 2.0](LICENSE).
