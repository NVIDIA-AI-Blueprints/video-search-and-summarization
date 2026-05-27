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

### Database backend

The compose file deploys **Elasticsearch** as the database backend for caption storage,
summarization, and CA-RAG retrieval.

> **Note:** Kafka and Logstash are *profile-gated* — they start only when the `kafka` Compose
> profile is active.

### Set environment variables

Export the following environment variables or save them to a `.env` file in the repo root directory:

```sh
# Mandatory
export NGC_API_KEY=<>              # Required for nvcr.io images and NIM model access
export NVIDIA_API_KEY=<>           # Required for CA-RAG LLM
export BACKEND_PORT=38111          # LVS REST API port

# LLM Configuration (summarization)
export LVS_LLM_HOST=<>             # Hostname of the summarization LLM (e.g. gpt-oss-20b)
export LVS_LLM_PORT=<>             # Port of the summarization LLM (e.g. 9233)
export LVS_LLM_MODEL_NAME=<>      # LLM model name (e.g. openai/gpt-oss-20b)

# Database Backend
export LVS_DATABASE_BACKEND=elasticsearch_db      # Elasticsearch backend
export ES_HOST=elasticsearch                      # Elasticsearch hostname (default: elasticsearch)
export ES_PORT=9200                               # Elasticsearch port (default: 9200)

# RT-VLM (Video Language Model backend)
export RTVI_VLM_URL=http://rtvi-vlm:8000     # URL of the RT-VLM service

# Optional — Secrets
export HF_TOKEN=<>                 # HuggingFace token for gated models
export OPENAI_API_KEY=<>           # OpenAI-compatible endpoint swaps
export VIA_VLM_API_KEY=<>          # External VLM API auth

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

### Example `.env` file

```sh
NGC_API_KEY=nvapi-XXXXXXXXXXXXX
NVIDIA_API_KEY=nvapi-XXXXXXXXXXXXX
BACKEND_PORT=38111

LVS_LLM_HOST=gpt-oss-20b
LVS_LLM_PORT=9233
LVS_LLM_MODEL_NAME=openai/gpt-oss-20b

# Database backend
LVS_DATABASE_BACKEND=elasticsearch_db

# MCP Server
LVS_ENABLE_MCP=true
LVS_MCP_PORT=38112
```

### Start Video Summarization using Docker Compose

The compose file is available at
[docker/deploy/compose.yaml](docker/deploy/compose.yaml).

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

All endpoints accept `Authorization: Bearer <API_KEY>` (set via `VIA_VLM_API_KEY`).

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
