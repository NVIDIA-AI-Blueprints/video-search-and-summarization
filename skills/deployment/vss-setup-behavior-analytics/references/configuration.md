> See [`../SKILL.md`](../SKILL.md) for the overview.
# Configuration Guide

## Overview
Configurations are JSON files consumed by `AppConfig` (`services/analytics/behavior-analytics/src/mdx/analytics/core/schema/config.py`).

## Structure
```json
{
  "kafka": {...},
  "redisStream": {...},
  "mqtt": {...},
  "sensors": [...],
  "coordinateReferenceSystem": {...},
  "app": [...],
  "inference": {...}
}
```

## Priority
1. Sensor-specific overrides default sensor configs.
2. Default sensor configs override app-level defaults.

## Common app keys (examples)
- `in3dMode`: "false" (supports env var when value starts with `$`)
- `imageLocationMode`: "center" | "bottom_center" (for image coordinate system, determines which point from bbox is used to calculate location; default: "bottom_center")
- `roiEventDetectionMode`: "coordinate" | "bbox" (ROI ENTRY/EXIT test; "coordinate" [default] = object point inside ROI polygon, "bbox" = object bbox overlaps ROI polygon; "bbox" is image-calibration only, else falls back to coordinate)
- `behaviorMaxPoints`: "200"
- `behaviorEmitOnce`: "false" | "true" (default "false" = one behavior message per batch per track; "true" writes each behavior once instead, `behaviorStateValidInterval` seconds after the track goes quiet, so that key sets the latency. Tracks still live at shutdown are flushed. Events, anomalies and incidents are built from per-batch behaviors either way, so they are unaffected. Runtime-updatable)
- `sourceType` / `sinkType`: typically "kafka" (also supports `redisStream`, `mqtt`)
- `spaceAnalyticsIntervalSec`: "5.0"
- Playback: `playbackLoop`, `playbackSensors`, `playbackInSimulationMode`, etc.
- Trajectory/space: `traj*`, `spaceAnalytics*`, see `services/analytics/behavior-analytics/src/mdx/analytics/core/schema/config.py` for full list.

## Common sensor keys (examples)
- `tripwireMinPoints`: "5"
- `sensorMinFrames`: "5"
- `anomalySpeedViolation`: JSON string, e.g. `{ "enable": true, "mphThreshold": 90, "timeIntervalSecThreshold": 5 }`.
  **Units depend on the calibration.** `mphThreshold` is compared directly against the trajectory speed, which is
  mph only for **cartesian** coordinates — image coordinates are pixels, so the speed stays in **pixels/second** and
  the threshold is compared against a px/s number. Tune it per coordinate system; the same value does not mean the
  same thing in both. Same applies to `anomalyUnexpectedStop`'s `mphThreshold`.
- `proximityDetectionCenterClasses`: `["Forklift", "Person"]`
- Proximity detection: `proximityDetectionEnable`, `proximityDetectionThreshold`, `proximityDetectionSurroundingClasses`

## Minimal example
```json
{
  "kafka": {
    "brokers": "kafka:29092",
    "group": "my-app",
    "consumer": {"timeout": 0.1},
    "producer": {},
    "topics": [
      {"name": "raw", "value": "mdx-raw"},
      {"name": "behavior", "value": "mdx-behavior"}
    ]
  },
  "sensors": [{"id": "default", "configs": []}],
  "app": [
    {"name": "behaviorMaxPoints", "value": "200"},
    {"name": "in3dMode", "value": "false"}
  ]
}
```

> **Broker address.** `kafka.brokers` (and `redisStream.host` / `mqtt.host`) are **Docker DNS service names** (`kafka:29092`, `redis:6379`) resolved on the VSS compose bridge network — the shipped configs use these. For a **standalone** deploy against a broker that isn't on that network, set them to a reachable `host:port` instead (the DNS names won't resolve on a lone bridge network).

## Incidents & frame state
- All incident types (proximity, restricted area, confined area, FOV count) default to disabled (`...IncidentEnable = "false"`). Set the corresponding `...IncidentEnable = "true"` to turn them on.
- Each type has its own `...Threshold` (duration in sec, default `"1"`, min `0.0`) and `...ExpirationWindow` (gap
  tolerance in sec, default `"0.5"`, min `0.1`). Both accept **fractional** seconds — `"0.5"` is valid. The only
  incident knob that must stay a whole number is `fovCountViolationIncidentObjectThreshold`, which is an object count.
- FOV count uses two keys: `fovCountViolationIncidentObjectType` — the object **class** to count (e.g. `person`; must match the detector's label casing) — and `fovCountViolationIncidentObjectThreshold` — the **count at which** an incident fires. The comparison is `count >= threshold`, **not** `>` (`frame_state_management.py`: `if fov_metric.count < threshold: return`), so the default of `1` makes a single object a violation. To alert on "more than N", set it to `N + 1`.
- Details and timing: `services/analytics/behavior-analytics/docs/incident-detection.md`.

## Examples directory

Under `services/analytics/behavior-analytics/configs/`:

- `smart_city_config*.json`
- `warehouse_2d_config.json`
- `warehouse_3d_config.json`
- `public_safety_config.json`
- `composite_config.json` (every `numWorkersFor*` is `0` on purpose — set the ones you want or the app exits 0 at startup)
- `frame_playback_config.json`
- `rtls_amr_playback_config.json`

## Messaging blocks
- Kafka: brokers, group, topics under `kafka`.
- Redis Stream: host/port/db, streams, consumer/producer under `redisStream`.
- MQTT: host/port/clientId, topics, consumer/producer under `mqtt`.

## Other blocks
- CRS / road network: `coordinateReferenceSystem` (CRS, per-sensor origins, roadNetwork, mapMatching).
- Inference: `inference` (enable/url) for Triton.
- Space analytics / trajectory: `spaceAnalytics*`, `traj*`, `mapMatching*` keys.
- Playback: loop, sensors, simulation flags.

## Tips
- Every `app[]` / `sensors[].configs[]` value is a **string**, including numbers and booleans (`"200"`, `"false"`).
- Nested sensor configs are JSON encoded *inside* a string; escape the inner quotes.
- For env-var indirection, set the value to `$VARNAME` (supported for `in3dMode`).
- An invalid config is fatal: the app logs `FATAL - Config file ... contains invalid JSON` (or `has invalid
  structure`) and exits 1, so the container restart-loops rather than running degraded.
