# Video Summarization Deployment Reference

Use `vss-build-vision-ai` for full deployment. This file is the video summarization-specific
service reference for the VSS 3.2.0 `lvs` profile.

## Current VSS Docker Compose Shape

Source files:

- `deploy/docker/developer-profiles/dev-profile-lvs/.env`
- `deploy/docker/developer-profiles/dev-profile-lvs/overrides.env`
- `deploy/docker/services/video-summarization/compose.yml`
- `deploy/docker/services/video-summarization/configs/config.yaml`
- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `deploy/docker/services/infra/compose.yml`

Key service signals in the current develop branch:

| Item | Value |
|---|---|
| Compose profile | `lvs-server` |
| video summarization service | `lvs-server` |
| video summarization container | `vss-lvs` |
| video summarization image | `${LVS_IMAGE:-ghcr.io/nvidia-ai-blueprints/vss/vss-video-summarization}:${LVS_TAG:-develop-latest}` (multi-arch manifest; same tag on every platform) |
| REST API | `http://<HOST_IP>:38111` |
| Readiness | `GET /v1/ready` |
| MCP port | `38112`, disabled by default in the developer profile |
| RT-VLM | `http://<HOST_IP>:8018` |
| Kafka captions topic | `mdx-vlm-captions` |
| Kafka structured summary topic | `mdx-structured-events-summary` |

## Verify Running Service

```bash
curl -sf --max-time 15 "${LVS_BACKEND_URL:-http://localhost:38111}/v1/ready" >/dev/null
curl -sf --max-time 15 "${LVS_BACKEND_URL:-http://localhost:38111}/models" | jq '.data[0].id'
```

Non-destructive Docker checks:

```bash
docker ps --filter name=vss-lvs --format '{{.Names}} {{.Status}}'
docker logs --tail 100 vss-lvs
```

## Deploy Or Recreate

Prefer the profile deploy skill:

```text
the `/vss-build-vision-ai` stock Video Summarization workflow
```

If you are already operating a resolved Docker Compose build, use that build's
exact deployment file without profile flags:

```bash
: "${BUILD_DIR:?Set to the deployed build directory}"
docker compose -f "$BUILD_DIR/resolved.yml" ps lvs-server
docker compose -f "$BUILD_DIR/resolved.yml" logs -f lvs-server
```

## Required Inputs

Follow `vss-build-vision-ai` with the LVS Foundation. Keep the checked-in
`deploy/docker/developer-profiles/dev-profile-lvs/.env` and `overrides.env`
read-only. Write deployment-specific settings and their dependent values to
`_builds/<name>/override.env`. Resolve the four ordered layers: `containers.env`,
the Foundation `.env`, the Foundation `overrides.env`, and the build's
`override.env`. Normalize and validate `_builds/<name>/resolved.yml`, prepare
its data directories, and deploy that exact file as described in
[`deployment.md`](../../../vss-build-vision-ai/references/deployment.md).
Do not edit the service Compose directly or add password defaults to it;
supply credentials through the deployment's credential mechanism.

Core required values:

| Var | Purpose |
|---|---|
| `VSS_APPS_DIR` | Absolute path to `deploy/docker`. |
| `VSS_DATA_DIR` | Data root for models, videos, and logs. |
| `HOST_IP` | Host-reachable IP used by services and clients. |
| `NGC_CLI_API_KEY` | Required for local image/model pulls. |
| `NVIDIA_API_KEY` or `OPENAI_API_KEY` | Required when selected remote endpoints enforce auth. |
| `LLM_MODE`, `VLM_MODE` | `local_shared`, `local`, or `remote`. |
| `LLM_NAME`, `LLM_NAME_SLUG` | LLM model and deployment slug. |
| `VLM_NAME` | Must match the id returned by RT-VLM `/v1/models`. |

Video summarization service values:

| Var | Default / Example | Purpose |
|---|---|---|
| `LVS_BACKEND_URL` | `http://${HOST_IP}:38111` | Agent-facing video summarization URL. |
| `LVS_IMAGE` | `ghcr.io/nvidia-ai-blueprints/vss/vss-video-summarization` | video summarization image repository. |
| `LVS_TAG` | `3.3.0-rc2` on x86/Tegra; use `3.3.0-rc2-sbsa` on SBSA. |
| `LVS_ENABLE_MCP` | `false` | Enable MCP/SSE endpoint only when needed. |
| `LVS_DATABASE_BACKEND` | `elasticsearch_db` | Default event database backend. |
| `KAFKA_ENABLED` | `true` in dev-profile-lvs | Enables RTVI -> Kafka -> Logstash -> ES integration. |
| `KAFKA_BOOTSTRAP_SERVERS` | `${HOST_IP}:9092` | Broker address from the video summarization container. |
| `KAFKA_STRUCTURED_SUMMARY_TOPIC` | `mdx-structured-events-summary` | Structured summary publish topic. |
| `LVS_ENABLE_LLM_MERGING` | `true` in dev-profile-lvs | Merge duplicate or overlapping events with the LLM. |

## Database Backend Selection

The default backend is Elasticsearch:

```bash
LVS_DATABASE_BACKEND=elasticsearch_db
```

The video summarization config already supports graph backends, but the current
VSS Docker service graph does not define Neo4j or ArangoDB services by default.
Use an external DB endpoint or add one of the open-source containers with a
Compose override.

| Backend | `LVS_DATABASE_BACKEND` | Container | Image | Required video summarization env |
|---|---|---|---|---|
| Neo4j | `graph_db` | `graph-db` | `neo4j:5.26.4` | `GRAPH_DB_HOST`, `GRAPH_DB_BOLT_PORT`, `GRAPH_DB_USERNAME`, `GRAPH_DB_PASSWORD`, `LVS_EMB_ENABLE=true` |
| ArangoDB | `graph_db_arango` | `arango-db` | `arangodb/arangodb:3.12.4` | `ARANGO_DB_HOST`, `ARANGO_DB_PORT`, `ARANGO_DB_USERNAME`, `ARANGO_DB_PASSWORD`, `LVS_EMB_ENABLE=true` |

Do not switch to `graph_db` or `graph_db_arango` unless the embedding tool is
configured. Set `LVS_EMB_ENABLE=true`, `LVS_EMB_MODEL_NAME`, and
`LVS_EMB_BASE_URL` to a reachable OpenAI/NVIDIA-compatible text embedding
endpoint. The current LVS graph-RAG examples use
`nvidia/llama-3.2-nv-embedqa-1b-v2`, but always verify the exact model id from
the embedding endpoint:

```bash
curl -fsS "${LVS_EMB_BASE_URL%/}/models" | jq -r '.data[].id'
```

Copy the selected `id` into `LVS_EMB_MODEL_NAME`. Include `/v1` in
`LVS_EMB_BASE_URL`, for example `https://integrate.api.nvidia.com/v1` or a
local embedding NIM such as `http://127.0.0.1:9232/v1`. The video
summarization config maps embedding auth from `NVIDIA_API_KEY`, so ensure
`NVIDIA_API_KEY` is set if the endpoint requires auth. Otherwise keep
`elasticsearch_db`.

Do not use the VSS Search `rtvi-embed` service as the default graph backend
embedding endpoint. `rtvi-embed` is profile-gated for Search and exposes RTVI
embedding APIs such as `/v1/generate_text_embeddings`; the graph backend
expects the text embedding interface used by the video summarization embedding adapter.

The current VSS Docker `lvs-server` uses host networking. When adding Neo4j or
ArangoDB as open-source sidecar containers, expose their ports on the host and
point the video summarization service at `127.0.0.1` or `${HOST_IP}` via a
compose override. Do not rely on Docker DNS names like `graph-db` or `arango-db`
from inside the host-networked `lvs-server` unless the deployment has explicitly
provided those names.

Example Neo4j override:

```yaml
services:
  graph-db:
    profiles: ["graph-db"]
    image: neo4j:5.26.4
    container_name: graph-db
    environment:
      NEO4J_AUTH: ${GRAPH_DB_USERNAME:-neo4j}/${GRAPH_DB_PASSWORD:?GRAPH_DB_PASSWORD_required}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_server_bolt_listen__address: 0.0.0.0:${GRAPH_DB_BOLT_PORT:-7687}
      NEO4J_server_http_listen__address: 0.0.0.0:${GRAPH_DB_HTTP_PORT:-7474}
    ports:
      - ${GRAPH_DB_HTTP_PORT:-7474}:${GRAPH_DB_HTTP_PORT:-7474}
      - ${GRAPH_DB_BOLT_PORT:-7687}:${GRAPH_DB_BOLT_PORT:-7687}
    restart: unless-stopped

  lvs-server:
    environment:
      LVS_DATABASE_BACKEND: graph_db
      GRAPH_DB_HOST: 127.0.0.1
      GRAPH_DB_USERNAME: ${GRAPH_DB_USERNAME:-neo4j}
      GRAPH_DB_PASSWORD: ${GRAPH_DB_PASSWORD:?GRAPH_DB_PASSWORD_required}
      GRAPH_DB_HTTP_PORT: ${GRAPH_DB_HTTP_PORT:-7474}
      GRAPH_DB_BOLT_PORT: ${GRAPH_DB_BOLT_PORT:-7687}
      LVS_EMB_ENABLE: "true"
      LVS_EMB_MODEL_NAME: ${LVS_EMB_MODEL_NAME}
      LVS_EMB_BASE_URL: ${LVS_EMB_BASE_URL}
      NVIDIA_API_KEY: ${NVIDIA_API_KEY}
```

Example ArangoDB override:

```yaml
services:
  arango-db:
    profiles: ["arango-db"]
    image: arangodb/arangodb:3.12.4
    container_name: arango-db
    environment:
      ARANGO_ROOT_PASSWORD: ${ARANGO_DB_PASSWORD:?ARANGO_DB_PASSWORD_required}
    ports:
      - ${ARANGO_DB_PORT:-8529}:${ARANGO_DB_PORT:-8529}
    command:
      - arangod
      - --experimental-vector-index
      - --server.endpoint
      - tcp://0.0.0.0:${ARANGO_DB_PORT:-8529}
    restart: unless-stopped

  lvs-server:
    environment:
      LVS_DATABASE_BACKEND: graph_db_arango
      ARANGO_DB_HOST: 127.0.0.1
      ARANGO_DB_USERNAME: ${ARANGO_DB_USERNAME:-root}
      ARANGO_DB_PASSWORD: ${ARANGO_DB_PASSWORD:?ARANGO_DB_PASSWORD_required}
      ARANGO_DB_PORT: ${ARANGO_DB_PORT:-8529}
      LVS_EMB_ENABLE: "true"
      LVS_EMB_MODEL_NAME: ${LVS_EMB_MODEL_NAME}
      LVS_EMB_BASE_URL: ${LVS_EMB_BASE_URL}
      NVIDIA_API_KEY: ${NVIDIA_API_KEY}
```

Store the selected database override in `_builds/<name>/patches/database.yml`
and put its matching environment settings in that build's `override.env`.
Add only the selected database's service key (`graph-db` or `arango-db`) to
the build's effective `COMPOSE_PROFILES`, preserving the other selected services.
Include the patch after the root model in `_builds/<name>/compose.yml`:

```yaml
include:
  - path:
      - ../../deploy/docker/compose.yml
      - ./patches/database.yml
```

Keep any other required build patches in the ordered list. Follow the shared
[`deployment.md`](../../../vss-build-vision-ai/references/deployment.md)
resolution, normalization, validation, and data-directory preparation steps.
Verify the selected database service and `lvs-server` environment in the build's
`resolved.yml` before recreating anything. Do not print credentials while
inspecting that environment.

After validation and image pulls, recreate the selected database and video
summarization service from that exact file, without env-file or profile flags.
Do not add `--force-recreate`; Compose recreates services whose config changed
or that are down.

```bash
: "${BUILD_DIR:?Set to the deployed build directory}"
docker compose -f "$BUILD_DIR/resolved.yml" up -d graph-db lvs-server  # Neo4j

# Alternatively, for the ArangoDB build:
docker compose -f "$BUILD_DIR/resolved.yml" up -d arango-db lvs-server
```

Health checks:

```bash
curl -sf http://127.0.0.1:7474 >/dev/null                       # Neo4j HTTP
curl -sf http://127.0.0.1:8529/_admin/server/availability >/dev/null # ArangoDB
curl -sf "${LVS_BACKEND_URL:-http://localhost:38111}/v1/ready" >/dev/null
```

RT-VLM values:

| Var | Default / Example | Purpose |
|---|---|---|
| `RTVI_VLM_BASE_URL` | `http://${HOST_IP}:8018` | Agent-facing RT-VLM URL. |
| `RTVI_VLM_URL` | `http://${HOST_IP}:${RTVI_VLM_PORT}` | video summarization-facing RT-VLM URL. |
| `RTVI_VLM_MODEL_TO_USE` | `cosmos-reason3` | RT-VLM backend selector for default integrated mode. |
| `RTVI_VLM_MODEL_PATH` | `ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final` | Default integrated checkpoint. |
| `RTVI_VLM_KAFKA_ENABLED` | `true` | Publish raw captions to Kafka. |
| `RTVI_VLM_MESSAGE_BUS` | `kafka` | Generated-output broker type. |
| `RTVI_VLM_MESSAGE_BUS_TOPIC` | `mdx-vlm-captions` | Raw captions topic. |
| `RTVI_VLM_ERROR_BUS` | `kafka` | Error-output broker type. |

## Model Id Rule

`VLM_NAME`, when configured, must match the id returned by:

```bash
curl -sf "http://${HOST_IP}:8018/v1/models" | jq -r '.data[].id'
```

Use the endpoint response as authoritative; do not assume an image tag, NIM
profile name, or friendly name is also the serving id.

## Helm Notes

The Helm service chart lives at `deploy/helm/services/video-summarization`.
Current image values:

- `image.repository: ghcr.io/nvidia-ai-blueprints/vss/vss-video-summarization`
- `image.tag: "develop-latest"` (multi-arch manifest; same tag on every host)
- `global.container_prefix` / `global.container_tag` override the repository
  root and tag for the whole managed set, LVS included
- `service.backendPort: 38111`
- `service.mcpPort: 38112`
- `KAFKA_ENABLED: "true"`
- `KAFKA_STRUCTURED_SUMMARY_TOPIC: mdx-structured-events-summary`
- `LVS_ENABLE_MCP: "false"`

The Helm template computes `LVS_LLM_BASE_URL`, `LVS_LLM_MODEL_NAME`,
`VIA_VLM_ENDPOINT`, and `VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME` from profile or
global values.

## Common Checks

```bash
# video summarization health
curl -sf "http://${HOST_IP}:38111/v1/ready" >/dev/null

# RT-VLM model id
curl -sf "http://${HOST_IP}:8018/v1/models" | jq -r '.data[].id'

# Kafka topic traffic, when kafka is enabled
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic mdx-vlm-captions \
  --max-messages 1

# Shared Logstash pipeline
docker logs --tail 100 logstash
```
