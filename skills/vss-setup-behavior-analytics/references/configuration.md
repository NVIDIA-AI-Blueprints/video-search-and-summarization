> See [`../SKILL.md`](../SKILL.md) for the overview.
# Configuration Guide

## Overview
Configurations are JSON files consumed by `AppConfig` (`video-search-and-summarization/services/analytics/behavior-analytics/src/mdx/analytics/core/schema/config.py`).

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
- `sourceType` / `sinkType`: typically "kafka" (also supports `redisStream`, `mqtt`)
- `spaceAnalyticsIntervalSec`: "5.0"
- Playback: `playbackLoop`, `playbackSensors`, `playbackInSimulationMode`, etc.
- Trajectory/space: `traj*`, `spaceAnalytics*`, see `video-search-and-summarization/services/analytics/behavior-analytics/src/mdx/analytics/core/schema/config.py` for full list.

## Common sensor keys (examples)
- `tripwireMinPoints`: "5"
- `sensorMinFrames`: "5"
- `anomalySpeedViolation`: JSON string, e.g. `{ "enable": true, "mphThreshold": 90, "timeIntervalSecThreshold": 5 }`
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
- Each type has its own `...Threshold` (duration in sec) and `...ExpirationWindow` (gap tolerance in sec); both default to `"1"`.
- FOV count uses two keys: `fovCountViolationIncidentObjectType` — the object **class** to count (e.g. `person`; must match the detector's label casing) — and `fovCountViolationIncidentObjectThreshold` — the **count** above which an incident fires.
- Details and timing: `video-search-and-summarization/services/analytics/behavior-analytics/docs/incident-detection.md`.

## Examples directory

Under `video-search-and-summarization/services/analytics/behavior-analytics/configs/`:

- `smart_city_config*.json`
- `warehouse_2d_config.json`
- `warehouse_3d_config.json`
- `public_safety_config.json`
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
- Keep values as strings; convert types in code.
- Use JSON strings for nested sensor configs; escape quotes.
- Prefer adding to `app` or sensor configs; avoid new top-level sections unless necessary.
- For env var use, set value to `$VARNAME` (supported for `in3dMode`).
