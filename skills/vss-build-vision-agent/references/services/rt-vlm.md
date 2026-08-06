# RT-VLM Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile key |
|---|---|
| Streaming and file VLM inference | `rtvi-vlm` |

## Required peers

- Integrated mode needs model credentials/cache access but no standalone VLM
  NIM service.
- OpenAI-compatible mode needs a reachable endpoint and matching `VLM_NAME`.
- Kafka is required only when `RTVI_VLM_KAFKA_ENABLED=true`.
- Redis is required only when `ENABLE_REDIS_ERROR_MESSAGES=true`.
- Do not add `vlm_${VLM_MODE}_${VLM_NAME_SLUG}` for an integrated RT-VLM path.
- Headless VLM Q&A / dense captioning is served **directly** by RT-VLM's
  OpenAI-compatible `/v1/chat/completions` (and `/v1/models`) on `RTVI_VLM_PORT`
  (default `8018`) — no agent tier needed.
- Its Kafka output topic `mdx-vlm-captions` is already in the default
  `KAFKA_TOPICS` of `kafka-topic-init-container`, so no custom topic definition is
  required.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `RTVI_VLM_IMAGE_TAG`, `RTVI_VLM_PORT`, `RT_VLM_DEVICE_ID` | Select image, host port, and GPU. |
| `RTVI_VLM_MODEL_TO_USE`, `RTVI_VLM_MODEL_PATH`, `VLM_NAME` | Select an integrated model and its advertised id. |
| `RTVI_VLM_ENDPOINT`, `RTVI_VLM_API_KEY`, `VLM_BASE_URL` | Configure an OpenAI-compatible backend. |
| `RTVI_VLLM_GPU_MEMORY_UTILIZATION`, `RTVI_VLM_MAX_MODEL_LEN`, `RTVI_VLLM_MAX_NUM_SEQS`, `RTVI_VLLM_MAX_NUM_BATCHED_TOKENS` | Bound vLLM memory and concurrency. |
| `RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK`, `RTVI_VLM_BATCH_SIZE` | Tune frame sampling and batching. |
| `RTVI_VLM_KAFKA_ENABLED`, `RTVI_VLM_KAFKA_TOPIC`, `RTVI_VLM_KAFKA_BOOTSTRAP_SERVERS` | Configure event publication. |
| `VLM_MODEL_SUPPORTS_AUDIO`, `VLM_TRUST_REMOTE_CODE`, `HF_TOKEN` | Enable supported audio or gated/custom HF models. |
| `INSTALL_PROPRIETARY_CODECS`, `FORCE_SW_AV1_DECODER` | Select runtime codec behavior. |

## Sources

- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `skills/vss-deploy-dense-captioning/references/deploy-rt-vlm-service.md`
- `skills/vss-deploy-dense-captioning/references/integrate-rt-vlm.md`
