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
- Use exactly the dashboard initializer matching the selected Foundation.
- `logstash` requires the broker and the profile's selected `STREAM_TYPE`.
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
- `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-lvs/compose.yml`
- `deploy/docker/developer-profiles/dev-profile-search/compose.yml`
