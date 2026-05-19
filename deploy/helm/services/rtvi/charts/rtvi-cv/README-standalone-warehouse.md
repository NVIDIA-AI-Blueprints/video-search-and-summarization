# Standalone warehouse: `standalone-2d` and `standalone-3d`

This document describes a **from-scratch** install of the **`vss-rtvi-cv`** subchart under the **`rtvi`** umbrella using **`profileMode`** **`standalone-2d`** or **`standalone-3d`**: DeepStream perception with **file** sources from the NGC **`vss-warehouse-app-data`** bundle on a shared PVC. **Kafka and Redis are not used** in these profiles (FakeSink / `STREAM_TYPE=none`).

For chart internals (templates, ConfigMaps, jobs), see `charts/rtvi-cv/`.

---

## Prerequisites

- Kubernetes cluster with **NVIDIA GPU** nodes and the NVIDIA device plugin (workload requests `nvidia.com/gpu: 1`).
- **`helm`** (v3) with network access to pull images (`nvcr.io`, `docker.io`, …).
- **NGC CLI API key** in a Secret (default key name below). The key must be allowed to download the registry **resource** configured in `ngcAppDataResourceVersion`.
- A **StorageClass** for RWO volumes (or leave `persistence.storageClass` empty to use the cluster default).
- Optional: **`ngc-docker-reg-secret`** (or equivalent) if your cluster requires pull secrets for private images (`imagePullSecrets`).

---

## 1. Variables (set once per shell)

Pick a Helm **release name**, **namespace**, and ensure the NGC secret exists in that namespace.

```bash
export RELEASE="vss-standalone"
export NAMESPACE="vss-standalone"
export PROFILE="standalone-2d"   # or: standalone-3d
```

**Important:** with default `charts/rtvi-cv` helpers, Kubernetes **object names** for the workload are **`vss-rtvi-cv`** (StatefulSet, PVC, Job prefix), derived from the subchart **`Chart.name`**, **not** from the umbrella Helm **`RELEASE`** name. Labels such as `app.kubernetes.io/instance` **do** match the Helm release. If you set `global.useReleaseNamePrefix: true` or `fullnameOverride`, object names change—use `kubectl get sts,job,pvc -n "${NAMESPACE}"` to confirm.

---

## 2. Namespace and NGC secret (from scratch)

```bash
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
```

Create a Secret with the NGC API key. Defaults in `charts/rtvi-cv/values.yaml` expect **`Secret` name `ngc-api`**, key **`NGC_CLI_API_KEY`**:

```bash
kubectl create secret generic ngc-api \
  --namespace "${NAMESPACE}" \
  --from-literal=NGC_CLI_API_KEY='<your-ngc-api-key>' \
  --dry-run=client -o yaml | kubectl apply -f -
```

If you already use another secret name or key (for example `ngc-api-key-secret` / `NGC_API_KEY`), pass `vss-rtvi-cv.ngcApiKeySecretName` and `vss-rtvi-cv.ngcApiKeySecretKey` in Helm values or `--set` flags to match.

---

## 3. Helm chart path and dependencies

From the repository root (or any directory), the umbrella chart lives at:

`deploy/helm/services/rtvi`

If you use packaged dependencies instead of `file://` subcharts, run once:

```bash
cd deploy/helm/services/rtvi
helm dependency update
```

### 3.1 Install only the `vss-rtvi-cv` subchart (optional)

From `deploy/helm/services/rtvi/charts/rtvi-cv`, use **flattened** values (no `vss-rtvi-cv.` prefix), for example:

```bash
cd deploy/helm/services/rtvi/charts/rtvi-cv
helm upgrade --install "${RELEASE}" . \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --set enabled=true \
  --set profileMode="${PROFILE}" \
  --set downloadNgcAppData=true \
  --set downloadModelsFromNgc=false
```

---

## 4. Install (`standalone-2d` or `standalone-3d`)

Minimal install: enable **`vss-rtvi-cv`**, set **`profileMode`**, turn on **NGC app data download**, and size the models PVC. Adjust **`persistence.storageClass`** and **`persistence.models.size`** for your cluster.

```bash
cd deploy/helm/services/rtvi

helm upgrade --install "${RELEASE}" . \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --set vss-rtvi-cv.enabled=true \
  --set vss-rtvi-cv.profileMode="${PROFILE}" \
  --set vss-rtvi-cv.downloadNgcAppData=true \
  --set vss-rtvi-cv.downloadModelsFromNgc=false \
  --set vss-rtvi-cv.persistence.models.size=80Gi \
  --set vss-rtvi-cv.persistence.storageClass='' \
  --set-string vss-rtvi-cv.ngcAppDataOrg=nvstaging \
  --set-string vss-rtvi-cv.ngcAppDataResourceVersion=nvstaging/vss-warehouse/vss-warehouse-app-data:v3.2.0-04282026
```

Notes:

- **`downloadNgcAppData=true`** creates Job **`vss-rtvi-cv-download-ngc-app-data`**, which downloads and extracts the bundle onto PVC mount path **`vss-warehouse-app-data/`** and writes marker **`vss-warehouse-app-data/.ngc-extracted`**.
- **`downloadModelsFromNgc=false`** skips the separate NGC model download Job; standalone 2D/3D warehouse assets are expected from the app-data bundle. Set to **`true`** and populate **`ngcModelsToDownload`** only if you intentionally add extra models.
- Override **`vss-rtvi-cv.ngcAppDataResourceVersion`** / **`ngcAppDataOrg`** when NVIDIA publishes a newer bundle.
- **`standaloneWarehouse.*`** (Sparse4D paths, `streamType`, DeepStream flags) can be overridden with `--set` or a small values file.

---

## 5. Wait for NGC Job and StatefulSet

Wait for the download Job to complete (it may take many minutes on first run). With **default** subchart naming the Job is:

`vss-rtvi-cv-download-ngc-app-data`

```bash
kubectl wait --for=condition=complete "job/vss-rtvi-cv-download-ngc-app-data" \
  --namespace "${NAMESPACE}" \
  --timeout=3600s
```

Confirm names if you use `fullnameOverride` / `useReleaseNamePrefix`:

```bash
kubectl get jobs -n "${NAMESPACE}" -l app.kubernetes.io/instance="${RELEASE}"
```

Wait for the perception pod to be ready:

```bash
kubectl rollout status statefulset/vss-rtvi-cv -n "${NAMESPACE}" --timeout=600s
```

Follow logs (container **`vss-rtvi-cv`**):

```bash
kubectl logs -f "statefulset/vss-rtvi-cv" -n "${NAMESPACE}" -c vss-rtvi-cv
```

HTTP probe (if enabled): **`httpPort`** defaults to **9000** inside the pod.

---

## 6. Profiles at a glance

| `profileMode`    | Workload | `DS_MODEL_FAMILY` (env)   | Notes |
|-----------------|----------|---------------------------|--------|
| `standalone-2d` | RT-DETR warehouse + file cams | `rtdetr-warehouse` | 3 synthetic cameras from bundle. |
| `standalone-3d` | Sparse4D warehouse + file cams | `sparse4d-warehouse` | 4 cams; ONNX/NPY from PVC paths in `standaloneWarehouse`. |

Do **not** set `profileMode` to `alerts` or `search` in the same release if you intend this document’s flow; those modes use different StatefulSet templates (Kafka wait, different configs).

---

## 7. Uninstall and clean PVC / data

### 7.1 Uninstall Helm release

```bash
helm uninstall "${RELEASE}" --namespace "${NAMESPACE}"
```

### 7.2 Remove the models PVC (optional, for a full data wipe)

The standalone workload uses **`PersistentVolumeClaim`** **`vss-rtvi-cv-models`** (default name). Deleting it removes downloaded NGC data on the next install.

```bash
kubectl delete pvc vss-rtvi-cv-models -n "${NAMESPACE}" --wait=true
```

If the PVC name differs (prefix / override), list first:

```bash
kubectl get pvc -n "${NAMESPACE}" | grep rtvi-cv
```

### 7.3 Remove the NGC secret (optional)

```bash
kubectl delete secret ngc-api -n "${NAMESPACE}"
```

### 7.4 Remove the namespace (optional, destructive)

```bash
kubectl delete namespace "${NAMESPACE}"
```

### 7.5 Finished Jobs

The NGC download Job sets **`ttlSecondsAfterFinished`**; completed Jobs may disappear automatically. If a Job is stuck, delete it manually:

```bash
kubectl delete job -n "${NAMESPACE}" -l app.kubernetes.io/instance="${RELEASE}" --ignore-not-found
```

---

## 8. Troubleshooting (short)

- **Pod `Init:0/1` waiting on NGC**: Job not complete or marker missing — check Job logs:  
  `kubectl logs job/vss-rtvi-cv-download-ngc-app-data -n "${NAMESPACE}"` (adjust name if prefixed).
- **Permission errors writing TensorRT engines**: chart uses writable **`/opt/storage/trt-cache`** on the PVC; ensure the init container **`ensure-trt-cache`** ran.
- **Wrong profile rendered**: `helm get values "${RELEASE}" -n "${NAMESPACE}"` and confirm **`vss-rtvi-cv.profileMode`**.

---

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.  
SPDX-License-Identifier: Apache-2.0
