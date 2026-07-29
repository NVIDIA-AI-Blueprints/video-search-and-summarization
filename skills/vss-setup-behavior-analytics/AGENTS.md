# AGENTS.md

## Scope

Applies to `vss-setup-behavior-analytics`, the standalone behavior analytics
deployment skill.

## Rules

- Use this skill for standalone `vss-behavior-analytics`, not for the full
  warehouse profile.
- Select the entrypoint, config source, calibration source, and broker
  availability before editing compose or launching.
- `VSS_APPS_DIR` must point at `deploy/docker` for compose volume binds.
- Kafka, Redis Streams, or MQTT are optional but required for dynamic config and
  dynamic calibration flows.
- If a broker is absent, explain restart behavior instead of treating it as an
  unrelated container crash.

## Eval Behavior

- Follow `references/deploy-behavior-analytics-service.md` in order.
- Verify the actual service endpoint and dynamic update path named by the spec.
