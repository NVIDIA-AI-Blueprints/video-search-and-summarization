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
accepts `/api/v1/stream/add` and `/api/v1/stream/remove`.

## Prerequisites

- Kubernetes cluster with GPU nodes and NVIDIA device plugin.
- `helm` v3 and `kubectl` configured for the target cluster.
- Access to the SDRC and RTVI-CV images configured by the reused charts.
- Warehouse app data for RTVI-CV. The easiest fresh install path is to let the
  chart download the NGC warehouse data bundle after you create the NGC secrets.

Do not commit real API keys. The commands below use placeholders for secrets.

## Render Only

Use this when you only want to inspect the rendered Kubernetes manifests.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
CHART_DIR="$REPO_ROOT/services/sdrc/tests/http-header-lifecycle/helm"

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
CHART_DIR="$REPO_ROOT/services/sdrc/tests/http-header-lifecycle/helm"

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

## Install When App Data Already Exists

Use this shorter path if the `vss-rtvi-cv-models` PVC already contains
`vss-warehouse-app-data/` with the required model files.

```bash
RELEASE=sdrc-http-header
NAMESPACE=sdrc-http-header
REPO_ROOT=$(git rev-parse --show-toplevel)
CHART_DIR="$REPO_ROOT/services/sdrc/tests/http-header-lifecycle/helm"

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

Confirm the SDRC Service exposes the HTTP-header workload listener:

```bash
kubectl get svc sdrc-controller -n "$NAMESPACE" -o jsonpath='{.spec.ports[*].port}'
echo
```

Forward the lifecycle listener to your workstation:

```bash
kubectl port-forward svc/sdrc-controller 10001:10001 -n "$NAMESPACE"
```

In another terminal, run the add, reprovision, and delete curl commands from
the parent [README](../README.md#lifecycle-requests).

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
- `localhost:10001` does not respond: keep the `kubectl port-forward` command
  running and verify `kubectl get svc sdrc-controller` shows port `10001`.

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.

SPDX-License-Identifier: Apache-2.0
