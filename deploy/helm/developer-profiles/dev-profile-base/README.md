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

With default **`values.yaml`** and typical overrides (both NIMs enabled, **`vss-vios-streamprocessing`** running), the stack requests **3 GPUs** (`nvidia.com/gpu: 1` each). Pod names include the Helm release name and a replica hash; the table lists the **workload** substring from `kubectl get pods`.

| Workload | GPU |
|----------|-----|
| `nvidia-cosmos-reason2-8b` (NIM) | 1 |
| `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
| `vss-vios-streamprocessing` | 1 |
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
- **Shared VIOS umbrella**: the **`vios`** chart at **`helm/services/vios/`** bundles all **`vss-vios-*`** microservice charts as subcharts. Run **`helm dependency update`** in this profile directory before **`helm install`** / **`helm package`**. To validate all developer profiles and remove generated **`charts/*.tgz`** afterward, run **`python3 -m eval.scripts.infra.validate_developer_profile_helm`** from the repo root (same as the preflight CI job).


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
| **`llmNameSlug`** | Slug for the in-cluster **LLM** service (default **`nvidia-nemotron-nano-9b-v2`**, from shared **`helm/services/nims`**). Keep **`agent.vss-agent.llmName`** aligned with the same NGC model id. |
| **`vlmNameSlug`** | Slug for the in-cluster **VLM** service (default **`nvidia-cosmos-reason2-8b`**). Keep **`agent.vss-agent.vlmName`** aligned with the same NGC model id. |
| **`nims`** | Shared umbrella **`helm/services/nims`**: **`nims.enabled`**, **`nims.gpuType`**, **`nims.nemotron`**, **`nims.cosmos`** (**`nims.cosmos.enabled`** toggles the VLM NIM), **`nims.global`**. Set **`nims.enabled`** to **`false`** when using [remote LLM/VLM](#remote-llm-and-vlm) only. |
| **`global.llmBaseUrl`** / **`global.vlmBaseUrl`** (remote) | HTTP(S) base URLs for LLM and VLM when they are **not** deployed by this chart (OpenAI-compatible or NIM endpoints reachable from **vss-agent** pods). Use with **`nims.enabled: false`**. Leave **`""`** when serving models from in-cluster **NIM** subcharts. |
| **`global.llmName`** / **`global.vlmName`** (remote) | Model identifiers (e.g. **`nvidia/nvidia-nemotron-nano-9b-v2`**, **`nvidia/nvidia-cosmos-reason2-8b`**) the agent should use; must match what the remote endpoints expose. Shipped defaults in **`values-base.yaml`** match common NGC model ids. |
| **`vssIngress`** (optional) | Set **`vssIngress.enabled`** to **`true`** to create a Kubernetes **`Ingress`** for UI, agent, VST, and (when Phoenix is enabled) Phoenix under one hostname. Requires an **IngressClass** that already exists on the cluster (see [VSS Ingress (`vssIngress`)](#vss-ingress-vssingress)). **`global.externalHost`** must be set unless you set **`vssIngress.host`**. **`values-base.yaml`** enables this by default; set **`enabled: false`** if you use port-forward, **`NodePort`**, or a custom Ingress only. |

#### `values-base.yaml` vs chart `values.yaml`

| File | Role |
|------|------|
| **`values-base.yaml`** | **Your** small override file: fill required keys (NGC, StorageClass, external host, NIM slugs and **`nims`** hardware—or **`global.llmBaseUrl`** / **`global.vlmBaseUrl`** and **`nims.enabled: false`** for remote models) and anything else you change. Pass it with **`-f values-base.yaml`**. |
| **`values.yaml`** | **Chart defaults** shipped with the profile (full value tree). You normally **do not** edit it; add only the keys you need to your override file ( values-base.yaml) and Helm merges your file on top of these defaults. |

Use the table below when you want to change behavior beyond the minimal **`values-base.yaml`** fields. Defaults described here match the chart’s **`values.yaml`** in this repository.

##### Optional overrides — `values.yaml` keys (reference)

| Key / group | Default | Description |
|-------------|---------|-------------|
| **`mode`** | `""` | "" for dev-profile-base chart. |
| **`llmNameSlug`** | `""` | In-cluster LLM service slug (default **`nvidia-nemotron-nano-9b-v2`**). Set in **`values-base.yaml`** if you change models. |
| **`vlmNameSlug`** | `""` | In-cluster VLM service slug (default **`nvidia-cosmos-reason2-8b`**). Set in **`values-base.yaml`** if you change models. |
| **`ngc.createSecrets`** | `true` | When **`true`** and **`ngc.apiKey`** is set, the chart creates two secrets (see **`templates/ngc-secrets.yaml`**): **`ngc-api`** (Opaque: **`NGC_API_KEY`** / **`NGC_CLI_API_KEY`**) for NGC API access, and **`ngc-secret`** (**dockerconfigjson**) for pulling images from nvcr.io. Set **`false`** only if you create both secrets yourself; then set **`global.ngcApiSecret`** and **`global.imagePullSecrets`** to match your names. |
| **`ngc.apiKey`** | `""` | With **`ngc.createSecrets: true`**, set your NGC API key here; it backs both created secrets. With **`createSecrets: false`**, omit (or leave empty) and install the Opaque + docker secrets out of band; align **`global.*`** below with those objects. Optional: **`ngc.apiKeySecretName`** / **`ngc.dockerSecretName`** rename the generated secrets—update **`global.ngcApiSecret.name`** and **`global.imagePullSecrets`** accordingly. |
| **`global.imagePullSecrets`** | `[{ name: ngc-secret }]` | Pod **image pull** credentials for nvcr.io. Must reference the **Docker registry** secret (default **`ngc-secret`**, i.e. **`ngc.dockerSecretName`**). This is separate from the NGC **API** key secret. |
| **`global.ngcApiSecret`** | `name: ngc-api`, `key: NGC_API_KEY` | Tells NIM (**`NIMService`** / **`NIMCache`**) and related workloads which **Opaque** secret holds the NGC **API** key: **`name`** defaults to **`ngc-api`** (**`ngc.apiKeySecretName`**), **`key`** defaults to **`NGC_API_KEY`** (the key the chart writes in that secret). Change these if you use a different secret name or data key. |
| **`global.externalScheme`** | `""` in defaults | Set in **`values-base.yaml`** (e.g. **`http`** or **`https`**). With **`externalHost`** / **`externalPort`**, builds browser-facing URLs for **`vss-agent-ui`**, **`vss-agent`**, and **`vss-vios-ingress`** when their own URL fields are empty. |
| **`global.externalHost`** | `""` in defaults | Hostname or IP clients use in the browser (e.g. **`vss.YOUR_IP.nip.io`**). |
| **`global.externalPort`** | `""` in defaults | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`global.storageClass`** | unset in default **`values.yaml`** | Set in **`values-base.yaml`**; used to create PVC. |
| **`global.llmBaseUrl`** | `""` | Remote LLM API base URL for **vss-agent** when models are not in-cluster (use with **`nims.enabled: false`**). Must be reachable from pods in the release namespace (cluster DNS, **`NodePort`**, LB, or routable IP). |
| **`global.vlmBaseUrl`** | `""` | Remote VLM API base URL; same constraints as **`global.llmBaseUrl`**. |
| **`global.llmName`** | e.g. **`nvidia/nvidia-nemotron-nano-9b-v2`** | Catalog-style model id passed to the agent; must match the model served at **`global.llmBaseUrl`**. |
| **`global.vlmName`** | e.g. **`nvidia/nvidia-cosmos-reason2-8b`** | Catalog-style model id passed to the agent; must match the model served at **`global.vlmBaseUrl`**. |
| **`vios.vstStorage.createSharedPvcs`** | `true` | **`true`:** the **`vios`** umbrella creates **PersistentVolumeClaims** so **sensor** and **streamprocessing** share on-disk folders for VST data and video; data survives pod restarts but your cluster must have a working **`StorageClass`** (see **`global.storageClass`**). **`false`:** no shared PVCs from **`vios`**—pods use emptyDir or per-subchart PVCs depending on **`vios.vss-vios-*`** persistence. **`false`** avoids disk provisioning but **uploaded video and VST cache are lost** when pods are rescheduled if nothing else persists them. |
| **`vios.vstStorage.accessMode`** | **`ReadWriteOnce`** | Access mode for the three shared VST PVCs (see **`helm/services/vios/templates/vst-storage-pvc.yaml`**). |
| **`vios.vstStorage.vstData`** | **`size`:** **10Gi**, **`storageClass`:** `""` | Claim size for the shared **VST data** volume. Leave **`storageClass`** empty to inherit **`global.storageClass`**; set it only if this volume needs a different class than the rest of the chart. |
| **`vios.vstStorage.vstVideo`** | **`size`:** **20Gi**, **`storageClass`:** `""` | Claim size for the shared **VST video** volume; same **`storageClass`** rules as **`vstData`**. |
| **`vios.vstStorage.streamerVideos`** | **`size`:** **20Gi**, **`storageClass`:** `""` | Claim size for the shared **streamer upload** video volume; same **`storageClass`** rules as **`vstData`**. |
| **`infra.enabled`** | `true` | Master switch for the **`infra`** umbrella (Phoenix, Redis, …). |
| **`infra.phoenix.enabled`** | `true` | Set **`false`** to disable Phoenix only. |
| **`infra.redis.enabled`** | `true` | Set **`false`** to disable Redis only. |
| **`vios.enabled`** | `true` | Master switch for the **`vios`** umbrella (all bundled **`vss-vios-*`** subcharts). Set **`false`** to omit the entire VST microservice stack from the release. |
| **`vios.vss-vios-postgres.enabled`** | `true` | Set **`false`** to disable centralized DB. Storage sizing/class: subchart **`values.yaml`** or overrides under **`vios.vss-vios-postgres`**. |
| **`vios.vss-vios-envoy-proxy.enabled`** | `true` | Set **`false`** to disable Envoy in front of streamprocessing. |
| **`vios.vss-vios-sdr.enabled`** | `true` | Set **`false`** to disable SDR streamprocessing. |
| **`vios.vss-vios-sensor.enabled`** | `true` | **`false`** to disable **vss-vios-sensor**. |
| **`vios.vss-vios-sensor.persistence`** | Each of **`vstData`** and **`vstVideo`**: mount on, **`create: false`**, **`existingClaim`** empty by default | Controls whether **sensor** mounts two shared folders (**data** and **video**). **Typical setup:** leave **`existingClaim`** blank—Helm wires the pods to the PVCs created when **`vios.vstStorage.createSharedPvcs`** is **`true`**. **Custom PVCs:** set **`existingClaim`** to your claim name for that volume. **Disable a mount:** set that volume’s **`enabled`** to **`false`** (that path is not mounted). |
| **`vios.vss-vios-streamprocessing.enabled`** | `true` | **`false`** to disable **vss-vios-streamprocessing**. |
| **`vios.vss-vios-streamprocessing.persistence`** | **`vstData`**, **`vstVideo`**, **`streamerVideos`**: same idea as sensor | **Streamprocessing** mounts up to **three** shared folders: VST **data**, VST **video**, and **streamer** uploads. Use blank **`existingClaim`** to use the shared PVCs from **`vios`** (when **`vios.vstStorage.createSharedPvcs`** is **`true`**), or set **`existingClaim`** / **`enabled`** per volume the same way as for **sensor**. |
| **`vios.vss-vios-ingress.enabled`** | `true` | Deploys the in-cluster **VST ingress** (nginx). |
| **`vios.vss-vios-ingress.externallyAccessibleIp`** | `""` | Hostname or IP address advertised to VST/nginx for external access. If unset, the subchart uses **`global.externalHost`**; if that is unset, it defaults to **`127.0.0.1`**. Override this value only when the VST ingress must use a hostname or IP that differs from **`global.externalHost`**. |
| **`vssIngress.enabled`** | `false` in chart **`values.yaml`**; **`true`** in sample **`values-base.yaml`** | When **`true`**, renders **`templates/vss-ingress.yaml`**: one **`Ingress`** routing **`/`** and **`/api/chat`** to **vss-agent-ui**, **`/api`**, **`/chat`**, **`/websocket`**, **`/static`** to **vss-agent**, **`/vst`** to **vss-vios-ingress**, and (if Phoenix is enabled) **`phoenix.<host>/`** to Phoenix. No effect if **`global.externalHost`** and **`vssIngress.host`** are both empty. |
| **`vssIngress.ingressClassName`** | `haproxy` | **`spec.ingressClassName`** on the **`Ingress`**. Must match an **`IngressClass`** that already exists (for example the class created by **HAProxy Kubernetes Ingress**). Use another name (e.g. **`nginx`**) if your controller uses a different class. |
| **`vssIngress.host`** | `""` | Ingress hostname rule. If empty, **`global.externalHost`** is used. Set only when the Ingress hostname must differ from **`global.externalHost`**. |
| **`vssIngress.vssUiPort`** | `3000` | Backend **`Service`** port for **vss-agent-ui** paths. |
| **`vssIngress.vssAgentPort`** | `8000` | Backend **`Service`** port for **vss-agent** paths. |
| **`vssIngress.vstIngressPort`** | `30888` | Backend **`Service`** port for **vss-vios-ingress** (**`/vst`**). |
| **`vssIngress.phoenixHost`** | `""` | Second rule host for Phoenix. If empty, defaults to **`phoenix.<global.externalHost or vssIngress.host>`**. |
| **`vssIngress.phoenixPort`** | `6006` | Backend **`Service`** port for Phoenix when the Phoenix subchart is enabled. |
| **`vios.vss-vios-mcp.enabled`** | `true` | Set **`false`** to disable VST MCP dev. |
| **`agent.enabled`** | `true` | Set **`false`** to skip the **`agent`** umbrella (**`deploy/helm/services/agent`**). |
| **`agent.vss-agent.enabled`** | `true` | Set **`false`** to disable the **vss-agent** deployment only. |
| **`agent.vss-agent.mountConfigEdge`** / **`mountEvalOutput`** | `true` / `true` | Parent **ConfigMap** includes **`config_edge.yml`** when the file exists; **`/vss-agent/eval-output`** emptyDir when **`mountEvalOutput`** is **`true`**. Agent YAML lives at **`configs/vss-agent/config.yml`** (flat path, no profile subfolders). |
| **`agent.vss-agent.llmName`** | NGC model id (e.g. **`nvidia/nvidia-nemotron-nano-9b-v2`**) | NGC catalog id for the LLM; must match the model deployed under **`nims`**. |
| **`agent.vss-agent.vlmName`** | NGC model id (e.g. **`nvidia/nvidia-cosmos-reason2-8b`**) | NGC catalog id for the VLM; must match the model deployed under **`nims`**. |
| **`agent.vss-agent.evalLlmJudgeName`** | `""` | Optional eval judge model id. When empty, the **vss-agent** subchart defaults to **`llmName`**. |
| **`agent.vss-agent.evalLlmJudgeBaseUrl`** | `""` | Optional base URL for the eval judge endpoint. When empty, the subchart defaults alongside **`llmBaseUrl`**. |
| **`agent.vss-agent.reportsBaseUrl`** | `""` | Base URL for report links. When empty, templates derive a value from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.vstExternalUrl`** | `""` | External **VST** URL passed to the agent. When empty, derived from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.externalIp`** | `""` | Hostname or IP override for agent-facing external access when **`global.external*`** is not sufficient. |
| **`agent.vss-agent.env`** | *(see **`values.yaml`**)* | **Full** container env list for vss-agent (Option B). Each **`value`** is passed through Helm **`tpl`**, so URLs can reference **`.Values`**, **`.Release`**, and **`global`** keys. Defaults mirror **`deploy/helm/services/agent/charts/agent/values.yaml`**. Use **`agent.vss-agent.extraEnv`** from the subchart only if you need late overrides (secrets / ad‑hoc vars). |
| **`agent.vss-agent.extraEnv`** | *(omit)* | Optional **`{ name, value }`** appended last (same pattern as **vss-agent-ui.extraEnv**). |
| **`vss-agent-ui.enabled`** | `true` | Set **`false`** to disable the **vss-agent-ui** deployment. |
| **`vss-agent-ui.agentApiUrlBase`** | `""` | Base URL for the **vss-agent** HTTP API (browser **`NEXT_PUBLIC_AGENT_API_URL_BASE`**, typically ends with **`/api/v1`**). If unset, built from **`global.externalScheme`** / **`externalHost`** / **`externalPort`** as **`<global>/api/v1`**, else defaults to in-cluster **`http://<release>-vss-agent:8000/api/v1`**. |
| **`vss-agent-ui.vstApiUrl`** | `""` | **VST** HTTP API URL for the browser (**`NEXT_PUBLIC_VST_API_URL`**). If unset, built as **`<global>/vst/api`**, else **`http://<release>-vss-vios-ingress:30888/vst/api`**. |
| **`vss-agent-ui.chatCompletionUrl`** | `""` | HTTP chat completion URL (**`NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL`**). If unset, built as **`<global>/chat/stream`**, else **`http://<release>-vss-agent:8000/chat/stream`**. |
| **`vss-agent-ui.websocketChatUrl`** | `""` | WebSocket chat URL (**`NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL`**). If unset and **`global.externalHost`** is set, built as **`<ws-scheme>://<host>[:port]/websocket`** ( **`ws`** / **`wss`** from **`global.externalScheme`**). If both this and **`global.externalHost`** are empty, the chart may omit WebSocket env vars; set explicitly for port-forward or custom routing. |
| **`vss-agent-ui.appSubtitle`** | `""` | Optional; sets **`NEXT_PUBLIC_APP_SUBTITLE`** via merge when **`envOverrides`** does not already define that name (see **vss-agent-ui** **`deployment.yaml`**). Base defaults live in **`values.yaml`** **`envOverrides`**. |
| **`vss-agent-ui.enableDashboardTab`** | `""` | Optional; sets **`NEXT_PUBLIC_ENABLE_DASHBOARD_TAB`** when **`envOverrides`** does not already define that name. Base defaults live in **`values.yaml`** **`envOverrides`**. |
| **`vss-agent-ui.envOverrides`** | base defaults in **`values.yaml`** | List of **`{ name, value }`** merged into the subchart **`env`** list by variable name (same pattern as **dev-profile-alerts**). |
| **`vss-agent-ui.extraEnv`** | `[]` | List of **`{ name, value }`** appended last in the container **`env`** block (override or add any **`NEXT_PUBLIC_*`** without a ConfigMap). |
| **`vss-agent-ui.staticEnvConfigMapName`** | `""` | Optional **`envFrom`** **`ConfigMap`** name (you supply the **`ConfigMap`**). **`extraEnvFrom`** is also supported on the subchart. |
| **`nims.enabled`** | `true` | Master switch for the **`nims`** umbrella (**`helm/services/nims`**). When **`false`**, no **NIM** **`NIMService`** / **`NIMCache`** objects are installed. Use **`false`** with **`global.llmBaseUrl`**, **`global.vlmBaseUrl`**, **`global.llmName`** and **`global.vlmName`** for remote-only LLM/VLM. |
| **`nims.gpuType`** | **`H100`** | Selects **`gpuProfiles`** tuning for **`nemotron`** / **`cosmos`** **`nim-env`** ConfigMaps (**`H100`**, **`L40S`**, **`RTXPRO6000BW`**). |
| **`nims.nemotron` / `nims.cosmos`** | see **`values.yaml`** | Per-model **`enabled`**, images, resources, storage, and **`env`**. Align with **`llmNameSlug`**, **`vlmNameSlug`**, and **`agent.vss-agent.llmName`** / **`vlmName`**. |

### Remote LLM and VLM

When LLM and VLM run **outside** this release (another cluster service, **NIM** on a different node pool, or HTTP endpoints on your network), disable bundled **NIM** subcharts and set **`global`** URLs and model names in **`values-base.yaml`** (or via **`--set`**):

- **`nims.enabled`**: **`false`** — skips **NIM** workloads and related **NIMOperator** objects.
- **`global.llmBaseUrl`** / **`global.vlmBaseUrl`**: base URLs reachable from **vss-agent** pods (no trailing path required beyond what your API expects; use the same scheme/host/port the agent can resolve).
- **`global.llmName`** / **`global.vlmName`**: identifiers for those models (e.g. **`nvidia/nvidia-nemotron-nano-9b-v2`**, **`nvidia/nvidia-cosmos-reason2-8b`**), aligned with the remote service.

You can still set **`llmNameSlug`** / **`vlmNameSlug`** for chart wiring where applicable, or rely on **`values-base.yaml`** placeholders when not using in-chart **NIM** charts. Optional overrides on **vss-agent** (**`agent.vss-agent.llmBaseUrl`**, **`agent.vss-agent.vlmBaseUrl`**, etc.) exist if the agent must differ from **`global.*`**.

### 2. Install

```bash
# Clone the repository. For a specific branch or tag, add: -b <name-or-tag> (before the URL).
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deploy/helm/developer-profiles


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
  --set vlmNameSlug=nvidia-cosmos-reason2-8b \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS"

# OR — in-cluster VSS with remote LLM/VLM (no NIM subcharts); URLs must be reachable from vss-agent pods
# (reuse NGC_CLI_API_KEY, STORAGE_CLASS, EXTERNAL_HOST exports from the example above)
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-base ./dev-profile-base \
  -f dev-profile-base/values-base.yaml \
  -n vss-base --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS" \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nvidia/cosmos-reason2-8b"
```

## Exposing the stack

**Note:** After install or upgrade, wait until **all** pods in your namespace are **Ready** before using the application in the browser. When **in-cluster NIM** is enabled (**`nims.enabled: true`**, the usual default), **NIM** model workloads need **extra time** (image pull, **`NIMService`** / **`NIMCache`**, model download and warm-up). Opening **vss-agent-ui** while NIM or other backends are still starting can produce **transient errors** (failed API calls, timeouts, empty screens). Check **`kubectl get pods -n <NAMESPACE>`** (or **`kubectl get pods -n <NAMESPACE> -w`**) until every workload shows **`Running`** and **`READY`** matches the expected column (e.g. **`1/1`**). With **remote** LLM/VLM only (**`nims.enabled: false`**), startup is often faster, but still confirm all pods are ready.

To expose VSS through a single hostname, set **`global.externalHost`** (and **`global.externalScheme`** / **`global.externalPort`** as needed) in **`values-base.yaml`** as in the table under [Prepare the values file](#1-prepare-the-values-file). That drives in-chart URLs for **vss-agent-ui**, **vss-agent**, and **vss-vios-ingress** when their own URL fields are empty.

### VSS Ingress (`vssIngress`)

The chart can create a single Kubernetes **`Ingress`** (**`templates/vss-ingress.yaml`**) so clients reach UI, API, VST, and Phoenix through one external hostname.

**Prerequisites**

1. An **Ingress controller** must already be installed, and its **`IngressClass`** name must match **`vssIngress.ingressClassName`** (default **`haproxy`** for [HAProxy Kubernetes Ingress](https://github.com/haproxytech/helm-charts/tree/main/kubernetes-ingress)).
2. **`global.externalHost`** must be set to that hostname (for example **`vss.203.0.113.nip.io`**), unless you set **`vssIngress.host`** explicitly.
3. **`vssIngress.enabled`**: **`true`** in the sample **`values-base.yaml`**; set **`false`** if you do not use an Ingress (for example you rely on **`kubectl port-forward`** or a manually applied Ingress—see **`vss-ingress-example.yaml`** in this directory).

**What gets created**

- **`Ingress`** name **`<release>-vss-ingress`** in the release namespace.
- **`spec.ingressClassName`**: from **`vssIngress.ingressClassName`** (default **`haproxy`**).
- Path rules: **`/`**, **`/api/chat`** → **vss-agent-ui**; **`/api`**, **`/chat`**, **`/websocket`**, **`/static`** → **vss-agent**; **`/vst`** → **vss-vios-ingress**; optional second host **`phoenix.<main-host>`** → Phoenix when Phoenix is enabled.

After install, confirm the **`Ingress`** exists (replace **`<NAMESPACE>`** with your release namespace):

```bash
kubectl get ingress -n <NAMESPACE>
```

Expect **`NAME`** **`<RELEASE_NAME>-vss-ingress`** when **`vssIngress.enabled`** is **`true`**.

**Minimal values example** (HAProxy controller already on the cluster)

```yaml
global:
  externalHost: "vss.YOUR_IP.nip.io"
  externalScheme: "http"
vssIngress:
  enabled: true
  ingressClassName: haproxy
  host: ""   # omit to use global.externalHost
```

**Using another controller** (for example NGINX Ingress): set **`vssIngress.ingressClassName`** to that controller’s **`IngressClass`** name. Path-based routing is standard **`Ingress`**; HAProxy-specific annotations are not required for the default template.

**Important:** **`vssIngress`** only creates an **`Ingress`** resource. It does **not** install the HAProxy (or any) Ingress controller. If you also use a Helm chart that installs the **same** **`IngressClass`** (for example a bundled **`kubernetes-ingress`** subchart), disable **one** of the two installs—otherwise Helm reports an ownership conflict on the cluster **`IngressClass`** (often named **`haproxy`**). Prefer a **single** cluster-wide controller install and **`vssIngress.enabled: true`** on this release.

### Example: HAProxy and Ingress

**1. Install HAProxy Kubernetes Ingress controller** (example; adjust for your environment). Do this **once per cluster** (or use your platform’s ingress):

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

**2. Install or upgrade this chart (Optional)** with **`vssIngress.enabled: true`**, **`vssIngress.ingressClassName: haproxy`**, and **`global.externalHost`** set to the hostname clients use (DNS or **`nip.io`** must resolve to your ingress entry point). 

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

