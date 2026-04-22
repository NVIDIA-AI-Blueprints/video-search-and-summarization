# rtvi-vlm Environment Variables — Full Reference

Compose path: `deployments/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`

## Convention: `RTVI_VLM_*` → canonical

Host-side `.env` uses the `RTVI_VLM_*` or `RTVI_VLLM_*` prefix; compose
rewrites to the canonical container-side name. Set the host-side names in
`.env` — the compose handles the remap.

## Port & Host

| Host var | Required | Container var | Default | Notes |
|---|---|---|---|---|
| `RTVI_VLM_PORT` | **YES** (strict `?`) | — | — | Mapped to container 8000 |
| `HOST_IP` | **YES (effective)** | — | — | Interpolated into `KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092` |
| `MDX_DATA_DIR` | **YES (effective)** | — | — | Interpolated into VST clip-storage bind mount |

## Image & Profile

| Host var | Container var | Default |
|---|---|---|
| `RTVI_VLM_IMAGE_TAG` | — | `3.1.0` |
| `RT_VLM_DEVICE_ID` | device_ids | `0` (does NOT follow the `RTVI_VLM_*` convention — fixed upstream) |

## Model Selection

| Host var | Container var | Default |
|---|---|---|
| `RTVI_VLM_MODEL_TO_USE` | `VLM_MODEL_TO_USE` | `openai-compat` |
| `RTVI_VLM_MODEL_PATH` | `MODEL_PATH` | `ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` |
| `RTVI_VLM_MODEL_IMPLEMENTATION_PATH` | `MODEL_IMPLEMENTATION_PATH` | (empty) |
| `RTVI_VLM_SYSTEM_PROMPT` | `VLM_SYSTEM_PROMPT` | (empty) |

## Credentials

| Host var | Container var | Default | Required when |
|---|---|---|---|
| `NGC_CLI_API_KEY` | `NGC_API_KEY` + `VIA_VLM_API_KEY` (fallback chain) | — | nvcr.io pulls / NGC NIM auth |
| `RTVI_VLM_API_KEY` | `VIA_VLM_API_KEY` / `NGC_API_KEY` | fallback to `NGC_CLI_API_KEY` | Custom NIM auth override |
| `HF_TOKEN` | `HF_TOKEN` | (empty) | Gated HF models |
| `OPENAI_API_KEY` | `OPENAI_API_KEY` | `NOAPIKEYSET` | `openai-compat` → OpenAI |
| `OPENAI_API_VERSION` | `OPENAI_API_VERSION` | (empty) | Azure OpenAI |
| `NVIDIA_API_KEY` | `NVIDIA_API_KEY` | `NOAPIKEYSET` | Generic |
| `RTVI_VLM_ENDPOINT` | `VIA_VLM_ENDPOINT` | (empty) | External OpenAI-compat |
| `RTVI_VLM_OPENAI_MODEL_DEPLOYMENT_NAME` | `VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME` | (empty) | External OpenAI-compat |

## vLLM Runtime Tuning

| Host var | Container var | Default |
|---|---|---|
| `RTVI_VLLM_GPU_MEMORY_UTILIZATION` | `VLLM_GPU_MEMORY_UTILIZATION` | (empty → auto 0.7 if VRAM ≤ 50 GB) |
| `RTVI_VLLM_IGNORE_EOS` | `VLLM_IGNORE_EOS` | `false` |
| `RTVI_VLLM_MAX_NUM_SEQS` | `VLLM_MAX_NUM_SEQS` | `256` |
| `RTVI_VLLM_MAX_NUM_BATCHED_TOKENS` | `VLLM_MAX_NUM_BATCHED_TOKENS` | `5120` |
| `RTVI_VLM_MAX_MODEL_LEN` | `VLM_MAX_MODEL_LEN` | `32768` |
| `RTVI_VLLM_NUM_SCHEDULER_STEPS` | `VLLM_NUM_SCHEDULER_STEPS` | `8` |
| `RTVI_VLLM_ENABLE_PREFIX_CACHING` | `VLLM_ENABLE_PREFIX_CACHING` | `true` |
| `RTVI_VLLM_DISABLE_MM_PREPROCESSOR_CACHE` | `VLLM_DISABLE_MM_PREPROCESSOR_CACHE` | `false` |
| `RTVI_VLM_BATCH_SIZE` | `VLM_BATCH_SIZE` | (empty → auto-tuned by entrypoint) |
| `RTVI_VLM_INPUT_WIDTH` | `VLM_INPUT_WIDTH` | (empty → auto) |
| `RTVI_VLM_INPUT_HEIGHT` | `VLM_INPUT_HEIGHT` | (empty → auto) |
| `RTVI_VLM_NUM_GPUS` | `NUM_GPUS` | (empty → auto) |
| `RTVI_VLM_NUM_VLM_PROCS` | `NUM_VLM_PROCS` | (empty) |
| `RTVI_VLM_NUM_GPUS_PER_VLM_PROC` | `VSS_NUM_GPUS_PER_VLM_PROC` | (empty) |
| `RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK` | `VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK` | (empty) |
| `RTVI_VLM_NVIDIA_VISIBLE_DEVICES` | `NVIDIA_VISIBLE_DEVICES` | `all` |
| `RTVI_VLM_GST_ENABLE_CUSTOM_PARSER_MODIFICATIONS` | `GST_ENABLE_CUSTOM_PARSER_MODIFICATIONS` | `1` |

## RTSP Streaming

| Host var | Container var | Default |
|---|---|---|
| `RTVI_VLM_RTSP_LATENCY` | `RTVI_RTSP_LATENCY` | (empty) |
| `RTVI_VLM_RTSP_TIMEOUT` | `RTVI_RTSP_TIMEOUT` | (empty) |
| `RTVI_VLM_RTSP_RECONNECTION_INTERVAL` | `RTVI_RTSP_RECONNECTION_INTERVAL` | `5` |
| `RTVI_VLM_RTSP_RECONNECTION_WINDOW` | `RTVI_RTSP_RECONNECTION_WINDOW` | `60` |
| `RTVI_VLM_RTSP_RECONNECTION_MAX_ATTEMPTS` | `RTVI_RTSP_RECONNECTION_MAX_ATTEMPTS` | `10` |
| `RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT` | `RTVI_ADD_TIMESTAMP_TO_VLM_PROMPT` | (empty; docs default `true`) |

## Kafka

| Host var | Container var | Default |
|---|---|---|
| *(no rewrite)* `HOST_IP` | — | — |
| `RTVI_VLM_KAFKA_ENABLED` | `KAFKA_ENABLED` | `true` |
| `RTVI_VLM_KAFKA_TOPIC` | `KAFKA_TOPIC` | `vision-llm-messages` |
| `RTVI_VLM_KAFKA_INCIDENT_TOPIC` | `KAFKA_INCIDENT_TOPIC` | `vision-llm-events-incidents` |
| `RTVI_VLM_ERROR_MESSAGE_TOPIC` | `ERROR_MESSAGE_TOPIC` | `vision-llm-errors` |
| *(interpolated)* — | `KAFKA_BOOTSTRAP_SERVERS` | `${HOST_IP}:9092` |

## OpenTelemetry

| Host var | Container var | Default |
|---|---|---|
| `RTVI_VLM_ENABLE_OTEL_MONITORING` | `ENABLE_OTEL_MONITORING` | `false` |
| `RTVI_VLM_OTEL_RESOURCE_ATTRIBUTES` | `OTEL_RESOURCE_ATTRIBUTES` | (empty) |
| `RTVI_VLM_OTEL_TRACES_EXPORTER` | `OTEL_TRACES_EXPORTER` | `otlp` |
| `RTVI_VLM_OTEL_EXPORTER_OTLP_ENDPOINT` | `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` |
| `RTVI_VLM_OTEL_METRIC_EXPORT_INTERVAL` | `OTEL_METRIC_EXPORT_INTERVAL` | `60000` |

## Redis (Optional — error messages)

| Host var | Container var | Default |
|---|---|---|
| `ENABLE_REDIS_ERROR_MESSAGES` | `ENABLE_REDIS_ERROR_MESSAGES` | `false` |
| `REDIS_HOST` | `REDIS_HOST` | `redis` |
| `REDIS_PORT` | `REDIS_PORT` | `6379` |
| `REDIS_DB` | `REDIS_DB` | `0` |
| `REDIS_PASSWORD` | `REDIS_PASSWORD` | (empty) |

## Logging & Misc

| Host var | Container var | Default |
|---|---|---|
| `RTVI_VLM_LOG_LEVEL` | `LOG_LEVEL` | `INFO` |
| `INSTALL_PROPRIETARY_CODECS` | `INSTALL_PROPRIETARY_CODECS` | `false` |
| `FORCE_SW_AV1_DECODER` | `FORCE_SW_AV1_DECODER` | (empty) |
| `VSS_SKIP_INPUT_MEDIA_VERIFICATION` | `VSS_SKIP_INPUT_MEDIA_VERIFICATION` | (empty) |
| `RTVI_EXTRA_ARGS` | `RTVI_EXTRA_ARGS` | (empty) |

## Volumes (host-side vars)

| Host var | Mount target | Default |
|---|---|---|
| `ASSET_STORAGE_DIR` | `/tmp/assets` (optional bind) | (unset → tmpfs) |
| `RTVI_VLM_HF_CACHE` | `/tmp/huggingface` | named vol `rtvi-hf-cache` |
| `MDX_DATA_DIR` | `${MDX_DATA_DIR}/data_log/vst/clip_storage` → `/home/vst/vst_release/streamer_videos` | **required, no default** |
| `NGC_MODEL_CACHE` | `/opt/nvidia/rtvi/.rtvi/ngc_model_cache` | named vol `rtvi-ngc-model-cache` |
| `RTVI_VLM_LOG_DIR` | `/opt/nvidia/rtvi/log/rtvi/` (optional bind) | (unset → no bind) |

