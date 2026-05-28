---
name: vss-deploy-video-embedding
description: Use to deploy and operate the RT-Embed video-embedding microservice (Compose bring-up, /v1 REST, Redis/Kafka/OTel). Not for dense captioning or search.
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational deployment"
---
## Purpose

Stand up the RT-Embed video-embedding microservice, exercise its REST surface, and integrate it with Redis/Kafka/OTel.

## Instructions

Follow the routing tables and step-by-step workflows below. Each section that ends in *workflow*, *quick start*, or *flow* is intended to be executed top-to-bottom. Detailed reference material lives in `references/` and helper scripts live in `scripts/` — call them via `run_script` when the skill points to a script by name.

## Examples

Worked end-to-end examples are kept under `evals/` (each `*.json` manifest contains a runnable scenario) and inline in the per-workflow `curl` blocks below. Run a Tier-3 evaluation with `nv-base validate <this-skill-dir> --agent-eval` to replay them.

## Limitations

- Requires the matching VSS profile / microservice to be deployed and reachable from the caller.
- NGC-hosted models and NIMs may be subject to rate-limits, GPU memory requirements, and license restrictions.
- Concurrency, GPU memory, and storage limits depend on the host hardware and the profile's compose file.

# VSS Video Embedding (RT-Embed)

Use this skill when you need to:

- Deploy the VSS Video Embedding microservice from a Docker Compose file.
- Generate text or video embeddings against the Cosmos-Embed1-448p model.
- Embed an uploaded file, an HTTP/S3/file/data URL, or a live RTSP stream.
- Wire the service into a VSS deployment alongside Redis, Kafka, and OpenTelemetry.
- Triage readiness, model-download, GPU, or stream-reconnection failures.

**Trigger phrases:** `vss-deploy-video-embedding`, `RT-Embed`, `rtvi-embed`, `video embedding service`, `Cosmos-Embed1`, `embed live stream`, `embed video file`, `generate video embeddings`, `text embedding for video search`.

## Service Snapshot

- **VSS 3.2 GA skill:** `vss-deploy-video-embedding`.
- **Legacy 3.1 name:** RT-Embed.
- **Compose service:** `rtvi-embed`.
- **Container name:** `vss-rtvi-embed`.
- **Image:** `nvcr.io/nvidia/vss-core/vss-rt-embed` (override with `RTVI_EMBED_IMAGE`).
- **Default tag:** `3.2.0-26.05.4` (override with `RTVI_EMBED_TAG`).
- **Profile:** `bp_developer_search_2d`.
- **Container port:** `8000` (host-side `${RTVI_EMBED_PORT}`).
- **Default model:** `cosmos-embed1-448p` from `nvidia/Cosmos-Embed1-448p`.
- **Health endpoint:** `GET /v1/ready`.
- **Healthcheck startup grace:** `1200s` (20 minutes) on first boot.

## Prerequisites

Before bringing the service up:

1. NVIDIA driver + NVIDIA Container Toolkit installed; default runtime set to `nvidia`.
2. Docker Engine and Docker Compose plugin recent enough to support `${VAR:+value}` conditional volume substitution.
3. `docker login nvcr.io` completed with `$oauthtoken` and a valid NGC API key.
4. Host environment provides at minimum: `RTVI_EMBED_PORT`, `VSS_DATA_DIR`, `NGC_API_KEY`, and optionally `HF_TOKEN` to avoid Hugging Face 429 rate-limit errors during the Cosmos-Embed1 weights download.
5. Free disk space for persistent caches: `rtvi-hf-cache`, `rtvi-ngc-model-cache`, `rtvi-triton-model-repo` (multi-GB).

See `references/deploy-vss-deploy-video-embedding.md` for the full prerequisite list and `references/environment.md` for the variable matrix.

## Deploy

```bash
# Bring up the service under the required Compose profile.
docker compose -f rtvi-embed-docker-compose.yml \
  --profile bp_developer_search_2d up -d rtvi-embed

# Watch logs while the model downloads and Triton repo builds.
docker compose -f rtvi-embed-docker-compose.yml logs -f rtvi-embed
```

First-boot startup may take 20 minutes for the Cosmos-Embed1 download and Triton model repository build. Do not shorten the `start_period: 1200s` healthcheck during the first boot or the container will be marked unhealthy while still warming up.

### Verify

```bash
BASE_URL="http://localhost:${RTVI_EMBED_PORT}"

curl -fsS "$BASE_URL/v1/ready"               # 200 when warm.
curl -fsS "$BASE_URL/v1/ready?detailed=true" # Component-level status.
curl -fsS "$BASE_URL/v1/version"
curl -fsS "$BASE_URL/v1/models"              # Confirms cosmos-embed1-448p is loaded.
```

## Common Operations

### Generate video embeddings from an uploaded file

```bash
BASE_URL="http://localhost:${RTVI_EMBED_PORT}"

FILE_ID=$(curl -fsS -X POST "$BASE_URL/v1/files" \
  -F purpose=vision \
  -F media_type=video \
  -F file=@/path/to/clip.mp4 | jq -r .id)

curl -fsS -X POST "$BASE_URL/v1/generate_video_embeddings" \
  -H "Content-Type: application/json" \
  -d "{
    \"id\": \"$FILE_ID\",
    \"model\": \"cosmos-embed1-448p\",
    \"chunk_duration\": 60,
    \"chunk_overlap_duration\": 10
  }"
```

### Generate text embeddings (for text-to-video search)

```bash
curl -fsS -X POST "$BASE_URL/v1/generate_text_embeddings" \
  -H "Content-Type: application/json" \
  -d '{"text_input": "a forklift moving pallets", "model": "cosmos-embed1-448p"}'
```

### Embed a live RTSP stream

Live streams **require** `stream: true` and `chunk_duration > 0`. A synchronous call returns `400 BadParameters: "Only streaming output is supported for live-streams"`, and the `chunk_duration: 0` returned by `streams/add` is a placeholder — it must be overridden on the embed request or you get `400 BadParameter: "chunk_duration must be greater than 0"`.

`POST /v1/streams/add` does **not** deduplicate by `liveStreamUrl` — submitting the same URL twice mints two distinct `stream_id`s. Before adding, call `GET /v1/streams/get-stream-info` and reuse any existing registration for that URL to avoid orphaned entries.

```bash
STREAM_ID=$(curl -fsS -X POST "$BASE_URL/v1/streams/add" \
  -H "Content-Type: application/json" \
  -d '{"streams":[{"liveStreamUrl":"rtsp://host:port/live/video","description":"camera-001"}]}' \
  | jq -r '.results[0].id')

curl -N -X POST "$BASE_URL/v1/generate_video_embeddings" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d "{
    \"id\": \"$STREAM_ID\",
    \"model\": \"cosmos-embed1-448p\",
    \"stream\": true,
    \"chunk_duration\": 10,
    \"chunk_overlap_duration\": 2
  }"

# List registered live streams (use this to recover stream_ids across sessions).
curl -fsS "$BASE_URL/v1/streams/get-stream-info"

# Stop embedding for the stream when done (terminates SSE with data: [DONE]).
curl -fsS -X DELETE "$BASE_URL/v1/generate_video_embeddings/$STREAM_ID"
```

See `references/rest-api.md` for the full endpoint catalog, SSE streaming, and single-stream control-plane patterns.

## Logs, Metrics, And Status

```bash
docker compose -f rtvi-embed-docker-compose.yml ps
docker compose -f rtvi-embed-docker-compose.yml logs -f rtvi-embed
docker stats vss-rtvi-embed

curl -fsS "$BASE_URL/v1/metrics"          # Prometheus.
curl -fsS "$BASE_URL/v1/assets/stats"     # Asset storage counts and TTL.
```

If `RTVI_EMBED_LOG_DIR` is bound to a host directory, log files are also available at `/opt/nvidia/rtvi/log/rtvi/` on the host.

## Integration Surface

- **Inputs:** REST API on `:${RTVI_EMBED_PORT}` (`POST /v1/files`, `POST /v1/generate_text_embeddings`, `POST /v1/generate_video_embeddings`, live-stream control endpoints).
- **Outputs:** Synchronous REST responses, optional SSE for chunked video embeddings, optional Kafka messages on the topics named by `RTVI_EMBED_KAFKA_TOPIC` (container `KAFKA_TOPIC`) and `RTVI_EMBED_ERROR_MESSAGE_TOPIC` (container `ERROR_MESSAGE_TOPIC`) when Kafka is enabled (host: `RTVI_EMBED_KAFKA_ENABLED=true`, which Compose maps to container `KAFKA_ENABLED`).
- **Optional peers:** Redis (`ENABLE_REDIS_ERROR_MESSAGES=true`), Kafka (host: `RTVI_EMBED_KAFKA_ENABLED=true` → container `KAFKA_ENABLED`), OpenTelemetry collector (host: `RTVI_EMBED_ENABLE_OTEL_MONITORING=true` → container `ENABLE_OTEL_MONITORING`).

`references/integrate-vss-deploy-video-embedding.md` documents the full integration contract.

## Troubleshooting

For common failure patterns and resolutions, see `references/troubleshooting.md`. Frequent issues:

- `/v1/ready` stuck at 503 → check for missing `NGC_API_KEY`, Hugging Face 429 rate-limit failures during the first-boot model download (set `HF_TOKEN` to avoid), or unreachable Redis/Kafka peers when those flags are enabled.
- Healthcheck flipping unhealthy in the first 20 minutes → restore `start_period: 1200s`.
- Permission errors on bind-mounted cache directories → `chown -R 1001:1001` on the host paths.

## Upgrade And Rollback

See [Upgrade & Rollback](references/deploy-vss-deploy-video-embedding.md#upgrade--rollback)
in the deployment reference for the full image-swap and rollback steps; named
volumes persist across the swap.

## Tear Down

```bash
docker compose -f rtvi-embed-docker-compose.yml down       # keeps caches
docker compose -f rtvi-embed-docker-compose.yml down -v    # also removes the named caches; first re-start re-downloads the model and rebuilds the Triton repo (20+ min)
```

## References

See [`references/README.md`](references/README.md) for the full reference index
(deploy, integrate, API catalog, environment matrix, troubleshooting).

bump:1
