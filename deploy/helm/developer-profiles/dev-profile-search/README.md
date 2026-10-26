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

# Developer Search Profile - Kubernetes Deployment

Helm-based deployment of the VSS Developer Search Profile on Kubernetes.

For full documentation, see the [Quickstart Guide - Developer Search Profile (Kubernetes)](https://docs.nvidia.com/vss/latest/agent-workflow-search.html).

## RTVI CV startup policy

Search uses the shared `ds-start.sh` owned by the `rtvi-cv` subchart; this
profile's ConfigMap contains configuration data only. The pod stages the
read-only `mounted-configs/` data into a writable `configs/` volume before the
shared script patches vision-encoder paths.

Search selects `DS_MODEL_FAMILY=rtdetr-warehouse`,
`DS_VISION_ENCODER=true`, and `DS_TRACKER_REID=true`. Its model download Job
writes one completion marker per destination artifact, and the RTVI CV pod
waits for both each marker and its artifact before starting. Standalone
warehouse and MV3DT startup scripts remain separate.

## GPU Requirements

The stack requests GPUs (`nvidia.com/gpu: 1` each) for the workloads listed below. The exact total depends on whether you serve the LLM and VLM locally (Option A) or from remote endpoints (Option B).

### With Local NIMs (Option A)

The critic agent is available by default and controlled per request with `use_critic`. Its VLM (`rtvi.vss-rtvi-vlm`) is enabled by default. Setting `rtvi.vss-rtvi-vlm.enabled=false` saves one GPU but also disables `video_understanding`.

| Workload | GPU | Notes |
|----------|-----|-------|
| `vss-rtvi-cv` | 1 | |
| `vss-rtvi-embed` (Cosmos Embed) | 1 | |
| `vss-vios-streamprocessing` | 1 | |
| `nemotron-3.5-lightning-30b-a3b` (NIM) | 1 | |
| `vss-rtvi-vlm` (Cosmos3 checkpoint) | 1 | VLM used by the critic and `video_understanding` — enabled by default |
| **Total** | **5** | **4** if the VLM is disabled (`rtvi.vss-rtvi-vlm.enabled=false`) |

> **Note:** The VLM (used by the critic and `video_understanding`) is served in-pod by
> `rtvi.vss-rtvi-vlm` (not the standalone `nvidia-cosmos3-reasoner` NIM, which is disabled by
> default). It requests **1 full GPU** (`nvidia.com/gpu: "1"`), scheduled on any free GPU. To use
> the standalone NIM instead, set all of the following. The three flags alone leave
> `VLM_BASE_URL` on `http://vss-rtvi-vlm:8000` and `VLM_NAME` on the integrated RT-VLM id
> after that workload is disabled. `configs/vss-agent/config.yml` appends `/v1` to
> `VLM_BASE_URL` when `VLM_MODEL_TYPE=nim`.
>
> - `nims.cosmos3.enabled=true` and `rtvi.vss-rtvi-vlm.enabled=false`
> - `agent.vss-agent.vlmBaseUrl=http://nvidia-cosmos3-reasoner:8000` (release-prefixed when `global.useReleaseNamePrefix=true`)
> - `agent.vss-agent.extraEnv` entry `VLM_MODEL_TYPE=nim` (the chart env list sets `rtvi`; `extraEnv` wins on the same name)
> - `agent.vss-agent.vlmName` from `curl -sf http://nvidia-cosmos3-reasoner:8000/v1/models` — do not reuse the RT-VLM model id

### With Remote LLM and VLM Endpoints (Option B)

| Workload | GPU | Notes |
|----------|-----|-------|
| `vss-rtvi-cv` | 1 | |
| `vss-rtvi-embed` (Cosmos Embed) | 1 | |
| `vss-vios-streamprocessing` | 1 | |
| `vss-rtvi-vlm` (proxy) | 1 | Forwards VLM calls to the remote endpoint and loads no local model, but still requests `nvidia.com/gpu: 1` from the `rtvi-vlm` subchart defaults |
| **Total** | **4** | |

### GPU time-slicing

Time-slicing is a device-plugin scheduling mode. Each share is advertised as another `nvidia.com/gpu`. It does not reserve a full physical GPU, isolate GPU memory, or split compute in proportion to the request. A request of 2 is two shares. The GPU Operator can reject a request greater than 1 when `failRequestsGreaterThanOne` is enabled. See [Time-slicing GPUs in Kubernetes](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/25.10/gpu-sharing.html) for the operator you installed.

This profile does not ship a tested time-slicing configuration. The active Nemotron key is `nims.nemotron35` (`nims.nemotron` is ignored). The Nemotron `NIMCache` sets `tensorParallelism` from `nims.nemotron35.resources.limits["nvidia.com/gpu"]`, so changing that limit also changes the cache, not only the scheduler.

Put the literal key in a values file and confirm the rendered `NIMService` still has `nvidia.com/gpu` at the share count you tested:

```yaml
nims:
  nemotron35:
    resources:
      limits:
        nvidia.com/gpu: "<shares>"
      requests:
        nvidia.com/gpu: "<shares>"
```

`--set nims.nemotron35.resources.limits.nvidia.com/gpu=2` is parsed as a nested `nvidia.com` map and leaves `nvidia.com/gpu` at 1. Prefer the YAML file. If you must use `--set`, escape the dot and quote the whole argument: `--set 'nims.nemotron35.resources.limits.nvidia\.com/gpu=<shares>'`.

## Prerequisites

- **Kubernetes cluster**
  - Running cluster whose API you can reach with **`kubectl`** (correct context and, if applicable, kubeconfig).
  - **Server version** recorded for this profile: **1.34.2**.

- **NVIDIA GPU Operator**
  - [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/25.10/getting-started.html) **v25.10.1** on the cluster. Platform support: [platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/25.10/platform-support.html).
  - **Driver (x86 Ubuntu 24.04)** — pin **595.58.03** via GPU Operator driver settings.

- **NVIDIA NIM Operator** (required only for [Option A: Local NIMs](#option-a-deploy-with-local-nims))
  - Required when `nims` subcharts are enabled (`NIMCache` / `NIMService`).
  - Install **after** the GPU Operator. See [NVIDIA NIM Operator](https://docs.nvidia.com/nim-operator/3.1.0/install.html) **3.1.0**.
  - Install the NIM Operator:

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update

kubectl create namespace nim-operator

helm upgrade --install nim-operator nvidia/k8s-nim-operator \
  -n nim-operator \
  --version=3.1.0

# Verify the operator pod is running
kubectl get pods -n nim-operator
```

- **Volume provisioner (e.g. local-path)**
  - A **StorageClass** must exist on the cluster. Set **`global.storageClass`** in your Helm values override to that class's **`metadata.name`** (this profile's install examples use **`--set global.storageClass=…`**). The chart writes `storageClassName` on the PVCs it creates, so you do not need to change the cluster-wide default StorageClass for this profile.
  - **Node-local storage (local-path):** use [local-path-provisioner](https://github.com/rancher/local-path-provisioner/blob/v0.0.35/README.md#installation) **v0.0.35**, or the Helm-based install in [Step 1](#step-1-volume-provisioner-bare-metal-optional). The shared VST claims are `ReadWriteOnce`. **`vios.vss-vios-sensor`** and **`vios.vss-vios-streamprocessing`** share them, and streamprocessing also requests a GPU. **`vios.vss-vios-nvstreamer`** mounts the streamer-videos claim as well. Node-local volumes are not portable between nodes, and ReadWriteOnce allows those pods only on the same node. Set the same `nodeSelector` on sensor, streamprocessing, and NVStreamer, and confirm that node has the GPU and capacity for the claims.
  - **Default StorageClass (optional):** changing the cluster's *default* StorageClass is not required for this chart -- setting **`global.storageClass`** is enough. It only matters for other workloads that omit `storageClassName`. If you still want to make a class the cluster default, run:

    ```bash
    kubectl patch storageclass local-path -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
    ```

    Replace **`local-path`** with your StorageClass **`metadata.name`** if it differs.

### Chart / tooling

- **Helm** [3.7 or above](https://github.com/helm/helm/blob/v3.7.0/README.md#install)
- **kubectl**
- **GPUs**: see [GPU Requirements](#gpu-requirements)
- **NGC**: API key for image pull, model downloads, and NIM access
- **StorageClass**: set **`global.storageClass`** to a class on the cluster (see [Prerequisites](#prerequisites)—**Volume provisioner**). If you use local-path-provisioner, use **v0.0.35**.

## Environment Setup

```bash
export NODE_EXTERNAL_IP='<your node IP>'
export NGC_CLI_API_KEY='<your NGC API key>'
export STORAGE_CLASS='<Storage Class Name>'
export GPU_NAME='H100'  # One of: H100, GB300, L40S, RTXPRO4500BW, RTXPRO6000BW, OTHER
```

> **Critic behavior** is controlled per request with `use_critic` and defaults to enabled. RT-VLM remains available for both critic verification and `video_understanding`.

## Step 1: Volume provisioner (bare metal, optional)

Use this **only** when you want to install **local-path** with Helm on bare metal. If the cluster already has a suitable StorageClass, set **`global.storageClass`** per [Prerequisites](#prerequisites)—**Volume provisioner** and skip this step. The RTX cluster runs provisioner image **`v0.0.35`**. Helm chart **`0.0.36`** is the chart that contains that image. Chart **`0.0.35`** ships provisioner **`v0.0.34`**.

```bash
helm repo add containeroo https://charts.containeroo.ch
helm repo update

helm upgrade --namespace default --install \
  local-path-provisioner-default \
  containeroo/local-path-provisioner \
  --version '0.0.36'
```

Verify the StorageClass exists:

```bash
kubectl get storageclass
```

A missing `(default)` marker is fine. This chart writes `storageClassName` from `global.storageClass`. Patching the cluster default is optional and is only for other workloads that omit `storageClassName`. The optional patch is under [Prerequisites](#prerequisites)—**Volume provisioner**.

## Step 2: Install Ingress Controller (HAProxy)

This profile uses the HAProxy Kubernetes Ingress controller for **external traffic** (browser → VSS UI / agent / Kibana / Phoenix) via the controller's host ports `80`/`443` exposed by a DaemonSet. Install it once as a cluster prerequisite:

```bash
helm repo add haproxytech https://haproxytech.github.io/helm-charts
helm repo update

helm upgrade --install haproxy-kubernetes-ingress haproxytech/kubernetes-ingress \
  --version 1.49.0 \
  -n haproxy-controller --create-namespace \
  --set controller.kind=DaemonSet \
  --set controller.daemonset.useHostPort=true \
  --set controller.daemonset.hostPorts.http=80 \
  --set controller.daemonset.hostPorts.https=443
```

> **In-cluster RTVI affinity (optional).** Only needed when you deploy the Search profile with `global.rtviInternalIngress.enabled=true` (default `false`). That path routes vss-agent → rtvi-cv / rtvi-embed through the controller's **ClusterIP Service** at `haproxy-kubernetes-ingress.haproxy-controller:80`, matching the host-less rule the main Ingress renders for `/rtvi-cv` and `/rtvi-embed`; the stick-table affinity annotations sit on those two Services (`rtvi.vss-rtvi-cv.streamAffinity`). Stick tables are per controller replica (no peers sync), so keep the RTVI path on a single controller replica for global affinity. To enable it, append `--set controller.service.type=ClusterIP` to the install command above. If you only need external traffic, leave it off.

Verify the controller is running:

```bash
kubectl get pods -n haproxy-controller
kubectl get ingressclass
```

You should see an IngressClass named `haproxy`. If you enabled the ClusterIP Service for RTVI affinity, also confirm it exists:

```bash
kubectl get svc -n haproxy-controller haproxy-kubernetes-ingress
```

The `global.rtviInternalIngress.controllerService` default (`haproxy-kubernetes-ingress.haproxy-controller`) and `controllerPort` default (`80`) match this install; override only if you used a different release name or namespace.

## Step 3: Deploy the Search Profile

**Note:** The Helm install can take several minutes while dependent services start; wait for workloads to become Ready before using the UI. Use **`global.ngcApiKey`** (and **`nims.global.ngcApiKey`** for [Option A](#option-a-deploy-with-local-nims)) as in the examples below—Helm creates the needed NGC and registry secrets from those values.

```bash
# The URL alone checks out the default branch. Pin the release you intend.
# For VSS 3.3.0:
git clone --branch v3.3.0 --single-branch https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization
git rev-parse HEAD
cd deploy/helm/developer-profiles

helm dependency build ./dev-profile-search
```

### Option A: Deploy with Local NIMs

**This is the default and recommended way to deploy the Search profile.** Everything the critic
and `video_understanding` need runs on the cluster, so no external endpoint is required.

Runs the LLM NIM on-cluster via the NIM Operator, with the VLM served by `rtvi.vss-rtvi-vlm`. Requires additional GPUs for Nemotron and the VLM. See [GPU Requirements](#with-local-nims-option-a).

**Prerequisite:** Install the [NVIDIA NIM Operator](#prerequisites) before deploying.

The VLM pod is gated by **`rtvi.vss-rtvi-vlm.enabled`**, which defaults to `true`. Disabling this pod removes VLM support for both critic verification and `video_understanding`.

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -n vss-search --create-namespace \
  --set global.externalHost=vss-search.$NODE_EXTERNAL_IP.nip.io \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set global.storageClass=$STORAGE_CLASS \
  --set nims.global.ngcApiKey=$NGC_CLI_API_KEY \
  --set nims.gpuType=$GPU_NAME \
  --wait=false
```

#### With NodePort (instead of Ingress)

Services are exposed directly on the node via NodePort. No Ingress controller required.

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -f dev-profile-search/values-nodeport.yaml \
  -n vss-search --create-namespace \
  --set global.externalHost=vss-search.$NODE_EXTERNAL_IP.nip.io \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set global.storageClass=$STORAGE_CLASS \
  --set nims.global.ngcApiKey=$NGC_CLI_API_KEY \
  --set nims.gpuType=$GPU_NAME \
  --wait=false
```

See [Access via NodePort](#access-via-nodeport) for endpoint URLs.

### Option B: Remote LLM with a user-provided VLM endpoint

Serves the LLM and the VLM from outside the cluster, so neither needs a local GPU for inference.
`vss-rtvi-cv` and RTVI Embed still run on local GPUs, and `vss-rtvi-vlm` is still deployed — in this
mode it is an OpenAI-compatible proxy that forwards VLM calls to the remote endpoint rather than
loading a local checkpoint. See [GPU Requirements](#with-remote-llm-and-vlm-endpoints-option-b).

The LLM and the evaluation judge can use the hosted `nvidia/nemotron-3.5-lightning-30b-a3b`
on `https://integrate.api.nvidia.com` ([catalog entry](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b)),
matching the in-cluster default. If that hosted endpoint is ever retired, pick a current model
from the [API catalog](https://build.nvidia.com) or point `llmBaseUrl` at your own LLM.
**The VLM must be an endpoint you provide**: the Cosmos3 VLM
(`nvidia/cosmos3-nano-reasoner`) has no working hosted endpoint on `https://integrate.api.nvidia.com`,
so critic verification and `video_understanding` fail if you leave the VLM pointed there. Supply your
own OpenAI-compatible VLM — a self-hosted NIM, a shared service in another namespace, or any other
OpenAI-compatible API — or use [Option A](#option-a-deploy-with-local-nims) instead.

Set **`nims.enabled=false`** so this chart does not deploy in-cluster NIM workloads. The LLM and the
VLM are then configured through different keys, because the agent calls the LLM directly but reaches
the VLM through the RT-VLM proxy:

| Key | Purpose |
|-----|---------|
| `agent.vss-agent.llmBaseUrl` / `llmName` | The LLM endpoint the agent calls directly. Omit both to keep this file's hosted Nemotron default. |
| `agent.vss-agent.evalLlmJudgeBaseUrl` / `evalLlmJudgeName` | The judge used by evaluation runs. This file pins them to NVIDIA Build and they take precedence over `llmBaseUrl`, so a custom LLM leaves the judge on Build unless you set these as well — the example below does. |
| `global.vlmBaseUrl` / `global.vlmName` | The remote VLM that RT-VLM forwards to (`VIA_VLM_ENDPOINT`). Overriding only `agent.vss-agent.vlmBaseUrl` leaves the proxy pointed at this file's Cosmos3 default and the critic still fails. |
| `agent.vss-agent.vlmName` | The model id the agent asks RT-VLM for. Must match `global.vlmName`, or RT-VLM rejects the request. |

`configs/vss-agent/config.yml` appends `/v1` to `LLM_BASE_URL` (`nim_llm` / `openai_llm`) and to `VLM_BASE_URL` (`nim_vlm` / `openai_vlm`). Leave the trailing `/v1` off `agent.vss-agent.llmBaseUrl`. That file does not reference `EVAL_LLM_JUDGE_BASE_URL`, so the search agent does not append `/v1` to the judge URL. The benchmark client in `skills/benchmarking/vss-benchmark-vlm-qa/scripts/benchmark_vlm_qa.py` (`judge_completions_url`) keeps an existing `/v1` and adds `/v1` when the root has none, so a judge root with or without `/v1` both reach `/v1/chat/completions`. A URL that already ends in `/v1` is valid for that client and is not valid for `llmBaseUrl`. RT-VLM's `VIA_VLM_ENDPOINT` keeps a `/v1` suffix when `global.vlmBaseUrl` already has one, and adds `/v1` when it does not. The examples below use a root with no trailing `/v1`. Read the model id from each endpoint's `/v1/models` list.

This profile lists the **full** **`agent.vss-agent.env`** block for Search deployments. Search behavior is driven by **`general.front_end.streaming_ingest`** in `configs/vss-agent/config.yml`; the chart wires the agent for remote VLM mode and the default Elasticsearch index **`mdx-embed-filtered-2025-01-01`**. Critic verification is controlled per request with `use_critic`. Override **`agent.vss-agent.elasticsearchUrl`** or **`agent.vss-agent.elasticsearchIndex`** when you need a different Elasticsearch endpoint or index.

```bash

export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export LLM_MODEL_ID='<MODEL ID YOUR LLM ENDPOINT SERVES>'
export VLM_BASE_URL='<YOUR OPENAI-COMPATIBLE VLM ENDPOINT>'
export VLM_MODEL_ID='<MODEL ID YOUR VLM ENDPOINT SERVES>'

helm upgrade --install vss-search ./dev-profile-search \
  -f dev-profile-search/values-build-endpoint.yaml \
  -n vss-search --create-namespace \
  --set global.externalHost=vss-search.$NODE_EXTERNAL_IP.nip.io \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set agent.vss-agent.apiKeys.nvidia=$NGC_CLI_API_KEY \
  --set global.storageClass=$STORAGE_CLASS \
  --set nims.enabled=false \
  --set agent.vss-agent.llmName="$LLM_MODEL_ID" \
  --set agent.vss-agent.llmBaseUrl="$LLM_BASE_URL" \
  --set agent.vss-agent.evalLlmJudgeName="$LLM_MODEL_ID" \
  --set agent.vss-agent.evalLlmJudgeBaseUrl="$LLM_BASE_URL" \
  --set global.vlmBaseUrl="$VLM_BASE_URL" \
  --set global.vlmName="$VLM_MODEL_ID" \
  --set agent.vss-agent.vlmName="$VLM_MODEL_ID" \
  --wait=false
```

> **Option B note:** `values-build-endpoint.yaml` sets `nims.enabled=false` (the umbrella) and
> `nims.nemotron35.enabled=false` plus `nims.cosmos3.enabled=false`. `nims.nemotron.enabled` is not a key.
> Its `global.vlmBaseUrl` and `global.vlmName` defaults still point at Cosmos3 on NVIDIA Build, which does not serve that
> model — override them as shown above.

> **VLM endpoint credentials:** RT-VLM authenticates to the remote VLM with `VIA_VLM_API_KEY`, which
> the chart injects from `global.ngcApiSecret` (`ngc-api-key-secret` / `NGC_API_KEY`, populated from
> `global.ngcApiKey`). If your endpoint expects a different token, point
> `rtvi.vss-rtvi-vlm.ngcApiSecret.name` and `.key` at your own Secret. The `OPENAI_API_KEY` and
> `NVIDIA_API_KEY` values of `NOAPIKEYSET` in `values-build-endpoint.yaml` are inert, because
> `VIA_VLM_API_KEY` takes precedence over both.

### Deployed Components

This single chart deploys all application components:

- **Infrastructure**: PostgreSQL, Redis, Phoenix, Kafka
- **ELK Stack**: Elasticsearch, Kibana, Logstash
- **VST Pipeline**: Sensor MS, Stream Processing, SDR Envoy, VST Ingress, VST MCP
- **Search Pipeline**: NVStreamer, RTVI Embed (Cosmos), Search Analytics
- **Agent Services**: VSS Agent (search mode), VSS UI

### Webhook-based ingestion

VIOS loads `configs/vios/notification_config.json` — the Helm counterpart of
the Compose file
`deploy/docker/developer-profiles/dev-profile-search/vios/configs/notification_config.json`.
The Search chart renders it once per VIOS service into ConfigMaps
`vios-sensor-notification-config` and
`vios-streamprocessing-notification-config` (see
`global.vios.notificationConfigFile` and the two `*NotificationConfigMapName`
values). The names gain a `<release>-` prefix when
`global.useReleaseNamePrefix` is true. Each VIOS pod projects its own rendered
file over the subchart default, preserving that service's broker and endpoint
overrides.

`global.vios.notificationConfig` (inline JSON) replaces the bundled file in
that ConfigMap. A per-subchart `vios.vss-vios-sensor.notificationConfig` or
`vios.vss-vios-streamprocessing.notificationConfig` skips the overlay on that
pod so the subchart-generated file is used instead.

Edit the JSON to change Search webhook fan-out. Service-address placeholders
(`__RTVI_CV_ADDRESS__`, `__RTVI_EMBED_ADDRESS__`, `__RTVI_VLM_ADDRESS__`,
`__ELASTICSEARCH_ADDRESS__`) are resolved at install and honor
`global.useReleaseNamePrefix`. When `global.rtviInternalIngress.enabled=true`,
CV and Embed webhooks use the configured HAProxy controller paths so add/remove
requests retain `x-stream-id` affinity. Explicit per-service
`rtviCvServerAddress` / `rtviEmbedServerAddress` values take precedence.

### Critic Verification

The critic agent performs VLM-based verification of search results and is available by default. Set the request-level `use_critic` option to enable or skip verification for each search; it defaults to `true`.

Its VLM is served in-pod by **`rtvi.vss-rtvi-vlm`** (Cosmos3 checkpoint) in [Option A](#option-a-deploy-with-local-nims), or proxied by the same service to a user-provided remote endpoint in [Option B](#option-b-remote-llm-with-a-user-provided-vlm-endpoint). The same service supports `video_understanding`, so it is not exclusive to the critic. Setting `rtvi.vss-rtvi-vlm.enabled=false` removes both capabilities and should only be used when neither is needed.

## Verify Deployment

`helm template` and a successful `helm upgrade` do not mean the stack can search. The install examples use `--wait=false`, so Helm returns before workloads are ready. Use `<NAMESPACE>` on every command (the install examples use `vss-search`). `kubectl` otherwise uses the current-context namespace.

```bash
kubectl rollout status deployment/vss-agent -n <NAMESPACE>
kubectl rollout status deployment/vss-agent-ui -n <NAMESPACE>
kubectl rollout status deployment/vss-rtvi-embed -n <NAMESPACE>
kubectl get pods,pvc,nimservice -n <NAMESPACE>
kubectl logs -n <NAMESPACE> -f deployment/vss-rtvi-embed
```

With `global.useReleaseNamePrefix=true`, prefix those Deployment names with `<RELEASE_NAME>-`. Wait until PVCs are `Bound` and, for Option A, the Nemotron `NIMService` is ready. Embed model load can take several minutes.

Then exercise the product, not only the resource list:

1. Open the UI at the URL that matches how you exposed it (Ingress, NodePort, or port-forward). The browser must be calling the agent and VST URLs from the overlay for that mode.
2. Upload a clip on **Video Management**.
3. Run a search on the **Search** tab.
4. Run one search with `use_critic` enabled and confirm the critic returns, and one with it disabled.

A rollout that never becomes ready, a PVC stuck in `Pending`, or a search that cannot reach the agent is a failed deploy.

## Access the Services

### Access via Ingress (Recommended)

When deployed with `vssIngress.enabled=true` (the default), services are accessible via host-based routing through the Ingress controller.

| Service           | URL                                                    |
|-------------------|--------------------------------------------------------|
| VSS UI (Search)   | `http://vss-search.<NODE_IP>.nip.io`                       |
| VSS Agent API     | `http://vss-search.<NODE_IP>.nip.io/api`                   |
| VST API           | `http://vss-search.<NODE_IP>.nip.io/vst/api`               |
| RT-VLM (OpenAI)   | `http://vss-search.<NODE_IP>.nip.io/rtvi-vlm/v1`           |
| RT-Embed          | `http://vss-search.<NODE_IP>.nip.io/rtvi-embed/v1`         |
| RT-CV             | `http://vss-search.<NODE_IP>.nip.io/rtvi-cv/api/v1`        |
| LLM (NIM, [Option A](#option-a-deploy-with-local-nims) only) | `http://vss-search.<NODE_IP>.nip.io/llm/v1` |
| Elasticsearch     | `http://vss-search.<NODE_IP>.nip.io/elasticsearch`         |
| NVStreamer HTTP    | `http://streamer.<NODE_IP>.nip.io`                     |
| Kibana Dashboards | `http://kibana.<NODE_IP>.nip.io`                       |
| Phoenix Tracing   | `http://vss-search.<NODE_IP>.nip.io/phoenix`           |

Replace `<NODE_IP>` with the value of `$NODE_EXTERNAL_IP`. Phoenix is on the main host at `/phoenix`. A separate `phoenix.<NODE_IP>.nip.io` rule is added only when `vssIngress.hosts.phoenix` is set. The Phoenix Deployment sets `PHOENIX_HOST_ROOT_PATH=/phoenix`.

Verify the Ingress is configured:

```bash
kubectl get ingress -n <NAMESPACE>
```

### Access via NodePort

`values-nodeport.yaml` turns Ingress off and sets Service types to NodePort. It does not change the URLs the UI or the agent publish. With the install `--set global.externalHost=vss-search.$NODE_EXTERNAL_IP.nip.io`, the UI still renders `http://vss-search.<NODE_IP>.nip.io/api/v1` on port 80, and agent media and report URLs keep that origin. The NodePort table below is how you reach each Service. Pass this overlay as well so the browser and the agent use those ports. `chatBackendUrl` stays on the in-cluster agent; `chatCompletionUrl` is the browser URL and must not be the only chat setting, because an empty `chatBackendUrl` falls back to it.

```yaml
global:
  externalScheme: http
  externalHost: "<NODE_IP>"
  kibanaPublicUrl: "http://<NODE_IP>:31560"
agent:
  vss-agent:
    vstPublicBaseUseNipIo: false
    vssAgentExternalUrl: "http://<NODE_IP>:30800"
    reportsBaseUrl: "http://<NODE_IP>:30800/static/"
vss-agent-ui:
  agentApiUrlBase: "http://<NODE_IP>:30800/api/v1"
  vstApiUrl: "http://<NODE_IP>:30888/vst/api"
  chatCompletionUrl: "http://<NODE_IP>:30800/v1/chat/stream"
  chatBackendUrl: "http://vss-agent:8000/v1/chat/stream"
  sidebarChatBackendUrl: "http://vss-agent:8000/v1/chat/stream"
  websocketChatUrl: "ws://<NODE_IP>:30800/websocket"
  dashboardKibanaBaseUrl: "http://<NODE_IP>:31560"
```

`vssAgentExternalUrl` is also the first choice for `VST_EXTERNAL_URL`. In-cluster VST calls still use `VST_INTERNAL_URL` (`http://vss-vios-ingress:30888`). The UI VST tab uses `vstApiUrl`.

| Service           | URL                                    |
|-------------------|----------------------------------------|
| VSS UI (Search)   | `http://<NODE_IP>:32300`               |
| VSS Agent API     | `http://<NODE_IP>:30800/api`           |
| VST API           | `http://<NODE_IP>:30888/vst/api`       |
| NVStreamer HTTP    | `http://<NODE_IP>:30900`               |
| Kibana Dashboards | `http://<NODE_IP>:31560`               |
| Phoenix Tracing   | `http://<NODE_IP>:30606/phoenix`       |
| NVStreamer RTSP    | not exposed by default (port-forward to pod; see `vst_config.json` `rtsp_server_port`) |

With default **`values.yaml`**, NVStreamer is **ClusterIP** on port **31000** (use [port-forward](#access-via-port-forward) to reach it). The **30900** NodePort row applies when you install with **`values-nodeport.yaml`**.

Replace `<NODE_IP>` with the value of `$NODE_EXTERNAL_IP`.

### Access via Port-Forward

When using the default ClusterIP services (no Ingress or NodePort), use `kubectl port-forward`:

These names match the default **`global.useReleaseNamePrefix=false`**. If you set it to **`true`**, prefix service names with **`<RELEASE_NAME>-`**.

Run each forward in its own terminal. Add `-n <NAMESPACE>`. A port-forward does not change the URLs baked into the UI. Set the browser-facing keys to localhost and leave the server-side chat backends on the cluster Service, or the UI server will try to call `localhost` from inside the pod.

```yaml
agent:
  vss-agent:
    vstPublicBaseUseNipIo: false
    vssAgentExternalUrl: "http://localhost:8000"
    reportsBaseUrl: "http://localhost:8000/static/"
vss-agent-ui:
  agentApiUrlBase: "http://localhost:8000/api/v1"
  vstApiUrl: "http://localhost:30888/vst/api"
  chatCompletionUrl: "http://localhost:8000/v1/chat/stream"
  chatBackendUrl: "http://vss-agent:8000/v1/chat/stream"
  sidebarChatBackendUrl: "http://vss-agent:8000/v1/chat/stream"
  dashboardKibanaBaseUrl: "http://localhost:5601"
global:
  kibanaPublicUrl: "http://localhost:5601"
```

```bash
kubectl port-forward -n <NAMESPACE> svc/vss-agent-ui 3000:3000
kubectl port-forward -n <NAMESPACE> svc/vss-agent 8000:8000
kubectl port-forward -n <NAMESPACE> svc/vss-vios-ingress 30888:30888
kubectl port-forward -n <NAMESPACE> svc/vss-vios-nvstreamer 31000:31000
kubectl port-forward -n <NAMESPACE> svc/kibana 5601:5601
kubectl port-forward -n <NAMESPACE> svc/phoenix 6006:6006
```

| Service           | Port-Forward URL                     |
|-------------------|--------------------------------------|
| VSS UI (Search)   | `http://localhost:3000`              |
| VSS Agent API     | `http://localhost:8000/api`          |
| VST API           | `http://localhost:30888/vst/api`      |
| NVStreamer HTTP    | `http://localhost:31000`              |
| Kibana Dashboards | `http://localhost:5601`              |
| Phoenix Tracing   | `http://localhost:6006/phoenix`      |
| NVStreamer RTSP    | not exposed by default (port-forward to pod) |

## Upload Videos

Upload video files through the VSS UI **Video Management** tab:

1. Navigate to the VSS UI (Ingress: `http://vss-search.<NODE_IP>.nip.io`, port-forward: `http://localhost:3000`)
2. Click on **Video Management**
3. Use **Upload Video** to upload mp4/mkv files
4. Switch to the **Search** tab and query with natural language (e.g., "a person carrying boxes")

## Ingress Configuration

The chart creates a Kubernetes Ingress resource when `vssIngress.enabled=true`. All HTTP services use ClusterIP and are routed through the Ingress controller. Its mounts come from the [canonical route table](../../services/common/README.md), shared with the other developer profiles and with the Docker edge, so `/rtvi-vlm`, `/elasticsearch` and the rest resolve the same way on any VSS deployment. `/llm` is also mounted whenever the in-cluster Nemotron NIM is enabled ([Option A](#option-a-deploy-with-local-nims)); it is omitted entirely with `nims.enabled=false`. RTSP (NVStreamer) is not routed through HTTP Ingress; by default it is not exposed as a separate NodePort service.

`ingress:` is the former name of the `vssIngress:` block. Keys still read from it: `enabled`, `className`, `hosts.*`, `annotations`, `tls`. Its removed `cliRoutes.*` keys are ignored — Elasticsearch and the RTVI mounts are no longer toggled. Set one block or the other, not both.

### Ingress Values

| Parameter                    | Default                          | Description                        |
|------------------------------|----------------------------------|------------------------------------|
| `vssIngress.enabled`         | `true`                           | Enable Ingress resource creation   |
| `vssIngress.ingressClassName`| `haproxy`                        | Ingress controller class name (legacy `className` still read) |
| `vssIngress.annotations`     | `{}`                             | Additional Ingress annotations     |
| `vssIngress.hosts.main`      | `""` (auto: `vss-search.<IP>.nip.io`) | VSS UI + Agent + VST API host |
| `vssIngress.hosts.streamer`  | `""` (auto: `streamer.<IP>.nip.io`)   | NVStreamer HTTP API host      |
| `vssIngress.hosts.kibana`    | `""` (auto: `kibana.<IP>.nip.io`)     | Kibana dashboards host        |
| `vssIngress.hosts.phoenix`   | `""` | Empty keeps Phoenix at `/phoenix` on the main host. Set this to add a separate Phoenix host. |
| `vssIngress.tls`             | `[]`                             | TLS configuration (secretName + hosts) |
| `vssIngress.elasticsearchPort` | `9200`                         | Backend port for `/elasticsearch`. The edge guard is on the `elasticsearch` Service (`infra.elasticsearch.ingressGuard`): denies PUT/DELETE, cluster-admin and two-segment mutating paths, but POST still reaches Elasticsearch |
| `vssIngress.rtviVlmPort` / `rtviCvPort` / `rtviEmbedPort` | `8000` / `9000` / `8000` | Backend ports for `/rtvi-vlm`, `/rtvi-cv`, `/rtvi-embed` |
| `vssIngress.timeoutClient` / `timeoutTunnel` | `3600s`          | Rendered for continuity, but **inert**: this controller reads both only from its own ConfigMap, never from an Ingress annotation. Server-side timeouts do work per backend and are set on the rtvi and agent Services |

When `hosts.main`, `hosts.streamer`, and `hosts.kibana` are empty, they are built as `vss-search.<IP>.nip.io`, `streamer.<IP>.nip.io`, and `kibana.<IP>.nip.io` from `global.externalHost`. `hosts.phoenix` is not. Kibana and streamer hosts are automatic. Phoenix stays on the main host until `hosts.phoenix` is set.

Browser URLs do not follow `vssIngress.hosts.*`. With `agent.vss-agent.vstPublicBaseUseNipIo` left at its default `true`, agent public URLs are built as `http://vss-search.<IP>.nip.io` from `global.externalHost`. The UI uses `global.externalScheme` and `global.externalHost`. Kibana's public URL is `global.kibanaPublicUrl`, or `kibana.<global.externalHost>` when that is empty.

### Custom Ingress Hostnames

Set the Ingress hosts and the public URL keys together. Leaving `global.externalHost` as the bare node IP while only changing `vssIngress.hosts.*` updates the Ingress rules and leaves the UI on `http://<NODE_IP>/api/v1` and the agent on `vss-search.<NODE_IP>.nip.io`.

```yaml
global:
  externalScheme: http
  externalHost: vss-search.example.com
  kibanaPublicUrl: http://kibana.example.com
  ngcApiKey: "<NGC_CLI_API_KEY>"
  storageClass: "<STORAGE_CLASS>"
agent:
  vss-agent:
    vstPublicBaseUseNipIo: false
vssIngress:
  hosts:
    main: vss-search.example.com
    streamer: streamer.example.com
    kibana: kibana.example.com
    # phoenix: phoenix.example.com   # optional separate host; default is /phoenix on main
vss-agent-ui:
  dashboardKibanaBaseUrl: http://kibana.example.com
```

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -n vss-search \
  -f dev-profile-search/values-custom-host.yaml \
  --wait=false
```

Save that YAML as `dev-profile-search/values-custom-host.yaml` (it is an example, not a file in this chart).

### TLS

Create the TLS secret in the same namespace as the Ingress, then confirm it is there. Unquoted `tls[0]` in zsh is a glob and fails before Helm runs. Put TLS in the values file.

```bash
kubectl create secret tls vss-search-tls \
  --cert=path/to/tls.crt \
  --key=path/to/tls.key \
  -n vss-search
kubectl get secret vss-search-tls -n vss-search
```

```yaml
global:
  externalScheme: https
  externalHost: vss-search.example.com
  kibanaPublicUrl: https://kibana.example.com
agent:
  vss-agent:
    vstPublicBaseUseNipIo: false
vssIngress:
  hosts:
    main: vss-search.example.com
    kibana: kibana.example.com
    streamer: streamer.example.com
  tls:
    - secretName: vss-search-tls
      hosts:
        - vss-search.example.com
        - kibana.example.com
        - streamer.example.com
vss-agent-ui:
  dashboardKibanaBaseUrl: https://kibana.example.com
```

A certificate on the Ingress does not switch `http` URLs to `https`. Set `global.externalScheme=https` and the public URL keys above. Add a host to `tls` for each auxiliary hostname you serve over HTTPS. Phoenix on the main host is covered by the main host entry. A separate `hosts.phoenix` needs its own TLS host.

## Teardown

Back up media and metadata before `helm uninstall`. The shared VST PVCs are chart-managed and have no `helm.sh/resource-policy: keep` annotation, so uninstall deletes those claims. Whether the bytes remain depends on the PV reclaim policy. PostgreSQL and Elasticsearch claims created by this release go with it too. `NIMCache` is namespaced, has `helm.sh/resource-policy: keep`, and stays after uninstall. It is not a PVC.

```bash
kubectl get pvc -n <NAMESPACE>
kubectl get nimcache -n <NAMESPACE>
helm get manifest vss-search -n <NAMESPACE> | kubectl get -f - -n <NAMESPACE>

# After the backup, uninstall the search release only.
helm uninstall vss-search -n <NAMESPACE>

# Leftover claims for this release, after you have decided the data can go.
# Do not use `kubectl delete pvc --all` or `kubectl delete nimcache --all`.
kubectl get pvc -n <NAMESPACE> -l app.kubernetes.io/instance=vss-search
kubectl delete pvc -n <NAMESPACE> -l app.kubernetes.io/instance=vss-search
kubectl delete nimcache nemotron-35-lightning-30b-a3b -n <NAMESPACE>
```

HAProxy (`haproxy-kubernetes-ingress` in `haproxy-controller`) and local-path-provisioner (`local-path-provisioner-default` in `default`) are cluster prerequisites. This chart does not own them. Leave them installed when any other application uses them. Uninstall one only after you have confirmed it has no other users.
