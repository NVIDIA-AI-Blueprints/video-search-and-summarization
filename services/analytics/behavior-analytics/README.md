# VSS Behavior Analytics

This module contains a Python streaming pipeline for spatial AI analytics — warehouse, smart city, public safety, video search, and more.

## Introduction

`vss-behavior-analytics` consumes frame metadata from a message broker (Kafka, Redis Streams, or MQTT), processes it through configurable transforms (calibration, trajectory tracking, anomaly detection, ROI/tripwire events, incident detection), and emits behaviors, events, and incidents back to the broker for downstream consumption.

### Available Apps

The pipeline ships several ready-to-run apps under `apps/`:

- **analytics** — Analytics for warehouse / space-utilization workloads (`main_analytics_2d_app.py`, `main_analytics_3d_app.py`)
- **smart_city** — Smart-city analytics (`main_smart_city_app.py`)
- **public_safety** — Public safety detection (`main_public_safety_app.py`)
- **fusion_search** — Fusion search analytics for video-search workloads (`main_fusion_search_analytics_app.py`)
- **playback** — Replay frames / AMR / embeddings from JSON for testing (`playback_frames.py`, `playback_amr.py`, `playback_embed.py`, …)
- **dev_example** — Minimal app skeleton for video alerts (`main_dev_example_app.py`)

Each app subclasses `BaseApp`, composes core pieces in `__init__`, registers processors, and hands off to `app_runner.run(AppClass)`. Library code lives under `src/mdx/analytics/core/`.

### Configuration Schema

Configuration is JSON, loaded via the Pydantic `AppConfig` schema in [`src/mdx/analytics/core/schema/config.py`](./src/mdx/analytics/core/schema/config.py).

## Getting Started

### Dependencies

1. [Python](https://www.python.org/) version 3.13
2. [pipenv](https://pipenv.pypa.io/) — for the dev install (creates an editable venv automatically)
3. A message broker — one of:
   - Kafka (default for warehouse/smart-city profiles)
   - Redis Streams
   - MQTT

### Installation

```bash
python3 -m pip install --upgrade pip pipenv setuptools wheel
pipenv install --dev
```

`vss-behavior-analytics` is declared in `[dev-packages]` of the `Pipfile` as `{path = ".", editable = true}`, so `pipenv install --dev` installs it editable automatically. Package metadata lives in `pyproject.toml` (PEP 621).

### Running an App

Apps are launched via `pipenv run python3 apps/<name>/<entrypoint>.py [--config <path>] [--calibration <path>]`. Examples:

```bash
# Warehouse 2D
pipenv run python3 apps/analytics/main_analytics_2d_app.py \
  --config configs/warehouse_2d_config.json \
  --calibration configs/calibration_2d.json

# Playback (replay frames from JSON, useful for offline testing)
pipenv run python3 apps/playback/playback_frames.py --config configs/frame_playback_config.json
```

See [`docs/installation-and-usage.md`](./docs/installation-and-usage.md) for the full app list and additional install modes (NVIDIA registry, Docker).

## Configuration

The default configurations live under [`configs/`](./configs/) (one JSON per profile, e.g. `warehouse_2d_config.json`, `smart_city_config.json`, `public_safety_config.json`).

### Configuration Options

| Section | Option | Description |
|---------|--------|-------------|
| `kafka.brokers` | Broker list | Required when `sourceType`/`sinkType` is `kafka`; e.g. `"localhost:9092"` |
| `redisStream` | host / port / db | Required when source/sink is `redisStream` |
| `mqtt` | host / port / clientId | Required when source/sink is `mqtt` |
| `app[].sourceType` / `app[].sinkType` | `kafka` \| `redisStream` \| `mqtt` | Stream broker selection |
| `app[].coordinateSystem` | `image` \| `euclidean` \| `geo` | Coordinate system for spatial analytics |
| `app[].behaviorMaxPoints` | int | Trajectory point cap per behavior. Default: `200` |
| `app[].spaceAnalyticsIntervalSec` | float | Space-analytics emission interval (seconds). Default: `5.0` |
| `sensors[].id` | string | Sensor identifier; `default` matches all sensors |
| `app[].*IncidentEnable` | `"true"` \| `"false"` | Incident toggles (proximity, restricted area, confined area, FOV count). Default: `"false"` |

**Note**: If any change needs to be made, it is recommended to create a copy of the config file and make changes so that the default-config is preserved.

For the full schema and dynamic-config behavior (runtime updates via Flask endpoint), see [`docs/configuration.md`](./docs/configuration.md) and [`docs/dynamic-config.md`](./docs/dynamic-config.md).

## Documentation

- [Installation and Usage](./docs/installation-and-usage.md)
- [Configuration Guide](./docs/configuration.md)
- [Docker and Deployment](./docs/docker.md)
- [Modules Overview](./docs/modules-overview.md)
- [Testing Guide](./docs/testing.md)
- [Troubleshooting](./docs/troubleshooting.md)
- [Building an Analytics App](./docs/building-mdx-analytics-app.md)
- [Incident Detection](./docs/incident-detection.md)
- [Dynamic Configuration](./docs/dynamic-config.md)
- [Dynamic Calibration](./docs/dynamic-calibration.md)
