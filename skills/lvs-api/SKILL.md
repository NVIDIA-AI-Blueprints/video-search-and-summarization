---
name: lvs-api
description: >
  Interact with the Long Video Summarization (LVS) API. Use this skill when working with LVS,
  calling the summarize endpoint, listing LVS models, checking LVS health, getting recommended
  chunk sizes, or querying LVS Prometheus metrics. Also use when debugging LVS responses,
  building integrations with the VIA engine, asking "how do I summarize a video with LVS",
  "what models are available in LVS", or "how do I call the LVS microservice".
argument-hint: "[endpoint or workflow description]"
allowed-tools: Bash(curl *), Bash(jq *)
---

# Long Video Summarization (LVS) API

Accelerated video summarization and insight extraction. LVS accepts a video URL (HTTP/S3)
or a pre-uploaded asset ID, processes it with a configurable Vision-Language Model (VLM),
and returns timestamped captions, summaries, or structured event detections.

## Setup

```bash
export BASE_URL="http://localhost:8000"   # Replace with your deployed LVS host:port
export API_KEY="your-api-key"             # Bearer token — set VIA_VLM_API_KEY in your .env
```

All endpoints use `Authorization: Bearer $API_KEY`. Check readiness before sending requests.

## media-server sidecar for sample videos

When LVS is launched alongside its `media-server` sidecar — either via `compose/` stacks in
this repo (see `compose/media-server.yaml`) or via a helm-based deployment — the sidecar is
reachable from the LVS container at the hostname `media-server` on port 80. The service name
is the same in both compose and helm deployments, so requests look identical.

A one-shot `downloader` container pulls sample videos from
`artifactory.nvidia.com/.../via-engine/media/perf/reencode/` using `ARTIFACTORY_USER` /
`ARTIFACTORY_TOKEN`, drops them in a shared volume (`via-media-data` in compose), and exits.
Nginx then serves that volume.

**How to reference these files in requests:** pass `url: "http://media-server/<filename>"`.
The hostname `media-server` resolves via internal DNS (docker compose network or kubernetes
service DNS) — it is reachable from the LVS container, not from the host. Do not use
`localhost` or the artifactory URL (LVS has no creds for artifactory; the download will 401).

**Sample videos available** (durations match filenames):

```
0.5min.mp4   1min.mp4     2min.mp4     5min.mp4     10min.mp4
30min.mp4    60min.mp4    120min.mp4   720min.mkv
```

The nginx config also adds a rewrite: `1min-3.mp4`, `10min-99.mp4`, etc. all alias back to
the base file (`1min.mp4`, `10min.mp4`). This lets you hit distinct URLs without changing
the payload — useful for defeating URL-keyed dedup caches during benchmarks.

Quick list from a live stack:
```bash
# Find the running media-server container (name varies by compose project)
docker ps --format '{{.Names}}' | grep media-server
docker exec <media-server-container> ls /usr/share/nginx/html/
```

**Content note:** despite the `perf/reencode` origin, not every sample is traffic/warehouse
footage — check content before choosing `scenario`/`events`. A mismatch is harmless (the VLM
describes what it actually sees), but makes event detection miss.

**Important:** these sample files do *not* include `bp_preview/its_264.mp4` or other
`bp_preview` content — only the `perf/reencode/` set listed above.

## Quick Start

List models first to get the correct model ID for your deployment, then summarize:

```bash
# Get available model IDs
curl -s "$BASE_URL/models" -H "Authorization: Bearer $API_KEY" | jq '.data[].id'

# Summarize a video by URL
curl -s -X POST "$BASE_URL/v1/summarize" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cosmos-reason1",
    "scenario": "warehouse",
    "events": ["safety violation", "unauthorized access"],
    "url": "https://example.com/video.mp4",
    "prompt": "Describe safety-relevant events with timestamps."
  }' | jq '.choices[0].message.content'
```

---

## Endpoints

### Health Check

#### `GET /v1/ready` — Readiness probe

Returns 200 only when LVS is fully initialized and ready. Use before sending summarization
requests. Returns 500 if model loading is still in progress.

```bash
curl -s "$BASE_URL/v1/ready"
```

#### `GET /v1/live` — Liveness probe

Returns 200 if the process is alive. Does **not** indicate model readiness — use `/v1/ready` instead.

```bash
curl -s "$BASE_URL/v1/live"
```

#### `GET /v1/startup` — Startup probe

Returns 200 once the initial startup sequence is complete.

```bash
curl -s "$BASE_URL/v1/startup"
```

#### `GET /v1/metadata` — Service metadata

Returns version, build info, and service configuration.

```bash
curl -s "$BASE_URL/v1/metadata" -H "Authorization: Bearer $API_KEY"
```

---

### Models

#### `GET /models` — List available models

Lists all VLM models configured in this LVS deployment. The `id` field is what you pass
as `model` in summarization requests. Model availability depends on deployment config.

```bash
curl -s "$BASE_URL/models" \
  -H "Authorization: Bearer $API_KEY" | jq '.data[] | {id, owned_by, api_type}'
```

**Response (200):**
```json
{
  "object": "list",
  "data": [
    {
      "id": "cosmos-reason1",
      "created": 1686935002,
      "object": "model",
      "owned_by": "NVIDIA",
      "api_type": "internal"
    }
  ]
}
```

---

### Summarization

Both `/v1/summarize` (versioned, preferred) and `/summarize` (unversioned, legacy) accept
the same request body and return the same response format.

#### `POST /v1/summarize` — Summarize a video

**Required fields:** `model`, `scenario`, `events`
**Video source:** provide `url` (HTTP/S3 URL) OR `id` (asset UUID) — not both.

**Required fields:**

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | Model ID from `GET /models` (e.g., `"cosmos-reason1"`) |
| `scenario` | string | Use-case context: `"warehouse"`, `"retail"`, `"security"`, etc. |
| `events` | array[string] | Events to detect, e.g. `["safety violation", "fire"]`. Pass `[]` if not detecting events. |

**Commonly used optional fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | — | HTTP/S3 URL to video. Use `id` instead for pre-uploaded assets. |
| `id` | string/array | — | Asset UUID(s) from the asset manager upload flow. |
| `prompt` | string | `""` | Custom prompt sent to the VLM. |
| `chunk_duration` | integer | `0` | Split video into N-second chunks. `0` = process entire video as one chunk. |
| `chunk_overlap_duration` | integer | `0` | Overlap between adjacent chunks in seconds. |
| `max_tokens` | integer | — | Maximum tokens to generate per chunk. |
| `temperature` | number 0–1 | — | Sampling temperature. Higher = more variation. |
| `schema` | string | — | JSON schema **string** for structured output extraction. |
| `enable_vlm_structured_output` | boolean | `true` | VLM generates structured JSON by default. Set `false` for plain text. |
| `enable_audio` | boolean | `false` | Transcribe the audio track alongside video. |
| `enable_vlm_structured_output` | boolean | `True` | VLM generates structured JSON by default. Set `false` for plain text. |
| `enable_audio` | boolean | `False` | Transcribe the audio track alongside video. |
| `enable_reasoning` | boolean | `False` | Enable VLM chain-of-thought reasoning (Cosmos Reason1). |
| `media_info` | object | — | Process only a portion of the video (see below). |
| `source_type` | string | auto | `"file"` or `"stream"`. LVS infers from asset state if omitted. |
| `auto_generate_prompt` | boolean | — | Auto-generate a prompt from `schema` and `events`. |
| `objects_of_interest` | array[string] | `[]` | Objects to focus on: `["person", "forklift"]`. |
| `alert_category` | string | — | Alert category label for event-detection mode. |

**Basic summarization by URL:**
```bash
curl -s -X POST "$BASE_URL/v1/summarize" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cosmos-reason1",
    "scenario": "warehouse",
    "events": ["forklift activity", "worker safety violation"],
    "url": "https://example.com/warehouse-cam.mp4",
    "prompt": "Describe all activity with timestamps."
  }'
```

**Long video with chunking (recommended for videos > 5 minutes):**
```bash
curl -s -X POST "$BASE_URL/v1/summarize" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cosmos-reason1",
    "scenario": "retail",
    "events": ["theft", "unusual behavior"],
    "url": "https://example.com/store-footage.mp4",
    "chunk_duration": 60,
    "chunk_overlap_duration": 5,
    "prompt": "Detect any suspicious customer behavior."
  }'
```

**Process only part of a video (offset):**
```bash
curl -s -X POST "$BASE_URL/v1/summarize" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cosmos-reason1",
    "scenario": "security",
    "events": ["intrusion", "fighting"],
    "url": "https://example.com/full-day.mp4",
    "media_info": {"type": "offset", "start_offset": 3600, "end_offset": 7200}
  }'
```

**Structured output with JSON schema:**
```bash
SCHEMA='{"type":"object","properties":{"events":{"type":"array","items":{"type":"object","properties":{"timestamp":{"type":"string"},"type":{"type":"string"},"severity":{"type":"string"}}}}}}'

curl -s -X POST "$BASE_URL/v1/summarize" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"cosmos-reason1\",
    \"scenario\": \"security\",
    \"events\": [\"intrusion\", \"vandalism\"],
    \"url\": \"https://example.com/video.mp4\",
    \"schema\": $(echo $SCHEMA | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read().strip()))'),
    \"enable_vlm_structured_output\": true,
    \"auto_generate_prompt\": true
  }"
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
        "content": "[00:00 - 01:00] A worker walks down the aisle...",
        "tool_calls": []
      }
    }
  ],
  "created": 1717405636,
  "model": "cosmos-reason1",
  "media_info": {"type": "offset", "start_offset": 0, "end_offset": 3600},
  "object": "summarization.completion",
  "usage": {
    "query_processing_time": 78,
    "total_chunks_processed": 5,
    "summary_tokens": 100
  }
}
```

When alerts are detected, `choices[0].message.tool_calls` contains structured alert objects
with `name`, `offset` (timestamp), `detectedEvents`, and `details`.

---

### Recommended Config

#### `POST /recommended_config` — Get recommended chunking parameters

Ask LVS to suggest `chunk_duration` based on video length and latency requirements.
Run this before summarizing long videos to optimize chunking.

| Field | Type | Description |
|-------|------|-------------|
| `video_length` | integer | Total video duration in seconds |
| `target_response_time` | integer | How quickly you need a response (seconds) |
| `usecase_event_duration` | integer | How long target events typically last (seconds) |

```bash
curl -s -X POST "$BASE_URL/recommended_config" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "video_length": 300,
    "target_response_time": 60,
    "usecase_event_duration": 5
  }'
```

**Response (200):**
```json
{
  "chunk_size": 60,
  "text": "Recommended chunk size is 60 seconds..."
}
```

---

### Metrics

#### `GET /metrics` — Prometheus metrics

Returns LVS operational metrics in Prometheus text format.

```bash
curl -s "$BASE_URL/metrics" -H "Authorization: Bearer $API_KEY"
```

---

## Common Workflows

### Workflow 1: Summarize a Security Camera Feed End-to-End

```bash
# 1. Wait until LVS is ready
until curl -sf "$BASE_URL/v1/ready" > /dev/null; do
  echo "Waiting for LVS..."; sleep 5
done

# 2. Get the first available model
MODEL=$(curl -s "$BASE_URL/models" \
  -H "Authorization: Bearer $API_KEY" | jq -r '.data[0].id')
echo "Model: $MODEL"

# 3. Get recommended chunk size for a 5-minute video
CHUNK=$(curl -s -X POST "$BASE_URL/recommended_config" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"video_length": 300, "target_response_time": 30, "usecase_event_duration": 3}' \
  | jq -r '.chunk_size')
echo "Chunk size: ${CHUNK}s"

# 4. Summarize with optimal config
curl -s -X POST "$BASE_URL/v1/summarize" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"scenario\": \"security\",
    \"events\": [\"intrusion\", \"fighting\", \"loitering\"],
    \"url\": \"https://your-storage/camera-001.mp4\",
    \"chunk_duration\": $CHUNK,
    \"prompt\": \"Detect and timestamp any security incidents.\"
  }" | jq '{summary: .choices[0].message.content, video_id: .video_id}'
```

### Workflow 2: Batch-Summarize Multiple Videos

```bash
for VIDEO_URL in \
  "https://storage/cam-001.mp4" \
  "https://storage/cam-002.mp4" \
  "https://storage/cam-003.mp4"; do

  echo "--- Processing: $VIDEO_URL"
  RESULT=$(curl -s -X POST "$BASE_URL/v1/summarize" \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{
      \"model\": \"cosmos-reason1\",
      \"scenario\": \"warehouse\",
      \"events\": [\"safety violation\", \"forklift near person\"],
      \"url\": \"$VIDEO_URL\",
      \"chunk_duration\": 60,
      \"enable_vlm_structured_output\": false
    }")

  # Check for 503 (server busy) and retry once
  STATUS=$(echo "$RESULT" | jq -r '.code // "ok"')
  if [ "$STATUS" != "ok" ]; then
    echo "Server busy, retrying in 10s..."
    sleep 10
    RESULT=$(curl -s -X POST "$BASE_URL/v1/summarize" \
      -H "Authorization: Bearer $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"model\": \"cosmos-reason1\", \"scenario\": \"warehouse\", \"events\": [], \"url\": \"$VIDEO_URL\"}")
  fi

  echo "$RESULT" | jq '{video_id: .video_id, content: .choices[0].message.content}'
done
```

### Workflow 3: Structured Event Extraction

Extract structured JSON from video using a schema:

```bash
# Define target output schema
SCHEMA=$(cat <<'EOF'
{
  "type": "object",
  "properties": {
    "events": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "timestamp": {"type": "string"},
          "event_type": {"type": "string"},
          "severity": {"type": "string", "enum": ["low", "medium", "high"]},
          "description": {"type": "string"}
        }
      }
    }
  }
}
EOF
)

curl -s -X POST "$BASE_URL/v1/summarize" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg model "cosmos-reason1" \
    --arg scenario "security" \
    --argjson events '["fire","smoke","unauthorized access"]' \
    --arg url "https://example.com/video.mp4" \
    --arg schema "$SCHEMA" \
    '{model: $model, scenario: $scenario, events: $events, url: $url, schema: $schema,
      enable_vlm_structured_output: true, auto_generate_prompt: true}'
  )" | jq '.choices[0].message.content | fromjson'
```

---

## Error Reference

| Code | Meaning | Common Cause |
|------|---------|--------------|
| 400 | Bad Request | Missing required field, invalid URL format, malformed JSON |
| 401 | Unauthorized | Missing `Authorization: Bearer` header, or invalid API key |
| 422 | Unprocessable | Extra unknown field (all schemas use `additionalProperties: false`), wrong type, value out of allowed range |
| 429 | Rate Limited | Too many concurrent requests to this LVS instance |
| 500 | Server Error | VLM inference failure, GPU out-of-memory, internal processing error |
| 503 | Server Busy | LVS is processing another file or live stream — retry with backoff |

---

## Gotchas

- **`model`, `scenario`, and `events` are always required** — even when not detecting specific
  events, pass `"events": []`. Omitting any of these three fields returns 422.

- **`enable_vlm_structured_output` defaults to `true`** — the VLM generates structured JSON
  by default. For plain freeform text captions, explicitly set `"enable_vlm_structured_output": false`.

- **`chunk_duration: 0` means no chunking** — the entire video is sent to the VLM as one chunk.
  For videos longer than ~5 minutes, set `chunk_duration` to 60–120 seconds to avoid timeout or OOM.

- **`additionalProperties: false` on every schema** — any field not in the spec causes 422.
  The field list in this skill is exhaustive; unknown fields are not silently ignored.

- **503 means busy, not failed** — LVS processes one stream at a time per instance. On 503,
  implement retry with exponential backoff (start at 5–10s, retry 3–5 times). The job did not start.

- **`url` accepts S3 (`s3://bucket/key`) or HTTP(S) URLs only** — local file paths are not
  supported via `url`. Upload the file via the asset manager first to get an `id`.

- **The spec `servers[0].url` is `/` (relative)** — you must set `BASE_URL` to the actual
  deployed hostname and port. There is no default port in the spec.

- **`media_info.type` must match the source** — use `"offset"` (with `start_offset`/`end_offset`
  in seconds) for video files, and `"timestamp"` (ISO 8601 strings) for live streams.

- **`summary_duration` applies to live streams only** — this parameter is silently ignored for
  file-based requests. For files, use `chunk_duration` instead.

- **`schema` is a JSON string, not an object** — pass the JSON schema as a string value:
  `"schema": "{\"type\": \"object\", ...}"`. Do not pass a nested JSON object directly.

- **`/v1/ready` vs `/v1/live`** — `/v1/ready` checks full service readiness including model load.
  `/v1/live` only checks process liveness. Always use `/v1/ready` before sending summarize requests.
