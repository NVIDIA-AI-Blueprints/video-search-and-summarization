# AGENTS.md

## Scope

Applies to `vss-setup-video-analytics-api`, the standalone video analytics API
deployment skill.

## Rules

- Use this skill for the REST service around Elasticsearch/Kafka analytics
  infrastructure, not for VA-MCP read-only queries.
- Read the configuration reference before changing server port, Elasticsearch,
  Kafka, or app-level tuning knobs.
- Validate custom config JSON before launch and preserve user-provided backend
  endpoints.
- Surface Elasticsearch/Kafka connection retries as dependency readiness
  issues, not generic application failures.

## Eval Behavior

- Report service health, `/docs` or endpoint probe output, and backend
  connectivity evidence.
