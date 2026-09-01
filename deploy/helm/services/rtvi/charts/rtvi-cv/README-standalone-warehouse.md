# Standalone warehouse: `standalone-2d`, `standalone-3d`, and `standalone-mv3dt`

This document describes a **from-scratch** install of the **`vss-rtvi-cv`** subchart under the **`rtvi`** umbrella using **`profileMode`** **`standalone-2d`**, **`standalone-3d`**, or **`standalone-mv3dt`**. Warehouse videos, playback, and calibration data come from the NGC **`vss-warehouse-app-data`** bundle, while RT-CV models are downloaded separately from versioned NGC model packages onto the same PVC. The 2D/3D standalone profiles use FakeSink / `STREAM_TYPE=none`; MV3DT is normally installed through its warehouse profile chart with Kafka or Redis, Mosquitto, and the BEV-fusion companion.

For chart internals (templates, ConfigMaps, jobs), see `charts/rtvi-cv/`.

---

## Prerequisites

For cluster setup, GPU nodes, Helm, NGC credentials, and storage expectations shared across VSS Helm profiles, see [Prerequisites](../../../../developer-profiles/dev-profile-search/README.md#prerequisites) in the dev-profile-search guide.

- Kubernetes cluster with **NVIDIA GPU** nodes and the NVIDIA device plugin (workload requests `nvidia.com/gpu: 1`).
- **`helm`** (v3) with network access to pull images (`nvcr.io`, `docker.io`, …).
- **NGC API key** in two Secrets (see [section 2](#2-namespace-and-ngc-secrets-from-scratch)): an **Opaque** secret for NGC CLI download Jobs (`ngc-api` / `NGC_CLI_API_KEY` by default) and a **docker-registry** secret for pulling `nvcr.io` images (`ngc-docker-reg-secret`). The key must be allowed to download the registry **resource** configured in `ngcAppDataResourceVersion`.
- A **StorageClass** for RWO volumes (or leave `persistence.storageClass` empty to use the cluster default).


---

## 1. Variables (set once per shell)

Pick a Helm **release name**, **namespace**, and ensure the NGC secrets exist in that namespace.

```bash
export RELEASE="vss-standalone"
export NAMESPACE="vss-standalone"
export PROFILE="standalone-2d"   # or: standalone-3d
```

**Important:** with default `charts/rtvi-cv` helpers, Kubernetes **object names** for the workload are **`vss-rtvi-cv`** (StatefulSet, PVC, Job prefix), derived from the subchart **`Chart.name`**, **not** from the umbrella Helm **`RELEASE`** name. Labels such as `app.kubernetes.io/instance` **do** match the Helm release. If you set `global.useReleaseNamePrefix: true` or `fullnameOverride`, object names change—use `kubectl get sts,job,pvc -n "${NAMESPACE}"` to confirm.

---

## 2. Namespace and NGC secrets (from scratch)

```bash
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
```

Create **two** secrets with the same NGC API key. Defaults in `charts/rtvi-cv/values.yaml`:

| Purpose | Secret name | Type | Data key |
|--------|-------------|------|----------|
| NGC CLI (download Jobs) | `ngc-api` | Opaque | `NGC_CLI_API_KEY` |
| Image pull (`nvcr.io`) | `ngc-docker-reg-secret` | docker-registry | `.dockerconfigjson` |

**1. Opaque secret** (NGC CLI for `job-download-ngc-app-data` and RT-CV `ds-start` model download):

```bash
kubectl create secret generic ngc-api \
  --namespace "${NAMESPACE}" \
  --from-literal=NGC_CLI_API_KEY='<your-ngc-api-key>' \
  --dry-run=client -o yaml | kubectl apply -f -
```

**2. Docker-registry pull secret** (`imagePullSecrets` on Jobs and the StatefulSet):

```bash
kubectl create secret docker-registry ngc-docker-reg-secret \
  --namespace "${NAMESPACE}" \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password='<your-ngc-api-key>' \
  --dry-run=client -o yaml | kubectl apply -f -
```

If you already use other secret names or keys (for example `ngc-api-key-secret` / `NGC_API_KEY` from dev-profile-search), set `vss-rtvi-cv.ngcApiKeySecretName` and `vss-rtvi-cv.ngcApiKeySecretKey` in Helm values or `--set` flags to match. For pull secrets, override `vss-rtvi-cv.imagePullSecrets` or `global.imagePullSecrets`.

---

## 3. Helm chart path and dependencies

From the repository root (or any directory), the umbrella chart lives at:

`deploy/helm/services/rtvi`

If you use packaged dependencies instead of `file://` subcharts, run once:

```bash
cd deploy/helm/services/rtvi
helm dependency update
```

---

## 4. Install (`standalone-2d`, `standalone-3d`, or `standalone-mv3dt`)

Minimal install: enable **`vss-rtvi-cv`**, set **`profileMode`**, turn on both NGC download paths, and size the models PVC. Populate `ngcModelsToDownload` with the profile-specific entries used by the warehouse 2D/3D/MV3DT umbrella chart values. Adjust **`persistence.storageClass`** and **`persistence.models.size`** for your cluster.

```bash
cd deploy/helm/services/rtvi

helm upgrade --install "${RELEASE}" . \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --set vss-rtvi-cv.enabled=true \
  --set vss-rtvi-cv.profileMode="${PROFILE}" \
  --set vss-rtvi-cv.downloadNgcAppData=true \
  --set vss-rtvi-cv.downloadModelsFromNgc=true \
  --set vss-rtvi-cv.persistence.models.size=80Gi \
  --set vss-rtvi-cv.persistence.storageClass='' \
  --set-string vss-rtvi-cv.ngcAppDataOrg=nvidia \
  --set-string vss-rtvi-cv.ngcAppDataResourceVersion=<vss-warehouse-app-data-resource>
```

Notes:

- **`downloadNgcAppData=true`** creates Job **`vss-rtvi-cv-download-ngc-app-data`**, which downloads and extracts the bundle onto PVC mount path **`vss-warehouse-app-data/`** and writes marker **`vss-warehouse-app-data/.ngc-extracted`**. RT-CV does not consume models from this subtree.
- **`downloadModelsFromNgc=true`** enables ds-start phase-0 model download inside the perception container on first start. It writes profile models at flattened paths under `/opt/storage` and creates one completion marker per artifact.
- Override **`vss-rtvi-cv.ngcAppDataResourceVersion`** / **`ngcAppDataOrg`** when NVIDIA publishes a newer bundle.
- **`standaloneWarehouse.*`** (Sparse4D paths, `streamType`, DeepStream flags) can be overridden with `--set` or a small values file.

---

## 5. Wait for NGC Job and StatefulSet

Wait for the app-data download Job to complete (it may take many minutes on first run). With **default** subchart naming the Job is:

`vss-rtvi-cv-download-ngc-app-data`

```bash
kubectl wait --for=condition=complete "job/vss-rtvi-cv-download-ngc-app-data" \
  --namespace "${NAMESPACE}" \
  --timeout=3600s
```

RT-CV model download runs inside the perception container on first start (ds-start phase 0). No separate model download Job is created.

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
| `standalone-2d` | RT-DETR warehouse + file cams | `rtdetr-warehouse` | Videos from app-data; RT-DETR from the model Job. |
| `standalone-3d` | Sparse4D warehouse + file cams | `sparse4d-warehouse` | Videos from app-data; ONNX from the model Job; anchor and labels from chart assets. |
| `standalone-mv3dt` | RT-DETR + MV3DT tracker | Dedicated `ds-start-mv3dt.sh` | App-data remains independent; RT-DETR and BodyPose3DNet come from the model Job; MQTT generation and BEV fusion remain MV3DT-specific. |

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

### 7.3 Remove the NGC secrets (optional)

```bash
kubectl delete secret ngc-api ngc-docker-reg-secret -n "${NAMESPACE}" --ignore-not-found
```

### 7.4 Remove the namespace (optional, destructive)

```bash
kubectl delete namespace "${NAMESPACE}"
```

### 7.5 Finished Jobs

The NGC download Job does not set **`ttlSecondsAfterFinished`** by default so Helm can observe completed Jobs during installs. If a Job is stuck, delete it manually:

```bash
kubectl delete job -n "${NAMESPACE}" -l app.kubernetes.io/instance="${RELEASE}" --ignore-not-found
```

---

## 8. Troubleshooting (short)

- **Pod `Init:0/1` waiting on NGC**: Job not complete or marker missing — check Job logs:  
  `kubectl logs job/vss-rtvi-cv-download-ngc-app-data -n "${NAMESPACE}"` (adjust name if prefixed).
- **Pod waiting for RT-CV models**: models are downloaded during ds-start phase 0 inside the perception container. Check perception container logs for download progress or errors; the startup requires both each artifact and its `.done` marker.
- **Permission errors writing TensorRT engines**: 2D/3D use writable **`/opt/storage/trt-cache`**; MV3DT writes beside RT-DETR and under **`/opt/storage/BodyPose3DNet`**. Ensure the applicable engine-directory init container ran.
- **Wrong profile rendered**: `helm get values "${RELEASE}" -n "${NAMESPACE}"` and confirm **`vss-rtvi-cv.profileMode`**.

---

## SPDX

SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.  
SPDX-License-Identifier: Apache-2.0
