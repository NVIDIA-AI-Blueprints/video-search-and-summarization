# Integration Reference: ELK

## Overview

ELK is the Elasticsearch + Logstash + Kibana stack used by VSS as the indexing and search layer. Elasticsearch holds caption text, embeddings, frame metadata, alerts, and incidents; Logstash consumes records from Kafka or Redis and writes them to Elasticsearch; Kibana provides the dashboarding surface (`SEARCH_PUBLICBASEURL` configurable). Source-of-truth definitions live in `deploy/docker/services/infra/compose.yml`. VSS 3.2 splits the Kafka-side Logstash configuration into **two pipelines**: `mdx-kafka` (subscribes to `mdx-{alerts,events,incidents,vlm-alerts,vlm-incidents,frames,mtmc,embed-filtered,...}` — generic-shape ES docs) and `mdx-lvs` (subscribes to `mdx-vlm-captions` + `mdx-structured-events-summary` — via-ctx-rag-shaped ES docs, indices named `<collection>_<id>`). IN-1 includes ELK so RT-VLM dense captions published to `mdx-vlm-captions` are decoded by the `mdx-lvs` pipeline and indexed.

ELK is treated as a **first-class catalog entry** with its own reference files. Cross-deployment sharing/isolation strategy (single-vs-multi ES instance across profiles) lives separately in `shared-infrastructure.md`; this file documents the per-service contract.

## Required Peer Services

### Peer microservices (cross-skill)

- **Producer of indexed records** — varies by deployment. For IN-1, RT-VLM is the producer publishing to `${RTVI_VLM_KAFKA_TOPIC}`. **The skill emits `mdx-vlm-captions`** (the VSS-3.2 upstream-subscribed topic). The raw rtvi-vlm-compose default `vision-llm-messages` is unsubscribed; the pre-3.2 `mdx-vlm` is also unsubscribed in current upstream — do not use either.

Notes on the structured block below:

- Kafka, Redis, and `broker-health-check` are NOT cross-skill peers — they ship as part of the foundational ELK / infra stack defined in `services/infra/compose.yml` and are therefore declared under ELK's own `component_services:` rather than as peer microservices.
- `STREAM_TYPE=kafka` (default) wires Logstash to the Kafka transport; `STREAM_TYPE=redis` switches to the Redis transport. Both transport containers are unconditionally part of the ELK component set; the toggle only changes which Logstash pipeline subscribes.

### Structured component_services (consumed by `vss-build-vision-agent` Step 4)

See `references/component-services-schema.md` for the schema.

```yaml
component_services:
  - key: elasticsearch
    file: services/infra/compose.yml
    role: Document store for caption and incident records.
  - key: elasticsearch-init-container
    file: services/infra/compose.yml
    role: One-shot init container that creates default templates and pipelines.
  - key: kibana
    file: services/infra/compose.yml
    role: Dashboard UI over Elasticsearch indices.
  - key: logstash
    file: services/infra/compose.yml
    role: Kafka -> Elasticsearch ingest pipeline with NvSchema protobuf decoders.
  - key: kafka
    file: services/infra/compose.yml
    role: Message bus for KAFKA_TOPIC, KAFKA_INCIDENT_TOPIC, sensor lifecycle events.
  - key: kafka-topic-init-container
    file: services/infra/compose.yml
    role: One-shot init that pre-creates topics Logstash subscribes to.
  - key: redis
    file: services/infra/compose.yml
    role: Cache plus alternate message bus for sensor.event and optional VLM error stream.
  - key: broker-health-check
    file: services/infra/compose.yml
    role: "Smoke endpoint other services depend on via depends_on with required:false."
  - key: phoenix
    file: services/infra/compose.yml
    role: OpenTelemetry trace sink for VSS internals.
  - key: mosquitto
    file: services/infra/compose.yml
    role: MQTT broker for alert republishing.
    required: false
```

## Integration Interfaces

### Inputs

- **Method:** Kafka topic consumption (Logstash, when `STREAM_TYPE=kafka`)
  **Pipeline config:** `deploy/docker/services/infra/elk/logstash/pipelines/kafka/mdx-logstash.conf` (mounted as `/usr/share/logstash/pipeline/logstash.conf` via the `${STREAM_TYPE}` substitution).
  **Schema:** NvSchema protobuf — decoded with `logstash-codec-protobuf` using descriptors at `/opt/logstash-data-libs/logstash/pb_definitions/descriptors/{ext.desc, schema.desc}`. The protobuf definitions are the same ones RT-VLM produces against, so the schema is consistent producer-to-consumer.
  **Topics consumed:** all topics that downstream consumers index. The foundational `kafka-topic-init-container` creates a canonical list (see `mdx-foundational.yml`): `mdx-raw`, `mdx-bev`, `mdx-space-utilization`, `mdx-alerts`, `mdx-behavior`, `mdx-behavior-plus`, `mdx-frames`, `mdx-mtmc`, `mdx-rtls`, `mdx-rtls-region-1`, `mdx-amr`, `mdx-vlm-alerts`, `mdx-notification`, `mdx-events`, `mdx-incidents`, `mdx-vlm-incidents`, `mdx-vlm`, `mdx-embed`, `mdx-embed-filtered`. RT-VLM's compose-default topics (`vision-llm-messages`, `vision-llm-events-incidents`, `vision-llm-errors`) are NOT subscribed by either pipeline in VSS 3.2 — the skill MUST override `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions` so captions flow into the `mdx-lvs` pipeline. `RTVI_VLM_KAFKA_INCIDENT_TOPIC=mdx-vlm-incidents` and the error topic remap are similarly required for alert + error paths. The `mdx-kafka-topics` init container creates all canonical `mdx-*` topics (including `mdx-vlm-captions`, `mdx-structured-events-summary`, `mdx-vlm-incidents`) so producers and consumers align.
  **Authentication:** none in default deployments (PLAINTEXT Kafka).

- **Method:** Redis stream consumption (Logstash, when `STREAM_TYPE=redis`)
  **Pipeline config:** `deploy/docker/services/infra/elk/logstash/pipelines/redis/mdx-logstash.conf`
  **Plugin:** `logstash-redis-stream-input-java` (installed at boot via `logstash-plugin install` from the bundled gem)
  **Authentication:** Redis password if configured.

- **Method:** Elasticsearch HTTP API
  **Endpoint:** `http://${HOST_IP}:9200/` — full Elasticsearch REST API for direct indexing or query.
  **Authentication:** none in `single-node` discovery mode (the foundational config).

- **Method:** Kibana HTTP UI
  **Endpoint:** `http://${HOST_IP}:5601/`
  **Authentication:** none by default.

### Outputs

- **Method:** Elasticsearch index documents
  **Indices:** controlled by ILM policies created by `elasticsearch-init-container` (`elasticsearch-ilm-policy-creation.sh`) and templates (`elasticsearch-template-creation.sh`). For VSS, the search profile in particular indexes `mdx-embed-filtered-2025-01-01` (see `dev-profile-search/.env`'s `ELASTIC_SEARCH_INDEX`).
  **Schema:** governed by the index templates — see `deploy/docker/services/infra/elk/elasticsearch/init-scripts/elasticsearch-template-creation.sh`. For caption-style indices, fields typically include caption text, timestamp, source clip / stream id, sensor id.
  **Trigger:** continuous, driven by Logstash pipeline throughput.

- **Method:** Kibana dashboards
  **Source:** dashboard objects imported by the per-profile `kibana-init-container-{lvs|search|alerts}` containers from `*-kibana-objects.ndjson` files.
  **Trigger:** at deployment time, once per profile.

## API Schema

### Elasticsearch

Standard Elasticsearch 9.x REST API. Cluster health: `GET /_cluster/health`. Bulk indexing, search, ILM management: see <https://www.elastic.co/guide/en/elasticsearch/reference/9.x/>. The VSS-specific aspects are the index templates and ILM policies — both are scripted in `deploy/docker/services/infra/elk/elasticsearch/init-scripts/`:

- `elasticsearch-ilm-policy-creation.sh` — sets retention via `ELASTICSEARCH_ILM_MIN_AGE` (IN-1 search uses `48h`; LVS / alerts use `4h`).
- `elasticsearch-template-creation.sh` — creates index mappings.
- `elasticsearch-ingest-pipeline-creation.sh` — creates ingest pipelines for transformation.

Embedding-dimension parameters that templates depend on:
- `ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM` (default `1536`)
- `ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM` (default `768`)

### Kibana

Standard Kibana API at `/api/`. The init container imports dashboards via the saved-objects API (`POST /api/saved_objects/_import`) — see `deploy/docker/developer-profiles/dev-profile-{lvs,search,alerts}/kibana-dashboard/init-scripts/kibana-import-dashboard.sh`.

Health: `GET /api/status`.

### Logstash

Logstash exposes a monitoring API on `:9600` by default; not used by VSS. The pipeline is configured via the mounted `logstash.conf` file, not an API.

## Environment Variables

| Variable | Purpose | Default | Required? |
|---|---|---|---|
| `STREAM_TYPE` | Selector for Kafka vs. Redis transport — picks Logstash pipeline config and Dockerfile | — | **Yes** (`kafka` for IN-1) |
| `MDX_SAMPLE_APPS_DIR` | Bind-mount root for ELK configs (`elasticsearch.yml`, `kibana.yml`, `logstash.yml`, pipeline configs, protobuf descriptors) | — | **Yes** |
| `MDX_DATA_DIR` | Bind-mount root for Elasticsearch data and logs (`/tmp/elastic/data`, `/tmp/elastic/logs`) | — | **Yes** |
| `HOST_IP` | Host IP for Logstash to reach Kafka / Redis | — | **Yes** |
| `KIBANA_PUBLIC_URL` | `SERVER_PUBLICBASEURL` env into Kibana | `http://localhost:5601` | Optional |
| `ES_JAVA_OPTS` | Elasticsearch heap | `-Xmx1024m -Xms256m` (compose-set) | Tune for index size |
| `LS_JAVA_OPTS` | Logstash heap | `-Xmx1024m -Xms256m` (compose-set) | Tune |
| `BP_PROFILE` | Used by `elasticsearch-init-container` to select correct ILM / template wiring per profile | — | Yes |
| `ELASTICSEARCH_ILM_MIN_AGE` | ILM phase transition age | `4h` (default) / `48h` (search profile) | Optional |
| `ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS` | Init-container retry budget | `20` | Optional |
| `ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM` | dense_vector dim for CV embeddings | `1536` | Optional |
| `ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM` | dense_vector dim for VLM embeddings | `768` | Optional |
| `MINIMAL_PROFILE` | Reserved compose-profile suffix toggle | — | Optional |

## Network Requirements

- **Ports exposed** (all `network_mode: host`, so they bind directly on the host):
  - Elasticsearch: `9200` (HTTP REST)
  - Kibana: `5601` (HTTP UI)
  - Logstash: no host-bound port; communicates outbound to ES on `127.0.0.1:9200` and to Kafka on `${HOST_IP}:9092` (or Redis on `:6379`)
- **Inbound:** REST clients hit Elasticsearch on `:9200`; users / agents hit Kibana on `:5601`.
- **Outbound:**
  - Logstash → Kafka on `${HOST_IP}:9092` (or Redis on `${HOST_IP}:6379`)
  - Logstash → Elasticsearch on `localhost:9200`
  - Kibana → Elasticsearch on `localhost:9200`
  - `elasticsearch-init-container` → Elasticsearch on `localhost:9200`
  - Logstash on first start may pull `logstash-codec-protobuf` plugin from the public RubyGems repo (cached in the `mdx-logstash-libs` named volume thereafter).
- **DNS / hostname assumptions:** all components run with `network_mode: host` and address each other as `localhost` — no compose-network DNS in play. `${HOST_IP}` is used only for cross-host references (e.g., where Kibana publishes its base URL).
- **`network_mode`:** `host` for all four components (`elasticsearch`, `kibana`, `logstash`, `elasticsearch-init-container`).

## Known Integration Constraints

- **Logstash plugin install at first boot.** The Logstash entrypoint runs `logstash-plugin install` for either `logstash-codec-protobuf` (Kafka mode) or the bundled Redis-stream gem (Redis mode) before booting the pipeline. First boot adds 1–2 minutes; subsequent boots use the `mdx-logstash-libs` volume.
- **Single-node Elasticsearch.** `discovery.type: single-node` is hardcoded in the compose. Multi-node clustering requires changes to the foundational compose.
- **`STREAM_TYPE` selects both pipeline AND health-check Dockerfile.** `broker-health-check` is built from `Dockerfiles/${STREAM_TYPE}-health-check.Dockerfile`. Switching transport mid-deployment requires rebuilding both the health-check image and the Logstash pipeline mount.
- **Logstash pipeline config is bind-mounted, not parameterized.** The active pipeline is `mdx-${STREAM_TYPE}-logstash.conf`. Custom pipelines for new topics or schemas require either editing the foundational pipeline configs or layering a new mount.
- **Protobuf descriptors must stay aligned with producers.** The RT-VLM caption protobuf schema (`schema.desc`, `ext.desc`) is shared between RT-VLM and Logstash. Changing the schema on one side without the other breaks decoding silently — Logstash will write empty / default-valued documents to ES.
- **Elasticsearch and Kibana versions must match.** Kibana `9.3.0` requires Elasticsearch `9.x`. The compose builds Elasticsearch from `Dockerfiles/elasticsearch.Dockerfile`; pin its base image to a `9.x` tag and update both together.
- **Init container is one-shot.** `elasticsearch-init-container` exits after creating ILM / templates / pipelines. Re-creating templates requires rerunning the container or hitting Elasticsearch directly.
- **Per-profile Kibana dashboards.** Each VSS dev profile (lvs, search, alerts) ships a separate `kibana-init-container-<profile>` that imports its own `*-kibana-objects.ndjson`. IN-1 reuses the search profile's dashboards by default; if IN-1 diverges, a new `kibana-init-container-in-1` and `.ndjson` must be added.
- **Compose profile gating.** All ELK components are gated on the compose-`profiles:` set in `deploy/docker/services/infra/compose.yml`. The build-vision-agent skill **invents a new gating flag per generation** (e.g., `bp_developer_in_1` for an IN-1 deployment) and adds it to every ELK component's `profiles:` list — but only in the patched copy under `build-output/patched/services/infra/compose.yml`, **never in the upstream file**. The patched copy is what `build-output/compose.yml` `include:`s; upstream stays untouched. Upstream's existing flags (`bp_developer_alerts_2d_vlm`, `bp_developer_search_2d`, etc.) remain present in the patched copy alongside the new one — the addition is additive, not replacing.

## Example Compose Snippet

This is the foundational ELK slice excerpted from `deploy/docker/services/infra/compose.yml`. Bring up alongside `kafka` / `redis` / `broker-health-check` from the same file. IN-1 wires the relevant compose profile into each component's `profiles:` list.

```yaml
services:

  elasticsearch:
    build:
      context: $MDX_SAMPLE_APPS_DIR/foundational
      dockerfile: Dockerfiles/elasticsearch.Dockerfile
      network: "host"
    image: elasticsearch
    network_mode: "host"
    environment:
      ES_JAVA_OPTS: "-Xmx1024m -Xms256m"
      discovery.type: single-node
    volumes:
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/configs/elasticsearch.yml:/usr/share/elasticsearch/config/elasticsearch.yml:ro
      - mdx-elastic-data:/tmp/elastic/data:rw
      - mdx-elastic-logs:/tmp/elastic/logs:rw
    container_name: mdx-elastic
    restart: always
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9200/_cluster/health"]
      interval: 10s
      timeout: 10s
      retries: 15
      start_period: 30s

  elasticsearch-init-container:
    build:
      context: $MDX_SAMPLE_APPS_DIR/foundational
      dockerfile: Dockerfiles/elastic-init.Dockerfile
      network: "host"
    network_mode: "host"
    container_name: mdx-elasticsearch-init
    environment:
      - BP_PROFILE=${BP_PROFILE}
      - ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS=${ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS:-20}
      - ELASTICSEARCH_ILM_MIN_AGE=${ELASTICSEARCH_ILM_MIN_AGE:-4h}
      - ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM=${ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM:-1536}
      - ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM=${ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM:-768}
    command: bash -c "
      /opt/mdx/init-scripts/elasticsearch-ilm-policy-creation.sh &&
      /opt/mdx/init-scripts/elasticsearch-template-creation.sh &&
      /opt/mdx/init-scripts/elasticsearch-ingest-pipeline-creation.sh
      "
    depends_on:
      - elasticsearch

  kibana:
    image: docker.elastic.co/kibana/kibana:9.3.0
    network_mode: "host"
    volumes:
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/configs/kibana.yml:/usr/share/kibana/config/kibana.yml:ro
    environment:
      SERVER_PUBLICBASEURL: ${KIBANA_PUBLIC_URL:-http://localhost:5601}
      SERVER_SECURITYRESPONSEHEADERS_DISABLEEMBEDDING: "false"
      CSP_STRICT: "false"
    container_name: mdx-kibana
    restart: always
    depends_on:
      elasticsearch:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5601/api/status"]
      interval: 10s
      timeout: 10s
      retries: 30
      start_period: 60s

  logstash:
    image: docker.elastic.co/logstash/logstash:9.3.0
    network_mode: "host"
    volumes:
      - mdx-logstash-libs:/opt/logstash-data-libs
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/pb_definitions/ruby/ext_pb.rb:/opt/logstash-data-libs/logstash/pb_definitions/ext_pb.rb
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/pb_definitions/ruby/schema_pb.rb:/opt/logstash-data-libs/logstash/pb_definitions/schema_pb.rb
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/pb_definitions/descriptors/ext.desc:/opt/logstash-data-libs/logstash/pb_definitions/descriptors/ext.desc
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/pb_definitions/descriptors/schema.desc:/opt/logstash-data-libs/logstash/pb_definitions/descriptors/schema.desc
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/configs/logstash.yml:/usr/share/logstash/config/logstash.yml
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/configs/mdx-${STREAM_TYPE}-logstash.conf:/usr/share/logstash/pipeline/logstash.conf
      - $MDX_SAMPLE_APPS_DIR/foundational/elk/gems/logstash-input-redis_stream-3.1.0-java.gem:/usr/share/logstash/gems/logstash-redis-stream-input-java.gem
    environment:
      LS_JAVA_OPTS: "-Xmx1024m -Xms256m"
      STREAM_TYPE: ${STREAM_TYPE}
    container_name: mdx-logstash
    restart: always
    depends_on:
      broker-health-check:
        condition: service_completed_successfully
      elasticsearch-init-container:
        condition: service_completed_successfully
    command: >
      bash -c "
        if [ \"$$STREAM_TYPE\" = 'redis' ]; then
          /usr/share/logstash/bin/logstash-plugin install /usr/share/logstash/gems/logstash-redis-stream-input-java.gem;
        elif [ \"$$STREAM_TYPE\" = 'kafka' ]; then
          /usr/share/logstash/bin/logstash-plugin install logstash-codec-protobuf;
        fi &&
        /usr/local/bin/docker-entrypoint"

volumes:
  mdx-logstash-libs:
  mdx-elastic-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: $MDX_DATA_DIR/data_log/elastic/data
  mdx-elastic-logs:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: $MDX_DATA_DIR/data_log/elastic/logs
```
