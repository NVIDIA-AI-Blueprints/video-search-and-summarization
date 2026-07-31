> Part of behavior-analytics docs. See `../README.md` for the overview.
# Configuration Guide

## Overview
Configurations are JSON files consumed by `AppConfig` (`src/mdx/analytics/core/schema/config.py`).

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
- `roiEventDetectionMode`: "coordinate" | "bbox" (ROI ENTRY/EXIT detection; "coordinate" [default] checks whether the object's coordinate is inside the ROI polygon, "bbox" checks whether the object's bounding box overlaps the ROI polygon). "bbox" is supported only for **image** calibration, where `object.bbox` and the ROI polygon share image-pixel coordinates. For **cartesian** and **geo** calibration it falls back to the coordinate-inside check with a one-time warning, since `object.bbox` is in image pixels while the ROI/trajectory are in world units.
- `behaviorMaxPoints`: "200"
- `behaviorEmitOnce`: "false" (see [Behavior emission](#behavior-emission))
- `sourceType` / `sinkType`: typically "kafka" (also supports `redisStream`, `mqtt`)
- `spaceAnalyticsIntervalSec`: "5.0"
- Playback: `playbackLoop`, `playbackSensors`, `playbackInSimulationMode`, etc.
- Trajectory/space: `traj*`, `spaceAnalytics*`, see `config.py` for full list.

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
    "brokers": "localhost:9092",
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

## Behavior emission
`behaviorEmitOnce` (default `"false"`) switches the behavior stream from one message per batch to one
per track. With it on, a track's behavior is written once, `behaviorStateValidInterval` seconds after
its last message — so that key sets the latency — and tracks still live when the app stops are flushed.

Events, anomalies and incidents are built from per-batch behaviors either way, so they are unaffected.

The key is runtime-updatable; switching it off hands over anything still being held back.

## Incidents & frame state
- All incident types (proximity, restricted area, confined area, FOV count) default to disabled (`...IncidentEnable = "false"`). Set the corresponding `...IncidentEnable = "true"` to turn them on.
- Each type has its own `...Threshold` (duration in sec) and `...ExpirationWindow` (gap tolerance in sec); both default to `"1"`.
- FOV count uses two extra keys: `fovCountViolationIncidentObjectThreshold` — the numeric count threshold (default `"1"`) — and `fovCountViolationIncidentObjectType` — the object type being counted (default `"Person"`).
- Details and timing: `docs/incident-detection.md`.

## Examples directory
- `configs/smart_city_config*.json`
- `configs/warehouse_2d_config.json`
- `configs/warehouse_3d_config.json`
- `configs/public_safety_config.json`
- `configs/frame_playback_config.json`

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
