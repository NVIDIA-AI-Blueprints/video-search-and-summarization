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
vss-behavior-analytics-alerts,nvstreamer-alerts,perception-alerts,kibana-init-container-alerts,vss-video-analytics-api-alerts,vss-va-mcp,vss-agent,alert-bridge,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,vss-ui,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG}
```

`2d_vlm`:

```text
nvstreamer-alerts,kibana-init-container-alerts,vss-video-analytics-api-alerts,vss-va-mcp,vss-agent,alert-bridge,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,rtvi-vlm,vss-ui,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG}
```

## Capability owners present

| Owner | Service profile keys |
|---|---|
| Alerts | `alert-bridge`, `vss-va-mcp`, `vss-video-analytics-api-alerts` |
| Behavior analytics | `vss-behavior-analytics-alerts` (`2d_cv`) |
| RT-CV | `perception-alerts` (`2d_cv`) |
| RT-VLM | `rtvi-vlm` (`2d_vlm`) |
| ELK | `elasticsearch`, `elasticsearch-init-container`, `kafka`, `kafka-topic-init-container`, `redis`, `kibana`, `logstash`, `broker-health-check`, `kibana-init-container-alerts` |
| VIOS | `nvstreamer-alerts`, `init-dirs`, `render-config`, `wdm-env-from-config`, `wait-for-redis`, `wait-for-docker-workloads`, `sdr-controller`, `centralizedb`, `vst-ingress`, `sensor-ms`, `streamprocessing-ms` |
| Agent | `vss-agent`, `vss-ui`, `vss-haproxy-ingress`, `phoenix` |
| LLM NIM | `llm_${LLM_MODE}_${LLM_NAME_SLUG}` |

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `MODE` | Select `2d_cv` or `2d_vlm`; keep `COMPOSE_PROFILES` aligned with the matching checked-in set. |
| `DS_MODEL_FAMILY`, `MODEL_NAME_2D`, `RT_CV_DEVICE_ID`, `PERCEPTION_TAG` | Configure RT-CV in `2d_cv`. |
| `VLM_NAME`, `VLM_MODE`, `VLM_BASE_URL`, `RTVI_VLM_*` | Configure verification or real-time VLM routing. |
| `VLM_AS_VERIFIER_CONFIG_FILE*` | Select mounted alert verifier and real-time rule configs. |
| `ALERT_BRIDGE_HOST_PORT`, `VSS_VA_MCP_HOST_PORT`, `RTVI_CV_HOST_PORT`, `RTVI_VLM_PORT` | Change alert-facing host ports. |
| `SDR_CONTROLLER_CONFIG_PATH`, `NVSTREAMER_HTTP_HOST_PORT` | Select rendered stream routing and source playback. |

## Stock readiness checks

Both modes:

```bash
curl -sf "http://${HOST_IP}:8000/health"
curl -sf "http://${HOST_IP}:9080/health"
curl -sf "http://${HOST_IP}:9901/health"
curl -sf "http://${HOST_IP}:3000/"
```

For `2d_cv`, also require `vss-rtvi-cv` and `vss-behavior-analytics` to
resolve and probe `http://${HOST_IP}:${RTVI_CV_HOST_PORT:-9010}/v1/health`.
For `2d_vlm`, require those two services to be absent and probe
`http://${HOST_IP}:8018/v1/health/ready`.

## Operator-facing views

After readiness passes, tell the operator how to *look* at this profile. Two
paths, both view-only — neither is a place to create or delete alert rules
(that stays in `vss-manage-alerts` Workflow D).

**1. VSS-UI, deployed with this profile.** `vss-ui` is in both mode service
sets and `vss-haproxy-ingress` fronts it, so the tabs and the alert/VIOS APIs
share one origin. Tabs are chosen at deploy time by
`deploy/docker/services/ui/compose.yml` env, resolved from the profile's `.env`
/ `overrides.env`:

| Knob | Default | For this profile |
|---|---|---|
| `NEXT_PUBLIC_ENABLE_ALERTS_TAB` | `true` | keep on |
| `NEXT_PUBLIC_ENABLE_DASHBOARD_TAB` | `true` | Kibana embed; on when `kibana` is up |
| `NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB` | `true` | sensor onboarding |
| `NEXT_PUBLIC_ENABLE_SEARCH_TAB` | `false` | leave off — no embedding index here |
| `NEXT_PUBLIC_ALERTS_TAB_DEFAULT_AUTO_REFRESH_IN_MILLISECONDS` | `1000` | incident poll cadence |

Deep-link a tab with the `#vss-mt-<tabId>` hash — `Home.tsx` resolves it on
first load and on `hashchange`, and tab ids are `chat`, `search`, `alerts`,
`dashboard`, `map`, `video-management`:

```text
http://${HOST_IP}:${VSS_PUBLIC_PORT:-7777}/#vss-mt-alerts
```

Report the ingress origin, not `:3000` — `:3000` is the UI container's own port
and bypasses the shared-origin routing the tabs' API calls rely on.

**2. Generated view artifacts — no UI needed.** `tools/vss-view` renders a
self-contained HTML file (live-polling or static) from a JSON spec the operate
skills emit. Point the operator at it when the Agent/UI layer is dropped in a
delta, when a shareable artifact is wanted, or when the viewer cannot reach the
deployment's UI port. See `skills/vss-manage-alerts/references/view-artifacts.md`.

## Sources

- `deploy/docker/developer-profiles/dev-profile-alerts/.env`
- `deploy/docker/developer-profiles/dev-profile-alerts/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/services/alert/compose.yml`
- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/rtvi/rtvi-cv/compose.yaml`
- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `skills/vss-manage-alerts/references/integrate-alerts.md`
- `skills/vss-setup-behavior-analytics/references/configuration.md`
