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
kibana-init-container-search,vss-search-analytics-2d-fusion,vss-video-analytics-api,nvstreamer-2d-fusion,perception-2d-fusion,vss-agent,phoenix,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,vss-haproxy-ingress,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,rtvi-embed,vss-ui,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,rtvi-vlm,llm_${LLM_MODE}_${LLM_NAME_SLUG}
