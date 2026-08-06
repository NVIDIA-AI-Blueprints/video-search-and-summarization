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
- The object-class filter keys in the mounted config
  (`fovCountViolationIncidentObjectType`, `stateManagementFilter`) must match the
  class-label taxonomy the resolved RT-CV detector emits — label set and casing.
  In a combined build these follow the single converged detector, not the value
  a source profile's config happened to ship.
- To serve more than one capability at once, run a single combined instance
  rather than two, under the selected Foundation's key for the one
  `vss-behavior-analytics` container, never both. Mount the shipped joint config
  `services/analytics/behavior-analytics/configs/search_and_alerts_config.json`
  **verbatim as a drop-in** via a service-definition patch — do **not** hand-merge
  the single-mode configs or hand-edit it (no developer profile mounts it by
  default; the setup skill owns the recipe). It already encodes the combined
  settings: its `numWorkersFor*` knobs gate each processor (incident generation for
  detection-rule alerts, behavior creation for search analytics, embed filtering for
  search embeddings), and its topic set unions all enabled paths — including
  `frames`.
- A combined instance writes more than one Elasticsearch index family, so its
  Kibana initializer must seed all of them — see `elk.md` (Kibana seeding).

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
