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

Local defaults request **3 GPUs** (`nvidia.com/gpu: 1` each): the LLM NIM, RT-VLM, and **`vss-vios-streamprocessing`**. The [remote LLM/VLM](#remote-llm-and-vlm) example sets **`nims.enabled=false`** and still requests **2 local GPUs** (RT-VLM and streamprocessing). Counts below are the chart defaults; validated **`resources`** overrides and hardware or model compatibility can change them. **`global.useReleaseNamePrefix`** is **`false`**, so workload names are unprefixed (`vss-rtvi-vlm`, `nemotron-35-lightning-30b-a3b`, `vss-vios-streamprocessing`). Set it to **`true`** to prefix them with the release name. Deployment pods add a replica-hash suffix. StatefulSet pods use an ordinal (`vss-vios-streamprocessing-0`), not that hash. The table lists the workload name from `kubectl get pods`, not the full pod name.

| Workload | Local default | Remote example |
|----------|---------------|----------------|
| `vss-rtvi-vlm` (RT-VLM) | 1 | 1 |
| `nemotron-35-lightning-30b-a3b` (NIM) | 1 | 0 (`nims.enabled=false`) |
| `vss-vios-streamprocessing` | 1 | 1 |
| **Total** | **3** | **2** |


## RT-VLM frame cap

Integrated RT-VLM keeps at most **256 frames per inference chunk** by default.
Override the cap without replacing the complete environment-variable list:

```yaml
rtvi:
  vss-rtvi-vlm:
    envOverrides:
      RTVI_VLLM_MM_PROCESSOR_VIDEO_NUM_FRAMES: "8192"
```

This is a server-side cap, not a sampling rate or a guarantee that 8192 frames
fit in GPU memory. Validate the model's context and GPU-memory budget before
raising it. It does not reconfigure an external VLM in shared-NIM mode.

## RT-VLM context length

Integrated RT-VLM preserves its **32768-token model context** default. This is
the total capacity for text/video input tokens and generated output, separate
from the frame cap and the request's output-token limit. Override it without
replacing the complete environment-variable list:

```yaml
rtvi:
  vss-rtvi-vlm:
    envOverrides:
      VLM_MAX_MODEL_LEN: "262144"
```

This becomes the integrated engine's `max_model_len` at startup; redeploy the
service after changing it. Choose a value supported by the checkpoint and
validate GPU-memory fit with a representative request. This does not configure
an external shared NIM, and sandbox `VSS_VLM_*` request settings cannot raise
this server limit.

## Prerequisites

- **Kubernetes cluster**
  - Running cluster whose API you can reach with **`kubectl`** (correct context and, if applicable, kubeconfig).
  - **Server version** recorded for this profile: **1.34**. **Driver (x86 Ubuntu 24.04)** recorded for this profile: **595.58.03**.
  - This README does **not** record a tested GPU Operator, NIM Operator, or local-path-provisioner version. Those products use their own version numbers. Do not replace an upstream version with VSS **3.3.0**. Links that say `latest`, `master`, or `main` move on their own and do not prove they were validated with Kubernetes 1.34 and driver 595.58.03. Install the versions you have validated, and open that version’s documentation.

- **NVIDIA GPU Operator**
  - Install the GPU Operator on the cluster so nodes advertise `nvidia.com/gpu`. Getting started: [GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html). Platform support for the version you install: [platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html). Replace `latest` in those URLs with the operator version you install.
  - Pin driver **595.58.03** through the GPU Operator driver settings for x86 Ubuntu 24.04.

- **NVIDIA NIM Operator**
  - Required when **`nims`** subcharts are enabled (`NIMCache` / `NIMService`).
  - Install **after** the GPU Operator. Installation for the version you install: [NIM Operator](https://docs.nvidia.com/nim-operator/latest/install.html). Replace `latest` with that operator version.

- **Volume provisioner**
  - A **StorageClass** must exist. Set **`global.storageClass`** to that class’s **`metadata.name`**. The chart writes `storageClassName` on the PVCs it creates, so you do not need to change the cluster-wide default StorageClass for this profile. Change the default only for other workloads that omit `storageClassName`.
  - **Node-local storage (for example local-path):** the shared VST claims are `ReadWriteOnce`. **`vss-vios-sensor`** and **`vss-vios-streamprocessing`** both mount `vst-data` and `vst-video`, so on a multi-node cluster they must run on a node that can mount those volumes — for local-path, the same node. The chart does not set that affinity. Set **`nodeSelector`** or **`affinity`** on **`vios.vss-vios-sensor`** and **`vios.vss-vios-streamprocessing`**, and confirm the node has capacity for the claims. See [shared VST volumes](#shared-vst-volumes-on-node-local-storage).
  - **Bare-metal clusters** with no provisioner yet can install [local-path-provisioner](https://github.com/rancher/local-path-provisioner). The `master` branch link is not a release pin; use the provisioner version you have validated.

### Chart / tooling

- **Helm** 3.x
- **Kubectl**
- **Curl** and **jq** (to discover model IDs from remote endpoints)
- **GPUs**: see [GPU requirements](#gpu-requirements) (3 with local defaults, 2 on the documented remote path).
- **NVIDIA NIM** (if using NIM subcharts): NIM Operator on the cluster (see [Prerequisites](#prerequisites) above).
- **NGC**: API key for NIM, image pull / chart secret creation (see below).
- **StorageClass**: a StorageClass must exist on the cluster to create PVCs.


## Quick start

### 1. Prepare the values file

Create `values-base.yaml` and set the following (all are required for a typical install):

| Key | Description |
|-----|-------------|
| **`ngc.apiKey`** | Your NGC API key (for image pull, NIM, and RT-VLM model access). Chart uses `ngc.createSecrets: true` by default.|
| **`global.storageClass`** | StorageClass name in your cluster (e.g. `oci-bv-high`, `gp3`, `standard`). |
| **`global.externalScheme`** | `http` or `https` (defaults to `http` in templates if unset). |
| **`global.externalHost`** | Hostname or IP the browser uses (e.g. `vss.YOUR_IP.nip.io`). Required for a typical external install when subchart URL fields are omitted. |
| **`global.externalPort`** | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`nims.nemotron35.enabled`** | Leave **`true`** (the chart default). This deploys the in-cluster LLM Service **`nemotron-35-lightning-30b-a3b`**. Set **`global.llmName`** to **`nvidia/nemotron-3.5-lightning-30b-a3b`** so the agent requests that model. **`agent.vss-agent.llmName`** overrides **`global.llmName`** when it is set. **`llmNameSlug`** is not read by the chart; leave it empty. |
| **`vlmNameSlug`** | Sample **`values-base.yaml`** sets **`none`**. Chart **`values.yaml`** leaves it **`""`**. Neither deploys a VLM NIM. RT-VLM loads the integrated checkpoint. Keep **`global.vlmName`** aligned with the RT-VLM `/v1/models` basename. |
| **`nims`** | Shared umbrella **`helm/services/nims`**: **`nims.enabled`**, **`nims.gpuType`**, **`nims.nemotron35`**, **`nims.cosmos3`** (**`nims.cosmos3.enabled`** is **`false`** by default because base uses RT-VLM), **`nims.global`**. Set **`nims.enabled`** to **`false`** when using [remote LLM/VLM](#remote-llm-and-vlm) only. |
| **`rtvi`** | Umbrella for **`vss-rtvi-vlm`**. Default base enables RT-VLM with the integrated Cosmos Reason3 Nano BF16 checkpoint (`modelPath=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final`). |
| **`global.llmBaseUrl`** / **`global.vlmBaseUrl`** (remote) | HTTP(S) base URLs for LLM and VLM when they are **not** deployed by this chart (OpenAI-compatible or NIM endpoints reachable from **vss-agent** pods). Use with **`nims.enabled: false`**. Leave **`""`** when serving models from in-cluster **NIM** / **RT-VLM** subcharts. |
| **`global.llmName`** / **`global.vlmName`** (remote) | Model identifiers the agent should use; **`global.vlmName`** must match the RT-VLM `/v1/models` basename (default **`nim_nvidia_cosmos3-nano-reasoner_bf16-final`**). |
| **`vssIngress`** (optional) | Set **`vssIngress.enabled`** to **`true`** to create a Kubernetes **`Ingress`** for UI, agent, VST, and (when Phoenix is enabled) Phoenix under one hostname. Requires an **IngressClass** that already exists on the cluster (see [VSS Ingress (`vssIngress`)](#vss-ingress-vssingress)). **`global.externalHost`** must be set unless you set **`vssIngress.host`**. **`values-base.yaml`** enables this by default; set **`enabled: false`** if you use port-forward, **`NodePort`**, or a custom Ingress only. |

#### `values-base.yaml` vs chart `values.yaml`

| File | Role |
|------|------|
| **`values-base.yaml`** | **Your** small override file: fill required keys (NGC, StorageClass, external host, **`nims.nemotron35`**, and **`global.llmName`**—or **`global.llmBaseUrl`** / **`global.vlmBaseUrl`** and **`nims.enabled: false`** for remote models) and anything else you change. Pass it with **`-f values-base.yaml`**. |
| **`values.yaml`** | **Chart defaults** shipped with the profile (full value tree). You normally **do not** edit it; add only the keys you need to your override file (**`values-base.yaml`**) and Helm merges your file on top of these defaults. |

Use the table below when you want to change behavior beyond the minimal **`values-base.yaml`** fields. Defaults described here match the chart’s **`values.yaml`** in this repository.

##### Optional overrides — `values.yaml` keys (reference)

| Key / group | Default | Description |
|-------------|---------|-------------|
| **`mode`** | `""` | "" for dev-profile-base chart. |
| **`llmNameSlug`** | `""` | Not consumed by the base, agent, or NIM templates. Changing it does not select a NIM. The enabled subchart is **`nims.nemotron35`** (Service **`nemotron-35-lightning-30b-a3b`**). The agent model ID comes from **`agent.vss-agent.llmName`**, then **`global.llmName`**, otherwise **`nvidia/nemotron-3.5-lightning-30b-a3b`**. |
| **`vlmNameSlug`** | `""` in chart **`values.yaml`**; **`none`** in sample **`values-base.yaml`** | Neither value selects a VLM NIM. RT-VLM loads the integrated checkpoint. The sample override and the chart default are different strings; do not treat the sample as the chart default. |
| **`ngc.createSecrets`** | `true` | When **`true`** and **`ngc.apiKey`** is set, the chart creates two secrets (see **`templates/ngc-secrets.yaml`**): **`ngc-api`** (Opaque: **`NGC_API_KEY`** / **`NGC_CLI_API_KEY`**) for NGC API access, and **`ngc-secret`** (**dockerconfigjson**) for pulling images from nvcr.io. Set **`false`** only if you create both secrets yourself; then set **`global.ngcApiSecret`** and **`global.imagePullSecrets`** to match your names. |
| **`ngc.apiKey`** | `""` | With **`ngc.createSecrets: true`**, set your NGC API key here; it backs both created secrets. With **`createSecrets: false`**, omit (or leave empty) and install the Opaque + docker secrets out of band; align **`global.*`** below with those objects. Optional: **`ngc.apiKeySecretName`** / **`ngc.dockerSecretName`** rename the generated secrets—update **`global.ngcApiSecret.name`** and **`global.imagePullSecrets`** accordingly. |
| **`global.imagePullSecrets`** | `[{ name: ngc-secret }]` | Pod **image pull** credentials for nvcr.io. Must reference the **Docker registry** secret (default **`ngc-secret`**, i.e. **`ngc.dockerSecretName`**). This is separate from the NGC **API** key secret. |
| **`global.ngcApiSecret`** | `name: ngc-api`, `key: NGC_API_KEY` | Tells NIM (**`NIMService`** / **`NIMCache`**) and related workloads which **Opaque** secret holds the NGC **API** key: **`name`** defaults to **`ngc-api`** (**`ngc.apiKeySecretName`**), **`key`** defaults to **`NGC_API_KEY`** (the key the chart writes in that secret). Change these if you use a different secret name or data key. |
| **`global.externalScheme`** | `""` in defaults | Set in **`values-base.yaml`** (e.g. **`http`** or **`https`**). With **`externalHost`** / **`externalPort`**, builds browser-facing URLs for **`vss-agent-ui`**, **`vss-agent`**, and **`vss-vios-ingress`** when their own URL fields are empty. |
| **`global.externalHost`** | `""` in defaults | Hostname or IP clients use in the browser (e.g. **`vss.YOUR_IP.nip.io`**). |
| **`global.externalPort`** | `""` in defaults | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`global.storageClass`** | unset in default **`values.yaml`** | Set in **`values-base.yaml`** to the StorageClass **`metadata.name`**. Chart PVCs use this as `storageClassName`. You do not need to mark that class as the cluster default. |
| **`global.llmBaseUrl`** | `""` | Remote LLM API base URL for **vss-agent** when models are not in-cluster (use with **`nims.enabled: false`**). Must be reachable from pods in the release namespace (cluster DNS, **`NodePort`**, LB, or routable IP). |
| **`global.vlmBaseUrl`** | `""` | Optional external VLM service root, without a trailing `/v1`. When set, keep RT-VLM enabled and set `rtvi.vss-rtvi-vlm.useSharedNim=true` so RT-VLM proxies that endpoint. |
| **`global.llmName`** | e.g. **`nvidia/nemotron-3.5-lightning-30b-a3b`** | Catalog-style model ID passed to the agent; must match the model served at **`global.llmBaseUrl`**. |
| **`global.vlmName`** | e.g. **`nim_nvidia_cosmos3-nano-reasoner_bf16-final`** | RT-VLM `/v1/models` basename (or remote model ID); must match the model served at **`global.vlmBaseUrl`** / RT-VLM. |
| **`vios.vstStorage.createSharedPvcs`** | `true` | **`true`:** the **`vios`** umbrella creates **PersistentVolumeClaims** so **sensor** and **streamprocessing** share on-disk folders for VST data and video; data survives pod restarts but your cluster must have a working **`StorageClass`** (see **`global.storageClass`**). **`false`:** no shared PVCs from **`vios`**—pods use emptyDir or per-subchart PVCs depending on **`vios.vss-vios-*`** persistence. **`false`** avoids disk provisioning but **uploaded video and VST cache are lost** when pods are rescheduled if nothing else persists them. |
| **`vios.vstStorage.accessMode`** | **`ReadWriteOnce`** | Access mode for the three shared VST PVCs (see **`helm/services/vios/templates/vst-storage-pvc.yaml`**). `ReadWriteOnce` allows multiple pods on one node. With node-local storage, **`vss-vios-sensor`** and **`vss-vios-streamprocessing`** must be placed on a node that can mount the shared claims. |
| **`vios.vstStorage.vstData`** | **`size`:** **10Gi**, **`storageClass`:** `""` | Claim size for the shared **VST data** volume. Leave **`storageClass`** empty to inherit **`global.storageClass`**; set it only if this volume needs a different class than the rest of the chart. |
| **`vios.vstStorage.vstVideo`** | **`size`:** **20Gi**, **`storageClass`:** `""` | Claim size for the shared **VST video** volume; same **`storageClass`** rules as **`vstData`**. |
| **`vios.vstStorage.streamerVideos`** | **`size`:** **20Gi**, **`storageClass`:** `""` | Claim size for the shared **streamer upload** video volume; same **`storageClass`** rules as **`vstData`**. |
| **`infra.enabled`** | `true` | Master switch for the **`infra`** umbrella (Phoenix, Redis, …). |
| **`infra.phoenix.enabled`** | `true` | Set **`false`** to disable Phoenix only. |
| **`infra.redis.enabled`** | `true` | Set **`false`** to disable Redis only. |
| **`vios.enabled`** | `true` | Master switch for the **`vios`** umbrella (all bundled **`vss-vios-*`** subcharts). Set **`false`** to omit the entire VST microservice stack from the release. |
| **`vios.vss-vios-postgres.enabled`** | `true` | Set **`false`** to disable centralized DB. Storage sizing/class: subchart **`values.yaml`** or overrides under **`vios.vss-vios-postgres`**. |
| **`vios.vss-vios-sensor.streamProcessorEndpoint`** | **`""`** (empty) | Sensor registers streams against streamprocessing directly (not **:10000**). Left empty, the sensor subchart derives the address itself: **`http://vss-vios-streamprocessing:30001`**, or **`http://<release>-vss-vios-streamprocessing:30001`** when **`global.useReleaseNamePrefix`** is **`true`**. Set a value only to point the sensor at an endpoint this chart does not render. |
| **`vios.vss-vios-sensor.peerUseReleaseNamePrefix`** | **`{}`** | How the sensor names its derived peers (**`streamprocessing`**, **`sdrc`**) when that differs from **`global.useReleaseNamePrefix`**. A subchart cannot read another subchart's local **`useReleaseNamePrefix`**, and the sensor's own flag names only the sensor, so a peer's local override alone is **not** enough. See *Mixed release-name prefixes* below. Unset keys follow the global value, and an explicit **`false`** is honoured. |
| **`vios.vss-vios-sensor.enabled`** | `true` | **`false`** to disable **vss-vios-sensor**. |
| **`vios.vss-vios-sensor.persistence`** | Each of **`vstData`** and **`vstVideo`**: mount on, **`create: false`**, **`existingClaim`** empty by default | Controls whether **sensor** mounts two shared folders (**data** and **video**). **Typical setup:** leave **`existingClaim`** blank—Helm wires the pods to the PVCs created when **`vios.vstStorage.createSharedPvcs`** is **`true`**. **Custom PVCs:** set **`existingClaim`** to your claim name for that volume. **Disable a mount:** set that volume’s **`enabled`** to **`false`** (that path is not mounted). |
| **`vios.vss-vios-streamprocessing.enabled`** | `true` | **`false`** to disable **vss-vios-streamprocessing**. |
| **`vios.vss-vios-streamprocessing.downloadFilesTimeoutSecs`** | `120` | Base download timeout in seconds (**`download_files_timeout_secs`** in **`vst_config.json`**); a positive 32-bit integer, validated by the subchart **`values.schema.json`**. Paths that consume it directly time out at exactly this value; a pipeline that keeps reporting progress may extend its own budget incrementally, up to a ceiling of at least 600 seconds. Raise it for long videos, especially on the software encoding path. |
| **`vios.vss-vios-streamprocessing.persistence`** | **`vstData`**, **`vstVideo`**, **`streamerVideos`**: same idea as sensor | **Streamprocessing** mounts up to **three** shared folders: VST **data**, VST **video**, and **streamer** uploads. Use blank **`existingClaim`** to use the shared PVCs from **`vios`** (when **`vios.vstStorage.createSharedPvcs`** is **`true`**), or set **`existingClaim`** / **`enabled`** per volume the same way as for **sensor**. |
| **`vios.vss-vios-ingress.enabled`** | `true` | Deploys the in-cluster **VST ingress** (nginx). |
| **`vios.vss-vios-ingress.externallyAccessibleIp`** | `""` | Hostname or IP address advertised to VST/nginx for external access. If unset, the subchart uses **`global.externalHost`**; if that is unset, it defaults to **`127.0.0.1`**. Override this value only when the VST ingress must use a hostname or IP that differs from **`global.externalHost`**. |
| **`vssIngress.enabled`** | `false` in chart **`values.yaml`**; **`true`** in sample **`values-base.yaml`** | When **`true`**, renders **`templates/vss-ingress.yaml`**: one **`Ingress`** named **`vss-ingress`** unless **`vssIngress.resourceName`** is set. Mounts come from the [canonical route table](../../services/common/README.md) — **`/`**, **`/api/chat`**, **`/api/agent`**, **`/api/vss-chat`**, and **`/api/proxy`** to **vss-agent-ui**; **`/api`**, **`/chat`**, **`/websocket`**, **`/static`** (and **`/docs`**, **`/redoc`**, **`/generate`**, **`/openapi.json`**) to **vss-agent**; **`/vst`** and **`/storage`** to **vss-vios-ingress**; **`/rtvi-vlm`** to RT-VLM; **`/phoenix`** to Phoenix on the main host; **`/llm`** to the in-cluster LLM NIM when **`nims.nemotron35`** is enabled. **`/llm`** is omitted when **`nims.enabled`** is **`false`**. A second Phoenix hostname is created only when **`vssIngress.phoenixHost`** is set. A service this profile does not deploy is not mounted. No effect if **`global.externalHost`** and **`vssIngress.host`** are both empty. |
| **`vssIngress.ingressClassName`** | `haproxy` | **`spec.ingressClassName`** on the **`Ingress`**. Must match an **`IngressClass`** that already exists (for example the class created by **HAProxy Kubernetes Ingress**). Use another name (e.g. **`nginx`**) if your controller uses a different class. |
| **`vssIngress.host`** | `""` | Ingress hostname rule. If empty, **`global.externalHost`** is used. Set only when the Ingress hostname must differ from **`global.externalHost`**. |
| **`vssIngress.annotations`** | `{}` | Extra annotations for the generated **`Ingress`**, for example a Traefik or NGINX rewrite middleware. Merged with the rewrite annotation the template generates. Supplying **`haproxy.org/path-rewrite`** here **replaces** the generated rewrite table for every backend rather than merging into it. See [VSS Ingress (`vssIngress`)](#vss-ingress-vssingress). |
| **`vssIngress.vssUiPort`** | `3000` | Backend **`Service`** port for **vss-agent-ui** paths. |
| **`vssIngress.vssAgentPort`** | `8000` | Backend **`Service`** port for **vss-agent** paths. |
| **`vssIngress.vstIngressPort`** | `30888` | Backend **`Service`** port for **vss-vios-ingress** (**`/vst`**, **`/storage`**). |
| **`vssIngress.rtviVlmServiceName`** | `vss-rtvi-vlm` | **`Service`** behind **`/rtvi-vlm`**. Empty falls back to **`agent.vss-agent.rtviVlmServiceName`**, then **`vss-rtvi-vlm`**. Do not use **`vlmNameSlug`** — with **`vlmNameSlug=none`** it names no **`Service`**. |
| **`vssIngress.rtviVlmPort`** | `8000` | Backend **`Service`** port for RT-VLM. The OpenAI-compatible root is **`<host>/rtvi-vlm/v1`**, **not** **`<host>/v1`**. |
| **`vssIngress.vaMcpPort`** | `9901` | Backend **`Service`** port for **vss-va-mcp** (**`/va-mcp`**), mounted when **`agent.vss-va-mcp.enabled`**. |
| **`vssIngress.elasticsearchPort`** | `9200` | Backend **`Service`** port for **`/elasticsearch`**, mounted when **`infra.elasticsearch.enabled`** (off in this profile). |
| **`vssIngress.resourceName`** | `""` | **`Ingress`** **`metadata.name`**. Empty uses **`vss-ingress`** (not prefixed with the release name). Set this to override the name. |
| **`vssIngress.phoenixHost`** | `""` | Dedicated Phoenix hostname. Empty keeps Phoenix at **`/phoenix`** on the main host and creates no second host rule. Set it (for example **`phoenix.example.com`**) only when you want that extra host. |
| **`vssIngress.phoenixPort`** | `6006` | Backend **`Service`** port for Phoenix when the Phoenix subchart is enabled. |
| **`agent.enabled`** | `true` | Set **`false`** to skip the **`agent`** umbrella (**`deploy/helm/services/agent`**). |
| **`agent.vss-agent.enabled`** | `true` | Set **`false`** to disable the **vss-agent** deployment only. |
| **`agent.vss-agent.mountConfigEdge`** / **`mountEvalOutput`** | `false` / `true` | Parent **ConfigMap** includes **`config_edge.yml`** when the file exists; **`/vss-agent/eval-output`** emptyDir when **`mountEvalOutput`** is **`true`**. Agent YAML lives at **`configs/vss-agent/config.yml`** (flat path, no profile subfolders). |
| **`agent.vss-agent.llmName`** | NGC model ID (e.g. **`nvidia/nemotron-3.5-lightning-30b-a3b`**) | NGC catalog id for the LLM; must match the model deployed under **`nims.nemotron35`**. |
| **`agent.vss-agent.vlmName`** | RT-VLM model ID (e.g. **`nim_nvidia_cosmos3-nano-reasoner_bf16-final`**) | Must match the model advertised by **`vss-rtvi-vlm`** `/v1/models`. |
| **`agent.vss-agent.evalLlmJudgeName`** | `""` | Optional eval judge model ID. When empty, the **vss-agent** subchart defaults to **`llmName`**. |
| **`agent.vss-agent.evalLlmJudgeBaseUrl`** | `""` | Optional base URL for the eval judge endpoint. When empty, the subchart defaults alongside **`llmBaseUrl`**. |
| **`agent.vss-agent.reportsBaseUrl`** | `""` | Base URL for report links. When empty, templates derive a value from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.vstExternalUrl`** | `""` | External **VST** URL passed to the agent. When empty, derived from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.externalIp`** | `""` | Hostname or IP override for agent-facing external access when **`global.external*`** is not sufficient. |
| **`agent.vss-agent.env`** | *(see **`values.yaml`**)* | **Full** container env list for vss-agent (Option B). Each **`value`** is passed through Helm **`tpl`**, so URLs can reference **`.Values`**, **`.Release`**, and **`global`** keys. Defaults mirror **`deploy/helm/services/agent/charts/agent/values.yaml`**. Use **`agent.vss-agent.extraEnv`** from the subchart only if you need late overrides (secrets / ad‑hoc vars). |
| **`agent.vss-agent.extraEnv`** | *(omit)* | Optional **`{ name, value }`** appended last (same pattern as **vss-agent-ui.extraEnv**). |
| **`vss-agent-ui.enabled`** | `true` | Set **`false`** to disable the **vss-agent-ui** deployment. |
| **`vss-agent-ui.agentApiUrlBase`** | `""` | Base URL for the **vss-agent** HTTP API (browser **`NEXT_PUBLIC_AGENT_API_URL_BASE`**, typically ends with **`/api/v1`**). If unset, built from **`global.externalScheme`** / **`externalHost`** / **`externalPort`** as **`<global>/api/v1`**, else defaults to in-cluster **`http://<release>-vss-agent:8000/api/v1`**. |
| **`vss-agent-ui.vstApiUrl`** | `""` | **VST** HTTP API URL for the browser (**`NEXT_PUBLIC_VST_API_URL`**). If unset, built as **`<global>/vst/api`**, else **`http://<release>-vss-vios-ingress:30888/vst/api`**. |
| **`vss-agent-ui.proxyBaseUrl`** | `""` | Server-only media-proxy target (**`VSS_PROXY_BASE_URL`**). If unset, uses the in-cluster **vss-vios-ingress** Service. |
| **`vss-agent-ui.chatCompletionUrl`** | `""` | HTTP chat completion URL (**`NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL`**). If unset, built as **`<global>/chat/stream`**, else **`http://<release>-vss-agent:8000/chat/stream`**. |
| **`vss-agent-ui.chatBackendUrl`**, **`sidebarChatBackendUrl`** | `""` | Server-only chat proxy targets. If unset, use an explicit **`chatCompletionUrl`**, otherwise the in-cluster **vss-agent** Service. |
| **`vss-agent-ui.websocketChatUrl`** | `""` | WebSocket chat URL (**`NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL`**). If unset and **`global.externalHost`** is set, built as **`<ws-scheme>://<host>[:port]/websocket`** ( **`ws`** / **`wss`** from **`global.externalScheme`**). If both this and **`global.externalHost`** are empty, the chart may omit WebSocket env vars; set explicitly for port-forward or custom routing. |
| **`vss-agent-ui.appSubtitle`** | `""` | Optional; sets **`NEXT_PUBLIC_APP_SUBTITLE`** via merge when **`envOverrides`** does not already define that name (see **vss-agent-ui** **`deployment.yaml`**). Base defaults live in **`values.yaml`** **`envOverrides`**. |
| **`vss-agent-ui.enableDashboardTab`** | `""` | Optional; sets **`NEXT_PUBLIC_ENABLE_DASHBOARD_TAB`** when **`envOverrides`** does not already define that name. Base defaults live in **`values.yaml`** **`envOverrides`**. |
| **`vss-agent-ui.envOverrides`** | base defaults in **`values.yaml`** | List of **`{ name, value }`** merged into the subchart **`env`** list by variable name (same pattern as **dev-profile-alerts**). |
| **`vss-agent-ui.extraEnv`** | `[]` | List of **`{ name, value }`** appended last in the container **`env`** block (override or add any **`NEXT_PUBLIC_*`** without a ConfigMap). |
| **`vss-agent-ui.staticEnvConfigMapName`** | `""` | Optional **`envFrom`** **`ConfigMap`** name (you supply the **`ConfigMap`**). **`extraEnvFrom`** is also supported on the subchart. |
| **`nims.enabled`** | `true` | Master switch for the **`nims`** umbrella (**`helm/services/nims`**). When **`false`**, no **NIM** **`NIMService`** / **`NIMCache`** objects are installed. Use **`false`** with **`global.llmBaseUrl`**, **`global.vlmBaseUrl`**, **`global.llmName`** and **`global.vlmName`** for remote-only LLM/VLM. |
| **`nims.gpuType`** | **`H100`** in chart **`values.yaml`**; **`RTXPRO6000BW`** in sample **`values-base.yaml`** | Selects a **`gpuProfiles`** entry for **`nemotron35`** / **`cosmos3`** **`nim-env`** ConfigMaps. Supported keys: **`H100`**, **`GB300`**, **`L40S`**, **`RTXPRO4500BW`**, **`RTXPRO6000BW`**, **`OTHER`**. A key picks those settings. It does not by itself certify that the hardware and model combination was validated. |
| **`nims.nemotron35` / `nims.cosmos3`** | see **`values.yaml`** | Per-model **`enabled`**, images, resources, storage, and **`env`**. Default base enables **`nemotron35`** (Service **`nemotron-35-lightning-30b-a3b`**, model ID **`nvidia/nemotron-3.5-lightning-30b-a3b`**); **`cosmos3`** stays off because RT-VLM serves the VLM. |

### Mixed release-name prefixes

The sensor derives its stream-processing peer's Service name itself, but it cannot see that
chart's local `useReleaseNamePrefix`. When a peer's naming differs from the global value, set
**both** the peer chart's local flag and the sensor's matching `peerUseReleaseNamePrefix` entry.
For example, prefixed names globally, with stream processing (and the sensor) unprefixed:

```yaml
global:
  useReleaseNamePrefix: true
vios:
  vss-vios-streamprocessing:
    useReleaseNamePrefix: false        # names the Service vss-vios-streamprocessing
  vss-vios-sensor:
    useReleaseNamePrefix: false        # names the sensor only
    peerUseReleaseNamePrefix:
      streamprocessing: false          # tells the sensor how its peer is named
```

Setting only `vss-vios-streamprocessing.useReleaseNamePrefix: false` is not enough, because the
sensor would still call `<release>-vss-vios-streamprocessing`. The chart refuses that
render and names the missing value. The same applies to `sdrc`
(`infra.sdrc.useReleaseNamePrefix` with `peerUseReleaseNamePrefix.sdrc`) in profiles that set
`global.vios.useSdrc`. An explicit `vss-vios-sensor.streamProcessorEndpoint` bypasses both.

### Remote LLM and VLM

When LLM and VLM run **outside** this release, disable bundled NIM workloads and configure the external service roots and the model IDs those endpoints advertise:

- **`nims.enabled`**: **`false`** — skips **NIM** workloads and related **NIMOperator** objects. This does not remove every local GPU request. The documented remote path still requests one GPU for RT-VLM and one for **`vss-vios-streamprocessing`** (**2** local GPUs). See [GPU requirements](#gpu-requirements).
- **`global.llmBaseUrl`** / **`global.vlmBaseUrl`**: service-root URLs reachable from pods, without a trailing `/v1`.
- **`global.llmName`** / **`global.vlmName`**: the exact ids returned by each endpoint’s **`/v1/models`**. Do not reuse the in-cluster Nemotron 3.5 Lightning id unless that remote LLM advertises it. **`global.vlmName`** is what RT-VLM sends as **`VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME`**.
- **`rtvi.vss-rtvi-vlm.useSharedNim`**: **`true`** — keeps RT-VLM in the request path and makes it proxy the external VLM instead of loading the integrated checkpoint.

**Credentials are separate from the NGC registry key.** **`ngc.apiKey`** creates **`ngc-api`** (`NGC_API_KEY`) and **`ngc-secret`** for nvcr.io. RT-VLM sets **`VIA_VLM_API_KEY`** from that same secret (`rtvi.vss-rtvi-vlm.ngcApiSecret`, default name **`ngc-api`**, key **`NGC_API_KEY`**). A remote VLM that expects a different token needs a secret whose data key you name with **`rtvi.vss-rtvi-vlm.ngcApiSecret.name`** and **`rtvi.vss-rtvi-vlm.ngcApiSecret.key`**. The agent does not read that secret: **`NVIDIA_API_KEY`** renders empty and **`OPENAI_API_KEY`** renders **`NOAPIKEYSET`**. If the remote LLM requires a credential the agent sends, set it with **`agent.vss-agent.extraEnv`**.

Do not disable `rtvi` for the normal migration path. The agent remains configured with `VLM_MODEL_TYPE=rtvi` and calls the in-cluster RT-VLM service.

### 2. Install

```bash
# Clone the repository. For a specific branch or tag, add: -b <name-or-tag> (before the URL).
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deploy/helm/developer-profiles

helm dependency build ./dev-profile-base

# Update the values-base.yaml and install the chart
helm upgrade --install <RELEASE_NAME> ./dev-profile-base \
  -f dev-profile-base/values-base.yaml \
  -n <NAMESPACE> --create-namespace

# For example:
helm upgrade --install vss-base ./dev-profile-base \
  -f dev-profile-base/values-base.yaml \
  -n vss-base --create-namespace

# OR
# Set the minimum required values inline to install the chart.
# The chart enables nims.nemotron35 by default (Service nemotron-35-lightning-30b-a3b,
# model ID nvidia/nemotron-3.5-lightning-30b-a3b). llmNameSlug is not read by the chart.
export NGC_CLI_API_KEY='<your NGC API key>'
export STORAGE_CLASS='<Storage Class Name>'
export EXTERNAL_HOST='<EXTERNAL_HOST_IP>'

helm upgrade --install vss-base ./dev-profile-base \
  -f dev-profile-base/values-base.yaml \
  -n vss-base --create-namespace \
  --set vlmNameSlug=none \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS"

# OR — base with remote LLM/VLM. RT-VLM remains deployed and proxies the external VLM.
# This path still requests 2 local GPUs (RT-VLM and vss-vios-streamprocessing).
# (reuse NGC_CLI_API_KEY, STORAGE_CLASS, EXTERNAL_HOST exports from the example above).
export LLM_BASE_URL='<REMOTE LLM SERVICE ROOT, no trailing /v1>'
export VLM_BASE_URL='<REMOTE VLM SERVICE ROOT, no trailing /v1>'

# Query each remote endpoint and use the exact model ID it advertises.
curl -fsS "$LLM_BASE_URL/v1/models" | jq -r '.data[].id'
curl -fsS "$VLM_BASE_URL/v1/models" | jq -r '.data[].id'
export LLM_MODEL_NAME='<MODEL ID returned by the LLM command above>'
export VLM_MODEL_NAME='<MODEL ID returned by the VLM command above>'

# ngc.apiKey is the NGC registry key. RT-VLM copies it into VIA_VLM_API_KEY.
# A different VLM token: create a secret and set rtvi.vss-rtvi-vlm.ngcApiSecret.
# A credential the agent must send: set agent.vss-agent.extraEnv.
# NVIDIA_API_KEY stays empty and OPENAI_API_KEY stays NOAPIKEYSET unless you override them.
helm upgrade --install vss-base ./dev-profile-base \
  -f dev-profile-base/values-base.yaml \
  -n vss-base --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS" \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="$LLM_MODEL_NAME" \
  --set-string global.vlmName="$VLM_MODEL_NAME" \
  --set rtvi.vss-rtvi-vlm.useSharedNim=true
```

### Shared VST volumes on node-local storage

The three shared VST claims use **`ReadWriteOnce`**. **`vss-vios-sensor`** and **`vss-vios-streamprocessing`** both mount **`<release>-vst-data`** and **`<release>-vst-video`**. Streamprocessing also mounts **`<release>-vst-streamer-videos`**.

Set **`global.storageClass`** to the class you want. That is enough for this chart. Patching the cluster default StorageClass is for other workloads that do not set `storageClassName`.

On node-local storage such as local-path, a volume is usable only on the node where it was created. This chart does not co-locate sensor and streamprocessing. On more than one node, pin both workloads to a node that can mount the claims and that has room for them, for example:

```yaml
vios:
  vss-vios-sensor:
    nodeSelector:
      kubernetes.io/hostname: gpu-node-a
  vss-vios-streamprocessing:
    nodeSelector:
      kubernetes.io/hostname: gpu-node-a
```

## Exposing the stack

**Note:** After install or upgrade, check long-running Deployments and StatefulSets until they are Ready. The NIM Operator can create cache Jobs from `NIMCache`; those Jobs finish **Completed** / **Succeeded** and their pods do not stay Running. Also confirm PVCs are **Bound**. Pod phase alone does not prove inference is ready: wait for image pull, model download, and warm-up, then call the model or API and read the response before opening **vss-agent-ui**. A still-starting LLM NIM or RT-VLM can return transient errors.

To expose VSS through a single hostname, set **`global.externalHost`** (and **`global.externalScheme`** / **`global.externalPort`** as needed) in **`values-base.yaml`** as in the table under [Prepare the values file](#1-prepare-the-values-file). That drives in-chart URLs for **vss-agent-ui**, **vss-agent**, and **vss-vios-ingress** when their own URL fields are empty.

### VSS Ingress (`vssIngress`)

The chart can create a single Kubernetes **`Ingress`** (**`templates/vss-ingress.yaml`**) so clients reach UI, API, VST, and Phoenix through one external hostname.

**Prerequisites**

1. An **Ingress controller** must already be installed, and its **`IngressClass`** name must match **`vssIngress.ingressClassName`** (default **`haproxy`**). The [HAProxy Kubernetes Ingress chart](https://github.com/haproxytech/helm-charts/tree/main/kubernetes-ingress) link follows `main` and is not a VSS 3.3.0 pin; use the controller version you have validated.
2. **`global.externalHost`** must be set to that hostname (for example **`vss.203.0.113.10.nip.io`**), unless you set **`vssIngress.host`** explicitly.
3. **`vssIngress.enabled`**: **`true`** in the sample **`values-base.yaml`**; set **`false`** if you do not use an Ingress (for example you rely on **`kubectl port-forward`** or a manually applied Ingress—see **`vss-ingress-example.yaml`** in this directory).

**What gets created**

- **`Ingress`** name **`vss-ingress`** in the release namespace. Override with **`vssIngress.resourceName`**.
- **`spec.ingressClassName`**: from **`vssIngress.ingressClassName`** (default **`haproxy`**).
- Path rules from the [canonical route table](../../services/common/README.md): **`/`**, **`/api/chat`**, **`/api/agent`**, **`/api/vss-chat`**, **`/api/proxy`** → **vss-agent-ui**; **`/api`**, **`/chat`**, **`/websocket`**, **`/static`**, **`/docs`**, **`/redoc`**, **`/generate`**, **`/openapi.json`** → **vss-agent**; **`/vst`** and **`/storage`** → **vss-vios-ingress**; **`/rtvi-vlm`** → RT-VLM; **`/phoenix`** → Phoenix on the main host; **`/llm`** → the in-cluster LLM NIM when that NIM is enabled. Remote-only inference (`nims.enabled=false`) does not mount **`/llm`**. A second host is added only when **`vssIngress.phoenixHost`** is set.
- RT-VLM is reached at **`<host>/rtvi-vlm/v1`**. It is no longer published at the origin root (**`<host>/v1`**), so a client no longer has to know which profile answered to find it.

After install, confirm the **`Ingress`** exists (replace **`<NAMESPACE>`** with your release namespace):

```bash
kubectl get ingress -n <NAMESPACE>
```

Expect **`NAME`** **`vss-ingress`** when **`vssIngress.enabled`** is **`true`** and **`vssIngress.resourceName`** is empty.

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

**Using another controller** (for example NGINX or Traefik): set **`vssIngress.ingressClassName`** to that controller’s **`IngressClass`** name. Path **routing** is standard **`Ingress`** and works on any controller, but path **rewriting** is not. The template always emits a **`haproxy.org/path-rewrite`** annotation, and controllers other than HAProxy ignore it, so these prefixes are forwarded to the backend instead of being stripped:

```text
^/storage/(.*) -> /vst/storage/\1     ^/storage -> /vst/storage
/rtvi-vlm/(.*) -> /\1                 /rtvi-vlm -> /
/phoenix/(.*)  -> /\1                 /phoenix  -> /
/llm/(.*)      -> /\1                 /llm      -> /
```

Without an equivalent rewrite, **`/rtvi-vlm/v1/models`** reaches RT-VLM as **`/rtvi-vlm/v1/models`** and returns 404. Supply your controller’s own rewrite through **`vssIngress.annotations`**.

These annotations are applied to the **whole** **`Ingress`**, so whatever you supply must
select the paths it rewrites and leave the other routes (**`/`**, **`/api`**, **`/vst`**,
**`/openapi.json`**, …) untouched.

**Traefik** — three of the four prefixes are stripped to the root and **`/storage`** is
replaced, so it takes two middlewares chained in order. Create them in the release namespace:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: vss-strip-prefix
spec:
  stripPrefix:
    prefixes: ["/rtvi-vlm", "/phoenix", "/llm"]   # only these paths are altered
---
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: vss-storage-path
spec:
  replacePathRegex:
    regex: "^/storage(.*)"
    replacement: "/vst/storage$1"
```

Then reference both, in order, replacing **`<NAMESPACE>`** with the release namespace:

```yaml
vssIngress:
  enabled: true
  ingressClassName: traefik
  annotations:
    traefik.ingress.kubernetes.io/router.middlewares: "<NAMESPACE>-vss-strip-prefix@kubernetescrd,<NAMESPACE>-vss-storage-path@kubernetescrd"
```

**NGINX** — **`nginx.ingress.kubernetes.io/rewrite-target`** cannot express this table on this
**`Ingress`**. It applies one target to every path, while **`/storage`** needs a different target
from the other three and most routes must not be rewritten at all; a capture-group target such as
**`/$2`** additionally requires **`use-regex`** paths, which this chart does not emit (every path
is **`Prefix`** or **`Exact`**). Route NGINX to the prefixes that need no rewriting and create a
separate **`Ingress`** per rewrite group, each with its own **`rewrite-target`**, or use a
controller whose middleware can be scoped per path.

Setting **`haproxy.org/path-rewrite`** in **`vssIngress.annotations`** replaces the generated table for **every** backend, not just the one you meant to change; omit that key unless you intend to define all of the rules above yourself.

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

**2. Install or upgrade this chart** with **`vssIngress.enabled: true`**, **`vssIngress.ingressClassName: haproxy`**, and **`global.externalHost`** set to the hostname clients use (DNS or **`nip.io`** must resolve to your ingress entry point). **`vssIngress`** is optional only when you expose the stack another way (port-forward, NodePort, or your own Ingress).

**Note:** How you expose the Ingress depends on your cloud or service provider. You may use a LoadBalancer Service or a cloud-specific Ingress (for example OCI LB, AWS ALB, or GKE Ingress). Follow your provider's documentation.

## Upgrade and uninstall

**Upgrade**:

```bash
helm upgrade <RELEASE_NAME> ./dev-profile-base -f dev-profile-base/values-base.yaml -n <NAMESPACE>
```

**Uninstall**:

Back up uploaded video and any database metadata you need before uninstall. Whether data survives after a claim is deleted depends on the PersistentVolume reclaim policy and the StorageClass provisioner, not on Helm alone.

```bash
helm uninstall <RELEASE_NAME> -n <NAMESPACE>
```

What `helm uninstall` does for this chart:

| Resource | Helm uninstall behavior | Notes |
|----------|-------------------------|-------|
| Shared VST PVCs (`<RELEASE_NAME>-vst-data`, `<RELEASE_NAME>-vst-video`, `<RELEASE_NAME>-vst-streamer-videos`) | Can be deleted with the release | These chart-managed claims have no `helm.sh/resource-policy: keep` annotation. Uploaded media can be lost. |
| Other release-owned PVCs (for example NIM or database claims created by this release) | Can be deleted with the release | Confirm reclaim policy / provisioner before assuming data remains on disk. |
| `NIMCache` (namespaced; default name `nemotron-35-lightning-30b-a3b`) | Retained | Templates set `helm.sh/resource-policy: keep`. `NIMCache` is a namespaced CRD object, not cluster-scoped. |

Do **not** run namespace-wide deletes such as `kubectl delete nimcache --all` or `kubectl delete pvc --all`. Those commands remove every matching object in the namespace, including leftovers from other releases.

After uninstall, inspect ownership, confirm you intend to discard the data, then delete only the selected leftovers for this release:

```bash
# List leftovers that may remain after uninstall.
kubectl get nimcache,pvc -n <NAMESPACE> \
  -l app.kubernetes.io/instance=<RELEASE_NAME>
kubectl get pvc -n <NAMESPACE> | grep -E "(^| )<RELEASE_NAME>-"

# Delete only named leftovers you have verified belong to this release.
# Example for the default LLM NIMCache (kept by Helm):
kubectl delete nimcache nemotron-35-lightning-30b-a3b -n <NAMESPACE>

# Example for shared VST claims if they remain and you no longer need the media:
kubectl delete pvc \
  <RELEASE_NAME>-vst-data \
  <RELEASE_NAME>-vst-video \
  <RELEASE_NAME>-vst-streamer-videos \
  -n <NAMESPACE>
```
