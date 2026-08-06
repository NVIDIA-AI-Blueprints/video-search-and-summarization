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

# Helm example

This Helm testapp deploys SDRC, Redis, RTVI-CV, and the VIOS/VST services
needed to generate RTSP streams from uploaded videos for HTTP-header lifecycle
testing. It reuses the repository's existing Helm charts and keeps only the
example SDRC workload config in this directory.

## What This Uses

- `deploy/helm/services/infra`: Redis, Kafka, and SDRC.
- `deploy/helm/services/rtvi`: RTVI-CV.
- `deploy/helm/services/vios`: VST sensor, streamprocessing, ingress, postgres, and nvstreamer.
- `helm/configs/sdrc/config.yml`: local HTTP-header lifecycle workload config.

The SDRC chart exposes an extra workload listener Service port for
`WDM_MS_LISTENER_PORT: 10001`. That port is the workload-compatible facade that
accepts `/api/v1/stream/add` and `/api/v1/stream/remove`. The paths and
HTTP methods are defined in `helm/configs/sdrc/config.yml`; this example uses
`POST` for add, reprovision, and delete.

## Prerequisites

- Kubernetes cluster with GPU nodes and NVIDIA device plugin.
- `helm` v3 and `kubectl` configured for the target cluster.
- Access to the SDRC, RTVI-CV, and VIOS images configured by the reused
  charts. The default SDRC image in `values.yaml` is
  `nvcr.io/nvstaging/vss-core/sdr-mw-l:3.3.0-2026.08.2-2`;
  override it if your cluster pulls from another registry.
- Warehouse app data for RTVI-CV. The easiest fresh install path is to let the
  chart download the NGC warehouse data bundle after you create the NGC secrets.

Do not commit real API keys. The commands below use placeholders for secrets.

## Render Only

Use this when you only want to inspect the rendered Kubernetes manifests.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
CHART_DIR="$REPO_ROOT/services/sdrc/demo/http-header-lifecycle/helm"

helm dependency build "$CHART_DIR"
helm template sdrc-http-header "$CHART_DIR" --namespace sdrc-http-header
```

## Install With NGC App Data Download

Use this path for a fresh namespace where the RTVI-CV models PVC does not
already contain the warehouse app data.

```bash
RELEASE=sdrc-http-header
NAMESPACE=sdrc-http-header
REPO_ROOT=$(git rev-parse --show-toplevel)
CHART_DIR="$REPO_ROOT/services/sdrc/demo/http-header-lifecycle/helm"

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic ngc-api \
  --namespace "$NAMESPACE" \
  --from-literal=NGC_CLI_API_KEY='<your-ngc-api-key>' \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret docker-registry ngc-docker-reg-secret \
  --namespace "$NAMESPACE" \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password='<your-ngc-api-key>' \
  --dry-run=client -o yaml | kubectl apply -f -

helm dependency build "$CHART_DIR"
helm upgrade --install "$RELEASE" "$CHART_DIR" \
  --namespace "$NAMESPACE" \
  --set rtvi.vss-rtvi-cv.downloadNgcAppData=true \
  --set-string rtvi.vss-rtvi-cv.ngcAppDataResourceVersion=<vss-warehouse-app-data-resource>
```

If your SDRC image is not available as
`nvcr.io/nvstaging/vss-core/sdr-mw-l:3.3.0-2026.08.2-2`,
add image overrides to the install command:

```bash
  --set infra.sdrc.image.repository=<registry>/sdr-mw-l \
  --set infra.sdrc.image.tag=<tag> \
  --set infra.sdrc.image.pullPolicy=IfNotPresent
```

## Install When App Data Already Exists

Use this shorter path if the `vss-rtvi-cv-models` PVC already contains
`vss-warehouse-app-data/` with the required model files.

```bash
RELEASE=sdrc-http-header
NAMESPACE=sdrc-http-header
REPO_ROOT=$(git rev-parse --show-toplevel)
CHART_DIR="$REPO_ROOT/services/sdrc/demo/http-header-lifecycle/helm"

helm dependency build "$CHART_DIR"
helm upgrade --install "$RELEASE" "$CHART_DIR" \
  --namespace "$NAMESPACE" \
  --create-namespace
```

## Validate

Wait for SDRC, RTVI-CV, and VIOS/VST services:

```bash
kubectl rollout status deployment/sdrc -n "$NAMESPACE" --timeout=300s
kubectl rollout status statefulset/vss-rtvi-cv -n "$NAMESPACE" --timeout=600s
kubectl rollout status deployment/vss-vios-sensor -n "$NAMESPACE" --timeout=300s
kubectl rollout status statefulset/vss-vios-streamprocessing -n "$NAMESPACE" --timeout=300s
kubectl rollout status deployment/vss-vios-ingress -n "$NAMESPACE" --timeout=300s
kubectl rollout status deployment/vss-vios-nvstreamer -n "$NAMESPACE" --timeout=300s
```

Confirm the SDRC Service exposes both the UI/API NodePort and the
HTTP-header workload listener NodePort from `values.yaml`. The output should
include `30003` for the SDRC UI/API and `31001` for the RTVI-CV lifecycle
listener:

```bash
kubectl get svc sdrc-controller -n "$NAMESPACE"
```

Set the node IP used for NodePort access. For a single-node local cluster this
is usually the first node InternalIP:

```bash
export NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
```

Open the SDRC UI at `http://$NODE_IP:30003` or check health with:

```bash
curl -s "http://$NODE_IP:30003/dashboard/health"
```

Optional local fallback: if NodePort access is blocked by your cluster network,
use port-forward in separate terminals instead:

```bash
kubectl port-forward svc/sdrc-controller 10001:10001 -n "$NAMESPACE"
kubectl port-forward svc/sdrc-controller 5003:5003 -n "$NAMESPACE"
```

## Generate RTSP Streams With NVStreamer

The chart also deploys VIOS/VST and nvstreamer so you can generate an RTSP URL
from a local video and use that URL in the SDRC add-stream curl request. You can
get an RTSP stream in either of these ways:

- Manually upload a video to nvstreamer with the curl command below.
- Skip nvstreamer and set `RTSP_URL` to any working RTSP stream that RTVI-CV can
  reach from inside Kubernetes.

For the Docker Compose demo, you can also place videos under `$VSS_DATA_DIR/videos`
because that host path is mounted into nvstreamer.

Forward nvstreamer in a separate terminal:

```bash
kubectl port-forward svc/vss-vios-nvstreamer 31000:31000 -n "$NAMESPACE"
```

Upload a local video file to nvstreamer:

```bash
export VIDEO_FILE=/path/to/sample.mp4
export STREAM_ID=camera-001

curl --location --request POST "http://localhost:31000/api/v1/storage/file" \
  --form "metadata={\"streamName\":\"${STREAM_ID}\",\"streamId\":\"${STREAM_ID}\"};type=application/json" \
  --form "mediaFile=@${VIDEO_FILE};type=video/mp4"
```

List generated streams and pick the RTSP URL for `camera-001`:

```bash
curl -s "http://localhost:31000/api/v1/sensor/streams"
```

Set `RTSP_URL` to the generated stream URL before running the SDRC add curl:

```bash
export RTSP_URL='rtsp://vss-vios-streamprocessing:30554/live/camera-001'
```

Use the in-cluster service DNS name in `RTSP_URL` because RTVI-CV reads the
stream from inside Kubernetes. If your nvstreamer response uses a localhost or
node address, replace the host with `vss-vios-streamprocessing` and keep the
same stream path.

## Exercise Lifecycle

These commands use the RTVI-CV lifecycle NodePort from `values.yaml`: service
port `10001` exposed as node port `31001`. They call SDRC through
`http://$NODE_IP:31001`. The `streamid` header is the routing key and the
stream identity SDRC uses for add, cached reprovision, and delete.

If you use the optional port-forward fallback instead, replace
`http://$NODE_IP:31001` with `http://localhost:10001` in the curl commands.

Add a stream:

Note: set `RTSP_URL` to a valid, reachable RTSP stream before running this
command. You can use the nvstreamer flow above to generate one from a video. If
the RTSP stream is not working, RTVI-CV may accept the lifecycle request but
you will not see FPS for that stream.

```bash
curl --location --request POST "http://$NODE_IP:31001/api/v1/stream/add" \
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
curl --location --request POST "http://$NODE_IP:31001/api/v1/stream/add" \
  --header 'streamid: camera-001'
```

Delete the stream:

```bash
curl --location --request POST "http://$NODE_IP:31001/api/v1/stream/remove" \
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
kubectl logs -f statefulset/vss-rtvi-cv -n "$NAMESPACE" -c vss-rtvi-cv
```

## Logs

```bash
kubectl logs -f deployment/sdrc -n "$NAMESPACE"
kubectl logs -f statefulset/vss-rtvi-cv -n "$NAMESPACE" -c vss-rtvi-cv
```

## Uninstall

```bash
helm uninstall "$RELEASE" --namespace "$NAMESPACE"
```

Optional full cleanup:

```bash
kubectl delete namespace "$NAMESPACE"
```

## Troubleshooting

- `helm dependency build` fails: run it from a checkout that has
  `deploy/helm/services/infra`, `deploy/helm/services/rtvi`, and
  `deploy/helm/services/vios` present.
- RTVI-CV waits for app data: either create the NGC secrets and install with
  `downloadNgcAppData=true`, or pre-populate the models PVC.
- `$NODE_IP:31001` does not respond: verify `kubectl get svc sdrc-controller`
  shows node port `31001` for the lifecycle listener, and confirm your cluster
  allows NodePort access to the selected node IP. Use the port-forward fallback
  if NodePort traffic is blocked.
- `localhost:10001` does not respond with the fallback path: keep the lifecycle
  `kubectl port-forward` command running and verify the Service shows port
  `10001`.
- Add/delete returns an unexpected method error: verify the curl command uses
  the method configured in `configs/sdrc/config.yml`. This demo config uses
  `POST` for both add and delete.
- SDRC UI is not reachable on `$NODE_IP:30003`: verify the Service shows
  node port `30003`, or start the optional `5003:5003` port-forward and use
  `http://localhost:5003`.
- nvstreamer upload or stream listing fails: verify
  `kubectl get pods -n "$NAMESPACE" | grep vss-vios` shows the VIOS pods
  ready and keep the `31000:31000` port-forward running.

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

SPDX-License-Identifier: Apache-2.0
