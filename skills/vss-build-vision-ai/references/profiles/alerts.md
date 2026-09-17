# Alerts Developer Profile

## Capabilities and routing cues

- `2d_cv`: RT-CV detections, behavior analytics, VLM verification, incidents,
  and alert APIs.
- `2d_vlm`: continuous RT-VLM inspection and real-time alert APIs.
- Choose for alert verification, incident reporting, or live VLM alerts.

## Profile Service Set

Authoritative source:
`deploy/docker/developer-profiles/dev-profile-alerts/overrides.env`.

`2d_cv`:

```text
vss-behavior-analytics-alerts,nvstreamer-alerts,perception-alerts,kibana-init-container-alerts,vss-video-analytics-api,vss-agent,alert-bridge,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,rtvi-vlm,vss-ui,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG}
```

`2d_vlm`:

```text
nvstreamer-alerts,kibana-init-container-alerts,vss-video-analytics-api,vss-agent,alert-bridge,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,rtvi-vlm,vss-ui,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG}
```

Neither stock mode selects `vss-va-mcp`. Add that key only in a delta that
explicitly requests the legacy video-analytics MCP interface, including the SOP
report flow documented by `services/sop.md`.

## Capability owners present

| Owner | Service profile keys |
|---|---|
| Alerts | `alert-bridge`, `vss-video-analytics-api` |
| Behavior analytics | `vss-behavior-analytics-alerts` (`2d_cv`) |
| RT-CV | `perception-alerts` (`2d_cv`) |
| RT-VLM | `rtvi-vlm` (both modes: `2d_cv` per-clip verification, `2d_vlm` real-time) |
| ELK | `elasticsearch`, `elasticsearch-init-container`, `kafka`, `kafka-topic-init-container`, `redis`, `kibana`, `logstash`, `broker-health-check`, `kibana-init-container-alerts` |
| VIOS | `nvstreamer-alerts`, `centralizedb`, `vst-ingress`, `sensor-ms`, `streamprocessing-ms` |
| Agent/UI | `vss-agent`, `vss-ui`, `phoenix` |
| Legacy analytics MCP (explicit delta only) | `vss-va-mcp` |
| Ingress | `vss-haproxy-ingress` |
| LLM NIM | `llm_${LLM_MODE}_${LLM_NAME_SLUG}` |

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `MODE` | Select `2d_cv` or `2d_vlm`; keep `COMPOSE_PROFILES` aligned with the matching checked-in set. |
| `VST_NOTIFICATION_CONFIG_PATH` | MODE-selected VIOS webhook config (`notification_config_${MODE}.json`): `2d_cv` → RT-CV stream add/remove; `2d_vlm` → Alert Bridge always-on. |
| `ALERT_AGENT_ALWAYS_ON` | Gate always-on rules (`true` for real-time / `2d_vlm`, `false` for verification / `2d_cv`). |
| `DS_MODEL_FAMILY`, `MODEL_NAME_2D`, `RT_CV_DEVICE_ID`, `VSS_RT_CV_TAG` | Configure RT-CV in `2d_cv`. |
| `VLM_NAME`, `VLM_MODE`, `VLM_BASE_URL`, `RTVI_VLM_*` | Configure verification or real-time VLM routing. |
| `VLM_AS_VERIFIER_CONFIG_FILE*` | Select mounted alert verifier and real-time rule configs. |
| `ALERT_BRIDGE_HOST_PORT`, `VIDEO_ANALYTICS_API_HOST_PORT`, `RTVI_CV_HOST_PORT`, `RTVI_VLM_PORT` | Change alert-facing host ports. |
| `VSS_VA_MCP_HOST_PORT` | Change the optional legacy VA-MCP host port when that service is explicitly selected. |
| `NVSTREAMER_HTTP_HOST_PORT` | Select source playback host port. |

## Stock readiness checks

Derive probes from `resolved.yml`; do not probe an omitted service. Both stock
modes select the Agent, analytics API, Alert Bridge, UI, and RT-VLM:

```bash
services=$(docker compose -f "$BUILD_DIR/resolved.yml" config --services)
if grep -qx vss-agent <<<"$services"; then
  curl -sf "http://${HOST_IP}:8000/health"
fi
if grep -qx vss-video-analytics-api <<<"$services"; then
  curl -sf "http://${HOST_IP}:${VIDEO_ANALYTICS_API_HOST_PORT:-8081}/livez"
fi
if grep -qx alert-bridge <<<"$services"; then
  curl -sf "http://${HOST_IP}:${ALERT_BRIDGE_HOST_PORT:-9080}/health"
fi
if grep -qx vss-va-mcp <<<"$services"; then
  curl -sf "http://${HOST_IP}:${VSS_VA_MCP_HOST_PORT:-9901}/health"
fi
if grep -qx vss-ui <<<"$services"; then
  curl -sf "http://${HOST_IP}:3000/"
fi
```

A NemoClaw or host-CLI delta omits both `vss-agent` and `vss-va-mcp`, so neither
`:8000` nor `:9901` is probed. The API and Alert Bridge probes still apply.

For `2d_cv`, also require `vss-rtvi-cv`, `vss-behavior-analytics`, and the
verification `rtvi-vlm` to resolve; probe
`http://${HOST_IP}:${RTVI_CV_HOST_PORT:-9010}/api/v1/ready`, requiring
HTTP 200 and `ready-info.ds-ready=YES`, and
`http://${HOST_IP}:8018/v1/health/ready`.
For `2d_vlm`, require `vss-rtvi-cv` and `vss-behavior-analytics` to be absent and
probe `http://${HOST_IP}:8018/v1/health/ready`.

## Sources

- `deploy/docker/developer-profiles/dev-profile-alerts/.env`
- `deploy/docker/developer-profiles/dev-profile-alerts/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/services/alert/compose.yml`
- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/rtvi/rtvi-cv/compose.yaml`
- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `skills/operations/vss-manage-alerts/references/integrate-alerts.md`
- `skills/deployment/vss-setup-behavior-analytics/references/configuration.md`
