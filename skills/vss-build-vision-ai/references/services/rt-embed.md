# RT-Embed Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile key |
|---|---|
| Video and text embedding generation | `rtvi-embed` |

## Required peers

- Requires writable model caches and the VIOS clip-storage path.
- Search event ingestion requires Kafka and the Search analytics owner: RT-Embed
  publishes to `mdx-embed` (`MESSAGE_BUS_TOPIC`), which the Search analytics
  owner filters into `mdx-embed-filtered` (see `services/search.md` for the full
  write path).
- `HF_TOKEN` is required only for gated or authenticated Hugging Face access.
- Redis is required only when Redis error messages are enabled.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_RT_EMBED_IMAGE`, `VSS_RT_EMBED_TAG`, `RTVI_EMBED_PORT`, `RT_EMBED_DEVICE_ID` | Select image, host port, and GPU. |
| `MODEL_PATH`, `MODEL_IMPLEMENTATION_PATH`, `MODEL_REPOSITORY_SCRIPT_PATH` | Select a supported embedding model implementation. |
| `RTVI_EMBED_NUM_VLM_PROCS`, `RTVI_EMBED_NUM_GPUS`, `VLM_BATCH_SIZE` | Tune execution parallelism. |
| `MESSAGE_BUS`, `MESSAGE_BUS_TOPIC`, `ERROR_BUS`, `RTVI_EMBED_KAFKA_BOOTSTRAP_SERVERS` | Configure embedding event and error publishing (see note below). |
| `RTVI_EMBED_HF_CACHE`, `NGC_MODEL_CACHE`, `HF_TOKEN`, `NGC_API_KEY` | Configure model caches and credentials. |
| `INSTALL_PROPRIETARY_CODECS`, `FORCE_SW_AV1_DECODER` | Select runtime codec behavior. |

## Kafka output contract

The generated-output bus is off unless `MESSAGE_BUS` is set: the root Compose
include path does not load `services/rtvi/rtvi-embed/.env` (which sets
`MESSAGE_BUS=kafka`), so it falls back to the compose default empty value, which
disables Kafka output entirely. When a generated profile requires embedding
events to flow through Kafka, set both `MESSAGE_BUS=kafka` and
`MESSAGE_BUS_TOPIC=mdx-embed` in the build `override.env`. Without this override
the embedding write path is broken: RT-Embed produces no Kafka output and the
Search analytics `mdx-embed` -> `mdx-embed-filtered` indexing path stays empty.
`ERROR_BUS` is the separate error bus, unset by the same mechanism and disabled
by the same empty default; set `ERROR_BUS=kafka` alongside it if the build needs
RT-Embed error events on Kafka (the error topic `RTVI_EMBED_ERROR_MESSAGE_TOPIC`
already defaults to `vision-embed-errors`, so only the bus toggle needs setting).

## Placement and sizing

RT-Embed has a fixed footprint determined primarily by its model, stream count,
workers, and batch size. Prefer a dedicated device; share only when the measured
combined budget fits. See `../sizing.md` for placement resolution and benchmark
stream ceilings.

## Sources

- `deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml`
- `skills/vss-deploy-video-embedding/references/environment.md`
- `skills/vss-deploy-video-embedding/references/integrate-vss-deploy-video-embedding.md`
