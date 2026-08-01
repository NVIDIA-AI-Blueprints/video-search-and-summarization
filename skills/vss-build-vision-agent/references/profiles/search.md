# Search Developer Profile

## Capabilities and routing cues

- Video ingest, RT-CV detection/tracking, RT-Embed video/text embeddings,
  Elasticsearch retrieval, and default-enabled VLM critique.
- Choose for natural-language video search or combined ingestion + detection +
  embedding requests.

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
| Agent | `vss-agent`, `vss-ui`, `vss-haproxy-ingress`, `phoenix` |
| LLM NIM | `llm_${LLM_MODE}_${LLM_NAME_SLUG}` |

## Profile-specific environment knobs

| Knob | Purpose |
|---|---|
| `RT_CV_DEVICE_ID`, `RTVI_CV_HOST_PORT`, `DS_MODEL_FAMILY` | Configure the perception pipeline. |
| `VISION_ENCODER_MODEL`, `VISION_ENCODER_VERSION` | Select the vision encoder NGC artifact downloaded by ds-start phase 0; the checked-in RT-CV config uses the fixed RT-DETR warehouse artifact. |
| `RT_EMBED_DEVICE_ID`, `RTVI_EMBED_PORT`, `MODEL_PATH`, `HF_TOKEN` | Place and configure RT-Embed. |
| `ENABLE_CRITIC`, `VLM_NAME`, `VLM_BASE_URL`, `VLM_MODEL_TYPE`, `RTVI_VLM_*` | Configure default-enabled result critique through RT-VLM; set `ENABLE_CRITIC=false` only when critique is explicitly excluded. |
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

Because critique is enabled by default, also probe RT-VLM's `/v1/models`
endpoint. Skip this check only when `ENABLE_CRITIC=false`.

## Operator-facing views

After readiness passes, tell the operator how to *look* at this profile. Both
paths are view-only.

**1. VSS-UI, deployed with this profile.** `vss-ui` is in the service set and
`vss-haproxy-ingress` fronts it. Tabs come from
`deploy/docker/services/ui/compose.yml` env, resolved from the profile's `.env`
/ `overrides.env` — note the search tab is **off by default** and this is the
profile that should turn it on:

| Knob | Default | For this profile |
|---|---|---|
| `NEXT_PUBLIC_ENABLE_SEARCH_TAB` | `false` | **set `true`** |
| `NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB` | `true` | source ingestion |
| `NEXT_PUBLIC_ENABLE_ALERTS_TAB` | `true` | leave off — no Alert Bridge here |
| `NEXT_PUBLIC_SEARCH_TAB_MEDIA_WITH_OBJECTS_BBOX` | — | bbox overlay on result media |

Deep-link a tab with the `#vss-mt-<tabId>` hash (`Home.tsx` resolves it on first
load and on `hashchange`; ids are `chat`, `search`, `alerts`, `dashboard`,
`map`, `video-management`):

```text
http://${HOST_IP}:${VSS_PUBLIC_PORT:-7777}/#vss-mt-search
```

Report the ingress origin, not `:3000` — that is the UI container's own port and
bypasses the shared-origin routing the tab's API calls rely on.

**2. Generated view artifacts — no UI needed.** `tools/vss-view` renders search
results into a self-contained HTML grid from a JSON spec, with thumbnails
embeddable so the file outlives the deployment. Use it when the Agent/UI layer
is dropped in a delta or a shareable artifact is wanted. See
`skills/vss-search-archive/references/view-artifacts.md`.

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
