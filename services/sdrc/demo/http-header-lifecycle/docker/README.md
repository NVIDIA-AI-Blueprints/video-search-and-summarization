# Docker Compose example

This Docker Compose testapp starts SDRC, Redis, RTVI-CV, and the VIOS/VST
services needed to generate RTSP streams from uploaded videos for HTTP-header
stream lifecycle management.

## What Starts

- `redis`: lifecycle state store.
- `sdr-controller`: SDRC multi-workload router plus Envoy listener generation.
- `perception`: the shared RTVI-CV Docker Compose service.
- `centralizedb`, `sensor-ms`, `streamprocessing-ms`, and `vst-ingress`: reused VIOS/VST services.
- `nvstreamer`: video-to-RTSP service using the warehouse nvstreamer config.

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

`docker/.env` already sets `VSS_APPS_DIR`, `COMPOSE_PROFILES` (so VIOS/VST
profiles start with a plain `docker compose up`), `HOST_IP`,
`HARDWARE_PROFILE`, and `SAMPLE_VIDEO_DATASET`. You only need the warehouse
app data path (and optional image overrides):

```bash
export VSS_DATA_DIR=/path/to/vss-warehouse-app-data
# optional:
export SDR_MW_L_IMAGE=nvcr.io/nv-metropolis-dev/metropolis-analytic/sdr-mw-l:3.3.3-test-2026-08-03
```

Optional host-port overrides, useful when a local service already uses one of
the defaults:

```bash
export SDRC_CONTROLLER_HOST_PORT=5003
export SDRC_RTVI_CV_PROXY_HOST_PORT=10001
export SDRC_DIRECT_HOST_PORT=8011
export SDRC_ENVOY_ADMIN_HOST_PORT=9902
export REDIS_HOST_PORT=6379
export SENSOR_HTTP_HOST_PORT=30000
export STREAM_PROCESSOR_HTTP_HOST_PORT=30001
export RTSP_SERVER_HOST_PORT=30554
export RTSP_SERVER_HOST_PORT_END=30564
export NVSTREAMER_HTTP_HOST_PORT=31000
```

Use the same value for `VSS_DATA_DIR` that you would pass to
`blueprint-deploy.sh -D`. `SAMPLE_VIDEO_DATASET` is set only to satisfy reusable
VIOS compose variables referenced by inactive profiles; the demo upload command
uses `VIDEO_FILE` directly. RTVI-CV expects the RT-DETR model at
`$VSS_DATA_DIR/models/mtmc/rtdetr_warehouse_v1.0.2.fp16.onnx`, mounted inside the
container as `/opt/storage/rtdetr_warehouse_v1.0.2.fp16.onnx`.

## Start

```bash
export REPO_ROOT=$(git rev-parse --show-toplevel)
export VSS_DATA_DIR=/path/to/vss-warehouse-app-data
cd "$REPO_ROOT/services/sdrc/demo/http-header-lifecycle/docker"
docker compose up -d
```

That starts `redis`, `sdr-controller`, `perception` (RTVI-CV), `centralizedb`,
`sensor-ms`, `streamprocessing-ms`, `vst-ingress` (`:30888`), and `nvstreamer`
(`:31000`). Override any `.env` default by exporting the variable in your shell
before `docker compose up`.

## Validate

Validate the Compose model:

```bash
docker compose config --quiet
```

Check containers:

```bash
docker compose ps
```

The output should include SDRC, RTVI-CV, and the VIOS/VST containers
`vss-vios-postgres`, `vss-vios-sensor`, `vss-vios-streamprocessing`,
`vss-vios-ingress`, and `vss-vios-nvstreamer`.

Check SDRC router health and open the SDRC UI:

```bash
curl -s http://localhost:${SDRC_CONTROLLER_HOST_PORT:-5003}/dashboard/health
```

SDRC UI: `http://localhost:${SDRC_CONTROLLER_HOST_PORT:-5003}`

Check that Envoy created the RTVI-CV workload listener on `10001`:

```bash
curl -s http://localhost:${SDRC_ENVOY_ADMIN_HOST_PORT:-9902}/listeners | grep ${SDRC_RTVI_CV_PROXY_HOST_PORT:-10001}
```

## Generate RTSP Streams With NVStreamer

The Compose app also starts nvstreamer so you can generate an RTSP URL from a
local video and use that URL in the SDRC add-stream curl request. You can get an
RTSP stream in any of these ways:

- Add video files under `$VSS_DATA_DIR/videos` before starting the demo, then
  use nvstreamer to scan/list the generated streams.
- Manually upload a video to nvstreamer with the curl command below.
- Skip nvstreamer and set `RTSP_URL` to any working RTSP stream that RTVI-CV can
  reach from inside the Compose network.

Upload a local video file to nvstreamer:

```bash
export VIDEO_FILE=/path/to/sample.mp4
export STREAM_ID=camera-001

curl --location --request POST "http://localhost:${NVSTREAMER_HTTP_HOST_PORT:-31000}/api/v1/storage/file" \
  --form "metadata={\"streamName\":\"${STREAM_ID}\",\"streamId\":\"${STREAM_ID}\"};type=application/json" \
  --form "mediaFile=@${VIDEO_FILE};type=video/mp4"
```

List generated streams and pick the RTSP URL for `camera-001`:

```bash
curl -s "http://localhost:${NVSTREAMER_HTTP_HOST_PORT:-31000}/api/v1/sensor/streams"
```

Set `RTSP_URL` to the generated stream URL before running the SDRC add curl:

```bash
export RTSP_URL='rtsp://vss-vios-streamprocessing:30554/live/camera-001'
```

Use the Compose service DNS name in `RTSP_URL` because RTVI-CV reads the stream
from inside the Compose network. If your nvstreamer response uses localhost or
the host IP, replace the host with `vss-vios-streamprocessing` and keep the same
stream path.

## Exercise Lifecycle

These commands call the SDRC workload listener on `10001`. The `streamid`
header is the routing key and the stream identity SDRC uses for add, cached
reprovision, and delete.

Add a stream:

Note: set `RTSP_URL` to a valid, reachable RTSP stream before running this
command. You can use the nvstreamer flow above to generate one from a video. If
the RTSP stream is not working, RTVI-CV may accept the lifecycle request but
you will not see FPS for that stream.

```bash
curl --location --request POST "http://localhost:${SDRC_RTVI_CV_PROXY_HOST_PORT:-10001}/api/v1/stream/add" \
  --header 'streamid: camera-001' \
  --header 'Content-Type: application/json' \
  --data @- <<EOF
{
  "alert_type": "camera_status_change",
  "created_at": "2026-01-01T00:00:00Z",
  "event": {
    "camera_id": "camera-001",
    "camera_name": "Dock Camera 1",
    "camera_url": "$RTSP_URL",
    "change": "camera_add",
    "metadata": {"site": "warehouse-a"}
  },
  "source": "vst"
}
EOF
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
docker compose logs -f nvstreamer
docker compose logs -f streamprocessing-ms
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

- `VSS_APPS_DIR is missing`: ensure you run compose from `docker/` so `.env`
  loads, or export `VSS_APPS_DIR="$REPO_ROOT/deploy/docker"`.
- `vst-ingress` / `:30888` missing: confirm `COMPOSE_PROFILES` includes
  `vst-ingress` (set in `.env` by default) and `docker compose ps` lists
  `vss-vios-ingress`.
- RT-DETR model not found: point `VSS_DATA_DIR` at the warehouse app data root,
  not at the repository `data/` directory unless that directory contains the
  warehouse model bundle.
- `localhost:10001` connection reset: confirm `sdr-controller` is healthy and
  `curl -s http://localhost:${SDRC_ENVOY_ADMIN_HOST_PORT:-9902}/listeners | grep ${SDRC_RTVI_CV_PROXY_HOST_PORT:-10001}` returns a listener.
- Add/delete returns an unexpected method error: verify the curl command uses
  the method configured in `configs/config.yml`. This demo config uses `POST`
  for both add and delete.
- nvstreamer upload or stream listing fails: verify `docker compose ps` shows
  `nvstreamer` and `streamprocessing-ms` running, and confirm host port
  `${NVSTREAMER_HTTP_HOST_PORT:-31000}` is free.

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

SPDX-License-Identifier: Apache-2.0
