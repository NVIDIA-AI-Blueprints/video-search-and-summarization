# Docker Compose example

This Docker Compose testapp starts SDRC, Redis, and RTVI-CV for HTTP-header
stream lifecycle management.

## What Starts

- `redis`: lifecycle state store.
- `sdr-controller`: SDRC multi-workload router plus Envoy listener generation.
- `perception`: the shared RTVI-CV Docker Compose service.

Clients call SDRC through the RTVI-CV workload listener on host port `10001`.
The lifecycle endpoint paths come from `./configs/config.yml`.

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
export SDR_MW_L_IMAGE=sdr-mw-l:local
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

Check SDRC router health:

```bash
curl -s http://localhost:5003/dashboard/health
```

Check that Envoy created the RTVI-CV workload listener on `10001`:

```bash
curl -s http://localhost:9902/listeners | grep 10001
```

## Exercise Lifecycle

Run the add, reprovision, and delete curl commands from the parent
[README](../README.md#lifecycle-requests).

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
  `curl -s http://localhost:9902/listeners | grep 10001` returns a listener.

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

SPDX-License-Identifier: Apache-2.0
