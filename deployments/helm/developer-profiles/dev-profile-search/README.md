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
| `perception` | 1 |
| `rtvi-embed` (Cosmos Embed) | 1 |
| `streamprocessing-ms` | 1 |
| **Total** | **3** |

### With Local NIMs (Option B)

| Workload | GPU |
|----------|-----|
| `perception` | 1 |
| `rtvi-embed` (Cosmos Embed) | 1 |
| `streamprocessing-ms` | 1 |
| `nvidia-nemotron-nano-9b-v2` (NIM) | 1 |
| `cosmos-reason2-8b` (NIM) | 1 |
| **Total** | **5** |

> **Note:** By default, `cosmos-reason2-8b` requests **1 full GPU** (`nvidia.com/gpu: "1"`).
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

- **Volume provisioner / StorageClass**
  - A **default StorageClass** must exist on the cluster.
  - **Bare-metal clusters:** install a local-path provisioner (see [rancher/local-path-provisioner](https://github.com/rancher/local-path-provisioner/tree/master)).
  - **Cloud clusters:** use your provider's block storage class (e.g. `gp3`, `oci-bv-high`, `standard`). Skip [Step 1](#step-1-install-local-path-provisioner-bare-metal-only) and proceed to [Step 2](#step-2-install-ingress-controller-haproxy).

### Chart / Tooling

- **Helm** 3.x
- **kubectl**
- **GPUs**: see [GPU Requirements](#gpu-requirements)
- **NGC**: API key for image pull, model downloads, and NIM access

## Environment Setup

```bash
export NODE_EXTERNAL_IP='<your node IP>'
export NGC_CLI_API_KEY='<your NGC API key>'
export GPU_NAME='H100'  # One of: H100, L40S, RTXPRO6000BW
export ENABLE_CRITIC='false'  # Set 'true' to enable VLM verification of the search results, requires VLM inference
```
## Step 1: Install Local-Path Provisioner (Bare-Metal Only)

> **Cloud clusters** that already have a default StorageClass (e.g. `gp3`, `standard`) can skip this step.

```bash
helm repo add containeroo https://charts.containeroo.ch
helm repo update

helm upgrade --namespace default --install \
  local-path-provisioner-default \
  containeroo/local-path-provisioner \
  --version '0.0.32'

# Patch storage class as default
kubectl patch storageclass local-path \
  -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```

Verify:

```bash
kubectl get storageclass
```

You should see `local-path (default)` in the output.

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

`NOTE:` Helm install command will take a few minutes to install all dependent services so please wait

The following Kubernetes secrets are **automatically created** by the chart when `global.ngcApiKey` is set:
- `ngc-api-key-secret` — NGC API key for model downloads (used by perception, rtvi-embed, and other services)
- `ngc-docker-reg-secret` — Docker registry pull secret for `nvcr.io` images
- `ngc-nim-api-key-secret` — NIM-specific API key secret
- `ngc-nim-docker-reg-secret` — NIM-specific registry pull secret


```bash
# Clone the repository
git clone -b feat/kubernetes-support https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deployments/helm/developer-profiles
```

### Option A: Deploy with NVIDIA Build Endpoint (Recommended)

Uses hosted NIMs at `https://integrate.api.nvidia.com` — no local GPU required for LLM/VLM inference.
Perception and RTVI Embed still run on local GPUs. See [GPU Requirements](#with-nvidia-build-endpoint-option-a).

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -f dev-profile-search/values-build-endpoint.yaml \
  -n vss-search --create-namespace \
  --set global.externalHost=$NODE_EXTERNAL_IP \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set agents.vssAgent.apiKeys.nvidia=$NGC_CLI_API_KEY \
  --set global.enableCritic=$ENABLE_CRITIC \
  --wait=false
```

### Option B: Deploy with Local NIMs

Runs all LLM/VLM NIMs on-cluster via the NIM Operator. Requires additional GPUs for Nemotron and Cosmos (if `ENABLE_CRITIC` is `true`). See [GPU Requirements](#with-local-nims-option-b).

**Prerequisite:** Install the [NVIDIA NIM Operator](#prerequisites) before deploying.

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -n vss-search --create-namespace \
  --set global.externalHost=$NODE_EXTERNAL_IP \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
  --set nims.global.ngcApiKey=$NGC_CLI_API_KEY \
  --set global.enableCritic=$ENABLE_CRITIC \
  --set nims.gpuType=$GPU_NAME \
  --wait=false
```

#### With NodePort (instead of Ingress)

Services are exposed directly on the node via NodePort. No Ingress controller required.

```bash
helm upgrade --install vss-search ./dev-profile-search \
  -f dev-profile-search/values-nodeport.yaml \
  -n vss-search --create-namespace \
  --set global.externalHost=$NODE_EXTERNAL_IP \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
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

## Verify Deployment

```bash
kubectl get pods -n <NAMESPACE>
kubectl get deployments -n <NAMESPACE>
kubectl get statefulsets -n <NAMESPACE>
kubectl get svc -n <NAMESPACE>
kubectl get ingress -n <NAMESPACE>

# Check RTVI Embed model loading (may take 5-10 minutes)
kubectl logs -f deployment/vss-search-rtvi-embed # <RELEASE_NAME>-rtvi-embed
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
kubectl get ingress -n default
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
| NVStreamer RTSP    | `rtsp://<NODE_IP>:31554`               |

Replace `<NODE_IP>` with the value of `$NODE_EXTERNAL_IP`.

### Access via Port-Forward

When using the default ClusterIP services (no Ingress or NodePort), use `kubectl port-forward`:

```bash
# VSS UI
kubectl port-forward svc/vss-search-agents-vss-ui 3000:3000

# VSS Agent API
kubectl port-forward svc/vss-search-agents-vss-agent 8000:8000

# VST API (via vst-ingress)
kubectl port-forward svc/vss-search-vst-ingress 8080:8000

# NVStreamer HTTP
kubectl port-forward svc/vss-search-nvstreamer-nvstreamer 9100:9100

# Kibana
kubectl port-forward svc/vss-search-kibana-kibana 5601:5601

# Phoenix
kubectl port-forward svc/vss-search-phoenix 6006:6006


| Service           | Port-Forward URL                     |
|-------------------|--------------------------------------|
| VSS UI (Search)   | `http://localhost:3000`              |
| VSS Agent API     | `http://localhost:8000/api`          |
| VST API           | `http://localhost:8080/vst/api`      |
| NVStreamer HTTP    | `http://localhost:9100`              |
| Kibana Dashboards | `http://localhost:5601`              |
| Phoenix Tracing   | `http://localhost:6006`              |
| NVStreamer RTSP    | `rtsp://<NODE_IP>:31554` (always NodePort) |

## Upload Videos

Upload video files through the VSS UI **Video Management** tab:

1. Navigate to the VSS UI (Ingress: `http://vss-search.<NODE_IP>.nip.io`, port-forward: `http://localhost:3000`)
2. Click on **Video Management**
3. Use **Upload Video** to upload mp4/mkv files
4. Switch to the **Search** tab and query with natural language (e.g., "a person carrying boxes")

## Ingress Configuration

The chart creates a Kubernetes Ingress resource when `ingress.enabled=true`. All HTTP services use ClusterIP and are routed through the Ingress controller. RTSP traffic (NVStreamer) always uses a separate NodePort service since RTSP cannot be routed through HTTP Ingress.

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
  -n default \
  --set global.externalHost=$NODE_EXTERNAL_IP \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
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
  -n default \
  --set global.externalHost=$NODE_EXTERNAL_IP \
  --set global.ngcApiKey=$NGC_CLI_API_KEY \
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

# Uninstall NIMs (if deployed separately)
helm uninstall nemotron-nim -n -n <NAMESPACE>

# Uninstall HAProxy Ingress controller
helm uninstall haproxy-kubernetes-ingress -n haproxy-controller

# Uninstall local-path provisioner (if installed)
helm uninstall local-path-provisioner-default

# Cleanup remaining storage
kubectl delete nimcache --all -n <NAMESPACE>
kubectl delete pvc --all -n <NAMESPACE>

# Delete secrets
kubectl delete secret ngc-api-key-secret ngc-nim-api-key-secret ngc-nim-docker-reg-secret
```
