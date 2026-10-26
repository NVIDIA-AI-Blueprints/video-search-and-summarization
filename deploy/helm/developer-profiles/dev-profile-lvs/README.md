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

## Deployment mode at a glance

By default this profile is an **in-cluster** deployment:

| Component | Default behavior | Default model name |
|-----------|------------------|--------------------|
| LLM | Deploys the **`nemotron-3.5-lightning-30b-a3b`** NIM through the **`nims`** umbrella chart (`NIMCache` / `NIMService`). | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| VLM / RT-VLM | Deploys **`vss-rtvi-vlm`** in this release. The RT-VLM pod loads the integrated Cosmos Reason3 Nano BF16 checkpoint; the profile does **not** deploy a separate Cosmos VLM NIM by default. | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` |

Switch to **external-service mode** only when the model endpoints already run outside this release. Setting **`global.llmBaseUrl`** or **`global.vlmBaseUrl`** (or the matching **`agent.vss-agent.*BaseUrl`** / **`vss-summarization.*BaseUrl`** overrides) makes those workloads call the supplied external service instead of the default in-cluster service. When both LLM and VLM are external, set **`nims.enabled=false`** and set **`rtvi.vss-rtvi-vlm.useSharedNim=true`** so RT-VLM proxies the external VLM instead of loading the integrated checkpoint.

## Performance profile (`values-perf.yaml`, opt-in)

`values-perf.yaml` is an opt-in overlay for the integrated VLM checkpoint. It does **not** change the Nemotron NIM's engine settings. Those still come from `nims.gpuProfiles[<nims.gpuType>].nemotron35` in `deploy/helm/services/nims/values.yaml`: reasoning stays **enabled** (`enable_thinking:true`), and `NIM_MODEL_PROFILE` stays the same pinned INT4 profile (`vllm-int4-tp1-pp1-32.0`) on every listed GPU, including Blackwell. KV-cache percent, GPU memory fraction, context length, and concurrency are the explicit values in that profile. They are not auto-sized, and they are not NVFP4.

What the overlay does change is the integrated Cosmos Reason3 Nano checkpoint and `nims.gpuType` (default **`H100`** in the overlay). `vlmPrecision.byGpuType` maps **`H100`** and **`L40S`** to **FP8**, and **`RTXPRO6000BW`** to **NVFP4**. `templates/validate-vlm-precision.yaml` fails `helm template` / `helm install` when `global.vlmName` or `rtvi.vss-rtvi-vlm.modelPath` disagrees with that map. The guard is skipped for external VLM (`rtvi.vss-rtvi-vlm.useSharedNim=true`) and when this overlay is not applied (`vlmPrecision` absent).

Apply it after your install values, and set **`nims.gpuType`** to the GPU you are actually installing on:

```bash
helm upgrade --install <RELEASE_NAME> ./dev-profile-lvs \
  -f dev-profile-lvs/values-lvs.yaml \
  -f dev-profile-lvs/values-perf.yaml \
  --set nims.gpuType=<GPU> \
  -n <NAMESPACE> --create-namespace
```

The overlay file itself bakes the **FP8** VLM strings and `nims.gpuType: H100`. For **Blackwell (`RTXPRO6000BW`)**, also set the platform and the NVFP4 VLM strings — the guard fails if they disagree:

```yaml
# values-perf-blackwell.yaml — apply after values-perf.yaml
nims:
  gpuType: RTXPRO6000BW
global:
  vlmName: "nim_nvidia_cosmos3-nano-reasoner_modelopt-nvfp4-full-quantize-final_format_fix"
rtvi:
  vss-rtvi-vlm:
    modelPath: "ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix"
```

## GPU requirements

With default **`values.yaml`** and typical LVS install (LLM NIM enabled, **`vss-vios-streamprocessing`**, **`vss-rtvi-vlm`**), the stack requests **3 GPUs** (`nvidia.com/gpu: 1` each). **`vss-summarization`** does not request a GPU because it delegates VLM inference to **`vss-rtvi-vlm`**. With default **`global.useReleaseNamePrefix: false`**, the **`vss-rtvi-vlm`** Deployment pod gets the workload name plus a replica hash, and the **`vss-vios-streamprocessing`** StatefulSet pod gets the workload name plus a numeric ordinal (`-0`); the table lists the **workload** substring from `kubectl get pods`.

| Workload | GPU |
|----------|-----|
| `nemotron-3.5-lightning-30b-a3b` (NIM) | 1 |
| `vss-vios-streamprocessing` | 1 |
| `vss-rtvi-vlm` (integrated Cosmos checkpoint) | 1 |
| **Total** | **3** |

To run the RTVI-VLM against a shared/remote VLM endpoint instead of the integrated checkpoint, set `rtvi.vss-rtvi-vlm.useSharedNim=true` and configure the target VLM endpoint/model values. With **`nims.enabled=false`** (remote LLM, no in-cluster Nemotron NIM), the total drops to **2 GPUs** (**`vss-vios-streamprocessing`** and **`vss-rtvi-vlm`**).

### Optional GPU monitoring

The chart's DCGM exporter is disabled by default because the NVIDIA GPU
Operator commonly installs one cluster-wide. On clusters without an existing
exporter, enable the bundled component explicitly:

```bash
helm upgrade --install <RELEASE_NAME> ./dev-profile-lvs \
  -f dev-profile-lvs/values-lvs.yaml \
  --set monitoring.enabled=true \
  --set monitoring.dcgmExporter.enabled=true \
  -n <NAMESPACE> --create-namespace
```

### NIM GPU profile guidance

The chart requests **one GPU per enabled NIM**, but the repo does **not** encode an exact minimum GPU memory size for each profile. Treat the **`nims.gpuType`** values as tuning profiles, not a certified capacity statement. Use only a profile that matches the GPU class you have validated on the cluster.

| `nims.gpuType` | Pinned `NIM_MODEL_PROFILE` | Explicit limits in `gpuProfiles` (not auto-selected) |
|----------------|----------------------------|------------------------------------------------------|
| `H100`, `GB300`, `RTXPRO6000BW`, `OTHER` | `2ef85c7286907e706eb0d6c4750a1aefa719447097d151ab34c7837fc02bdac4` (`vllm-int4-tp1-pp1-32.0`) | `NIM_KVCACHE_PERCENT=0.3`, `NIM_GPU_MEM_FRACTION=0.3`, `NIM_MAX_MODEL_LEN=65536`, `--max-num-seqs 8`, `MAX_JOBS=4`. Passthrough keeps `enable_thinking:true`. |
| `L40S` | same INT4 pin | `NIM_KVCACHE_PERCENT=0.8` and `NIM_GPU_MEM_FRACTION=0.8` (0.3 of a 48 GB card cannot hold the ~21 GB INT4 weights). No `NIM_MAX_MODEL_LEN` on this entry. Passthrough keeps `enable_thinking:true`. |
| `RTXPRO4500BW` | same INT4 pin | No memory-fraction or context keys on the `nemotron35` entry. Passthrough keeps `enable_thinking:true`. |

`NIM_MODEL_PROFILE` is pinned so the container cannot auto-select the >=66 GB BF16 profile it advertises as runnable. A dedicated `nvidia.com/gpu: 1` request does not remove these limits. Inspect the rendered `<nim>-nim-env` ConfigMap after install and confirm the card can hold that profile before relying on it. Use a remote endpoint when it cannot.

## RTVI-VLM integration (always on)

The LVS profile always deploys **`vss-rtvi-vlm`**. VLM calls from both HTTP clients are routed through this in-cluster service, and VIOS registers cameras with it on VST lifecycle events:

- **`vss-agent`**: `video_understanding` picks the `rtvi_vlm` LLM profile (`configs/vss-agent/config.yml`) because `VLM_MODEL_TYPE=rtvi`. `RTVI_VLM_BASE_URL` resolves to the in-cluster `vss-rtvi-vlm` Service via `agent.vss-agent.rtviVlmServiceName` (default `vss-rtvi-vlm`).
- **`vss-summarization`**: receives `RTVI_VLM_URL=http://vss-rtvi-vlm:8000` by default (`http://<RELEASE_NAME>-vss-rtvi-vlm:8000` only when `global.useReleaseNamePrefix=true`), plus `RTVI_VLM_URL_PASSTHROUGH=true` via `vss-summarization.extraEnv` — LVS backend forwards `/generate_captions` to the RTVI pod.
- **VIOS** (sensor and stream-processing): `global.vios.notificationConfig` enables `camera_add` / `camera_streaming` / `camera_remove` webhooks to `http://<rtvi>/v1/stream/add` and `/v1/stream/remove`, matching Compose. The usable stream URL arrives on `camera_streaming`; `camera_add` may have an empty URL.

Key values (see `values.yaml` for defaults and the full `rtvi.vss-rtvi-vlm.env` list):

| Key | Default | Notes |
|-----|---------|-------|
| `rtvi.enabled` | `true` | Umbrella switch; set `false` only if you intentionally bypass RT-VLM. Also configure `vss-agent` `VLM_MODEL_TYPE=nim` and remove `vss-summarization.extraEnv` entries that target the RTVI service. |
| `rtvi.vss-rtvi-vlm.enabled` | `true` | Deploy the RTVI-VLM pod. |
| `rtvi.vss-rtvi-vlm.useSharedNim` | `false` | Load the integrated checkpoint in the RT-VLM pod. Sets `MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final` and `VLM_MODEL_TO_USE=cosmos-reason3`. |
| `rtvi.vss-rtvi-vlm.modelPath` | `ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final` | Integrated RT-VLM checkpoint path used when `useSharedNim=false`. |
| `infra.kafka.enabled` | `true` | Deploy Kafka for RTVI-VLM event publishing and create the default VSS topics, including `mdx-vlm-captions` and `mdx-vlm-incidents`. |
| `rtvi.vss-rtvi-vlm.waitForKafka.enabled` | `true` | The RTVI-VLM init container waits for Kafka and required RTVI topics before startup. |
| `rtvi.vss-rtvi-vlm.env` | full list | Replaces the subchart default `env`. Override individual values (e.g. edge `VLM_INPUT_*`) by editing the list in your overlay. |
| `vss-summarization.extraEnv` | 2 RTVI vars | `RTVI_VLM_URL`, `RTVI_VLM_URL_PASSTHROUGH`. `RTVI_VLM_URL` is rendered with `tpl`, so it picks up `{{ .Release.Name }}` when `global.useReleaseNamePrefix` is true. |
| `global.vios.notificationConfig` | LVS webhook JSON | Shared by `vss-vios-sensor` and `vss-vios-streamprocessing`. Empty on those subcharts uses the chart file default (webhooks off); this profile override registers VST cameras with RT-VLM. |
| `agent.vss-agent.rtviVlmEnabled` / `rtviVlmServiceName` | `true` / `vss-rtvi-vlm` | Parity flags; `RTVI_VLM_BASE_URL` in the `env` list reads `rtviVlmServiceName`. |
| `agent.vss-agent.env` → `VLM_MODEL_TYPE` | `rtvi` | Flip to `nim` only to bypass RTVI for the agent (video_understanding will then hit the VLM NIM directly). |

Remote VLM + RTVI: RTVI-VLM also supports remote VLM endpoints when `global.vlmBaseUrl` is set, `nims.enabled=false`, and `rtvi.vss-rtvi-vlm.useSharedNim=true`; see `deploy/helm/services/rtvi/charts/rtvi-vlm/templates/deployment.yaml` for the selection logic.

### RT-VLM frame cap

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

### RT-VLM context length

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
  - **Server version** recorded for this profile: **1.34.2**.

- **NVIDIA GPU Operator**
  - [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/25.10/getting-started.html) **v25.10.1** on the cluster. Platform support: [platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/25.10/platform-support.html).
  - **Driver (x86 Ubuntu 24.04)** — pin **595.58.03** via GPU Operator driver settings.

- **NVIDIA NIM Operator**
  - Required **before deploying this profile with defaults**. The default **`nims.enabled: true`** path creates **`NIMCache`** and **`NIMService`** custom resources for the LLM NIM, so the NIM Operator and its CRDs must already exist on the cluster before `helm upgrade --install`.
  - Install **after** the GPU Operator. See [NVIDIA NIM Operator](https://docs.nvidia.com/nim-operator/3.1.0/install.html) **3.1.0**.
  - If you intentionally run with **`nims.enabled=false`** and all LLM/VLM traffic goes to external endpoints, the NIM CRDs are not created by this chart.

- **Volume provisioner (e.g. local-path)**
  - A **StorageClass** must exist on the cluster (VST, Elasticsearch, and related volumes). Set **`global.storageClass`** in your Helm values override to that class's **`metadata.name`** (see [Prepare the values file](#1-prepare-the-values-file)). The chart writes `storageClassName` on the PVCs it creates, so you do not need to change the cluster-wide default StorageClass for this profile.
  - **Node-local storage (local-path):** use [local-path-provisioner](https://github.com/rancher/local-path-provisioner/blob/v0.0.35/README.md#installation) **v0.0.35**. Node-local volumes are not portable between nodes, and **`vios.vss-vios-sensor`** and **`vios.vss-vios-streamprocessing`** mount the same three shared VST PVCs (see **`vios.vstStorage.*`** below) -- set **`nodeSelector`** on both so Kubernetes schedules them onto the same node, and confirm that node has capacity for the claims.
  - **Bare-metal clusters** with no provisioner yet can install [local-path-provisioner](https://github.com/rancher/local-path-provisioner/blob/v0.0.35/README.md#installation) **v0.0.35** and use the StorageClass it creates. Changing the cluster's *default* StorageClass is optional and only matters for other workloads that omit `storageClassName`; it is not required for this chart.

### Chart / tooling

- **Helm** [3.7 or above](https://github.com/helm/helm/blob/v3.7.0/README.md#install)
- **Kubectl**
- **GPUs**: see [GPU requirements](#gpu-requirements) (3 with defaults).
- **NVIDIA NIM** (if using NIM subcharts): NIM Operator **3.1.0** on the cluster (see [Prerequisites](#prerequisites) above).
- **NGC**: API key for NIM, image pull / chart secret creation (see below).
- **StorageClass** for PVCs: set **`global.storageClass`** to a class that exists on the cluster (see [Prerequisites](#prerequisites) above--**Volume provisioner**). If you use local-path-provisioner, use **v0.0.35**.

### Required NGC secrets

With defaults, the chart creates the two Kubernetes secrets when **`ngc.createSecrets=true`** and **`ngc.apiKey`** is non-empty:

| Secret | Type | Used for | Default reference |
|--------|------|----------|-------------------|
| **`ngc-api`** | `Opaque` with **`NGC_API_KEY`** and **`NGC_CLI_API_KEY`** | NIM Operator resources and RT-VLM NGC access. | **`global.ngcApiSecret.name`** / **`global.ngcApiSecret.key`** |
| **`ngc-secret`** | `kubernetes.io/dockerconfigjson` for **`nvcr.io`** | Image pulls from NGC. | **`global.imagePullSecrets`** |

If **`ngc.createSecrets=false`**, create both secrets yourself in the release namespace before installing, then keep **`global.ngcApiSecret`** and **`global.imagePullSecrets`** aligned with the names and keys you chose.

```bash
export NAMESPACE='<NAMESPACE>'
export NGC_CLI_API_KEY='<your NGC API key>'

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic ngc-api \
  -n "$NAMESPACE" \
  --from-literal=NGC_API_KEY="$NGC_CLI_API_KEY" \
  --from-literal=NGC_CLI_API_KEY="$NGC_CLI_API_KEY"

kubectl create secret docker-registry ngc-secret \
  -n "$NAMESPACE" \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password="$NGC_CLI_API_KEY"
```

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
| **`llmNameSlug`** | Reserved compatibility value; leave empty. LVS selects its LLM through **`nims.*.enabled`**, **`global.llmName`**, and the workload service settings. |
| **`vlmNameSlug`** | Use **`none`** for the default LVS flow; RT-VLM loads the integrated checkpoint instead of deploying a VLM NIM subchart. |
| **`nims`** | **`nims.enabled`**: umbrella for all NIM subcharts. Use **`nims.gpuType`** to select model settings and **`nims.<model>.enabled`** to choose models. Chart **`values.yaml`** defaults **`nims.gpuType`** to **`H100`**. Sample **`values-lvs.yaml`** sets **`RTXPRO6000BW`**. Set **`nims.gpuType`** to the GPU you are installing on before rendering or installing; a mismatched value selects the wrong `gpuProfiles` entry. Default LVS enables **`nims.nemotron35`** and disables the other bundled NIMs because RT-VLM loads the integrated VLM checkpoint. Set **`nims.enabled`** to **`false`** when using [remote LLM/VLM](#remote-llm-and-vlm) only. |
| **`global.llmBaseUrl`** / **`global.vlmBaseUrl`** (remote) | HTTP(S) service-root base URLs when LLM/VLM are **not** deployed by this chart, for example **`http://host:31081`** without a trailing **`/v1`**. Use with **`nims.enabled: false`**. Shared by **vss-agent** and **vss-summarization**; must be reachable from those pods. Leave **`""`** for in-cluster **NIM** services. |
| **`global.llmName`** / **`global.vlmName`** (remote) | NGC-style model IDs for **both** **vss-agent** and **vss-summarization**; must match remote endpoints. Defaults in **`values-lvs.yaml`** match common NGC models. |
| **`vssIngress`** (optional) | Set **`vssIngress.enabled`** to **`true`** to create a Kubernetes **`Ingress`**. Phoenix is served at **`/phoenix`** on the main host. A separate **`phoenix.<host>`** rule is added only when **`vssIngress.phoenixHost`** is set. Kibana, when enabled, is on **`kibana.<host>`**. Requires an existing **IngressClass** (see [VSS Ingress (`vssIngress`)](#vss-ingress-vssingress)). **`global.externalHost`** must be set unless **`vssIngress.host`** is set. Sample **`values-lvs.yaml`** enables this by default. |

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
| **`llmNameSlug`** | `""` | Reserved compatibility value; currently not consumed by the LVS chart. |
| **`vlmNameSlug`** | `none` | RT-VLM loads the integrated checkpoint by default instead of deploying a VLM NIM subchart. |
| **`ngc.createSecrets`** | **`true`** | When **`true`** and **`ngc.apiKey`** is set, the chart creates two secrets (see **`templates/ngc-secrets.yaml`**): **`ngc-api`** (Opaque: **`NGC_API_KEY`** / **`NGC_CLI_API_KEY`**) for NGC API access, and **`ngc-secret`** (**dockerconfigjson**) for pulling images from nvcr.io. Set **`false`** only if you create both secrets yourself; then set **`global.ngcApiSecret`** and **`global.imagePullSecrets`** to match your names. |
| **`ngc.apiKey`** | **`""`** | With **`ngc.createSecrets: true`**, set your NGC API key here; it backs both created secrets. With **`createSecrets: false`**, omit (or leave empty) and install the Opaque + docker secrets out of band; align **`global.*`** below with those objects. Optional: **`ngc.apiKeySecretName`** / **`ngc.dockerSecretName`** rename the generated secrets—update **`global.ngcApiSecret.name`** and **`global.imagePullSecrets`** accordingly. Set in **`values-lvs.yaml`** (or your overlay). |
| **`global.imagePullSecrets`** | **`[{ name: ngc-secret }]`** | Pod **image pull** credentials for nvcr.io. Must reference the **Docker registry** secret (default **`ngc-secret`**, i.e. **`ngc.dockerSecretName`**). This is separate from the NGC **API** key secret. |
| **`global.ngcApiSecret`** | **`name: ngc-api`**, **`key: NGC_API_KEY`** | Tells NIM (**`NIMService`** / **`NIMCache`**) and related workloads which **Opaque** secret holds the NGC **API** key: **`name`** defaults to **`ngc-api`** (**`ngc.apiKeySecretName`**), **`key`** defaults to **`NGC_API_KEY`** (the key the chart writes in that secret). Change these if you use a different secret name or data key. |
| **`global.externalScheme`** | **`""`** | Set in **`values-lvs.yaml`** (e.g. **`http`** or **`https`**). With **`externalHost`** / **`externalPort`**, builds browser-facing URLs for **`vss-agent-ui`**, **`vss-agent`**, and **`vss-vios-ingress`** when their own URL fields are empty. |
| **`global.externalHost`** | **`""`** | Hostname or IP clients use in the browser (e.g. **`vss.YOUR_IP.nip.io`**). |
| **`global.externalPort`** | **`""`** | Port segment in generated URLs; use **`""`** so URLs omit **`:port`** when using default 80/443. Set only for non-default ports (e.g. **`8080`**). |
| **`global.kibanaPublicUrl`** | **`""`** | Public Kibana base URL (no **`/kibana`** path suffix). Prefer this over duplicating **`infra.kibana.kibanaPublicUrl`** unless Kibana must use a different host than the main UI. |
| **`global.llmBaseUrl`** | **`""`** | **Single place** for remote LLM base URL shared by **vss-agent** and **vss-summarization** ( **`LLM_BASE_URL`**, **`LVS_LLM_BASE_URL`** ). Use the service root without trailing **`/v1`** (e.g. **`http://host:31081`**): **vss-agent** appends **`/v1`** in its config, and **vss-summarization** adds **`/v1`** when missing. Subchart **`agent.vss-agent.llmBaseUrl`** or **`vss-summarization.llmBaseUrl`** overrides when set. |
| **`global.vlmBaseUrl`** | **`""`** | Same for VLM (**`VLM_BASE_URL`**, **`VIA_VLM_ENDPOINT`**). Use the same service-root convention without trailing **`/v1`** for compatibility with **vss-agent** and RT-VLM proxying. |
| **`global.llmName`** | **`nvidia/nemotron-3.5-lightning-30b-a3b`** | NGC model ID for **both** **vss-agent** (**`LLM_NAME`**) and **vss-summarization** (**`LVS_LLM_MODEL_NAME`**). Override with **`agent.vss-agent.llmName`** or **`vss-summarization.llmName`** when a workload needs a different ID (e.g. remote NIM). |
| **`global.vlmName`** | **`nim_nvidia_cosmos3-nano-reasoner_bf16-final`** | Same for VLM (**`VLM_NAME`**, **`VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME`**). |
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
| **`vios.vss-vios-sensor.streamProcessorService`** | **`sdrc`** | Sensor registers streams through the SDRC controller service (**`sdrc-controller:10000`** by default). |
| **`vios.vss-vios-sensor.enabled`** | **`true`** | Set **`false`** to disable the **sensor** workload. |
| **`vios.vss-vios-sensor.persistence`** | **`vstData`** / **`vstVideo`**: **`enabled: true`**, **`create: false`**, **`existingClaim: ""`** | Controls whether **sensor** mounts two shared folders (**data** and **video**). **Typical setup:** leave **`existingClaim`** blank—Helm wires the pods to the PVCs created when **`vios.vstStorage.createSharedPvcs`** is **`true`**. **Custom PVCs:** set **`existingClaim`** to your claim name for that volume. **Disable a mount:** set that volume’s **`enabled`** to **`false`** (that path is not mounted). |
| **`vios.vss-vios-streamprocessing.enabled`** | **`true`** | Set **`false`** to disable **vss-vios-streamprocessing**. |
| **`vios.vss-vios-streamprocessing.persistence`** | **`vstData`**, **`vstVideo`**, **`streamerVideos`**: same idea as sensor | **Streamprocessing** mounts up to **three** shared folders: VST **data**, VST **video**, and **streamer** uploads. Use blank **`existingClaim`** to use the shared PVCs from **`vios`** when **`vios.vstStorage.createSharedPvcs`** is **`true`**, or set **`existingClaim`** / **`enabled`** per volume the same way as for **sensor**. |
| **`vios.vss-vios-ingress.enabled`** | **`true`** | Deploys the in-cluster **VST ingress** (nginx). |
| **`vios.vss-vios-ingress.externallyAccessibleIp`** | **`""`** | Hostname or IP address advertised to VST/nginx for external access. If unset, the subchart uses **`global.externalHost`**; if that is unset, it defaults to **`127.0.0.1`**. Override this value only when the VST ingress must use a hostname or IP that differs from **`global.externalHost`**. |
| **`vssIngress.enabled`** | **`false`** in chart **`values.yaml`**; **`true`** in sample **`values-lvs.yaml`** | When **`true`**, renders **`templates/vss-ingress.yaml`**: paths on the main host from the [canonical route table](../../services/common/README.md), including **vss-agent-ui** at **`/`**, **`/api/chat`**, **`/api/agent`**, **`/api/vss-chat`**, and **`/api/proxy`**; **vss-agent** at **`/api`**, **`/chat`**, **`/websocket`**, **`/static`**, **`/docs`**, **`/redoc`**, **`/generate`**, and **`/openapi.json`**; **vss-vios-ingress** (**`/vst`**, **`/storage`**); **`/lvs`** to the summarization API; **`/rtvi-vlm`** to RT-VLM; **`/llm`** to the in-cluster Nemotron NIM when it is enabled (omitted entirely with **`nims.enabled=false`**); **`/elasticsearch`**; and **`/phoenix`** on the main host. The chart's summarization readiness probe is **`/v1/ready`**; through this Ingress that check is **`/lvs/v1/ready`** (the **`/lvs`** prefix is stripped). Summarize is **`/lvs/v1/summarize`**. RT-VLM is **`/rtvi-vlm/v1/models`** and **`/rtvi-vlm/v1/chat/completions`**. Do not use **`/lvs/v1/live`** as the readiness check; this README does not claim **`/v1/live`** is absent from the container. Neither owns the origin root any more, so **`/v1/*`** no longer resolves. **`kibana.<host>`** is added when Kibana is enabled. **`phoenix.<host>`** is added only when **`vssIngress.phoenixHost`** is non-empty. No **`Ingress`** if **`global.externalHost`** and **`vssIngress.host`** are both empty. |
| **`vssIngress.ingressClassName`** | **`haproxy`** | **`spec.ingressClassName`** on the **`Ingress`**. Must match an **`IngressClass`** on the cluster (e.g. from **HAProxy Kubernetes Ingress**). |
| **`vssIngress.host`** | **`""`** | Ingress hostname for the main rules; if empty, **`global.externalHost`** is used. |
| **`vssIngress.vssUiPort`** | **`3000`** | Backend port for **vss-agent-ui** paths. |
| **`vssIngress.vssAgentPort`** | **`8000`** | Backend port for **vss-agent** paths. |
| **`vssIngress.vstIngressPort`** | **`30888`** | Backend port for **vss-vios-ingress** (**`/vst`**). |
| **`vssIngress.lvsBackendPort`** | **`38111`** | Backend port for **vss-summarization** on **`/lvs`**. The prefix is stripped, so **`/lvs/v1/summarize`** reaches the backend as **`/v1/summarize`**. |
| **`vssIngress.rtviVlmPort`** | **`8000`** | Kubernetes Service port for **vss-rtvi-vlm** on **`/rtvi-vlm`**. The OpenAI-compatible root is **`<host>/rtvi-vlm/v1`**. |
| **`vssIngress.elasticsearchPort`** | **`9200`** | Backend port for **`/elasticsearch`**, mounted whenever **`infra.elasticsearch`** is deployed. The edge guard is on the **`elasticsearch`** **`Service`** (**`infra.elasticsearch.ingressGuard`**): guarded at the edge: PUT/DELETE, cluster-admin paths and the two-segment mutating operations are denied, but **POST still reaches Elasticsearch** (`_bulk`, `_reindex`, `/<index>/_doc`) — the same grant the Docker edge makes, not a read-only endpoint. |
| **`vssIngress.timeoutClient`** | **`3600s`** | Rendered for continuity, but **inert**: this controller reads **`timeout-client`** only from its own ConfigMap, never from an Ingress annotation. Raising it means a cluster-wide controller setting. Server-side timeouts *do* work per backend and are set on **vss-summarization**, **vss-rtvi-vlm** and **vss-agent**; set **`vssIngress.timeoutServer`** only to impose an ingress-wide floor on every route. |
| **`vssIngress.kibanaHost`** | **`""`** | Host rule for Kibana; default **`kibana.<global.externalHost or vssIngress.host>`**. |
| **`vssIngress.phoenixHost`** | **`""`** | Empty by default: Phoenix stays at **`/phoenix`** on the main host. Set this to add a separate host rule (for example **`phoenix.<main-host>`**). |
| **`vssIngress.kibanaPort`** | **`5601`** | Kibana **Service** port when Kibana is enabled. |
| **`vssIngress.phoenixPort`** | **`6006`** | Phoenix **Service** port when Phoenix is enabled. |
| **`vss-summarization.enabled`** | **`true`** | Set **`false`** to disable the **LVS** summarization service. |
| **`vss-summarization.elasticsearchHost`** | **`""`** | Elasticsearch hostname for **vss-summarization** **`ES_HOST`**. When empty and **`global.useReleaseNamePrefix`** is **`false`** (the default), the Service is **`elasticsearch`**. With the flag **`true`**, it is **`<RELEASE_NAME>-elasticsearch`**. |
| **`vss-summarization.elasticsearchPort`** | **`9200`** | Elasticsearch HTTP port (**`ES_PORT`**). |
| **`vss-summarization.llmService`** | **`nemotron-35-lightning-30b-a3b`** | NIM subchart **name segment** used to build **`LVS_LLM_BASE_URL`** when **`global.llmBaseUrl`** and **`vss-summarization.llmBaseUrl`** are empty. Default (**`useReleaseNamePrefix: false`**) is **`http://nemotron-35-lightning-30b-a3b:8000/v1`**. With the flag **`true`**, the host is **`<RELEASE_NAME>-<value>`**. Keep it aligned with the enabled LLM NIM. |
| **`vss-summarization.vlmService`** | **`vss-rtvi-vlm`** | Service segment used to build **`VIA_VLM_ENDPOINT`** when **`global.vlmBaseUrl`** and **`vss-summarization.vlmBaseUrl`** are empty. Default host is **`vss-rtvi-vlm`** (**`<RELEASE_NAME>-vss-rtvi-vlm`** only when **`useReleaseNamePrefix`** is **`true`**). Default LVS points this at RT-VLM, not a Cosmos NIM service. |
| **`vss-summarization.llmBaseUrl`** | **`""`** | Optional **LVS-only** override of **`global.llmBaseUrl`**. |
| **`vss-summarization.vlmBaseUrl`** | **`""`** | Optional **LVS-only** override of **`global.vlmBaseUrl`**. |
| **`vss-summarization.llmName`** | **`""`** | Optional **LVS-only** override of **`global.llmName`** (**`LVS_LLM_MODEL_NAME`**). |
| **`vss-summarization.vlmName`** | **`""`** | Optional **LVS-only** override of **`global.vlmName`** (**`VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME`**). |
| **`infra.elasticsearch.enabled`** | **`true`** | Set **`false`** to disable the in-cluster **Elasticsearch** deployment. |
| **`infra.elasticsearch.persistence.data.size`** | **10Gi** | PVC size for Elasticsearch **data** volume. |
| **`infra.elasticsearch.persistence.logs.size`** | **5Gi** | PVC size for Elasticsearch **logs** volume (LVS chart exposes both **data** and **logs** sizes). |
| **`infra.elasticsearch.persistence.storageClass`** | **`""`** | **StorageClass** for Elasticsearch PVCs; leave empty to inherit **`global.storageClass`**, or set explicitly. |
| **`infra.kibana.enabled`** | **`true`** | Set **`false`** to disable the in-cluster **Kibana** deployment. |
| **`infra.kibana.elasticsearchHosts`** | **`""`** | Elasticsearch URL list **Kibana** connects to. When empty, defaults to **`http://elasticsearch:9200`** (**`http://<RELEASE_NAME>-elasticsearch:9200`** only when **`global.useReleaseNamePrefix`** is **`true`**). |
| **`infra.kibana.kibanaPublicUrl`** | **`""`** | Browser-facing **Kibana** base URL. When empty, templates use **`global.kibanaPublicUrl`** if set, else **`http://kibana:5601`** (**`http://<RELEASE_NAME>-kibana:5601`** only when release-name prefixing is on). |
| **`infra.elasticsearch.init.enabled`** | **`true`** | Set **`false`** to skip the **Job** that runs Elasticsearch index/ILM setup for this profile. |
| **`infra.elasticsearch.init.elasticsearchUrl`** | **`""`** | Elasticsearch URL the init **Job** targets. When empty, defaults to **`http://elasticsearch:9200`** (release-prefixed only when **`global.useReleaseNamePrefix`** is **`true`**). |
| **`infra.kibana.init.enabled`** | **`true`** | Set **`false`** to skip the **Job** that applies Kibana saved objects / dashboards for LVS. |
| **`infra.kibana.init.kibanaUrl`** | **`""`** | **Kibana** URL the init **Job** calls. When empty, defaults to **`http://kibana:5601`** (release-prefixed only when **`global.useReleaseNamePrefix`** is **`true`**). |
| **`infra.kibana.init.elasticsearchUrl`** | **`""`** | **Elasticsearch** URL the init **Job** uses. When empty, defaults to **`http://elasticsearch:9200`** (release-prefixed only when **`global.useReleaseNamePrefix`** is **`true`**). |
| **`agent.enabled`** | **`true`** | Set **`false`** to skip the **`agent`** umbrella (**`deploy/helm/services/agent`**). |
| **`agent.vss-agent.enabled`** | **`true`** | Set **`false`** to disable the **vss-agent** deployment only. |
| **`agent.vss-agent.profile`** | **`lvs`** | Passed to the **vss-agent** subchart for LVS-specific deployment/env behavior. ConfigMap data is **`configs/vss-agent/config.yml`** (flat path under this chart). |
| **`agent.vss-agent.lvsBackendService`** | **`vss-summarization`** | Kubernetes **Service** name for the **LVS** backend (must match release + subchart naming). |
| **`agent.vss-agent.vstInternalUrl`** | **`""`** | In-cluster **VST** URL for the agent when the default wiring is insufficient. |
| **`agent.vss-agent.vstInternalIp`** | **`""`** | In-cluster **VST** host/IP override when defaults are insufficient. |
| **`agent.vss-agent.vssAgentExternalUrl`** | **`""`** | External **vss-agent** URL override for browser / callbacks when **`global.external*`** is not enough. |
| **`agent.vss-agent.vssAgentVersion`** | **`3.3.0-65576357eb80`** | Optional version label / env; adjust per release. |
| **`agent.vss-agent.llmName`** | **`""`** | Optional **vss-agent-only** override of **`global.llmName`** (**`LLM_NAME`**). |
| **`agent.vss-agent.vlmName`** | **`""`** | Optional **vss-agent-only** override of **`global.vlmName`** (**`VLM_NAME`**). |
| **`agent.vss-agent.llmBaseUrl`** | **`""`** | Optional **vss-agent-only** override of **`global.llmBaseUrl`**. |
| **`agent.vss-agent.vlmBaseUrl`** | **`""`** | Optional **vss-agent-only** override of **`global.vlmBaseUrl`**. |
| **`agent.vss-agent.evalLlmJudgeName`** | **`""`** | Optional eval judge model ID. When empty, the **vss-agent** subchart defaults to **`llmName`**. |
| **`agent.vss-agent.evalLlmJudgeBaseUrl`** | **`""`** | Optional base URL for the eval judge endpoint. When empty, the subchart defaults alongside **`llmBaseUrl`**. |
| **`agent.vss-agent.reportsBaseUrl`** | **`""`** | Base URL for report links. When empty, templates derive a value from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.vstExternalUrl`** | **`""`** | External **VST** URL passed to the agent. When empty, derived from **`global.external*`** and in-cluster defaults. |
| **`agent.vss-agent.externalIp`** | **`""`** | Hostname or IP override for agent-facing external access when **`global.external*`** is not sufficient. |
| **`agent.vss-agent.env`** | *(see **`values.yaml`**)* | Full **`env`** list (Option B). **`LVS_BACKEND_URL`** is set via **`tpl`** from **`lvsBackendService`** and **`global.useReleaseNamePrefix`** so it points to the in-cluster LVS backend service. |
| **`agent.vss-agent.extraEnv`** | *(omit)* | Optional **`{ name, value }`** appended last. |
| **`vss-agent-ui.enabled`** | **`true`** | Set **`false`** to disable the **vss-agent-ui** deployment. |
| **`vss-agent-ui.envOverrides`** | (see **`values.yaml`**) | Full **`NEXT_PUBLIC_*`** defaults aligned with **dev-profile-base**; LVS sets subtitle (**`Vision (LVS)`**) and **`NEXT_PUBLIC_ENABLE_DASHBOARD_TAB`**. |
| **`vss-agent-ui.agentApiUrlBase`** | **`""`** | Base URL for the **vss-agent** HTTP API (browser **`NEXT_PUBLIC_AGENT_API_URL_BASE`**, typically ends with **`/api/v1`**). If unset and **`global.externalHost`** is set, built as **`<global>/api/v1`**. If both are empty, the in-cluster default is **`http://vss-agent:8000/api/v1`** (**`http://<RELEASE_NAME>-vss-agent:8000/api/v1`** only when **`global.useReleaseNamePrefix`** is **`true`**). |
| **`vss-agent-ui.vstApiUrl`** | **`""`** | **VST** HTTP API URL for the browser (**`NEXT_PUBLIC_VST_API_URL`**). If unset and **`global.externalHost`** is set, built as **`<global>/vst/api`**. Otherwise **`http://vss-vios-ingress:30888/vst/api`** (release-prefixed only when **`global.useReleaseNamePrefix`** is **`true`**). |
| **`vss-agent-ui.proxyBaseUrl`** | **`""`** | Server-only media-proxy target (**`VSS_PROXY_BASE_URL`**). If unset, uses the in-cluster **vss-vios-ingress** Service. |
| **`vss-agent-ui.chatCompletionUrl`** | **`""`** | HTTP chat completion URL (**`NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL`**). If unset and **`global.externalHost`** is set, built as **`<global>/v1/chat/stream`**. Otherwise **`http://vss-agent:8000/v1/chat/stream`** (release-prefixed only when **`global.useReleaseNamePrefix`** is **`true`**). |
| **`vss-agent-ui.chatBackendUrl`**, **`sidebarChatBackendUrl`** | **`""`** | Server-only chat proxy targets. If unset, use an explicit **`chatCompletionUrl`**, otherwise the in-cluster **vss-agent** Service. |
| **`vss-agent-ui.websocketChatUrl`** | **`""`** | WebSocket chat URL (**`NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL`**). If unset and **`global.externalHost`** is set, built as **`<ws-scheme>://<host>[:port]/websocket`** (**`ws`** / **`wss`** from **`global.externalScheme`**). If both this and **`global.externalHost`** are empty, the chart may omit WebSocket env vars; set explicitly for port-forward or custom routing. |
| **`vss-agent-ui.dashboardKibanaBaseUrl`** | **`""`** | Override Kibana base URL for the Dashboard tab when **`global.kibanaPublicUrl`** / **`infra.kibana.kibanaPublicUrl`** are not used. |
| **`nims.enabled`** | **`true`** | Master switch for the **`nims`** umbrella subchart. When **`false`**, no **NIM** model workloads or **`NIMService`** / **`NIMCache`** objects are installed. Use **`false`** with **`global.llmBaseUrl`**, **`global.vlmBaseUrl`**, **`global.llmName`**, and **`global.vlmName`** for remote-only LLM/VLM (**vss-agent** and **vss-summarization**). |
| **`nims.<model>.enabled`** | per model in **`values.yaml`** | Enables or disables one bundled **NIM** model. Default LVS enables **`nemotron35`** and disables the other LLM and Cosmos NIMs because RT-VLM loads the integrated VLM checkpoint. |
| **`nims.gpuType`** | **`H100`** in chart **`values.yaml`**; **`RTXPRO6000BW`** in sample **`values-lvs.yaml`** | Selects **`gpuProfiles`** tuning for enabled NIM ConfigMaps. Set this to the GPU you are installing on. Supported values are **`H100`**, **`GB300`**, **`L40S`**, **`RTXPRO4500BW`**, **`RTXPRO6000BW`**, and **`OTHER`**. See [NIM GPU profile guidance](#nim-gpu-profile-guidance). |

### Remote LLM and VLM

When LLM and VLM run **outside** this release, set **`nims.enabled`** to **`false`** and configure **`global.llmBaseUrl`**, **`global.vlmBaseUrl`**, **`global.llmName`**, and **`global.vlmName`** in **`values-lvs.yaml`** (or **`--set`**). Use service-root base URLs without a trailing **`/v1`**; **vss-agent** appends **`/v1`** in its config, and the LVS/RT-VLM templates add **`/v1`** where their containers require it. **vss-summarization** and **vss-agent** both consume these globals unless overridden with **`vss-summarization.llmBaseUrl`**, **`agent.vss-agent.llmBaseUrl`**, etc. Endpoints must be reachable from pods in the release namespace. **`vss-vios-streamprocessing`** and **`vss-rtvi-vlm`** still request one GPU each (**2** total).

Discover the model IDs from each endpoint's **`/v1/models`**. Do not reuse the in-cluster IDs unless that endpoint advertises them. Endpoint authentication is separate from the NGC registry secret (**`ngc-secret`**) used for image pull:

- **RT-VLM** sends **`VIA_VLM_API_KEY`** from **`global.ngcApiSecret`** (default secret **`ngc-api`**, key **`NGC_API_KEY`**, filled from **`ngc.apiKey`**). If the remote VLM expects a different token, point **`rtvi.vss-rtvi-vlm.ngcApiSecret.name`** and **`.key`** at that Secret. **`rtvi.vss-rtvi-vlm.useSharedNim`** must be **`true`**, or the pod loads the integrated checkpoint and ignores **`global.vlmBaseUrl`**.
- **vss-agent** does not copy **`ngc.apiKey`** into **`NVIDIA_API_KEY`**. This profile's env list sets **`NVIDIA_API_KEY`** to empty. If the remote LLM requires a bearer token, set **`NVIDIA_API_KEY`** on **`agent.vss-agent.env`** to that endpoint's credential.
- **vss-summarization** calls the remote LLM on its own. Its chart default sets **`NVIDIA_API_KEY`** to empty, and **`configs/config.yaml`** sends that value as **`api_key`**. **`vss-summarization.extraEnv`** is applied last and overrides that name. Add the entry below to the existing **`extraEnv`** list. Do not replace the list: it already carries **`RTVI_VLM_URL`** and **`RTVI_VLM_URL_PASSTHROUGH`**. This chart's **`extraEnv`** entries are **`name`** and **`value`** only.

```yaml
vss-summarization:
  extraEnv:
    - name: NVIDIA_API_KEY
      value: "<REMOTE_LLM_TOKEN>"
```

### 2. Install

```bash
# The URL alone checks out the default branch. Pin the release you intend.
# For VSS 3.3.0:
git clone --branch v3.3.0 --single-branch https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization
git rev-parse HEAD
cd deploy/helm/developer-profiles

helm dependency build ./dev-profile-lvs

# values-lvs.yaml sets nims.gpuType=RTXPRO6000BW. Chart values.yaml defaults to H100.
# Set nims.gpuType to the GPU you are installing on before this command.
# Replace <RELEASE_NAME> and <NAMESPACE> before running.

# Update the values-lvs.yaml and install the chart
helm upgrade --install <RELEASE_NAME> ./dev-profile-lvs \
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
  --set vlmNameSlug=none \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS"

# OR — LVS with remote LLM/VLM (no NIM subcharts); URLs must be reachable from vss-agent and vss-summarization pods
# (reuse NGC_CLI_API_KEY, STORAGE_CLASS, EXTERNAL_HOST from the example above)
export LLM_BASE_URL='<REMOTE LLM SERVICE ROOT, no trailing /v1>'
export VLM_BASE_URL='<REMOTE VLM SERVICE ROOT, no trailing /v1>'

# Discover the exact model IDs the remote endpoints advertise — do not assume
# they match the in-cluster defaults.
curl -sf "$LLM_BASE_URL/v1/models" | jq -r '.data[].id'
curl -sf "$VLM_BASE_URL/v1/models" | jq -r '.data[].id'
export LLM_NAME='<LLM MODEL ID FROM ABOVE>'
export VLM_NAME='<VLM MODEL ID FROM ABOVE>'

helm upgrade --install vss-lvs ./dev-profile-lvs \
  -f dev-profile-lvs/values-lvs.yaml \
  -n vss-lvs --create-namespace \
  --set nims.enabled=false \
  --set-string ngc.apiKey="$NGC_CLI_API_KEY" \
  --set global.externalHost=vss.$EXTERNAL_HOST.nip.io \
  --set global.storageClass="$STORAGE_CLASS" \
  --set-string global.llmBaseUrl="$LLM_BASE_URL" \
  --set-string global.vlmBaseUrl="$VLM_BASE_URL" \
  --set-string global.llmName="$LLM_NAME" \
  --set-string global.vlmName="$VLM_NAME" \
  --set rtvi.vss-rtvi-vlm.useSharedNim=true
```

**Important:** `rtvi.vss-rtvi-vlm.useSharedNim=true` is required for a remote VLM — without it, RT-VLM ignores `global.vlmBaseUrl` and loads its integrated checkpoint instead of proxying to the remote endpoint.

### 3. Post-deploy validation

Wait for pods first:

```bash
kubectl get pods -n <NAMESPACE>
kubectl get pods -n <NAMESPACE> -w
```

With default **`global.useReleaseNamePrefix=false`**, use these services for direct checks. If you set **`global.useReleaseNamePrefix=true`**, prefix each service name with **`<RELEASE_NAME>-`**. If the NIM Operator creates a differently named Service in your environment, discover it with **`kubectl get svc -n <NAMESPACE> | grep nemotron`** and use that service name for the NIM port-forward.

Run the port-forwards in separate terminals:

```bash
kubectl port-forward -n <NAMESPACE> svc/vss-summarization 38111:38111
kubectl port-forward -n <NAMESPACE> svc/vss-rtvi-vlm 8018:8000
kubectl port-forward -n <NAMESPACE> svc/nemotron-35-lightning-30b-a3b 30081:8000
```

Then validate:

```bash
# LVS readiness
curl -f http://127.0.0.1:38111/v1/ready

# RT-VLM readiness
curl -f http://127.0.0.1:8018/v1/health/ready

# NIM readiness (skip this when nims.enabled=false and the LLM is external)
curl -f http://127.0.0.1:30081/v1/health/ready

# LVS model list
curl -f http://127.0.0.1:38111/models

# NIM model list (skip this when nims.enabled=false and the LLM is external)
curl -f http://127.0.0.1:30081/v1/models
```


## Exposing the stack

**Note:** After install or upgrade, wait until **all** pods in your namespace are **Ready** before using the application in the browser. When **in-cluster NIM** is enabled (**`nims.enabled: true`**), **NIM** model pods need **extra time** (image pull, **`NIMService`** / **`NIMCache`**, warm-up)—this is **common to both base and LVS** profiles. On **LVS**, the **`vss-summarization`** pod also often needs **additional time** (Elasticsearch, backends, NIM reachability). Opening **vss-agent-ui** while NIM or other dependencies are still starting can produce **transient errors** (failed API calls, timeouts, or empty screens). Check progress with **`kubectl get pods -n <NAMESPACE>`** (or **`kubectl get pods -n <NAMESPACE> -w`**) until every workload shows **`Running`** and **`READY`** matches the expected column (e.g. **`1/1`**). With **remote** LLM/VLM only (**`nims.enabled: false`**), NIM startup is skipped, but still wait for **`vss-summarization`** and the rest of the stack.

This chart also renders one-time Kubernetes **`Job`**s for setup (Elasticsearch and Kibana index/dashboard init, and NIM-Operator cache preparation when the LLM NIM is enabled). Their pods run to **`Completed`** and exit — do not expect them to show **`Running`** or **`Ready`** like a Deployment/StatefulSet pod; only wait for the long-running workload pods to reach **`Running`**/**`Ready`**.

Set **`global.externalHost`**, **`global.kibanaPublicUrl`** (and scheme/port as needed) in **`values-lvs.yaml`** so **vss-agent-ui**, **vss-agent**, **vss-vios-ingress**, and Kibana links resolve for browsers.

### VSS Ingress (`vssIngress`)

The chart can create a Kubernetes **`Ingress`** (**`templates/vss-ingress.yaml`**) so one main hostname serves UI, API, VST, summarization (**`/lvs`**), RT-VLM, Elasticsearch, the in-cluster LLM (**`/llm`**, when enabled), and Phoenix at **`/phoenix`**. Kibana gets its own hostname when that subchart is enabled. Phoenix gets a separate hostname only when **`vssIngress.phoenixHost`** is set.

**Prerequisites**

1. An **Ingress controller** must already be installed; **`vssIngress.ingressClassName`** (default **`haproxy`**) must match its **`IngressClass`**.
2. **`global.externalHost`** must be set unless **`vssIngress.host`** overrides the main rule hostname.
3. **`vssIngress.enabled`**: **`true`** in sample **`values-lvs.yaml`**. Set **`false`** to skip Helm **`Ingress`** and use **`kubectl port-forward`**, **`NodePort`**, or manual manifests (**`vss-ingress-example.yaml`** / **`vss-ingress-example-rewrites.yaml`** in this directory).

**What gets created**

- **`Ingress`** **`<RELEASE_NAME>-vss-ingress`** in the release namespace.
- **`spec.ingressClassName`**: **`vssIngress.ingressClassName`** (default **`haproxy`**).
- Main host, when the matching backend is deployed: **`/`**, **`/api/chat`**, **`/api/agent`**, **`/api/vss-chat`**, **`/api/proxy`** → **vss-agent-ui**; **`/api`**, **`/chat`**, **`/websocket`**, **`/static`**, **`/docs`**, **`/redoc`**, **`/generate`**, **`/openapi.json`** → **vss-agent**; **`/vst`** and **`/storage`** → **vss-vios-ingress**; **`/lvs`** → **vss-summarization** (readiness through the Ingress is **`/lvs/v1/ready`**); **`/rtvi-vlm`** → **vss-rtvi-vlm**; **`/llm`** → the in-cluster Nemotron NIM when enabled; **`/elasticsearch`** when Elasticsearch is enabled; **`/phoenix`** on this same host when Phoenix is enabled. HAProxy strips the prefixes that the [canonical route table](../../services/common/README.md) marks for rewrite.
- If **Kibana** is enabled: host **`kibana.<main-host>`** (or **`vssIngress.kibanaHost`**) → **Kibana** (**`vssIngress.kibanaPort`**).
- If **`vssIngress.phoenixHost`** is set: that host → **Phoenix**. Leaving it empty does not create **`phoenix.<main-host>`**.

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

## Troubleshooting

### NIM `CrashLoopBackOff`: no available memory for cache blocks

Symptom:

```bash
kubectl get pods -n <NAMESPACE>
kubectl logs -n <NAMESPACE> <NIM_POD_NAME> --previous
```

The NIM pod enters **`CrashLoopBackOff`** and logs include **`No available memory for the cache blocks`**.

Fix:

Reduce the active NIM profile's model length and/or sequence count, then upgrade the release. The exact values are hardware and workload dependent; the repo does not define a universal minimum GPU memory target.

```yaml
nims:
  gpuType: RTXPRO6000BW
  gpuProfiles:
    RTXPRO6000BW:
      nemotron35:
        NIM_PASSTHROUGH_ARGS: "--reasoning-parser nemotron_v3 --enable-auto-tool-choice --tool-call-parser qwen3_coder --max-model-len 65536 --max-num-seqs 2"
```

Use the key that matches your active **`nims.gpuType`**. Apply the override through **`values-lvs.yaml`** or another Helm values file, then run:

```bash
helm upgrade <RELEASE_NAME> ./dev-profile-lvs -f dev-profile-lvs/values-lvs.yaml -n <NAMESPACE>
```

## Upgrade and uninstall

**Upgrade**

```bash
helm upgrade <RELEASE_NAME> ./dev-profile-lvs -f dev-profile-lvs/values-lvs.yaml -n <NAMESPACE>
```

**Uninstall**

Back up uploaded media and Postgres metadata before `helm uninstall`. Those claims have no `helm.sh/resource-policy: keep` annotation, so the release delete removes them. Copy the volumes with your storage provider first, and read each PV's reclaim policy. A `Delete` policy removes the disk with the claim.

| Resource | Helm uninstall behavior | Notes |
|----------|-------------------------|-------|
| Shared VST PVCs (`<RELEASE_NAME>-vst-data`, `<RELEASE_NAME>-vst-video`, `<RELEASE_NAME>-vst-streamer-videos`) | Removed with the release | Names always include the release, including when `global.useReleaseNamePrefix` is `false`. This is the uploaded and streamed media. |
| Postgres PVC | Removed with the release | Default name `vss-vios-postgres-data`. With `global.useReleaseNamePrefix: true` it is `<RELEASE_NAME>-vss-vios-postgres-data`. Database files are on that claim at `postgresql/db`. |
| Elasticsearch PVCs | Removed with the release | The StatefulSet sets `persistentVolumeClaimRetentionPolicy.whenDeleted: Delete`. Default claims are `es-data-elasticsearch-0` and `es-logs-elasticsearch-0`. With the prefix flag they are `es-data-<RELEASE_NAME>-elasticsearch-0` and `es-logs-<RELEASE_NAME>-elasticsearch-0`. |
| `NIMCache` (namespaced; default name `nemotron-35-lightning-30b-a3b`) | Retained | Templates set `helm.sh/resource-policy: keep`. `NIMCache` is a namespaced CRD object, not cluster-scoped, and not a PVC. |

```bash
kubectl get pvc -n <NAMESPACE>
kubectl get nimcache -n <NAMESPACE>

# After the media and Postgres backup:
helm uninstall <RELEASE_NAME> -n <NAMESPACE>
```

Do **not** run namespace-wide deletes such as `kubectl delete nimcache --all` or `kubectl delete pvc --all`. Those commands remove every matching object in the namespace, including leftovers from other releases. Inspect ownership, confirm you intend to discard the data, then delete only the selected leftovers for this release:

```bash
# Example for the default Nemotron NIMCache (kept by Helm):
kubectl delete nimcache nemotron-35-lightning-30b-a3b -n <NAMESPACE>
```
