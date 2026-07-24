# Base Developer Profile

## Capabilities and routing cues

- General VSS agent, UI, video ingest/storage, LLM, and integrated RT-VLM.
- Choose for "deploy VSS", general video understanding, or the smallest
  developer foundation.
- Prefer as the Foundation when the request needs agent + VIOS + inference but no
  ELK-backed alerts, search, or long-video summarization.

## Profile Service Set

Authoritative source:
`deploy/docker/developer-profiles/dev-profile-base/overrides.env`.

```text
phoenix,redis,vss-haproxy-ingress,vss-ui,vss-agent,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG},rtvi-vlm
```

## Capability owners present

| Owner | Service profile keys |
|---|---|
| Agent | `vss-agent`, `vss-ui`, `vss-haproxy-ingress`, `phoenix` |
| VIOS | `centralizedb`, `vst-ingress`, `sensor-ms`, `streamprocessing-ms` |
| LLM NIM | `llm_${LLM_MODE}_${LLM_NAME_SLUG}` |
| RT-VLM | `rtvi-vlm` |

`redis` is a shared peer used by this profile graph.

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `HARDWARE_PROFILE` | Select current hardware defaults. |
| `LLM_MODE`, `LLM_NAME`, `LLM_NAME_SLUG`, `LLM_DEVICE_ID`, `LLM_BASE_URL` | Select and place the LLM. |
| `VLM_MODE`, `VLM_NAME`, `RT_VLM_DEVICE_ID`, `VLM_BASE_URL` | Select and place integrated or remote VLM inference. |
| `RTVI_VLM_KAFKA_ENABLED` | Keep `false` unless a broker is added. |
| `VSS_APPS_DIR`, `VSS_DATA_DIR`, `HOST_IP`, `EXTERNAL_IP` | Resolve repository, data, and network paths. |
| `HAPROXY_HOST_PORT`, `VSS_PUBLIC_*`, `VSS_*_HOST_PORT` | Change public ingress or host port bindings. |

## Stock readiness checks

```bash
curl -sf "http://${HOST_IP}:8000/health"
curl -sf "http://${HOST_IP}:8018/v1/health/ready"
curl -sf "http://${HOST_IP}:${LLM_PORT:-30081}/v1/health/ready"
curl -sf "http://${HOST_IP}:3000/"
```

For remote LLM/VLM mode, probe the selected remote `/v1/models` endpoint
instead of the absent local service.

## Sources

- `deploy/docker/developer-profiles/dev-profile-base/.env`
- `deploy/docker/developer-profiles/dev-profile-base/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-base/compose.yml`
- `deploy/docker/services/agent/compose.yml`
- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `deploy/docker/services/vios/compose.yml`
