# RT-Embed Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile key |
|---|---|
| Video and text embedding generation | `rtvi-embed` |

## Required peers

- Requires writable model caches and the VIOS clip-storage path.
- Search event ingestion requires Kafka and the Search analytics owner.
- `HF_TOKEN` is required only for gated or authenticated Hugging Face access.
- Redis is required only when Redis error messages are enabled.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `RTVI_EMBED_IMAGE`, `RTVI_EMBED_TAG`, `RTVI_EMBED_PORT`, `RT_EMBED_DEVICE_ID` | Select image, host port, and GPU. |
| `MODEL_PATH`, `MODEL_IMPLEMENTATION_PATH`, `MODEL_REPOSITORY_SCRIPT_PATH` | Select a supported embedding model implementation. |
| `RTVI_EMBED_NUM_VLM_PROCS`, `RTVI_EMBED_NUM_GPUS`, `VLM_BATCH_SIZE` | Tune execution parallelism. |
| `RTVI_EMBED_KAFKA_ENABLED`, `RTVI_EMBED_KAFKA_TOPIC`, `RTVI_EMBED_KAFKA_BOOTSTRAP_SERVERS` | Configure embedding events. |
| `RTVI_EMBED_HF_CACHE`, `NGC_MODEL_CACHE`, `HF_TOKEN`, `NGC_API_KEY` | Configure model caches and credentials. |
| `INSTALL_PROPRIETARY_CODECS`, `FORCE_SW_AV1_DECODER` | Select runtime codec behavior. |

## Kafka output contract

When a generated profile requires embedding events to flow through Kafka, set
both `RTVI_EMBED_KAFKA_ENABLED=true` and
`RTVI_EMBED_KAFKA_TOPIC=mdx-embed` in the build `override.env`. The root
Compose include path does not load `services/rtvi/rtvi-embed/.env`, and the
service compose fallback topic is `vision-embed-messages`, which does not feed
the Search analytics `mdx-embed` -> `mdx-embed-filtered` indexing path.

## Sources

- `deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml`
- `skills/vss-deploy-video-embedding/references/environment.md`
- `skills/vss-deploy-video-embedding/references/integrate-vss-deploy-video-embedding.md`
