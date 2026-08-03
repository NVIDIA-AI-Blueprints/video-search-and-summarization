# Docker Compose example

This Docker Compose testapp starts SDRC, Redis, and RTVI-CV for HTTP-header
stream lifecycle management.

## What Starts

- `redis`: lifecycle state store.
- `sdr-controller`: SDRC multi-workload router plus Envoy listener generation.
- `perception`: the shared RTVI-CV Docker Compose service.

Clients call SDRC through the RTVI-CV workload listener on host port `10001`.
The lifecycle endpoint paths and methods come from `./configs/config.yml`; this
example config uses `POST /api/v1/stream/add` for add/reprovision and
`POST /api/v1/stream/remove` for delete.

## Prerequisites

- Docker and Docker Compose plugin.
- SDRC image available locally or in a registry.
- RTVI-CV image available locally or in a registry, as required by the shared
  RTVI-CV Compose file.
- Warehouse app data containing
  `models/mtmc/rtdetr_warehouse_v1.0.2.fp16.onnx`.

Set these variables from the repository root:

```bash
export REPO_ROOT=$(git rev-parse --show-toplevel)
export VSS_APPS_DIR="$REPO_ROOT/deploy/docker"
export VSS_DATA_DIR=/path/to/vss-warehouse-app-data
export HOST_IP=127.0.0.1
export HARDWARE_PROFILE=dGPU
export SDR_MW_L_IMAGE=nvcr.io/nvstaging/vss-core/sdr-mw-l:3.0.0-prd.10
```

Optional host-port overrides, useful when a local service already uses one of
the defaults:

```bash
export SDRC_CONTROLLER_HOST_PORT=5003
export SDRC_RTVI_CV_PROXY_HOST_PORT=10001
export SDRC_DIRECT_HOST_PORT=8011
export SDRC_ENVOY_ADMIN_HOST_PORT=9902
export REDIS_HOST_PORT=6379
```

Use the same value for `VSS_DATA_DIR` that you would pass to
`blueprint-deploy.sh -D`. RTVI-CV expects the RT-DETR model at
`$VSS_DATA_DIR/models/mtmc/rtdetr_warehouse_v1.0.2.fp16.onnx`, mounted inside the
container as `/opt/storage/rtdetr_warehouse_v1.0.2.fp16.onnx`.

## Start

```bash
cd "$REPO_ROOT/services/sdrc/demo/http-header-lifecycle/docker"
docker compose up -d
```

## Validate

Validate the Compose model:

```bash
docker compose config --quiet
```

Check containers:

```bash
docker compose ps
```

Check SDRC router health and open the SDRC UI:

```bash
curl -s http://localhost:${SDRC_CONTROLLER_HOST_PORT:-5003}/dashboard/health
```

SDRC UI: `http://localhost:${SDRC_CONTROLLER_HOST_PORT:-5003}`

Check that Envoy created the RTVI-CV workload listener on `10001`:

```bash
curl -s http://localhost:${SDRC_ENVOY_ADMIN_HOST_PORT:-9902}/listeners | grep ${SDRC_RTVI_CV_PROXY_HOST_PORT:-10001}
```

## Exercise Lifecycle

These commands call the SDRC workload listener on `10001`. The `streamid`
header is the routing key and the stream identity SDRC uses for add, cached
reprovision, and delete.

Add a stream:

Note: replace the sample `camera_url` with a valid, reachable RTSP stream for
your environment. If the RTSP stream is not working, RTVI-CV may accept the
lifecycle request but you will not see FPS for that stream.

```bash
curl --location --request POST "http://localhost:${SDRC_RTVI_CV_PROXY_HOST_PORT:-10001}/api/v1/stream/add" \
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

Reprovision the same stream. This intentionally uses the configured add path
without a request body; SDRC reuses the cached stream state for the `streamid`
header value.

```bash
curl --location --request POST "http://localhost:${SDRC_RTVI_CV_PROXY_HOST_PORT:-10001}/api/v1/stream/add" \
  --header 'streamid: camera-001'
```

Delete the stream:

```bash
curl --location --request POST "http://localhost:${SDRC_RTVI_CV_PROXY_HOST_PORT:-10001}/api/v1/stream/remove" \
  --header 'streamid: camera-001'
```

A successful add/delete returns JSON with `status: ok`. Reprovision can return
`status: deferred` when workload replicas are not ready; that means SDRC
accepted the HTTP-header request and deferred reprovision until readiness is
satisfied.

## Verify In RTVI-CV

After add or delete returns successfully, check RTVI-CV logs to confirm the
workload received the stream lifecycle update:

```bash
docker compose logs -f perception
```

## Logs

```bash
docker compose logs -f sdr-controller
docker compose logs -f perception
```

## Stop

```bash
cd "$REPO_ROOT/services/sdrc/demo/http-header-lifecycle/docker"
docker compose down
```

To also remove generated local logs:

```bash
rm -rf .generated
```

## Troubleshooting

- `VSS_APPS_DIR is missing`: export `VSS_APPS_DIR="$REPO_ROOT/deploy/docker"`.
- RT-DETR model not found: point `VSS_DATA_DIR` at the warehouse app data root,
  not at the repository `data/` directory unless that directory contains the
  warehouse model bundle.
- `localhost:10001` connection reset: confirm `sdr-controller` is healthy and
  `curl -s http://localhost:${SDRC_ENVOY_ADMIN_HOST_PORT:-9902}/listeners | grep ${SDRC_RTVI_CV_PROXY_HOST_PORT:-10001}` returns a listener.
- Add/delete returns an unexpected method error: verify the curl command uses
  the method configured in `configs/config.yml`. This demo config uses `POST`
  for both add and delete.

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

SPDX-License-Identifier: Apache-2.0
