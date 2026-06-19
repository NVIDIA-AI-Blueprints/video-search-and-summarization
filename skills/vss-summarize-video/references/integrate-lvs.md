# Integration Reference: VS (Video Summarization)

## Overview

The Video Summarization (VS) service (`lvs-server`, container `vss-lvs`, image
`nvcr.io/nvidia/vss-core/vss-video-summarization:3.2.0`) turns a stream of dense VLM captions
into a single, consolidated structured summary of a long video or live stream. Include this
service when a deployment must, in addition to producing per-chunk dense captions, **aggregate
those captions into a higher-level narrative summary plus a deduplicated list of timestamped
events** — either on-demand for an uploaded file (`POST /v1/summarize`) or continuously for a
live stream (`POST /v1/generate_captions` → `POST /v1/stream_summarize`).

In a VSS pipeline, VS layers on top of the dense-captioning (RT-VLM) baseline. RT-VLM publishes
raw caption events to the Kafka topic `mdx-vlm-captions`; the shared-infra Logstash `mdx-lvs`
pipeline writes those raw events into Elasticsearch; VS's CA-RAG context manager reads the raw
events back from Elasticsearch, performs LLM-based summarization and event merging, and writes the
resulting structured summary back to Elasticsearch (collection `lvs-events`) and to the Kafka topic
`mdx-structured-events-summary`. VS itself runs CPU-only logic but **depends on an external
summarization LLM endpoint** (a local LLM NIM or an NVIDIA-hosted remote endpoint) and on the
RT-VLM endpoint for VLM serving.

## Required Peer Services

**Prose — peer microservices (cross-skill dependencies):**

- **Elasticsearch** (from ELK / `integrate-elk.md`) — **required.** Default CA-RAG database backend
  (`LVS_DATABASE_BACKEND=elasticsearch_db`). VS reads raw caption events written by the Logstash
  `mdx-lvs` pipeline and writes structured summaries to the `lvs-events` collection. Reached at
  `ES_HOST:ES_PORT` (`${HOST_IP}:9200`).
- **Kafka** (from ELK) — **required when `KAFKA_ENABLED=true`** (the VS-profile default). VS
  publishes structured summaries to `KAFKA_STRUCTURED_SUMMARY_TOPIC=mdx-structured-events-summary`.
  This topic is already created by ELK's `kafka-topic-init-container`
  (`services/infra/compose.yml`), and the Logstash `mdx-lvs` pipeline already subscribes to it — no
  new topic or pipeline wiring is needed.
- **Logstash** (from ELK) — **required.** The `mdx-lvs` pipeline
  (`elk/logstash/pipelines/kafka/mdx-lvs-logstash.conf`) is the bridge that puts both the raw
  `mdx-vlm-captions` events (which VS reads back) and VS's own `mdx-structured-events-summary`
  output into Elasticsearch. The streaming summarization read path depends on raw events being in ES.
- **RT-VLM** (from `integrate-rt-vlm.md`) — **required.** Serves the VLM for caption generation; VS
  routes VLM calls to it via `RTVI_VLM_URL` (`http://${HOST_IP}:8018`) with
  `RTVI_VLM_URL_PASSTHROUGH=true`. RT-VLM is also the producer of the `mdx-vlm-captions` raw events
  VS summarizes.
- **Summarization LLM endpoint** — **required.** VS's CA-RAG `summarization_llm` tool needs an
  OpenAI-compatible chat-completions endpoint at `LVS_LLM_BASE_URL` serving model
  `LVS_LLM_MODEL_NAME`. This can be (a) a **local LLM NIM** on a GPU (the dev-profile-lvs
  `local_shared` default; e.g. `nvidia/nvidia-nemotron-nano-9b-v2` on `${HOST_IP}:${LLM_PORT}`), or
  (b) an **NVIDIA-hosted remote endpoint** (`LLM_BASE_URL=https://integrate.api.nvidia.com/v1` +
  `NVIDIA_API_KEY`). The dense-captioning (RT-VLM-only) baseline does NOT ship an LLM NIM, so a
  deployment that adds VS to that baseline must supply one of these two LLM sources.
- **Embedding endpoint** — **optional.** Only required when a graph DB backend (`graph_db` /
  `graph_db_arango`) is selected. For the default `elasticsearch_db` backend, `LVS_EMB_ENABLE=false`
  and no embedding endpoint is needed.
- **VIOS** (from `integrate-vios-service.md`) — **optional.** For the on-demand file path, the
  summarized clip URL is obtained from VIOS; VS itself only needs an HTTP(S)/S3/`file://`-reachable
  video URL or a file/stream id known to the captioning service.

**Structured — `component_services:` block.** The VS `component_services:` block is NOT carried in
this file. Per the 2026-06-08 decoupling, it lives in build-vision-agent's own per-service patch
reference: `skills/vss-build-vision-agent/references/patch-lvs.md`. This file holds only the neutral
microservice contract.

## Integration Interfaces

### Inputs

- **Elasticsearch read (raw caption events).** During streaming summarization
  (`summarization_online` / `POST /v1/stream_summarize`), VS reads raw `raw_events` documents that
  the Logstash `mdx-lvs` pipeline wrote from the `mdx-vlm-captions` topic. Address:
  `ES_HOST:ES_PORT` (`${HOST_IP}:9200`). Schema: via-ctx-rag document shape
  (`{text, vector, metadata:{source, content_metadata}}`), `doc_type=raw_events`, in the
  per-stream `default_<streamId>` index. Authentication: none (local ES).
- **REST API — file summarization.** `POST /v1/summarize` (and the `/summarize` alias).
  Request body `SummarizationQuery` with required `model`, `scenario`, `events`; a video source via
  `url` (HTTP(S)/S3) or `id` (file/stream UUID known to the captioning service). Authentication:
  bearer optional; local VSS deployments expose it without auth.
- **REST API — stream captioning.** `POST /v1/generate_captions` to start RTVI stream captioning for
  a stream id; `POST /v1/stream_summarize` to summarize already-captioned stream events from the
  database. Request schemas `id` + `model` required.
- **VLM passthrough.** VS routes VLM inference to RT-VLM at `RTVI_VLM_URL` with
  `RTVI_VLM_URL_PASSTHROUGH=true` (and `VIA_VLM_ENDPOINT=${VLM_BASE_URL}/v1/`). Method: HTTP.

### Outputs

- **Elasticsearch write (structured summary).** Method: ES index write (via the Logstash `mdx-lvs`
  pipeline consuming the Kafka topic below). **Index/collection — runtime-resolved, NOT a fixed
  `lvs-events` index.** The `config.yaml elasticsearch_db.params.collection_name: lvs-events`
  setting is only the *fallback* default. When a `/v1/summarize` (or `/v1/stream_summarize`)
  request carries a file/stream `id`, the emitted `mdx-structured-events-summary` message sets
  `info.collection_name = default_<id>` (id with dashes→underscores), and the Logstash `mdx-lvs`
  pipeline routes the doc to that **`default_<id>`** index verbatim — the same per-stream index the
  raw captions land in — NOT to a standalone `lvs-events` index. Verified live 2026-06-18 (IN-1-1):
  after `/v1/summarize` with `id=<file_id>`, the `default_<file_id>` index gained `structured_events`
  + `aggregated_summary` docs; no `lvs-events` index was ever created. A standalone `lvs-events`
  index appears only when summarization runs without a per-stream id (so the config default applies).
  Schema: via-ctx-rag `add_summary` shape with `doc_type` of `structured_events` / `aggregated_summary`.
  Trigger: per-summarization-request / per live-stream summary cadence.
- **Kafka produce (structured summary).** Method: Kafka produce. Topic:
  `KAFKA_STRUCTURED_SUMMARY_TOPIC=mdx-structured-events-summary` (default; created by ELK's
  `kafka-topic-init-container`). Schema: `nv.VisionLLM` protobuf, carrying an explicit
  `info.collection_name` so Logstash routes it to the named ES collection. Trigger: when
  `KAFKA_ENABLED=true`, on each structured-summary emission.
- **REST API — summary payload.** Method: HTTP response. Endpoint: `POST /v1/summarize` /
  `/v1/stream_summarize`. Schema: `CompletionResponse` envelope; the summary JSON string lives at
  `choices[0].message.content` and parses to `{video_summary, events}`. Trigger: per-request.

## API Schema

The authoritative OpenAPI source is `long-video-summarization/api_spec/openapi.json`. Key endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/ready` | GET | Readiness probe. HTTP 200 = ready; 503 = warming / dependency unavailable. Body may be empty — check status code only. |
| `/v1/live`, `/v1/startup`, `/v1/healthz` | GET | Liveness / startup / VIA health. |
| `/v1/metadata` | GET | Service metadata. |
| `/models` | GET | Models available to the summarization service. |
| `/recommended_config` | POST | Recommend chunking parameters. |
| `/metrics` | GET | Prometheus metrics. |
| `/v1/summarize` | POST | Summarize a video file. Canonical 3.2 GA route. |
| `/summarize` | POST | Compatibility alias for `/v1/summarize`. |
| `/v1/generate_captions` | POST | Start RTVI stream captioning for a stream id. |
| `/v1/stream_summarize` | POST | Summarize an already-captioned stream from DB captions. |

`POST /v1/summarize` request (`SummarizationQuery`) — required fields: `model` (must match a
`/models` id), `scenario` (string), `events` (array[string]). Source: `url` (HTTP(S)/S3) or `id`
(UUID / array). Most schemas set `additionalProperties: false` — do not invent fields. Response:
`CompletionResponse`; summary JSON string at `choices[0].message.content`.

Full request/response field tables, optional fields, and gotchas are in
`skills/vss-summarize-video/references/video-summarization-api.md`.

## Environment Variables

| Variable | Purpose | Default | Required? |
|---|---|---|---|
| `CONTAINER_IMAGE` | VS image | `nvcr.io/nvidia/vss-core/vss-video-summarization:3.2.0` | No |
| `LVS_IMAGE` / `LVS_TAG` | Image repo / tag feeding `CONTAINER_IMAGE` | `…/vss-video-summarization` / `3.2.0` | No |
| `CA_RAG_CONFIG` | Path to mounted `config.yaml` inside container | `/app/config.yaml` | Yes (set by compose) |
| `BACKEND_PORT` | REST API port | `38111` | No |
| `LVS_MCP_PORT` | MCP/SSE port (only if `LVS_ENABLE_MCP=true`) | `38112` | No |
| `LVS_ENABLE_MCP` | Enable optional MCP/SSE endpoint | `false` | No |
| `ES_HOST` / `ES_PORT` | Elasticsearch connection | `${HOST_IP}` / `9200` | Yes (for `elasticsearch_db`) |
| `LVS_DATABASE_BACKEND` | CA-RAG DB backend (`elasticsearch_db` / `graph_db` / `graph_db_arango`) | `elasticsearch_db` | No |
| `LVS_LLM_MODEL_NAME` | Summarization LLM model id | `${LLM_NAME}` | Yes |
| `LVS_LLM_BASE_URL` | Summarization LLM base URL (`/v1` appended) | `${LLM_BASE_URL:-http://${HOST_IP}:${LLM_PORT}}/v1` | Yes |
| `LVS_LLM_API_KEY` | Summarization LLM API key | `${OPENAI_API_KEY:-${NVIDIA_API_KEY}}` | If endpoint enforces auth |
| `NVIDIA_API_KEY` | NVIDIA-hosted remote LLM/embedding auth + CA-RAG LLM key fallback | empty | If remote LLM/embedding |
| `VIA_VLM_ENDPOINT` | VLM endpoint (routes to RT-VLM) | `${VLM_BASE_URL:-http://${HOST_IP}:${VLM_PORT}}/v1/` | Yes |
| `RTVI_VLM_URL` | RT-VLM base URL for VLM passthrough | `http://${HOST_IP}:${RTVI_VLM_PORT}` | Yes |
| `RTVI_VLM_URL_PASSTHROUGH` | Route VLM calls through RT-VLM `/generate_captions` | `true` | No |
| `LVS_EMB_ENABLE` | Enable embedding tool (required for graph backends) | `false` | No |
| `LVS_EMB_MODEL_NAME` / `LVS_EMB_BASE_URL` | Embedding model id / endpoint (graph backends only) | unset | If graph backend |
| `KAFKA_ENABLED` | Enable Kafka structured-summary integration | `false` (compose) / `true` (VS profile) | No |
| `KAFKA_BOOTSTRAP_SERVERS` | Broker address from the VS container | `kafka:9092` (compose) / `${HOST_IP}:9092` (profile) | Yes when Kafka enabled |
| `KAFKA_STRUCTURED_SUMMARY_TOPIC` | Structured-summary publish topic | `mdx-structured-events-summary` | No |
| `LVS_ENABLE_LLM_MERGING` | LLM-merge overlapping/duplicate events | `false` (compose) / `true` (VS profile) | No |
| `LVS_DISABLE_DB_RESET_ON_REQUEST_DONE` | Keep events in ES after a request completes | `true` | No |
| `MODEL_ROOT_DIR` / `NGC_MODEL_CACHE` | Model cache dir (mounted) | `/tmp/model_cache` (compose) / `/opt/models/` (profile) | No |
| `ENABLE_AUDIO` | Audio ingest for Omni VLMs | `false` | No |
| `ENABLE_DENSE_CAPTION` | (VS-internal) dense caption mode | `false` | No |
| `DISABLE_CA_RAG` | Disable CA-RAG entirely | `false` | No |
| `VSS_LOG_LEVEL` | Log verbosity | `INFO` | No |
| `GRAPH_DB_*` / `ARANGO_DB_*` / `MINIO_*` | Graph DB / object-store creds (only for those backends) | unset | If that backend |

Compose-boundary rewrites: `LVS_LLM_BASE_URL` is composed from `${LLM_BASE_URL}` (host) or, when
empty, `http://${HOST_IP}:${LLM_PORT}`; the container always appends `/v1`. `VIA_VLM_ENDPOINT`
similarly derives from `${VLM_BASE_URL}` or `http://${HOST_IP}:${VLM_PORT}/v1/`. The CA-RAG
`config.yaml` reads `LVS_LLM_MODEL_NAME`, `LVS_LLM_BASE_URL`, `NVIDIA_API_KEY` for its
`summarization_llm` tool via `!ENV` interpolation.

## Network Requirements

- **Ports exposed** — `38111/tcp` (REST API, host) and, only when `LVS_ENABLE_MCP=true`,
  `38112/tcp` (MCP/SSE, host).
- **Inbound traffic** — REST clients (agents, smoke tests) on `:38111`; healthcheck on
  `localhost:38111/v1/ready`.
- **Outbound traffic** — Elasticsearch (`${HOST_IP}:9200`), Kafka (`${HOST_IP}:9092`), the
  summarization LLM endpoint (`LVS_LLM_BASE_URL`), RT-VLM (`${HOST_IP}:8018`), and an embedding
  endpoint when a graph backend is enabled.
- **DNS / hostname assumptions** — runs with `network_mode: host`, so it reaches peers via
  `${HOST_IP}`/`localhost` and host ports, NOT compose DNS names. The compose default
  `KAFKA_BOOTSTRAP_SERVERS=kafka:9092` MUST be overridden to `${HOST_IP}:9092` under host
  networking; the VS profile env does this. Likewise `GRAPH_DB_HOST`/`ARANGO_DB_HOST` default to
  compose DNS names and must be set to `127.0.0.1`/`${HOST_IP}` if those sidecars are added.
- **`network_mode`** — `host`.

## Known Integration Constraints

- **Summarization LLM is a hard dependency.** The CA-RAG `summarization_llm` tool requires a
  reachable OpenAI-compatible chat endpoint at `LVS_LLM_BASE_URL`. A dense-captioning-only baseline
  (RT-VLM, no LLM NIM) does NOT provide one; adding VS to it requires supplying a local LLM NIM
  (GPU + NGC) or a remote NVIDIA-hosted endpoint (`NVIDIA_API_KEY`). With no reachable LLM, `/v1/ready`
  stays 503 or summarization requests fail.
- **Streaming summarization reads captions back from Elasticsearch, not from Kafka directly.** The
  `summarization_online` function (`config.yaml`) sets `kafka_enabled: true` precisely so the online
  aggregator reads raw events from the DB (written by the Logstash `mdx-lvs` pipeline) instead of
  expecting in-process accumulation. The `context_manager.functions` list MUST include
  `summarization_online` or `POST /aggregate_live_stream` raises a KeyError.
- **`mdx-structured-events-summary` topic and the `mdx-lvs` Logstash subscription already exist in
  the shared infra.** ELK's `kafka-topic-init-container` creates the topic and the `mdx-lvs`
  pipeline subscribes to both `mdx-vlm-captions` and `mdx-structured-events-summary`. Adding VS does
  NOT require any change to ELK, Kafka, or Logstash — VS is purely an additive producer/ES-reader.
- **Healthcheck `start_period` is 120 s.** `/v1/ready` returns 503 while warming; do not treat 503
  during warmup as failure. Check HTTP status only — the body may be empty on success.
- **Host networking → no compose-DNS peers.** See Network Requirements. The biggest live failure is
  leaving `KAFKA_BOOTSTRAP_SERVERS=kafka:9092` (compose default) under host networking — it must be
  `${HOST_IP}:9092`.
- **MCP is opt-in.** `LVS_ENABLE_MCP=false` by default to avoid host-port collisions on `:38112`
  under host networking. The HTTP API on `:38111` is sufficient for the developer profile.
- **`model` ids are runtime-generated** when using the integrated RT-VLM VLM path. The `VLM_NAME`
  used in summarize/caption requests must match RT-VLM's `/v1/models` id
  (`nim_nvidia_cosmos-reason2-8b_hf-1208`), not the friendly name — resolve at runtime.

## Example Compose Snippet

```yaml
services:
  lvs-server:
    image: ${CONTAINER_IMAGE:-nvcr.io/nvidia/vss-core/vss-video-summarization:3.2.0}
    container_name: vss-lvs
    profiles: ["bp_developer_lvs_2d"]
    network_mode: host
    volumes:
      - $VSS_APPS_DIR/services/video-summarization/configs/config.yaml:/app/config.yaml:ro
      - ${MODEL_ROOT_DIR:-/tmp/model_cache}:${MODEL_ROOT_DIR:-/tmp/model_cache}
    environment:
      - CA_RAG_CONFIG=/app/config.yaml
      - ES_HOST=${ES_HOST}
      - ES_PORT=${ES_PORT}
      - LVS_DATABASE_BACKEND=${LVS_DATABASE_BACKEND:-elasticsearch_db}
      - LVS_LLM_MODEL_NAME=${LVS_LLM_MODEL_NAME}
      - LVS_LLM_BASE_URL=${LLM_BASE_URL:-http://${HOST_IP}:${LLM_PORT}}/v1
      - LVS_LLM_API_KEY=${OPENAI_API_KEY:-${NVIDIA_API_KEY}}
      - NVIDIA_API_KEY=${NVIDIA_API_KEY}
      - VIA_VLM_ENDPOINT=${VLM_BASE_URL:-http://${HOST_IP}:${VLM_PORT}}/v1/
      - RTVI_VLM_URL=${RTVI_VLM_URL:-}
      - RTVI_VLM_URL_PASSTHROUGH=true
      - BACKEND_PORT=${BACKEND_PORT:-38111}
      - KAFKA_ENABLED=${KAFKA_ENABLED:-false}
      - KAFKA_BOOTSTRAP_SERVERS=${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}
      - KAFKA_STRUCTURED_SUMMARY_TOPIC=${KAFKA_STRUCTURED_SUMMARY_TOPIC:-mdx-structured-events-summary}
      - LVS_ENABLE_LLM_MERGING=${LVS_ENABLE_LLM_MERGING:-false}
      - LVS_EMB_ENABLE=${LVS_EMB_ENABLE}
      - DISABLE_CA_RAG=false
      - ENABLE_DENSE_CAPTION=false
      - LVS_ENABLE_MCP=${LVS_ENABLE_MCP:-false}
    env_file:
      - $VSS_APPS_DIR/services/video-summarization/.env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:${BACKEND_PORT:-38111}/v1/ready"]
      interval: 30s
      timeout: 10s
      retries: 10
      start_period: 120s
    restart: always
    depends_on:
      rtvi-vlm:
        condition: service_healthy
        required: false
```

## Test / Smoke Hooks

- Readiness: `curl -sf -o /dev/null -w '%{http_code}' http://${HOST_IP}:38111/v1/ready` → `200`.
- Models: `curl -sf http://${HOST_IP}:38111/models | jq '.data[].id'`.
- File summarize: `POST /v1/summarize` with `{model, url|id, scenario, events}`; assert
  `choices[0].message.content` parses to a non-empty `video_summary`.
- Structured-summary topic: `docker exec kafka kafka-get-offsets --bootstrap-server localhost:9092
  --topic mdx-structured-events-summary` → offsets advance after a summarize request.
- ES summary docs: query the **runtime-resolved** index, not a fixed `lvs-events`. For a
  `/v1/summarize` with `id=<file_id>`, assert the structured summary landed in the per-stream index:
  `curl -sf 'http://${HOST_IP}:9200/default_<file_id_underscored>/_count'` → `count` increases over
  its pre-summarize value (the index also holds the raw captions, so assert a delta, not just `>0`).
  Cross-check the producer half via the Kafka topic offset:
  `docker exec kafka kafka-get-offsets --bootstrap-server localhost:9092 --topic mdx-structured-events-summary`
  → offsets advance. (`lvs-events/_count` is only valid when summarization ran without a per-stream
  id — see Outputs note above. Verified live 2026-06-18, IN-1-1.)

---

*Sources: `deploy/docker/services/video-summarization/compose.yml`,
`deploy/docker/services/video-summarization/.env`,
`deploy/docker/services/video-summarization/configs/config.yaml`,
`deploy/docker/developer-profiles/dev-profile-lvs/.env`,
`deploy/docker/services/infra/compose.yml` (kafka-topic-init topic list),
`deploy/docker/services/infra/elk/logstash/pipelines/kafka/mdx-lvs-logstash.conf`,
`skills/vss-summarize-video/references/video-summarization-api.md` (OpenAPI-derived),
`skills/vss-summarize-video/references/video-summarization-environment-variables.md`,
`skills/vss-summarize-video/references/video-summarization-deployment.md`. Local met-blueprint-docs
RST set (`long-video-summarization.rst`, `long-video-summarization-api.rst`,
`agent-workflow-lvs.rst`, `performance-lvs.rst`, `Known-Limitations.rst`) was identified as the
upstream authority but was not readable in this session; the skill reference docs above (which cite
the OpenAPI spec) and the compose ground-truth were used and cross-checked instead. Flag for a
follow-up RST cross-check when those files become readable.*
