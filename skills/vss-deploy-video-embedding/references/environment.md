# Environment Reference: Video Embedding (RT-Embed)

This reference lists every variable the Compose service consumes and how host-level variables are translated into the container's environment. Use it when wiring `.env` files or sizing a deployment.

## Required Host Variables

| Variable | Purpose | Notes |
|---|---|---|
| `RTVI_EMBED_PORT` | Host port mapped to container `8000`. | Compose uses `${RTVI_EMBED_PORT?}`, so a missing value fails `docker compose config`. |
| `VSS_DATA_DIR` | Host root for VSS shared data. | `${VSS_DATA_DIR}/data_log/vst/clip_storage` is bind-mounted to the container clip-storage reader path declared in `rtvi-embed-docker-compose.yml`. |
| `NGC_API_KEY` | NGC API key for asset downloads. | Required for first-boot model fetches from NGC. |
| `HF_TOKEN` | Hugging Face token. | Optional. Recommended to avoid Hugging Face 429 rate-limit errors during the first-boot Cosmos-Embed1 weights download. |

## Optional Host Variables That Rename On The Container Boundary

Several host-side variables map to differently named container variables. The Compose service performs the rewrite.

| Host variable | Container variable | Default |
|---|---|---|
| `VSS_RT_EMBED_IMAGE` | image base | `ghcr.io/nvidia-ai-blueprints/vss/vss-rt-embed` |
| `VSS_RT_EMBED_TAG` | image tag | `develop-latest`; set `develop-latest-sbsa` on SBSA/DGX Spark. |
| `RT_EMBED_DEVICE_ID` | `device_ids[0]` reservation | `0` |
| `RTVI_EMBED_NVIDIA_VISIBLE_DEVICES` | `NVIDIA_VISIBLE_DEVICES` | `all` |
| `RTVI_EMBED_NUM_GPUS` | `NUM_GPUS` | (unset) |
| `RTVI_EMBED_NUM_VLM_PROCS` | `NUM_VLM_PROCS` | (unset) |
| `RTVI_EMBED_LOG_LEVEL` | `LOG_LEVEL` | `INFO` |
| `RTVI_EMBED_RTSP_LATENCY` | `RTVI_RTSP_LATENCY` | (unset) |
| `RTVI_EMBED_RTSP_TIMEOUT` | `RTVI_RTSP_TIMEOUT` | (unset) |
| `RTVI_EMBED_RTSP_RECONNECTION_INTERVAL` | `RTVI_RTSP_RECONNECTION_INTERVAL` | `5` |
| `RTVI_EMBED_RTSP_RECONNECTION_WINDOW` | `RTVI_RTSP_RECONNECTION_WINDOW` | `60` |
| `RTVI_EMBED_RTSP_RECONNECTION_MAX_ATTEMPTS` | `RTVI_RTSP_RECONNECTION_MAX_ATTEMPTS` | `10` |
| `RTVI_EMBED_ENABLE_OTEL_MONITORING` | `ENABLE_OTEL_MONITORING` | `false` |
| `RTVI_EMBED_OTEL_RESOURCE_ATTRIBUTES` | `OTEL_RESOURCE_ATTRIBUTES` | (unset) |
| `RTVI_EMBED_OTEL_TRACES_EXPORTER` | `OTEL_TRACES_EXPORTER` | `otlp` |
| `RTVI_EMBED_OTEL_EXPORTER_OTLP_ENDPOINT` | `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` |
| `RTVI_EMBED_OTEL_METRIC_EXPORT_INTERVAL` | `OTEL_METRIC_EXPORT_INTERVAL` | `60000` (ms) |
| `RTVI_EMBED_ERROR_MESSAGE_TOPIC` | `ERROR_MESSAGE_TOPIC` | `vision-embed-errors` |
| `RTVI_EMBED_KAFKA_BOOTSTRAP_SERVERS` | `KAFKA_BOOTSTRAP_SERVERS` | `kafka:29092` |
| `RTVI_EMBED_HF_CACHE` | volume source for `/tmp/huggingface` | `rtvi-hf-cache` (named) |
| `NGC_MODEL_CACHE` | volume source for the NGC cache | `rtvi-ngc-model-cache` (named) |
| `RTVI_EMBED_LOG_DIR` | optional host bind for `/opt/nvidia/rtvi/log/rtvi/` | (unset; mount is skipped) |
| `ASSET_STORAGE_DIR` | optional host bind for `/tmp/assets` | (unset; mount is skipped) |

## Direct (No-Rename) Container Variables

| Variable | Purpose | Default |
|---|---|---|
| `MODEL_PATH` | Model source URI for first-boot download. | `git:https://huggingface.co/nvidia/Cosmos-Embed1-448p` |
| `MODEL_IMPLEMENTATION_PATH` | In-container path to the model implementation. | `/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1` |
| `MODEL_REPOSITORY_SCRIPT_PATH` | Script that builds the Triton model repository. | `/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1/create_triton_model_repo.py` |
| `MESSAGE_BUS` | Generated-message output bus. Use `kafka` to publish embedding events; set empty to disable. | `kafka` in the shipped `.env` |
| `MESSAGE_BUS_TOPIC` | Generated-message Kafka topic when `MESSAGE_BUS=kafka`. | `mdx-embed` |
| `ERROR_BUS` | Error output bus. Use `kafka` for Kafka-backed errors; set empty to disable. | `kafka` in the shipped `.env` |
| `REMOTE_EMBED_ENDPOINT` | Optional CE1 NIM endpoint URL. When set, RT-Embed uses the remote CE1 backend. | (empty) |
| `REMOTE_EMBED_ENDPOINT_MODEL_NAME` | CE1 NIM deployment/model id. | `nvidia/cosmos-embed1` |
| `REMOTE_EMBED_ENDPOINT_API_KEY` | Optional bearer token for the CE1 NIM endpoint. | (empty) |
| `REMOTE_EMBED_ENDPOINT_TIMEOUT_SEC` | Request timeout for CE1 NIM calls. | `300` |
| `REMOTE_EMBED_ENDPOINT_BATCH_SIZE` | Maximum batch size used by the CE1 NIM client wrapper. | `64` |
| `VLM_BATCH_SIZE` | Inference batch size. | (unset) |
| `INSTALL_PROPRIETARY_CODECS` | Install proprietary codecs at startup. | `false` |
| `FORCE_SW_AV1_DECODER` | Force software AV1 decoding. | (unset) |
| `NVIDIA_API_KEY` | NVIDIA API key for downstream calls. | `NOAPIKEYSET` |
| `ENABLE_REDIS_ERROR_MESSAGES` | Publish error messages to Redis. | `false` |
| `REDIS_HOST` | Redis host. | `redis` |
| `REDIS_PORT` | Redis port. | `6379` |
| `REDIS_DB` | Redis database index. | `0` |
| `REDIS_PASSWORD` | Redis password. | (empty) |
| `ASSET_DOWNLOAD_TOTAL_TIMEOUT` | Maximum seconds for a URL asset download. | `300` |
| `ASSET_DOWNLOAD_CONNECT_TIMEOUT` | Connection timeout for asset downloads. | `10` |
| `ASSET_DOWNLOAD_MAX_FILE_SIZE_GB` | Max file size for HTTP/data URI asset ingestion. | `8` |
| `ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS` | Comma-separated domains where RT-Embed should skip TLS verification for asset downloads. | (empty) |
| `ASSET_DOWNLOAD_MAX_REDIRECTS` | Max redirect hops for URL downloads. `0` disables redirects. | `0` |
| `ASSET_DOWNLOAD_AUTH_TOKENS` | Optional server-level auth headers for URL downloads, formatted as `domain=Bearer token` entries separated by semicolons. | (empty) |
| `FILE_URL_ALLOWED_DIRS` | Comma-separated allow-list for `file://` URL access. Empty disables `file://` URLs. | (empty) |
| `MAX_ASSET_STORAGE_SIZE_GB` | Optional numeric max asset storage size in GB for eviction. | (empty) |
| `ASSET_MAX_AGE_HOURS` | TTL-based asset eviction in hours. `0` disables TTL eviction. | `0` |
| `ENABLE_REQUEST_PROFILING` | Per-request profiling. | `false` |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker list used when `MESSAGE_BUS=kafka` or `ERROR_BUS=kafka`. | `kafka:29092` via `RTVI_EMBED_KAFKA_BOOTSTRAP_SERVERS` |

## Secret-Sensitive Variables

The following are credentials. Set them through `.env`, a secrets manager, or your orchestrator's secret store. Never bake values into committed files or generated documentation.

- `NGC_API_KEY`
- `NVIDIA_API_KEY`
- `HF_TOKEN`
- `REDIS_PASSWORD`
- `REMOTE_EMBED_ENDPOINT_API_KEY`
- `ASSET_DOWNLOAD_AUTH_TOKENS`

## Volume / Bind Variables

| Variable | Effect |
|---|---|
| `NGC_MODEL_CACHE` | Overrides the source of the volume mounted at `/opt/nvidia/rtvi/.rtvi/ngc_model_cache`. Defaults to the named volume `rtvi-ngc-model-cache`. |
| `RTVI_EMBED_HF_CACHE` | Overrides the source of the volume mounted at `/tmp/huggingface`. Defaults to the named volume `rtvi-hf-cache`. |
| `ASSET_STORAGE_DIR` | When set, bind-mounts that host directory at `/tmp/assets`. Otherwise the mount is skipped. |
| `RTVI_EMBED_LOG_DIR` | When set, bind-mounts that host directory at `/opt/nvidia/rtvi/log/rtvi/`. Otherwise the mount is skipped. |
| `VSS_DATA_DIR` | Used as the host root for the VST clip-storage bind mount. |

## OpenTelemetry Defaults

When `RTVI_EMBED_ENABLE_OTEL_MONITORING=true` is set on the host (Compose injects this as `ENABLE_OTEL_MONITORING` inside the container), the service exports OTLP traces and metrics to the endpoint named by `RTVI_EMBED_OTEL_EXPORTER_OTLP_ENDPOINT` (injected as `OTEL_EXPORTER_OTLP_ENDPOINT`; default `http://otel-collector:4318`). The default `RTVI_EMBED_OTEL_METRIC_EXPORT_INTERVAL=60000` (injected as `OTEL_METRIC_EXPORT_INTERVAL`) is in milliseconds. Set `RTVI_EMBED_OTEL_RESOURCE_ATTRIBUTES` on the host (injected as `OTEL_RESOURCE_ATTRIBUTES`) to tag traces with deployment-specific labels. Setting any of the container-side names (`ENABLE_OTEL_MONITORING`, `OTEL_*`) directly on the host has no effect.
