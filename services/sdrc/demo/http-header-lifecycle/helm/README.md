# Helm example

This Helm testapp deploys SDRC, Redis, and RTVI-CV for HTTP-header lifecycle
testing. It reuses the repository's existing Helm charts and keeps only the
example SDRC workload config in this directory.

## What This Uses

- `deploy/helm/services/infra`: Redis and SDRC.
- `deploy/helm/services/rtvi`: RTVI-CV.
- `helm/configs/sdrc/config.yml`: local HTTP-header lifecycle workload config.

The SDRC chart exposes an extra workload listener Service port for
`WDM_MS_LISTENER_PORT: 10001`. That port is the workload-compatible facade that
accepts `/api/v1/stream/add` and `/api/v1/stream/remove`. The paths and
HTTP methods are defined in `helm/configs/sdrc/config.yml`; this example uses
`POST` for add, reprovision, and delete.

## Prerequisites

- Kubernetes cluster with GPU nodes and NVIDIA device plugin.
- `helm` v3 and `kubectl` configured for the target cluster.
- Access to the SDRC and RTVI-CV images configured by the reused charts. The
  default SDRC image in `values.yaml` is `localhost:5000/sdr-mw-l:local`;
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

If your SDRC image is not available as `localhost:5000/sdr-mw-l:local`, add
image overrides to the install command:

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

Wait for SDRC and RTVI-CV:

```bash
kubectl rollout status deployment/sdrc -n "$NAMESPACE" --timeout=300s
kubectl rollout status statefulset/vss-rtvi-cv -n "$NAMESPACE" --timeout=600s
```

Confirm the SDRC Service exposes both the UI/API port and the HTTP-header
workload listener. The output should include `5003` and `10001`:

```bash
kubectl get svc sdrc-controller -n "$NAMESPACE" -o jsonpath='{.spec.ports[*].port}'
echo
```

Forward the lifecycle listener to your workstation:

```bash
kubectl port-forward svc/sdrc-controller 10001:10001 -n "$NAMESPACE"
```

Optional: forward the SDRC UI/API port in another terminal:

```bash
kubectl port-forward svc/sdrc-controller 5003:5003 -n "$NAMESPACE"
```

Then open `http://localhost:5003` or check health with:

```bash
curl -s http://localhost:5003/dashboard/health
```

## Exercise Lifecycle

Keep `kubectl port-forward svc/sdrc-controller 10001:10001 -n "$NAMESPACE"`
running in another terminal before executing these commands. They call the SDRC
workload listener on `10001`. The `streamid` header is the routing key and the
stream identity SDRC uses for add, cached reprovision, and delete.

Add a stream:

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

Reprovision the same stream. This intentionally uses the configured add path
without a request body; SDRC reuses the cached stream state for the `streamid`
header value.

```bash
curl --location --request POST 'http://localhost:10001/api/v1/stream/add' \
  --header 'streamid: camera-001'
```

Delete the stream:

```bash
curl --location --request POST 'http://localhost:10001/api/v1/stream/remove' \
  --header 'streamid: camera-001'
```

A successful add/delete returns JSON with `status: ok`. Reprovision can return
`status: deferred` when workload replicas are not ready; that means SDRC
accepted the HTTP-header request and deferred reprovision until readiness is
satisfied.

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
  `deploy/helm/services/infra` and `deploy/helm/services/rtvi` present.
- RTVI-CV waits for app data: either create the NGC secrets and install with
  `downloadNgcAppData=true`, or pre-populate the models PVC.
- `localhost:10001` does not respond: keep the lifecycle `kubectl port-forward`
  command running and verify `kubectl get svc sdrc-controller` shows port
  `10001`.
- Add/delete returns an unexpected method error: verify the curl command uses
  the method configured in `configs/sdrc/config.yml`. This demo config uses
  `POST` for both add and delete.
- SDRC UI is not reachable: start the optional `5003:5003` port-forward and
  verify the Service exposes port `5003`.

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

SPDX-License-Identifier: Apache-2.0
