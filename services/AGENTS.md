# AGENTS.md

Router for source code under `services/`.

## Scope

- More specific `AGENTS.md` or `CLAUDE.md` files under a service take
  precedence.
- Read the target service guide only; do not load sibling service context unless
  the change crosses that service boundary.

## Routing

| Path | Read next |
|---|---|
| `services/agent/**` | `services/agent/AGENTS.md` |
| `services/analytics/behavior-analytics/**` | `services/analytics/behavior-analytics/AGENTS.md` |
| `services/analytics/video-analytics-api/**` | `services/analytics/video-analytics-api/AGENTS.md` |
| `services/ui/**` | `services/ui/README.md` and `services/ui/CONTRIBUTING.md` |
| `services/video-summarization/**` | `services/video-summarization/README.md` |
| `services/vios/**` | `services/vios/README.md` |
| `services/rtvi/**` | Nearest README under `services/rtvi/` |
| Other service paths | Nearest service README |

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
- For cross-service behavior, validate each touched service and the deployment
  config that wires them together.
