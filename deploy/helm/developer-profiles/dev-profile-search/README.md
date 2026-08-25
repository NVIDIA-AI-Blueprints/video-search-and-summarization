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
| `nemotron-35-lightning-30b-a3b` (NIM) | 1 | |
| `vss-rtvi-vlm` (Cosmos3 checkpoint) | 1 | VLM used by the critic and `video_understanding` — enabled by default |
| **Total** | **5** | **4** if the VLM is disabled (`rtvi.vss-rtvi-vlm.enabled=false`) |

> **Note:** The VLM (used by the critic and `video_understanding`) is served in-pod by
> `rtvi.vss-rtvi-vlm` (not the standalone `nvidia-cosmos3-reasoner` NIM, which is disabled by
> default). It requests **1 full GPU** (`nvidia.com/gpu: "1"`), scheduled on any free GPU. To use
> the standalone NIM instead, set `nims.cosmos3.enabled=true`, `rtvi.vss-rtvi-vlm.enabled=false`,
> and flip the agent `VLM_MODEL_TYPE` back to `nim`.

### With Remote LLM and VLM Endpoints (Option B)

| Workload | GPU | Notes |
|----------|-----|-------|
| `vss-rtvi-cv` | 1 | |
| `vss-rtvi-embed` (Cosmos Embed) | 1 | |
| `vss-vios-streamprocessing` | 1 | |
| `vss-rtvi-vlm` (proxy) | 1 | Forwards VLM calls to the remote endpoint and loads no local model, but still requests `nvidia.com/gpu: 1` from the `rtvi-vlm` subchart defaults |
| **Total** | **4** | |

### GPU Time-Slicing (Limited GPU Environments)

If you have limited GPUs, you can enable **time-slicing** to share a single physical GPU between multiple pods. This allows workloads to share GPU memory and compute without requiring dedicated GPUs for each pod.

For setup instructions, refer to [Time-Slicing GPUs in Kubernetes](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html).

When time-slicing is enabled, each time-sliced partition appears as a separate `nvidia.com/gpu` resource. Adjust the GPU resource requests in `values.yaml` to match your time-slicing configuration:

```bash
# Example: Nemotron NIM needs 1 full GPU but with 2x time-slicing per GPU
--set nims.nemotron35.resources.limits."nvidia.com/gpu"="2" \
--set nims.nemotron35.resources.requests."nvidia.com/gpu"="2"
```

## Prerequisites

- **Kubernetes cluster**
  - Running cluster whose API you can reach with **`kubectl`** (correct context and, if applicable, kubeconfig).
  - **Server version** validated for this profile: **1.34** — use a different minor/patch only if your platform or release notes require it; confirm compatibility with the [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) and [NIM Operator](https://docs.nvidia.com/nim-operator/latest/install.html) versions you deploy.

- **NVIDIA GPU Operator**
  - Install the GPU Operator on the cluster. Follow [GPU Operator getting started](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html).
  - **Driver (x86 Ubuntu)** — pin via GPU Operator driver settings as appropriate:
    - **580.105.08** (x86 hosts with Ubuntu 24.04)
    - **580.65.06** (x86 hosts with Ubuntu 22.04)

- **NVIDIA NIM Operator** (required only for [Option A: Local NIMs](#option-a-deploy-with-local-nims))
  - Required when `nims` subcharts are enabled (`NIMCache` / `NIMService`).
  - Install **after** the GPU Operator. See [NIM Operator installation](https://docs.nvidia.com/nim-operator/latest/install.html).
  - Install the NIM Operator:

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update

kubectl create namespace nim-operator

helm upgrade --install nim-operator nvidia/k8s-nim-operator \
  -n nim-operator \
  --version=3.0.2

# Verify the operator pod is running
kubectl get pods -n nim-operator
```

- **Volume provisioner (e.g. local-path)**
  - A **StorageClass** must exist on the cluster. Set **`global.storageClass`** in your Helm values override to that class’s **`metadata.name`** (this profile’s install examples use **`--set global.storageClass=…`**).
  - **Bare-metal clusters:** Install **local-path** (see [rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner/tree/master)), or use the Helm-based install in [Step 1](#step-1-volume-provisioner-bare-metal-optional) if you prefer that packaging.
  - **Default StorageClass:** If your class (for example **`local-path`**) is not already the default, set it as the default StorageClass:

    ```bash
    kubectl patch storageclass local-path -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
    ```

    Replace **`local-path`** with your StorageClass **`metadata.name`** if it differs.

### Chart / tooling

- **Helm** 3.x
- **kubectl**
- **GPUs**: see [GPU Requirements](#gpu-requirements)
- **NGC**: API key for image pull, model downloads, and NIM access
- **StorageClass**: set **`global.storageClass`** to a class on the cluster (see [Prerequisites](#prerequisites)—**Volume provisioner**).

## Environment Setup

```bash
export NODE_EXTERNAL_IP='<your node IP>'
export NGC_CLI_API_KEY='<your NGC API key>'
export STORAGE_CLASS='<Storage Class Name>'
export GPU_NAME='H100'  # One of: H100, L40S, RTXPRO6000BW
```

> **Critic behavior** is controlled per request with `use_critic` and defaults to enabled. RT-VLM remains available for both critic verification and `video_understanding`.

## Step 1: Volume provisioner (bare metal, optional)

Use this **only** when you want to install **local-path** with Helm on bare metal. If the cluster already has a suitable StorageClass, set **`global.storageClass`** per [Prerequisites](#prerequisites)—**Volume provisioner** and skip this step.

```bash
helm repo add containeroo https://charts.containeroo.ch
helm repo update

helm upgrade --namespace default --install \
  local-path-provisioner-default \
  containeroo/local-path-provisioner \
  --version '0.0.32'
```

Verify the StorageClass exists:

```bash
kubectl get storageclass
```

If **`local-path`** is listed but is **not** the default (no **`(default)`** marker), run the **`kubectl patch storageclass`** command under [Prerequisites](#prerequisites)—**Volume provisioner** (**Default StorageClass**) to set it as the default StorageClass.

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

> **In-cluster RTVI affinity (optional).** Only needed when you deploy the Search profile with `global.rtviInternalIngress.enabled=true` (default `false`). That path routes vss-agent → rtvi-cv / rtvi-embed through the controller's **ClusterIP Service** at `haproxy-kubernetes-ingress.haproxy-controller:80`. To enable it, append `--set controller.service.type=ClusterIP` to the install command above. If you only need external traffic, leave it off.

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
# Clone the repository. For a specific branch or tag, add: -b <name-or-tag> (before the URL).
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deploy/helm/developer-profiles

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

The LLM and the evaluation judge can use the hosted `nvidia/nemotron-3.5-lightning-30b-a3b` on
`https://integrate.api.nvidia.com`, whose hosted model catalogue changes over time — if the endpoint stops serving this
model, pick a current model from the
[API catalog](https://build.nvidia.com) or point `llmBaseUrl` at your own LLM as well.
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

Include a path prefix in the base URLs if your service requires one, and keep every model id aligned
with what the endpoints actually advertise on `/v1/models`.

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

> **Option B note:** `values-build-endpoint.yaml` disables local Nemotron and Cosmos3 NIM
> deployments (`nims.nemotron35.enabled=false`, `nims.cosmos3.enabled=false`). Its `global.vlmBaseUrl`
> and `global.vlmName` defaults still point at Cosmos3 on NVIDIA Build, which does not serve that
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

### Critic Verification

The critic agent performs VLM-based verification of search results and is available by default. Set the request-level `use_critic` option to enable or skip verification for each search; it defaults to `true`.

Its VLM is served in-pod by **`rtvi.vss-rtvi-vlm`** (Cosmos3 checkpoint) in [Option A](#option-a-deploy-with-local-nims), or proxied by the same service to a user-provided remote endpoint in [Option B](#option-b-remote-llm-with-a-user-provided-vlm-endpoint). The same service supports `video_understanding`, so it is not exclusive to the critic. Setting `rtvi.vss-rtvi-vlm.enabled=false` removes both capabilities and should only be used when neither is needed.

## Verify Deployment

```bash
kubectl get pods -n <NAMESPACE>
kubectl get deployments -n <NAMESPACE>
kubectl get statefulsets -n <NAMESPACE>
kubectl get svc -n <NAMESPACE>
kubectl get ingress -n <NAMESPACE>

# Check RTVI Embed model loading (may take 5-10 minutes)
kubectl logs -f deployment/vss-rtvi-embed # <RELEASE_NAME>-vss-rtvi-embed if global.useReleaseNamePrefix=true
```

## Access the Services

### Access via Ingress (Recommended)

When deployed with `ingress.enabled=true` (the default), services are accessible via host-based routing through the Ingress controller.

| Service           | URL                                                    |
|-------------------|--------------------------------------------------------|
| VSS UI (Search)   | `http://vss-search.<NODE_IP>.nip.io`                       |
| VSS Agent API     | `http://vss-search.<NODE_IP>.nip.io/api`                   |
| VST API           | `http://vss-search.<NODE_IP>.nip.io/vst/api`               |
| NVStreamer HTTP    | `http://streamer.<NODE_IP>.nip.io`                     |
| Kibana Dashboards | `http://kibana.<NODE_IP>.nip.io`                       |
| Phoenix Tracing   | `http://phoenix.<NODE_IP>.nip.io`                      |

Replace `<NODE_IP>` with the value of `$NODE_EXTERNAL_IP`.

Verify the Ingress is configured:

```bash
kubectl get ingress -n <NAMESPACE>
```

### Access via NodePort

When deployed with `values-nodeport.yaml`, services are accessible directly on the node IP.

| Service           | URL                                    |
|-------------------|----------------------------------------|
| VSS UI (Search)   | `http://<NODE_IP>:32300`               |
| VSS Agent API     | `http://<NODE_IP>:30800/api`           |
| VST API           | `http://<NODE_IP>:30888/vst/api`       |
| NVStreamer HTTP    | `http://<NODE_IP>:30900`               |
| Kibana Dashboards | `http://<NODE_IP>:31560`               |
| Phoenix Tracing   | `http://<NODE_IP>:30606`               |
| NVStreamer RTSP    | not exposed by default (port-forward to pod; see `vst_config.json` `rtsp_server_port`) |

With default **`values.yaml`**, NVStreamer is **ClusterIP** on port **31000** (use [port-forward](#access-via-port-forward) to reach it). The **30900** NodePort row applies when you install with **`values-nodeport.yaml`**.

Replace `<NODE_IP>` with the value of `$NODE_EXTERNAL_IP`.

### Access via Port-Forward

When using the default ClusterIP services (no Ingress or NodePort), use `kubectl port-forward`:

These names match the default **`global.useReleaseNamePrefix=false`**. If you set it to **`true`**, prefix service names with **`<RELEASE_NAME>-`**.

```bash
# VSS UI
kubectl port-forward svc/vss-agent-ui 3000:3000

# VSS Agent API
kubectl port-forward svc/vss-agent 8000:8000

# VST API (via vss-vios-ingress; service listens on 30888)
kubectl port-forward svc/vss-vios-ingress 30888:30888

# NVStreamer HTTP (ClusterIP service port 31000; matches bundled vst_config.json)
kubectl port-forward svc/vss-vios-nvstreamer 31000:31000

# Kibana
kubectl port-forward svc/kibana 5601:5601

# Phoenix (Service metadata name is `phoenix` when release-name prefixing is off)
kubectl port-forward svc/phoenix 6006:6006
```

| Service           | Port-Forward URL                     |
|-------------------|--------------------------------------|
| VSS UI (Search)   | `http://localhost:3000`              |
| VSS Agent API     | `http://localhost:8000/api`          |
| VST API           | `http://localhost:30888/vst/api`      |
| NVStreamer HTTP    | `http://localhost:31000`              |
| Kibana Dashboards | `http://localhost:5601`              |
| Phoenix Tracing   | `http://localhost:6006`              |
| NVStreamer RTSP    | not exposed by default (port-forward to pod) |

## Upload Videos

Upload video files through the VSS UI **Video Management** tab:

1. Navigate to the VSS UI (Ingress: `http://vss-search.<NODE_IP>.nip.io`, port-forward: `http://localhost:3000`)
2. Click on **Video Management**
3. Use **Upload Video** to upload mp4/mkv files
4. Switch to the **Search** tab and query with natural language (e.g., "a person carrying boxes")

## Ingress Configuration

The chart creates a Kubernetes Ingress resource when `ingress.enabled=true`. All HTTP services use ClusterIP and are routed through the Ingress controller. RTSP (NVStreamer) is not routed through HTTP Ingress; by default it is not exposed as a separate NodePort service.

### Ingress Values

| Parameter                    | Default                          | Description                        |
|------------------------------|----------------------------------|------------------------------------|
| `ingress.enabled`            | `true`                           | Enable Ingress resource creation   |
| `ingress.className`          | `haproxy`                        | Ingress controller class name      |
| `ingress.annotations`        | `{}`                             | Additional Ingress annotations     |
| `ingress.hosts.main`         | `""` (auto: `vss-search.<IP>.nip.io`) | VSS UI + Agent + VST API host |
| `ingress.hosts.streamer`     | `""` (auto: `streamer.<IP>.nip.io`)   | NVStreamer HTTP API host      |
| `ingress.hosts.kibana`       | `""` (auto: `kibana.<IP>.nip.io`)     | Kibana dashboards host        |
| `ingress.hosts.phoenix`      | `""` (auto: `phoenix.<IP>.nip.io`)    | Phoenix tracing UI host       |
| `ingress.tls`                | `[]`                             | TLS configuration (secretName + hosts) |

When host values are left empty (default), they are auto-constructed from `global.externalHost` using `nip.io` wildcard DNS.

### Custom Ingress Hostnames

To use custom DNS names instead of `nip.io`:

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -n vss-search \
  --set global.externalHost=$NODE_EXTERNAL_IP \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set global.storageClass=$STORAGE_CLASS \
  --set ingress.hosts.main=vss-search.example.com \
  --set ingress.hosts.streamer=streamer.example.com \
  --set ingress.hosts.kibana=kibana.example.com \
  --set ingress.hosts.phoenix=phoenix.example.com \
  --wait=false
```

### TLS

To enable TLS, create a Kubernetes secret with your certificate and reference it:

```bash
kubectl create secret tls vss-search-tls \
  --cert=path/to/tls.crt \
  --key=path/to/tls.key

helm upgrade --install vss-search ./dev-profile-search \
  -n vss-search \
  --set global.externalHost=$NODE_EXTERNAL_IP \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set global.storageClass=$STORAGE_CLASS \
  --set ingress.hosts.main=vss-search.example.com \
  --set ingress.tls[0].secretName=vss-search-tls \
  --set ingress.tls[0].hosts[0]=vss-search.example.com \
  --wait=false
```

## Teardown

```bash
# Uninstall the search profile
helm uninstall vss-search -n <NAMESPACE>

# Clean up PVCs (includes database, video storage, model caches)
kubectl delete pvc -l app.kubernetes.io/instance=vss-search

# If you installed additional Helm releases for NIMs or other add-ons, uninstall them by release name, for example:
# helm uninstall <OTHER_RELEASE_NAME> -n <NAMESPACE>

# Uninstall HAProxy Ingress controller
helm uninstall haproxy-kubernetes-ingress -n haproxy-controller

# Uninstall local-path provisioner (if installed in namespace default)
helm uninstall local-path-provisioner-default -n default

# Cleanup remaining storage
kubectl delete nimcache --all -n <NAMESPACE>
kubectl delete pvc --all -n <NAMESPACE>
```
