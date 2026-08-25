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

# Warehouse 3D App Helm Chart

This profile chart wraps `deploy/helm/services/rtvi` and enables **`vss-rtvi-cv.profileMode`**=`standalone-3d`.

```bash
helm dependency build deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app
helm lint deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app
helm template warehouse-3d deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app
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

- **TURN server** — required for WebRTC playback in the VST/VIOS web UI whenever the browser isn't on the same network as the cluster. See [TURN server prerequisite](../TURN-SERVER.md) and set **`global.turnServerUrl`** in your values override.

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

### 1. Prepare the values file

Create a values override file (e.g. `my-values.yaml`) and set at least:

| Key | Description |
|-----|-------------|
| **`global.storageClass`** | StorageClass for VST, Elasticsearch, and related PVCs (e.g. **`local-path`**, **`oci-bv-high`**). Must exist on the cluster before install. |
| **`global.externalHost`** | Node IP or hostname browsers use to reach the UIs (e.g. `192.168.1.10`). Drives all browser-reachable URLs. |
| **`global.vssIngress.enabled`** | Set **`true`** to create the HAProxy `Ingress`. Requires the controller installed in [step 2](#2-install-the-ingress-controller). Leave **`false`** and use `values-nodeport.yaml` instead for NodePort access. |
| **`monitoring.grafana.rootUrl`** | Full external URL for Grafana including path prefix, e.g. `http://<NODE_IP>/grafana`. Grafana embeds this in redirect links; without it Grafana points at `localhost`. |
| **`infra.kibana.kibanaPublicUrl`** | Full external URL for Kibana including path prefix, e.g. `http://<NODE_IP>/kibana`. Kibana uses this for absolute links in the UI. |
| **`rtvi.vss-rtvi-cv.ngcAppDataResourceVersion`** | NGC resource version for the warehouse app-data bundle (models, configs, video seed). Default is `nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0`; override when using a different release. |

#### `values.yaml` vs your override file

| File | Role |
|------|------|
| **`values.yaml`** | Chart defaults shipped with the profile. Do not edit it directly; override only the keys you need. |
| **`my-values.yaml`** (your file) | Your site-specific overrides. Pass with `-f my-values.yaml` at install time. |

#### Optional overrides — `values.yaml` keys (reference)

Order follows `values.yaml`. Set only the keys you need in your override file; Helm merges it on top of the chart defaults.

##### `global`

| Key | Default | Description |
|-----|---------|-------------|
| **`global.externalScheme`** | **`""`** | `http` or `https`. Builds browser-facing URLs together with **`global.externalHost`** and **`global.externalPort`**. |
| **`global.externalPort`** | **`""`** | Port segment in generated URLs. Leave empty so URLs omit `:port` when using standard 80/443. Set only for non-standard ports. |
| **`global.useReleaseNamePrefix`** | **`false`** | When `true`, all in-cluster service names are prefixed with the Helm release name. |
| **`global.ngcApiSecret.name`** | **`ngc-api`** | Name of the Opaque secret holding the NGC API key (see [Required secrets](#required-secrets)). |
| **`global.ngcApiSecret.key`** | **`NGC_CLI_API_KEY`** | Key inside the secret that holds the NGC API key value. |
| **`global.imagePullSecrets`** | **`[{name: ngc-docker-reg-secret}]`** | Image pull credentials for nvcr.io. Must reference the docker-registry secret created in [Required secrets](#required-secrets). |

##### `vios`

| Key | Default | Description |
|-----|---------|-------------|
| **`vios.vstStorage.createSharedPvcs`** | **`true`** | Creates shared PVCs so sensor and streamprocessing pods mount the same VST data and video directories. Set `false` only if managing PVCs externally. |
| **`vios.vstStorage.accessMode`** | **`ReadWriteOnce`** | Access mode for the three shared VST PVCs. |
| **`vios.vstStorage.vstData.size`** | **`10Gi`** | PVC size for shared VST data volume. |
| **`vios.vstStorage.vstVideo.size`** | **`20Gi`** | PVC size for shared VST video volume. |
| **`vios.vstStorage.streamerVideos.size`** | **`20Gi`** | PVC size for the NVStreamer upload volume. |
| **`vios.vss-vios-streamprocessing.useSoftwarePath`** | **`false`** | Set **`true`** (paired with **`resources: null`**) to use FFmpeg software encode/decode and free the second GPU. Both flags required — see [GPU requirements](#gpu-requirements). |
| **`vios.vss-vios-streamprocessing.resources`** | `nvidia.com/gpu: 1` | Pod resource requests/limits for streamprocessing. Set **`null`** (with **`useSoftwarePath: true`**) to drop the GPU claim entirely. |
| **`vios.vss-vios-nvstreamer.syncFileCount`** | **`4`** | Number of sample video files NVStreamer syncs. Keep in step with `bp-configurator` `NUM_STREAMS`. |
| **`vios.vss-vios-nvstreamer.ngcVideoSeed.resourceVersion`** | **`nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0`** | NGC resource for the NVStreamer sample video seed. Keep in step with **`rtvi.vss-rtvi-cv.ngcAppDataResourceVersion`**. |
| **`vios.vss-vios-nvstreamer.ngcVideoSeed.fromExistingClaim`** | **`vss-rtvi-cv-models`** | Reuses the PVC from the `vss-rtvi-cv` NGC download job so the video data is not downloaded twice. Clear this and set **`resourceVersion`** to download the video seed independently. |
| **`vios.vss-vios-sensor.videoMetadataServerUrl`** | **`""`** (derived: `<elasticsearch-svc>:9200/mdx-raw*`) | VST overlay metadata source. Derived from the in-cluster `elasticsearch` Service; override for a non-standard endpoint. No `http://` scheme — VST rejects one. |
| **`vios.vss-vios-streamprocessing.videoMetadataServerUrl`** | **`""`** (derived: `<elasticsearch-svc>:9200/mdx-raw*`) | Same as above, for streamprocessing. |
| **`vios.vss-vios-nvstreamer.videoMetadataServerUrl`** | **`""`** (derived: `http://<elasticsearch-svc>:9200/mdx-raw*`) | NVStreamer's overlay metadata source. Requires the `http://` scheme, unlike the two rows above. |

##### `infra`

| Key | Default | Description |
|-----|---------|-------------|
| **`infra.redis.persistence.size`** | **`5Gi`** | PVC size for Redis. |
| **`infra.elasticsearch.persistence.data.size`** | **`10Gi`** | PVC size for Elasticsearch data. |
| **`infra.elasticsearch.persistence.logs.size`** | **`5Gi`** | PVC size for Elasticsearch logs. |
| **`infra.elasticsearch.persistence.storageClass`** | **`""`** | StorageClass for Elasticsearch PVCs; inherits **`global.storageClass`** when empty. |
| **`infra.elasticsearch.init.env.ELASTICSEARCH_ILM_MIN_AGE`** | **`4h`** | ILM policy minimum age before Elasticsearch rolls over an index. |
| **`infra.kibana.basePath`** | **`/kibana`** | Kibana base path matching the `/kibana` ingress route. Change only if the ingress path changes. |
| **`infra.kafka.persistence.size`** | **`50Gi`** | PVC size for Kafka. |
| **`infra.phoenix.enabled`** | **`false`** | Enable Phoenix observability (pipeline traces and spans). Off by default in this profile. |

##### `analytics`

| Key | Default | Description |
|-----|---------|-------------|
| **`analytics.vss-video-analytics-api.storage.size`** | **`5Gi`** | PVC size for the video analytics API service. |

##### `rtvi`

| Key | Default | Description |
|-----|---------|-------------|
| **`rtvi.vss-rtvi-cv.ngcAppDataResourceVersion`** | **`nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0`** | NGC resource version for the warehouse app-data bundle (models, configs). Override when pinning to a specific release. |
| **`rtvi.vss-rtvi-cv.persistence.models.size`** | **`80Gi`** | PVC size for the NGC model download job. |
| **`rtvi.vss-rtvi-cv.resources`** | `nvidia.com/gpu: 1` | GPU request/limit for the CV inference pod. Always required for the 3D pipeline. |

##### `monitoring`

| Key | Default | Description |
|-----|---------|-------------|
| **`monitoring.enabled`** | **`true`** | Master switch for Prometheus and Grafana. Set **`false`** to skip the stack. |
| **`monitoring.prometheus.routePrefix`** | **`/prometheus`** | Prometheus route prefix matching the `/prometheus` ingress path. Change only if the ingress path changes. |
| **`monitoring.grafana.rootUrl`** | **`http://localhost:8080/grafana`** | Full external URL for Grafana including the path prefix. Set to `http://<NODE_IP>/grafana` so redirect links resolve correctly. |
| **`monitoring.nodeExporter.enabled`** | **`true`** | Enable the node exporter DaemonSet for host-level metrics. |
| **`monitoring.dcgmExporter.enabled`** | **`false`** | Stays off because the GPU Operator already runs `nvidia-dcgm-exporter`. Enable only on clusters without the GPU Operator. |

##### `cameraInfo`

| Key | Default | Description |
|-----|---------|-------------|
| **`cameraInfo.enabled`** | **`false`** | Enable live RTSP camera registration. When `true`, also set `bp-configurator` env `SENSOR_INFO_SOURCE=file` so the configurator reads the sensor list from this ConfigMap rather than discovering NVStreamer files. |
| **`cameraInfo.sensors`** | **`[]`** | List of RTSP camera entries. Each entry takes `camera_name`, `rtsp_url`, `group_id`, and `region`. |

##### `vssIngress`

| Key | Default | Description |
|-----|---------|-------------|
| **`vssIngress.ingressClassName`** | **`haproxy`** | IngressClass name. Must match the controller installed on the cluster. |
| **`vssIngress.host`** | **`""`** | Hostname for Ingress routing rules. If empty, **`global.externalHost`** is used. |
| **`vssIngress.vstPort`** | **`30888`** | Backend Service port for the VST ingress. |
| **`vssIngress.kibanaPort`** | **`5601`** | Backend Service port for Kibana. |
| **`vssIngress.grafanaPort`** | **`3000`** | Backend Service port for Grafana. |
| **`vssIngress.prometheusPort`** | **`9090`** | Backend Service port for Prometheus. |
| **`vssIngress.nvstreamerPort`** | **`31000`** | Backend Service port for NVStreamer. |
| **`vssIngress.videoAnalyticsApiPort`** | **`8081`** | Backend Service port for the video analytics API. |
| **`vssIngress.behaviorAnalyticsPort`** | **`8080`** | Backend Service port for the behavior analytics service. |

##### `calibration-import`

| Key | Default | Description |
|-----|---------|-------------|
| **`calibration-import.enabled`** | **`true`** | Runs a one-shot Job that uploads the sample calibration file and floor-plan images to the video analytics API at startup. Set **`false`** to skip and provide calibration data manually. |
| **`calibration-import.calibrationFileSource`** | (bundle URL) | Source URL for the sample calibration JSON. Override to point at custom calibration data. |

### 2. Install the ingress controller

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

### 3. Install

```bash
helm dependency update deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app

helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app \
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

### 4. Post-install validation

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
helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app \
  -n <namespace> --create-namespace \
  -f deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app/values-nodeport.yaml
```

| UI | URL |
| --- | --- |
| VST | `http://<NODE_IP>:30888/vst/` |
| NVStreamer | `http://<NODE_IP>:30900/` |
| Kibana | `http://<NODE_IP>:31560/` |
| Grafana | `http://<NODE_IP>:30300/` |
| Prometheus | `http://<NODE_IP>:30909/` |

It sets **`global.vssIngress.enabled`** to false and clears
the path prefixes, since each app then owns the root of its own port.

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
helm upgrade wh deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app \
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
