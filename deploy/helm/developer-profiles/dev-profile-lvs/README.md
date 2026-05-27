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

# VSS Helm Chart (LVS profile)

Helm chart for deploying **VSS LVS (Long Video Summarization) Developer Profile** on Kubernetes.

## GPU requirements

With default **`values.yaml`** and typical LVS install (LLM NIM enabled, **`vss-summarization`**, **`vss-vios-streamprocessing`**, **`vss-rtvi-vlm`**), the stack requests **4 GPUs** (`nvidia.com/gpu: 1` each). Pod names include the Helm release name and a replica hash; the table lists the **workload** substring from `kubectl get pods`.

| Workload | GPU |
|----------|-----|
| `vss-summarization` | 1 |
| `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
| `vss-vios-streamprocessing` | 1 |
| `vss-rtvi-vlm` (integrated Cosmos checkpoint) | 1 |
| **Total** | **4** |

To run the RTVI-VLM against a shared/remote VLM endpoint instead of the integrated checkpoint, set `rtvi.vss-rtvi-vlm.useSharedNim=true` and configure the target VLM endpoint/model values.

## RTVI-VLM integration (always on)

The LVS profile always deploys **`vss-rtvi-vlm`**. VLM calls from both clients are routed through this in-cluster service:

- **`vss-agent`**: `video_understanding` picks the `rtvi_vlm` LLM profile (`configs/vss-agent/config.yml`) because `VLM_MODEL_TYPE=rtvi`. `RTVI_VLM_BASE_URL` resolves to the in-cluster `vss-rtvi-vlm` Service via `agent.vss-agent.rtviVlmServiceName` (default `vss-rtvi-vlm`).
- **`vss-summarization`**: receives `RTVI_VLM_URL=http://<release>-vss-rtvi-vlm:8000`, `RTVI_VLM_URL_PASSTHROUGH=true` via `vss-summarization.extraEnv` — LVS backend forwards `/generate_captions` to the RTVI pod.

Key values (see `values.yaml` for defaults and the full `rtvi.vss-rtvi-vlm.env` list):

| Key | Default | Notes |
|-----|---------|-------|
| `rtvi.enabled` | `true` | Umbrella switch; set `false` only if you intentionally bypass RT-VLM. Also configure `vss-agent` `VLM_MODEL_TYPE=nim` and remove `vss-summarization.extraEnv` entries that target the RTVI service. |
| `rtvi.vss-rtvi-vlm.enabled` | `true` | Deploy the RTVI-VLM pod. |
| `rtvi.vss-rtvi-vlm.useSharedNim` | `false` | Load the integrated checkpoint in the RT-VLM pod. Sets `MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` and `VLM_MODEL_TO_USE=cosmos-reason2`. |
| `rtvi.vss-rtvi-vlm.modelPath` | `ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` | Integrated RT-VLM checkpoint path used when `useSharedNim=false`. |
| `infra.kafka.enabled` | `true` | Deploy Kafka for RTVI-VLM event publishing and create the default VSS topics, including `mdx-vlm` and `mdx-vlm-incidents`. |
| `rtvi.vss-rtvi-vlm.waitForKafka.enabled` | `true` | The RTVI-VLM init container waits for Kafka and required RTVI topics before startup. |
| `rtvi.vss-rtvi-vlm.env` | full list | Replaces the subchart default `env`. Override individual values (e.g. edge `VLM_INPUT_*`) by editing the list in your overlay. |
| `vss-summarization.extraEnv` | 2 RTVI vars | `RTVI_VLM_URL`, `RTVI_VLM_URL_PASSTHROUGH`. `RTVI_VLM_URL` is rendered with `tpl`, so it picks up `{{ .Release.Name }}` when `global.useReleaseNamePrefix` is true. |
| `agent.vss-agent.rtviVlmEnabled` / `rtviVlmServiceName` | `true` / `vss-rtvi-vlm` | Parity flags; `RTVI_VLM_BASE_URL` in the `env` list reads `rtviVlmServiceName`. |
| `agent.vss-agent.env` → `VLM_MODEL_TYPE` | `rtvi` | Flip to `nim` only to bypass RTVI for the agent (video_understanding will then hit the VLM NIM directly). |

Remote VLM + RTVI: RTVI-VLM also supports remote VLM endpoints when `global.vlmBaseUrl` is set, `nims.enabled=false`, and `rtvi.vss-rtvi-vlm.useSharedNim=true`; see `deploy/helm/services/rtvi/charts/rtvi-vlm/templates/deployment.yaml` for the selection logic.


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
  - Required when **`nims`** subcharts are enabled (`NIMCache` / `NIMService`). LVS deploys the LLM NIM by default; the Cosmos VLM NIM is optional because RT-VLM loads its integrated checkpoint unless you enable a shared VLM NIM.
  - Install **after** the GPU Operator. See [NIM Operator installation](https://docs.nvidia.com/nim-operator/latest/install.html).

- **Volume provisioner (e.g. local-path)**
  - A **StorageClass** must exist on the cluster (VST, Elasticsearch, and related volumes). Set **`global.storageClass`** in your Helm values override to that class’s **`metadata.name`** (see [Prepare the values file](#1-prepare-the-values-file)).
  - **Bare-metal clusters:** Install **local-path** (see [rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner/tree/master)).
  - **Default StorageClass:** If your class (for example **`local-path`**) is not already the default, set it as the default StorageClass:

    ```bash
    kubectl patch storageclass local-path -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
    ```

    Replace **`local-path`** with your StorageClass **`metadata.name`** if it differs.

### Chart / tooling

- **Helm** 3.x
- **Kubectl**
- **GPUs**: see [GPU requirements](#gpu-requirements) (4 with defaults).
- **NVIDIA NIM** (if using NIM subcharts): NIM Operator on the cluster (see [Prerequisites](#prerequisites) above).
- **NGC**: API key for NIM, image pull / chart secret creation (see below).
- **StorageClass** for PVCs: set **`global.storageClass`** to a class that exists on the cluster (see [Prerequisites](#prerequisites) above—**Volume provisioner**).

## Quick start

### 1. Prepare the values file

Edit **`values-lvs.yaml`** and set at least:

| Key | Description |
|-----|-------------|
| **`ngc.apiKey`** | Your NGC API key (for image pull and NIM). Chart uses **`ngc.createSecrets: true`** by default. Do not commit the key. |
| **`global.storageClass`** | StorageClass for **VST**, **Elasticsearch**, and related PVCs (e.g. **`oci-bv-high`**, **`local-path`**). |
| **`global.externalScheme`** | **`http`** or **`https`**. |
| **`global.externalHost`** | Hostname or IP the browser uses (e.g. `vss.YOUR_IP.nip.io`). Required for a typical external install when subchart URL fields are omitted. |
| **`global.externalPort`** | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`global.kibanaPublicUrl`** | Public Kibana base URL for the Dashboard tab (no **`/kibana`** path suffix), e.g. **`http://kibana.vss.YOUR_IP.nip.io`**. Align with DNS or **`nip.io`** so it matches how users open Kibana (often the same pattern as **`vssIngress`** **`kibana.<host>`**). |
| **`llmNameSlug`** | Replace the placeholder with the subchart name of the **LLM** NIM you enable under **`services/nims/charts/`** (e.g. `nvidia-nemotron-nano-9b-v2`). Keep **`agent.vss-agent.llmName`** / **`global.llmName`** in **`values.yaml`** aligned with the same NGC model. |
| **`vlmNameSlug`** | Use **`none`** for the default LVS flow; RT-VLM loads the integrated checkpoint instead of deploying a VLM NIM subchart. |
| **`nims`** | **`nims.enabled`**: umbrella for all NIM subcharts. Use **`nims.gpuType`** to select model tuning from **`gpuProfiles`** and **`nims.nemotron.enabled`** / **`nims.cosmos.enabled`** to choose the models to deploy. **`nims.cosmos.enabled`** is **`false`** by default because LVS uses the integrated RT-VLM checkpoint. Set **`nims.enabled`** to **`false`** when using [remote LLM/VLM](#remote-llm-and-vlm) only. |
| **`global.llmBaseUrl`** / **`global.vlmBaseUrl`** (remote) | HTTP(S) base URLs when LLM/VLM are **not** deployed by this chart. Use with **`nims.enabled: false`**. Shared by **vss-agent** and **vss-summarization**; must be reachable from those pods. Leave **`""`** for in-cluster **NIM** services. |
| **`global.llmName`** / **`global.vlmName`** (remote) | NGC-style model ids for **both** **vss-agent** and **vss-summarization**; must match remote endpoints. Defaults in **`values-lvs.yaml`** match common NGC models. |
| **`vssIngress`** (optional) | Set **`vssIngress.enabled`** to **`true`** to create a Kubernetes **`Ingress`** for UI, agent, VST, and (when enabled) **Kibana** and **Phoenix** on **`kibana.<host>`** / **`phoenix.<host>`**. Requires an existing **IngressClass** (see [VSS Ingress (`vssIngress`)](#vss-ingress-vssingress)). **`global.externalHost`** must be set unless **`vssIngress.host`** is set. Sample **`values-lvs.yaml`** enables this by default. |

#### `values-lvs.yaml` vs chart `values.yaml`

| File | Role |
|------|------|
| **`values-lvs.yaml`** (or host-specific overlay) | **Your** override file: NGC, **`global.*`**, slugs, **`nims`**, **`vss-summarization`** endpoints, **`vssIngress`**, or remote **`global.llmBaseUrl`** / **`global.vlmBaseUrl`** with **`nims.enabled: false`**, etc. Pass with **`-f`**. |
| **`values.yaml`** | **Chart defaults** shipped with the profile (full value tree). You normally **do not** edit it; add only the keys you need to your override file (**`values-lvs.yaml`**) and Helm merges your file on top of these defaults. |

Use the table below for additional keys. Order follows **`values.yaml`**. **`ngc`**, **`global`**, **VST** shared storage, ingress, and other shared subchart keys are described explicitly in this table.

##### Optional overrides — `values.yaml` keys (reference)

| Key / group | Default | Description |
|-------------|---------|-------------|
| **`profile`** | **`lvs`** | Must stay **`lvs`** for this chart. |
| **`mode`** | **`""`** | "" for dev-profile-lvs chart. |
| **`llmNameSlug`** | `""` | Replace the placeholder with the subchart name of the **LLM** NIM you enable under **`services/nims/charts/`** (e.g. `nvidia-nemotron-nano-9b-v2`). Set in **`values-lvs.yaml`** (or your overlay). |
| **`vlmNameSlug`** | `none` | RT-VLM loads the integrated checkpoint by default instead of deploying a VLM NIM subchart. |
| **`ngc.createSecrets`** | **`true`** | When **`true`** and **`ngc.apiKey`** is set, the chart creates two secrets (see **`templates/ngc-secrets.yaml`**): **`ngc-api`** (Opaque: **`NGC_API_KEY`** / **`NGC_CLI_API_KEY`**) for NGC API access, and **`ngc-secret`** (**dockerconfigjson**) for pulling images from nvcr.io. Set **`false`** only if you create both secrets yourself; then set **`global.ngcApiSecret`** and **`global.imagePullSecrets`** to match your names. |
| **`ngc.apiKey`** | **`""`** | With **`ngc.createSecrets: true`**, set your NGC API key here; it backs both created secrets. With **`createSecrets: false`**, omit (or leave empty) and install the Opaque + docker secrets out of band; align **`global.*`** below with those objects. Optional: **`ngc.apiKeySecretName`** / **`ngc.dockerSecretName`** rename the generated secrets—update **`global.ngcApiSecret.name`** and **`global.imagePullSecrets`** accordingly. Set in **`values-lvs.yaml`** (or your overlay). |
| **`global.imagePullSecrets`** | **`[{ name: ngc-secret }]`** | Pod **image pull** credentials for nvcr.io. Must reference the **Docker registry** secret (default **`ngc-secret`**, i.e. **`ngc.dockerSecretName`**). This is separate from the NGC **API** key secret. |
| **`global.ngcApiSecret`** | **`name: ngc-api`**, **`key: NGC_API_KEY`** | Tells NIM (**`NIMService`** / **`NIMCache`**) and related workloads which **Opaque** secret holds the NGC **API** key: **`name`** defaults to **`ngc-api`** (**`ngc.apiKeySecretName`**), **`key`** defaults to **`NGC_API_KEY`** (the key the chart writes in that secret). Change these if you use a different secret name or data key. |
| **`global.externalScheme`** | **`""`** | Set in **`values-lvs.yaml`** (e.g. **`http`** or **`https`**). With **`externalHost`** / **`externalPort`**, builds browser-facing URLs for **`vss-agent-ui`**, **`vss-agent`**, and **`vss-vios-ingress`** when their own URL fields are empty. |
| **`global.externalHost`** | **`""`** | Hostname or IP clients use in the browser (e.g. **`vss.YOUR_IP.nip.io`**). |
| **`global.externalPort`** | **`""`** | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`global.kibanaPublicUrl`** | **`""`** | Public Kibana base URL (no **`/kibana`** path suffix). Prefer this over duplicating **`infra.kibana.kibanaPublicUrl`** unless Kibana must use a different host than the main UI. |
| **`global.llmBaseUrl`** | **`""`** | **Single place** for remote LLM base URL shared by **vss-agent** and **vss-summarization** ( **`LLM_BASE_URL`**, **`LVS_LLM_BASE_URL`** ). LVS often needs **`/v1`** on the path (e.g. **`http://host:31081/v1`**). Subchart **`agent.vss-agent.llmBaseUrl`** or **`vss-summarization.llmBaseUrl`** overrides when set. |
| **`global.vlmBaseUrl`** | **`""`** | Same for VLM (**`VLM_BASE_URL`**, **`VIA_VLM_ENDPOINT`**). |
| **`global.llmName`** | **`nvidia/nvidia-nemotron-nano-9b-v2`** | NGC model id for **both** **vss-agent** (**`LLM_NAME`**) and **vss-summarization** (**`LVS_LLM_MODEL_NAME`**). Override with **`agent.vss-agent.llmName`** or **`vss-summarization.llmName`** when a workload needs a different id (e.g. remote NIM). |
| **`global.vlmName`** | **`nim_nvidia_cosmos-reason2-8b_hf-1208`** | Same for VLM (**`VLM_NAME`**, **`VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME`**). |
| **`global.storageClass`** | unset in repo **`values.yaml`** | Set in **`values-lvs.yaml`**; used for **Elasticsearch**, **`vios.vstStorage`** PVCs, and other subcharts that inherit **`global.storageClass`**. |
| **`vios.vstStorage.createSharedPvcs`** | **`true`** | **`true`:** the **`vios`** umbrella creates **PersistentVolumeClaims** so **sensor** and **streamprocessing** share on-disk folders for VST data and video; data survives pod restarts but your cluster must have a working **StorageClass** (see **`global.storageClass`**). **`false`:** no shared PVCs from **`vios`**; behavior depends on **`vios.vss-vios-*`** persistence settings. |
| **`vios.vstStorage.accessMode`** | **`ReadWriteOnce`** | Access mode for the three shared VST PVCs (see **`helm/services/vios/templates/vst-storage-pvc.yaml`**). |
| **`vios.vstStorage.vstData`** | **`size`:** **10Gi**, **`storageClass`:** **`""`** | Claim size for the shared **VST data** volume. Leave **`storageClass`** empty to inherit **`global.storageClass`**; set it only if this volume needs a different class than the rest of the chart. |
| **`vios.vstStorage.vstVideo`** | **`size`:** **20Gi**, **`storageClass`:** **`""`** | Claim size for the shared **VST video** volume; same **`storageClass`** rules as **`vstData`**. |
| **`vios.vstStorage.streamerVideos`** | **`size`:** **20Gi**, **`storageClass`:** **`""`** | Claim size for the shared **streamer upload** video volume; same **`storageClass`** rules as **`vstData`**. |
| **`infra.phoenix.enabled`** | **`true`** | Set **`false`** to disable Phoenix ( **`infra`** subchart). |
| **`infra.redis.enabled`** | **`true`** | Set **`false`** to disable Redis. |
| **`vios.enabled`** | **`true`** | Master switch for the **`vios`** umbrella (all bundled **`vss-vios-*`** subcharts). Set **`false`** to omit the entire VST microservice stack from the release. |
| **`vios.vss-vios-postgres.enabled`** | **`true`** | Set **`false`** to disable centralized DB. Storage sizing/class: subchart **`values.yaml`** or overrides under **`vios.vss-vios-postgres`**. |
| **`vios.vss-vios-sensor.streamProcessorService`** | **`sdrc`** | Sensor registers streams through the SDRC controller service (**`sdrc-controller:5003`** by default). |
| **`vios.vss-vios-sensor.enabled`** | **`true`** | Set **`false`** to disable the **sensor** workload. |
| **`vios.vss-vios-sensor.persistence`** | **`vstData`** / **`vstVideo`**: **`enabled: true`**, **`create: false`**, **`existingClaim: ""`** | Controls whether **sensor** mounts two shared folders (**data** and **video**). **Typical setup:** leave **`existingClaim`** blank—Helm wires the pods to the PVCs created when **`vios.vstStorage.createSharedPvcs`** is **`true`**. **Custom PVCs:** set **`existingClaim`** to your claim name for that volume. **Disable a mount:** set that volume’s **`enabled`** to **`false`** (that path is not mounted). |
| **`vios.vss-vios-streamprocessing.enabled`** | **`true`** | Set **`false`** to disable **vss-vios-streamprocessing**. |
| **`vios.vss-vios-streamprocessing.persistence`** | **`vstData`**, **`vstVideo`**, **`streamerVideos`**: same idea as sensor | **Streamprocessing** mounts up to **three** shared folders: VST **data**, VST **video**, and **streamer** uploads. Use blank **`existingClaim`** to use the shared PVCs from **`vios`** when **`vios.vstStorage.createSharedPvcs`** is **`true`**, or set **`existingClaim`** / **`enabled`** per volume the same way as for **sensor**. |
| **`vios.vss-vios-ingress.enabled`** | **`true`** | Deploys the in-cluster **VST ingress** (nginx). |
| **`vios.vss-vios-ingress.externallyAccessibleIp`** | **`""`** | Hostname or IP address advertised to VST/nginx for external access. If unset, the subchart uses **`global.externalHost`**; if that is unset, it defaults to **`127.0.0.1`**. Override this value only when the VST ingress must use a hostname or IP that differs from **`global.externalHost`**. |
| **`vssIngress.enabled`** | **`false`** in chart **`values.yaml`**; **`true`** in sample **`values-lvs.yaml`** | When **`true`**, renders **`templates/vss-ingress.yaml`**: paths on the main host for **vss-agent-ui**, **vss-agent**, **vss-vios-ingress**; optional hosts **`kibana.<host>`** and **`phoenix.<host>`** when **Kibana** / **Phoenix** are enabled. No **`Ingress`** if **`global.externalHost`** and **`vssIngress.host`** are both empty. |
| **`vssIngress.ingressClassName`** | **`haproxy`** | **`spec.ingressClassName`** on the **`Ingress`**. Must match an **`IngressClass`** on the cluster (e.g. from **HAProxy Kubernetes Ingress**). |
| **`vssIngress.host`** | **`""`** | Ingress hostname for the main rules; if empty, **`global.externalHost`** is used. |
| **`vssIngress.vssUiPort`** | **`3000`** | Backend port for **vss-agent-ui** paths. |
| **`vssIngress.vssAgentPort`** | **`8000`** | Backend port for **vss-agent** paths. |
| **`vssIngress.vstIngressPort`** | **`30888`** | Backend port for **vss-vios-ingress** (**`/vst`**). |
| **`vssIngress.kibanaHost`** | **`""`** | Host rule for Kibana; default **`kibana.<global.externalHost or vssIngress.host>`**. |
| **`vssIngress.phoenixHost`** | **`""`** | Host rule for Phoenix; default **`phoenix.<global.externalHost or vssIngress.host>`**. |
| **`vssIngress.kibanaPort`** | **`5601`** | Kibana **Service** port when Kibana is enabled. |
| **`vssIngress.phoenixPort`** | **`6006`** | Phoenix **Service** port when Phoenix is enabled. |
| **`vss-summarization.enabled`** | **`true`** | Set **`false`** to disable the **LVS** summarization service. |
| **`vss-summarization.elasticsearchHost`** | **`""`** | Elasticsearch hostname for **vss-summarization** **`ES_HOST`**. When empty, defaults to **`<release>-elasticsearch`**. |
| **`vss-summarization.elasticsearchPort`** | **`9200`** | Elasticsearch HTTP port (**`ES_PORT`**). |
| **`vss-summarization.llmService`** | **`""`** | NIM subchart **name segment** used to build **`LVS_LLM_BASE_URL`** as **`http://<release>-<value>:8000/v1`** when **`global.llmBaseUrl`** and **`vss-summarization.llmBaseUrl`** are empty. When empty, defaults to **`nvidia-nemotron-nano-9b-v2`**; set to match your enabled **LLM** under **`nims`** (same as **`llmNameSlug`**). |
| **`vss-summarization.vlmService`** | **`vss-rtvi-vlm`** | Service segment used to build **`VIA_VLM_ENDPOINT`** when **`global.vlmBaseUrl`** and **`vss-summarization.vlmBaseUrl`** are empty. Default LVS points this at RT-VLM, not a Cosmos NIM service. |
| **`vss-summarization.llmBaseUrl`** | **`""`** | Optional **LVS-only** override of **`global.llmBaseUrl`**. |
| **`vss-summarization.vlmBaseUrl`** | **`""`** | Optional **LVS-only** override of **`global.vlmBaseUrl`**. |
| **`vss-summarization.llmName`** | **`""`** | Optional **LVS-only** override of **`global.llmName`** (**`LVS_LLM_MODEL_NAME`**). |
| **`vss-summarization.vlmName`** | **`""`** | Optional **LVS-only** override of **`global.vlmName`** (**`VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME`**). |
| **`infra.elasticsearch.enabled`** | **`true`** | Set **`false`** to disable the in-cluster **Elasticsearch** deployment. |
| **`infra.elasticsearch.persistence.data.size`** | **10Gi** | PVC size for Elasticsearch **data** volume. |
| **`infra.elasticsearch.persistence.logs.size`** | **5Gi** | PVC size for Elasticsearch **logs** volume (LVS chart exposes both **data** and **logs** sizes). |
| **`infra.elasticsearch.persistence.storageClass`** | **`""`** | **StorageClass** for Elasticsearch PVCs; leave empty to inherit **`global.storageClass`**, or set explicitly. |
| **`infra.kibana.enabled`** | **`true`** | Set **`false`** to disable the in-cluster **Kibana** deployment. |
| **`infra.kibana.elasticsearchHosts`** | **`""`** | Elasticsearch URL list **Kibana** connects to. When empty, defaults to **`http://<release>-elasticsearch:9200`**. |
| **`infra.kibana.kibanaPublicUrl`** | **`""`** | Browser-facing **Kibana** base URL. When empty, templates use **`global.kibanaPublicUrl`** if set, else **`http://<release>-kibana:5601`**. |
| **`infra.elasticsearch.init.enabled`** | **`true`** | Set **`false`** to skip the **Job** that runs Elasticsearch index/ILM setup for this profile. |
| **`infra.elasticsearch.init.elasticsearchUrl`** | **`""`** | Elasticsearch URL the init **Job** targets. When empty, defaults to **`http://<release>-elasticsearch:9200`**. |
| **`infra.kibana.init.enabled`** | **`true`** | Set **`false`** to skip the **Job** that applies Kibana saved objects / dashboards for LVS. |
| **`infra.kibana.init.kibanaUrl`** | **`""`** | **Kibana** URL the init **Job** calls. When empty, defaults to **`http://<release>-kibana:5601`**. |
| **`infra.kibana.init.elasticsearchUrl`** | **`""`** | **Elasticsearch** URL the init **Job** uses. When empty, defaults to **`http://<release>-elasticsearch:9200`**. |
| **`agent.enabled`** | **`true`** | Set **`false`** to skip the **`agent`** umbrella (**`deploy/helm/services/agent`**). |
| **`agent.vss-agent.enabled`** | **`true`** | Set **`false`** to disable the **vss-agent** deployment only. |
| **`agent.vss-agent.profile`** | **`lvs`** | Passed to the **vss-agent** subchart for LVS-specific deployment/env behavior. ConfigMap data is **`configs/vss-agent/config.yml`** (flat path under this chart). |
| **`agent.vss-agent.lvsBackendService`** | **`vss-summarization`** | Kubernetes **Service** name for the **LVS** backend (must match release + subchart naming). |
| **`agent.vss-agent.vstInternalUrl`** | **`""`** | In-cluster **VST** URL for the agent when the default wiring is insufficient. |
| **`agent.vss-agent.vstInternalIp`** | **`""`** | In-cluster **VST** host/IP override when defaults are insufficient. |
| **`agent.vss-agent.vssAgentExternalUrl`** | **`""`** | External **vss-agent** URL override for browser / callbacks when **`global.external*`** is not enough. |
| **`agent.vss-agent.vssAgentVersion`** | **`3.2.0-26.05.2-b0ba77ae135d`** | Optional version label / env; adjust per release. |
| **`agent.vss-agent.llmName`** | **`""`** | Optional **vss-agent-only** override of **`global.llmName`** (**`LLM_NAME`**). |
| **`agent.vss-agent.vlmName`** | **`""`** | Optional **vss-agent-only** override of **`global.vlmName`** (**`VLM_NAME`**). |
| **`agent.vss-agent.llmBaseUrl`** | **`""`** | Optional **vss-agent-only** override of **`global.llmBaseUrl`**. |
| **`agent.vss-agent.vlmBaseUrl`** | **`""`** | Optional **vss-agent-only** override of **`global.vlmBaseUrl`**. |
| **`agent.vss-agent.evalLlmJudgeName`** | **`""`** | Optional eval judge model id. When empty, the **vss-agent** subchart defaults to **`llmName`**. |
| **`agent.vss-agent.evalLlmJudgeBaseUrl`** | **`""`** | Optional base URL for the eval judge endpoint. When empty, the subchart defaults alongside **`llmBaseUrl`**. |
| **`agent.vss-agent.reportsBaseUrl`** | **`""`** | Base URL for report links. When empty, templates derive a value from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.vstExternalUrl`** | **`""`** | External **VST** URL passed to the agent. When empty, derived from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.externalIp`** | **`""`** | Hostname or IP override for agent-facing external access when **`global.external*`** is not sufficient. |
| **`agent.vss-agent.env`** | *(see **`values.yaml`**)* | Full **`env`** list (Option B). **`LVS_BACKEND_URL`** is set via **`tpl`** from **`lvsBackendService`** and **`global.useReleaseNamePrefix`** so it points to the in-cluster LVS backend service. |
| **`agent.vss-agent.extraEnv`** | *(omit)* | Optional **`{ name, value }`** appended last. |
| **`vss-agent-ui.enabled`** | **`true`** | Set **`false`** to disable the **vss-agent-ui** deployment. |
| **`vss-agent-ui.envOverrides`** | (see **`values.yaml`**) | Full **`NEXT_PUBLIC_*`** defaults aligned with **dev-profile-base**; LVS sets subtitle (**`Vision (LVS)`**) and **`NEXT_PUBLIC_ENABLE_DASHBOARD_TAB`**. |
| **`vss-agent-ui.agentApiUrlBase`** | **`""`** | Base URL for the **vss-agent** HTTP API (browser **`NEXT_PUBLIC_AGENT_API_URL_BASE`**, typically ends with **`/api/v1`**). If unset, built from **`global.externalScheme`** / **`externalHost`** / **`externalPort`** as **`<global>/api/v1`**, else defaults to in-cluster **`http://<release>-vss-agent:8000/api/v1`**. |
| **`vss-agent-ui.vstApiUrl`** | **`""`** | **VST** HTTP API URL for the browser (**`NEXT_PUBLIC_VST_API_URL`**). If unset, built as **`<global>/vst/api`**, else **`http://<release>-vss-vios-ingress:30888/vst/api`**. |
| **`vss-agent-ui.chatCompletionUrl`** | **`""`** | HTTP chat completion URL (**`NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL`**). If unset, built as **`<global>/chat/stream`**, else **`http://<release>-vss-agent:8000/chat/stream`**. |
| **`vss-agent-ui.websocketChatUrl`** | **`""`** | WebSocket chat URL (**`NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL`**). If unset and **`global.externalHost`** is set, built as **`<ws-scheme>://<host>[:port]/websocket`** (**`ws`** / **`wss`** from **`global.externalScheme`**). If both this and **`global.externalHost`** are empty, the chart may omit WebSocket env vars; set explicitly for port-forward or custom routing. |
| **`vss-agent-ui.dashboardKibanaBaseUrl`** | **`""`** | Override Kibana base URL for the Dashboard tab when **`global.kibanaPublicUrl`** / **`infra.kibana.kibanaPublicUrl`** are not used. |
| **`nims.enabled`** | **`true`** | Master switch for the **`nims`** umbrella subchart. When **`false`**, no **NIM** model workloads or **`NIMService`** / **`NIMCache`** objects are installed. Use **`false`** with **`global.llmBaseUrl`**, **`global.vlmBaseUrl`**, **`global.llmName`**, and **`global.vlmName`** for remote-only LLM/VLM (**vss-agent** and **vss-summarization**). |
| **`nims.<model>.enabled`** | per model in **`values.yaml`** | Enables or disables one bundled **NIM** model. Default LVS enables the LLM NIM and disables the Cosmos VLM NIM because RT-VLM loads the integrated checkpoint. |
| **`nims.gpuType`** | **`H100`** | Selects **`gpuProfiles`** tuning for the bundled **`nemotron`** and **`cosmos`** NIM ConfigMaps. Supported values include **`H100`**, **`L40S`**, and **`RTXPRO6000BW`**. |

### Remote LLM and VLM

When LLM and VLM run **outside** this release, set **`nims.enabled`** to **`false`** and configure **`global.llmBaseUrl`**, **`global.vlmBaseUrl`**, **`global.llmName`**, and **`global.vlmName`** in **`values-lvs.yaml`** (or **`--set`**). **vss-summarization** and **vss-agent** both consume these globals unless overridden with **`vss-summarization.llmBaseUrl`**, **`agent.vss-agent.llmBaseUrl`**, etc. Endpoints must be reachable from pods in the release namespace. LVS often expects a **`/v1`** path on the LLM URL when talking to OpenAI-compatible NIM APIs.

### 2. Install

```bash
# Clone the repository. For a specific branch or tag, add: -b <name-or-tag> (before the URL).
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deploy/helm/developer-profiles

helm dependency build ./dev-profile-lvs

# Update the values-lvs.yaml and install the chart
helm upgrade --install <RELEASE NAME> ./dev-profile-lvs \
  -f dev-profile-lvs/values-lvs.yaml \
  -n <NAMESPACE> --create-namespace

# OR
# Set the minimum required values inline to install the chart
export NGC_CLI_API_KEY='<your NGC API key>'
export STORAGE_CLASS='<Storage Class Name>'
export EXTERNAL_HOST='<EXTERNAL_HOST_IP>'

helm upgrade --install vss-lvs ./dev-profile-lvs \
  -f dev-profile-lvs/values-lvs.yaml \
  -n vss-lvs --create-namespace \
  --set llmNameSlug=nvidia-nemotron-nano-9b-v2 \
  --set vlmNameSlug=none \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS"

# OR — LVS with remote LLM/VLM (no NIM subcharts); URLs must be reachable from vss-agent and vss-summarization pods
# (reuse NGC_CLI_API_KEY, STORAGE_CLASS, EXTERNAL_HOST from the example above)
export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-lvs ./dev-profile-lvs \
  -f dev-profile-lvs/values-lvs.yaml \
  -n vss-lvs --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS" \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set-string global.vlmName="nvidia/cosmos-reason2-8b" \
  --set rtvi.vss-rtvi-vlm.useSharedNim=true
```


## Exposing the stack

**Note:** After install or upgrade, wait until **all** pods in your namespace are **Ready** before using the application in the browser. When **in-cluster NIM** is enabled (**`nims.enabled: true`**), **NIM** model pods need **extra time** (image pull, **`NIMService`** / **`NIMCache`**, warm-up)—this is **common to both base and LVS** profiles. On **LVS**, the **`vss-summarization`** pod also often needs **additional time** (Elasticsearch, backends, NIM reachability). Opening **vss-agent-ui** while NIM or other dependencies are still starting can produce **transient errors** (failed API calls, timeouts, or empty screens). Check progress with **`kubectl get pods -n <NAMESPACE>`** (or **`kubectl get pods -n <NAMESPACE> -w`**) until every workload shows **`Running`** and **`READY`** matches the expected column (e.g. **`1/1`**). With **remote** LLM/VLM only (**`nims.enabled: false`**), NIM startup is skipped, but still wait for **`vss-summarization`** and the rest of the stack.

Set **`global.externalHost`**, **`global.kibanaPublicUrl`** (and scheme/port as needed) in **`values-lvs.yaml`** so **vss-agent-ui**, **vss-agent**, **vss-vios-ingress**, and Kibana links resolve for browsers.

### VSS Ingress (`vssIngress`)

The chart can create a Kubernetes **`Ingress`** (**`templates/vss-ingress.yaml`**) so one main hostname serves UI, API, and VST, with optional hostnames for **Kibana** and **Phoenix** when those subcharts are enabled.

**Prerequisites**

1. An **Ingress controller** must already be installed; **`vssIngress.ingressClassName`** (default **`haproxy`**) must match its **`IngressClass`**.
2. **`global.externalHost`** must be set unless **`vssIngress.host`** overrides the main rule hostname.
3. **`vssIngress.enabled`**: **`true`** in sample **`values-lvs.yaml`**. Set **`false`** to skip Helm **`Ingress`** and use **`kubectl port-forward`**, **`NodePort`**, or manual manifests (**`vss-ingress-example.yaml`** / **`vss-ingress-example-rewrites.yaml`** in this directory).

**What gets created**

- **`Ingress`** **`<release>-vss-ingress`** in the release namespace.
- **`spec.ingressClassName`**: **`vssIngress.ingressClassName`** (default **`haproxy`**).
- Main host: **`/`**, **`/api/chat`** → **vss-agent-ui**; **`/api`**, **`/chat`**, **`/websocket`**, **`/static`** → **vss-agent**; **`/vst`** → **vss-vios-ingress**.
- If **Kibana** is enabled: host **`kibana.<main-host>`** (or **`vssIngress.kibanaHost`**) → **Kibana** (**`vssIngress.kibanaPort`**).
- If **Phoenix** is enabled: host **`phoenix.<main-host>`** (or **`vssIngress.phoenixHost`**) → **Phoenix**.

After install, confirm the **`Ingress`** exists (replace **`<NAMESPACE>`** with your release namespace):

```bash
kubectl get ingress -n <NAMESPACE>
```

Expect **`NAME`** **`<RELEASE_NAME>-vss-ingress`** when **`vssIngress.enabled`** is **`true`**.

**Minimal values** (controller already on cluster)

```yaml
global:
  externalHost: "vss.YOUR_IP.nip.io"
  externalScheme: "http"
  kibanaPublicUrl: "http://kibana.vss.YOUR_IP.nip.io"
vssIngress:
  enabled: true
  ingressClassName: haproxy
  host: ""
```

**Important:** **`vssIngress`** only adds an **`Ingress`**; it does not install a controller. Install **HAProxy Kubernetes Ingress** (or another controller) cluster-wide separately; **`vssIngress.ingressClassName`** must match that controller’s **`IngressClass`**.

### Example: HAProxy and Ingress

**1. Install HAProxy Kubernetes Ingress controller** (once per cluster, or use your cloud ingress):

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

**2. Install or upgrade this chart** with **`vssIngress.enabled: true`**, **`vssIngress.ingressClassName: haproxy`**, **`global.externalHost`**, and **`global.kibanaPublicUrl`** set so DNS (e.g. **`nip.io`**) matches your entry point.

**3. Optional — manual Ingress:** instead of **`vssIngress`**, edit **`vss-ingress-example.yaml`** and **`vss-ingress-example-rewrites.yaml`** (**`<RELEASE_NAME>`**, **`<NAMESPACE>`**, **`<EXTERNAL_HOST>`**), then:

```bash
kubectl apply -f vss-ingress-example.yaml -f vss-ingress-example-rewrites.yaml -n <NAMESPACE>
```


## Upgrade and uninstall

**Upgrade**

```bash
helm upgrade <RELEASE_NAME> ./dev-profile-lvs -f dev-profile-lvs/values-lvs.yaml -n <NAMESPACE>
```

**Uninstall**:

```bash
helm uninstall <RELEASE_NAME> -n <NAMESPACE>
```

Note: PVCs and any cluster-scoped resources are not removed by `helm uninstall`; delete them manually if needed.

```bash
kubectl delete nimcache --all -n <NAMESPACE>
kubectl delete pvc --all -n <NAMESPACE>
```
