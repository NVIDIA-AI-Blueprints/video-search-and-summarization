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

# Warehouse MV3DT App Helm Chart

This profile chart wraps `deploy/helm/services/infra` and `deploy/helm/services/rtvi`, enabling Kafka, Redis, shared-infra Mosquitto, MV3DT BEV fusion, and **`vss-rtvi-cv.profileMode`**=`standalone-mv3dt`.

```bash
helm dependency build deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
helm lint deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
helm template warehouse-mv3dt deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app
```

Override **`rtvi.vss-rtvi-cv.ngcAppDataResourceVersion`** and **`vios.vss-vios-nvstreamer.ngcVideoSeed.resourceVersion`** when using a different NGC warehouse app-data resource.

## Prerequisites

- **Kubernetes cluster** with `kubectl` configured to reach its API server.

- **NVIDIA GPU Operator** — installs the driver and device plugin so pods can request `nvidia.com/gpu`. Follow [GPU Operator getting started](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html). Recommended driver versions (x86):
  - **580.105.08** — Ubuntu 24.04
  - **580.65.06** — Ubuntu 22.04

- **Volume provisioner** — the chart creates PVCs for VST, Elasticsearch, and related storage. A StorageClass must exist on the cluster. Set **`global.storageClass`** to its name in your values override. On bare-metal clusters, [local-path-provisioner](https://github.com/rancher/local-path-provisioner) is a straightforward option:

  ```bash
  kubectl patch storageclass local-path \
    -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
  ```

  Replace `local-path` with your StorageClass name if it differs.

- **Helm 3.x** and **kubectl**

- **NGC API key** — required for the image pull secret and the NGC model/app-data download job. See [Required secrets](#required-secrets).

### GPU requirements

By default the profile requests **2 GPUs** — one for the CV pipeline and one for
hardware-accelerated video encode/decode in the stream processor.

| Workload | GPU | Notes |
|----------|-----|-------|
| `vss-rtvi-cv` | 1 | CV inference; always required |
| `vss-vios-streamprocessing` | 1 | HW encode/decode; see below |
| **Total** | **2** | |

To run `vss-vios-streamprocessing` in software encode/decode mode (FFmpeg CPU path)
and free that GPU for other workloads, set **`vios.vss-vios-streamprocessing.resources`**
to an empty map in your values override:

```yaml
vios:
  vss-vios-streamprocessing:
    useSoftwarePath: true
    resources: null
```

Or inline at install time:

```bash
--set vios.vss-vios-streamprocessing.useSoftwarePath=true \
--set 'vios.vss-vios-streamprocessing.resources=null'
```

Both flags are required together — **`useSoftwarePath`** switches the VST encode/decode
path in the config, and **`resources: null`** drops the GPU claim from the pod spec.
Setting only one leaves the stack misconfigured.

`resources: {}` does **not** work — Helm deep-merges maps, so the subchart default
keys survive an empty-map override. Use `null` to drop the block entirely.

Software mode reduces video throughput; use it only when a second GPU is not available.

### Required secrets

Create both secrets in the release namespace before installing. The chart references them by name from **`global.ngcApiSecret`** (`ngc-api`) and **`global.imagePullSecrets`** (`ngc-docker-reg-secret`).

```bash
export NAMESPACE='<NAMESPACE>'
export NGC_CLI_API_KEY='<your NGC API key>'

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic ngc-api \
  -n "$NAMESPACE" \
  --from-literal=NGC_CLI_API_KEY="$NGC_CLI_API_KEY"

kubectl create secret docker-registry ngc-docker-reg-secret \
  -n "$NAMESPACE" \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password="$NGC_CLI_API_KEY"
```

## Web UIs

**`global.vssIngress.enabled`** (off by default) renders one `Ingress` routing every UI
under a single host, matching the `vss-haproxy-ingress` service in the compose
profiles. The top-level **`vssIngress.*`** block holds config only (host, ports,
ingressClassName); it is not the enable gate.

**`global.externalHost`** drives all browser-reachable URLs (VST endpoint, analytics
address, incident links). **`vssIngress.host`** controls only the Ingress
`spec.rules[].host` for Kubernetes routing. Set both if they differ; omit
**`vssIngress.host`** to match any hostname.

### Prerequisite: HAProxy ingress controller

The controller is not in this repo and the chart does not install it. Install it
once per cluster:

```bash
helm repo add haproxytech https://haproxytech.github.io/helm-charts
helm repo update

helm upgrade --install haproxy-ingress haproxytech/kubernetes-ingress --version 1.49.0 \
  -n haproxy-controller --create-namespace \
  --set controller.kind=DaemonSet \
  --set controller.daemonset.useHostPort=true \
  --set controller.daemonset.hostPorts.http=80 \
  --set controller.daemonset.hostPorts.https=443 \
  --set controller.service.enabled=false \
  --set controller.ingressClass=haproxy
```

`useHostPort=true` binds node ports 80 (HTTP) and 443 (HTTPS) directly. A stock
install creates a LoadBalancer Service, which stays `Pending` on bare metal. Check with:

```bash
kubectl get ingressclass          # expect: haproxy
```

### Install

```bash
helm dependency update deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app

helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app \
  -n <namespace> --create-namespace \
  --set global.vssIngress.enabled=true \
  --set global.externalHost=<NODE_IP> \
  --set global.storageClass=<STORAGE_CLASS> \
  --set monitoring.grafana.rootUrl=http://<NODE_IP>/grafana \
  --set infra.kibana.kibanaPublicUrl=http://<NODE_IP>/kibana
```

**`global.storageClass`**, **`monitoring.grafana.rootUrl`**, and **`infra.kibana.kibanaPublicUrl`**
are host-specific. Grafana and Kibana build absolute links, so without them Grafana
points at `localhost` and Kibana at its in-cluster Service name. The rest works off
the defaults.

### Post-install validation

Wait for all pods to be ready:

```bash
kubectl get pods -n <namespace> -w
```

Then confirm the VST ingress responds:

```bash
kubectl port-forward -n <namespace> svc/vss-vios-ingress 30888:30888
curl -f http://127.0.0.1:30888/vst/api/health
```

### URLs

With `<NODE_IP>` being any cluster node:

| UI | URL |
| --- | --- |
| VST | `http://<NODE_IP>/vst/` |
| Kibana | `http://<NODE_IP>/kibana/` |
| NVStreamer | `http://<NODE_IP>/streamer/` |
| Grafana | `http://<NODE_IP>/grafana/` |
| Prometheus | `http://<NODE_IP>/prometheus/` |

`/storage/`, `/video-analytics-api/` and `/behavior-analytics/` are routed too.

Kibana, Grafana and Prometheus run under a path prefix set by
**`infra.kibana.basePath`**, **`monitoring.grafana.rootUrl`** and
**`monitoring.prometheus.routePrefix`**. Change an ingress path and the matching value
has to change too, or the app 404s after its first redirect.

### No ingress controller: NodePort

The bundled override puts the same UIs on node ports and skips the Ingress:

```bash
helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app \
  -n <namespace> --create-namespace \
  -f deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app/values-nodeport.yaml
```

| UI | URL |
| --- | --- |
| VST | `http://<NODE_IP>:30888/vst/` |
| NVStreamer | `http://<NODE_IP>:30900/` |
| Kibana | `http://<NODE_IP>:31560/` |
| Grafana | `http://<NODE_IP>:30300/` |
| Prometheus | `http://<NODE_IP>:30909/` |

It sets **`global.vssIngress.enabled`** to false and clears the path prefixes, since
each app then owns the root of its own port.

## Monitoring

Prometheus and Grafana come with the profile (**`monitoring.enabled`**, on by
default). Prometheus scrapes pods in the release namespace that carry
`prometheus.io/scrape`, container metrics from the kubelet, node metrics from the
node-exporter DaemonSet, and GPU metrics from the GPU operator's
`nvidia-dcgm-exporter`. Three dashboards are provisioned: containers, node, and
GPU.

`dcgmExporter` stays off because the GPU operator already runs one. Set
**`monitoring.enabled`**=`false` to skip the stack.

To reach a service directly:

```bash
kubectl port-forward -n <namespace> svc/grafana 3000:3000
```

## Upgrade and uninstall

**Upgrade**

```bash
helm upgrade wh deploy/helm/industry-profiles/warehouse-operations/warehouse-mv3dt-app \
  -n <namespace> -f <your-values-file>.yaml
```

**Uninstall**:

```bash
helm uninstall wh -n <namespace>
```

PVCs are not removed by `helm uninstall`; delete them manually if needed:

```bash
kubectl delete pvc --all -n <namespace>
```
