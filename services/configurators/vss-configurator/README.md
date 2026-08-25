# VSS Configurator

A unified configuration management service for VSS Blueprints that handles both sensor/camera configurations and hardware-specific profile configurations.

## Table of Contents

- [Overview](#overview)
- [Repository structure](#repository-structure)
- [Startup behavior](#startup-behavior)
- [Architecture](#architecture)
- [Components](#components)
  - [Sensor Configuration Manager](#sensor-configuration-manager)
  - [Profile Configurator](#profile-configurator)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Deployment](#deployment)
- [Usage Examples](#usage-examples)
- [Troubleshooting](#troubleshooting)
- [Dependencies](#dependencies)
- [Running tests](#running-tests)

---

## Overview

The VSS Configurator is a multi-purpose Flask-based service that provides:

1. **Sensor Configuration Management**: Manages camera/sensor configurations, calibration data, and sensor mappings for video analytics pipelines
2. **Profile Configuration**: Automatically configures application settings based on detected hardware profiles (GPU types) and deployment modes (2D/3D)

The service runs as a containerized application using Gunicorn as the WSGI server and supports multiple message brokers (Kafka, Redis) for event streaming.

---

## Repository structure

```
vss-configurator/
├── 3rdParty_Licenses.md                      # Full third-party license text; copied into image at build
├── pyproject.toml                            # Python project metadata and runtime dependencies
├── uv.lock                                   # Locked Python dependency graph
├── app/                                      # Runtime source copied into /usr/src/app
│   ├── entrypoint.py                         # Distroless entrypoint (default CMD)
│   ├── sensor_config_manager.py              # Flask app and REST API
│   ├── gunicorn_config.py                    # Gunicorn worker hooks
│   ├── profile_configurator/                 # GPU profile file operations
│   └── utils/                                # Kafka/Redis, uploads, sensor mapping
├── tests/                                    # pytest unit tests and fixtures
├── docker/
│   └── Dockerfile                            # Container build recipe (monorepo-root context)
```

Python dependencies are managed with **uv** in this service directory (`requires-python >= 3.13`). The `spatialai-data-utils` dependency is built from the monorepo source under `libs/analytics/spatialai-data-utils`. The runtime image is **distroless** (no shell); startup uses `entrypoint.py`.

---

## Startup behavior

Container startup is driven by `app/entrypoint.py` (see `docker/Dockerfile` `CMD`):

| Step | Condition | Action |
|------|-----------|--------|
| 1 | `ENABLE_PROFILE_CONFIGURATOR=true` | Run `profile_configurator/profile_config_manager.py` once; exit on failure |
| 2 | `ENABLE_SENSOR_CONFIGURATOR=true` (default) | `exec` Gunicorn with `sensor_config_manager:app` on `PORT` (default `5000`) |
| 3 | `ENABLE_SENSOR_CONFIGURATOR=false` | Exit after profile step (profile-only init container mode) |

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_PROFILE_CONFIGURATOR` | `false` | Run GPU profile file operations at startup |
| `ENABLE_SENSOR_CONFIGURATOR` | `true` | Start the Flask/Gunicorn sensor configuration API |

When profile configuration is enabled, it writes a readiness marker (see `PROFILE_CONFIG_READY_FILE`); the `/readyz` endpoint checks this file before reporting ready.

Each Gunicorn worker runs a **background thread** (`gunicorn_config.post_worker_init`) that loads sensor/calibration data and builds the sensor mapping. `gunicorn_config` also defines `worker_init` (called after fork, before `post_worker_init`) and `pre_fork` hooks for process lifecycle management.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   VSS Configurator                           │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────┐    ┌──────────────────────────┐   │
│  │ Profile Configurator│    │ Sensor Configuration Mgr │   │
│  │  (Init-time only)   │    │   (Background Thread)     │   │
│  ├─────────────────────┤    ├──────────────────────────┤   │
│  │ • GPU Detection     │    │ • Calibration Mgmt       │   │
│  │ • File Operations   │    │ • Sensor Mapping         │   │
│  │ • Config Generation │    │ • API Endpoints          │   │
│  │ • Variable Eval     │    │ • Message Broker         │   │
│  └─────────────────────┘    │ • VMS Integration        │   │
│                              └──────────────────────────┘   │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Gunicorn WSGI Server                     │   │
│  │  (Multi-worker process management)                    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
           │                           │
           ▼                           ▼
    External APIs              Kafka/Redis Streams
```

### Key Features

- **Dual-mode operation**: Profile configuration + sensor management in a single container
- **Hardware-aware configuration**: Automatically detects GPU type and adjusts settings
- **Multiple calibration sources**: Support for API fetch, file upload, and volume mount modes
- **Flexible message brokers**: Kafka or Redis for sensor event streaming
- **Background processing**: Continuous sensor mapping updates via daemon threads
- **REST API**: Full-featured API for calibration management and sensor queries
- **Advanced expression evaluation**: Support for mathematical expressions, comparisons, and ternary operators in configuration variables

---

## Components

### Sensor Configuration Manager

The Sensor Configuration Manager manages sensor/camera configurations for video analytics deployments.

#### Features

- **Calibration Management**
  - **Fetch Mode** (default): Periodically retrieves calibration data from a configured API endpoint
  - **Upload Mode**: Accepts calibration data via POST requests
  - **Mount Mode**: Reads calibration from a mounted volume
  
- **Sensor Mapping**
  - Generates mappings between sensor IDs, names, URLs, and metadata
  - Persists mappings to disk for reliability
  - Supports sensor sources: Metropolis Sensor Bridge (`msb`), NVStreamer (`nvstreamer`), or local JSON (`file`)
  
- **VMS Integration**
  - Automatically registers sensors with Video Management Systems
  - Stream validation and health checking
  - Configurable retry logic for robust operation
  
- **Message Broker Integration**
  - Publishes sensor configuration events to Kafka or Redis
  - Redis event duplication for multi-consumer scenarios
  - Configurable topics and message keys

#### Workflow

```
┌──────────────┐
│ Sensor Info  │  (from MSB or NVStreamer)
└──────┬───────┘
       │
       ▼
┌──────────────────┐
│ Calibration Data │  (from API, upload, or mount)
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ Generate Sensor  │
│     Mapping      │
└──────┬───────────┘
       │
       ├─────────────────┐
       │                 │
       ▼                 ▼
┌──────────────┐   ┌──────────────┐
│ Publish to   │   │ Register with│
│ Kafka/Redis  │   │     VMS      │
└──────────────┘   └──────────────┘
```

---

### Profile Configurator

The Profile Configurator automatically adjusts application configurations based on the detected hardware profile and deployment mode.

#### Features

- **Hardware Detection**
  - Automatically detects GPU type (L4, A100, H100, etc.)
  - Reads from `HARDWARE_PROFILE` environment variable or hardware detection utilities
  
- **Deployment Modes**
  - **2D mode**: Optimized for 2D analytics (crowd counting, heatmaps)
  - **3D mode**: Optimized for 3D analytics (pose estimation, depth)
  
- **Configuration operations** (`operation_type` in profile YAML)
  - `yaml_update` — patch YAML files (`target_file`, `updates`, optional `backup`)
  - `json_update` — patch JSON with format preservation (`write_json_preserving`)
  - `text_config_update` — key/value updates in text config files
  - `text_replace` — pattern-based text replacement
  - `file_management` — file counts and keep-count cleanup (prerequisites)
  - **Variable computation**: Math expressions, ternary operators, comparisons
  - **Variable validation**: Rules with `allowed_values`, patterns, and conditions
  - **Backup creation**: Automatic timestamped backups before mutating files
  
- **Advanced Expression Evaluation**
  - Mathematical operations: `+`, `-`, `*`, `/`, `//`, `%`, `**`
  - Comparison operators: `>`, `<`, `>=`, `<=`, `==`, `!=`
  - Boolean operators: `and`, `or`, `not`
  - Ternary expressions: `value_if_true if condition else value_if_false`
  - Built-in functions: `min()`, `max()`, `abs()`, `round()`, `int()`, `float()`

#### Configuration File Structure

```yaml
# Example: gpu_configs_generic.yaml (under profile_configurator/ or PROFILE_CONFIG_FILE)

commons:
  variables:
    3d:
      - num_cameras: "8"
  file_operations:
    3d:
      - operation_type: json_update
        target_file: /usr/src/app/config/app.json
        backup: true
        updates:
          app.mode: "${common_mode}"

L4:
  3d:
    max_streams_supported: 8
    variables:
      - batch_size: "4 if max_streams_supported > 5 else 2"
      - stream_density: "min(num_cameras, max_streams_supported)"
    file_operations:
      - operation_type: yaml_update
        target_file: /usr/src/app/config/pipeline.yaml
        backup: true
        updates:
          streammux.batch-size: "${batch_size}"

H100:
  3d:
    max_streams_supported: 64
    variables:
      - batch_size: "16"
```

Profile YAML supports `commons` sections keyed by deployment mode (`2d` / `3d`) when `DEPLOYMENT_MODES_ENABLED=true`, or a mode-less layout when disabled. See `tests/fixtures/gpu_configs_minimal.yaml` for a minimal example.

#### Workflow

```
Container Start
      │
      ▼
┌─────────────────┐
│ Detect Hardware │  (HARDWARE_PROFILE env or detection)
│   Profile (GPU) │
└─────┬───────────┘
      │
      ▼
┌─────────────────┐
│ Determine Mode  │  (MODE env: 2d or 3d)
└─────┬───────────┘
      │
      ▼
┌─────────────────┐
│ Load Profile    │  (from gpu_configs_generic.yaml)
│  Configuration  │
└─────┬───────────┘
      │
      ▼
┌─────────────────┐
│ Execute         │
│ Prerequisites   │  (file counts, setup operations)
└─────┬───────────┘
      │
      ▼
┌─────────────────┐
│ Process & Eval  │
│   Variables     │  (math, ternary, comparisons)
└─────┬───────────┘
      │
      ▼
┌─────────────────┐
│ Execute File    │  (copy, modify, create, delete)
│   Operations    │
└─────┬───────────┘
      │
      ▼
┌─────────────────┐
│ Write readiness │  (PROFILE_CONFIG_READY_FILE)
│     marker      │
└─────┬───────────┘
      │
      ▼
┌─────────────────┐
│ Start Gunicorn  │  (if ENABLE_SENSOR_CONFIGURATOR=true)
│  Service Server │
└─────────────────┘
```

---

## Quick Start

### Build the container image

Build from the **monorepo root** so the Dockerfile can copy the service files and the `spatialai-data-utils` source:

```bash
docker build \
  -f services/configurators/vss-configurator/docker/Dockerfile \
  -t vss-configurator .
```

The image uses a multi-stage build: **Python 3.13** dependencies, including the in-repo SDU package, via `uv sync --frozen --no-dev`, runtime on **`nvcr.io/nvidian/distroless/python:3.13-v4.0.5`**.

**Legal requirements (container distribution):**

| # | Requirement | How it is met |
|---|-------------|----------------|
| 1 | NVIDIA application **source** in the image stays **Apache-2.0** (SPDX headers; no proprietary re-license) | All shipped `.py` under `app/` (excluding `tests/`) include `SPDX-License-Identifier: Apache-2.0` and the standard NVIDIA Apache header block |
| 2 | **Third-party license text** is **in the shipped image** | `services/configurators/vss-configurator/3rdParty_Licenses.md` is copied into `/usr/src/app/3rdParty_Licenses.md` via `docker/Dockerfile` |
| 3 | **NVIDIA Software License Agreement** is **in the shipped image** | The Dockerfile downloads `NVIDIA-Software-License-Agreement-2026.5.7.pdf` from `nvidia.com` at build time with a pinned SHA-256 checksum and places it at `/usr/src/app/NVIDIA-Software-License-Agreement.pdf` |

Build before release:

```bash
docker build \
  -f services/configurators/vss-configurator/docker/Dockerfile \
  -t vss-configurator .
```

| In image? | Path | Notes |
|-----------|------|--------|
| Yes | `/usr/src/app/**/*.py` (app code only) | Apache-2.0 SPDX headers |
| Yes | `/usr/src/app/3rdParty_Licenses.md` | Full third-party license text |
| Yes | `/usr/src/app/NVIDIA-Software-License-Agreement.pdf` | NVIDIA Software License Agreement downloaded at build time with a pinned SHA-256 checksum |

### Run: sensor API only

```bash
docker run -d \
  --name vss-configurator \
  -p 5000:5000 \
  -e ENABLE_SENSOR_CONFIGURATOR=true \
  -e CALIBRATION_MODE=fetch \
  -e CALIBRATION_API_ENDPOINT=http://config-api:8080/calibration \
  -e SENSOR_INFO_SOURCE=msb \
  -e SENSOR_BRIDGE_HTTP_ENDPOINT=http://<sensor-bridge-host>:8000/mtmc/urls \
  -e MESSAGE_BROKER_TYPE=kafka \
  -e WDM_KFK_BOOTSTRAP_URL=kafka:9092 \
  -e WDM_KFK_TOPIC=sensor.config \
  vss-configurator
```

### Run: profile init + sensor API

```bash
docker run -d \
  --name vss-configurator \
  -p 5000:5000 \
  -e ENABLE_PROFILE_CONFIGURATOR=true \
  -e ENABLE_SENSOR_CONFIGURATOR=true \
  -e HARDWARE_PROFILE=H100 \
  -e MODE=3d \
  -e CALIBRATION_MODE=mount \
  -v /path/to/gpu_configs.yaml:/usr/src/app/profile_configurator/gpu_configs_generic.yaml \
  -v /path/to/calibration:/usr/src/app/calibration_store \
  vss-configurator
```

### Run: profile-only init (no HTTP server)

```bash
docker run --rm \
  -e ENABLE_PROFILE_CONFIGURATOR=true \
  -e ENABLE_SENSOR_CONFIGURATOR=false \
  -e HARDWARE_PROFILE=L4 \
  -e MODE=3d \
  -v /path/to/config:/usr/src/app/config \
  vss-configurator
```

### Using Docker Compose

Use the monorepo root as the Compose build context:

```yaml
version: '3.8'

services:
  vss-configurator:
    build:
      context: .
      dockerfile: services/configurators/vss-configurator/docker/Dockerfile
    ports:
      - "5000:5000"
    environment:
      # Startup
      ENABLE_PROFILE_CONFIGURATOR: "true"
      ENABLE_SENSOR_CONFIGURATOR: "true"
      HARDWARE_PROFILE: "L4"
      MODE: "3d"
      
      # Sensor Configuration
      CALIBRATION_MODE: "fetch"
      CALIBRATION_API_ENDPOINT: "http://config-api:8080/calibration"
      SENSOR_INFO_SOURCE: "msb"
      SENSOR_BRIDGE_HTTP_ENDPOINT: "http://sensor-bridge:8000/mtmc/urls"
      
      # Message Broker
      MESSAGE_BROKER_TYPE: "kafka"
      WDM_KFK_BOOTSTRAP_URL: "kafka:9092"
      WDM_KFK_TOPIC: "sensor.config"
      
      # VMS Integration
      CALL_SENSOR_ADD_API: "true"
      VST_CAMERA_ADD_ENDPOINT: "http://vms:30000/api/v1/sensor/add"
      
    volumes:
      - ./config:/usr/src/app/config
      - ./calibration:/usr/src/app/calibration_store
      - ./gpu_configs_generic.yaml:/usr/src/app/profile_configurator/gpu_configs_generic.yaml
    depends_on:
      - kafka
      - sensor-bridge
```

---

## Configuration

### Core Environment Variables

#### Startup and profile configurator

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_PROFILE_CONFIGURATOR` | `false` | Run profile configurator once at container start |
| `ENABLE_SENSOR_CONFIGURATOR` | `true` | Start Gunicorn/Flask sensor API (set `false` for profile-only jobs) |
| `DEPLOYMENT_MODES_ENABLED` | `true` | When `true`, use `MODE` (2d/3d) for config and commons; when `false`, mode-less layout |
| `HARDWARE_PROFILE` | `default` | Hardware profile key in profile YAML (e.g., `L4`, `H100`) |
| `MODE` | `3d` | Deployment mode: `2d` or `3d` (when `DEPLOYMENT_MODES_ENABLED=true`) |
| `PROFILE_CONFIG_FILE` | `profile_configurator/gpu_configs_generic.yaml` | Path to profile YAML (absolute or under `/usr/src/app`) |
| `PROFILE_CONFIG_READY_FILE` | `/tmp/profile_config_ready` | Marker file; `/readyz` checks this when profile configurator is enabled |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARN`, `ERROR` |

#### Sensor Configuration Settings

| Variable | Default | Description |
|----------|---------|-------------|
| **Calibration Configuration** | | |
| `ENABLE_CALIBRATION_PROCESS` | `true` | Enable calibration processing |
| `CALIBRATION_MODE` | `fetch` | Calibration source: `fetch`, `upload`, or `mount` |
| `CALIBRATION_API_ENDPOINT` | `` | API endpoint for fetching calibration (required for `fetch` mode) |
| `CALIBRATION_API_TIMEOUT` | `30` | API request timeout in seconds |
| `GET_CALIBRATION_DELAY` | `30` | Delay between calibration fetch attempts (seconds) |
| `CALIBRATION_DIR_MOUNT_PATH` | `/usr/src/app/calibration_store` | Directory for calibration files |
| `CALIBRATION_FILE_NAME` | `calibration.json` | Name of the calibration file |
| **Sensor Configuration** | | |
| `SENSOR_INFO_SOURCE` | `msb` | Sensor source: `msb`, `nvstreamer`, `file`, or `not_required` (skips all sensor processing) |
| `SENSOR_FILE_PATH` | `{CALIBRATION_DIR}/sensors.json` | Sensor JSON when `SENSOR_INFO_SOURCE=file` |
| `SENSOR_BRIDGE_HTTP_ENDPOINT` | `http://localhost:8000/mtmc/urls` | MSB sensor bridge HTTP endpoint |
| `RECOMPUTE_BEV_CENTERS_ENABLED` | `false` | Recompute BEV group origins via `spatialai_data_utils` (3D mode only) |
| `NVSTREAMER_STREAMS_ENDPOINT` | `http://localhost:30000/api/v1/live/streams` | NVStreamer streams endpoint |
| `NVSTREAMER_SENSOR_STATUS_ENDPOINT` | `http://localhost:30000/api/v1/sensor/status` | NVStreamer status endpoint |
| `NUM_STREAMS` | `0` | Expected NVStreamer camera count; wait until the stream list reaches this size before registering with VST (`0` = wait for a stable non-empty list instead) |
| `NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT` | `120` | Seconds to keep waiting after NVStreamer returns a non-empty list that is still below `NUM_STREAMS` (or not yet stable when `NUM_STREAMS=0`). Then the partial list is used. Unreachable / non-200 / empty responses retry indefinitely |
| `NVSTREAMER_STREAM_VALIDATION_MAX_RETRIES` | `50` | Max retries for stream validation |
| `NVSTREAMER_STREAM_VALIDATION_RETRY_DELAY` | `5` | Delay between validation retries (seconds) |
| **Video upload (NVStreamer / VMS)** | | |
| `ENABLE_NVSTREAMER_VIDEO_UPLOAD` | `false` | Upload videos to NVStreamer from `VIDEO_SOURCE_DIR` |
| `ENABLE_VMS_VIDEO_UPLOAD` | `false` | Upload videos to VMS storage API (PUT binary to `/vst/api/v1/storage/file/{file_name}/{timestamp}`) |
| `VIDEO_SOURCE_DIR` | `` | Directory containing .mp4/.mkv files to upload (all videos in the dir are uploaded) |
| `VIDEO_UPLOAD_TIMEOUT` | `300` | Request timeout in seconds for each upload |
| `VIDEO_UPLOAD_DELAY` | `0` | Delay in seconds between subsequent video uploads (0 = no delay) |
| `NVSTREAMER_UPLOAD_BASE_URL` | `http://localhost:30000` | NVStreamer base URL (path `/api/v1/storage/file` is appended for uploads) |
| `VMS_UPLOAD_BASE_URL` | `http://localhost:30888` | VMS base URL (e.g. `http://<VMS_HOST>:<PORT>`) for storage uploads |
| **VMS Integration** | | |
| `CALL_SENSOR_ADD_API` | `true` | Enable sensor registration with VMS |
| `VST_CAMERA_ADD_ENDPOINT` | `http://vms-vms-svc:30000/api/v1/sensor/add` | VMS camera registration endpoint |
| **Message Broker Configuration** | | |
| `MESSAGE_BROKER_TYPE` | `kafka` | Message broker type: `kafka` or `redis` |
| `SEND_CONFIG_TO_SDR` | `true` | Send sensor configuration events to message broker |
| **Kafka Settings** | | |
| `WDM_KFK_BOOTSTRAP_URL` | `` | Kafka bootstrap servers |
| `WDM_KFK_TOPIC` | `` | Kafka topic for sensor config events |
| `WDM_KFK_MSG_KEY` | `sensor` | Kafka message key |
| `WDM_WL_ID_FIELD` | `camera_id` | Sensor ID field name in messages |
| `WDM_WL_EVENT_FIELD` | `event` | Event field name in messages |
| **Redis Settings** | | |
| `WDM_REDIS_HOST` | `localhost` | Redis server host |
| `WDM_REDIS_PORT` | `6379` | Redis server port |
| `WDM_REDIS_STREAM_NAME` | `sensor` | Redis stream name for sensor events |
| `WDM_REDIS_MSG_KEY` | `sensor.id` | Redis message key field |
| **Redis Event Duplication** | | |
| `ENABLE_REDIS_DUPLICATOR_THREAD` | `false` | Enable Redis event duplication |
| `REDIS_DB` | `0` | Redis database number |
| `REDIS_SOURCE_TOPIC` | `vst.event` | Source topic for event duplication |
| `REDIS_TARGET_TOPIC_CV` | `vst.event.cv` | Target topic for CV events |
| `REDIS_TARGET_TOPIC_PN26` | `vst.event.pn26` | Target topic for PN26 events |
| **Naming Configuration** | | |
| `PN_SUFFIX` | `` | Suffix for PN sensor names |
| `CV_SUFFIX` | `-cv` | Suffix for CV sensor names |
| **Application Configuration** | | |
| `PORT` | `5000` | Service port |

---

## API Reference

### Sensor Configuration API Endpoints

#### POST `/calibration`

Upload calibration data (upload mode) or receive warning (fetch mode).

**Request Body:**
```json
{
  "sensors": [...],
  "calibrationType": "intrinsic_extrinsic",
  "calibration_data": {...}
}
```

**Response (Upload Mode):**
```json
{
  "status": "success",
  "message": "Calibration File added"
}
```

**Response (Fetch Mode):**
```json
{
  "status": "warning",
  "message": "Calibration mode is set to 'fetch'. Data will be fetched from http://api.example.com/calibration. Upload ignored."
}
```

**Status Codes:** `200 OK`, `400 Bad Request`, `503 Service Unavailable`

---

#### GET `/download`

Download the current calibration file.

**Response:** Binary file download (`calibration.json`)

**Status Codes:** `200 OK`, `404 Not Found`, `500 Internal Server Error`, `503 Service Unavailable`

---

#### GET `/cameras`

Get list of configured sensor/camera names (available when background sensor mapping has completed).

**Note:** Does not require `ENABLE_CALIBRATION_PROCESS`; returns `503` until `sensor_mapping` is initialized.

**Response:**
```json
[
  "camera-001",
  "camera-002",
  "sensor-north-01"
]
```

**Status Codes:** `200 OK`, `503 Service Unavailable`, `500 Internal Server Error`

---

#### GET `/groups`

Get list of all sensor group names. Requires `ENABLE_CALIBRATION_PROCESS=true`; returns `503` when calibration processing is disabled.

**Response:**
```json
[
  "north-entrance",
  "south-parking",
  "main-lobby"
]
```

**Status Codes:** `200 OK`, `503 Service Unavailable`, `500 Internal Server Error`

---

#### GET `/healthz`

Liveness probe — process is up.

**Response:** `{"status": "healthy"}`

**Status Codes:** `200 OK`

---

#### GET `/readyz`

Readiness probe — profile configuration complete when `ENABLE_PROFILE_CONFIGURATOR=true`.

| Condition | Response | Status |
|-----------|----------|--------|
| Profile configurator disabled | `{"status": "ready", ...}` | `200` |
| Enabled and `PROFILE_CONFIG_READY_FILE` exists | `{"status": "ready", ...}` | `200` |
| Enabled and marker missing | `{"status": "not_ready", ...}` | `503` |

Use this endpoint (not `/healthz`) for Kubernetes readiness when profile init runs in the same pod.

---

#### GET `/video-upload-status`

Status for NVStreamer/VMS video upload (for init-container polling).

| Status | HTTP | Meaning |
|--------|------|---------|
| `disabled` | `404` | Neither `ENABLE_NVSTREAMER_VIDEO_UPLOAD` nor `ENABLE_VMS_VIDEO_UPLOAD` is true |
| `completed` | `200` | All uploads finished (`ready: true`) |
| `in_progress`, `not_started` | `503` | Still uploading |
| `failed` | `503` | Upload error (see `error` field) |

---

## Deployment

### Container image

| Item | Value |
|------|--------|
| Build context | monorepo root (`.`) |
| Builder | `python:3.13-trixie` + `uv sync --frozen --no-dev` |
| Runtime base | `nvcr.io/nvidian/distroless/python:3.13-v4.0.5` |
| Entrypoint | `python entrypoint.py` (no shell in image) |
| Working directory | `/usr/src/app` |
| Python deps | `PYTHONPATH=/usr/src/app/site-packages` |
| Port | `5000` (`PORT` env) |
| WSGI | Gunicorn; workers start sensor background thread via `gunicorn_config.py` |

The default image is distroless and starts through `app/entrypoint.py`.

### Volume mounts

```bash
# Calibration persistence
-v /host/calibration:/usr/src/app/calibration_store

# Application configs patched by profile configurator
-v /host/config:/usr/src/app/config

# Profile YAML
-v /host/gpu_configs.yaml:/usr/src/app/profile_configurator/gpu_configs_generic.yaml

# Video upload source (when ENABLE_*_VIDEO_UPLOAD=true)
-v /host/videos:/data/videos
```

### Health checks

The distroless image has **no `curl` or shell**. Use HTTP probes from the orchestrator:

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 5000
  initialDelaySeconds: 30
  periodSeconds: 30
readinessProbe:
  httpGet:
    path: /readyz    # use /healthz if ENABLE_PROFILE_CONFIGURATOR=false
    port: 5000
  initialDelaySeconds: 10
  periodSeconds: 10
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vss-configurator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vss-configurator
  template:
    metadata:
      labels:
        app: vss-configurator
    spec:
      containers:
      - name: vss-configurator
        image: vss-configurator:latest
        ports:
        - containerPort: 5000
        env:
        - name: ENABLE_PROFILE_CONFIGURATOR
          value: "true"
        - name: HARDWARE_PROFILE
          value: "H100"
        - name: MODE
          value: "3d"
        - name: CALIBRATION_MODE
          value: "fetch"
        - name: CALIBRATION_API_ENDPOINT
          value: "http://config-api:8080/calibration"
        - name: MESSAGE_BROKER_TYPE
          value: "kafka"
        - name: WDM_KFK_BOOTSTRAP_URL
          value: "kafka:9092"
        - name: ENABLE_SENSOR_CONFIGURATOR
          value: "true"
        volumeMounts:
        - name: calibration
          mountPath: /usr/src/app/calibration_store
        - name: config
          mountPath: /usr/src/app/config
        livenessProbe:
          httpGet:
            path: /healthz
            port: 5000
          initialDelaySeconds: 30
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /readyz
            port: 5000
          initialDelaySeconds: 10
          periodSeconds: 10
      volumes:
      - name: calibration
        persistentVolumeClaim:
          claimName: calibration-pvc
      - name: config
        configMap:
          name: app-config
```

---

## Usage Examples

### Example 1: Basic Sensor Configuration with Fetch Mode

```bash
docker run -d \
  --name vss-configurator \
  -p 5000:5000 \
  -e CALIBRATION_MODE=fetch \
  -e CALIBRATION_API_ENDPOINT=http://config-server:8080/api/calibration \
  -e SENSOR_INFO_SOURCE=msb \
  -e SENSOR_BRIDGE_HTTP_ENDPOINT=http://sensor-bridge:8000/mtmc/urls \
  -e MESSAGE_BROKER_TYPE=kafka \
  -e WDM_KFK_BOOTSTRAP_URL=kafka:9092 \
  -e WDM_KFK_TOPIC=sensor.config \
  vss-configurator
```

### Example 2: Profile Configurator + Sensor Management with Upload Mode

```bash
docker run -d \
  --name vss-configurator \
  -p 5000:5000 \
  -e ENABLE_PROFILE_CONFIGURATOR=true \
  -e ENABLE_SENSOR_CONFIGURATOR=true \
  -e HARDWARE_PROFILE=L4 \
  -e MODE=3d \
  -e CALIBRATION_MODE=upload \
  -v /opt/config:/usr/src/app/config \
  -v /opt/calibration:/usr/src/app/calibration_store \
  vss-configurator

# Upload calibration data
curl -X POST http://localhost:5000/calibration \
  -H "Content-Type: application/json" \
  -d @calibration.json
```

### Example 3: Redis Message Broker with Event Duplication

```bash
docker run -d \
  --name vss-configurator \
  -p 5000:5000 \
  -e CALIBRATION_MODE=fetch \
  -e CALIBRATION_API_ENDPOINT=http://config-api:8080/calibration \
  -e SENSOR_INFO_SOURCE=nvstreamer \
  -e NVSTREAMER_STREAMS_ENDPOINT=http://nvstreamer:30000/api/v1/live/streams \
  -e MESSAGE_BROKER_TYPE=redis \
  -e WDM_REDIS_HOST=redis \
  -e WDM_REDIS_PORT=6379 \
  -e WDM_REDIS_STREAM_NAME=sensor.config \
  -e ENABLE_REDIS_DUPLICATOR_THREAD=true \
  -e REDIS_SOURCE_TOPIC=vst.event \
  -e REDIS_TARGET_TOPIC_CV=vst.event.cv \
  -e REDIS_TARGET_TOPIC_PN26=vst.event.pn26 \
  vss-configurator
```

### Example 4: Query Sensor Information

```bash
# Get all cameras
curl http://localhost:5000/cameras

# Get all groups
curl http://localhost:5000/groups

# Download calibration file
curl -O http://localhost:5000/download

# Liveness and readiness
curl http://localhost:5000/healthz
curl http://localhost:5000/readyz

# Video upload status (init containers)
curl http://localhost:5000/video-upload-status
```

### Example 5: Profile configuration variables and JSON update

```yaml
commons:
  variables:
    3d:
      - num_cameras: "16"
  file_operations:
    3d:
      - operation_type: json_update
        target_file: /usr/src/app/config/app.json
        backup: true
        updates:
          deployment.mode: '"3d"'

L4:
  3d:
    max_streams_supported: 8
    variables:
      - batch_size: "4 if num_cameras > 10 else 2"
      - stream_density: "min(num_cameras / 2, max_streams_supported)"
      - enable_high_quality: "true if num_cameras < 20 else false"
    file_operations:
      - operation_type: yaml_update
        target_file: /usr/src/app/config/deepstream.yaml
        backup: true
        updates:
          streammux.batch-size: "${batch_size}"
          streammux.buffer-pool-size: "${stream_density}"
```

---

## Troubleshooting

### Common Issues

#### 1. Calibration File Not Found

**Symptom:** API returns 503 or "Sensor mapping not created yet"

**Solutions:**
- Check `CALIBRATION_MODE` is correctly set
- Verify `CALIBRATION_API_ENDPOINT` is accessible (fetch mode)
- Ensure calibration file exists in mount path (mount mode)
- Check logs for calibration fetch/load errors

```bash
docker logs vss-configurator | grep -i calibration
```

#### 2. Sensor Mapping Not Updating

**Symptom:** `/cameras` endpoint returns outdated sensor list

**Solutions:**
- Verify `SENSOR_INFO_SOURCE` is correct (`msb` or `nvstreamer`)
- Check sensor bridge/NVStreamer endpoint is accessible
- Review background thread logs

```bash
docker logs vss-configurator | grep -i "sensor mapping"
```

#### 3. Message Broker Connection Failed

**Symptom:** Logs show Kafka/Redis connection errors

**Solutions:**
- Verify `WDM_KFK_BOOTSTRAP_URL` or `WDM_REDIS_HOST` is correct
- Check network connectivity to message broker
- Ensure topic/stream names are correct
- Verify broker is running and healthy

```bash
# Distroless image has no shell — test connectivity from another pod or host
kubectl run -it --rm debug --image=busybox --restart=Never -- nc -zv kafka 9092
```

#### 4. Profile Configurator Not Running

**Symptom:** Configuration files not generated on startup

**Solutions:**
- Ensure `ENABLE_PROFILE_CONFIGURATOR=true`
- Verify `HARDWARE_PROFILE` is set correctly
- Check profile configuration file exists and is valid YAML
- Review startup logs for profile configurator errors

```bash
docker logs vss-configurator | grep -i "profile"
```

#### 5. VMS Sensor Registration Failed

**Symptom:** Sensors not appearing in VMS

**Solutions:**
- Verify `CALL_SENSOR_ADD_API=true`
- Check `VST_CAMERA_ADD_ENDPOINT` is accessible
- Review sensor add API logs
- Ensure sensor URLs are reachable from VMS

```bash
docker logs vss-configurator | grep -i "add sensor"
```

### Debug Mode

Enable debug logging for detailed troubleshooting:

```bash
docker run -d \
  --name vss-configurator \
  -p 5000:5000 \
  -e LOG_LEVEL=DEBUG \
  ...other env vars...
  vss-configurator

# View debug logs
docker logs -f vss-configurator
```

### Log Levels

- `DEBUG`: Detailed information for diagnosing problems
- `INFO`: General informational messages (default)
- `WARN`: Warning messages for potentially harmful situations
- `ERROR`: Error messages for failure scenarios

---

## Dependencies

Runtime dependencies are declared in `pyproject.toml` and locked in `uv.lock`.

| Package | Version | Role |
|---------|---------|------|
| Flask | 3.1.0 | REST API |
| gunicorn | 23.0.0 | WSGI server |
| kafka-python | 2.3.0 | Kafka producer |
| redis | 5.0.1 | Redis streams / duplicator |
| requests | 2.32.3 | HTTP client (calibration, MSB, NVStreamer, VMS) |
| ruamel.yaml | 0.18.15 | Profile YAML read/write |
| spatialai-data-utils | in-repo path (`libs/analytics/spatialai-data-utils/release`) | BEV center recomputation (optional, 3D mode only) |

Full transitive runtime licenses: [3rdParty_Licenses.md](3rdParty_Licenses.md).

---

## Running tests

Unit tests live under `tests/` and use **pytest** with mocks for Kafka, Redis, and `spatialai_data_utils`.

```bash
uv sync --frozen
uv run pytest tests/ -v
```

With coverage:

```bash
uv run pytest tests/ -v --cov=app --cov-report=term-missing
```

Test modules cover profile configurator expressions, JSON format preservation, calibration filtering, message broker factory, MTMC API routes (`/healthz`, `/readyz`, `/calibration`, etc.), and sensor mapping utilities.

---
