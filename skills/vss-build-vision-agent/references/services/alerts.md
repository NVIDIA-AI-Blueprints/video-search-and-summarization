# Alerts Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Alert verification and real-time bridge | `alert-bridge` |
| Video-analytics MCP | `vss-va-mcp` |
| Alerts analytics API | `vss-video-analytics-api-alerts` |

`vss-video-analytics-api-alerts` is the **same** `vss-video-analytics-api` container
as search's `vss-video-analytics-api-fusion`; only one exists per build. It is an
**exposed read surface**: like the ingress, include it only when the request asks to
expose a query/browse/REST-API surface, and prune it otherwise even though the
Foundation ships it. When included, use `vss-video-analytics-api-alerts` **only when
the selected Foundation is `alerts`**; on any other Foundation (e.g. a search-based
combined build) the analytics API is that Foundation's key — never introduce the
`-alerts` key to add a second key for the one container.

## Required peers

- `alert-bridge` requires Kafka, Elasticsearch, topic initialization, and the
  matching checked-in alert config mounts.
- CV-verification alerts derive from detections: RT-CV feeds Behavior Analytics,
  which generates incidents that a VLM then verifies. This path requires RT-CV
  and Behavior Analytics with its incident processor enabled.
- Real-time alerts derive from continuous VLM inspection of the media: the signal
  flows `rtvi-vlm` → `alert-bridge` and requires RT-VLM. This path does not use
  Behavior Analytics or incident generation.
- `vss-va-mcp` requires the matching Agent config and reachable VST/ELK
  endpoints.

## Write-path topic flow

A build resolves exactly one alerts mode; the two flows are mutually exclusive.
Surface the resolved flow in the architecture preview (SKILL.md step 6 requires
principal data flows and topics); both are authoritatively defined in
`skills/vss-manage-alerts/references/integrate-alerts.md`.

- **CV verification** (`perception-alerts` + `vss-behavior-analytics-alerts` +
  `alert-bridge`): `perception-alerts -> mdx-raw -> vss-behavior-analytics-alerts ->
  mdx-incidents` (candidate incidents) `-> alert-bridge` (retrieves the clip and runs
  the VLM verifier) `-> mdx-vlm-incidents` (verified). Alert Bridge writes the verified
  record with its `verdict` **directly to Elasticsearch** `mdx-vlm-incidents-*` and
  `mdx-vlm-alerts-*` (its `vlm_enhanced_sink`; optionally also to Kafka
  `mdx-vlm-incidents`). Requires RT-CV and Behavior Analytics with incident generation
  enabled.
- **VLM real-time** (`alert-bridge` realtime rules + `rtvi-vlm`, no Behavior
  Analytics): an `alert-bridge` realtime rule drives `rtvi-vlm` over the live stream;
  `rtvi-vlm -> mdx-vlm-incidents` (`RTVI_VLM_KAFKA_INCIDENT_TOPIC`) `-> Logstash ->
  Elasticsearch mdx-vlm-incidents-*`. RT-VLM produces the incident (confirmed at
  source); Alert Bridge orchestrates the rule but does **not** write Elasticsearch.
  This path has no `mdx-raw`/`mdx-incidents` candidate stage.

The modes are exclusive: do not enable Behavior Analytics incident generation for
real-time alerts, and do not route CV verification through the real-time rule path.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `ALERT_BRIDGE_HOST_PORT`, `ALERT_BRIDGE_PORT` | Publish and bind the alert API. |
| `VLM_BASE_URL`, `VLM_NAME`, `VLM_MODE` | Configure the verification VLM. |
| `RTVI_VLM_BASE_URL`, `RTVI_VLM_MODEL_TO_USE` | Configure real-time VLM alerts. |
| `VLM_AS_VERIFIER_CONFIG_FILE`, `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME`, `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` | Select mounted verifier/rule configs. An `alert-bridge` `2d_cv` delta on a Foundation that lacks them (e.g. `search`) must set `VLM_AS_VERIFIER_CONFIG_FILE` and `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` to the shipped `dev-profile-alerts/vlm-as-verifier/configs/{config.yml,alert_type_config.json}` so the mounts resolve to real files, never `/path/to/*`; `..._REALTIME` follows the alerts Foundation (`2d_cv` does not load it). |
| `HOST_IP`, `EXTERNAL_IP`, `VST_INTERNAL_URL` | Configure media URL routing. |
| `VSS_VA_MCP_HOST_PORT`, `VSS_VA_MCP_PORT`, `VSS_VA_MCP_CONFIG_FILE` | Configure video-analytics MCP. |
| `VIDEO_ANALYTICS_API_HOST_PORT`, `VSS_VIDEO_ANALYTICS_API_IMAGE`, `VSS_VIDEO_ANALYTICS_API_TAG` | Configure the alerts analytics API. |

## Sources

- `deploy/docker/services/alert/compose.yml`
- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/analytics/video-analytics-api/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `skills/vss-manage-alerts/references/integrate-alerts.md`
- `skills/vss-manage-alerts/references/deploy-alerts.md`
