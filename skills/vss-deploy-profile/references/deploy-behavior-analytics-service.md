# Deploy Behavior Analytics — Standalone Service

Deploy **just** `vss-behavior-analytics` (no agent, no perception, no UI) and point it at a custom config and calibration file. Useful for:

- Running a behavior-analytics pipeline against an **already-running** message broker (your own Kafka / Redis Streams / MQTT cluster, or one started by another VSS profile).
- Iterating on `vss-behavior-analytics-config.json` or a calibration JSON without restarting the full warehouse blueprint stack.
- Swapping the entrypoint (`warehouse` 2D / 3D, `dev_example`, `fusion_search`, `playback`) without modifying the image.

This reference is for the **service** — not a deployment profile. If you actually want the full warehouse pipeline (perception + auto-calibration + agent + UI), use [`warehouse.md`](warehouse.md) instead.

---

## Image

```
nvcr.io/nvstaging/vss-core/vss-behavior-analytics:3.2-26.05.15
```

Image pulls from NGC (`nvcr.io`). Confirm `$NGC_CLI_API_KEY` is set per [`ngc.md`](ngc.md).

The image is built from the [`behavior-analytics`](https://gitlab-master.nvidia.com/metromind/mdat/py-analytics-stream) repo (formerly `py-analytics-stream`). It ships:

- All `apps/<name>/main_*_app.py` entrypoints (warehouse 2D / 3D, smart_city, dev_example, fusion_search, playback, public_safety, robot_speed_control, rpm).
- Default configs under `resources/` (e.g. `warehouse_2d_config.json`, `warehouse_3d_config.json`, `dev_example_config.json`, …).
- Default calibration files under `resources/` (e.g. `calibration_2d_i.json`, `calibration_3d.json`).
- WORKDIR is `/behavior-analytics`. Mount-friendly directory `/resources/` is used by all profile compose files for the **operator-supplied** config and calibration.

## Prerequisites

1. **A message broker reachable from the container.** Default config wires to `localhost:9092` (Kafka). For Redis Streams or MQTT, see [Custom broker](#custom-broker).
   - The container uses `network_mode: "host"`, so `localhost:<port>` inside the container points at the host's broker.
   - If you don't have one running, the simplest path is to start an infra-only compose (`services/infra/compose.yml`) — see [`base.md`](base.md) for the bare broker stack.
2. **`VSS_APPS_DIR`** must point at `<repo>/deploy/docker/`. Required by the base compose's volume bind.
3. **`NGC_CLI_API_KEY`** set; first deploy pulls ~few-GB image. See [`ngc.md`](ngc.md).
4. **Customized config / calibration files on disk** (see [Customizing](#customizing-config-and-calibration)).

---

## Quick Start — bring up the base service

The base compose ships at `<repo>/deploy/docker/services/analytics/behavior-analytics/compose.yml`. It defines `vss-behavior-analytics-base` with the default 2D warehouse entrypoint:

```bash
cd <repo>/deploy/docker
export VSS_APPS_DIR=$(pwd)
docker compose -f services/analytics/behavior-analytics/compose.yml \
    up -d vss-behavior-analytics-base
```

By default this:

- Mounts `services/analytics/behavior-analytics/configs/vss-behavior-analytics-config.json` at `/resources/vss-behavior-analytics-config.json` inside the container.
- Runs `python3 apps/warehouse/main_warehouse_2d_app.py --config resources/warehouse_2d_config.json` — i.e. the **image-baked** config, not the mounted one (the base compose mounts the config but doesn't reference it on the CLI; the warehouse profiles do).

To actually use the mounted config, override the `command:` — that's what every real deployment does (see [Customizing](#customizing-config-and-calibration)).

### Verify

```bash
docker ps --filter "name=vss-behavior-analytics" --format '{{.Names}}\t{{.Status}}'
docker logs -f vss-behavior-analytics-base
```

Healthy logs include:

```
[Warehouse2DApp] starting with 4 worker processes
[CalibrationListener] subscribed to mdx-notification (key=calibration)
[ConfigListener]    request-config published (bootstrap_ref=behavior-analytics-<uuid>)
```

If you see Kafka `connect timeout` or `Connection refused`, the broker isn't reachable — confirm `localhost:9092` (or your override) is listening.

---

## Customizing config and calibration

The container reads two operator-supplied files at runtime:

| File | Container path | What it does |
|---|---|---|
| Behavior-analytics config | `/resources/vss-behavior-analytics-config.json` | All app/sensor config: broker (`sourceType`/`sinkType`), Kafka brokers + topics, sensor list, ROI/tripwire defaults, behavior watermark, allowlists, etc. Validates against the `AppConfig` Pydantic model in `py-analytics-stream`. |
| Calibration | `/resources/calibration.json` (and optional siblings) | Per-sensor `id`, `geoLocation`, `place[]`, `imageCoordinates[]`, `globalCoordinates[]`, ROI/tripwire definitions. Validates against `schemas/calibration.schema.json` in `py-analytics-stream`. |

Both come from the host via volume binds. The base compose only mounts the config; **you add the calibration mount in your override**, alongside the entrypoint command that references both.

### Recommended pattern — an override compose

Don't edit the base file directly (it's the shared template). Instead, write a sibling override that uses `extends:` to inherit the image and base mount, then layers your own config / calibration / command:

```yaml
# my-behavior-analytics.compose.yml  (anywhere under deploy/docker/)
services:
  vss-behavior-analytics:
    extends:
      file: ${VSS_APPS_DIR}/services/analytics/behavior-analytics/compose.yml
      service: vss-behavior-analytics-base
    container_name: vss-behavior-analytics
    volumes:
      # Override the default config mount with your own file.
      - /abs/path/to/my-config.json:/resources/vss-behavior-analytics-config.json
      # Add the calibration mount (the base file does not include it).
      - /abs/path/to/my-calibration.json:/resources/calibration.json
    # Run the entrypoint that matches your config / calibration shape.
    command: >
      python3 apps/warehouse/main_warehouse_2d_app.py
      --config /resources/vss-behavior-analytics-config.json
      --calibration /resources/calibration.json
```

Bring it up:

```bash
cd <repo>/deploy/docker
export VSS_APPS_DIR=$(pwd)
docker compose -f /abs/path/to/my-behavior-analytics.compose.yml up -d
```

> Do **not** `include:` the base file. `include` would double-mount `/resources/vss-behavior-analytics-config.json` (the base's mount + yours), which Compose flags as a conflict. Use `extends:` only — it inherits the image + restart policy + network_mode, and your `volumes:` block fully replaces the base list.

### Picking the entrypoint

The image ships every entrypoint from the upstream repo. Pick the one that matches what you want to run:

| Use case | Entrypoint | Extra flags |
|---|---|---|
| 2D warehouse / generic 2D spatial | `apps/warehouse/main_warehouse_2d_app.py` | `--config <path> --calibration <path>` |
| 3D warehouse / MV3DT | `apps/warehouse/main_warehouse_3d_app.py` | same |
| Smart city / ITS pipelines | `apps/smart_city/main_smart_city_app.py` | same |
| Dev / alerts profile | `apps/dev_example/main_dev_example_app.py` | `--config <path>` (no calibration) |
| Search analytics | `apps/fusion_search/main_fusion_search_analytics_app.py` | `--config <path>` |
| Playback recorded frames into the broker | `apps/playback/playback_frames.py` | `--config <path> --playback-filepath <path>` |
| Public-safety / RPM / robot-speed-control | `apps/<name>/main_<name>_app.py` | profile-specific |

If you point a `warehouse` entrypoint at a config / calibration that doesn't match its sensor topology (e.g. 2D config with a 3D calibration), the listener will log validation errors and the worker will sit on the previous good state — see [Troubleshooting](#troubleshooting).

### Custom broker

Edit your config JSON's top-level `sourceType` / `sinkType` and the matching transport block:

| Broker | `sourceType` / `sinkType` | Top-level block |
|---|---|---|
| Kafka | `"kafka"` | `kafka.brokers`, `kafka.topics[]`, `kafka.consumer.*`, `kafka.producer.*` |
| Redis Streams | `"redisStream"` | `redisStream.host`, `redisStream.port`, `redisStream.streams[]` |
| MQTT | `"mqtt"` | `mqtt.host`, `mqtt.port`, `mqtt.topics[]` |

`sourceType` and `sinkType` are independent — you can read from Kafka and write to Redis if you want, but typical deployments match them. The configurator in the warehouse blueprint sets both to `${STREAM_TYPE}` (`kafka` or `redisStream`) for consistency.

### Dynamic config / dynamic calibration

The container also accepts **runtime** config and calibration updates over the `mdx-notification` Kafka topic — published by the `video-analytics-api` service (or any producer that mirrors the wire contract). You don't need to redeploy to change a sensor's tripwire threshold or add a new sensor. See the upstream docs:

- `py-analytics-stream/readmes/dynamic-config.md` — wire format, validator policy, ack semantics.
- `py-analytics-stream/readmes/dynamic-calibration.md` — same for calibration.

If you only need static deployment, ignore this — your mounted JSONs are read at startup and stay loaded until a notification arrives.

---

## Standalone with your own broker (worked example)

A common setup: bring up Kafka manually (e.g. via `docker compose -f services/infra/compose.yml up -d kafka`), then point a standalone behavior-analytics at it:

```bash
cd <repo>/deploy/docker
export VSS_APPS_DIR=$(pwd)

# 1. Start the broker only.
docker compose -f services/infra/compose.yml up -d kafka

# 2. Copy a starter config and customize.
cp services/analytics/behavior-analytics/configs/vss-behavior-analytics-config.json \
   /tmp/my-config.json
# edit /tmp/my-config.json — broker host, topic names, sensors[], etc.

# 3. (Optional) Provide a calibration file.
cp industry-profiles/warehouse-operations/warehouse-2d-app/calibration/sample-data/warehouse-loading-dock-3cams-synthetic/calibration.json \
   /tmp/my-calibration.json

# 4. Write an override compose (see "Recommended pattern" above).

# 5. Bring up behavior-analytics.
docker compose -f /abs/path/to/my-behavior-analytics.compose.yml up -d
docker logs -f vss-behavior-analytics
```

Tear down with:

```bash
docker compose -f /abs/path/to/my-behavior-analytics.compose.yml down
# and the broker, if you started it
docker compose -f services/infra/compose.yml down kafka
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container restart loop; log starts with `FileNotFoundError: '/resources/...config...'` | Config mount path doesn't match the `--config` flag, or the host file doesn't exist. | Recheck both sides of the volume bind and the CLI flag. The container path in the bind must equal the path in `--config`. |
| `Connection refused` / `Connect timeout` on the broker | Broker isn't reachable on `localhost:<port>` from the container. | Confirm the broker is running on the host (`docker ps`); container is in host network mode so `localhost` = the host. If broker is in another container with its own network, point `kafka.brokers` at the container IP or host gateway. |
| `calibration schema violation` in logs after a notification arrives | The producer sent a payload that fails the per-action JSON Schema gate. | The previously-good calibration stays loaded; this is recoverable. See `py-analytics-stream/readmes/dynamic-calibration.md` for the schema. |
| `dropping config message: unrecognized reference-id ...` | A non-web-api producer is publishing `upsert` / `upsert-all` on `mdx-notification` with an unrecognized reference id. | Reference id must start with `video-analytics-api-` (web-api) or `behavior-analytics-` (bootstrap reply), or equal the source-type literal (`kafka` / `redis` / `mqtt`) for direct-publisher upserts. See `readmes/dynamic-config.md`. |
| `WORKDIR /behavior-analytics: not found` (older image) | Pinned an old tag where the WORKDIR was `/py-analytics-stream`. | Upgrade to `3.2-26.05.15` or later. |

For dynamic-config / dynamic-calibration debugging, the container also runs an integration-test driver if invoked manually:

```bash
docker exec -it vss-behavior-analytics \
    python3 tests/integration/dynamic_calibration/dynamic_calibration_e2e.py
```

(Won't work inside slim/distroless image variants — only the dev image carries `tests/`.)

---

## Tearing down

```bash
docker compose -f /abs/path/to/my-behavior-analytics.compose.yml down
```

`restart: always` is in the base, so the container will auto-restart on host reboot until you `down` it. Use `docker compose ... down -v` if you also want to clear named volumes (this service doesn't define any, but a parent compose might).

Full multi-profile teardown lives in [`teardown.md`](teardown.md).
