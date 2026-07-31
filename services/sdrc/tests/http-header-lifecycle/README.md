# SDRC HTTP-header lifecycle example

This directory contains a self-contained Docker Compose example for SDRC plus RTVI-CV using HTTP-header stream lifecycle management. The example keeps lifecycle demo endpoints in `services/sdrc/tests/http-header-lifecycle/configs/config.yml` instead of modifying any warehouse blueprint config.

## What This Starts

This testapp starts:

- `redis`: lifecycle state store.
- `sdr-controller`: SDRC multi-workload router plus Envoy listener generation.
- `perception`: the shared RTVI-CV Docker Compose service.

Clients call SDRC through the RTVI-CV workload listener on host port `10001`. The lifecycle endpoint paths come from `./configs/config.yml`.

## Prerequisites

Build or provide an SDRC image before starting the testapp:

```bash
export SDR_MW_L_IMAGE=sdr-mw-l:local
```

Make sure the shared Docker Compose files are available and set `VSS_DATA_DIR` to the same data root that you pass to `blueprint-deploy.sh -D`.

```bash
export REPO_ROOT=$(git rev-parse --show-toplevel)
export VSS_APPS_DIR="$REPO_ROOT/deploy/docker"
export VSS_DATA_DIR=/path/to/vss-warehouse-app-data
```

RTVI-CV expects the RT-DETR model at `$VSS_DATA_DIR/models/mtmc/rtdetr_warehouse_v1.0.2.fp16.onnx`, which is mounted into the container as `/opt/storage/rtdetr_warehouse_v1.0.2.fp16.onnx`.

## Run The Testapp

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/services/sdrc/tests/http-header-lifecycle"
VSS_APPS_DIR="$REPO_ROOT/deploy/docker" \
VSS_DATA_DIR=/path/to/vss-warehouse-app-data \
HOST_IP=127.0.0.1 \
HARDWARE_PROFILE=dGPU \
SDR_MW_L_IMAGE=sdr-mw-l:local \
docker compose up -d
```

Validate the rendered Compose model:

```bash
HARDWARE_PROFILE=dGPU docker compose config --quiet
```

Check containers and SDRC router health:

```bash
docker compose ps
curl -s http://localhost:5003/dashboard/health
```

Check Envoy created the RTVI-CV workload listener:

```bash
curl -s http://localhost:9902/listeners | grep 10001
```

## Add stream

```bash
curl --location --request POST 'http://localhost:10001/api/v1/stream/add' \
  --header 'streamid: camera-001' \
  --header 'Content-Type: application/json' \
  --data '{
    "alert_type": "camera_status_change",
    "created_at": "2026-01-01T00:00:00Z",
    "event": {
      "camera_id": "camera-001",
      "camera_name": "Dock Camera 1",
      "camera_url": "rtsp://vss-vios-streamprocessing:30554/webrtc/camera-001",
      "change": "camera_add",
      "metadata": {"site": "warehouse-a"}
    },
    "source": "vst"
  }'
```

## Reprovision stream

The add and reprovision actions intentionally share `POST /api/v1/stream/add`. SDRC treats a body-less request as reprovision and reuses cached stream state.

```bash
curl --location --request POST 'http://localhost:10001/api/v1/stream/add' \
  --header 'streamid: camera-001'
```

## Delete stream

The example config sets `WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD: POST`, so callers use POST for delete. Change that config value to `DELETE` if you want a DELETE-compatible facade.

```bash
curl --location --request POST 'http://localhost:10001/api/v1/stream/remove' \
  --header 'streamid: camera-001'
```

## Stop

```bash
docker compose down
```
