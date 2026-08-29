# RT-VLM Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile key |
|---|---|
| Streaming and file VLM inference | `rtvi-vlm` |

## Required peers

- Integrated mode needs model credentials/cache access but no standalone VLM
  NIM service.
- OpenAI-compatible mode needs a reachable endpoint and matching `VLM_NAME`.
- Kafka is required when `RTVI_VLM_MESSAGE_BUS=kafka` (the current Compose default).
- Without the Agent tier, call VLM Q&A directly at
  `POST http://<host>:${RTVI_VLM_PORT:-8018}/v1/chat/completions`.
- Generated captions use `RTVI_VLM_MESSAGE_BUS_TOPIC`, which defaults to
  `mdx-vlm-captions`. Do not set the obsolete `RTVI_VLM_KAFKA_TOPIC`; it is not
  forwarded by the current Compose service. When the requested topic is the
  default, enable Kafka without repeating the topic in the delta. VSS Compose
  still forwards legacy `RTVI_VLM_KAFKA_ENABLED`, although RT-VLM 26.08.2 does
  not consume `KAFKA_ENABLED`; keep it aligned with `RTVI_VLM_MESSAGE_BUS`
  until that compatibility field is removed.
- Redis is required when `RTVI_VLM_MESSAGE_BUS=redis` or
  `RTVI_VLM_ERROR_BUS=redis`; `ENABLE_REDIS_ERROR_MESSAGES` is only a legacy
  alias for the latter.
- Do not add `vlm_${VLM_MODE}_${VLM_NAME_SLUG}` for an integrated RT-VLM path.

## Available integrated model variants

These are the Cosmos Reason3 checkpoints RT-VLM can load on the integrated path.
`RTVI_VLM_MODEL_PATH` selects the checkpoint; `VLM_NAME` is the id RT-VLM then
advertises at `GET /v1/models`, derived from the path by dropping the `ngc:`
prefix and replacing `/` and `:` with `_`. Take both values from the same row —
a mismatch makes consumers fail with `400 BadParameters: No such model`.

| Model | `VLM_NAME` | `RTVI_VLM_MODEL_PATH` |
|---|---|---|
| CR3 Nano BF16 | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` | `ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final` |
| CR3 Nano FP8 | `nim_nvidia_cosmos3-nano-reasoner_modelopt-fp8-final_format_fix` | `ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix` |
| CR3 Nano NVFP4 | `nim_nvidia_cosmos3-nano-reasoner_modelopt-nvfp4-full-quantize-final_format_fix` | `ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix` |
| CR3 Super BF16 | `nim_nvidia_cosmos3-super-reasoner_modelopt-bf16-final` | `ngc:nim/nvidia/cosmos3-super-reasoner:modelopt-bf16-final` |
| CR3 Super FP8 | `nim_nvidia_cosmos3-super-reasoner_modelopt-fp8-final_format_fix` | `ngc:nim/nvidia/cosmos3-super-reasoner:modelopt-fp8-final_format_fix` |
| CR3 Super NVFP4 | `nim_nvidia_cosmos3-super-reasoner_modelopt-nvfp4-full-quantize-final_format_fix` | `ngc:nim/nvidia/cosmos3-super-reasoner:modelopt-nvfp4-full-quantize-final_format_fix` |

Notes on choosing a row:

- **Default to the profile, not to this table.** When the user does not name a
  model or quantization, keep the variant the selected deployment profile already
  ships and leave `RTVI_VLM_MODEL_PATH` / `VLM_NAME` untouched (`alerts` and
  `lvs` ship Nano BF16, `search` ships Nano FP8). Consult this table only when
  the user asks for a specific variant, or when placement forces a change under
  [Singleton and variant convergence](#singleton-and-variant-convergence).
- Nano is the default family for every profile in this repo; select Super only on
  an explicit request.
- Within a family, quantization is a memory/placement decision, not a capability
  one: BF16 is heaviest, FP8 fits alongside another GPU service, NVFP4 is the
  lightest and requires FP4-capable (Blackwell-class) hardware.
- Super is supported only on H100 and RTX PRO 6000, and needs one GPU dedicated
  to the VLM — do not co-locate another GPU service on it. Treat both as hard
  constraints when you are the one choosing the variant.
- **Surface an unsupported-hardware Super request before acting on it.** If Super
  is requested and the detected GPUs are neither H100 nor RTX PRO 6000, or no GPU
  can be dedicated to RT-VLM, stop and tell the user which constraint their system
  fails and what the detected hardware and placement actually are. Then ask
  (`AskUserQuestion`) whether to fall back to the equivalent Nano variant or
  override the constraint anyway. Only proceed with Super after the user overrides
  it knowingly — never assume the request itself is the override, and never
  silently downgrade to Nano either.
- Only the BF16 tag differs in shape between families (`bf16-final` for Nano,
  `modelopt-bf16-final` for Super). Copy tags verbatim rather than deriving them.
- `RTVI_VLM_MODEL_TO_USE=cosmos-reason3` for all six rows, and the served
  endpoint stays `http://rtvi-vlm:8000`; neither changes with the variant.

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
`RTVI_VLM_MODEL_PATH` — one row of
[Available integrated model variants](#available-integrated-model-variants) —
and do not combine values from different variants.

Consumer wiring is not part of that set.
`RTVI_VLM_MESSAGE_BUS`, `RTVI_VLM_MESSAGE_BUS_TOPIC` (generated captions),
`RTVI_VLM_KAFKA_INCIDENT_TOPIC` (verification incidents), and verifier config
mounts follow the consuming capability and operating mode, never the profile that
supplied the variant. Realtime VLM alerting (`2d_vlm`) must set
`RTVI_VLM_MESSAGE_BUS=kafka`, `RTVI_VLM_KAFKA_ENABLED=true`, and
`RTVI_VLM_KAFKA_INCIDENT_TOPIC=mdx-vlm-incidents`; the explicit incident topic
avoids the legacy VSS Compose fallback. CV verification (`2d_cv`) sets
`RTVI_VLM_MESSAGE_BUS=` and `RTVI_VLM_KAFKA_ENABLED=false` — verified incidents
reach Elasticsearch through the alert bridge, not RT-VLM's generated-message
path. For the
integrated path, `RTVI_VLM_MODEL_TO_USE=cosmos-reason3` and
the `http://rtvi-vlm:8000` endpoint (a consumer's `VLM_BASE_URL`) are invariant
across BF16 and FP8; a consumer owns that URL but never inherits it from the
variant profile.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_RT_VLM_TAG`, `RTVI_VLM_PORT`, `RT_VLM_DEVICE_ID` | Select image, host port, and GPU. |
| `RTVI_VLM_MODEL_TO_USE`, `RTVI_VLM_MODEL_PATH`, `VLM_NAME` | Select an integrated model and its advertised id (see [Available integrated model variants](#available-integrated-model-variants)). |
| `RTVI_VLM_ENDPOINT`, `RTVI_VLM_API_KEY`, `VLM_BASE_URL` | Configure an OpenAI-compatible backend. |
| `RTVI_VLLM_GPU_MEMORY_UTILIZATION`, `RTVI_VLM_MAX_MODEL_LEN`, `RTVI_VLLM_MAX_NUM_SEQS`, `RTVI_VLLM_MAX_NUM_BATCHED_TOKENS` | Bound vLLM memory and concurrency. |
| `RTVI_VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK`, `RTVI_VLM_BATCH_SIZE` | Tune frame sampling and batching. |
| `RTVI_VLM_MESSAGE_BUS`, `RTVI_VLM_MESSAGE_BUS_TOPIC`, `RTVI_VLM_KAFKA_BOOTSTRAP_SERVERS` | Configure generated-message publication. Current defaults are `kafka`, `mdx-vlm-captions`, and `kafka:29092`. VSS Compose still forwards legacy `RTVI_VLM_KAFKA_ENABLED`, but RT-VLM 26.08.2 ignores `KAFKA_ENABLED`; keep it aligned with `RTVI_VLM_MESSAGE_BUS` during the transition. `RTVI_VLM_KAFKA_TOPIC` is obsolete. |
| `RTVI_VLM_KAFKA_INCIDENT_TOPIC`, `RTVI_VLM_ERROR_BUS`, `RTVI_VLM_ERROR_MESSAGE_TOPIC` | Configure incident and error publication. RT-VLM 26.08.2 defaults to `mdx-vlm-incidents` and `mdx-vlm-errors`; current VSS Compose instead falls back to `vision-llm-events-incidents` and `vision-llm-errors`, so set the `mdx-*` names explicitly until those fallbacks are migrated. |
| `VLM_MODEL_SUPPORTS_AUDIO`, `VLM_TRUST_REMOTE_CODE`, `HF_TOKEN` | Enable supported audio or gated/custom HF models. |
| `INSTALL_PROPRIETARY_CODECS`, `FORCE_SW_AV1_DECODER` | Select runtime codec behavior. |

## Sources

- `deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml`
- `skills/vss-build-vision-ai/references/composition.md`
- `skills/deployment/vss-deploy-dense-captioning/references/deploy-rt-vlm-service.md`
- `skills/deployment/vss-deploy-dense-captioning/references/integrate-rt-vlm.md`
