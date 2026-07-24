# LVS Developer Profile

## Capabilities and routing cues

- Long-video summarization, timestamped highlights, structured summaries, and
  report generation.
- Uses RT-VLM for VLM serving and ELK/Kafka for event flow.
- Choose for video summarization or LVS requests.

## Profile Service Set

Authoritative source:
`deploy/docker/developer-profiles/dev-profile-lvs/overrides.env`.

```text
kibana-init-container-lvs,nvstreamer-lvs,vss-agent,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,rtvi-vlm,vss-ui,lvs-server,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG}
```

## Capability owners present

| Owner | Service profile keys |
|---|---|
| LVS | `lvs-server` |
| RT-VLM | `rtvi-vlm` |
| ELK | `elasticsearch`, `elasticsearch-init-container`, `kafka`, `kafka-topic-init-container`, `redis`, `kibana`, `logstash`, `broker-health-check`, `kibana-init-container-lvs` |
| VIOS | `nvstreamer-lvs`, `init-dirs`, `render-config`, `wdm-env-from-config`, `wait-for-redis`, `wait-for-docker-workloads`, `sdr-controller`, `centralizedb`, `vst-ingress`, `sensor-ms`, `streamprocessing-ms` |
| Agent | `vss-agent`, `vss-ui`, `vss-haproxy-ingress`, `phoenix` |
| LLM NIM | `llm_${LLM_MODE}_${LLM_NAME_SLUG}` |

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `LVS_TAG`, `BACKEND_HOST_PORT`, `LVS_MCP_HOST_PORT`, `LVS_ENABLE_MCP` | Select the image and exposed LVS APIs. |
| `LVS_DATABASE_BACKEND`, `LVS_EMB_*` | Configure the supported summary database and optional text-embedding endpoint. |
| `KAFKA_ENABLED`, `KAFKA_STRUCTURED_SUMMARY_TOPIC`, `LVS_ENABLE_LLM_MERGING` | Configure summary event flow. |
| `ENABLE_AUDIO` | Enable audio-aware summarization when the selected VLM supports it. |
| `VLM_NAME`, `VLM_BASE_URL`, `RTVI_VLM_MODEL_PATH`, `RTVI_VLM_MODEL_TO_USE` | Keep the LVS-requested model id aligned with RT-VLM. |
| `RT_VLM_DEVICE_ID`, `RTVI_VLLM_GPU_MEMORY_UTILIZATION`, `RTVI_VLM_MAX_MODEL_LEN` | Place and size RT-VLM. |

## Stock readiness checks

```bash
curl -sf "http://${HOST_IP}:8000/health"
curl -sf "http://${HOST_IP}:38111/v1/ready"
curl -sf "http://${HOST_IP}:8018/v1/health/ready"
curl -sf "http://${HOST_IP}:${LLM_PORT:-30081}/v1/health/ready"
```

Skip a local model probe only when that model is explicitly remote, and probe
the selected remote `/v1/models` endpoint instead.

## Sources

- `deploy/docker/developer-profiles/dev-profile-lvs/.env`
- `deploy/docker/developer-profiles/dev-profile-lvs/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-lvs/compose.yml`
- `deploy/docker/services/video-summarization/compose.yml`
- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `skills/vss-summarize-video/references/video-summarization-environment-variables.md`
