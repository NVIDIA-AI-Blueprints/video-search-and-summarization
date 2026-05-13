<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and limitations under the License.

-->

# VSS Helm Chart (Smart Cities profile)

Helm chart for deploying **VSS Smart Cities Profile** on Kubernetes. This profile adds smartcities capabilities on top of the core alerts stack, including camera calibration, road-network-aware behavior analytics, and a dedicated video analytics UI with map/dashboard views.

## Modes

The default values file matches **verification** mode:

| File | Mode |
|------|------|
| `values-verification.yaml` | Verification |

## GPU requirements

With default **`values.yaml`** and the verification values file (both NIMs enabled; verification enables **perception**), the stack requests **4 GPUs** (`nvidia.com/gpu: 1` each).

### Smart Cities verification (`values-verification.yaml`)

| Workload | GPU |
|----------|-----|
| `vss-rtvi-cv` | 1 |
| `vss-vios-streamprocessing` | 1 |
| `nvidia-cosmos-reason2-8b` (NIM) | 1 |
| `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
| **Total** | **4** |

## Prerequisites

- **Kubernetes cluster**
  - Running cluster whose API you can reach with **`kubectl`** (correct context and, if applicable, kubeconfig).
  - **Server version** validated for this profile: **1.34** — use a different minor/patch only if your platform or release notes require it.

- **NVIDIA GPU Operator**
  - Install the GPU Operator on the cluster. Follow [GPU Operator getting started](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html).

- **NVIDIA NIM Operator**
  - Required when **`nims`** subcharts are enabled (`NIMCache` / `NIMService`).
  - Install **after** the GPU Operator. See [NIM Operator installation](https://docs.nvidia.com/nim-operator/latest/install.html).

- **Volume provisioner (e.g. local-path)**
  - A **StorageClass** must exist on the cluster.
  - **Bare-metal clusters:** install **local-path** (see [rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner/tree/master)).
  - **Cloud clusters:** use your provider's block storage class instead of local-path.

### Chart / tooling

- **Helm** 3.x
- **Kubectl**
- **GPUs**: see [GPU requirements](#gpu-requirements) (4 with defaults).
- **NVIDIA NIM** (if using NIM subcharts): NIM Operator on the cluster (see [Prerequisites](#prerequisites) above).
- **NGC**: API key for NIM, image pull / chart secret creation (see below).
- **StorageClass** : Need a storageclass present on cluster for PVC creation
- **Shared services**: this chart uses shared services from **`helm/services/`** (infra, vios, agent, ui, alert, analytics, rtvi, nims). Before **`helm install`** / **`helm package`**, run **`helm dependency build`** in this chart directory (uses **`Chart.lock`** to populate **`charts/*.tgz`**, which are gitignored). Use **`helm dependency update`** if **`Chart.lock`** is missing or you changed **`Chart.yaml`** dependencies.

## Smart Cities specific services

In addition to the shared services, this chart includes services specific to Smart Cities:

| Service | Description | Default Port |
|---------|-------------|-------------|
| **`calibration-toolkit`** | Camera calibration service for geo-referenced coordinate systems | 8003 |
| **`video-analytics-ui`** | ITS dashboard and map UI for traffic monitoring | 3002 |
| **`import-calibration`** | Post-install hook job that uploads calibration and road-network data to the video-analytics API | — |

These services are defined under **`services/`** within this chart directory.

> **Note:** The `import-calibration` job reads `calibration.json` and `road-network.json` from `configs/behavior-analytics/` (shared with behavior-analytics). Edit those files to change calibration or road-network data for both services.

## Quick start

### 1. Prepare the values file

Edit **`values-verification.yaml`** and set the following (all are required for a typical install):

| Key | Description |
|-----|-------------|
| **`ngc.apiKey`** | Your NGC API key (for image pull and NIM). Chart uses `ngc.createSecrets: true` by default. |
| **`global.storageClass`** | StorageClass name in your cluster (e.g. `oci-bv-high`, `gp3`, `standard`). |
| **`global.externalScheme`** | `http` or `https` (defaults to `http` in templates if unset). |
| **`global.externalHost`** | Hostname or IP the browser uses (e.g. `vss-smc.YOUR_IP.nip.io`). |
| **`global.externalPort`** | Port segment in generated URLs; use **`""`** for default 80/443. |
| **`global.kibanaPublicUrl`** | Full public Kibana base URL (no `/kibana` suffix). |
| **`video-analytics-ui.googleMapsApiKey`** | Google Maps API key for the map view (optional, leave empty to disable map tiles). |

#### Mode values files vs chart `values.yaml`

| File | Role |
|------|------|
| **`values-verification.yaml`** | Your values override for **verification** mode: **`ngc.apiKey`**, **`global.*`**, **`nims`**, **`vssIngress`**, and any flag overrides. Pass with **`-f values-verification.yaml`** (only one **`-f`** for this chart install). |

##### Key differences from Alerts profile

| Feature | Alerts | Smart Cities |
|---------|--------|-------------|
| **Profile** | `alerts` | `smartcities` |
| **UI Subtitle** | "Vision (Alerts - CV)" | "Smart Cities" |
| **Chat Workflow** | "VSS Agent" | "Smart Cities Agent" |
| **Dashboard Tab** | Disabled | Enabled |
| **Map Tab** | Disabled | Enabled |
| **File Upload** | Disabled | Enabled |
| **Behavior Analytics** | `dev_example` app | ITS app with calibration + road network |
| **Agent Config** | General incident reporting | ITS routing agent with geo tools, FOV charts, places |
| **RTDETR Labels** | bicycle, car, person | two_wheeler, Vehicle, Person |
| **Calibration** | N/A | calibration-toolkit service |
| **Analytics UI** | N/A | video-analytics-ui (ITS dashboard) |

##### Optional overrides — `values.yaml` keys (reference)

| Key / group | Default | Description |
|-------------|---------|-------------|
| **`profile`** | **`smartcities`** | Must stay **`smartcities`** for this chart. |
| **`mode`** | **`verification`** | Set according to mode you are deploying. |
| **`ngc.createSecrets`** | **`true`** | When **`true`** and **`ngc.apiKey`** is set, the chart creates two secrets: **`ngc-api`** (Opaque) and **`ngc-secret`** (dockerconfigjson). |
| **`global.imagePullSecrets`** | **`[{ name: ngc-secret }]`** | Pod **image pull** credentials for nvcr.io. |
| **`global.externalScheme`** | **`""`** | `http` or `https` for browser-facing URLs. |
| **`global.externalHost`** | **`""`** | Hostname or IP clients use in the browser. |
| **`calibration-toolkit.enabled`** | **`true`** | Set **`false`** to disable the calibration toolkit. |
| **`calibration-toolkit.persistence.size`** | **`5Gi`** | PVC size for calibration data. |
| **`video-analytics-ui.enabled`** | **`true`** | Set **`false`** to disable the video analytics UI. |
| **`video-analytics-ui.appName`** | **`its`** | Application name for the UI config mount path. |
| **`video-analytics-ui.googleMapsApiKey`** | **`""`** | Google Maps API key for map tiles. |
| **`vssIngress.calibrationPort`** | **`8003`** | Backend port for calibration toolkit ingress path. |
| **`vssIngress.videoAnalyticsUiPort`** | **`3002`** | Backend port for video analytics UI ingress path. |
| **`analytics.vss-behavior-analytics.command`** | ITS app | Uses `apps/its/main_its_app.py` with `--calibration` flag. |
| **`agent.vss-agent.profile`** | **`smartcities`** | Passed to vss-agent subchart for Smart Cities UX. |
| **`agent.vss-agent.reportTemplates.enabled`** | **`true`** | Enables ITS incident report template mounting. |
| **`import-calibration.enabled`** | **`true`** | Runs a post-install job to upload calibration + road-network data to the video-analytics API. |

For shared service overrides (infra, vios, agent, UI, alert-bridge, analytics, rtvi, nims), refer to the [Alerts profile README](../developer-profiles/dev-profile-alerts/README.md) — the same keys apply.

### 2. Install

```bash
# From the repository root
cd deploy/helm

helm dependency build ./smartcities
```

**Verification mode:**

```bash
# Set the minimum required values inline to install the chart
export NGC_CLI_API_KEY='<your NGC API key>'
export STORAGE_CLASS='<Storage Class Name>'
export EXTERNAL_HOST='<EXTERNAL_HOST_IP>'

helm upgrade --install vss-smc ./smartcities \
  -f ./smartcities/values-verification.yaml \
  -n vss-smc \
  --create-namespace \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost="vss-smc.$EXTERNAL_HOST.nip.io" \
  --set global.storageClass="$STORAGE_CLASS"

# OR — verification with remote LLM/VLM (no in-cluster NIMs)
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-smc ./smartcities \
  -f ./smartcities/values-verification.yaml \
  -n vss-smc \
  --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost="vss-smc.$EXTERNAL_HOST.nip.io" \
  --set global.storageClass="$STORAGE_CLASS" \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nvidia/cosmos-reason2-8b"
```

## Exposing the stack

**Note:** After install or upgrade, wait until **all** pods in your namespace are **Ready** before using the UI. When **in-cluster NIM** is enabled, **NIM** workloads need extra time. The stack also runs **Kafka**, **Elasticsearch**, **NVStreamer**, **calibration-toolkit**, and (in verification mode) **vss-alert-bridge** and **vss-rtvi-cv**; these can take many minutes. Use **`kubectl get pods -n <NAMESPACE>`** (or **`-w`**) until workloads are **Running**.

Set **`global.externalHost`** and **`global.kibanaPublicUrl`** (and scheme/port) in your mode values file so browser URLs resolve.

### VSS Ingress (`vssIngress`)

The chart can create a Kubernetes **`Ingress`** (**`templates/vss-ingress.yaml`**) with path rules for **vss-agent-ui**, **vss-agent**, **vss-vios-ingress**, **`/video-analytics-api`**, **`/alert-bridge`**, **`/calibration`**, **`/analytics-ui`**, and optional hosts for **Kibana**, **Phoenix**, and **NVStreamer** when those subcharts are enabled.

**Prerequisites**

1. An **Ingress controller** must already be installed; **`vssIngress.ingressClassName`** (default **`haproxy`**) must match its **`IngressClass`**.
2. **`global.externalHost`** must be set unless **`vssIngress.host`** overrides the main hostname.
3. **`metadata.annotations`** include **`haproxy.org/path-rewrite`** for path-prefix stripping (HAProxy-compatible Ingress).

**Minimal values** (controller already on cluster)

```yaml
global:
  externalHost: "vss-smc.YOUR_IP.nip.io"
  externalScheme: "http"
  kibanaPublicUrl: "http://kibana.vss-smc.YOUR_IP.nip.io"
vssIngress:
  enabled: true
  ingressClassName: haproxy
  host: ""
```

### Example: HAProxy and Ingress

**1. Install HAProxy Kubernetes Ingress controller** (once per cluster):

```bash
helm repo add haproxytech https://haproxytech.github.io/helm-charts
helm repo update

helm upgrade --install haproxy-kubernetes-ingress haproxytech/kubernetes-ingress \
  --version 1.49.0 \
  -n haproxy-controller --create-namespace \
  --set controller.kind=DaemonSet \
  --set controller.service.enabled=false \
  --set controller.daemonset.useHostPort=true \
  --set controller.daemonset.hostPorts.http=80 \
  --set controller.daemonset.hostPorts.https=443
```

**2. Install or upgrade this chart** with **`vssIngress.enabled: true`**, **`vssIngress.ingressClassName: haproxy`**, and **`global.externalHost`** set.

**3. Optional — manual Ingress:** edit **`vss-ingress-example.yaml`** and **`vss-ingress-example-rewrites.yaml`** (**`RELEASE_NAME`**, **`NAMESPACE`**, **`EXTERNAL_HOST`**), then:

```bash
kubectl apply -f vss-ingress-example.yaml -f vss-ingress-example-rewrites.yaml -n <NAMESPACE>
```

## Upgrade and uninstall

**Upgrade**

```bash
helm upgrade <RELEASE_NAME> ./smartcities -f smartcities/values-verification.yaml -n <NAMESPACE>
```

**Uninstall**:

```bash
helm uninstall <RELEASE_NAME> -n <NAMESPACE>
```

Note: PVCs and any cluster-scoped resources (nimcache) are not removed by `helm uninstall`; delete them manually if needed.

```bash
kubectl delete nimcache --all -n <NAMESPACE>
kubectl delete pvc --all -n <NAMESPACE>
```
