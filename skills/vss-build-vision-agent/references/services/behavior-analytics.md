# Behavior Analytics Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Alerts behavior rules | `vss-behavior-analytics-alerts` |
| Search analytics | `vss-search-analytics-2d-fusion` |

## Required peers

- Requires the matching profile-owned JSON config mounted by its extending
  service.
- Kafka-backed configs require `kafka`, `kafka-topic-init-container`, and
  `broker-health-check`.
- Alerts mode consumes RT-CV events; Search mode consumes the Search perception
  pipeline. Do not activate both variants for a single capability.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_BEHAVIOR_ANALYTICS_IMAGE`, `VSS_BEHAVIOR_ANALYTICS_TAG` | Select the Behavior Analytics image. |
| `VSS_APPS_DIR` | Resolve the profile-owned mounted JSON config. |
| `STREAM_TYPE` | Select the checked-in Kafka or Redis Search config where supported. |

Incident rules, broker addresses, thresholds, and sensor settings are fields in
the mounted JSON config, not Compose environment knobs. A requested rule change
there is a config change outside this env-only contract.

## Sources

- `deploy/docker/services/analytics/behavior-analytics/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`
- `skills/vss-setup-behavior-analytics/references/configuration.md`
- `skills/vss-setup-behavior-analytics/references/deploy-behavior-analytics-service.md`
