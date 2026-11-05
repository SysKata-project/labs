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

No runtime or infrastructure provider has been selected, and no lab launcher exists yet. Proposals should include the skills supported, environment lifecycle, assessment behavior, and failure recovery. Coordinate contracts in [docs](https://github.com/SysKata-project/docs).

## Status

Planning scaffold only; listed capabilities are planned.

## License

Copyright 2026 SysKata contributors.

This repository, including its code, documentation, and learning content, is licensed under the [Apache License 2.0](LICENSE).
