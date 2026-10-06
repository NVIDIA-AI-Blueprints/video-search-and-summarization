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

- **NVIDIA GPU Operator** — installs the driver and device plugin so pods can request `nvidia.com/gpu`. Follow [GPU Operator getting started](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html). Required driver version (x86 Ubuntu 24.04): **595.58.03**

- **Volume provisioner** — the chart creates PVCs for VST, Elasticsearch, and related storage. A StorageClass must exist on the cluster. Set **`global.storageClass`** to its name in your values override. On bare-metal clusters with no provisioner yet, install [local-path-provisioner](https://github.com/rancher/local-path-provisioner) via Helm:

  ```bash
  helm repo add containeroo https://charts.containeroo.ch
  helm repo update
  helm upgrade --namespace local-path-storage --create-namespace --install \
    local-path-provisioner-default containeroo/local-path-provisioner --version '0.0.32'
  ```

  Then, if `local-path` isn't already the default StorageClass:

  ```bash
  kubectl patch storageclass local-path \
    -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
  ```

  Replace `local-path` with your StorageClass name if it differs.

  `local-path` binds each PV to whichever node claims it first — on a multi-node cluster, a pod
  with several PVCs can end up unschedulable if they land on different nodes. Prefer a shared
  StorageClass on multi-node clusters instead, e.g.
  [nfs-subdir-external-provisioner](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner)
  (needs an existing NFS server):

  ```bash
  helm repo add nfs-subdir-external-provisioner https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/
  helm repo update
  helm upgrade --namespace nfs-provisioner --create-namespace --install \
    nfs-subdir-external-provisioner nfs-subdir-external-provisioner/nfs-subdir-external-provisioner \
    --set nfs.server=<NFS_SERVER_IP> \
    --set nfs.path=<NFS_EXPORT_PATH>
  ```

  Creates a StorageClass named `nfs-client` by default.

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

Keep hardware video processing enabled and allocate a GPU to VIOS streamprocessing.

#### Dropping a GPU claim

To disable a GPU allocation, explicitly set both `resources.limits.nvidia.com/gpu`
and `resources.requests.nvidia.com/gpu` to `0`. Setting `nvidia.com/gpu: null`,
`resources: null`, or `resources: {}` does not disable the allocation: Helm
coalesces the dependency chart's defaults back in, restoring the GPU count to `1`.

For VIOS streamprocessing, pair the zeroed GPU claim with `useSoftwarePath: true`
to use software encode/decode:

```yaml
vios:
  vss-vios-streamprocessing:
    useSoftwarePath: true
    resources:
      limits:
        nvidia.com/gpu: 0
      requests:
        nvidia.com/gpu: 0
```

Software mode reduces video throughput. Keep the CV inference GPU allocation enabled.

### GPU sharing

If there are not enough physical GPUs to assign one to each GPU workload, consider GPU sharing:

- [Multi-Instance GPU (MIG)](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-mig.html) partitions supported GPUs into instances with dedicated memory and fault isolation.
- [GPU time-slicing](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html) lets multiple workloads share a GPU without memory or fault isolation.

Choose based on your GPU hardware, workload compatibility, memory needs, and isolation requirements. See the [MIG and time-slicing comparison](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html#comparison-time-slicing-and-multi-instance-gpu). Keep hardware video processing enabled for VIOS and verify that the selected GPU or MIG profile supports the required video encode/decode capabilities.

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
| **`rtvi.vss-rtvi-cv.ngcAppDataResourceVersion`** | NGC resource version for the warehouse app-data bundle (models, configs, video seed). Default is `nvstaging/vss-warehouse/vss-warehouse-app-data:v3.3.0-09152026`; override when using a different release. |

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
| **`global.vios.messageBrokerConsumer`** | **`kafka`** | Live metadata broker VST/VIOS listens on for overlay bounding boxes. Chart default is `redis`; this profile overrides it since perception publishes to Kafka. Shared by `vss-vios-sensor` and `vss-vios-streamprocessing`. |
| **`global.vios.messageBrokerTopicConsumer`** | **`mdx-bev`** | Topic VIOS consumes for live overlay metadata. |
| **`global.vios.messageBrokerMetadataTopic`** | **`mdx-bev`** | Same topic, used by the notification/webhook side of the same config. |
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
| **`vios.vss-vios-streamprocessing.resources`** | `nvidia.com/gpu: 1` | Keep one GPU allocation for hardware video processing. For software mode, set both GPU limits and requests to `0` with `useSoftwarePath: true`; `null` restores the default GPU count. See [GPU requirements](#gpu-requirements). |
| **`vios.vss-vios-nvstreamer.syncFileCount`** | **`4`** | Number of sample video files NVStreamer syncs. Keep in step with `bp-configurator` `NUM_STREAMS`. |
| **`vios.vss-vios-nvstreamer.ngcVideoSeed.resourceVersion`** | **`nvstaging/vss-warehouse/vss-warehouse-app-data:v3.3.0-09152026`** | NGC resource for the NVStreamer sample video seed. Keep in step with **`rtvi.vss-rtvi-cv.ngcAppDataResourceVersion`**. |
| **`vios.vss-vios-nvstreamer.ngcVideoSeed.fromExistingClaim`** | **`vss-rtvi-cv-models`** | Reuses the PVC from the `vss-rtvi-cv` NGC download job so the video data is not downloaded twice. Clear this and set **`resourceVersion`** to download the video seed independently. |
| **`vios.vss-vios-sensor.videoMetadataServerUrl`** | **`""`** (derived: `<elasticsearch-svc>:9200/mdx-raw*`) | VST overlay metadata source. Derived from the in-cluster `elasticsearch` Service; override for a non-standard endpoint. No `http://` scheme — VST rejects one. |
| **`vios.vss-vios-streamprocessing.videoMetadataServerUrl`** | **`""`** (derived: `<elasticsearch-svc>:9200/mdx-raw*`) | Same as above, for streamprocessing. Prefer **`videoMetadataIndexPattern`** below — this bypasses release-name-prefix awareness. |
| **`vios.vss-vios-streamprocessing.videoMetadataIndexPattern`** | **`mdx-bev`\*** | Overlay index pattern, prefix-aware. This profile fuses detections into BEV metadata, so it overrides the chart's `mdx-raw*` default. |
| **`vios.vss-vios-streamprocessing.overlaySensorName`** | **`bev-sensor-1`** | Associates BEV overlay metadata with camera streams. Must match `group.name` in `calibration.json`. |
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
| **`rtvi.vss-rtvi-cv.ngcAppDataResourceVersion`** | **`nvstaging/vss-warehouse/vss-warehouse-app-data:v3.3.0-09152026`** | NGC resource version for the warehouse app-data bundle (models, configs). Override when pinning to a specific release. |
| **`rtvi.vss-rtvi-cv.persistence.models.size`** | **`80Gi`** | PVC size for the NGC model download job. |
| **`rtvi.vss-rtvi-cv.sparse4d.numTorchThreads`** | **`8`** | PyTorch CPU thread count for Sparse4D. Scale with the CPU request/limit below if you observe contention. |
| **`rtvi.vss-rtvi-cv.resources`** | `nvidia.com/gpu: 1`; `cpu: 8` request, `12` limit | GPU request/limit for the CV inference pod. CPU request should be ≥ **`sparse4d.numTorchThreads`** above; the `12` limit is a buffer for the rest of the DeepStream pipeline. |
| **`warehouse.datasetType`** | **`synthetic`** | `synthetic` or `real`. Picks the Sparse4D model/anchor/label set (same as compose's `DATASET_TYPE`). Don't hand-edit `rtvi.vss-rtvi-cv.sparse4d`/`ngcModelsToDownload` — run `scripts/compute_model_selection.py --dataset-type <type> -f <your-values.yaml> -o values-model.yaml` and pass `-f values-model.yaml` to `helm upgrade --install`. |

##### `monitoring`

| Key | Default | Description |
|-----|---------|-------------|
| **`monitoring.enabled`** | **`true`** | Master switch for Prometheus and Grafana. Set **`false`** to skip the stack. |
| **`monitoring.prometheus.routePrefix`** | **`/prometheus`** | Prometheus route prefix matching the `/prometheus` ingress path. Change only if the ingress path changes. |
| **`monitoring.grafana.rootUrl`** | **`http://localhost:8080/grafana`** | Full external URL for Grafana including the path prefix. Set to `http://<NODE_IP>/grafana` so redirect links resolve correctly. |
| **`monitoring.nodeExporter.enabled`** | **`true`** | Enable the node exporter DaemonSet for host-level metrics. |
| **`monitoring.dcgmExporter.enabled`** | **`false`** | Stays off because the GPU Operator already runs `nvidia-dcgm-exporter`. Enable only on clusters without the GPU Operator. |

##### `global.cameraInfo`

| Key | Default | Description |
|-----|---------|-------------|
| **`global.cameraInfo.enabled`** | **`false`** | Enable live RTSP camera registration. Also flips `bp-configurator`'s `SENSOR_INFO_SOURCE` env entry to `file` automatically, so no other setting is needed. |
| **`global.cameraInfo.sensors`** | **`[]`** | List of RTSP camera entries: `camera_name`, `rtsp_url`, `group_id`, `region`. For a handful of cameras. |
| **`global.cameraInfo.sensorsFile`** | **`""`** | Raw JSON content (strict JSON, no comments). Takes priority over `sensors` when set. Copy `../camera_configs/camera_info.example.json` somewhere outside the repo, fill in real cameras, and point `--set-file` at that path. Fails the render if the JSON is invalid or missing a `sensors` key. |

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

GIT_REF=$(git describe --tags --exact-match 2>/dev/null || git rev-parse --abbrev-ref HEAD)

helm upgrade --install wh deploy/helm/industry-profiles/warehouse-operations/warehouse-3d-app \
  -n <namespace> --create-namespace \
  --set global.vssIngress.enabled=true \
  --set global.externalHost=<NODE_IP> \
  --set global.storageClass=<STORAGE_CLASS> \
  --set global.gitRef=$GIT_REF \
  --set monitoring.grafana.rootUrl=http://<NODE_IP>/grafana \
  --set infra.kibana.kibanaPublicUrl=http://<NODE_IP>/kibana
```

**`global.storageClass`**, **`monitoring.grafana.rootUrl`**, and **`infra.kibana.kibanaPublicUrl`**
are host-specific. Grafana and Kibana build absolute links, so without them Grafana
points at `localhost` and Kibana at its in-cluster Service name. The rest works off
the defaults.

**`global.gitRef`** picks the branch/tag the calibration-import source links
(`calibrationFileSource`, `imageMetadataFileSource`, `imageBaseSource`) point at.
`GIT_REF` above resolves to the tag when installing from a tagged checkout, or the
branch name otherwise; omit `--set global.gitRef=...` to default to `develop`.

**`global.sampleVideoDataset`** picks the dataset directory under
`calibration/sample-data/` those same three links point at. Default is
`warehouse-4cams-20mx20m-synthetic`.

**`analytics.vss-behavior-analytics.resourceFiles.calibration.apiUrl`** and
**`resourceFiles.calibration.enabled`** (both default to a live API URL /
`true`) together control calibration:

- **Default** — fetches `calibration.json` from `apiUrl` via an initContainer
  before the app starts.
- **Clear `apiUrl`** — skips the fetch, falls back to the bundled
  `files/behavior-analytics/calibration.json`.
- **Set `enabled: false`** — skips calibration entirely (no initContainer, no
  fallback). Not viable for 3D: `SpaceAnalyzer` and the global-ROI transform
  need real calibration geometry to produce correct output.

#### Using a custom dataset

Video source — pick one; they're mutually exclusive, don't configure both:

1. **Recorded video files**, not live cameras: point
   **`vios.vss-vios-nvstreamer.persistence.streamerVideos.hostPath`** (or an
   equivalent PVC binding) at the video files, and set
   **`vios.vss-vios-nvstreamer.ngcVideoSeed.enabled=false`** so the chart
   doesn't also seed sample videos into that volume. bp-configurator's default
   **`SENSOR_INFO_SOURCE=nvstreamer`** auto-discovers sensors from what
   NVStreamer is serving — leave `global.cameraInfo` unset for this path. Set
   **`vios.vss-vios-nvstreamer.syncFileCount`** to the effective stream count
   from **Stream count** below, not the raw file count — set higher than the
   stream cap, sync stalls instead of serving media.

2. **Live RTSP streams**: set **`global.cameraInfo.enabled=true`**, which
   flips bp-configurator to `SENSOR_INFO_SOURCE=file`. Add each camera under
   **`global.cameraInfo.sensors`** — required: `camera_name`, `rtsp_url`;
   optional: `group_id`, `region`. For more than a handful, use
   **`global.cameraInfo.sensorsFile`** instead (raw JSON, takes priority over
   `sensors` — copy `../camera_configs/camera_info.example.json` outside the
   repo, fill in real cameras, and pass it with `--set-file`). Each
   `rtsp_url` must be reachable from the cluster — VIOS connects to it
   directly; test with VLC or `ffplay` from the deployment machine before
   deploying.

Calibration data has to be supplied either way:

| Setting | Set | Effect |
|---|---|---|
| `calibration-import.calibrationFileSource` | your `calibration.json` URL | Replaces the bundled sample calibration. |
| `calibration-import.imageMetadataFileSource` | your `imageMetadata.json` URL | Must resolve to a file with an `images[]` array, each entry carrying a `fileName`. |
| `calibration-import.imageBaseSource` | base URL for your floor-plan images | Base URL each `fileName` above is fetched from. |
| `calibration-import.requireCalibration` / `requireImages` | keep default `true` | A broken URL fails the Job instead of deploying with no calibration. |

Each `camera_name` registered above must match the corresponding sensor name
in `calibration.json` — the importer doesn't check this for you. This
repoints what's uploaded to the video analytics API only — the 3D perception
pod reads its own copy from
`deploy/helm/services/rtvi/charts/rtvi-cv/files/warehouse-standalone-3d/calibration/calibration.json`;
replace that file too (before `helm dependency update`) so perception matches.

Also configure, outside `global`:

- **`warehouse.datasetType`** — set to `real`, then regenerate the
  model-selection values file (see the `warehouse.datasetType` row above).
  Left at the default `synthetic`, real footage still runs against the
  synthetic Sparse4D model/anchor/label set: no error, just wrong results.
- **Stream count** — set `<N>` to the number of cameras/streams for whichever
  video source you picked above (sensors under `global.cameraInfo.sensors`/
  `sensorsFile` for RTSP, or the number of video files for the recorded-video
  path), by running
  `python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py --mode 3d --num-streams <N>`
  (see [Scaling: NUM_STREAMS by GPU](#scaling-num_streams-by-gpu)) and
  layering the generated file in. Left at the default 4, sensors past the 4th
  are dropped silently.

`global.gitRef`, `global.sampleVideoDataset`, and (by default)
`vios.vss-vios-nvstreamer.ngcVideoSeed.dataset` only matter for the bundled
sample dataset — irrelevant once the video and calibration sources above are
overridden.

### 4. Post-install validation

Wait for all pods to be ready:

```bash
kubectl get pods -n <namespace> -w
```

Then confirm the VST ingress responds:

```bash
kubectl port-forward -n <namespace> svc/vss-vios-ingress 30888:30888
curl -f http://127.0.0.1:30888/health
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

### Port-forward

No ingress, no NodePort:

```bash
kubectl port-forward -n <namespace> svc/vss-vios-ingress 30888:30888
kubectl port-forward -n <namespace> svc/kibana 5601:5601
kubectl port-forward -n <namespace> svc/grafana 3000:3000
kubectl port-forward -n <namespace> svc/prometheus 9090:9090
```

| UI | URL |
| --- | --- |
| VST | `http://localhost:30888/vst/` |
| Kibana | `http://localhost:5601` |
| Grafana | `http://localhost:3000` |
| Prometheus | `http://localhost:9090` |

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

## Scaling: NUM_STREAMS by GPU

The chart ships a fixed `NUM_STREAMS=4` in `bp-configurator.env` with no GPU cap —
unlike Docker Compose, which caps it automatically per `HARDWARE_PROFILE`. Before an initial
install or an upgrade where you want streams sized to your hardware, generate a values-override:

```bash
python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
  --mode 3d --num-streams <N> -o values-stream-cap.generated.yaml
```

Layer `-f values-stream-cap.generated.yaml` into `helm upgrade --install`, and set
`vios.vss-vios-nvstreamer.syncFileCount` to the effective count it prints. See
[`skills/deployment/vss-deploy-warehouse-helm/references/streams.md`](../../../../../skills/deployment/vss-deploy-warehouse-helm/references/streams.md)
for the full command and GPU→cap table. No skill/agent required — the script runs standalone.

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
