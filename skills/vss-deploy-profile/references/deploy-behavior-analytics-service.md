# Deploy Behavior Analytics — Standalone Service

Deploy **just** `vss-behavior-analytics` (no agent, no perception, no UI) — useful when you want to:

- Run a behavior-analytics pipeline against an existing broker (or no broker at all).
- Pick a different entrypoint (warehouse 2D / 3D / mv3dt, dev_example, fusion_search) without modifying the image.

---

## What you edit

You only edit the existing service compose:

```
<repo>/deploy/docker/services/analytics/behavior-analytics/compose.yml
```

1. **`command:`** — which app entrypoint to run.
2. **`volumes:`** — what config (required) and what calibration (optional) to mount.
3. The `--config` and optional `--calibration` flags inside the same `command:` line.

After editing, deploy with:

```bash
cd <repo>/deploy/docker
export VSS_APPS_DIR=$(pwd)
docker compose -f services/analytics/behavior-analytics/compose.yml \
    up -d vss-behavior-analytics-base
```

---

## Step 1 — Pick an entrypoint

Set the first half of `command:` to one of the following:

| Entrypoint | Class | What it does |
|---|---|---|
| `apps/warehouse/main_warehouse_2d_app.py` | `Warehouse2DApp` | 2D spatial pipeline: object tracking → behavior creation, ROI / tripwire / FOV-count / restricted-area / confined-area / proximity-violation events, plus map-matching. Single Kafka/Redis source, single sink. **The default.** |
| `apps/warehouse/main_warehouse_3d_app.py` | `Warehouse3DApp` | Same as 2D plus a **space-analyzer** processor (estimates space utilization per region) and a **frame-enhancement** processor (3D BEV-style metadata). Three parallel processors instead of one. Use this for 3D warehouse / multi-view 3D tracking (mv3dt). |
| `apps/dev_example/main_dev_example_app.py` | `DevExampleApp` | Smaller app that focuses on **FOV-count violation** and **restricted-area violation** detection. No behavior creation, no map-matching. Good starting point for new incident types — also the entrypoint used by `dev-profile-alerts`. |
| `apps/fusion_search/main_fusion_search_analytics_app.py` | `FusionSearchAnalyticsApp` | Two-path app: (a) behavior creation from raw frames, like 2D but without the FOV-count / ROI / tripwire events; (b) **video-embedding downsampling** — reads chunked video embeddings, optionally downsamples them (SDT / fixed-window), writes filtered embeddings. Use this with the VSS search profile. |

**mv3dt** uses `main_warehouse_3d_app.py` (the multi-view 3D tracker is a perception-side variant — the analytics pipeline is the same as 3D). There is no separate `main_mv3dt_app.py`.

---

## Step 2 — Choose a config (required)

Every entrypoint requires `--config <path>`. The container has three viable sources:

### Option A — Use the image-baked default

Cheapest path. The image ships defaults at `/behavior-analytics/resources/*.json`. No volume mount needed. Match the config to the entrypoint:

| Entrypoint | Image-baked config flag |
|---|---|
| `main_warehouse_2d_app.py` | `--config resources/warehouse_2d_config.json` |
| `main_warehouse_3d_app.py` | `--config resources/warehouse_3d_config.json` |
| `main_dev_example_app.py` | `--config resources/dev_example_config.json` |
| `main_fusion_search_analytics_app.py` | `--config resources/fusion_search_analytics_config.json` |

The defaults assume Kafka at `localhost:9092` and the standard `mdx-*` topic names (`mdx-raw`, `mdx-behavior`, `mdx-frames`, `mdx-notification`, `mdx-events`, `mdx-incidents`). Edit the `command:` accordingly:

```yaml
command: python3 apps/warehouse/main_warehouse_3d_app.py --config resources/warehouse_3d_config.json
```

You can also drop the volume mount entirely in this case — the base file's mount becomes unused.

### Option B — Use a profile's existing config

If you want the behavior/topic/sensor wiring a specific blueprint uses (already tuned to its dataset), point the volume mount at one of the profile-shipped configs and reference the mounted path on the `--config` flag.

Recommended pairings (entrypoint → existing config):

| Entrypoint | Recommended existing config |
|---|---|
| `main_warehouse_2d_app.py` | `industry-profiles/warehouse-operations/warehouse-2d-app/vss-behavior-analytics/configs/vss-behavior-analytics-config.json` |
| `main_warehouse_3d_app.py` | `industry-profiles/warehouse-operations/warehouse-3d-app/vss-behavior-analytics/configs/vss-behavior-analytics-config.json` |
| `main_warehouse_3d_app.py` (mv3dt) | `industry-profiles/warehouse-operations/warehouse-mv3dt-app/vss-behavior-analytics/configs/vss-behavior-analytics-config.json` |
| `main_dev_example_app.py` | `developer-profiles/dev-profile-alerts/vss-behavior-analytics/configs/vss-behavior-analytics-config.json` |
| `main_fusion_search_analytics_app.py` | the search profile's own config (lives outside `behavior-analytics/`; see [`search.md`](search.md)) |

Compose change:

```yaml
services:
  vss-behavior-analytics-base:
    volumes:
      - $VSS_APPS_DIR/industry-profiles/warehouse-operations/warehouse-3d-app/vss-behavior-analytics/configs/vss-behavior-analytics-config.json:/resources/vss-behavior-analytics-config.json
    command: python3 apps/warehouse/main_warehouse_3d_app.py --config /resources/vss-behavior-analytics-config.json
```

### Option C — Use your own custom config

Drop in any absolute host path; copy one of the above as a starting point and edit. Compose change is identical to Option B but with `/abs/path/to/my-config.json` as the bind source.

```yaml
volumes:
  - /abs/path/to/my-config.json:/resources/vss-behavior-analytics-config.json
command: python3 apps/warehouse/main_warehouse_2d_app.py --config /resources/vss-behavior-analytics-config.json
```

### Config — what's in it

Top-level shape (every config has all of these):

| Section | What it controls |
|---|---|
| `kafka` / `redisStream` / `mqtt` | Broker host, topics, consumer/producer tuning. `sourceType` / `sinkType` in the `app[]` section pick which one is actually used. |
| `app[]` | List of `{name, value}` strings. Knobs like `behaviorWatermarkSec`, `numWorkersForBehaviorCreation`, `stateManagementFilter`, `clusterThreshold`, `trajDirectionMode`, plus per-incident-type toggles (`fovCountViolationIncidentEnable`, `restrictedAreaViolationIncidentEnable`, etc.). |
| `sensors[]` | Per-sensor entries with `{id, configs: [{name, value}]}` — per-sensor overrides for things like `tripwireMinPoints`, `proximityDetectionEnable`, `anomalySpeedViolation`. |

For the full schema (every field, type, default), the authoritative source is the Pydantic model in the repo:

- `behavior-analytics/src/mdx/analytics/core/schema/config.py` — `AppConfig` + subsection models.
- `behavior-analytics/src/mdx/analytics/core/transform/config/config_validator.py` — `ALLOWED_APP_KEYS` / `ALLOWED_SENSOR_KEYS` allowlists (for dynamic updates).

Higher-level docs:

- `readmes/configuration.md` — config field guide.

---

## Step 3 — Choose a calibration (optional)

Calibration tells the app the sensor map, ROIs, tripwires, geo-locations, homographies, etc. It's **optional** at startup.

### Calibration types

The type is encoded in the calibration JSON itself, on the top-level `calibrationType` field. There are three values, and the app picks its calibration class accordingly (`CalibrationType` enum in `behavior-analytics/src/mdx/analytics/core/transform/calibration/calibration_base.py`):

| `calibrationType` | Class | What it does |
|---|---|---|
| `"cartesian"` | `CalibrationE` | **Typical for warehouse / smart-city.** Maps image-plane coordinates (pixels) to real-world Cartesian metres via the per-sensor homography (`imageCoordinates[]` ↔ `globalCoordinates[]`). All downstream behavior creation, ROI / tripwire / proximity / space-analytics math is in metres. **Recommended starting point.** |
| `"geo"` | `Calibration` | Maps image coordinates to geographic lat/lng. Use when sensors are placed against a real map (OSM, GIS) and you want behaviors / events anchored to GPS. |
| `"image"` | `CalibrationI` | No real-world mapping — keeps coordinates in raw pixel space. The downstream pipeline still runs, but distance / speed / area numbers are in pixels, not metres, and most metric-based incident thresholds become meaningless. |

### What happens if you skip calibration

Don't add a `--calibration` flag and don't mount one. The app starts with a `DynamicCalibration` wrapper that initially behaves as `CalibrationI` (image-plane). It then:

1. **Watches `mdx-notification`** for the first `calibrationType` notification. When one arrives, the wrapper switches itself to the typed subclass (`CalibrationE` / `Calibration` / `CalibrationI`) inferred from the payload's `calibrationType`. After the switch, all subsequent updates go through the typed instance via the same Kafka flow.
2. **Until that first notification arrives**, frames are processed with image-plane coordinates — effectively a no-op for analytics (no real-world distances, no ROI/tripwire firings against a map). If you don't intend to wire a producer for dynamic calibration, supply a static calibration file instead.

### Pick a calibration source

- **Use one of the profile-shipped calibrations.** Same pattern as config Option B:

  | Entrypoint | Recommended existing calibration |
  |---|---|
  | `main_warehouse_2d_app.py` | `industry-profiles/warehouse-operations/warehouse-2d-app/calibration/sample-data/<dataset>/calibration.json` |
  | `main_warehouse_3d_app.py` | `industry-profiles/warehouse-operations/warehouse-3d-app/calibration/sample-data/<dataset>/calibration.json` |
  | `main_warehouse_3d_app.py` (mv3dt) | `industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/<dataset>/calibration.json` |
  | `main_dev_example_app.py` | the dev profile may not need one. |
- **Bring your own.** Any absolute host path that conforms to the calibration JSON schema. If you're hand-rolling one, start from the `"cartesian"` type — that's the path the rest of the pipeline is tuned for.

  Compose change for either of the last two:

  ```yaml
  volumes:
    - $VSS_APPS_DIR/services/analytics/behavior-analytics/configs/vss-behavior-analytics-config.json:/resources/vss-behavior-analytics-config.json
    - /abs/path/to/calibration.json:/resources/calibration.json   # or a profile sample-data path
  command: >
    python3 apps/warehouse/main_warehouse_2d_app.py
    --config /resources/vss-behavior-analytics-config.json
    --calibration /resources/calibration.json
  ```

The schema for the calibration JSON is vendored from `vss-analytics-api/web-api-core/schemas/ajv/calibration.json` and lives at `behavior-analytics/src/mdx/analytics/core/transform/calibration/schemas/calibration.schema.json` — read `readmes/dynamic-calibration.md` for the per-action policy and the sensor/ROI/tripwire field semantics.

---

## Step 4 — Broker (not required to launch)

`vss-behavior-analytics` does **not** require a broker to be present at start time:

- The container starts fine without Kafka/Redis/MQTT reachable.
- The Kafka client retries the broker connection. You'll see repeated `Connect to ipv4#…:9092 failed: Connection refused` warnings in `docker logs vss-behavior-analytics-base` — that's expected, not a fatal error.
- `restart: always` is set in the base compose, so even if the process exits it'll come back up. Once the broker becomes reachable the consumer thread starts draining messages normally.

This is convenient for "bring up the analytics container first, broker later" workflows. If you want it to fail-fast when there's no broker (e.g. in CI), wrap with your own healthcheck or override `restart:` to `on-failure`.

### When a broker IS present — dynamic updates

If Kafka is up and reachable (the same broker the producer / `video-analytics-api` uses), two runtime-update flows become available — no container restart needed:

#### Dynamic config

Publish an `upsert` (per-key patch) or `upsert-all` (full snapshot) message to topic `mdx-notification` with Kafka key `behavior-analytics-config` and headers:

- `event.type`: `upsert` | `upsert-all` | `request-config` | `ack`
- `reference-id`: `video-analytics-api-<uuid>` (web-api originated) or `behavior-analytics-<uuid>` (bootstrap reply) or the source-type literal (`kafka` / `redis` / `mqtt`) for direct-publisher upserts.

Body: `{"status": ..., "config": <patch>, "error": ...}`.

The listener validates each message at the envelope layer (rejects unknown keys, missing config, malformed status/error) and at the per-payload layer (rejects forbidden sections, bad item shapes). Successful upserts are persisted to disk, applied to every worker, and ACK'd back over the topic.

Full wire contract + ack semantics: `readmes/dynamic-config.md` in the behavior-analytics repo.

#### Dynamic calibration

Publish to the same topic with Kafka key `calibration` and headers:

- `event.type`: `upsert-all` (full snapshot) | `upsert` (per-sensor merge) | `delete` (per-sensor removal)
- `timestamp`: ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS.fffZ`).

Body: JSON sensor list (and ROIs/tripwires/homographies for `upsert-all`).

The listener validates against the vendored AJV schema before persisting. Schema violations log a `calibration schema violation` warning and are dropped — the previously-good calibration stays loaded.

Full wire contract + per-action validation policy: `readmes/dynamic-calibration.md`.

Both flows live entirely on the broker — the producer can be `video-analytics-api`, your own script, or any Kafka client that mirrors the wire shape. They're the recommended way to change configuration after the container is running, so you don't have to redeploy.

---

## Deploy + verify

```bash
cd <repo>/deploy/docker
export VSS_APPS_DIR=$(pwd)

# (one-time) edit services/analytics/behavior-analytics/compose.yml — entrypoint, config volume, optional calibration volume.

docker compose -f services/analytics/behavior-analytics/compose.yml up -d vss-behavior-analytics-base

docker ps --filter "name=vss-behavior-analytics" --format '{{.Names}}\t{{.Status}}'
docker logs -f vss-behavior-analytics-base
```

Healthy log lines include:

```
[Warehouse2DApp] starting with N worker processes
[CalibrationListener] subscribed to mdx-notification (key=calibration)
[ConfigListener] request-config published (bootstrap_ref=behavior-analytics-<uuid>)
```

If you skipped calibration, you'll also see:

```
DynamicCalibration: no --calibration provided; waiting for first calibration notification...
```

## Teardown

```bash
docker compose -f services/analytics/behavior-analytics/compose.yml down
```

For a multi-service teardown (broker, ES, etc.) see [`teardown.md`](teardown.md).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `FileNotFoundError: '/resources/...'` on startup | `--config` flag and the volume bind target don't match. | Either point both at the same container path, or drop the mount and use the image-baked `resources/<config>.json`. |
| Container alive but log just keeps printing `Connect to … failed: Connection refused` | No broker listening on the host. | Expected if you're running broker-less; otherwise start your broker. The container will pick up the connection automatically once it's up. |
| `calibration schema violation` after a notification arrives | Producer sent a payload that fails the JSON Schema gate. | Previously-good calibration stays loaded; check the producer's payload against the schema in `src/mdx/analytics/core/transform/calibration/schemas/calibration.schema.json`. |
| `dropping config message: unrecognized reference-id …` | Inbound dynamic-config `upsert` / `upsert-all` carries a reference-id outside the accepted set. | Reference-id must start with `video-analytics-api-` (web-api), `behavior-analytics-` (bootstrap echo), or equal the active source-type literal (`kafka` / `redis` / `mqtt`). |
| `dropping config message: no config to update` | Inbound `upsert` had `config: null` or omitted the field. | An `upsert` with no config is a producer bug; `upsert-all` with `config=null` is allowed (it's the bootstrap-failure signal). |
| Workers fall behind / `Avg processing speed` very low | Worker count too low for the input rate. | Increase `numWorkersForBehaviorCreation` (and `numWorkersForFrameEnhancement` / `numWorkersForSpaceEstimation` for 3D) in the config's `app[]` section. |
