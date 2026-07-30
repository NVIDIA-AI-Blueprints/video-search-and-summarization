# AGENTS.md

Scoped guidance for source code under `services/`.

## Scope

- This file routes service changes. More specific `AGENTS.md` or `CLAUDE.md`
  files under a service take precedence for that service.
- Prefer the service README and local package metadata over assumptions from
  other services.

## Routing

- Agent service: read `services/agent/AGENTS.md` and
  `services/agent/README.md`.
- Behavior analytics: read `services/analytics/behavior-analytics/AGENTS.md`.
- Video analytics API: read `services/analytics/video-analytics-api/AGENTS.md`.
- UI: read `services/ui/README.md` and `services/ui/CONTRIBUTING.md`.
- Long video summarization: read `services/video-summarization/README.md`.
- VIOS: read `services/vios/README.md`.
- RT video intelligence services: read the nearest README under
  `services/rtvi/`.
- Alert, SDRC, and configurator services: start with the nearest service
  README before changing code.

## Source Rules

- Keep service boundaries intact. Shared deployment wiring belongs under
  `deploy/`; service runtime code belongs under its service directory.
- Preserve each service's package manager, formatter, test runner, and language
  conventions.
- Update tests and API/spec docs when behavior, schemas, endpoints, or public
  configuration change.
- Do not hardcode hostnames, ports, credentials, or model names when the service
  already supports configuration.

## Validation

- Run the narrowest service-level test or lint command documented by the
  service guide.
- For cross-service behavior, validate each touched service plus the deployment
  config that wires them together.
