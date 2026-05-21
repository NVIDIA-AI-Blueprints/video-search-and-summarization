# Video Summarization Environment Variables

This is the 3.2.0 `lvs` profile env reference for the VSS develop branch. For
full deployment decisions, use `vss-deploy-profile`; this file is for quick
video summarization debugging and request construction.

## User-Edited Profile Env

Primary file:

```text
deploy/docker/developer-profiles/dev-profile-lvs/.env
```

Core deployment:

| Var | Purpose |
|---|---|
| `MODE` | Profile mode, currently `2d`. |
| `BP_PROFILE` | Blueprint profile, `bp_developer_lvs`. |
| `COMPOSE_PROFILES` | Computed profile list. Includes `bp_developer_lvs_2d`. |
| `HARDWARE_PROFILE` | Hardware profile for NIM sizing. |
| `VSS_APPS_DIR` | Absolute path to `deploy/docker`. |
| `VSS_DATA_DIR` | Data root. |
| `HOST_IP` | Host-reachable IP address. |

Model selection:

| Var | Purpose |
|---|---|
| `LLM_MODE` | `local_shared`, `local`, or `remote`. |
| `VLM_MODE` | `local_shared`, `local`, or `remote`; video summarization uses RT-VLM for VLM serving. |
| `LLM_NAME`, `LLM_NAME_SLUG` | LLM model id and service slug. |
| `VLM_NAME` | Model id sent to the video summarization service and RT-VLM. Must match `/v1/models`. |
| `VLM_NAME_SLUG` | VLM service slug, often `none` for integrated RT-VLM. |
| `LLM_BASE_URL`, `VLM_BASE_URL` | Remote endpoints when using remote mode. |

Credentials:

| Var | Purpose |
|---|---|
| `NGC_CLI_API_KEY` | Image/model pulls for local deployment. |
| `NVIDIA_API_KEY` | NVIDIA-hosted remote endpoints and video summarization LLM API key fallback. |
| `OPENAI_API_KEY` | OpenAI-compatible remote endpoints, if used. |
| `HF_TOKEN` | Required for gated Hugging Face checkpoints such as Omni. |

RT-VLM:

| Var | Default / Example | Purpose |
|---|---|---|
| `RTVI_VLM_IMAGE_TAG` | `3.2.0-26.05.1` | RT-VLM image tag. |
| `RTVI_VLM_BASE_URL` | `http://${HOST_IP}:8018` | Agent-facing base URL. |
| `RTVI_VLM_PORT` | `8018` | Host port. |
| `RTVI_VLM_URL` | `http://${HOST_IP}:${RTVI_VLM_PORT}` | video summarization-facing URL. |
| `RTVI_VLM_MODEL_TO_USE` | `cosmos-reason2` | Default integrated backend selector. |
| `RTVI_VLM_MODEL_PATH` | `ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` | Default checkpoint. |
| `RTVI_VLLM_GPU_MEMORY_UTILIZATION` | empty | Optional vLLM memory fraction. |
| `RTVI_VLM_KAFKA_ENABLED` | `true` | Publish raw caption events. |
| `RTVI_VLM_KAFKA_TOPIC` | `mdx-vlm-captions` | Raw caption topic. |
| `RTVI_VLM_KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Broker URL from RT-VLM. |

Video summarization service:

| Var | Default / Example | Purpose |
|---|---|---|
| `LVS_BACKEND_URL` | `http://${HOST_IP}:38111` | Agent-facing video summarization URL. |
| `LVS_IMAGE` | `nvcr.io/nvstaging/vss-core/vss-video-summarization` | Image repository. |
| `LVS_TAG` | `3.2.0-rc10-6f75390` | Image tag in current develop. |
| `LVS_ENABLE_MCP` | `false` | Enable optional MCP/SSE port. |
| `KAFKA_ENABLED` | `true` | video summarization Kafka integration. |
| `KAFKA_BOOTSTRAP_SERVERS` | `${HOST_IP}:9092` | Broker URL from the video summarization service. |
| `KAFKA_STRUCTURED_SUMMARY_TOPIC` | `mdx-structured-events-summary` | Structured summary topic. |
| `LVS_ENABLE_LLM_MERGING` | `true` | Merge duplicate/overlapping events. |

## Service Compose Env

The video summarization service compose lives at:

```text
deploy/docker/services/video-summarization/compose.yml
```

It maps profile env into container env. Important container env names:

| Container env | Source / value |
|---|---|
| `CA_RAG_CONFIG` | `/app/config.yaml` |
| `BACKEND_PORT` | `${BACKEND_PORT:-38111}` |
| `LVS_MCP_PORT` | `${LVS_MCP_PORT:-38112}` |
| `LVS_LLM_MODEL_NAME` | `${LVS_LLM_MODEL_NAME}` |
| `LVS_LLM_BASE_URL` | `${LLM_BASE_URL:-http://${HOST_IP}:${LLM_PORT}}/v1` |
| `LVS_LLM_API_KEY` | `${OPENAI_API_KEY:-${NVIDIA_API_KEY}}` |
| `VIA_VLM_ENDPOINT` | `${VLM_BASE_URL:-http://${HOST_IP}:${VLM_PORT}}/v1/` |
| `LVS_EMB_ENABLE` | `${LVS_EMB_ENABLE}` |
| `LVS_DATABASE_BACKEND` | `${LVS_DATABASE_BACKEND:-elasticsearch_db}` |
| `ES_HOST`, `ES_PORT` | Elasticsearch connection. |
| `KAFKA_ENABLED` | `${KAFKA_ENABLED:-false}` |
| `KAFKA_BOOTSTRAP_SERVERS` | `${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}` |
| `KAFKA_STRUCTURED_SUMMARY_TOPIC` | `${KAFKA_STRUCTURED_SUMMARY_TOPIC:-mdx-structured-events-summary}` |
| `RTVI_VLM_URL` | `${RTVI_VLM_URL:-}` |
| `ENABLE_AUDIO` | `${ENABLE_AUDIO:-false}` |
| `ENABLE_DENSE_CAPTION` | `false` |
| `VSS_LOG_LEVEL` | `INFO` |

## Config Map Env

The CA-RAG config at `deploy/docker/services/video-summarization/configs/config.yaml`
uses:

| Env | Purpose |
|---|---|
| `MILVUS_DB_HOST`, `MILVUS_DB_GRPC_PORT` | Milvus backend. |
| `ES_HOST`, `ES_PORT` | Elasticsearch backend. |
| `GRAPH_DB_HOST`, `GRAPH_DB_BOLT_PORT` | Neo4j graph backend. |
| `ARANGO_DB_HOST`, `ARANGO_DB_PORT` | ArangoDB graph backend. |
| `LVS_LLM_MODEL_NAME`, `LVS_LLM_BASE_URL` | Summarization LLM. |
| `LVS_EMB_ENABLE`, `LVS_EMB_MODEL_NAME`, `LVS_EMB_BASE_URL` | Embedding tool. |
| `KAFKA_ENABLED` | Kafka-backed summarization aggregation. |
| `LVS_ENABLE_LLM_MERGING` | LLM merge behavior. |
| `LVS_DATABASE_BACKEND` | Active DB tool, usually `elasticsearch_db`. |

## Runtime Rules

- Do not guess the model id. Verify with `/models` or RT-VLM `/v1/models`.
- Use `LVS_BACKEND_URL` for video summarization API calls and strip trailing `/v1` from VLM
  base URLs before appending `/v1/chat/completions`.
- For 3.2 GA examples, prefer `/v1/summarize` and
  `num_frames_per_second_or_fixed_frames_chunk`.
- Do not add development-only API switches to GA instructions.
