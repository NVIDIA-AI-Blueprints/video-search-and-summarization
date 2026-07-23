# Search Developer Profile

## Capabilities and routing cues

- Video ingest, RT-CV detection/tracking, RT-Embed video/text embeddings,
  Elasticsearch retrieval, and optional VLM critique.
- Choose for natural-language video search or combined ingestion + detection +
  embedding requests.

## Profile Service Set

Authoritative source:
`deploy/docker/developer-profiles/dev-profile-search/overrides.env`.

```text
kibana-init-container-search,vss-search-analytics-2d-fusion,vss-video-analytics-api-fusion,nvstreamer-2d-fusion,perception-2d-init,perception-2d-fusion,vss-agent,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,rtvi-embed,vss-ui,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG},vlm_${VLM_MODE}_${VLM_NAME_SLUG}
```

## Capability owners present

| Owner | Service profile keys |
|---|---|
| Search | `vss-search-analytics-2d-fusion`, `vss-video-analytics-api-fusion` |
| RT-CV | `perception-2d-init`, `perception-2d-fusion` |
| RT-Embed | `rtvi-embed` |
| VLM NIM | `vlm_${VLM_MODE}_${VLM_NAME_SLUG}` |
| ELK | `elasticsearch`, `elasticsearch-init-container`, `kafka`, `kafka-topic-init-container`, `redis`, `kibana`, `logstash`, `broker-health-check`, `kibana-init-container-search` |
| VIOS | `nvstreamer-2d-fusion`, `init-dirs`, `render-config`, `wdm-env-from-config`, `wait-for-redis`, `wait-for-docker-workloads`, `sdr-controller`, `centralizedb`, `vst-ingress`, `sensor-ms`, `streamprocessing-ms` |
| Agent | `vss-agent`, `vss-ui`, `vss-haproxy-ingress`, `phoenix` |
| LLM NIM | `llm_${LLM_MODE}_${LLM_NAME_SLUG}` |

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `RT_CV_DEVICE_ID`, `RTVI_CV_HOST_PORT`, `MODEL_TYPE`, `MODEL_NAME_2D` | Configure the perception pipeline. |
| `RT_EMBED_DEVICE_ID`, `RTVI_EMBED_PORT`, `MODEL_PATH`, `HF_TOKEN` | Place and configure RT-Embed. |
| `ENABLE_CRITIC`, `VLM_MODE`, `VLM_NAME`, `VLM_NAME_SLUG`, `VLM_BASE_URL` | Enable and place optional result critique. |
| `COSMOS_EMBED_ENDPOINT`, `ELASTIC_SEARCH_ENDPOINT`, `ELASTIC_SEARCH_INDEX` | Wire the agent to embedding and retrieval services. |
| `ELASTICSEARCH_ENABLE_EMBEDDINGS`, `ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM`, `ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM` | Configure indexed vectors. |
| `LLM_DEVICE_ID`, `VLM_DEVICE_ID`, `RESERVED_DEVICE_IDS`, `FIXED_SHARED_DEVICE_IDS` | Preserve the intended multi-GPU layout. |

## Stock readiness checks

```bash
curl -sf "http://${HOST_IP}:8000/health"
curl -sf "http://${HOST_IP}:8017/v1/ready"
curl -sf "http://${HOST_IP}:${RTVI_CV_HOST_PORT:-9000}/v1/health"
curl -sf "http://${HOST_IP}:9200/_cluster/health"
curl -sf "http://${HOST_IP}:3000/"
```

When `ENABLE_CRITIC=true`, also probe the selected VLM `/v1/models` endpoint.

## Sources

- `deploy/docker/developer-profiles/dev-profile-search/.env`
- `deploy/docker/developer-profiles/dev-profile-search/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-search/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`
- `deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml`
- `deploy/docker/services/rtvi/rtvi-cv/compose.yaml`
- `skills/vss-deploy-video-embedding/references/environment.md`
- `skills/vss-deploy-detection-tracking-2d/references/environment.md`
