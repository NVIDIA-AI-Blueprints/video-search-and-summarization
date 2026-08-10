<!-- SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
-->

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

## RTSP Stream Sources

For the add-stream request, `camera_url` must point to a valid RTSP stream that
the target workload can reach. You can use one of these approaches:

- Docker Compose demo: add video files under `$VSS_DATA_DIR/videos` and use
  nvstreamer to expose them as RTSP streams.
- Docker Compose or Helm demo: manually upload a video to nvstreamer and use the
  generated RTSP URL.
- Either demo: provide your own working RTSP URL directly.

See `docker/README.md` or `helm/README.md` for the exact nvstreamer commands.

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
