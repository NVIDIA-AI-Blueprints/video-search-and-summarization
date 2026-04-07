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

# VSS Helm Chart (Base profile)

Helm chart for deploying **VSS Base Developer Profile** on Kubernetes.

## GPU requirements

With default **`values.yaml`** and typical overrides (both NIMs enabled, **`streamprocessing-ms-dev`** running), the stack requests **3 GPUs** (`nvidia.com/gpu: 1` each). Pod names include the Helm release name and a replica hash; the table lists the **workload** substring from `kubectl get pods`.

| Workload | GPU |
|----------|-----|
| `cosmos-reason2-8b` (NIM) | 1 |
| `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
| `streamprocessing-ms-dev` | 1 |
| **Total** | **3** |


## Prerequisites

- **Kubernetes cluster**
  - Running cluster whose API you can reach with **`kubectl`** (correct context and, if applicable, kubeconfig).
  - **Server version** validated for this profile: **1.34** — use a different minor/patch only if your platform or release notes require it; confirm compatibility with the [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) and [NIM Operator](https://docs.nvidia.com/nim-operator/latest/install.html) versions you deploy.

- **NVIDIA GPU Operator**
  - Install the GPU Operator on the cluster. Follow [GPU Operator getting started](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html).
  - **Driver (x86 Ubuntu)** — pin via GPU Operator driver settings as appropriate:
    - **580.105.08** (x86 hosts with Ubuntu 24.04)
    - **580.65.06** (x86 hosts with Ubuntu 22.04)

- **NVIDIA NIM Operator**
  - Required when **`nims`** subcharts are enabled (`NIMCache` / `NIMService`).
  - Install **after** the GPU Operator. See [NIM Operator installation](https://docs.nvidia.com/nim-operator/latest/install.html).

- **Volume provisioner (e.g. local-path)**
  - A **StorageClass** must exist on the cluster.
  - **Bare-metal clusters:** Install **local-path** (see [rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner/tree/master)). 
  - **Cloud clusters:** use your provider’s block storage class instead of local-path.

### Chart / tooling

- **Helm** 3.x
- **Kubectl**
- **GPUs**: see [GPU requirements](#gpu-requirements) (3 with defaults).
- **NVIDIA NIM** (if using NIM subcharts): NIM Operator on the cluster (see [Prerequisites](#prerequisites) above).
- **NGC**: API key for NIM, image pull / chart secret creation (see below).
- **StorageClass** : Need a storageclass present on cluster for PVC creation


## Quick start

### 1. Prepare the values file

Create `values-base.yaml` and set the following (all are required for a typical install):

| Key | Description |
|-----|-------------|
| **`ngc.apiKey`** | Your NGC API key (for image pull and NIM). Chart uses `ngc.createSecrets: true` by default.|
| **`global.storageClass`** | StorageClass name in your cluster (e.g. `oci-bv-high`, `gp3`, `standard`). |
| **`global.externalScheme`** | `http` or `https` (defaults to `http` in templates if unset). |
| **`global.externalHost`** | Hostname or IP the browser uses (e.g. `vss.YOUR_IP.nip.io`). Required for a typical external install when subchart URL fields are omitted. |
| **`global.externalPort`** | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`llmNameSlug`** | Replace the placeholder with the subchart name of the **LLM** NIM you enable under **`charts/nims/charts/`** (e.g. `nvidia-nemotron-nano-9b-v2`). Keep **`vss-agent.llmName`** in **`values.yaml`** aligned with the same NGC model. |
| **`vlmNameSlug`** | Replace the placeholder with the subchart name of the **VLM** NIM you enable (e.g. `cosmos-reason2-8b`). Keep **`vss-agent.vlmName`** in **`values.yaml`** aligned with the same NGC model. |
| **`nims`** | **`nims.enabled`**: umbrella for all NIM subcharts. Per model: **`nims.<model>.enabled`** and **`nims.<model>.hardwareProfile`**. **`<model>`** must match a subchart directory (e.g. `cosmos-reason2-8b`). **`hardwareProfile`** value should be from `H100`, `RTXPRO6000BW`, `L40S`. |

#### `values-base.yaml` vs chart `values.yaml`

| File | Role |
|------|------|
| **`values-base.yaml`** | **Your** small override file: fill required keys (NGC, StorageClass, external host, NIM slugs, `nims` hardware) and anything else you change. Pass it with **`-f values-base.yaml`**. |
| **`values.yaml`** | **Chart defaults** shipped with the profile (full value tree). You normally **do not** edit it; add only the keys you need to your override file ( values-base.yaml) and Helm merges your file on top of these defaults. |

Use the table below when you want to change behavior beyond the minimal **`values-base.yaml`** fields. Defaults described here match the chart’s **`values.yaml`** in this repository.

##### Optional overrides — `values.yaml` keys (reference)

| Key / group | Default | Description |
|-------------|---------|-------------|
| **`mode`** | `""` | "" for dev-profile-base chart. |
| **`llmNameSlug`** | `""` | Replace the placeholder with the subchart name of the **LLM** NIM you enable under **`charts/nims/charts/`** (e.g. `nvidia-nemotron-nano-9b-v2`). Set in **`values-base.yaml`**. |
| **`vlmNameSlug`** | `""` | Replace the placeholder with the subchart name of the **VLM** NIM you enable (e.g. `cosmos-reason2-8b`). Set in **`values-base.yaml`**. |
| **`ngc.createSecrets`** | `true` | When **`true`** and **`ngc.apiKey`** is set, the chart creates two secrets (see **`templates/ngc-secrets.yaml`**): **`ngc-api`** (Opaque: **`NGC_API_KEY`** / **`NGC_CLI_API_KEY`**) for NGC API access, and **`ngc-secret`** (**dockerconfigjson**) for pulling images from nvcr.io. Set **`false`** only if you create both secrets yourself; then set **`global.ngcApiSecret`** and **`global.imagePullSecrets`** to match your names. |
| **`ngc.apiKey`** | `""` | With **`ngc.createSecrets: true`**, set your NGC API key here; it backs both created secrets. With **`createSecrets: false`**, omit (or leave empty) and install the Opaque + docker secrets out of band; align **`global.*`** below with those objects. Optional: **`ngc.apiKeySecretName`** / **`ngc.dockerSecretName`** rename the generated secrets—update **`global.ngcApiSecret.name`** and **`global.imagePullSecrets`** accordingly. |
| **`global.imagePullSecrets`** | `[{ name: ngc-secret }]` | Pod **image pull** credentials for nvcr.io. Must reference the **Docker registry** secret (default **`ngc-secret`**, i.e. **`ngc.dockerSecretName`**). This is separate from the NGC **API** key secret. |
| **`global.ngcApiSecret`** | `name: ngc-api`, `key: NGC_API_KEY` | Tells NIM (**`NIMService`** / **`NIMCache`**) and related workloads which **Opaque** secret holds the NGC **API** key: **`name`** defaults to **`ngc-api`** (**`ngc.apiKeySecretName`**), **`key`** defaults to **`NGC_API_KEY`** (the key the chart writes in that secret). Change these if you use a different secret name or data key. |
| **`global.externalScheme`** | `""` in defaults | Set in **`values-base.yaml`** (e.g. **`http`** or **`https`**). With **`externalHost`** / **`externalPort`**, builds browser-facing URLs for **`vss-ui`**, **`vss-agent`**, and **`vst-ingress-dev`** when their own URL fields are empty. |
| **`global.externalHost`** | `""` in defaults | Hostname or IP clients use in the browser (e.g. **`vss.YOUR_IP.nip.io`**). |
| **`global.externalPort`** | `""` in defaults | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`global.storageClass`** | unset in default **`values.yaml`** | Set in **`values-base.yaml`**; used to create PVC. |
| **`vstStorage.createSharedPvcs`** | `true` | **`true`:** the chart creates **PersistentVolumeClaims** so **sensor** and **streamprocessing** share on-disk folders for VST data and video; data survives pod restarts but your cluster must have a working **`StorageClass`** (see **`global.storageClass`**). **`false`:** no PVCs—pods use in-memory/ephemeral storage only, so installs start quickly and need no disk provisioning, but **uploaded video and VST cache are lost** when pods are deleted or rescheduled. |
| **`vstStorage.accessMode`** | **`ReadWriteOnce`** | Access mode for the three shared VST PVCs (see **`templates/vst-storage-pvc.yaml`**). |
| **`vstStorage.vstData`** | **`size`:** **10Gi**, **`storageClass`:** `""` | Claim size for the shared **VST data** volume. Leave **`storageClass`** empty to inherit **`global.storageClass`**; set it only if this volume needs a different class than the rest of the chart. |
| **`vstStorage.vstVideo`** | **`size`:** **20Gi**, **`storageClass`:** `""` | Claim size for the shared **VST video** volume; same **`storageClass`** rules as **`vstData`**. |
| **`vstStorage.streamerVideos`** | **`size`:** **20Gi**, **`storageClass`:** `""` | Claim size for the shared **streamer upload** video volume; same **`storageClass`** rules as **`vstData`**. |
| **`phoenix.enabled`** | `true` | Set **`false`** to disable the Phoenix. |
| **`redis.enabled`** | `true` | Set **`false`** to disable Redis. |
| **`centralizedb-dev.enabled`** | `true` | Set **`false`** to disable centralized DB. Storage sizing/class: subchart **`values.yaml`** or overrides under **`centralizedb-dev`**. |
| **`envoy-streamprocessing.enabled`** | `true` | Set **`false`** to disable Envoy in front of streamprocessing. |
| **`sdr-streamprocessing.enabled`** | `true` | Set **`false`** to disable SDR streamprocessing. |
| **`sensor-ms-dev.enabled`** | `true` | **`false`** to disable **sensor-ms-dev**. |
| **`sensor-ms-dev.persistence`** | Each of **`vstData`** and **`vstVideo`**: mount on, **`create: false`**, **`existingClaim`** empty by default | Controls whether **sensor** mounts two shared folders (**data** and **video**). **Typical setup:** leave **`existingClaim`** blank—Helm wires the pods to the PVCs created when **`vstStorage.createSharedPvcs`** is **`true`**. **Custom PVCs:** set **`existingClaim`** to your claim name for that volume. **Disable a mount:** set that volume’s **`enabled`** to **`false`** (that path is not mounted). |
| **`streamprocessing-ms-dev.enabled`** | `true` | **`false`** to disable **streamprocessing-ms-dev**. |
| **`streamprocessing-ms-dev.persistence`** | **`vstData`**, **`vstVideo`**, **`streamerVideos`**: same idea as sensor | **Streamprocessing** mounts up to **three** shared folders: VST **data**, VST **video**, and **streamer** uploads. Use blank **`existingClaim`** to use the parent chart’s shared PVCs (when **`vstStorage.createSharedPvcs`** is **`true`**), or set **`existingClaim`** / **`enabled`** per volume the same way as for **sensor**. |
| **`vst-ingress-dev.enabled`** | `true` | Deploys the in-cluster **VST ingress** (nginx). |
| **`vst-ingress-dev.externallyAccessibleIp`** | `""` | Hostname or IP address advertised to VST/nginx for external access. If unset, the subchart uses **`global.externalHost`**; if that is unset, it defaults to **`127.0.0.1`**. Override this value only when the VST ingress must use a hostname or IP that differs from **`global.externalHost`**. |
| **`vst-mcp-dev.enabled`** | `true` | Set **`false`** to disable VST MCP dev. |
| **`vss-proxy.enabled`** | `false` | Currently disabled for dev-base-profile chart. |
| **`rtvi-vlm.enabled`** | `false` | Currently disabled for dev-base-profile chart. |
| **`vss-agent.enabled`** | `true` | Set **`false`** to disable the **vss-agent** deployment. |
| **`vss-agent.rtviVlmEnabled`** | `false` | Set **`true`** when **`rtvi-vlm.enabled`** is **`true`** so the agent receives **RTVI** / **VLM** base URLs targeting the **rtvi-vlm** service. |
| **`vss-agent.profile`** | `base` | Selects **`configs/<profile>/config.yml`** from the **vss-agent** subchart package for the agent **ConfigMap**. Keep **`base`** for this chart (**`base`** | **`lvs`** | **`search`** | **`alerts`**). |
| **`vss-agent.llmName`** | NGC model id (e.g. **`nvidia/nvidia-nemotron-nano-9b-v2`**) | NGC catalog id for the LLM; must match the model deployed under **`nims`**. |
| **`vss-agent.vlmName`** | NGC model id (e.g. **`nvidia/cosmos-reason2-8b`**) | NGC catalog id for the VLM; must match the model deployed under **`nims`**. |
| **`vss-agent.evalLlmJudgeName`** | `""` | Optional eval judge model id. When empty, the **vss-agent** subchart defaults to **`llmName`**. |
| **`vss-agent.evalLlmJudgeBaseUrl`** | `""` | Optional base URL for the eval judge endpoint. When empty, the subchart defaults alongside **`llmBaseUrl`**. |
| **`vss-agent.reportsBaseUrl`** | `""` | Base URL for report links. When empty, templates derive a value from **`global.external*`** and in-cluster defaults. |
| **`vss-agent.vstExternalUrl`** | `""` | External **VST** URL passed to the agent. When empty, derived from **`global.external*`** and in-cluster defaults. |
| **`vss-agent.externalIp`** | `""` | Hostname or IP override for agent-facing external access when **`global.external*`** is not sufficient. |
| **`vss-ui.enabled`** | `true` | Set **`false`** to disable the **vss-ui** deployment. |
| **`vss-ui.agentApiUrlBase`** | `""` | Base URL for the **vss-agent** HTTP API (browser **`NEXT_PUBLIC_AGENT_API_URL_BASE`**, typically ends with **`/api/v1`**). If unset, built from **`global.externalScheme`** / **`externalHost`** / **`externalPort`** as **`<global>/api/v1`**, else defaults to in-cluster **`http://<release>-vss-agent:8000/api/v1`**. |
| **`vss-ui.vstApiUrl`** | `""` | **VST** HTTP API URL for the browser (**`NEXT_PUBLIC_VST_API_URL`**). If unset, built as **`<global>/vst/api`**, else **`http://<release>-vst-ingress-dev:30888/vst/api`**. |
| **`vss-ui.chatCompletionUrl`** | `""` | HTTP chat completion URL (**`NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL`**). If unset, built as **`<global>/chat/stream`**, else **`http://<release>-vss-agent:8000/chat/stream`**. |
| **`vss-ui.websocketChatUrl`** | `""` | WebSocket chat URL (**`NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL`**). If unset and **`global.externalHost`** is set, built as **`<ws-scheme>://<host>[:port]/websocket`** ( **`ws`** / **`wss`** from **`global.externalScheme`**). If both this and **`global.externalHost`** are empty, the chart may omit WebSocket env vars; set explicitly for port-forward or custom routing. |
| **`nims.enabled`** | `true` | Master switch for the **`nims`** umbrella subchart. When **`false`**, no **NIM** model workloads or **`NIMService`** / **`NIMCache`** objects are installed.|
| **`nims.<model>.enabled`** | per model in **`values.yaml`** | Enables or disables one bundled **NIM** model. **`<model>`** is the subchart directory name under **`charts/nims/charts/`** (for example **`cosmos-reason2-8b`**, **`nvidia-nemotron-nano-9b-v2`**). Enable only models you deploy; align **`llmNameSlug`**, **`vlmNameSlug`**, and **`vss-agent.llmName`** / **`vlmName`** with the same **NGC** models (see [Prepare the values file](#1-prepare-the-values-file)). |
| **`nims.<model>.hardwareProfile`** | e.g. **`H100`** | Selects the environment block from **`envByHardware`** in **`charts/nims/charts/<model>/values.yaml`** (GPU SKU, sharing, and related **NIM** settings). The value must match a key defined in that map (for example **`H100`**, **`RTXPRO6000BW`**). Use **`""`** to apply only the chart’s default **`env`** section. Choose a profile that matches your **GPU** hardware and the **NIM** deployment pattern you use. |

### 2. Install

```bash
# Clone the repository
git clone -b feat/kubernetes-support https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deployments/helm/developer-profiles

# Update the values-base.yaml and install the chart
helm upgrade --install <RELEASE NAME> ./dev-profile-base \
  -f dev-profile-base/values-base.yaml \
  -n <NAMESPACE> --create-namespace

# For Example: 
helm upgrade --install vss-base ./dev-profile-base \
  -f dev-profile-base/values-base.yaml \
  -n vss-base --create-namespace \

# OR
# Set the minimum required values inline to install the chart
export NGC_CLI_API_KEY='<your NGC API key>'
export STORAGE_CLASS='<Storage Class Name>'
export EXTERNAL_HOST='<EXTERNAL_HOST_IP>'

helm upgrade --install vss-base ./dev-profile-base \
  -f dev-profile-base/values-base.yaml \
  -n vss-base --create-namespace \
  --set llmNameSlug=nvidia-nemotron-nano-9b-v2 \
  --set vlmNameSlug=cosmos-reason2-8b \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS"
```

## Exposing the stack

To expose VSS through a single hostname, set **`global.externalHost`** (and scheme/port as needed) in `values-base.yaml` as in the table under [Prepare the values file](#1-prepare-the-values-file).

### Example: HAProxy and Ingress

**1. Install HAProxy Kubernetes Ingress controller** (example; adjust for your environment):

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

**2. Apply the ingress.** Edit **`vss-ingress-example.yaml`** and **`vss-ingress-example-rewrites.yaml`** in this directory: Update the **RELEASE_NAME**, **NAMESPACE**, **EXTERNAL_HOST** in the files.


```bash
kubectl apply -f dev-profile-base/vss-ingress-example.yaml -f dev-profile-base/vss-ingress-example-rewrites.yaml -n <NAMESPACE>
```

**Note:** How you expose the ingress depends on your **CSP (cloud/service provider)**. You may use a LoadBalancer service or a cloud-specific ingress (e.g. OCI LB, AWS ALB, GKE Ingress). Adjust your configuration based on provider’s documentation.

## Upgrade and uninstall

**Upgrade**:

```bash
helm upgrade <RELEASE_NAME> ./dev-profile-base -f dev-profile-base/values-base.yaml -n <NAMESPACE>
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

