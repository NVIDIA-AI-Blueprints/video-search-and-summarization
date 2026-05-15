# Standalone RTVI Embed (`vss-rtvi-embed`)

This document describes a **from-scratch** install of the **`vss-rtvi-embed`** subchart: the **RTVI Cosmos Embed1** HTTP service (port **8000**) for text/video embeddings and MDX search. The chart deploys a GPU **Deployment**, **Service**, and PVCs for NGC and Hugging Face model caches. **Kafka and Redis are not required** for the standalone profile in **`overrides_rtvi_embed.yaml`** (`kafkaEnabled: false`, `waitForKafka.enabled: false`).

For chart internals (templates, values), see `charts/rtvi-embed/`.

---

## Prerequisites

- Kubernetes cluster with **NVIDIA GPU** nodes and the NVIDIA device plugin (workload requests `nvidia.com/gpu: 1`).
- **`helm`** (v3) with network access to pull images (`nvcr.io`, `docker.io`, …).
- **Hugging Face token** in a Secret (default name/key below) for model `git:https://huggingface.co/nvidia/Cosmos-Embed1-448p`.
- A **StorageClass** for RWO volumes (or leave `persistence.storageClass` empty to use the cluster default).
- **`ngc-image-pull-secret`** (or equivalent) if your cluster requires pull secrets for private images (`imagePullSecrets` in `overrides_rtvi_embed.yaml`). Base `values.yaml` defaults to **`ngc-docker-reg-secret`** instead—use one name consistently.
- On **MicroK8s / GPU Operator** clusters, GPU scheduling may require extra node setup (device plugin, `nvidia.com/gpu` allocatable); this chart version does not expose `runtimeClassName` in `templates/deployment.yaml`.

Default image: `nvcr.io/nvstaging/vss-core/vss-rt-embed:3.2.0-26.05.2` (see `Chart.yaml` / `values.yaml`).

---

## 1. Variables (set once per shell)

Pick a Helm **release name**, **namespace**, and ensure secrets exist in that namespace.

```bash
export RELEASE="vss-rtvi-embed"
export NAMESPACE="rtvi-embed"
```

With default `charts/rtvi-embed` helpers, Kubernetes **object names** for the workload are **`vss-rtvi-embed`** (Deployment, Service, PVC prefix), derived from the subchart **`Chart.name`**, **not** from the umbrella Helm **`RELEASE`** name. Labels such as `app.kubernetes.io/instance` **do** match the Helm release. If you set `global.useReleaseNamePrefix: true` or `fullnameOverride`, object names change—use `kubectl get deploy,svc,pvc -n "${NAMESPACE}"` to confirm.

---

## 2. Namespace and secrets

```bash
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
```

**NGC / nvcr.io pull secret.** `overrides_rtvi_embed.yaml` expects **`Secret` name `ngc-image-pull-secret`**, key **`.dockerconfigjson`**:

```bash
kubectl create secret generic ngc-image-pull-secret \
  --namespace "${NAMESPACE}" \
  --from-file=.dockerconfigjson=/path/to/.docker/config.json \
  --type=kubernetes.io/dockerconfigjson \
  --dry-run=client -o yaml | kubectl apply -f -
```

**Hugging Face token.** `overrides_rtvi_embed.yaml` wires **`hfTokenSecret.name: hf-token-secret`**, key **`HF_TOKEN`**:

```bash
kubectl create secret generic hf-token-secret \
  --namespace "${NAMESPACE}" \
  --from-literal=HF_TOKEN='<your-hf-token>' \
  --dry-run=client -o yaml | kubectl apply -f -
```

If you use other secret names, set `imagePullSecrets` and `hfTokenSecret` in Helm values or `--set` flags to match.

---

## 3. Helm chart path and dependencies

From the repository root (or any directory), the umbrella chart lives at:

`deploy/helm/services/rtvi`

If you use packaged dependencies instead of `file://` subcharts, run once:

```bash
cd deploy/helm/services/rtvi
helm dependency update
```

### 3.1 Install only the `vss-rtvi-embed` subchart (recommended)

From `deploy/helm/services/rtvi/charts/rtvi-embed`, use **flattened** values (no `vss-rtvi-embed.` prefix):

```bash
cd deploy/helm/services/rtvi/charts/rtvi-embed
helm upgrade --install "${RELEASE}" . \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  -f overrides_rtvi_embed.yaml \
  --wait --timeout 45m
```

---

## 4. Install via the `rtvi` umbrella (optional)

Minimal install: enable **`vss-rtvi-embed`**, disable other RTVI subcharts, and set standalone-style Kafka/Redis placeholders. Prefer **section 3.1** if you only need embed; use this when you already deploy from the umbrella chart.

```bash
cd deploy/helm/services/rtvi

helm upgrade --install "${RELEASE}" . \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --set vss-rtvi-embed.enabled=true \
  --set vss-rtvi-cv.enabled=false \
  --set vss-rtvi-cv-sdr.enabled=false \
  --set vss-rtvi-vlm.enabled=false \
  --set vss-rtvi-embed.kafkaEnabled=false \
  --set vss-rtvi-embed.waitForKafka.enabled=false \
  --set vss-rtvi-embed.kafkaTopic=mdx-embed \
  --set vss-rtvi-embed.hfTokenSecret.name=hf-token-secret \
  --set vss-rtvi-embed.hfTokenSecret.key=HF_TOKEN \
  --wait --timeout 45m
```

Notes:

- Ensure **`hf-token-secret`** exists in **`${NAMESPACE}`** (umbrella install does not create secrets). For image pull, either create **`ngc-docker-reg-secret`** (default in subchart `values.yaml`) or add `--set-json 'vss-rtvi-embed.imagePullSecrets=[{"name":"ngc-image-pull-secret"}]'` to match section 2.
- **`kafkaTopic`** defaults to **`mdx-embed`** in `values.yaml`; standalone overrides keep the same topic for later Kafka integration.
- The Deployment sets **`ERROR_MESSAGE_TOPIC=vision-embed-errors`** (hardcoded env today); output topic for Kafka is **`KAFKA_TOPIC`** / **`kafkaTopic`**.
- First startup can take **many minutes** (HF model download + Triton model repo; `startupProbe` in `values.yaml` allows a long initial delay).

---

## 5. Wait for Deployment and verify

Wait for the embed pod to become ready (model download + engine build on first run):

```bash
kubectl rollout status deployment/vss-rtvi-embed -n "${NAMESPACE}" --timeout=45m
```

Confirm resources:

```bash
kubectl get pods,svc,pvc -n "${NAMESPACE}" -o wide
```

Follow logs (container **`vss-rtvi-embed`**):

```bash
kubectl logs -f deployment/vss-rtvi-embed -n "${NAMESPACE}" --tail=100
```

HTTP readiness: **`GET /v1/ready`** on port **8000**. Port-forward and probe:

```bash
kubectl port-forward -n "${NAMESPACE}" svc/vss-rtvi-embed 8000:8000
curl -sS "http://127.0.0.1:8000/v1/ready"
```

---

## 6. Standalone vs full stack

| Mode | `kafkaEnabled` | `waitForKafka` | Typical use |
|------|----------------|----------------|-------------|
| **Standalone** (`overrides_rtvi_embed.yaml`) | `false` | `false` | Dev GPU cluster; HTTP embed only |
| **Full VSS** (default `values.yaml` + umbrella) | `true` | `true` | Search pipeline; topic **`mdx-embed`** |

Do **not** leave **`waitForKafka.enabled: true`** without a reachable **`kafkaBootstrapServers`**; the init container will block startup.

---

## 7. Uninstall and clean PVC / data

### 7.1 Uninstall Helm release

```bash
helm uninstall "${RELEASE}" --namespace "${NAMESPACE}"
```

### 7.2 Remove model cache PVCs (optional, for a full data wipe)

Default PVC names:

- **`vss-rtvi-embed-rtvi-ngc-cache`**
- **`vss-rtvi-embed-rtvi-hf-cache`**

```bash
kubectl delete pvc vss-rtvi-embed-rtvi-ngc-cache vss-rtvi-embed-rtvi-hf-cache \
  -n "${NAMESPACE}" --wait=true
```

If the PVC name differs (prefix / override), list first:

```bash
kubectl get pvc -n "${NAMESPACE}" | grep rtvi-embed
```

### 7.3 Remove secrets (optional)

```bash
kubectl delete secret ngc-image-pull-secret hf-token-secret -n "${NAMESPACE}" --ignore-not-found
```

### 7.4 Remove the namespace (optional, destructive)

```bash
kubectl delete namespace "${NAMESPACE}"
```

---

## 7. Troubleshooting

- **Pod `Pending`**: insufficient **`nvidia.com/gpu`** or missing device plugin — `kubectl describe pod -n "${NAMESPACE}"`.
- **`ImagePullBackOff`**: check **`ngc-image-pull-secret`**, image repository/tag, registry reachability.
- **Stuck in init `wait-for-kafka`**: use **`overrides_rtvi_embed.yaml`** or set **`waitForKafka.enabled: false`**.
- **HF / model errors**: verify **`hf-token-secret`** / **`HF_TOKEN`**; check pod logs during Cosmos-Embed1 download.
- **Slow ready on first install**: expected; keep **`--timeout 45m`** on `helm upgrade --wait` or inspect **`startupProbe`** in `values.yaml`.
- **Wrong values applied**: `helm get values "${RELEASE}" -n "${NAMESPACE}"` and confirm **`kafkaTopic`**, **`modelPath`**, image tag.

---

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.  
SPDX-License-Identifier: Apache-2.0
