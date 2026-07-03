# Patch Reference: RT-Embedding (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator needs to fold the **Video Embedding** microservice (RT-Embed, image `vss-rt-embed`, container `vss-rtvi-embed`) into a generated deployment: the `component_services:` block, the Step 6.5 patch specifics, and the Kafka-topic wiring. It is NOT a microservice contract.

For the underlying RT-Embed API, env vars, ports, and known constraints, read the skill-neutral pair files in the RT-Embed skill:

- `skills/vss-deploy-video-embedding/references/integrate-vss-deploy-video-embedding.md` — integration contract (REST API, inputs/outputs, env vars, network, constraints).
- `skills/vss-deploy-video-embedding/references/deploy-vss-deploy-video-embedding.md` — deployment contract (image, GPU, storage, startup).

Schema for the `component_services:` block is in `references/component-services-schema.md`; the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is `references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc, which currently ships no such block) to learn which upstream compose service-key RT-Embed owns. Step 4 unions this block with the other selected microservices' patch files and writes the flat allow-list to `allow-list.yml`.
- **Step 6.5** reads ONLY the resulting sidecar and applies Patch 1 to the RT-Embed compose copy under `patched/services/rtvi/rtvi-embed/`.

## component_services block

RT-Embed is a single service, single variant — no `variants:` block. It carries no upstream `depends_on` and no relative bind mounts, so Patch 2 and Patch 3 are no-ops for it.

```yaml
component_services:
  - key: rtvi-embed
    file: services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml
    role: Cosmos-Embed1 video / frame / text embedding service on Triton. REST :8017; optional Kafka publish of embedding events.
```

The generated `allow-list.yml` must preserve this key exactly (`rtvi-embed`).

## Required peers

- **Kafka** — contributed by ELK's `component_services:` (`services/infra/compose.yml`). RT-Embed publishes raw embedding events to topic **`mdx-embed`** when `RTVI_EMBED_KAFKA_ENABLED=true` (→ container `KAFKA_ENABLED`/`KAFKA_TOPIC`).
- **BA — `vss-search-analytics-2d-fusion` (REQUIRED when embeddings must reach Elasticsearch).** The fusion-search-analytics app consumes `mdx-embed` and produces `mdx-embed-filtered`, the only topic ELK indexes into `mdx-embed-filtered-<date>`. It lives in RT-CV's upstream compose (`video-analytics-2d-app/compose.yml`); keep it in the patched RT-CV compose. See the topic-wiring section below.
- **VIOS clip storage (optional)** — RT-Embed reads `${VSS_DATA_DIR}/data_log/vst/clip_storage` (absolute bind) for on-demand embedding of VST-written clips. Present whenever VIOS is in the deployment.
- **Redis / OTEL (optional)** — off by default (`ENABLE_REDIS_ERROR_MESSAGES=false`, `RTVI_EMBED_ENABLE_OTEL_MONITORING=false`); do not wire unless requested.

## Kafka topic wiring — rtvi-embed → `mdx-embed` → BA → `mdx-embed-filtered` → ES

**RT-Embed does NOT publish to `mdx-embed-filtered` directly. It publishes its raw embeddings to `mdx-embed` (its own output topic), and the fusion-search-analytics service ("BA") consumes `mdx-embed`, filters, and produces `mdx-embed-filtered`, which ELK/Logstash then indexes into `mdx-embed-filtered-<date>`.** The full designed flow (identical for a live/RTSP source and a static/uploaded video) is:

```
rtvi-embed --(RTVI_EMBED_KAFKA_TOPIC=mdx-embed)--> Kafka mdx-embed
    --> vss-search-analytics-2d-fusion (BA)  [consumes mdx-embed + mdx-raw + mdx-behavior;
                                              applies objectConfidenceThreshold, embedEnableDownsampling]
    --> Kafka mdx-embed-filtered
    --> ELK Logstash --> Elasticsearch mdx-embed-filtered-<date>
```

Topic evidence in-repo: rtvi-embed default `services/rtvi/rtvi-embed/.env` → `RTVI_EMBED_KAFKA_TOPIC=mdx-embed` (compose maps it to container `KAFKA_TOPIC`); BA config `developer-profiles/dev-profile-search/video-analytics-2d-app/vss-search-analytics/configs/vss-search-analytics-kafka-config.json` → `embed:mdx-embed` (in) / `embedFiltered:mdx-embed-filtered` (out); Logstash indexes `mdx-embed-filtered` (not `mdx-embed`); `dev-profile-search/.env` → `ELASTIC_SEARCH_INDEX=mdx-embed-filtered-2025-01-01`.

**Keep the upstream default; do NOT override the topic to `mdx-embed-filtered`.** The generated `.env` should set:

```bash
RTVI_EMBED_KAFKA_ENABLED=true            # → container KAFKA_ENABLED
RTVI_EMBED_KAFKA_TOPIC=mdx-embed         # → container KAFKA_TOPIC; upstream default — KEEP, do NOT set mdx-embed-filtered
RTVI_EMBED_PORT=8017                      # ${RTVI_EMBED_PORT?} is a required-var substitution
RT_EMBED_DEVICE_ID=<gpu>                  # RT-Embed GPU (distinct from RT_CV_DEVICE_ID)
MODEL_PATH=git:https://huggingface.co/nvidia/Cosmos-Embed1-448p-anomaly-detection
NGC_API_KEY=${NGC_CLI_API_KEY}            # first-boot model pull
HF_TOKEN=                                 # optional; recommended (avoids HF 429s)
```

> **Required for "embeddings stored in ES": include `vss-search-analytics-2d-fusion` (BA).** It lives in RT-CV's upstream compose (`developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`) and is the ONLY producer of `mdx-embed-filtered`; ELK only indexes `mdx-embed-filtered`, never the raw `mdx-embed`. If BA is omitted, embeddings reach `mdx-embed` but never Elasticsearch. **Do not drop `vss-search-analytics-2d-fusion` from the patched RT-CV compose** for an ingestion+embeddings+ES profile.
>
> ⚠️ Two mistakes silently break "embeddings in ES": (1) omitting `vss-search-analytics-2d-fusion`, and (2) overriding `RTVI_EMBED_KAFKA_TOPIC=mdx-embed-filtered` so rtvi-embed publishes straight to the filtered topic. Either bypasses BA's confidence/downsampling filter; the second also produces unfiltered records. Always route rtvi-embed → `mdx-embed` → BA → `mdx-embed-filtered`.

**Index date suffix (same rule as RT-CV):** the ES `mdx-embed-filtered-<date>` suffix comes from the embedding record's timestamp. A **live/RTSP** source is wall-clock-stamped → `mdx-embed-filtered-<today>`. A **static/uploaded** video is offset-based and defaults to epoch 0 → the wrong `mdx-embed-filtered-1970-01-01` unless `creation_time:"2025-01-01T00:00:00.000Z"` is passed on `generate_video_embeddings`, which lands it in `mdx-embed-filtered-2025-01-01` (the search-profile default index). Validate the full chain (see `references/validation-harness.md § 6`).

## Ingestion & embedding modes (live vs static)

RT-Embed embeds a source two ways; the skill picks based on the Step-4 ingestion option:

- **Live RTSP (option 1 / NvStreamer):** `POST /v1/streams/add` (the `{"streams":[{...}]}` envelope, `liveStreamUrl`) → `POST /v1/generate_video_embeddings` on the returned id with `stream:true` + `chunk_duration>0`. Chunks are NTP/wall-clock-stamped.
- **Static uploaded video (option 3a / VIOS upload):** embed by **`url`** directly on `POST /v1/generate_video_embeddings` (`is_live=False`) — do **NOT** call `/v1/streams/add`. Pass `creation_time` for the correct index suffix. `VideoEmbeddingsQuery` accepts `url`, `creation_time`, `media_type`, `chunk_duration`, `chunk_overlap_duration`, `stream`, `url_headers` (confirmed from the live OpenAPI). `url` schemes: `http/https/s3/file/data`; `file://` is gated by `FILE_URL_ALLOWED_DIRS`.
- **Single-worker lock keyed by `videoId`** (derived from the URL), not the request `id`: a second `generate_video_embeddings` at the same URL before the first finishes returns `409 ResourceInUse`. A leftover live-stream embed holds the worker until deleted (`DELETE /v1/streams/delete/<id>` + `DELETE /v1/generate_video_embeddings/<id>`), or restart the container to clear a wedged queue.

## Patch specifics (Step 6.5)

Applied to the patched copy under `<BUILD_DIR>/patched/services/rtvi/rtvi-embed/`; the upstream tree is never modified.

- **Patch 1** — append the invented flag to `rtvi-embed`'s `profiles:` list (upstream: inline `["bp_developer_search_2d"]`).
- **Patch 2** — no-op (RT-Embed declares no `depends_on`).
- **Patch 3** — no-op (no relative bind sources; the `clip_storage` mount is `${VSS_DATA_DIR}/...` absolute).

## Deployment notes (Step 5 / deploy skill)

- **GPU:** reserve `device_ids: ["${RT_EMBED_DEVICE_ID}"]`; place on a GPU distinct from RT-CV (`RT_CV_DEVICE_ID`) when both are present.
- **Cold boot:** `start_period: 1200s`. First boot downloads `nvidia/Cosmos-Embed1-448p-anomaly-detection` (HF/NGC) and builds a Triton/TRT engine. `/v1/ready` stays 503 until the engine is built. `HF_TOKEN` avoids anonymous-pull 429s.
- **Model id:** callers must use the id returned by `GET :8017/v1/models` (e.g. `cosmos-embed1-448p-anomaly-detection`), not a hardcoded string.
- **Smoke:** see `references/validation-harness.md § 6` — VIOS → RT-Embed (live via NvStreamer § 6a, or static upload by URL § 6b) → BA → ES, asserting the full chain: `mdx-embed` advances (rtvi-embed output) → `mdx-embed-filtered` advances (BA output) → a non-zero `mdx-embed-filtered-<date>` ES doc count.
