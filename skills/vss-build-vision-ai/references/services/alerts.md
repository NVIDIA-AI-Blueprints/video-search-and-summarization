# Alerts Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Alert verification and real-time bridge | `alert-bridge` |
| Video-analytics MCP | `vss-va-mcp` |
| Alerts analytics API | `vss-video-analytics-api` |

`vss-video-analytics-api` is a common Compose service with one profile key and one container name across all Foundations. Include that key when the build needs the REST query surface; never add a Foundation-specific alias or a second API instance.
