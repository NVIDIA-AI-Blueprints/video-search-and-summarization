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

# VSS Helm Chart (Alerts profile)

Helm chart for deploying **VSS Alerts Developer Profile** on Kubernetes. 

## Modes

Two modes, each with a **single values file** — use only one `-f` when installing:

| File | Mode |
|------|------|
| `values-verification.yaml` | Verification |
| `values-realtime.yaml` | Real-time |

## GPU requirements

With default **`values.yaml`** and the mode values files (both NIMs enabled; verification enables **perception**; real-time enables **vss-rtvi-vlm**), the stack requests **4 GPUs** (`nvidia.com/gpu: 1` each) per mode. Pod names include the Helm release name and a replica hash; below lists the **workload** you will see in `kubectl get pods`.

### Alert verification (`values-verification.yaml`)

| Workload | GPU |
|----------|-----|
| `vss-rtvi-cv` | 1 |
| `vss-vios-streamprocessing` | 1 |
| `nvidia-cosmos-reason2-8b` (NIM) | 1 |
| `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
| **Total** | **4** |

### Alert real-time (`values-realtime.yaml`)

| Workload | GPU |
|----------|-----|
| `vss-vios-streamprocessing` | 1 |
| `nvidia-cosmos-reason2-8b` (NIM) | 1 |
| `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
| `vss-rtvi-vlm` | 1 |
| **Total** | **4** |


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
  - **Bare-metal clusters:** install **local-path** (see [rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner/tree/master)).
  - **Cloud clusters:** use your provider’s block storage class instead of local-path.

### Chart / tooling

- **Helm** 3.x
- **Kubectl**
- **GPUs**: see [GPU requirements](#gpu-requirements) (4 per mode with defaults).
- **NVIDIA NIM** (if using NIM subcharts): NIM Operator on the cluster (see [Prerequisites](#prerequisites) above).
- **NGC**: API key for NIM, image pull / chart secret creation (see below).
- **StorageClass** : Need a storageclass present on cluster for PVC creation
- **Shared VIOS umbrella**: the **`vios`** chart at **`helm/services/vios/`** bundles the canonical **`vss-vios-*`** microservice charts as subcharts. Before **`helm install`** / **`helm package`**, run **`helm dependency build`** in this profile directory (uses **`Chart.lock`** to populate **`charts/*.tgz`**, which are gitignored). Use **`helm dependency update`** if **`Chart.lock`** is missing or you changed **`Chart.yaml`** dependencies. To lint after vendoring: from **`deploy/helm/developer-profiles`**, run **`helm dependency build ./dev-profile-alerts`** then **`helm lint ./dev-profile-alerts`**. Remove generated **`charts/*.tgz`** from the profile directory if you do not want vendored tarballs in your working tree.

## Quick start

### 1. Prepare the values file

Edit **`values-verification.yaml`** or **`values-realtime.yaml`** (use **one** **`-f`** per install) and set the following (all are required for a typical install):

| Key | Description |
|-----|-------------|
| **`ngc.apiKey`** | Your NGC API key (for image pull and NIM). Chart uses `ngc.createSecrets: true` by default. |
| **`global.storageClass`** | StorageClass name in your cluster (e.g. `oci-bv-high`, `gp3`, `standard`). |
| **`global.externalScheme`** | `http` or `https` (defaults to `http` in templates if unset). |
| **`global.externalHost`** | Hostname or IP the browser uses (e.g. `vss-alerts.YOUR_IP.nip.io`). Required for a typical external install when subchart URL fields are omitted. |
| **`global.externalPort`** | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`global.kibanaPublicUrl`** | Full public Kibana base URL (no `/kibana` suffix). Used for the Dashboard tab and Kibana `SERVER_PUBLICBASEURL` when subchart `kibana.kibanaPublicUrl` is empty. |
| **`nims`** | **`nims.enabled`**: umbrella for all NIM subcharts. Per model: **`nims.<model>.enabled`** and **`nims.<model>.hardwareProfile`**. **`<model>`** must match a subchart directory (e.g. `nvidia-cosmos-reason2-8b`). Set **`nims.enabled`** to **`false`** when using [remote LLM/VLM](#remote-llm-and-vlm) only. |
| **`global.llmBaseUrl`** / **`global.vlmBaseUrl`** (remote) | HTTP(S) base URLs when LLM/VLM are **not** deployed in-cluster; use with **`nims.enabled: false`**. Must be reachable from **vss-agent** (and **vss-alert-bridge** / **vss-rtvi-vlm** as applicable). Align **`global.llmName`** / **`global.vlmName`** with remote endpoints. |
| **`global.llmName`** / **`global.vlmName`** (remote) | Model identifiers for **vss-agent** and related services; must match remote APIs. |
| **`vssIngress`** (optional) | Set **`vssIngress.enabled`** to **`true`** to create a Kubernetes **`Ingress`** for UI, agent, VST, **video-analytics-api**, **vss-alert-bridge**, and optional **Kibana** / **Phoenix** / **NVStreamer** hosts. Requires an existing **IngressClass** (see [VSS Ingress (`vssIngress`)](#vss-ingress-vssingress)). **`global.externalHost`** must be set unless **`vssIngress.host`** is set. Mode sample files enable this by default. |

#### Mode values files vs chart `values.yaml`

| File | Role |
|------|------|
| **`values-verification.yaml`** | Your values override for **verification** mode: **`ngc.apiKey`**, **`global.*`**, **`nims`**, **`vssIngress`**, and any flag overrides. Pass with **`-f values-verification.yaml`** (only one **`-f`** for this chart install). |
| **`values-realtime.yaml`** | Your values override for **real-time** mode: same classes of keys as verification; repo defaults enable **vss-rtvi-vlm** and adjust components for real-time alerts. Pass with **`-f values-realtime.yaml`** (only one **`-f`** for this chart install). |

##### Optional overrides — `values.yaml` keys (reference)

In **Description**, **Real-time (`values-realtime.yaml`)** notes which subcharts that file turns **`enabled: false`** so you know those keys are **not required** for **real-time mode** installs (verification still uses them as those are by default enabled in chart's values.yaml file).

| Key / group | Default | Description |
|-------------|---------|-------------|
| **`profile`** | **`alerts`** | Must stay **`alerts`** for this chart. |
| **`mode`** | **`verification`** | valid values are **verification** and **real-time**, set according to mode you are deploying |
| **`ngc.createSecrets`** | **`true`** | When **`true`** and **`ngc.apiKey`** is set, the chart creates two secrets (see **`templates/ngc-secrets.yaml`**): **`ngc-api`** (Opaque: **`NGC_API_KEY`** / **`NGC_CLI_API_KEY`**) for NGC API access, and **`ngc-secret`** (**dockerconfigjson**) for pulling images from nvcr.io. Set **`false`** only if you create both secrets yourself; then set **`global.ngcApiSecret`** and **`global.imagePullSecrets`** to match your names. |
| **`ngc.apiKey`** | **`""`** | With **`ngc.createSecrets: true`**, set your NGC API key here; it backs both created secrets. With **`createSecrets: false`**, omit (or leave empty) and install the Opaque + docker secrets out of band; align **`global.*`** below with those objects. Optional: **`ngc.apiKeySecretName`** / **`ngc.dockerSecretName`** rename the generated secrets—update **`global.ngcApiSecret.name`** and **`global.imagePullSecrets`** accordingly. |
| **`global.imagePullSecrets`** | **`[{ name: ngc-secret }]`** | Pod **image pull** credentials for nvcr.io. Must reference the **Docker registry** secret (default **`ngc-secret`**, i.e. **`ngc.dockerSecretName`**). This is separate from the NGC **API** key secret. |
| **`global.ngcApiSecret`** | **`name: ngc-api`**, **`key: NGC_API_KEY`** | Tells NIM (**`NIMService`** / **`NIMCache`**) and related workloads which **Opaque** secret holds the NGC **API** key: **`name`** defaults to **`ngc-api`** (**`ngc.apiKeySecretName`**), **`key`** defaults to **`NGC_API_KEY`** (the key the chart writes in that secret). Change these if you use a different secret name or data key. |
| **`global.externalScheme`** | **`""`** | Set in **`values-verification.yaml`** or **`values-realtime.yaml`** (e.g. **`http`** or **`https`**). With **`externalHost`** / **`externalPort`**, builds browser-facing URLs for **`vss-agent-ui`**, **`vss-agent`**, **`vss-vios-ingress`**, **vss-alert-bridge**, and related services when their own URL fields are empty. |
| **`global.externalHost`** | **`""`** | Hostname or IP clients use in the browser (e.g. **`vss-alerts.YOUR_IP.nip.io`**). |
| **`global.externalPort`** | **`""`** | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`global.kibanaPublicUrl`** | **`""`** | Public Kibana base URL (no **`/kibana`** path suffix). Prefer this over duplicating **`kibana.kibanaPublicUrl`** unless Kibana must use a different host than the main UI. |
| **`global.storageClass`** | unset in repo **`values.yaml`** | Set in **`values-verification.yaml`** or **`values-realtime.yaml`**; used to create PVC. |
| **`global.llmBaseUrl`** | **`""`** | Remote LLM base URL for **vss-agent** when models are not in-cluster (use with **`nims.enabled: false`**). Must be reachable from pods in the release namespace. |
| **`global.vlmBaseUrl`** | **`""`** | Remote VLM base URL; same constraints. **vss-alert-bridge** and **vss-rtvi-vlm** may use separate overrides when enabled. |
| **`global.llmName`** | e.g. **`nvidia/nvidia-nemotron-nano-9b-v2`** | Catalog-style model id for **vss-agent**; align with **`global.llmBaseUrl`** when remote. |
| **`global.vlmName`** | e.g. **`nvidia/cosmos-reason2-8b`** | Same for VLM. |
| **`vios.vstStorage.createSharedPvcs`** | **`true`** | **`true`:** the **`vios`** umbrella creates **PersistentVolumeClaims** so **sensor** and **streamprocessing** share on-disk folders for VST data and video; data survives pod restarts but your cluster must have a working **StorageClass** (see **`global.storageClass`**). **`false`:** no shared PVCs from **`vios`**; behavior depends on **`vios.vss-vios-*`** persistence settings. |
| **`vios.vstStorage.accessMode`** | **`ReadWriteOnce`** | Access mode for the three shared VST PVCs (see **`helm/services/vios/templates/vst-storage-pvc.yaml`**). |
| **`vios.vstStorage.vstData`** | **`size`:** **10Gi**, **`storageClass`:** **`""`** | Claim size for the shared **VST data** volume. Leave **`storageClass`** empty to inherit **`global.storageClass`**; set it only if this volume needs a different class than the rest of the chart. |
| **`vios.vstStorage.vstVideo`** | **`size`:** **20Gi**, **`storageClass`:** **`""`** | Claim size for the shared **VST video** volume; same **`storageClass`** rules as **`vstData`**. |
| **`vios.vstStorage.streamerVideos`** | **`size`:** **20Gi**, **`storageClass`:** **`""`** | Claim size for the shared **streamer upload** video volume; same **`storageClass`** rules as **`vstData`**. |
| **`infra.phoenix.enabled`** | **`true`** | Set **`false`** to disable Phoenix ( **`infra`** subchart). |
| **`redis.enabled`** | **`true`** | Set **`false`** to disable Redis. |
| **`vios.enabled`** | **`true`** | Master switch for the **`vios`** umbrella (all bundled **`vss-vios-*`** subcharts). Set **`false`** to omit the entire VST microservice stack from the release. |
| **`vios.vss-vios-postgres.enabled`** | **`true`** | Set **`false`** to disable centralized DB. Storage sizing/class: subchart **`values.yaml`** or overrides under **`vios.vss-vios-postgres`**. |
| **`vios.vss-vios-envoy-proxy.enabled`** | **`true`** | Set **`false`** to disable Envoy in front of streamprocessing. |
| **`vios.vss-vios-sdr.enabled`** | **`true`** | Set **`false`** to disable SDR streamprocessing. |
| **`vios.vss-vios-sensor.enabled`** | **`true`** | Set **`false`** to disable the **sensor** workload. |
| **`vios.vss-vios-sensor.persistence`** | Each of **`vstData`** and **`vstVideo`**: mount on, **`create: false`**, **`existingClaim`** empty by default | Controls whether **sensor** mounts two shared folders (**data** and **video**). **Typical setup:** leave **`existingClaim`** blank—Helm wires the pods to the PVCs created when **`vios.vstStorage.createSharedPvcs`** is **`true`**. **Custom PVCs:** set **`existingClaim`** to your claim name for that volume. **Disable a mount:** set that volume’s **`enabled`** to **`false`** (that path is not mounted). |
| **`vios.vss-vios-streamprocessing.enabled`** | **`true`** | Set **`false`** to disable **vss-vios-streamprocessing**. |
| **`vios.vss-vios-streamprocessing.useSdrEnvoyStyleHeadless`** | **`false`** | Alerts: networking style for streamprocessing / SDR integration; override only if your deployment doc requires headless Envoy-style wiring. |
| **`vios.vss-vios-streamprocessing.persistence`** | **`vstData`**, **`vstVideo`**, **`streamerVideos`**: same idea as sensor | **Streamprocessing** mounts up to **three** shared folders: VST **data**, VST **video**, and **streamer** uploads. Use blank **`existingClaim`** to use the shared PVCs from **`vios`** (when **`vios.vstStorage.createSharedPvcs`** is **`true`**), or set **`existingClaim`** / **`enabled`** per volume the same way as for **sensor**. |
| **`vios.vss-vios-ingress.enabled`** | **`true`** | Deploys the in-cluster **VST ingress** (nginx). |
| **`vios.vss-vios-ingress.externallyAccessibleIp`** | **`""`** | Hostname or IP address advertised to VST/nginx for external access. If unset, the subchart uses **`global.externalHost`**; if that is unset, it defaults to **`127.0.0.1`**. Override this value only when the VST ingress must use a hostname or IP that differs from **`global.externalHost`**. |
| **`vssIngress.enabled`** | **`true`** in chart defaults and mode sample files | When **`true`**, renders **`templates/vss-ingress.yaml`**: main host routes for **vss-agent-ui**, **vss-agent**, **vss-vios-ingress**, **`/video-analytics-api`**, **`/alert-bridge`** (with HAProxy path-rewrite annotations), optional **Kibana** / **Phoenix** / **NVStreamer** hosts. |
| **`vssIngress.ingressClassName`** | **`haproxy`** | **`spec.ingressClassName`**. Must match an **`IngressClass`** on the cluster. **`metadata.annotations`** use **`haproxy.org/path-rewrite`** for **HAProxy** Ingress; other controllers may need different annotations or manual YAML (**`vss-ingress-example*.yaml`**). |
| **`vssIngress.host`** | **`""`** | Main Ingress hostname; if empty, **`global.externalHost`** is used. |
| **`vssIngress.vssUiPort`** | **`3000`** | Backend port for **vss-agent-ui** paths. |
| **`vssIngress.vssAgentPort`** | **`8000`** | Backend port for **vss-agent** paths. |
| **`vssIngress.vstIngressPort`** | **`30888`** | Backend port for **vss-vios-ingress** (**`/vst`**). |
| **`vssIngress.videoAnalyticsApiPort`** | **`8081`** | Backend port for **vss-video-analytics-api** (**`/video-analytics-api`**). |
| **`vssIngress.alertBridgePort`** | **`9080`** | Backend port for **vss-alert-bridge** (**`/alert-bridge`**). |
| **`vssIngress.kibanaHost`** / **`phoenixHost`** / **`streamerHost`** | **`""`** | Optional host overrides; defaults **`kibana.`** / **`phoenix.`** / **`streamer.`** + main host. |
| **`vssIngress.kibanaPort`** | **`5601`** | Kibana **Service** port. |
| **`vssIngress.phoenixPort`** | **`6006`** | Phoenix **Service** port. |
| **`vssIngress.streamerPort`** | **`31000`** | **NVStreamer** **Service** port when **`vios.vss-vios-nvstreamer.enabled`** is **`true`**. |
| **`vios.vss-vios-mcp.enabled`** | **`true`** | Set **`false`** to disable VST MCP dev. |
| **`agent.enabled`** | **`true`** | Set **`false`** to skip the **`agent`** umbrella (**`deploy/helm/services/agent`**: **vss-agent** and optional **vss-va-mcp**). |
| **`agent.vss-agent.enabled`** | **`true`** | Set **`false`** to disable the **vss-agent** deployment only. |
| **`agent.vss-agent.profile`** | **`alerts`** | Passed to the **vss-agent** subchart so it mounts **report-templates** and sets template env for the **alerts** UX. ConfigMap data is read from **`configs/vss-agent/config.yml`** (and **`incident_report_template.md`**) in this chart — flat paths, no profile subfolders. |
| **`agent.vss-agent.mountEvalOutput`** | **`false`** | Shared **vss-agent** chart defaults **`mountEvalOutput: true`** (eval **emptyDir**); alerts sets **`false`** to match **met-blueprints** alerts (no eval mount). |
| **`agent.vss-agent.llmName`** | NGC model id (e.g. **`nvidia/nvidia-nemotron-nano-9b-v2`**) | NGC catalog id for the LLM; must match the model deployed under **`nims`**. |
| **`agent.vss-agent.vlmName`** | NGC model id (e.g. **`nvidia/cosmos-reason2-8b`**) | NGC catalog id for the VLM; must match the model deployed under **`nims`**. |
| **`agent.vss-agent.evalLlmJudgeName`** | **`""`** | Optional eval judge model id. When empty, the **vss-agent** subchart defaults to **`llmName`**. |
| **`agent.vss-agent.evalLlmJudgeBaseUrl`** | **`""`** | Optional base URL for the eval judge endpoint. When empty, the subchart defaults alongside **`llmBaseUrl`**. |
| **`agent.vss-agent.reportsBaseUrl`** | **`""`** | Base URL for report links. When empty, templates derive a value from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.vstExternalUrl`** | **`""`** | External **VST** URL passed to the agent. When empty, derived from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.externalIp`** | **`""`** | Hostname or IP override for agent-facing external access when **`global.external*`** is not sufficient. |
| **`agent.vss-agent.env`** | *(see **`values.yaml`**)* | Full **`env`** list (Option B), including **`VSS_ES_PORT=9200`**, **`VSS_AGENT_TEMPLATE_*`** for mounted report templates, and **`waitForDependencies`** for startup ordering. |
| **`agent.vss-agent.waitForDependencies.enabled`** | **`true`** in **`values.yaml`** | When **`true`**, an **initContainer** waits until the **vss-va-mcp** workload (TCP **9901**) accepts connections before **vss-agent** starts when **MCP** is enabled, avoiding **`ConnectError`** during FastAPI startup. Set **`false`** to skip. Override **`waitForDependencies.vaMcpHost`** / **`vaMcpPort`** if **`VIDEO_ANALYSIS_MCP_URL`** uses a different host. |
| **`agent.vss-agent.extraEnv`** | *(omit)* | Optional **`{ name, value }`** appended last. |
| **`vss-agent-ui.enabled`** | **`true`** | Set **`false`** to disable the **vss-agent-ui** deployment. |
| **`vss-agent-ui.fillAlertBridgeUrlFromGlobal`** | **`true`** | When **`true`**, **Alerts** tab can use **`alertsApiUrl`** / global wiring to **vss-alert-bridge**. **Real-time (`values-realtime.yaml`):** **`false`** because **vss-alert-bridge** is **`enabled: false`** **Alerts** tab uses **MDX** web API URLs instead (see **vss-agent-ui** templates). |
| **`vss-agent-ui.agentApiUrlBase`** | **`""`** | Base URL for the **vss-agent** HTTP API (browser **`NEXT_PUBLIC_AGENT_API_URL_BASE`**, typically ends with **`/api/v1`**). If unset, built from **`global.externalScheme`** / **`externalHost`** / **`externalPort`** as **`<global>/api/v1`**, else defaults to in-cluster **`http://<release>-vss-agent:8000/api/v1`**. |
| **`vss-agent-ui.vstApiUrl`** | **`""`** | **VST** HTTP API URL for the browser (**`NEXT_PUBLIC_VST_API_URL`**). If unset, built as **`<global>/vst/api`**, else **`http://<release>-vss-vios-ingress:30888/vst/api`**. |
| **`vss-agent-ui.chatCompletionUrl`** | **`""`** | HTTP chat completion URL (**`NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL`**). If unset, built as **`<global>/chat/stream`**, else **`http://<release>-vss-agent:8000/chat/stream`**. |
| **`vss-agent-ui.websocketChatUrl`** | **`""`** | WebSocket chat URL (**`NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL`**). If unset and **`global.externalHost`** is set, built as **`<ws-scheme>://<host>[:port]/websocket`** (**`ws`** / **`wss`** from **`global.externalScheme`**). If both this and **`global.externalHost`** are empty, the chart may omit WebSocket env vars; set explicitly for port-forward or custom routing. |
| **`vss-agent-ui.alertsApiUrl`** | **`""`** | Browser **Alerts API** base URL when **vss-alert-bridge** exposure differs from other globals. |
| **`vss-agent-ui.appSubtitle`** | **`"Vision (Alerts - CV)"`** in **`values.yaml`**; **`"Vision (Alerts - VLM)"`** in **`values-realtime.yaml`** | Subtitle for the UI (**`NEXT_PUBLIC_APP_SUBTITLE`**); **verification** mode (2d_cv) vs **real-time** (2d_vlm), aligned with **met-blueprints** **dev-profile-alerts**. |
| **`vss-agent-ui.enableDashboardTab`** | **`"false"`** | Dashboard tab toggle; same default as **met-blueprints** embedded **vss-agent-ui** chart. |
| **`vss-agent-ui.envOverrides`** | short list | Alerts-only **`NEXT_PUBLIC_*`** deltas aligned with **met-blueprints** **`dev-profile-alerts/values.yaml`** (workflow, **Alerts** tab, upload/RTSP/WebSocket, verified flag); other **`NEXT_PUBLIC_*`** keys come from **`deploy/helm/services/ui/values.yaml`** **`env`**. |
| **`vss-summarization.enabled`** | **`false`** | Keep **`false`** for alerts. |
| **`vss-alert-bridge.enabled`** | **`true`** | Set **`false`** to disable **vss-alert-bridge** when using **`real-time`** mode. |
| **`vss-alert-bridge.kafkaBootstrapServers`** | **`""`** | Kafka bootstrap string for incident/enhanced topics. When empty, the rendered **`config.yml`** defaults to **`kafka-kafka:9092`**; with **`global.useReleaseNamePrefix: true`**, **`<release>-kafka-kafka:9092`**. |
| **`vss-alert-bridge.redisHost`** | **`""`** | Redis host for **event_bridge** streams. When empty, defaults to **`<release>-redis`**. |
| **`vss-alert-bridge.redisPort`** | **`6379`** | Redis port for source/sink streams in **`config.yml`**. |
| **`vss-alert-bridge.elasticHosts`** | **`""`** | Elasticsearch HTTP URL for **`elastic.hosts`** in **`config.yml`**. When empty, defaults to **`http://<release>-elasticsearch:9200`**. |
| **`vss-alert-bridge.vlmBaseUrl`** | **`""`** | Base URL of the **VLM** NIM HTTP service (no **`/v1`** suffix here; **`config.yml`** appends **`/v1`** for **`vlm.base_url`**). When empty, defaults to **`http://<release>-nvidia-cosmos-reason2-8b:8000`**; align with the **VLM** you deploy under **`nims`** and with **`vlmName`**. |
| **`vss-alert-bridge.vlmName`** | **`nvidia/cosmos-reason2-8b`** | NGC model id passed to **`vlm.model`**; must match the **VLM** NIM you run (same idea as **`agent.vss-agent.vlmName`**). |
| **`vss-alert-bridge.vstBaseUrl`** | **`""`** | **VST** ingress base URL for **`vst_config`** and storage paths in **`config.yml`**. When empty, defaults to **`http://<release>-vss-vios-ingress:30888`**. |
| **`vss-alert-bridge.alertReviewMediaBaseDir`** | **`""`** | Optional **`ALERT_REVIEW_MEDIA_BASE_DIR`** in **`config.yml`** for alert-review media; leave empty if unused. |
| **`vss-alert-bridge.configVariant`** | **`""`** | **`""`:** default **`config.yml`**. |
| **`vss-alert-bridge.waitForDependencies.enabled`** | **`true`** in **`values.yaml`** | When **`true`**, an **initContainer** waits until **Kafka**, **Redis**, and **Elasticsearch** TCP ports accept connections before **`vss-alert-bridge`** starts (reduces startup **`Connection refused`** when infra stack pods are still coming up). Set **`false`** to skip. Override **`waitForDependencies.*Host`** / ports if your service DNS differs. |
| **`infra.kafka.enabled`** | **`true`** | Set **`false`** to disable **Kafka** (infra subchart). Bootstrap DNS is **`kafka-kafka:9092`**; with **`global.useReleaseNamePrefix: true`**, **`<release>-kafka-kafka:9092`**. |
| **`infra.kafka.persistence.size`** | **10Gi** in **`values.yaml`** | PVC size for Kafka broker data (alerts override). |
| **`infra.kafka.persistence.storageClass`** | **`""`** | **StorageClass** for the Kafka PVC. |
| **`infra.kafka.topics`** | YAML list | Topic bootstrap **Job** reads **`infra.kafka.topics`** (or a legacy JSON string in **`topics`**). |
| **`elasticsearch.enabled`** | **`true`** | Set **`false`** to disable the in-cluster **Elasticsearch** deployment. |
| **`elasticsearch.storage.dataSize`** | **10Gi** | PVC size for Elasticsearch **data** volume. |
| **`elasticsearch.storage.storageClass`** | **`""`** | **StorageClass** for Elasticsearch PVCs; leave empty to inherit **`global.storageClass`**, or set explicitly. |
| **`infra.vss-broker-health-check.enabled`** | **`true`** | Set **`false`** to disable the **Job** that waits until **Kafka** and **Redis** are reachable (infra subchart). |
| **`infra.vss-broker-health-check.kafkaHost`** | **`""`** | Kafka hostname for the health check. When empty, defaults to **`kafka-kafka`**; with **`global.useReleaseNamePrefix: true`**, **`<release>-kafka-kafka`**. |
| **`infra.vss-broker-health-check.kafkaPort`** | **`9092`** | Kafka port for the health check. |
| **`infra.vss-broker-health-check.redisHost`** | **`""`** | Redis hostname for the health check. When empty, defaults to **`<release>-redis`**. |
| **`infra.vss-broker-health-check.redisPort`** | **`6379`** | Redis port for the health check. |
| **`analytics.enabled`** | **`true`** | Set **`false`** to skip the entire **`analytics`** umbrella (both **vss-behavior-analytics** and **vss-video-analytics-api**). |
| **`analytics.vss-behavior-analytics.enabled`** | **`true`** | Set **`false`** to disable **vss-behavior-analytics** only (e.g. **`real-time`** mode). |
| **`analytics.vss-behavior-analytics.command`** | *(see `values.yaml`)* | Container command (Python app entrypoint and **`--config`** path); must match the **`config.kafkaConfigJson`** workload. |
| **`analytics.vss-behavior-analytics.config.kafkaConfigJson`** | *(see `values.yaml`)* | Full **`vss-behavior-analytics-config.json`** body (parent-owned). Default bootstrap **`kafka-kafka:9092`** matches **`global.useReleaseNamePrefix: false`**; if you use release-prefixed service DNS, override this JSON accordingly. |
| **`analytics.vss-video-analytics-api.enabled`** | **`true`** | Set **`false`** to disable the **video analytics API** for Alerts. |
| **`analytics.vss-video-analytics-api.kafkaBrokers`** | **`""`** | Kafka bootstrap for the API. When empty, defaults to **`kafka-kafka:9092`**; with **`global.useReleaseNamePrefix: true`**, **`<release>-kafka-kafka:9092`**. |
| **`analytics.vss-video-analytics-api.elasticsearchNode`** | **`""`** | Elasticsearch HTTP URL for the API. When empty, defaults to **`http://<release>-elasticsearch:9200`**. |
| **`analytics.vss-video-analytics-api.storage.size`** | **5Gi** | PVC size for API local storage. |
| **`analytics.vss-video-analytics-api.storage.storageClass`** | **`""`** | **StorageClass** for that PVC; leave empty to inherit **`global.storageClass`**, or set explicitly. |
| **`analytics.vss-video-analytics-api.waitForDependencies.enabled`** | **`true`** in **`values.yaml`** | When **`true`**, an **initContainer** waits until **Kafka** and **Elasticsearch** TCP ports accept connections before the API **Pod** starts. Set **`false`** to skip. |
| **`vss-elasticsearch-init.enabled`** | **`true`** | Set **`false`** to skip the **Job** that runs Elasticsearch index/ILM setup for the Alerts profile. |
| **`vss-elasticsearch-init.elasticsearchUrl`** | **`""`** | Elasticsearch URL the init **Job** targets. When empty, defaults to **`http://<release>-elasticsearch:9200`**. |
| **`kibana.enabled`** | **`true`** | Set **`false`** to disable the in-cluster **Kibana** deployment. |
| **`kibana.elasticsearchHosts`** | **`""`** | Elasticsearch URL list **Kibana** connects to. When empty, defaults to **`http://<release>-elasticsearch:9200`**. |
| **`kibana.kibanaPublicUrl`** | **`""`** | Browser-facing **Kibana** base URL. When empty, templates use **`global.kibanaPublicUrl`** if set, else **`http://<release>-kibana:5601`**. |
| **`vss-kibana-init.enabled`** | **`true`** | Set **`false`** to skip the **Job** that applies Kibana saved objects / dashboards for Alerts. |
| **`vss-kibana-init.kibanaUrl`** | **`""`** | **Kibana** URL the init **Job** calls. When empty, defaults to **`http://<release>-kibana:5601`**. |
| **`vss-kibana-init.elasticsearchUrl`** | **`""`** | **Elasticsearch** URL the init **Job** uses. When empty, defaults to **`http://<release>-elasticsearch:9200`**. |
| **`infra.logstash.enabled`** | **`true`** | Set **`false`** to disable **Logstash** (Kafka → Elasticsearch pipeline for **mdx-raw** / incidents, etc.). |
| **`infra.logstash.kafka.bootstrapServers`** | **`""`** | Kafka bootstrap for Logstash input. When empty, the **logstash** subchart defaults to **`kafka-kafka:9092`**; with **`global.useReleaseNamePrefix: true`**, **`<release>-kafka-kafka:9092`**. |
| **`infra.logstash.elasticsearch.host`** | **`""`** | Elasticsearch output host. When empty, defaults to **`elasticsearch:9200`** unprefixed (see **logstash** subchart **`_helpers.tpl`**). |
| **`vios.vss-vios-nvstreamer.enabled`** | **`true`** | Set **`false`** to disable **NVStreamer** for Alerts. |
| **`vss-rtvi-cv.enabled`** | **`true`** | Set **`false`** to disable **vss-rtvi-cv** in **`real-time`** mode |
| **`vss-rtvi-cv-sdr.enabled`** | **`true`** | Set **`false`** to disable **vss-rtvi-cv-sdr** in **`real-time`** mode |
| **`vss-rtvi-cv-sdr.perceptionProvisioningAddress`** | **`""`** | **Perception** service host:port for SDR provisioning. When empty, defaults to **`<release>-vss-rtvi-cv:9010`**. |
| **`vss-rtvi-cv-sdr.vstStatusEndpoint`** | **`""`** | **VST** sensor status HTTP URL. When empty, defaults to **`http://<release>-vss-vios-ingress:30888/vst/api/v1/sensor/status`**. |
| **`vss-rtvi-cv-sdr.vstStreamsEndpoint`** | **`""`** | **VST** live streams HTTP URL. When empty, defaults to **`http://<release>-vss-vios-ingress:30888/vst/api/v1/live/streams`**. |
| **`vss-rtvi-vlm.enabled`** | **`false`** in **`values.yaml`** | Set **`true`** for **real-time** alerts. |
| **`vss-rtvi-vlm.useSharedNim`** | **`true`** in **`values.yaml`** | When **`true`**, use the shared **VLM** NIM service (**`nvidia-cosmos-reason2-8b`**) **`sharedNimService`**, **`viaVlmOpenAiModelDeploymentName`**, and **`ngcApiSecret`** overrides live in **`deploy/helm/services/rtvi/charts/rtvi-vlm/values.yaml`**. |
| **`rtvi.vss-rtvi-vlm.vlmNameSlug`** | **`nvidia-cosmos-reason2-8b`** (in subchart defaults) | Optional nested override used by the **rtvi-vlm** subchart to derive the in-cluster shared NIM service host when **`useSharedNim: true`**. This is separate from top-level developer-profile values. |
| **`nims.enabled`** | **`true`** (mirrors **`nims.enabled`** in **`values.yaml`**) | Subchart copy of the umbrella NIM switch. **`vss-rtvi-vlm`** also treats non-empty **`global.vlmBaseUrl`** as remote VLM (so **`--set-string global.vlmBaseUrl=…`** picks **`VIA_VLM_*`** from globals even if **`--set nims.enabled=false`** alone does not update this key). |
| **`vss-rtvi-vlm.kafkaBootstrapServers`** | **`""`** | Kafka bootstrap for **vss-rtvi-vlm**. When empty, defaults to **`kafka-kafka:9092`**; with **`global.useReleaseNamePrefix: true`**, **`<release>-kafka-kafka:9092`**. |
| **`vss-rtvi-vlm.redisHost`** | **`""`** | Redis host for **vss-rtvi-vlm**. When empty, defaults to **`<release>-redis`**. **`redisPort`** / **`waitForKafka`** and other keys are in **`deploy/helm/services/rtvi/charts/rtvi-vlm/values.yaml`**. |
| **`agent.vss-va-mcp.enabled`** | **`true`** | Set **`false`** to disable **vss-va-mcp** (video analytics **MCP** server). |
| **`agent.vss-va-mcp.vstIngressUrl`** | **`""`** | **VST** ingress base URL for MCP config. When empty, defaults to **`http://<release>-vss-vios-ingress:30888`**. |
| **`agent.vss-va-mcp.elasticsearchUrl`** | **`""`** | **Elasticsearch** URL for MCP. When empty, defaults to **`http://<release>-elasticsearch:9200`**. **`hfToken`**, **`vstMcpUrl`**, and **`hostIp`** are in **`deploy/helm/services/agent/charts/va-mcp/values.yaml`**. |
| **`vss-proxy.enabled`** | **`false`** | Optional nginx proxy. |
| **`nims.enabled`** | **`true`** | Master switch for all NIM subcharts (requires NIM Operator + GPUs). Set **`false`** with **`global.*`** URLs/names for remote-only LLM/VLM. For **vss-rtvi-vlm** **`VIA_VLM_*`**, setting **`global.vlmBaseUrl`** (and **`global.vlmName`**) is enough for remote mode; optionally also **`--set vss-rtvi-vlm.nims.enabled=false`** to match **`nims.enabled`**. |
| **`nims.<model>.enabled`** | per model | Enable only models you deploy; align slugs and **vss-agent**/**vss-alert-bridge** NGC ids. |

### Remote LLM and VLM

When LLM and VLM run **outside** this release, set **`nims.enabled`** to **`false`** and set **`global.llmBaseUrl`**, **`global.vlmBaseUrl`**, **`global.llmName`**, and **`global.vlmName`**. **vss-rtvi-vlm** uses **`global.vlmBaseUrl`** / **`global.vlmName`** for **`VIA_VLM_*`** whenever **`global.vlmBaseUrl`** is non-empty (even if **`--set nims.enabled=false`** does not update **`vss-rtvi-vlm.nims.enabled`**). In **`values.yaml`**, **`vss-rtvi-vlm.nims.enabled`** YAML-aliases **`nims.enabled`**; optional **`--set vss-rtvi-vlm.nims.enabled=false`** keeps them aligned for CLI-only installs. **vss-alert-bridge** (verification mode) may still need **`vss-alert-bridge.*`** overrides if defaults point at in-cluster services. URLs must be reachable from pods in the release namespace.

### 2. Install

```bash
# Clone the repository. For a specific branch or tag, add: -b <name-or-tag> (before the URL).
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deploy/helm/developer-profiles

helm dependency build ./dev-profile-alerts
```

**Verification mode:**

Run these from **`deploy/helm/developer-profiles`** (paths use **`./dev-profile-alerts/…`** relative to that directory). Use any DNS prefix you configure in **`global.externalHost`**; the examples use **`vss-alerts`** to match release **`vss-alerts`** and **`values-verification.yaml`**.

```bash
# Update the values-verification.yaml and install the chart
helm upgrade --install <RELEASE NAME> ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-verification.yaml \
  -n <NAMESPACE> --create-namespace

# Set the minimum required values inline to install the chart
export NGC_CLI_API_KEY='<your NGC API key>'
export STORAGE_CLASS='<Storage Class Name>'
export EXTERNAL_HOST='<EXTERNAL_HOST_IP>'

helm upgrade --install vss-alerts ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-verification.yaml \
  -n vss-alerts \
  --create-namespace \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost="vss-alerts.$EXTERNAL_HOST.nip.io" \
  --set global.storageClass="$STORAGE_CLASS"

# OR — verification with remote LLM/VLM (no in-cluster NIMs); reuse exports above
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-alerts ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-verification.yaml \
  -n vss-alerts \
  --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost="vss-alerts.$EXTERNAL_HOST.nip.io" \
  --set global.storageClass="$STORAGE_CLASS" \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nvidia/cosmos-reason2-8b"
```

**Real-time mode:**

```bash
# Update the values-realtime.yaml and install the chart
helm upgrade --install <RELEASE NAME> ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-realtime.yaml \
  -n <NAMESPACE> --create-namespace

# OR
# Set the minimum required values inline to install the chart
export NGC_CLI_API_KEY='<your NGC API key>'
export STORAGE_CLASS='<Storage Class Name>'
export EXTERNAL_HOST='<EXTERNAL_HOST_IP>'

helm upgrade --install vss-alerts ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-realtime.yaml \
  -n vss-alerts \
  --create-namespace \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost="vss-alerts.$EXTERNAL_HOST.nip.io" \
  --set global.storageClass="$STORAGE_CLASS"

# OR — real-time with remote LLM/VLM (no in-cluster NIMs); reuse exports above
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-alerts ./dev-profile-alerts \
  -f ./dev-profile-alerts/values-realtime.yaml \
  -n vss-alerts \
  --create-namespace \
  --set nims.enabled=false \
  --set vss-rtvi-vlm.nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost="vss-alerts.$EXTERNAL_HOST.nip.io" \
  --set global.storageClass="$STORAGE_CLASS" \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nvidia/cosmos-reason2-8b"
```

## Exposing the stack

**Note:** After install or upgrade, wait until **all** pods in your namespace are **Ready** before using the UI. When **in-cluster NIM** is enabled (**`nims.enabled: true`**), **NIM** workloads need extra time. The Alerts stack also runs **Kafka**, **Elasticsearch**, **NVStreamer**, and (in verification mode) **vss-alert-bridge** and **vss-rtvi-cv**, these can take many minutes. Opening **vss-agent-ui** too early can show **transient errors**. Use **`kubectl get pods -n <NAMESPACE>`** (or **`-w`**) until workloads are **Running** with expected **READY** counts.

Set **`global.externalHost`** and **`global.kibanaPublicUrl`** (and scheme/port) in your mode values file so browser URLs resolve.

### VSS Ingress (`vssIngress`)

The chart can create a Kubernetes **`Ingress`** (**`templates/vss-ingress.yaml`**) with path rules for **vss-agent-ui**, **vss-agent**, **vss-vios-ingress**, **`/video-analytics-api`**, **`/alert-bridge`**, and optional hosts for **Kibana**, **Phoenix**, and **NVStreamer** when those subcharts are enabled.

**Prerequisites**

1. An **Ingress controller** must already be installed; **`vssIngress.ingressClassName`** (default **`haproxy`**) must match its **`IngressClass`**.
2. **`global.externalHost`** must be set unless **`vssIngress.host`** overrides the main hostname.
3. **`metadata.annotations`** include **`haproxy.org/path-rewrite`** so **`/video-analytics-api`** and **`/alert-bridge`** prefixes are stripped for backends. That requires a **HAProxy**-compatible Ingress implementation (or adjust annotations for your controller).

**What gets created**

- **`Ingress`** **`<release>-vss-ingress`** in the release namespace.
- Main host routes as in **`templates/vss-ingress.yaml`**; optional **`kibana.`** / **`phoenix.`** / **`streamer.`** hosts when components are enabled.

After install, list **`Ingress`** objects:

```bash
kubectl get ingress -n <NAMESPACE>
```

Expect **`NAME`** **`<RELEASE_NAME>-vss-ingress`** when **`vssIngress.enabled`** is **`true`**.

**Minimal values** (controller already on cluster)

```yaml
global:
  externalHost: "vss-alerts.YOUR_IP.nip.io"
  externalScheme: "http"
  kibanaPublicUrl: "http://kibana.vss-alerts.YOUR_IP.nip.io"
vssIngress:
  enabled: true
  ingressClassName: haproxy
  host: ""
```

### Example: HAProxy and Ingress

**1. Install HAProxy Kubernetes Ingress controller** (once per cluster, or use your platform ingress):

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

**Note:** CSP-specific exposure (LoadBalancer, NodePort, cloud LB) may require different controller **`Service`** settings. See your provider’s documentation.

## Upgrade and uninstall

**Upgrade**

```bash
helm upgrade <RELEASE_NAME> ./dev-profile-alerts -f dev-profile-alerts/values-verification.yaml -n <NAMESPACE>
```

Use **`-f dev-profile-alerts/values-realtime.yaml`** if you installed **real-time** mode.

**Uninstall**:

```bash
helm uninstall <RELEASE_NAME> -n <NAMESPACE>
```

Note: PVCs and any cluster-scoped resources (nimcache) are not removed by `helm uninstall`; delete them manually if needed.

```bash
kubectl delete nimcache --all -n <NAMESPACE>
kubectl delete pvc --all -n <NAMESPACE>
```
