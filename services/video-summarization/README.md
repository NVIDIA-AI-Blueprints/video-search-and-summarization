<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Video Summarization

Accelerated long video summarization and insight extraction microservice. Video Summarization
processes video content using Vision-Language Models (VLMs) and returns timestamped captions,
structured event detections, and aggregated summaries via a REST API.

## Architecture

Video Summarization is composed of the following services:

| Service | Role |
|---------|------|
| **lvs** | REST API server — orchestrates captioning, summarization, and streaming workflows |
| **rt-vlm** | Real-Time VLM inference — downloads video, chunks frames, runs VLM, streams captions via SSE or Kafka |
| **LLM NIM** (e.g. gpt-oss-20b) | Summarization LLM — aggregates captions into structured summaries via Context-Aware RAG |
| **Elasticsearch** | Document store for captions and summaries |
| **Kafka + Logstash** | (Optional, profile-gated) Streaming pipeline — RT-VLM publishes raw events to Kafka, Logstash writes to Elasticsearch |

## Running Video Summarization

Video Summarization uses Docker Compose for deployment.

### Docker runtime prerequisites

Use the repo-level Docker prerequisites before starting this service: Docker 28.3.3+,
Docker Compose v2.39.1+, NVIDIA Container Toolkit 1.17.8+, and Docker registry
access to `nvcr.io` with your NGC credentials.

This compose stack is validated with Docker's classic image store. Newer Docker
installations may enable the containerd image store by default ([Docker Desktop 4.34+](https://docs.docker.com/desktop/features/containerd/)
and [fresh Docker Engine 29.0+ installs](https://docs.docker.com/engine/storage/containerd/)).
On affected hosts, pulling `nvcr.io` images during `docker compose up` can fail with
an error similar to:

```text
error from registry: Incorrect Repository Format
```

If you see this error, switch Docker back to the classic image store by disabling the
containerd snapshotter and restarting Docker. On Linux Docker Engine, merge the
following setting into `/etc/docker/daemon.json`:

```json
{
  "features": {
    "containerd-snapshotter": false
  }
}
```

Then restart Docker:

```sh
sudo systemctl restart docker
docker info -f 'Driver={{ .Driver }} DriverStatus={{ .DriverStatus }}'
```

If the output contains `io.containerd.snapshotter.v1`, Docker is still using the
containerd image store. With the classic image store, `Driver` should report a
classic storage driver such as `overlay2`.

On Docker Desktop, clear **Settings > General > Use containerd for pulling and storing
images**, then apply the change and restart Docker Desktop. Docker keeps separate image
stores for classic and containerd modes, so images and containers from the inactive
store may be hidden until you switch back or re-pull images.

### Deploy the summarization LLM (prerequisite)

The Video Summarization compose stack **does not deploy the summarization LLM**. The
`lvs` service depends on a `wait-for-llm` step that polls
`http://${LVS_LLM_HOST}:${LVS_LLM_PORT}/v1/health/ready`, so an OpenAI-compatible LLM
endpoint must already be reachable before you run `docker compose up` for this
project. If the endpoint is not ready (or `LVS_LLM_HOST` / `LVS_LLM_PORT` are unset),
the `lvs` service will refuse to start.

The recommended path is to deploy `gpt-oss-20b` locally using the repo-level NIM
compose at `deploy/docker/services/nim/compose.yml` (which defines the
`llm_local_gpt-oss-20b` profile). From the repo root:

```sh
cd deploy/docker
export VSS_APPS_DIR=$(pwd)            # absolute path to deploy/docker
export HARDWARE_PROFILE=H100          # H100 | A100 | L40S | ...
export NGC_CLI_API_KEY=<nvapi-...>    # NGC credential used to pull the NIM image
export LLM_PORT=9233                  # host port to expose the NIM on (any free port)
export LLM_DEVICE_ID=1                # GPU index the NIM should use

docker compose -f services/nim/compose.yml \
  --profile llm_local_gpt-oss-20b \
  up -d gpt-oss-20b
```

Wait until the NIM reports ready before bringing up Video Summarization:

```sh
until curl -sf http://localhost:${LLM_PORT}/v1/health/ready; do
  echo "Waiting for gpt-oss-20b..."; sleep 5
done
echo "LLM ready on port ${LLM_PORT}."
```

Then point Video Summarization at the LLM by setting `LVS_LLM_HOST`, `LVS_LLM_PORT`,
and `LVS_LLM_MODEL_NAME` (see [Set environment variables](#set-environment-variables)).
The NIM runs in a separate Docker Compose project, so reference it by the docker host
IP (or another routable hostname), not by the `gpt-oss-20b` container name — the LVS
stack's `app-network` cannot resolve container names across projects.

Any OpenAI-compatible LLM endpoint (a remote NIM, NVIDIA-hosted endpoint, a vLLM
server, etc.) can be substituted by pointing `LVS_LLM_HOST` / `LVS_LLM_PORT` /
`LVS_LLM_MODEL_NAME` at it instead of running the local NIM compose above.

**Troubleshooting `wait-for-llm`.** If the `wait-for-llm` container logs a sequence
similar to:

```text
wait-for-llm-1  | curl: (3) URL rejected: No host part in the URL
wait-for-llm-1  | ERROR: Metropolis hosted LLM server not ready after 60 attempts
```

it means `LVS_LLM_HOST` and/or `LVS_LLM_PORT` are unset or empty when compose was
invoked, so the wait loop is hitting a malformed URL. Export them (or set them in
`.env`) to point at the LLM endpoint you brought up above, then re-run
`docker compose up`.

### Database backends

The compose file deploys **Elasticsearch** as the database backend for caption storage,
summarization, and CA-RAG retrieval.

> **Note:** Kafka and Logstash are *profile-gated* — they start only when the `kafka` Compose
> profile is active.

### Set environment variables

Export the following environment variables or save them to a `.env` file in the repo root directory:

```sh
# Mandatory
export NGC_API_KEY=<>              # Required by the LVS service for NGC-backed access
export NGC_CLI_API_KEY=<>          # Required by repo-standard NIM and RT-VLM compose paths
export NVIDIA_API_KEY=<>           # Required for CA-RAG LLM
export BACKEND_PORT=38111          # LVS REST API port

# LLM Configuration (summarization)
# These point LVS at the external LLM endpoint you brought up in
# "Deploy the summarization LLM (prerequisite)" above. Use the docker
# host IP (or a routable hostname) — the NIM runs in a separate compose
# project, so its container name will not resolve from within this stack.
export LVS_LLM_HOST=<>             # Hostname of the summarization LLM (e.g. gpt-oss-20b)
export LVS_LLM_PORT=<>             # Port of the summarization LLM (e.g. 9233)
export LVS_LLM_MODEL_NAME=<>      # LLM model name (e.g. openai/gpt-oss-20b)

# Database Backend
export LVS_DATABASE_BACKEND=elasticsearch_db      # Elasticsearch backend
export ES_HOST=elasticsearch                      # Elasticsearch hostname (default: elasticsearch)
export ES_PORT=9200                               # Elasticsearch port (default: 9200)

# RT-VLM (Video Language Model backend)
export RTVI_VLM_URL=http://rtvi-vlm:8000     # URL of the RT-VLM service
# Optional: set if RT-VLM should use a different NGC credential than NGC_CLI_API_KEY
# export RTVI_VLM_API_KEY=<>
# Optional: override the default local RT-VLM image
# export RTVI_VLM_IMAGE=nvcr.io/nvidia/vss-core/vss-rt-vlm:3.2.0

# Optional — Secrets
export HF_TOKEN=<>                 # HuggingFace token for gated models
export OPENAI_API_KEY=<>           # OpenAI-compatible endpoint swaps

# Optional — Features
export LVS_ENABLE_MCP=${LVS_ENABLE_MCP:-true}   # Enable MCP server (default: true)
export LVS_MCP_PORT=38112                        # MCP server port
export KAFKA_ENABLED=false                       # Enable Kafka streaming pipeline
export ENABLE_AUDIO=false                        # Enable audio transcription
export VIA_DEV_API=true                          # Enable /files and /generate_vlm_captions dev routes
export DISABLE_CA_RAG=false                      # Disable CA-RAG aggregation

# Optional — Elasticsearch tuning
export ES_MAX_SHARDS_PER_NODE=2000   # Raise for retain-mode workloads
export ES_JAVA_OPTS="-Xms4g -Xmx4g" # Elasticsearch JVM heap

# Optional — Observability
export VIA_ENABLE_OTEL=false
export VIA_OTEL_ENDPOINT=http://otel-collector:4318
export VSS_LOG_LEVEL=DEBUG
```

Set both `NGC_API_KEY` and `NGC_CLI_API_KEY` when using local NGC-backed services.
They may contain the same NGC credential, but they are consumed by different compose
paths. The LVS service reads `NGC_API_KEY`; repo-standard NIM and RT-VLM compose paths
read `NGC_CLI_API_KEY`. For local RT-VLM (`--profile rtvi`), this compose file passes
`RTVI_VLM_API_KEY` first, then `NGC_CLI_API_KEY`, into RT-VLM's in-container
`NGC_API_KEY` and `VIA_VLM_API_KEY` settings. `NGC_API_KEY` alone is not used as an
RT-VLM credential fallback.

The runtime environment variables above do not log Docker into `nvcr.io`. If Docker
needs to pull private NGC images, authenticate the Docker client with an NGC credential
before running `docker compose up`.

### Data directory prerequisites for repo-level Compose

The standalone compose file in this directory uses Docker-managed volumes for its
database services. The repo-level compose path under `deploy/docker/compose.yml`,
including the `lvs` developer profile, uses `VSS_DATA_DIR` host bind mounts for shared
infrastructure data. If you run that repo-level compose path directly instead of the
`dev-profile.sh` or `blueprint-deploy.sh` helpers, pre-create the writable data
directories first:

```sh
export VSS_DATA_DIR=/path/to/vss-apps-data

mkdir -p \
  "$VSS_DATA_DIR/data_log/elastic/data" \
  "$VSS_DATA_DIR/data_log/elastic/logs" \
  "$VSS_DATA_DIR/data_log/kafka" \
  "$VSS_DATA_DIR/data_log/redis/data" \
  "$VSS_DATA_DIR/data_log/redis/log"

chmod -R 777 "$VSS_DATA_DIR/data_log"
```

Kafka writes `/tmp/kafka-data/cluster_id` through the
`$VSS_DATA_DIR/data_log/kafka` bind mount, Elasticsearch writes data and logs through
`$VSS_DATA_DIR/data_log/elastic/{data,logs}`, and Redis writes under
`$VSS_DATA_DIR/data_log/redis`. If these host directories are missing or not writable
by the container users, startup can fail with permission errors such as Kafka failing
to create `cluster_id` or Elasticsearch failing to open `gc.log`.

### Example `.env` file

```sh
NGC_API_KEY=nvapi-XXXXXXXXXXXXX
NGC_CLI_API_KEY=nvapi-XXXXXXXXXXXXX
# Optional: set only if RT-VLM should use a different NGC credential.
# RTVI_VLM_API_KEY=nvapi-YYYYYYYYYYYYY
NVIDIA_API_KEY=nvapi-XXXXXXXXXXXXX
BACKEND_PORT=38111

LVS_LLM_HOST=<host-ip>
LVS_LLM_PORT=9233
LVS_LLM_MODEL_NAME=openai/gpt-oss-20b

# Database backend
LVS_DATABASE_BACKEND=elasticsearch_db

# MCP Server
LVS_ENABLE_MCP=true
LVS_MCP_PORT=38112
```

### Build the container locally

From the `services/video-summarization` directory:

```sh
make -C docker build
```

This builds the `via-engine` Docker image used by the `lvs` service in the compose file.
The image name is `via-engine-<username>`.

### Start Video Summarization using Docker Compose

The compose file is available at
[docker/deploy/compose.yaml](docker/deploy/compose.yaml).

> **Prerequisite:** the summarization LLM must already be running and reachable at
> `${LVS_LLM_HOST}:${LVS_LLM_PORT}` before you run the commands below. See
> [Deploy the summarization LLM (prerequisite)](#deploy-the-summarization-llm-prerequisite).

```sh
# If exporting env variables or if .env is in the current directory:
docker compose -f docker/deploy/compose.yaml up

# If .env is not in the current directory:
docker compose -f docker/deploy/compose.yaml --env-file=<path/to/.env> up

# With RT-VLM (local VLM inference):
docker compose -f docker/deploy/compose.yaml --profile rtvi up

# With Kafka streaming pipeline:
docker compose -f docker/deploy/compose.yaml --profile kafka up

# Both RT-VLM and Kafka:
docker compose -f docker/deploy/compose.yaml --profile rtvi --profile kafka up
```

Logs will show the ports the services are running at. The LVS API defaults to port `38111`.

### Verify readiness

Wait for the `/v1/ready` endpoint to return 200 before sending requests:

```sh
until curl -sf http://localhost:38111/v1/ready; do
  echo "Waiting for Video Summarization to be ready..."; sleep 5
done
echo "Ready!"
```

---

## API Reference

Base URL: `http://localhost:38111`

All endpoints accept `Authorization: Bearer <API_KEY>` when API authentication is
configured for the deployment.

### Health Check

| Endpoint | Description |
|----------|-------------|
| `GET /v1/ready` | Readiness probe — returns 200 when fully initialized |
| `GET /v1/live` | Liveness probe — returns 200 if process is alive |
| `GET /v1/startup` | Startup probe — returns 200 once startup is complete |
| `GET /v1/metadata` | Service metadata (version, build info) |

### Models

#### `GET /models` — List available models

```sh
curl -s http://localhost:38111/models \
  -H "Authorization: Bearer $API_KEY" | jq '.data[].id'
```

### Summarization

#### `POST /v1/summarize` — Summarize a video file

**Required fields:** `model`, `scenario`, `events`

**Video source:** provide `url` (HTTP/S3 URL) OR `id` (pre-uploaded asset UUID) — not both.

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | Model ID from `GET /models` |
| `scenario` | string | Use-case context: `"warehouse"`, `"retail"`, `"security"`, etc. |
| `events` | array[string] | Events to detect. Pass `[]` if not detecting events. |

**Common optional fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | — | HTTP/S3 URL to video |
| `id` | string | — | Pre-uploaded asset UUID |
| `prompt` | string | `""` | Custom prompt sent to the VLM |
| `chunk_duration` | integer | `0` | Split video into N-second chunks. `0` = entire video as one chunk |
| `chunk_overlap_duration` | integer | `0` | Overlap between adjacent chunks in seconds |
| `max_tokens` | integer | — | Maximum tokens per chunk |
| `temperature` | number | — | Sampling temperature (0–1) |
| `schema` | string | — | JSON schema string for structured output extraction |
| `enable_vlm_structured_output` | boolean | `true` | VLM generates structured JSON. Set `false` for plain text |
| `enable_audio` | boolean | `false` | Transcribe audio track alongside video |
| `enable_reasoning` | boolean | `false` | Enable VLM chain-of-thought reasoning |
| `media_info` | object | — | Process only a portion of the video |
| `objects_of_interest` | array[string] | `[]` | Objects to focus on |

**Example:**

```sh
curl -s -X POST http://localhost:38111/v1/summarize \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cosmos-reason1",
    "scenario": "warehouse",
    "events": ["safety violation", "unauthorized access"],
    "url": "https://example.com/video.mp4",
    "chunk_duration": 60,
    "prompt": "Describe all activity with timestamps."
  }' | jq '.choices[0].message.content'
```

**Response (200):**

```json
{
  "id": "uuid",
  "video_id": "uuid",
  "choices": [
    {
      "index": 0,
      "finish_reason": "stop",
      "message": {
        "role": "assistant",
        "content": "[00:00 - 01:00] A worker walks down the aisle..."
      }
    }
  ],
  "created": 1717405636,
  "model": "cosmos-reason1",
  "media_info": {"type": "offset", "start_offset": 0, "end_offset": 3600},
  "object": "summarization.completion",
  "usage": {
    "query_processing_time": 78,
    "total_chunks_processed": 5
  }
}
```

### Livestream APIs (requires `KAFKA_ENABLED=true`)

Livestream summarization uses a two-phase approach:

#### Phase 1: `POST /v1/generate_captions` — Start stream captioning

Fire-and-forget: kicks off VLM captioning on RT-VLM for a stream previously added via
RT-VLM `stream/add`. Returns immediately once RT-VLM acknowledges.

```sh
curl -s -X POST http://localhost:38111/v1/generate_captions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "<stream-id>",
    "model": "cosmos-reason1",
    "chunk_duration": 30,
    "scenario": "warehouse",
    "events": ["safety violation"]
  }'
```

#### Phase 2: `POST /v1/stream_summarize` — Summarize a stream

Aggregates existing captions from the database via CA-RAG and returns a structured summary.

```sh
curl -s -X POST http://localhost:38111/v1/stream_summarize \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "<stream-id>",
    "model": "cosmos-reason1",
    "start_time": "2024-01-01T00:00:00Z",
    "end_time": "2024-01-01T01:00:00Z"
  }'
```

### Recommended Config

#### `POST /recommended_config` — Get recommended chunking parameters

```sh
curl -s -X POST http://localhost:38111/recommended_config \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "video_length": 300,
    "target_response_time": 60,
    "usecase_event_duration": 5
  }'
```

### Metrics

#### `GET /metrics` — Prometheus metrics

```sh
curl -s http://localhost:38111/metrics
```

---

## MCP (Model Context Protocol) Server

Video Summarization includes an MCP server that exposes the same functionality as the REST API
through MCP tools. MCP is enabled by default.

### Enabling MCP Server

```sh
export LVS_ENABLE_MCP=true    # Enable MCP server (default: true)
export LVS_MCP_PORT=38112     # Port for MCP server (default: 38112)
```

When enabled, the MCP server runs on SSE (Server-Sent Events) transport alongside the REST API.

### MCP Tools Available

- **health_ready**: Check if the server is ready to accept requests
- **health_live**: Check if the server is alive
- **list_models**: List available VLM models
- **summarize_video**: Generate a summary of video content
- **generate_vlm_captions**: Generate VLM captions for video frames
- **get_recommended_config**: Get recommended configuration for video processing
- **get_metrics**: Get server metrics in Prometheus format

### Accessing the MCP Server

```text
http://<host>:38112/sse
```

---

## Error Reference

| Code | Meaning | Common Cause |
|------|---------|--------------|
| 400 | Bad Request | Missing required field, invalid URL format, malformed JSON |
| 401 | Unauthorized | Missing or invalid `Authorization: Bearer` header |
| 422 | Unprocessable | Extra unknown field, wrong type, value out of range |
| 429 | Rate Limited | Too many concurrent requests |
| 500 | Server Error | VLM inference failure, GPU OOM, internal error |
| 503 | Server Busy | Processing another file/stream — retry with backoff |

## Important Notes

- **`model`, `scenario`, and `events` are always required** for `/v1/summarize` — even when not
  detecting specific events, pass `"events": []`.
- **`enable_vlm_structured_output` defaults to `true`** — for plain text captions, explicitly set
  `"enable_vlm_structured_output": false`.
- **`chunk_duration: 0` means no chunking** — for videos longer than ~5 minutes, set
  `chunk_duration` to 60–120 seconds to avoid timeout or OOM.
- **503 means busy, not failed** — implement retry with exponential backoff (start at 5–10s).
- **`schema` is a JSON string, not an object** — pass the JSON schema as a string value.
- **`/v1/ready` vs `/v1/live`** — always use `/v1/ready` before sending requests.

## Force Software Decoder for AV1 Streams

For platforms where hardware AV1 decoding is not supported:

```sh
FORCE_SW_AV1_DECODER=true
```
