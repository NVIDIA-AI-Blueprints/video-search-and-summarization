# Integration Reference: Behavior Analytics

> **Scope.** This is the neutral integration contract for the **Behavior Analytics** microservice (image `vss-behavior-analytics`, compose base service-key `vss-behavior-analytics-base`). It describes how to fold the service into a composed deployment. The standalone deploy companion is `deploy-behavior-analytics-service.md`; runtime config detail lives in `configuration.md`, `dynamic-config.md`, `dynamic-calibration.md`.

## Overview

Behavior Analytics is the spatial-analytics stage of the VSS pipeline: a Python streaming app that **consumes frame metadata from a message broker, processes it through configurable transforms (calibration, behavior/trajectory tracking, ROI/tripwire events, incident detection, video-embedding downsampling), and emits behaviors / events / incidents back to the broker**. It has no inbound REST surface — it is entirely broker- and config-driven. Its core loop per registered processor is: **(1)** poll a source topic, **(2)** transform via the active calibration, **(3)** update per-sensor state, **(4)** write the result to a sink topic.

### Entrypoints and processing modes

The image ships several apps under `apps/`; the compose `command:` selects one. Each app registers one or more **processors**, and a processor's worker count (`numWorkersFor*` config key) gates whether it runs at all — `0` (or an omitted key, for apps that default to `0`) leaves that processor unregistered.

- `apps/analytics/main_analytics_2d_app.py` (`Analytics2DApp`) — **the base default.** Behavior creation + frame enhancement / incidents on 2D world-plane coordinates.
- `apps/analytics/main_analytics_3d_app.py` (`Analytics3DApp`) — 3D coordinates + a space-utilization processor.
- `apps/search_and_alerts/main_search_and_alerts_app.py` (`SearchAndAlertsApp`) — three opt-in processors selected by `numWorkersFor*`:
  - **incident generation** (`numWorkersForIncidentGeneration`) → enhanced frames + incidents,
  - **behavior creation** (`numWorkersForBehaviorCreation`) → behaviors,
  - **video-embedding downsampling** (`numWorkersForEmbedFiltering`) → filtered embeddings.
  Set the unwanted paths to `0`: incident-only reproduces the alerts profile, behaviors + embed reproduces the search profile.
- `apps/composite/main_composite_app.py` (`CompositeApp`) — heavyweight config-driven superset with independent workers for behavior/events/detections, frame enhancement/incidents, space estimation, embedding filtering, and behavior clustering. All workers default to `0`; destination mappings independently select which produced outputs are retained. Use it only when no profile-specific app provides the required capability set, and run exactly one behavior producer for a sensor set.

Source-of-truth definitions: `deploy/docker/services/analytics/behavior-analytics/compose.yml` (the `vss-behavior-analytics-base` service — image, host-network, config mount, default command; extend, never `include`), and the per-profile config JSONs under `deploy/docker/developer-profiles/*/vss-behavior-analytics/configs/` (and `.../vss-search-analytics/configs/` for the search profile).

## Required Peer Services

**Core data path (required):**

- **Message broker (Kafka / Redis Streams / MQTT)** — required. The `sourceType` / `sinkType` app-config keys pick which backend is live (Kafka is the default; brokers are configured **inside the config JSON**, not via env — e.g. `kafka.brokers: kafka:29092` (a Docker DNS service name on the compose bridge network; for a standalone deploy against an external broker, set this to a reachable `host:port` — see Network Requirements § Standalone caveat). Behavior Analytics consumes candidate frame metadata and produces behaviors/events/incidents. Owned by ELK/infra.
- **kafka-topic-init-container** — required (Kafka backend). Every configured Kafka topic must exist before the app uses it. A missing destination mapping disables that output and logs one warning instead of crashing; map and pre-create every output the deployment intends to retain (see Outputs). Owned by ELK/infra.
- **Calibration source** — required for spatial transforms (`transform_frame`, ROIs, tripwires, homography). Supplied as a mounted calibration JSON (`--calibration <path>`) or delivered at runtime via a dynamic-calibration notification (see Inputs). **Optional at startup**: with no file the app uses `CalibrationI` (image-plane, no perspective) and can switch once a calibration arrives. See `dynamic-calibration.md`.
- **Upstream detector (for the incident / behavior paths)** — an RTVI CV / perception producer (Grounding DINO / RT-DETR, DeepStream) writing frame metadata to the raw topic. Without it the incident/behavior processors have no input. Owned by RT-CV (`skills/vss-deploy-detection-tracking-2d/`). In the alerts profile the config-bearing key `perception-alerts` is contributed by the `cv-verification` variant in `vss-build-vision-agent/references/patch-alerts.md`.

**Path-specific / downstream (conditional):**

- **Video-embedding producer** — required only when the embedding-downsampling processor runs (search path): it consumes chunked video embeddings from the embed topic. Owned by the video-embedding microservice (`integrate-vss-deploy-video-embedding.md`).
- **ELK (Elasticsearch / Logstash / Kibana)** — required for the outputs to be queryable/visualized. **Logstash consumes Behavior Analytics' output topics** (`mdx-incidents`, `mdx-frames`, `mdx-behavior`, `mdx-embed-filtered`) via protobuf codecs and indexes them into ES (`mdx-frames-*`, `mdx-behavior-*`, `mdx-incidents-*`, etc.). Not a hard `depends_on` of Behavior Analytics itself, but required for the pipeline to be observable. Owned by ELK (`integrate-elk.md`).
- **Alert Microservice (`alert-bridge`)** — downstream consumer of `mdx-incidents` / `mdx-alerts` when Behavior Analytics is the candidate-alert source for VLM verification. The `category` field on emitted incidents is the join key into `alert_type_config.json`. Owned by the Alert Microservice (`integrate-alerts.md`).
- **Video Analytics API** — optional headless query surface over the ES indices; does not depend on Behavior Analytics directly. Owned by `skills/vss-setup-video-analytics-api/`.

> **Where the `component_services:` block lives (decoupling).** Behavior Analytics is owned by the `vss-setup-behavior-analytics` skill, so — per the decoupling convention used by VIOS / RT-VLM / the Alert Microservice — its structured `component_services:` selection (upstream compose keys + the `ba_path` mode/path variant) is **not** carried here. It lives in the orchestrator's own patch reference `vss-build-vision-agent/references/patch-behavior-analytics.md`, which documents **all three** paths (alerts-incident, search-embedding, analytics-2d). This file is the neutral integration contract only.

## Integration Interfaces

### Inputs

- **Method:** Broker topic (consume) — raw frame metadata
  **Topic:** `raw` → `mdx-raw` (Kafka) / stream key (Redis) / topic (MQTT), per `kafka.topics[name=raw]`.
  **Schema:** NvSchema `nv.Frame` protobuf. Each frame carries `sensorId` and per-object type/bbox.
  **Consumed by:** the incident and behavior processors.

- **Method:** Broker topic (consume) — chunked video embeddings
  **Topic:** `embed` → `mdx-embed`.
  **Schema:** NvSchema `nv.VisionLLM` protobuf.
  **Consumed by:** the embedding-downsampling processor only (search path).

- **Method:** Dynamic config / calibration (consume) — runtime updates
  **Topic:** `notification` → `mdx-notification`.
  **Schema:** JSON config/calibration update notifications. Lets operators push `AppConfig` changes and calibration reloads without a restart (values are re-read at use time, so they take effect on the next access). See `dynamic-config.md` / `dynamic-calibration.md`.

- **Method:** Mounted file — calibration JSON (optional at startup)
  **Path:** `--calibration <path>` (host bind). Defines the sensor map, ROIs, tripwires, geo/homography.

### Outputs

Produced only by processors that are enabled (worker count > 0), and emitted only when the destination is mapped. Each output the deployment intends to retain must appear in the active sink's destination list (`kafka.topics`, `redisStream.streams`, or `mqtt.topics`); configured Kafka topics must also be pre-created.

- **`behavior` → `mdx-behavior`** — per-sensor behaviors (`nv.Behavior`). Behavior processor.
- **`incidents` → `mdx-incidents`** — violation incidents (`nv.Incident`) with a `category` (proximity / restricted-area / confined-area / FOV-count …). Incident processor.
- **`frames` → `mdx-frames`** — calibration-enhanced frames (`nv.Frame`, ROI/FOV metrics + violation flags). Written by the incident processor's frame-enhancement step; consumed/indexed by Logstash.
- **`embedFiltered` → `mdx-embed-filtered`** — downsampled/filtered video embeddings (`nv.VisionLLM`). Embedding processor.
- **`events` → `mdx-events`** — ROI/tripwire events (`nv.Behavior`), for apps that register the event path.

## Configuration & Modes

Config is a single JSON (`AppConfig`): a broker block (`kafka` / `redisStream` / `mqtt` with `topics`), a `sensors[]` block, and an `app[]` list of `{name, value}` string knobs.

> **This table is the integration-relevant subset only.** For the complete config field guide — every `app[]` / `sensors[]` key, incident enable/threshold/timing knobs, and worked examples — see [`configuration.md`](configuration.md) (which in turn defers to `services/analytics/behavior-analytics/src/mdx/analytics/core/schema/config.py` as the authoritative source, and `services/analytics/behavior-analytics/docs/incident-detection.md` for incident timing). Runtime-update semantics for these keys are in [`dynamic-config.md`](dynamic-config.md). Do not treat the list below as exhaustive.

Key integration knobs:

| Knob | Purpose |
|---|---|
| `sourceType` / `sinkType` | `kafka` \| `redisStream` \| `mqtt` — selects the active broker (must match the `topics`/stream block present). |
| `numWorkersForIncidentGeneration` / `numWorkersForBehaviorCreation` / `numWorkersForEmbedFiltering` | Per-processor worker count. `0` disables that processor (the mechanism that selects incident-only vs search vs full mode in `SearchAndAlertsApp`). |
| `numWorkersForBehaviorCreation` / `numWorkersForFrameEnhancement` (analytics apps) | Worker counts for the 2D/3D analytics processors. |
| `*IncidentEnable` toggles | `proximityViolationIncidentEnable`, `restrictedAreaViolationIncidentEnable`, `confinedAreaViolationIncidentEnable`, `fovCountViolationIncidentEnable` — per-incident-type gates (default `false`). |
| `fovCountViolationIncidentObjectType` | Object type to count. **Must match the detector's label casing** — the RT-DETR / GDINO label file emits lowercase (e.g. `person`); the FOV comparison is exact `==`, so a casing mismatch silently fires no incidents. |
| `stateManagementFilter` | JSON list of object types tracked by state management. |
| `embed*` (`embedEnableDownsampling`, `embedDownsampler*`, …) | Video-embedding downsampling parameters (search path). |

## Environment Variables

The base compose is deliberately thin — broker endpoints, topics, and all tuning live in the **mounted config JSON**, not env. Broker addresses in the shipped configs are **Docker DNS service names** (`kafka:29092`, `redis:6379`), resolved on the compose bridge network — **not** `${HOST_IP}` (Behavior Analytics does not read `HOST_IP`). The env touched at compose time:

| Variable | Purpose | Required? |
|---|---|---|
| `VSS_APPS_DIR` | Compose interpolation for the config (and `extends`) bind-mount paths (`$VSS_APPS_DIR/services/analytics/behavior-analytics/...`). | **Yes (compose-set)** |
| `STREAM_TYPE` | Selects the search-profile config variant (`vss-search-analytics-${STREAM_TYPE}-config.json`, e.g. `kafka` / `redis`). | Search profile only |

## Network Requirements

- **Ports exposed:** none inbound (no REST surface).
- **Outbound traffic:**
  - Message broker — Kafka `kafka:29092`, Redis `redis:6379`, or MQTT `<mqtt-broker>:1883` (per `sourceType`/`sinkType`), as set in the config JSON's broker block.
  - GitHub Container Registry `ghcr.io` for the image pull on first boot (the image is public; no login needed). Only if you override `VSS_CONTAINER_REGISTRY` to NGC do you also need `nvcr.io`.
- **DNS / hostname assumptions:** peers resolve by **Docker DNS service name** on the compose bridge network (`kafka`, `redis`, …) — the base compose uses the default bridge network (no `network_mode: host`), and the profile composes join it via the `default` network with alias `vss-behavior-analytics`.
- **`network_mode`:** default bridge (not host).

> **Standalone caveat.** The shipped broker addresses (`kafka:29092`, `redis:6379`) only resolve **inside the VSS compose network**, where the broker services run under those names. Running Behavior Analytics on its own (this skill's standalone path) against an **external** broker requires editing the config JSON's `brokers` / `host` to a reachable address (a routable `host:port`, or attach the container to the broker's network) — the default DNS names will not resolve on a lone bridge network. See `deploy-behavior-analytics-service.md § Step 4`.

## Known Integration Constraints

- **`extends`, never `include`.** The base `vss-behavior-analytics-base` block is designed to be composed via compose `extends:` (a profile service overrides `command`, `profiles`, `container_name`, and the config volume at the same container path). A standalone/patched deploy must **copy the base compose into the patched tree** so `extends:` resolves (see `patch-alerts.md` Patch 3).
- **Destination mapping selects outputs.** A processor whose worker count > 0 may produce several outputs. If a destination key is absent from the active sink's `topics` or `streams` list, the sink drops that output and logs `No destination configured for '<key>'; output for it is disabled` once; it does not crash. Define every desired destination carefully, because a typo now disables that stream. Disabling a processor (`numWorkersFor*=0`) also means its destinations need not be mapped — this is why the alerts config omits `behavior`/`embed*` destinations and the search config omits `incidents`/`frames`.
- **Mode is chosen by worker counts, not app swap.** `SearchAndAlertsApp` runs incident, behavior, and embed processors; deployments pick a mode by zeroing the others. Omitting a `numWorkersFor*` key defaults to `0` for that app (opt-in), so the config must explicitly enable the paths it wants and map every output it wants to retain.
- **Calibration determines sensor coverage.** With a typed calibration (`cartesian`/`geo`), frames from sensors not in the calibration are handled by the active calibration class; with `CalibrationI` (no file) all frames pass through with image-plane coordinates. Choose the calibration that matches the deployed sensors, or supply one at runtime via `mdx-notification`.
- **`category` must match `alert_type_config.json` (alerts path).** When Behavior Analytics feeds the Alert Microservice, an incident whose `category` has no `alert_type` entry is never VLM-verified (no prompt). Keep the emitted categories and the verifier config in sync.
- **Object-type casing.** Incident object-type knobs (e.g. `fovCountViolationIncidentObjectType`) are compared with exact `==` against the detector's emitted type. Match the detector label file casing (lowercase `person` for RT-DETR/GDINO), even where the code default or `stateManagementFilter` uses a different case.
- **Configured topics must be pre-created.** Behavior Analytics does not create Kafka topics; the ELK `kafka-topic-init-container` must create every mapped topic first.
- **Single config mount.** The command reads exactly one `--config` path; the bind target must be present at boot.

## Example Compose Snippet

The base (`deploy/docker/services/analytics/behavior-analytics/compose.yml`) plus the `extends`-based profile override. A standalone deploy patches a copy (never upstream); the `profiles:` placeholder is where Step 6.5 Patch 1 inserts the invented flag.

```yaml
# --- base (copied into the patched tree so `extends:` resolves) ---
services:
  vss-behavior-analytics-base:
    # Same expansion as the authoritative file, so an override of
    # VSS_CONTAINER_REGISTRY / VSS_BEHAVIOR_ANALYTICS_TAG still works.
    # Source of truth: deploy/docker/services/analytics/behavior-analytics/compose.yml
    image: ${VSS_BEHAVIOR_ANALYTICS_IMAGE:-${VSS_CONTAINER_REGISTRY:-ghcr.io/nvidia-ai-blueprints/vss}/vss-behavior-analytics}:${VSS_BEHAVIOR_ANALYTICS_TAG:-develop-latest}
    restart: always
    volumes:
      - $VSS_APPS_DIR/services/analytics/behavior-analytics/configs/vss-behavior-analytics-config.json:/resources/vss-behavior-analytics-config.json
    command: python3 apps/analytics/main_analytics_2d_app.py --config /resources/vss-behavior-analytics-config.json

# --- profile override (e.g. dev-profile-alerts) ---
  vss-behavior-analytics-alerts:
    extends:
      file: ${VSS_APPS_DIR}/services/analytics/behavior-analytics/compose.yml
      service: vss-behavior-analytics-base
    container_name: vss-behavior-analytics
    profiles:
      - <your-profile-flag>            # Step 6.5 Patch 1 inserts the invented flag (additive)
    volumes:
      - ${VSS_APPS_DIR}/developer-profiles/dev-profile-alerts/vss-behavior-analytics/configs/vss-behavior-analytics-config.json:/resources/vss-behavior-analytics-config.json
    command: python3 apps/search_and_alerts/main_search_and_alerts_app.py --config /resources/vss-behavior-analytics-config.json
```

## Schema Compatibility

The frame/behavior/incident protobufs (`nv.Frame` on `mdx-raw`/`mdx-frames`, `nv.Behavior` on `mdx-behavior`, `nv.Incident` on `mdx-incidents`, `nv.VisionLLM` on `mdx-embed`/`mdx-embed-filtered`) must align with the NvSchema descriptors at `deploy/docker/services/infra/elk/logstash/pb_definitions/descriptors/{schema.desc, ext.desc}` shared with Logstash and the CV producers. Drift between the upstream CV producer's frame schema and the Behavior Analytics consumer causes silently dropped frames; drift in the emitted `Incident.category` breaks the `alert_type_config.json` join downstream.

## Test / Smoke Hooks

- **Container up:** `docker ps --filter name=vss-behavior-analytics --format '{{.Status}}'` — expect `Up`. (No `/health` endpoint; readiness = the process consuming without crash-looping.)
- **Consuming input:** logs show `Batch N - <k> msgs fetched from source.` once the upstream detector produces to `mdx-raw`.
- **Producing incidents (alerts path):** after a triggering detection, incidents land in `mdx-incidents` → ES:
  ```bash
  curl -sf "http://${HOST_IP}:9200/mdx-incidents/_count" | jq '.count'
  ```
- **Producing enhanced frames:** `mdx-frames-*` ES index is populated (Logstash indexes the `mdx-frames` topic).
- **Producing filtered embeddings (search path):** logs show `Video embeddings: received=<a>, final=<b>` and `mdx-embed-filtered` is produced.
- **No accidentally disabled output:** inspect startup/batch logs for `No destination configured for '<key>'; output for it is disabled`. The warning is emitted once per missing key; no warning should name an output the deployment intends to retain.
