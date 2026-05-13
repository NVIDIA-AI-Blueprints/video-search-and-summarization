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

## GPU Requirements

The stack requests GPUs (`nvidia.com/gpu: 1` each) for the workloads listed below. The exact total depends on whether you deploy with hosted NIMs (NVIDIA Build Endpoint) or local NIMs.

### With NVIDIA Build Endpoint (Option A)

| Workload | GPU |
|----------|-----|
| `vss-rtvi-cv` | 1 |
| `vss-rtvi-embed` (Cosmos Embed) | 1 |
| `vss-vios-streamprocessing` | 1 |
| **Total** | **3** |

### With Local NIMs (Option B)

The critic agent and Cosmos NIM are **enabled by default** (`global.enableCritic=true`, `nims.cosmos.enabled=true`). To reduce GPU requirements, disable them with `--set global.enableCritic=false,nims.cosmos.enabled=false` (saves 1 GPU).

| Workload | GPU | Notes |
|----------|-----|-------|
| `vss-rtvi-cv` | 1 | |
| `vss-rtvi-embed` (Cosmos Embed) | 1 | |
| `vss-vios-streamprocessing` | 1 | |
| `nvidia-nemotron-nano-9b-v2` (NIM) | 1 | |
| `nvidia-cosmos-reason2-8b` (NIM) | 1 | Critic agent VLM — enabled by default |
| **Total** | **5** | **4** if critic is disabled |

> **Note:** By default, `nvidia-cosmos-reason2-8b` requests **1 full GPU** (`nvidia.com/gpu: "1"`).
> If you are using GPU time-slicing, adjust the resource request in `values.yaml` as needed
> (e.g. `nims.cosmos.resources.limits."nvidia.com/gpu": "2"` for two time-sliced replicas).

### GPU Time-Slicing (Limited GPU Environments)

If you have limited GPUs, you can enable **time-slicing** to share a single physical GPU between multiple pods. This allows workloads to share GPU memory and compute without requiring dedicated GPUs for each pod.

For setup instructions, refer to [Time-Slicing GPUs in Kubernetes](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html).

When time-slicing is enabled, each time-sliced partition appears as a separate `nvidia.com/gpu` resource. Adjust the GPU resource requests in `values.yaml` to match your time-slicing configuration:

```bash
# Example: Cosmos NIM needs 1 full GPU but with 2x time-slicing per GPU
--set nims.cosmos.resources.limits."nvidia.com/gpu"="2" \
--set nims.cosmos.resources.requests."nvidia.com/gpu"="2"
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

- **NVIDIA NIM Operator** (required only for [Option B: Local NIMs](#option-b-deploy-with-local-nims))
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

> **Critic agent behavior** is enabled by default (`global.enableCritic=true`). With NVIDIA Build Endpoint (Option A), local Nemotron and Cosmos NIMs are disabled by `values-build-endpoint.yaml`; disable critic verification with `--set global.enableCritic=false`. With Local NIMs (Option B), disable both critic verification and the local Cosmos NIM with `--set global.enableCritic=false,nims.cosmos.enabled=false`. See [Disabling the Critic Agent](#disabling-the-critic-agent).

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

Verify the controller is running:

```bash
kubectl get pods -n haproxy-controller
kubectl get ingressclass
```

You should see an IngressClass named `haproxy`.

## Step 3: Deploy the Search Profile

**Note:** The Helm install can take several minutes while dependent services start; wait for workloads to become Ready before using the UI. Use **`global.ngcApiKey`** (and **`nims.global.ngcApiKey`** for [Option B](#option-b-deploy-with-local-nims)) as in the examples below—Helm creates the needed NGC and registry secrets from those values.

```bash
# Clone the repository. For a specific branch or tag, add: -b <name-or-tag> (before the URL).
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deploy/helm/developer-profiles

helm dependency build ./dev-profile-search
```

### Option A: Remote NIMs

Deploy with NVIDIA Build Endpoint

Uses hosted NIMs at `https://integrate.api.nvidia.com` — no local GPU required for LLM/VLM inference.
vss-rtvi-cv and RTVI Embed still run on local GPUs. See [GPU Requirements](#with-nvidia-build-endpoint-option-a).

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -f dev-profile-search/values-build-endpoint.yaml \
  -n vss-search --create-namespace \
  --set global.externalHost=vss-search.$NODE_EXTERNAL_IP.nip.io \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set agent.vss-agent.apiKeys.nvidia=$NGC_CLI_API_KEY \
  --set global.storageClass=$STORAGE_CLASS \
  --wait=false
```

> **Option A note:** `values-build-endpoint.yaml` disables local Nemotron and Cosmos NIM deployments (`nims.nemotron.enabled=false`, `nims.cosmos.enabled=false`). Critic verification is still enabled by default and uses the hosted VLM endpoint. To disable critic verification, add `--set global.enableCritic=false`.

**Custom remote NIM (self-hosted or external endpoints)**

If you already run **NIM** (or an OpenAI-compatible LLM/VLM API) outside this cluster—another namespace, a shared service, or a hosted endpoint—use the steps below to point **vss-agent** at those URLs. Set **`nims.enabled=false`** so this chart does not deploy in-cluster NIM workloads; set **`agent.vss-agent.llmBaseUrl`** and **`agent.vss-agent.vlmBaseUrl`** to the HTTP(S) base URLs your agent can reach (include path prefix if your service requires it). Keep **`agent.vss-agent.llmName`** and **`agent.vss-agent.vlmName`** aligned with the models those endpoints serve.

This profile lists the **full** **`agent.vss-agent.env`** block for Search deployments. Search behavior is driven by **`general.front_end.streaming_ingest`** in `configs/vss-agent/config.yml`; the chart wires the agent for remote VLM mode, critic verification via **`global.enableCritic`**, and the default Elasticsearch index **`mdx-embed-filtered-2025-01-01`**. Override **`agent.vss-agent.elasticsearchUrl`** or **`agent.vss-agent.elasticsearchIndex`** when you need a different Elasticsearch endpoint or index.

```bash

export LLM_BASE_URL='<REMOTE LLM ENDPOINT>'
export VLM_BASE_URL='<REMOTE VLM ENDPOINT>'

helm upgrade --install vss-search ./dev-profile-search \
  -f dev-profile-search/values-build-endpoint.yaml \
  -n vss-search --create-namespace \
  --set global.externalHost=vss-search.$NODE_EXTERNAL_IP.nip.io \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set agent.vss-agent.apiKeys.nvidia=$NGC_CLI_API_KEY \
  --set global.storageClass=$STORAGE_CLASS \
  --set nims.enabled=false \
  --set agent.vss-agent.llmName="nvidia/nvidia-nemotron-nano-9b-v2" \
  --set agent.vss-agent.vlmName="nvidia/cosmos-reason2-8b" \
  --set agent.vss-agent.llmBaseUrl="$LLM_BASE_URL" \
  --set agent.vss-agent.vlmBaseUrl="$VLM_BASE_URL" \
  --wait=false
```



### Option B: Deploy with Local NIMs

Runs all LLM/VLM NIMs on-cluster via the NIM Operator. Requires additional GPUs for Nemotron and Cosmos (unless `ENABLE_CRITIC` is `false`). See [GPU Requirements](#with-local-nims-option-b).

**Prerequisite:** Install the [NVIDIA NIM Operator](#prerequisites) before deploying.

Shared **`helm/services/nims`** only gates Cosmos on **`nims.cosmos.enabled`** (it does not read **`global.enableCritic`**). Both default to `true`. To disable critic and skip the Cosmos NIM, pass both keys in a **single** `--set` (comma-separated): `--set global.enableCritic=false,nims.cosmos.enabled=false`.

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

### Deployed Components

This single chart deploys all application components:

- **Infrastructure**: PostgreSQL, Redis, Phoenix, Kafka
- **ELK Stack**: Elasticsearch, Kibana, Logstash
- **VST Pipeline**: Sensor MS, Stream Processing, SDR Envoy, VST Ingress, VST MCP
- **Search Pipeline**: NVStreamer, RTVI Embed (Cosmos), Search Analytics
- **Agent Services**: VSS Agent (search mode), VSS UI

### Disabling the Critic Agent

The critic agent (VLM-based verification of search results) is **enabled by default**. With NVIDIA Build Endpoint (Option A), it uses the hosted VLM endpoint and local Cosmos NIM is not deployed. With Local NIMs (Option B), its backing **Cosmos Reason2 8B** NIM is enabled by default.

For NVIDIA Build Endpoint or another remote VLM endpoint, disable critic verification with:

```bash
--set global.enableCritic=false
```

For Local NIM deployments, disable both critic verification and the local Cosmos NIM with:

```bash
--set global.enableCritic=false,nims.cosmos.enabled=false
```

This has two effects:

1. **Agent config**: `enable_critic` is set to `false` in the vss-agent `config.yml`, so the search and search_agent functions skip VLM verification.
2. **Cosmos NIM**: For Local NIM deployments, the `nvidia-cosmos-reason2-8b` NIM pod is not deployed, freeing 1 GPU (see [GPU Requirements](#with-local-nims-option-b)).

> **Note:** `global.enableCritic` controls the agent behavior. `nims.cosmos.enabled` controls the Cosmos NIM pod. The shared **`helm/services/nims`** subchart does not read `global.enableCritic`, so Local NIM deployments should set both keys together. When using a **remote VLM** endpoint (`nims.enabled=false` + `agent.vss-agent.vlmBaseUrl`), set only `global.enableCritic=false` to disable the critic while keeping the remote VLM URL configured for other uses.

To re-enable critic verification with NVIDIA Build Endpoint or another remote VLM endpoint:

```bash
helm upgrade vss-search ./dev-profile-search \
  --reuse-values \
  --set global.enableCritic=true
```

To re-enable critic verification and the local Cosmos NIM with Local NIMs:

```bash
helm upgrade vss-search ./dev-profile-search \
  --reuse-values \
  --set global.enableCritic=true,nims.cosmos.enabled=true
```

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
