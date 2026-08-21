# Alerts Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Alert verification and real-time bridge | `alert-bridge` |
| Video-analytics MCP | `vss-va-mcp` |
| Alerts analytics API | `vss-video-analytics-api-alerts` |

`vss-video-analytics-api-alerts` is the **same** single `vss-video-analytics-api`
container that other Foundations expose under their own key; only one exists per
build. When included, use `vss-video-analytics-api-alerts` **only when the
selected Foundation is `alerts`**; on any other Foundation the analytics API is
that Foundation's key — never introduce the `-alerts` key to add a second key for
the one container.

## Required peers

- `alert-bridge` requires Kafka, Elasticsearch, topic initialization, Redis, and
  **no service-definition patch**: the stock definition carries the `alert-bridge`
  profile gate, reads `VLM_BASE_URL`/`VLM_NAME` from env, and mounts its verifier
  configs from env-interpolated sources (`VLM_AS_VERIFIER_CONFIG_FILE*`). Wire it in
  `override.env` — add `alert-bridge` to `COMPOSE_PROFILES` and point those
  mount-source vars at the checked-in alerts verifier configs (not inherited on a
  non-`alerts` Foundation); do **not** author an `alert-bridge.yml` patch.
- **CV verification** (`MODE=2d_cv`): RT-CV (`perception-alerts`) feeds Behavior
  Analytics (`vss-behavior-analytics-alerts`), which emits candidate incidents;
  `alert-bridge` verifies clips with a VLM. Requires RT-CV + Behavior Analytics
  with incident generation enabled. `rtvi-vlm` still runs (verification backend).
- **VLM real-time** (`MODE=2d_vlm`): `alert-bridge` realtime / always-on rules
  drive `rtvi-vlm` over live media. No Behavior Analytics or CV candidate stage.
- Alerts VIOS is **direct** (no `sdr-controller`): stream add/remove reaches RT-CV
  or Alert Bridge via MODE-selected VIOS webhook configs under
  `developer-profiles/dev-profile-alerts/vios/configs/notification_config_${MODE}.json`
  (`VST_NOTIFICATION_CONFIG_PATH`).
- When Behavior Analytics also serves another capability on one shared instance
  (a combined build), it runs as **one** shared instance, not two — converge its
  single mounted JSON config per [`behavior-analytics.md`](behavior-analytics.md);
  its `numWorkersFor*` gates are not env-expressible.
- `vss-va-mcp` requires the matching Agent config and reachable VST/ELK
  endpoints.

## Stream lifecycle (VIOS webhooks)

| `MODE` | Webhook config | `camera_streaming` / `camera_remove` target |
|---|---|---|
| `2d_cv` | `notification_config_2d_cv.json` | RT-CV `POST …/api/v1/stream/add` / `…/stream/remove` (`:9010`) |
| `2d_vlm` | `notification_config_2d_vlm.json` | Alert Bridge `POST …/api/v1/realtime/always-on` (`:9080`) |

Always-on is gated by `ALERT_AGENT_ALWAYS_ON` (substituted into
`alert_agent.always_on` in the verifier config): **true** for real-time /
`2d_vlm`, **false** for verification / `2d_cv`. When enabled, Alert Bridge must
render a valid always-on rules file at boot.

## Write-path topic flow

A build resolves exactly one alerts mode; the two flows are mutually exclusive.
Surface the resolved flow in the architecture preview (SKILL.md step 6 requires
principal data flows and topics).

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
  This path has no `mdx-raw`/`mdx-incidents` candidate stage. Always-on rules start
  from the VIOS webhook path above when `ALERT_AGENT_ALWAYS_ON=true`.

The modes are exclusive: do not enable Behavior Analytics incident generation for
real-time alerts, and do not route CV verification through the real-time rule path.

## Alert Bridge config rendering

`alert-bridge` entrypoint (`env-substitute.py`) renders the required main verifier
YAML and, when present, the optional realtime / always-on YAML into `/app/runtime`
(including `${VLM_NAME}` in always-on rules). Keep `model: "${VLM_NAME}"` in
`realtime-config.yml` — do not hardcode the model id. `ALWAYS_ON_RULES_CONFIG`
points at the **rendered** file under `/app/runtime` when always-on is enabled.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `MODE` | Select `2d_cv` (verification) or `2d_vlm` (real-time); keep `COMPOSE_PROFILES` aligned. |
| `ALERT_AGENT_ALWAYS_ON` | Gate always-on (`true` for `2d_vlm`, `false` for `2d_cv`). |
| `VST_NOTIFICATION_CONFIG_PATH` | MODE-selected VIOS webhook config (`notification_config_${MODE}.json`). |
| `ALERT_BRIDGE_HOST_PORT`, `ALERT_BRIDGE_PORT` | Publish and bind the alert API. |
| `VLM_BASE_URL`, `VLM_NAME`, `VLM_MODE` | Configure the verification VLM (`VLM_NAME` must match RT-VLM `/v1/models`). |
| `RTVI_VLM_BASE_URL`, `RTVI_VLM_MODEL_TO_USE` | Configure real-time VLM alerts. |
| `VLM_AS_VERIFIER_CONFIG_FILE`, `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME`, `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` | Select mounted verifier/rule configs. |
| `HOST_IP`, `EXTERNAL_IP`, `VST_INTERNAL_URL` | Configure media URL routing. |
| `VSS_VA_MCP_HOST_PORT`, `VSS_VA_MCP_PORT`, `VSS_VA_MCP_CONFIG_FILE` | Configure video-analytics MCP. |
| `VIDEO_ANALYTICS_API_HOST_PORT`, `VSS_VIDEO_ANALYTICS_API_IMAGE`, `VSS_VIDEO_ANALYTICS_API_TAG` | Configure the alerts analytics API. |

## Sources

- `deploy/docker/services/alert/compose.yml`
- `deploy/docker/services/alert/scripts/env-substitute.py`
- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/analytics/video-analytics-api/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-alerts/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-alerts/vios/configs/notification_config_*.json`
- `deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/`
- `skills/vss-build-vision-agent/references/profiles/alerts.md`
- `skills/vss-manage-alerts/references/integrate-alerts.md`
- `skills/vss-manage-alerts/references/deploy-alerts.md`
