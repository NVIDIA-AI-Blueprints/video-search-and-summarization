# SDRC HTTP-header lifecycle examples

This directory contains Docker Compose and Helm test applications for SDRC
HTTP-header stream lifecycle management. The examples keep demo configs under
`services/sdrc/demo/http-header-lifecycle` so warehouse blueprint configs do
not carry showcase-only lifecycle settings.

## Choose A Runtime

- `docker/`: quickest local path when Docker Compose and the RTVI-CV image are
  available on the host.
- `helm/`: Kubernetes path that reuses the repository's existing `infra` and
  `rtvi` Helm charts.

Both examples expose the RTVI-CV lifecycle listener on port `10001`, matching
`WDM_MS_LISTENER_PORT: 10001` in their SDRC workload configs. The add, delete,
and reprovision paths and methods are read from each example's `config.yml`;
the curl commands below intentionally use those configured paths instead of
hardcoded SDRC defaults.

## Lifecycle Requests

Run these commands after either example is up and `localhost:10001` reaches the
SDRC RTVI-CV workload listener. For Helm, create the lifecycle port-forward
from `helm/README.md` first.

Add stream:

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

Reprovision stream. This intentionally uses the same add endpoint without a
body; SDRC reuses cached stream state for the `streamid` header value.

```bash
curl --location --request POST 'http://localhost:10001/api/v1/stream/add' \
  --header 'streamid: camera-001'
```

Delete stream:

```bash
curl --location --request POST 'http://localhost:10001/api/v1/stream/remove' \
  --header 'streamid: camera-001'
```

## Expected Responses

A successful add/delete returns JSON with `status: ok`. Reprovision can return
`status: deferred` when the workload replicas are not ready yet; that means the
HTTP-header route was accepted and SDRC deferred the reprovision until workload
readiness is satisfied.

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

SPDX-License-Identifier: Apache-2.0
