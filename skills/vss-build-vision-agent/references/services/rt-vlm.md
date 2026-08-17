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

## Singleton and variant convergence

RT-VLM is a singleton owner: one instance, one checkpoint, and one
variant/placement knob-set per build. When capabilities bring different
integrated Cosmos3 Nano variants, resolve the placement first, then converge on
one variant:

- a dedicated GPU selects the heavier BF16 variant;
- co-residence with another GPU service selects the lighter FP8 variant.

Resolve the variant/placement knobs as one set:
`RTVI_VLM_MODEL_PATH`, `VLM_NAME`, `RTVI_VLLM_GPU_MEMORY_UTILIZATION`,
`RTVI_VLM_MAX_MODEL_LEN`, and `RT_VLM_DEVICE_ID`. Take the checkpoint and model
name from the profile that ships the resolved variant; resolve maximum model
length, device ID, and utilization together for the selected hardware and
placement. Keep `VLM_NAME` aligned with the model id advertised by
`RTVI_VLM_MODEL_PATH`; do not combine values from different variants.

Consumer wiring is not part of that set.
`RTVI_VLM_KAFKA_ENABLED`, `RTVI_VLM_MESSAGE_BUS_TOPIC` (generated captions),
`RTVI_VLM_KAFKA_INCIDENT_TOPIC` (verification incidents), and verifier config
mounts follow the consuming capability and operating mode, never the profile that
supplied the variant. Realtime VLM alerting (`2d_vlm`) must set Kafka enablement
to `true` even when the variant profile defaults it to `false`, or no incidents
are published; CV verification (`2d_cv`) keeps it `false` — verified incidents
reach Elasticsearch through the alert bridge, not RT-VLM's Kafka path. For the
integrated path, `RTVI_VLM_MODEL_TO_USE=cosmos-reason3` and
the `http://rtvi-vlm:8000` endpoint (a consumer's `VLM_BASE_URL`) are invariant
across BF16 and FP8; a consumer owns that URL but never inherits it from the
variant profile.

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
- `skills/vss-build-vision-agent/references/composition.md`
- `skills/vss-deploy-dense-captioning/references/deploy-rt-vlm-service.md`
- `skills/vss-deploy-dense-captioning/references/integrate-rt-vlm.md`
