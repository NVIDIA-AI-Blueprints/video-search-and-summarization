# Alerts Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Alert verification and real-time bridge | `alert-bridge` |
| Video-analytics MCP | `vss-va-mcp` |
| Alerts analytics API | `vss-video-analytics-api-alerts` |

## Required peers

- `alert-bridge` requires Kafka, Elasticsearch, topic initialization, and the
  matching checked-in alert config mounts.
- CV verification requires RT-CV and Behavior Analytics.
- Real-time alerts require RT-VLM.
- `vss-va-mcp` requires the matching Agent config and reachable VST/ELK
  endpoints.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `ALERT_BRIDGE_HOST_PORT`, `ALERT_BRIDGE_PORT` | Publish and bind the alert API. |
| `VLM_BASE_URL`, `VLM_NAME`, `VLM_MODE` | Configure the verification VLM. |
| `RTVI_VLM_BASE_URL`, `RTVI_VLM_MODEL_TO_USE` | Configure real-time VLM alerts. |
| `VLM_AS_VERIFIER_CONFIG_FILE`, `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME`, `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` | Select mounted verifier/rule configs. |
| `HOST_IP`, `EXTERNAL_IP`, `VST_INTERNAL_URL` | Configure media URL routing. |
| `VSS_VA_MCP_HOST_PORT`, `VSS_VA_MCP_PORT`, `VSS_VA_MCP_CONFIG_FILE` | Configure video-analytics MCP. |
| `MDX_PORT`, `VSS_VIDEO_ANALYTICS_API_IMAGE`, `VSS_VIDEO_ANALYTICS_API_TAG` | Configure the alerts analytics API. |

## Sources

- `deploy/docker/services/alert/compose.yml`
- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/analytics/video-analytics-api/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `skills/vss-manage-alerts/references/integrate-alerts.md`
- `skills/vss-manage-alerts/references/deploy-alerts.md`
