# Integration Reference: RT-VLM

## Overview

RT-VLM (Real-Time VLM Microservice) is a FastAPI-based REST API service providing real-time video understanding via Vision Language Models. It exposes a `/v1` REST surface (`POST /v1/generate_captions`, `POST /v1/files`, `POST /v1/streams/add`, OpenAI-compatible `/v1/chat/completions`) and publishes structured caption + incident records to Kafka using the NvSchema protobuf wire format. Sourced from `met-blueprint-docs/real-time-vlm.rst` § Overview / Key Features and § Architecture.

Use this service when the workflow requires either (a) dense captioning of a stored video file on demand (VOD path: upload → `/v1/files`, then `POST /v1/generate_captions` with the returned `file_id`) or (b) streaming dense captioning over an RTSP live stream (`POST /v1/streams/add`, then `POST /v1/generate_captions` with the returned `stream_id`). Both modes drive the same VLM and emit the same Kafka schema; the difference is only the input source. IN-1's "streaming + on-demand video dense captioning" capability requires RT-VLM with both modes wired.

## Required Peer Services

- **Kafka** — required when `KAFKA_ENABLED=true` (the compose default). RT-VLM publishes caption + incident protobuf records to `${KAFKA_TOPIC}` and `${KAFKA_INCIDENT_TOPIC}`. Broker must be reachable at `${KAFKA_BOOTSTRAP_SERVERS}` (compose default `${HOST_IP}:9092` for host-network Kafka). Source: `real-time-vlm.rst` § Sample docker-compose.yml file (lines 343–349). Setting `KAFKA_ENABLED=false` makes Kafka optional — captions then return only via HTTP.
- **Sibling VLM backend** — required when `VLM_MODEL_TO_USE=openai-compat`, which is the compose default. Either a sibling NIM (`cosmos-reason1-7b`, `cosmos-reason2-8b`, `qwen3-vl-8b-instruct`) reachable at `${VIA_VLM_ENDPOINT}`, OR remote OpenAI/Azure. In-process operation requires switching `VLM_MODEL_TO_USE` to `cosmos-reason1` / `cosmos-reason2` / `cosmos-reason3` / `vllm-compatible` and pointing `MODEL_PATH` at an NGC or git URL. Source: `real-time-vlm.rst` § Models Supported (lines 62–87).
- **Video source** — RT-VLM accepts video input via **three independent paths**:
  1. **On-demand (VOD) via VIOS-shared bind mount.** Operator uploads to VIOS `POST /vst/api/v1/files`, file lands in `${VSS_DATA_DIR}/data_log/vst/clip_storage/`, RT-VLM mounts the same host directory at `/home/vst/vst_release/streamer_videos/` and can reference the file path in `POST /v1/files` or `POST /v1/generate_captions`. **VIOS is the producer; RT-VLM is the reader.** Both must mount the same host path or the path-based caption call gets "file not found".
  2. **On-demand (VOD) via direct URL.** Send `POST /v1/files` to RT-VLM with `{url: "https://...|s3://...|file://..."}` — RT-VLM downloads and processes directly. VIOS is NOT involved. Available 3.2.0+ per `real-time-vlm.rst` § New API Capabilities lines 131–135.
  3. **Live RTSP via direct registration on RT-VLM.** Send `POST /v1/streams/add` (or 3.2.0 alias `POST /v1/stream/add`) to RT-VLM with an RTSP URL — RT-VLM connects to the RTSP source directly and ingests frames. **This endpoint is on RT-VLM (port 8000 internal / `${RTVI_VLM_PORT}` host), NOT on VIOS.** VIOS does not auto-relay sensor-add events to RT-VLM (verified 2026-05-23: rtvi-vlm has no `KAFKA_CONSUMER_TOPIC` or `sensor.id` subscriber). If the user wants RT-VLM to caption a stream that VIOS is also recording, the operator must register the stream on both services independently (or use the same source's stable VIOS-proxy URL `rtsp://<host>:30554/live/<sensorId>` as the input to RT-VLM's `/v1/streams/add`).

  Source: `real-time-vlm.rst` § Streaming Example lines 558–627 + § CV-Compatible Stream API lines 648–682; verified live 2026-05-23.
- **Redis** — optional. Required only when `ENABLE_REDIS_ERROR_MESSAGES=true` to publish VLM/decode errors onto a Redis channel. Source: `real-time-vlm.rst` § Sample docker-compose.yml (lines 350–354).
- **Elasticsearch + Logstash + Kibana (via ELK stack)** — optional but standard for IN-1: Logstash consumes the `${KAFKA_TOPIC}` and `${KAFKA_INCIDENT_TOPIC}` from Kafka and writes documents into Elasticsearch. Schema compatibility is mediated by the NvSchema protobuf descriptors (`schema.desc` + `ext.desc`) shared between RT-VLM (producer) and Logstash (consumer). Source: `integrate-elk.md` § Required Peer Services + § Known Integration Constraints.

Sibling NIM backends (`cosmos-reason1-7b`, `cosmos-reason2-8b`, `qwen3-vl-8b-instruct`, plus their `-shared-gpu` variants) live in a separate `services/nim/compose.yml` and have their own forthcoming `integrate-vlm-nim.md`. When a profile uses the in-process backend (the IN-1 default), those NIM service-keys MUST NOT appear in the allow-list and Patch 2 will strip the corresponding `depends_on: { required: false }` entries from rtvi-vlm.

### Structured component_services (consumed by `vss-build-vision-agent` Step 4)

See `references/component-services-schema.md` for the schema.

```yaml
component_services:
  # RT-VLM itself — required, single variant (the in-process vLLM backend; no sibling NIM).
  - key: rtvi-vlm
    file: services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml
    role: VLM inference service emitting captions on Kafka topic ${KAFKA_TOPIC}.
```

Sibling NIM backends (`cosmos-reason1-7b`, `cosmos-reason2-8b`, `qwen3-vl-8b-instruct`, plus their `-shared-gpu` variants) live in a separate `services/nim/compose.yml` and have their own forthcoming `integrate-vlm-nim.md`. When a profile uses the in-process backend (the IN-1 default), those NIM service-keys MUST NOT appear in the allow-list and Patch 2 will strip the corresponding `depends_on: { required: false }` entries from rtvi-vlm.

## Integration Interfaces

### Inputs

- **Method:** REST — `POST /v1/files` (multipart upload, also accepts HTTP/S/S3/file URLs via the new 3.2.0 URL-based ingestion path)
  **Endpoint:** `http://<host>:${RTVI_VLM_PORT}/v1/files`
  **Schema:** multipart body `{file, purpose, media_type, creation_time}` OR JSON body `{url, media_type, creation_time, url_headers}` for URL-based ingestion (3.2.0+). Returns `{id, ...}` where `id` is the file handle to pass into `/v1/generate_captions`. Source: `real-time-vlm.rst` § Dense Captioning Example (lines 514–528) + § New API Capabilities (lines 131–135).
  **Auth:** Bearer token when gated; bearer required for the NIM backend pull (`NGC_API_KEY` / `VIA_VLM_API_KEY`).

- **Method:** REST — `POST /v1/streams/add` (RTSP live-stream registration, VSS-style)
  **Endpoint:** `http://<host>:${RTVI_VLM_PORT}/v1/streams/add`
  **Schema:** JSON body `{streams: [{liveStreamUrl, description, ...}]}`. Returns `{results: [{id, ...}]}` where `id` is the stream handle. **Duplicate stream IDs now return HTTP 409 `DuplicateStreamId`** instead of silently overwriting (3.2.0 breaking change). Source: `real-time-vlm.rst` § Breaking Changes (lines 96–102) + § Streaming Example (lines 575–593).
  **Auth:** Bearer token when gated.

- **Method:** REST — `POST /v1/stream/add` (CV-compatible alias, 3.2.0+)
  **Endpoint:** `http://<host>:${RTVI_VLM_PORT}/v1/stream/add`
  **Schema** (verified live 2026-05-23 via the FastAPI OpenAPI at `/openapi.json`): JSON body uses the key/value envelope `{key: "<key-string>", value: {camera_id, camera_url, change: "camera_add", camera_name?, creation_time?, metadata?}, headers?: {source?, created_at?}}`. The `change` field is required and must be one of the change-type strings (e.g., `camera_add`). Returns `{camera_id, asset_id, status, inference}`. **NOTE:** the `.rst` docs at `real-time-vlm.rst` § CV-Compatible Stream API (lines 661–676) show a flat `{camera_id, url, metadata}` schema; the actual running service requires the `{key, value: {...}}` envelope. Cross-check with `GET /openapi.json` on the live container before authoring clients. Companion endpoints: `POST /v1/stream/remove`, `GET /v1/stream/get-stream-info`. Source: live OpenAPI inspection 2026-05-23 + `real-time-vlm.rst` § CV-Compatible Stream API lines 648–682.
  **Auth:** Bearer token when gated.

- **Method:** REST — `POST /v1/generate_captions` (note: renamed from 3.1.0's `/v1/generate_captions_alerts`)
  **Endpoint:** `http://<host>:${RTVI_VLM_PORT}/v1/generate_captions`
  **Schema:** JSON body `{id, prompt, model, chunk_duration, chunk_overlap_duration, stream, system_prompt?, enable_audio?, enable_reasoning?, ...}`. `id` is either a file_id or a stream_id from the steps above. Source: `real-time-vlm.rst` § Breaking Changes (line 96) + § Dense Captioning Example (lines 530–545).
  **Auth:** Bearer token when gated.

  > **Required-field gotchas (verified live 2026-05-23, expanded 2026-05-26):**
  > - `model` is required. The friendly string `cosmos-reason2` from the `.rst` examples is rejected with `BadParameters: No such model 'cosmos-reason2'`. Use the long form returned by `GET /v1/models` — for cosmos-reason2-8b loaded from `:1208-fp8-static-kv8` it's `nim_nvidia_cosmos-reason2-8b_1208-fp8-static-kv8`. **Format rule (verified 2026-05-26):** the live id is derived from the NGC artifact reference by replacing every `/` and `:` with `_` and dropping the leading `ngc:` if present (e.g. `ngc:nim/nvidia/cosmos-reason2-8b:1208-fp8-static-kv8` → `nim_nvidia_cosmos-reason2-8b_1208-fp8-static-kv8`). Clients should always discover the id from `GET /v1/models` rather than constructing it.
  > - `prompt` is required (no fallback — the field is non-nullable in the request schema). Omitting it returns `BadParameters: prompt must be provided`. The skill / caller must supply a captioning prompt verbatim; there is no built-in default. Source: live verification 2026-05-26.
  > - For **live streams** (`id` = stream_id from `/v1/streams/add`), `stream: true` is required (`BadParameters: Only streaming output is supported for live-streams`) and `chunk_duration` must be `> 0` (`chunk_duration must be greater than 0`). The response is a Server-Sent Events stream (`Content-Type: text/event-stream`) emitting one event per chunk.
  > - For **VOD files** (`id` = file_id from `/v1/files`), `stream: false` is allowed and the response is a single JSON. `stream: true` is also allowed and emits per-chunk SSE.

- **Method:** REST — `POST /v1/chat/completions` (OpenAI-compatible, text-only or with media)
  **Endpoint:** `http://<host>:${RTVI_VLM_PORT}/v1/chat/completions`
  **Schema:** OpenAI Chat Completions request body; supports multi-turn and token-level SSE streaming (3.2.0+). Source: `real-time-vlm.rst` § New API Capabilities (line 144).

### Outputs

- **Method:** HTTP response (SSE when `stream=true`, single-JSON when `stream=false`)
  **Endpoint:** response body of `POST /v1/generate_captions`
  **Schema:** `{id, object: "caption", chunk_responses: [{start_time, end_time, content, chunk_id?, reasoning_description?, audio_transcript?}], usage}`. Source: `kafka-workflows.md` § 4 HTTP response vs. Kafka message bus.
  **Trigger:** per-request; per-chunk for SSE streams.

- **Method:** Kafka topic — caption records
  **Topic:** `${KAFKA_TOPIC}` (skill default: `mdx-vlm-captions` — the VSS-3.2 upstream `mdx-lvs` Logstash pipeline subscribes to this topic and indexes captions in via-ctx-rag shape under `<collection>_<id>` ES indices. Raw rtvi-vlm compose default is `vision-llm-messages`, which is NOT subscribed by any pipeline; pre-3.2 references to `mdx-vlm` topic are obsolete — also unsubscribed in current upstream.)
  **Schema:** NvSchema `nv.VisionLLM` protobuf; descriptors at `deploy/docker/services/infra/elk/pb_definitions/descriptors/schema.desc`. Headers: `message_type: vision_llm` and `info["incidentDetected"] = "true"|"false"`. Source: `kafka-workflows.md` § 4.
  **Trigger:** per-caption-chunk when `KAFKA_ENABLED=true`.
  **Partition key:** `<request_id>:<chunk_idx>` — guarantees caption and matching incident (if any) land on the same partition for join-by-key consumers.

- **Method:** Kafka topic — incident records (alert-positive captions)
  **Topic:** `${KAFKA_INCIDENT_TOPIC}` (compose default `mdx-vlm-incidents`; raw upstream compose fallback `vision-llm-events-incidents`)
  **Schema:** NvSchema `nv.Incident` protobuf. The server lower-cases each chunk's VLM response and checks for the tokens `"yes"` or `"true"` — if either appears, an `nv.Incident` record is built with `isAnomaly=True`, `info["triggerPhrase"]=<matched tokens>`, `info["verdict"]="confirmed"`. Headers: `message_type: incident`. Source: `kafka-workflows.md` § 3 Dense captions with alerts.
  **Trigger:** per-chunk, only when the lower-cased response contains `"yes"` or `"true"`.

- **Method:** Kafka topic — error records
  **Topic:** `${ERROR_MESSAGE_TOPIC}` (compose default `mdx-vlm-errors`; raw upstream compose fallback `vision-llm-errors`)
  **Schema:** NvSchema error protobuf. Headers: `message_type: error`.
  **Trigger:** any upstream / VLM error.

- **Method:** Prometheus metrics (`/metrics`) + service metadata (`/v1/info` etc.)
  **Endpoint:** `http://<host>:${RTVI_VLM_PORT}/metrics` and `/v1/info`. Source: `real-time-vlm.rst` § API Reference (lines 213–229).

## API Schema

All endpoints prefixed with `/v1`. Live, authoritative schema is available from a running service via `GET /openapi.json` or via the FastAPI auto-docs at `/docs`. The documented endpoint categories per `real-time-vlm.rst` § API Reference (lines 220–229) are:

| Category | Purpose |
|---|---|
| Captions | `POST /v1/generate_captions` — generate VLM captions and alerts for videos and live streams |
| Files | `POST /v1/files`, `DELETE /v1/files/{id}` — upload and manage video/image files (3.2.0 also supports URL-based ingestion) |
| Live Stream | `POST /v1/streams/add`, plus 3.2.0 CV-compatible aliases `POST /v1/stream/add`, `POST /v1/stream/remove`, `GET /v1/stream/get-stream-info` |
| Models | `GET /v1/models` — list available VLM models with their full NGC artifact IDs |
| Health Check | `GET /v1/health/ready` — readiness probe; **not** `/v1/ready` as some older docs suggest |
| Metrics | `GET /metrics` — Prometheus endpoint |
| Metadata | service version, build info |
| NIM Compatible | `POST /v1/chat/completions` — OpenAI-compatible Chat Completions (text-only or with media; SSE-streaming supported) |

When the user passes the example string `cosmos-reason2` as the `model` field of `/v1/generate_captions`, the service resolves it against the loaded model's full artifact ID (e.g., `nim_nvidia_cosmos-reason2-8b_1208-fp8-static-kv8`). Use `GET /v1/models` first to discover the live ID.

## Environment Variables

The host-side variable names that the compose interpolates differ from the canonical container-side variable names; the compose rewrites them at the boundary. Both are listed below for the IN-1-relevant subset (full list in `real-time-vlm.rst` lines 290–376 and the existing `deploy-rt-vlm-service.md` §7).

| Variable | Purpose | Default | Required? |
|---|---|---|---|
| `RTVI_VLM_PORT` | Host REST API port (rewrites to container `8000`) | strict (`${RTVI_VLM_PORT?}`) | **Yes** |
| `HOST_IP` | Interpolated into `KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092`; no fallback | — | **Yes (effective)** |
| `VSS_DATA_DIR` | Bind-mount root for VST clip storage `${VSS_DATA_DIR}/data_log/vst/clip_storage` | — | **Yes** |
| `NGC_CLI_API_KEY` / `RTVI_VLM_API_KEY` | rewrites to `NGC_API_KEY` + `VIA_VLM_API_KEY` — image pull + NIM auth | — | **Yes** |
| `HF_TOKEN` | Gated HuggingFace pulls (Qwen3-VL, Cosmos3) | — | conditional |
| `RTVI_VLM_MODEL_TO_USE` → `VLM_MODEL_TO_USE` | Backend selector: `openai-compat` (default), `cosmos-reason1`, `cosmos-reason2`, `cosmos-reason3`, `vllm-compatible`, `custom` | `openai-compat` | **Yes** |
| `RTVI_VLM_MODEL_PATH` → `MODEL_PATH` | NGC/git path to model when not `openai-compat`. **Override to `:1208-fp8-static-kv8` for cosmos-reason2 to match VSS docs / NIM siblings — compose default `:hf-1208` is a different quant.** | `ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` (compose) / `ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8` (rst sample) | conditional |
| `RT_VLM_DEVICE_ID` | GPU `device_ids` entry (breaks `RTVI_VLM_*` pattern by design — fixed by upstream compose) | `0` | optional |
| `RTVI_VLM_NVIDIA_VISIBLE_DEVICES` → `NVIDIA_VISIBLE_DEVICES` | GPU visibility | `all` | optional |
| `KAFKA_ENABLED` | Toggle Kafka output | `true` | optional |
| `RTVI_VLM_KAFKA_TOPIC` → `KAFKA_TOPIC` | Caption topic | `mdx-vlm-captions` (VSS 3.2 — subscribed by `mdx-lvs` Logstash pipeline) | optional (compose-default is `vision-llm-messages`; skill **must override** to reach ES) |
| `RTVI_VLM_KAFKA_INCIDENT_TOPIC` → `KAFKA_INCIDENT_TOPIC` | Incident topic | `mdx-vlm-incidents` | optional |
| `KAFKA_BOOTSTRAP_SERVERS` | Broker address | `${HOST_IP}:9092` (host-net) or `kafka:9092` (compose-net) | **Yes (effective)** |
| `ERROR_MESSAGE_TOPIC` | Error topic | `mdx-vlm-errors` | optional |
| `VIA_VLM_ENDPOINT` (host: `RTVI_VLM_ENDPOINT`) | Remote OpenAI-compat backend URL when `VLM_MODEL_TO_USE=openai-compat` | — | conditional |
| `VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME` (host: `VLM_NAME`) | Remote model deployment name | — | conditional |
| `RTVI_VLM_IMAGE_TAG` | Compose image-tag override; pick platform-correct tag (`3.2.0-26.04.1` for x86/Tegra; `3.2.0-26.04.1-sbsa` for SBSA Grace/Spark) | `3.2.0-26.04.1` | optional |

## Network Requirements

- **Ports exposed:** `${RTVI_VLM_PORT}:8000` (host:container). FastAPI binds `0.0.0.0:8000` inside the container. No other host-bound ports.
- **Inbound traffic:** REST clients on `${RTVI_VLM_PORT}` for API. No inbound from peers — RT-VLM is a pure publisher to Kafka and pure REST callee.
- **Outbound traffic:**
  - Kafka broker on `${KAFKA_BOOTSTRAP_SERVERS}` (host:9092 by default)
  - Redis on `${REDIS_HOST}:${REDIS_PORT}` when `ENABLE_REDIS_ERROR_MESSAGES=true`
  - NIM backend on `${VIA_VLM_ENDPOINT}` when `VLM_MODEL_TO_USE=openai-compat`
  - NGC image registry `nvcr.io`, `huggingface.co` for model downloads on first boot
- **DNS / hostname assumptions:** the compose default `KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092` assumes Kafka runs on the **host network**, not the compose bridge — this is the foundational IN-1 wiring (compose puts the broker on `network_mode: host` and RT-VLM on bridge → RT-VLM reaches it via the host IP). `extra_hosts: host.docker.internal: host-gateway` is wired as an alternative way to reach the host. Source: `real-time-vlm.rst` § Sample docker-compose.yml (lines 386–388) + `deploy-rt-vlm-service.md` §10.
- **`network_mode`:** bridge (default Docker compose network). RT-VLM does NOT use `network_mode: host` — distinguishing it from the ELK + Kafka foundational services.

## Known Integration Constraints

- **Endpoint rename (3.2.0 breaking change).** `/v1/generate_captions_alerts` was renamed to `/v1/generate_captions`. 3.1.0-era clients break — update before deployment. Source: `real-time-vlm.rst` § Breaking Changes.
- **Duplicate stream/camera IDs now 409.** Re-adding a stream with the same ID returns HTTP 409 (`DuplicateStreamId` / `DuplicateCameraId`) instead of silently overwriting. Idempotent registration code must remove first or handle 409. Source: `real-time-vlm.rst` § Breaking Changes (lines 99–102).
- **`depends_on.required: false` is NOT enough on recent Docker Compose.** The compose declares `depends_on: cosmos-reason1-7b / cosmos-reason2-8b / qwen3-vl-8b-instruct` all with `required: false`, but recent Compose still validates these refs at project-load time and rejects standalone deploys with `invalid compose project`. Build-vision-agent's Step 6.5 strips the `depends_on` block from the patched copy under `build-output/patched/`. Source: `deploy-rt-vlm-service.md` §20 + §4 + `kafka-workflows.md` not applicable here.
- **Profiles are mandatory.** The upstream rtvi-vlm compose declares 6 compose profiles (`bp_wh_2d`, `bp_developer_alerts_2d_vlm`, `bp_developer_alerts_2d_cv`, `bp_developer_base_2d_IGX-THOR`, `bp_developer_base_2d_AGX-THOR`, `bp_developer_lvs_2d`). `docker compose up` without `--profile` starts **nothing**. IN-1 invents a new flag (`bp_developer_in_1`) and patches it into the `profiles:` list of the build-output copy.
- **Single-instance.** `container_name: vss-rtvi-vlm` is hardcoded in the upstream compose. A second instance on the same host fails with `Conflict. The container name "/vss-rtvi-vlm" is already in use`. Source: `deploy-rt-vlm-service.md` §20.
- **NvSchema protobuf must align with Logstash descriptors.** The producer (RT-VLM) and the consumer (Logstash) must agree on the `nv.VisionLLM` and `nv.Incident` proto schema. The shared source-of-truth is the descriptor pair at `deploy/docker/services/infra/elk/pb_definitions/descriptors/{schema.desc, ext.desc}`. Schema drift on one side without the other causes Logstash to write empty/default-valued documents to ES without raising. Source: `integrate-elk.md` § Known Integration Constraints.
- **Caption topic must be `mdx-vlm-captions` (resolved in VSS 3.2).** VSS 3.2 added the `mdx-lvs` Logstash pipeline (`pipelines/kafka/mdx-lvs-logstash.conf`) that subscribes to `mdx-vlm-captions` and indexes captions into ES using via-ctx-rag's `add_summary` shape (indices named `<collection>_<id>`, not `mdx-vlm-*` date indices). The legacy Finding 4 gap (plain `mdx-vlm` topic unsubscribed) is now obsolete: use `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions` and captions reach ES without any Logstash config patching. The `patch-logstash.py` Option A workaround at the repo root is now superseded by this topic choice. Source: live verification 2026-05-23, `met-blueprint-docs/real-time-vlm.rst`, `mdx-lvs-logstash.conf`.
- **MODEL_PATH tag divergence (must override).** Compose default is `ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` but the VSS-docs-matching tag is `:1208-fp8-static-kv8` (the rst sample uses `:0303-fp8-dynamic-kv8`). These tags are NOT interchangeable on a live cache — different quant produces different torch_aot_compile cache hashes. Source: `deploy-rt-vlm-service.md` §20 third bullet.
- **VOD vs. RTSP timestamp differ.** File uploads (VOD) carry chunk-relative epoch timestamps; if the upload `timestamp` query param is unset, caption docs land in the `mdx-vlm-1970-01-01` ES index. RTSP streams carry wall-clock NTP timestamps and land in the correct date-named index. Affects Kibana dashboards — time picker must include 1970 for VOD-driven captions. Source: `EXAMPLE-PROMPTS.md` § P-006-S1 known edge cases.
- **Jetson Thor / DGX Spark instability at 8+ vision tokens.** Documented platform issue. Cap streams ≤2 or drop input resolution. Source: `real-time-vlm.rst` § (search "Jetson Thor") + `deploy-rt-vlm-service.md` §9.
- **First-boot cold-start is 20 minutes.** Model weight download + vLLM warmup + CUDA graph capture fills the `start_period: 1200s`. Warm-cache restart is ~55 s. Pre-warn operators not to kill as "stuck." Source: `real-time-vlm.rst` § Sample docker-compose.yml (line 393).
- **VIOS-proxied RTSP URLs must use `${HOST_IP}`, NOT `localhost`, when registered with RT-VLM (Finding 12, 2026-05-26).** When wiring a VIOS live-RTSP proxy URL (`rtsp://<host>:30554/live/<sensorId>`) into RT-VLM via `POST /v1/streams/add`, the `liveStreamUrl` MUST resolve to the host's routable IP, not `localhost`. The `vss-rtvi-vlm` container runs on a Docker bridge network (NOT `network_mode: host`), so `localhost` inside it points at the container itself — not the host's VIOS RTSP server at `:30554`. Calls with `rtsp://localhost:30554/live/<id>` fail with `Could not connect to the RTSP URL or there is no video stream from the RTSP URL`; the same URL with `${HOST_IP}` (`rtsp://10.110.29.168:30554/live/<id>` on the validated host) succeeds immediately. The build-vision-agent skill / any client that bridges VIOS → RT-VLM must substitute `${HOST_IP}` into the VIOS-proxied URL before posting. (VIOS itself runs `network_mode: host` and exposes RTSP only on the routable interface; the container's `/etc/hosts` `localhost` entry does not reach it from sibling bridge containers.) Source: live verification 2026-05-26.

## Example Compose Snippet

Minimal IN-1-relevant block. Full upstream compose is at `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`; the build-vision-agent skill patches a copy into `build-output/patched/`. Sourced from `real-time-vlm.rst` § Sample docker-compose.yml (lines 272–397) — abbreviated to the IN-1 minimum.

```yaml
services:
  rtvi-vlm:
    image: nvcr.io/nvidia/vss-core/vss-rt-vlm:${RTVI_VLM_IMAGE_TAG:-3.2.0-26.04.1}
    container_name: vss-rtvi-vlm
    shm_size: '16gb'
    runtime: nvidia
    user: "1001:1001"
    ports:
      - "${RTVI_VLM_PORT?}:8000"
    profiles:
      - bp_developer_in_1   # inserted by build-vision-agent Step 6.5
      # ... existing upstream profiles preserved
    volumes:
      - "${RTVI_VLM_HF_CACHE:-rtvi-hf-cache}:/tmp/huggingface"
      - "${VSS_DATA_DIR}/data_log/vst/clip_storage:/home/vst/vst_release/streamer_videos"
      - "${NGC_MODEL_CACHE:-rtvi-ngc-model-cache}:/opt/nvidia/rtvi/.rtvi/ngc_model_cache"
    environment:
      NGC_API_KEY: "${NGC_CLI_API_KEY:-}"
      HF_TOKEN: "${HF_TOKEN:-}"
      VLM_MODEL_TO_USE: "${RTVI_VLM_MODEL_TO_USE:-cosmos-reason2}"
      MODEL_PATH: "${RTVI_VLM_MODEL_PATH:-ngc:nim/nvidia/cosmos-reason2-8b:1208-fp8-static-kv8}"
      KAFKA_ENABLED: "true"
      KAFKA_TOPIC: "${RTVI_VLM_KAFKA_TOPIC:-mdx-vlm-captions}"
      KAFKA_INCIDENT_TOPIC: "${RTVI_VLM_KAFKA_INCIDENT_TOPIC:-mdx-vlm-incidents}"
      KAFKA_BOOTSTRAP_SERVERS: "${HOST_IP}:9092"
      ERROR_MESSAGE_TOPIC: "${ERROR_MESSAGE_TOPIC:-mdx-events}"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ["${RT_VLM_DEVICE_ID:-0}"]
              capabilities: [gpu]
    extra_hosts:
      host.docker.internal: host-gateway
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/v1/health/ready"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 1200s
    ulimits:
      memlock: { soft: -1, hard: -1 }
      stack: 67108864
    ipc: host
    # depends_on: STRIPPED by build-vision-agent Step 6.5 for standalone IN-1 deploys.
    # Upstream compose declares depends_on for sibling NIMs (cosmos-reason1-7b,
    # cosmos-reason2-8b, qwen3-vl-8b-instruct) all required:false, but recent
    # Docker Compose rejects the standalone project at load time.

volumes:
  rtvi-hf-cache:
  rtvi-ngc-model-cache:
```

## Schema Compatibility

The `nv.VisionLLM` and `nv.Incident` protobuf schemas are the contract between RT-VLM (producer) and Logstash (consumer). Both sides bind the same descriptor files (`schema.desc`, `ext.desc`) under `deploy/docker/services/infra/elk/pb_definitions/descriptors/`. The Ruby pb_definitions files (`schema_pb.rb`, `ext_pb.rb`) are mounted into Logstash for decoding. Any change to the protobuf on the RT-VLM side requires republishing the descriptors to ELK in the same release; otherwise Logstash silently writes default-valued documents to ES with no error surfaced. Source: `integrate-elk.md` § Known Integration Constraints + `kafka-workflows.md` § 4 (last paragraph).

## Test / Smoke Hooks

- **Health:** `curl -f http://localhost:${RTVI_VLM_PORT}/v1/health/ready` — used by the Docker healthcheck; expect HTTP 200.
- **Loaded model ID:** `curl -s http://localhost:${RTVI_VLM_PORT}/v1/models | jq` — confirms the model resolved at warmup. Source: `real-time-vlm.rst` § Usage Examples note (lines 478–491).
- **End-to-end caption + Kafka:** upload via `POST /v1/files`, call `POST /v1/generate_captions`, then poll `mdx-vlm` topic offsets:

```bash
docker exec kafka kafka-get-offsets --bootstrap-server 127.0.0.1:9092 --topic mdx-vlm-captions
docker exec mdx-kafka kafka-console-consumer \
  --bootstrap-server 127.0.0.1:9092 --topic mdx-vlm-captions \
  --from-beginning --timeout-ms 5000 --max-messages 5 \
  --property print.headers=true --property print.key=true --property print.value=false
```

Source: `kafka-workflows.md` § 4 (last code block).
