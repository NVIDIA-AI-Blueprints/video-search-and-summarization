# ELK and Broker Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Elasticsearch storage and initialization | `elasticsearch`, `elasticsearch-init-container` |
| Kafka broker and topics | `kafka`, `kafka-topic-init-container` |
| Redis | `redis` |
| Kibana and profile dashboards | `kibana`, `kibana-init-container-alerts`, `kibana-init-container-lvs`, `kibana-init-container-search` |
| Log ingestion and broker readiness | `logstash`, `broker-health-check` |

## Required peers

- Use `elasticsearch-init-container` with `elasticsearch`.
- Use `kafka-topic-init-container` and `broker-health-check` with Kafka-backed
  capability owners.
- `logstash` is the **sole** bridge from Kafka topics to Elasticsearch. No other
  selected service writes Kafka events into ES indices. A build that publishes to
  Kafka and stores in Elasticsearch must include `logstash`; omitting it leaves
  the requested ES storage permanently empty.
- `logstash` requires the broker and the profile's selected `STREAM_TYPE`.
- When the selected Foundation ships `kibana` and a `kibana-init-container-*`
  key, retain both in any delta that stores data in Elasticsearch — they are the
  browse surface for that data and are not pruned by forward closure. Do not add
  `kibana` to a Foundation that does not ship it.
- Use exactly the dashboard initializer matching the selected Foundation.
- To browse more than one capability's indices from a single Kibana, converge on
  one merged saved-object bundle imported by a single initializer, never two:
  importing both per-profile bundles duplicates the shared `mdx-raw-*`/`mdx-behavior-*`
  data views and conflicts on the default-view singleton. Keep the Foundation's one
  dashboard initializer and patch it to mount the merged bundle in place of the
  single-profile one it imports; do not add a second profile's initializer. Where a
  merged bundle already ships, it is the drop-in for that patch (see
  `search-and-alerts-kibana-objects.ndjson` in Sources).
- `redis` may be used without the full ELK/Kafka set when it is only a cache.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `ELASTICSEARCH_HOST_PORT`, `ELASTICSEARCH_URL`, `ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS` | Publish and initialize Elasticsearch. |
| `ELASTICSEARCH_ILM_MIN_AGE` | Set retention policy age. |
| `ELASTICSEARCH_ENABLE_EMBEDDINGS`, `ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM`, `ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM` | Configure indexed vector fields. |
| `KAFKA_HOST_PORT`, `KAFKA_BOOTSTRAP_HOST`, `KAFKA_INTERNAL_PORT` | Configure broker access. |
| `KAFKA_TOPICS`, `DEFAULT_PARTITIONS`, `DEFAULT_RETENTION_MS` | Configure topic initialization when overridden by a service definition. |
| `REDIS_HOST_PORT`, `KIBANA_HOST_PORT`, `PHOENIX_HOST_PORT` | Change shared host bindings. |
| `STREAM_TYPE`, `BROKER_BOOTSTRAP_HOST` | Select Kafka or Redis ingestion where supported. |

## Sources

- `deploy/docker/services/infra/compose.yml`
- `deploy/docker/services/infra/elk/`
- `deploy/docker/services/infra/elk/kibana/configs/search-and-alerts-kibana-objects.ndjson`
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-lvs/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/compose.yml`
