# Search Developer Profile

## Capabilities and routing cues

- Video ingest, RT-CV detection/tracking, RT-Embed video/text embeddings,
  Elasticsearch retrieval, and agent-served RT-VLM critique / visual follow-up
  Q&A.
- Choose for natural-language video search or combined ingestion + detection +
  embedding requests.
- See `services/rt-cv.md` for detector model-family → Foundation mapping.

## Profile Service Set

Authoritative source:
`deploy/docker/developer-profiles/dev-profile-search/overrides.env`.

```text
kibana-init-container-search,vss-search-analytics-2d-fusion,vss-video-analytics-api-fusion,nvstreamer-2d-fusion,perception-2d-fusion,vss-agent,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,rtvi-embed,vss-ui,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,rtvi-vlm,llm_${LLM_MODE}_${LLM_NAME_SLUG}
```

## Capability owners present

| Owner | Service profile keys |
|---|---|
| Search | `vss-search-analytics-2d-fusion` |
| RT-CV | `perception-2d-fusion` |
| RT-Embed | `rtvi-embed` |
| RT-VLM | `rtvi-vlm` |
| ELK | `elasticsearch`, `elasticsearch-init-container`, `kafka`, `kafka-topic-init-container`, `redis`, `kibana`, `logstash`, `broker-health-check`, `kibana-init-container-search` |
| VIOS | `nvstreamer-2d-fusion`, `init-dirs`, `render-config`, `wdm-env-from-config`, `wait-for-redis`, `wait-for-docker-workloads`, `sdr-controller`, `centralizedb`, `vst-ingress`, `sensor-ms`, `streamprocessing-ms` |
| Agent | `vss-agent`, `vss-ui`, `phoenix` |
| Ingress | `vss-haproxy-ingress` |
| LLM NIM | `llm_${LLM_MODE}_${LLM_NAME_SLUG}` |

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `RT_CV_DEVICE_ID`, `RTVI_CV_HOST_PORT`, `DS_MODEL_FAMILY` | Configure the perception pipeline. |
| `VISION_ENCODER_MODEL`, `VISION_ENCODER_VERSION` | Select the vision encoder NGC artifact downloaded by ds-start phase 0; the checked-in RT-CV config uses the fixed RT-DETR warehouse artifact. |
| `RT_EMBED_DEVICE_ID`, `RTVI_EMBED_PORT`, `MODEL_PATH`, `HF_TOKEN` | Place and configure RT-Embed. |
| `VLM_NAME`, `VLM_BASE_URL`, `VLM_MODEL_TYPE`, `RTVI_VLM_*` | Wire the agent to RT-VLM for result critique and visual follow-up Q&A. Include these only when the build ships `rtvi-vlm` and the `vss-agent` tier that invokes it; critique is a per-request option (`use_critic`, default on), not a build-time flag — do not introduce an `ENABLE_CRITIC` delta. |
| `COSMOS_EMBED_ENDPOINT`, `ELASTIC_SEARCH_ENDPOINT`, `ELASTIC_SEARCH_INDEX` | Wire the agent to embedding and retrieval services. |
| `ELASTICSEARCH_ENABLE_EMBEDDINGS`, `ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM`, `ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM` | Configure indexed vectors. |
| `LLM_DEVICE_ID`, `RT_VLM_DEVICE_ID`, `RESERVED_DEVICE_IDS`, `FIXED_SHARED_DEVICE_IDS` | Preserve the intended multi-GPU layout. |

## Stock readiness checks

```bash
curl -sf "http://${HOST_IP}:8000/health"
curl -sf "http://${HOST_IP}:8017/v1/ready"
curl -sf "http://${HOST_IP}:8018/v1/health/ready"
curl -sf "http://${HOST_IP}:${RTVI_CV_HOST_PORT:-9000}/ready"
curl -sf "http://${HOST_IP}:9200/_cluster/health"
curl -sf "http://${HOST_IP}:3000/"
```

When the build ships `rtvi-vlm` and the `vss-agent` tier, also probe RT-VLM's
`/v1/models` endpoint. Skip this check for headless builds that omit `rtvi-vlm`.

## Sources

- `deploy/docker/developer-profiles/dev-profile-search/.env`
- `deploy/docker/developer-profiles/dev-profile-search/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-search/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`
- `deploy/docker/services/rtvi/rtvi-cv/ds-start.sh`
- `deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml`
- `deploy/docker/services/rtvi/rtvi-cv/compose.yaml`
- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `skills/vss-deploy-video-embedding/references/environment.md`
- `skills/vss-deploy-detection-tracking-2d/references/environment.md`
