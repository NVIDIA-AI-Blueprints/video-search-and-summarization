> Part of behavior-analytics docs. See `../README.md` for the project overview.

# MDX Core Modules Overview

Authoritative map of `src/mdx/analytics/core/` — what each subpackage is for, the
key files in it, the most important classes/functions, and **where to add
common kinds of new code**. Skills and agents should consult this doc before
reading source to locate a target file.

Apps live outside this tree at `apps/<name>/main_<name>_app.py`. Apps
compose pieces from `src/mdx/analytics/core/` — they don't own library code.

## Top-level tree

```
src/mdx/analytics/core/
├── app/           # BaseApp lifecycle, CLI runner, scheduler
├── inference/     # Triton inference client
├── schema/        # Pydantic config, domain models, protobuf, trajectory
├── stream/        # I/O: sources, sinks, state management
├── transform/     # Logic: calibration, detection, ROI/tripwire events
├── tools/         # Dev-only utilities (CLI scripts)
├── utils/         # Pure helpers (schema, distance, CRP/CRS, io, timing)
├── typings/       # Custom type stubs (e.g. confluent_kafka)
├── constants.py   # Module-level constants and small enums
└── logger_setup.py # Logging config loader, microsecond formatter
```

## app/ — Application lifecycle

Purpose: drive the run loop. Apps subclass `BaseApp`, register processors,
hand off to `app_runner.run()`.

| File | What it does |
|---|---|
| `app/app_base.py` | `BaseApp` ABC: source/sink wiring, `Processor` dataclass, hooks, graceful shutdown |
| `app/app_runner.py` | CLI entrypoint wrapper: parses `--config`/`--calibration`, sets up logging, calls `BaseApp.run()` |
| `app/app_args.py` | Shared argparse helpers |
| `app/scheduler/app_scheduler.py` | Worker loop; reads batches from source, dispatches to processors, emits `BatchStats` |
| `app/scheduler/app_scheduler_mp.py` | Multiprocessing variant of the scheduler |

**Add a new app** → `apps/<name>/main_<name>_app.py` (use the `new-app` skill).
Do **not** edit `BaseApp` unless the new app needs a lifecycle hook every app
would benefit from.

## schema/ — Config and domain models

Purpose: Pydantic v2 data shapes. Configs (runtime), models (domain), protobuf
(wire), trajectory (derived geometry).

| File | What it does |
|---|---|
| `schema/config.py` | `AppConfig` + subsection configs (KafkaConfig, RedisConfig, MqttConfig, SensorConfig, InferenceConfig, SpaceConfig, TrajectoryConfig, PlaybackConfig, VideoEmbeddingConfig, fall-risk/lack-movement/etc.). **This is where all app-configurable fields live.** |
| `schema/models.py` | Domain models: `Behavior`, `Event`, `Incident`, `Place`, `Location`, `Coordinate`, `Point2D`, `ROI`, `Tripwire`, `Line`, `IncidentCategory` (StrEnum), `AnalyticsModule`, `Action`, `AmrState`, `FrameState`, `RoiState` |
| `schema/proto/schema_pb2.py` | Generated protobuf — **off-limits** to edit |
| `schema/proto/ext_pb2.py` | Generated protobuf extensions — **off-limits** |
| `schema/trajectory/trajectory_base.py` | `TrajectoryBase` — shared trajectory interface |
| `schema/trajectory/trajectory.py` | Default `Trajectory` (haversine-based geodesic helpers) |
| `schema/trajectory/trajectory_e.py` | Euclidean trajectory variant |
| `schema/trajectory/trajectory_i.py` | Image-plane trajectory variant |
| `schema/action/action_state.py` | `ActionState` — per-object action tracking |
| `schema/collision/collision_state.py` | `CollisionState` — collision event state |

**Add a new config field** → `schema/config.py`. Always give a default.
**Add a new domain model** → `schema/models.py` (stay Pydantic v2).
**Change protobuf** → **stop** and flag; regeneration is a separate task.

## stream/ — I/O and state management

Purpose: read from and write to external brokers; maintain per-stream state
across frames.

### stream/source/ — ingress

| File | What it does |
|---|---|
| `source/source_base.py` | `Source` ABC (abstract poll/close) |
| `source/source_factory.py` | Factory: `make_source(type, config)` — dispatches on config |
| `source/source_kafka.py` | Confluent Kafka consumer |
| `source/source_redis_stream.py` | Redis Streams consumer (XREAD/XREADGROUP) |
| `source/source_mqtt.py` | Paho MQTT subscriber |

**Add a new source backend** → new `source_<name>.py`, register in
`source_factory.py`, add config section to `schema/config.py`.

### stream/sink/ — egress

| File | What it does |
|---|---|
| `sink/sink_base.py` | `Sink` ABC (abstract send/flush/close) |
| `sink/sink_factory.py` | Factory: `make_sink(type, config)` |
| `sink/sink_kafka.py` | Kafka producer |
| `sink/sink_redis_stream.py` | Redis Streams producer (XADD) |
| `sink/sink_mqtt.py` | MQTT publisher |

**Add a new sink backend** → mirror the source pattern above.

### stream/state/ — stateful processing

| File | What it does |
|---|---|
| `state/behavior/state_management_base.py` | Base for Euclidean/image-plane variants |
| `state/behavior/state_management_e.py` | Euclidean behavior state (`StateMgmtE`, `StateMgmtEWithTripwire`) |
| `state/behavior/state_management_i.py` | Image-plane behavior state |
| `state/behavior/state_management.py` | Shared helpers / composition |
| `state/frame/frame_state_management.py` | `FrameStateMgmt` — **all frame-level incident detection** (proximity, restricted-area, confined-area, FOV-count) |
| `state/amr/amr_state_management.py` | `AmrStateMgmt` — AMR (autonomous mobile robot) state |
| `state/video_embedding/video_embedding_state_mgmt.py` | Embedding aggregation for video search |
| `state/video_embedding/downsampling/downsampler_base.py` | Downsampler ABC |
| `state/video_embedding/downsampling/downsampler_sdt.py` | SDT (semantic distance threshold) downsampler |
| `state/video_embedding/downsampling/downsampler_window.py` | Fixed-window downsampler |

**Add a new incident type** → `state/frame/frame_state_management.py` (use the
`new-incident` skill).
**Add a new downsampler** → new `downsampler_<name>.py`, subclass
`EmbeddingDownsampler`, register where it's selected by config.

## transform/ — Logic

Purpose: stateless (or per-message) transforms and event emitters.

### transform/calibration/

| File | What it does |
|---|---|
| `calibration/calibration_base.py` | `CalibrationBase` ABC + `CalibrationFileMonitor` watchdog; hosts `reload_data` and `update_calibration_info` (upsert-all / upsert / delete merge) |
| `calibration/calibration.py` | Default 2D/geo calibration |
| `calibration/calibration_e.py` | Euclidean calibration (`CalibrationE`, `CalibrationES`) |
| `calibration/calibration_i.py` | Image-plane calibration |
| `calibration/calibration_dynamic.py` | One-time-switch wrapper used when the app starts with no `--calibration`; defines `CalibrationType` enum |
| `calibration/calibration_listener.py` | Kafka consumer thread that drains `mdx-notification` (key `calibration`), schema-validates each payload, and atomic-writes valid per-action JSON files |
| `calibration/calibration_validator.py` | Per-action JSON Schema gate (`upsert-all` / `upsert` full schema; `delete` minimal inline) raising `CalibrationValidationError` |
| `calibration/schemas/calibration.schema.json` | Vendored from `web-apis/web-api-core/schemas/ajv/calibration.json` — used by the validator |

**Add a new calibration variant** → subclass `CalibrationBase`, register where
selected by config.

### transform/detection/

| File | What it does |
|---|---|
| `detection/proximity_detection.py` | `ProximityDetection` — object-pair distance |
| `detection/collision_detection.py` | Collision prediction (trajectory extrapolation) |
| `detection/stop_detection.py` | Stop/idle detection |
| `detection/anomaly_action_detection.py` | Fall-risk / lack-of-movement via `Action` state |

**Add a new detection algorithm** → new file in `detection/`. Consumers wire it
into state management or an app's processor.

### transform/event/

| File | What it does |
|---|---|
| `event/base_event.py` | `BaseEvent` generic (Protocol + TypeVar) |
| `event/roi_event.py` | Region-of-interest enter/exit events |
| `event/tripwire_event.py` | Tripwire crossing events |

**Add a new event type** → subclass `BaseEvent`, follow ROI/tripwire patterns.

## inference/ — Triton client

| File | What it does |
|---|---|
| `inference/inference_client.py` | Triton gRPC `InferenceServerClient` wrapper: model load/infer, embedding post-processing |

Uses `InferenceConfig` from `schema/config.py`.

## tools/ — Dev-only CLI utilities

**The only subpackage allowed to use `print()`** (see CLAUDE.md).

| File | What it does |
|---|---|
| `tools/json_transformer.py` | Convert between JSON messages and protobuf frames |
| `tools/space_utilization.py` | Batch space-utilization analysis CLI |
| `tools/usd_search.py` | USD search utility |
| `tools/latency/latency_monitor.py` | Percentile-based latency monitor for stream topics |
| `tools/latency/chart_generator.py` | Render latency charts from monitor output |
| `tools/kml/kml_writer_base.py` | KML writer base |
| `tools/kml/kml_writer_e.py` | Euclidean KML writer |

Tools are **not** installed as part of the runtime library — they are scripts
invoked via `pipenv run python3 src/mdx/analytics/core/tools/<path>.py`.

## utils/ — Pure helpers

No side effects, no class hierarchies beyond simple data classes. Import-safe.

| File | What it does |
|---|---|
| `utils/schema_util.py` | Proto ↔ dict conversions, message grouping/filtering, timestamp helpers |
| `utils/distance_util.py` | Euclidean, haversine, geodesic distance; bearing; line intersection; unit conversions (MPS_TO_MPH etc.) |
| `utils/processing_stats.py` | `ProcessingStats`, `BatchStats` counters |
| `utils/io_utils.py` | Argparse file validators (`ValidateFile`), JSON loaders |
| `utils/util.py` | Small helpers: `cosine_similarity`, `dot_product`, `normalize`, `str_to_bool`, timestamp conversions |
| `utils/anomaly_util.py` | Map-matching anomaly helpers (uses `leuvenmapmatching`). **Uses camelCase for historical reasons — do not match in new files.** |
| `utils/crp.py` | Clustering result / prediction (CRP) — `Model` class |
| `utils/crs.py` | OSMnx coordinate reference / network helpers |
| `utils/genetic_algorithm.py` | GA for pallet placement |
| `utils/greedy_search.py` | Greedy search for pallet placement |
| `utils/space_utilization.py` | `SpaceAnalyzer` (consumed by `tools/space_utilization.py`) |

**Add a new pure helper** → pick the most-specific existing file; only create a
new `<topic>_util.py` if the helper doesn't fit any existing file.

## typings/ — Custom type stubs

| File | What it does |
|---|---|
| `typings/confluent_kafka/__init__.py` | Type stubs for confluent_kafka (untyped upstream) |

Referenced in `pyrightconfig.json` via `extraPaths`.

## Root files

| File | What it does |
|---|---|
| `constants.py` | Module-level constants (paths, date patterns, time conversions) and small enums (`TripwireDirection`, `ROIDirection`) |
| `logger_setup.py` | `MicrosecondFormatter`, JSON logging config loader |

## Cross-cutting patterns

- **Config-driven selection** — most factories (source/sink/calibration) pick an
  implementation from a string in config. If you add a variant, add the
  config enum and wire the factory.
- **Pydantic defaults** — every new config field must have a default. Tests
  construct `AppConfig()` without arguments.
- **Proto off-limits** — all `*_pb2.py` and `*_pb2.pyi` are generated. If the
  wire format needs to change, flag it; regeneration is a separate workflow.
- **One logger per module** — `logger = logging.getLogger(__name__)` at top.
  No `print()` outside `tools/`.

## Cross-references

- Configuration details: `readmes/configuration.md`
- Building an app: `readmes/building-mdx-analytics-app.md` (and the `new-app` skill)
- Incident detection: `readmes/incident-detection.md` (and the `new-incident` skill)
- Dev example walkthrough: `readmes/cursor-dev-example-walkthrough.md`
- Repo rules for agents: `../CLAUDE.md`
