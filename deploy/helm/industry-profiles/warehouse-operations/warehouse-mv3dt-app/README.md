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

The profile makes **3 GPU claims**, which fit on **2 physical GPUs** — the
recommended configuration.

| Workload | Claim | Notes |
|----------|-------|-------|
| `vss-rtvi-cv` | 1 | CV inference; always required |
| `vss-vios-streamprocessing` | 1 | HW encode/decode; see below |
| `vss-reid-embed` | 1 | Appearance embeddings; see [ReID](#appearance-reid) |
| **Total** | **3 claims** | on **2** physical GPUs with time-slicing |

A claim is not the same as a card. `nvidia.com/gpu: 1` is an *exclusive integer
claim*, so unlike Compose — where the tracker and ReID both just use GPU 0 —
two pods cannot land on one physical GPU unless the device plugin advertises it
as shareable. Enable [time-slicing](#gpu-time-slicing-limited-gpu-environments)
and the CV pipeline and ReID service share a card, exactly as they do under
Compose.

Without sharing, the three claims need three physical GPUs. If you have only two
and would rather not configure the device plugin, disable the ReID service's
secondary embedding — it is the service's only GPU workload, so the claim can
then go to zero and CV and the stream processor keep a card each:

```yaml
rtvi:
  vss-reid-embed:
    secondaryEmbedding:
      enabled: false
    resources:
      limits:
        nvidia.com/gpu: 0
      requests:
        nvidia.com/gpu: 0
```

Appearance re-association still works — the embeddings driving it are extracted
by the tracker on the CV GPU, and the service only stores and compares them.
What you give up is the `mdx-compressed-embeddings` output: SigLIP2 embeddings
are published by the secondary embedding worker and nothing else writes that
topic, so it stays empty, along with its Elasticsearch index and Kibana pattern.

Set both keys together. Zeroing the GPU claim while leaving secondary embedding
enabled fails in the worst way — CUDA initialisation fails inside the worker, and
the service reports `/health/ready` as **503 indefinitely** rather than
crashing, so the pod never goes ready and DeepStream waits behind it. The chart
rejects that combination at render time rather than letting you deploy it.

This covers steady state, not the **first install**: the staging Job still needs
a GPU to export the CLIP-ReID ONNX, which the tracker requires whether or not
secondary embedding is on. With both cards held by CV and the stream processor,
that Job has nowhere to run and the CV pod waits behind it. For the first
install either free a card briefly (the CPU path below is the easiest way), or
stage the models out of band and set `rtvi.vss-reid-embed.init.enabled=false`.

The `vss-reid-embed-init` Job makes a fourth claim **transiently on first
install**, to export the CLIP-ReID ONNX on device. With time-slicing enabled it
is absorbed like the others; without it, see [ReID](#appearance-reid) for why it
can stall on a fully-committed cluster.

To run `vss-vios-streamprocessing` in software encode/decode mode (FFmpeg CPU path)
and free that GPU for other workloads, switch the path and zero its GPU claim:

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

Or inline at install time:

```bash
--set vios.vss-vios-streamprocessing.useSoftwarePath=true \
--set 'vios.vss-vios-streamprocessing.resources.limits.nvidia\.com/gpu=0' \
--set 'vios.vss-vios-streamprocessing.resources.requests.nvidia\.com/gpu=0'
```

Both parts are required together — **`useSoftwarePath`** switches the VST
encode/decode path in the config, and the zeroed claim releases the GPU. Setting
only one leaves the stack misconfigured.

#### Dropping a GPU claim

Setting the count to `0` is the way to release a GPU. Neither `resources: {}` nor
`resources: null` works, whether passed with `-f` or `--set`: Helm coalesces the
**subchart's own** `values.yaml` defaults back in after your override is applied,
so `nvidia.com/gpu: 1` reappears. Only overriding the value itself sticks.

Software mode reduces video throughput; use it only when an additional GPU is not
available.

### GPU time-slicing (limited GPU environments)

Time-slicing lets several pods share one physical GPU, which is how this profile
fits its 3 claims onto 2 cards. For setup instructions, refer to
[Time-Slicing GPUs in Kubernetes](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html).

A 2-replica configuration is enough here — a 2-GPU node then advertises 4
`nvidia.com/gpu`, absorbing the 3 steady-state claims plus the staging Job:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: time-slicing-config
  namespace: gpu-operator
data:
  any: |-
    version: v1
    flags:
      migStrategy: none
    sharing:
      timeSlicing:
        renameByDefault: false
        failRequestsGreaterThanOne: false
        resources:
          - name: nvidia.com/gpu
            replicas: 2
```

Point the device plugin at it:

```bash
kubectl patch clusterpolicies.nvidia.com/cluster-policy --type=merge \
  -p '{"spec":{"devicePlugin":{"config":{"name":"time-slicing-config","default":"any"}}}}'
```

**No chart changes are needed** with `renameByDefault: false`, because each slice
is advertised as an ordinary `nvidia.com/gpu` and the requests in this profile
already ask for one each. This is the configuration to prefer.

If your cluster sets `renameByDefault: true`, slices are advertised as
`nvidia.com/gpu.shared` and **every** claim must be renamed — a workload left
asking for `nvidia.com/gpu` will not schedule at all, since no such resource is
advertised any more. Set each exclusive count to `0` and add the shared one; see
[dropping a GPU claim](#dropping-a-gpu-claim) for why the count goes to zero
rather than being removed:

```yaml
rtvi:
  vss-rtvi-cv:
    resources:
      limits: {nvidia.com/gpu: 0, nvidia.com/gpu.shared: 1}
      requests: {nvidia.com/gpu: 0, nvidia.com/gpu.shared: 1}
  vss-reid-embed:
    resources:
      limits: {nvidia.com/gpu: 0, nvidia.com/gpu.shared: 1}
      requests: {nvidia.com/gpu: 0, nvidia.com/gpu.shared: 1}
    # The staging Job claims separately from the service.
    init:
      resources:
        limits: {nvidia.com/gpu: 0, nvidia.com/gpu.shared: 1}
        requests: {nvidia.com/gpu: 0, nvidia.com/gpu.shared: 1}
vios:
  vss-vios-streamprocessing:
    resources:
      limits: {nvidia.com/gpu: 0, nvidia.com/gpu.shared: 1}
      requests: {nvidia.com/gpu: 0, nvidia.com/gpu.shared: 1}
```

Time-slicing does not isolate GPU memory: the pods sharing a card must fit in it
together. That is the same bargain Compose makes by pointing the tracker and the
ReID service at GPU 0, so the working set is known to fit — but it is worth
remembering if you raise `batchSize` or the stream count. MPS is configured the
same way and gives better isolation at the cost of a more complex setup.

### Appearance ReID

The profile enables appearance-based re-identification. The DeepStream tracker
queries a ReID service for embeddings and uses them to re-associate objects that
tracking alone would lose, and the service republishes compressed embeddings on
`mdx-compressed-embeddings` for downstream search.

| Component | Role |
|-----------|------|
| `vss-reid-embed` | Embedding service the tracker queries; consumes `mdx-raw`, produces `mdx-compressed-embeddings` |
| `vss-reid-milvus` | Vector store for the appearance gallery |
| `vss-reid-etcd`, `vss-reid-minio` | Milvus metadata and object storage |
| `vss-reid-embed-init` | One-shot Job that stages the SigLIP2 and CLIP-ReID models |

The three backends use `emptyDir`, matching Compose: the gallery is rebuilt from
the live stream, so it is intentionally not persisted across restarts.

**Two settings must agree.** `rtvi.vss-reid-embed.enabled` deploys the service,
and `rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.reid.enabled` is what appends the
`ReID`/`ReIDService` blocks to the tracker config and starts the perception app
with `--tracker-reid`. Enabling only the first wastes a GPU; enabling only the
second leaves the tracker querying an address that does not exist. To turn ReID
off entirely:

```bash
--set rtvi.vss-reid-embed.enabled=false \
--set rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.reid.enabled=false
```

`serviceAddress` is derived from the `vss-reid-embed` Service name, so it stays
correct under `global.useReleaseNamePrefix`. Set it only to point the tracker at
a ReID service outside the release.

#### Model staging and first install

`vss-reid-embed-init` downloads SigLIP2 from NGC and exports the CLIP-ReID ONNX
onto the **`vss-rtvi-cv` models claim**, because the tracker loads
`reid_model.onnx` from its own `/opt/storage` mount. Both the ReID service and
the CV pod wait for the marker the Job writes, so they cannot start against a
half-populated directory. It needs the same `ngc-api` secret as the other model
downloads.

Two consequences worth planning for:

- The claim is `ReadWriteOnce`, so the CV pod, the ReID service and the Job all
  land on **one node**. Use `ReadWriteMany` if you need them spread.
- The Job needs a GPU (the ONNX export runs on device). The CV pod is scheduled
  and holding its own GPU while waiting for the Job's marker, so on a cluster
  whose GPUs are all exclusively claimed the two wait on each other until the
  timeout. With [time-slicing](#gpu-time-slicing-limited-gpu-environments)
  enabled this cannot happen, since the Job's claim is satisfied by a slice of
  an already-busy card. Otherwise leave one GPU free for the first install, or
  stage the models out of band as described below.

Readiness is signalled by a marker file, `.reid-models-ready`, in the model
directory. If you pre-stage the models yourself and set
`rtvi.vss-reid-embed.init.enabled=false`, create that marker too — otherwise the
CV pod waits for a file nothing will write and times out. Alternatively set
`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.reid.waitForModels=false` to drop the
gate, on the understanding that DeepStream will then fail outright if the model
is missing rather than waiting for it.

The ReID service claims a whole GPU by default. To co-locate it with the CV
pipeline on one card — the Compose arrangement — see
[GPU time-slicing](#gpu-time-slicing-limited-gpu-environments).

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
| **`infra.mosquitto.enabled`** | **`true`** | MQTT broker used by the MV3DT pipeline for inter-service messaging. Disable only if providing an external broker. |
| **`infra.phoenix.enabled`** | **`false`** | Phoenix observability is off by default in this profile. Set **`true`** to enable pipeline traces and spans. |
| **`infra.redis.persistence.size`** | **`5Gi`** | PVC size for Redis. |
| **`infra.elasticsearch.persistence.data.size`** | **`10Gi`** | PVC size for Elasticsearch data. |
| **`infra.elasticsearch.persistence.logs.size`** | **`5Gi`** | PVC size for Elasticsearch logs. |
| **`infra.elasticsearch.persistence.storageClass`** | **`""`** | StorageClass for Elasticsearch PVCs; inherits **`global.storageClass`** when empty. |
| **`infra.elasticsearch.init.env.ELASTICSEARCH_ILM_MIN_AGE`** | **`4h`** | ILM policy minimum age before Elasticsearch rolls over an index. |
| **`infra.kibana.basePath`** | **`/kibana`** | Kibana base path matching the `/kibana` ingress route. Change only if the ingress path changes. |
| **`infra.kafka.persistence.size`** | **`50Gi`** | PVC size for Kafka. |

##### `analytics`

| Key | Default | Description |
|-----|---------|-------------|
| **`analytics.vss-video-analytics-api.storage.size`** | **`5Gi`** | PVC size for the video analytics API service. |

##### `rtvi`

| Key | Default | Description |
|-----|---------|-------------|
| **`rtvi.vss-rtvi-cv.ngcAppDataResourceVersion`** | **`nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0`** | NGC resource version for the warehouse app-data bundle (models, configs). Override when pinning to a specific release. |
| **`rtvi.vss-rtvi-cv.persistence.models.size`** | **`100Gi`** | PVC size for the NGC model download job. Larger than 2D/3D because MV3DT downloads two models. |
| **`rtvi.vss-rtvi-cv.resources`** | `nvidia.com/gpu: 1` | GPU request/limit for the CV inference pod. Always required for the MV3DT pipeline. |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.batchSize`** | **`4`** | Number of camera frames batched per BEV fusion pass. |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.maxBatchSize`** | **`4`** | Maximum batch size cap for BEV fusion. |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.sensorTimeoutMs`** | **`100`** | Timeout in ms waiting on a sensor frame before proceeding with a partial batch. |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.maxExpectedSensors`** | **`4`** | Number of cameras the BEV fusion expects. Keep in step with NVStreamer **`syncFileCount`**. |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.fusion.rawTopic`** | **`mdx-raw`** | Kafka topic for per-camera detection messages fed into BEV fusion. |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.fusion.fusedTopic`** | **`mdx-bev`** | Kafka topic for BEV-fused output consumed by behavior analytics. |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.reid.enabled`** | **`true`** | Adds the `ReID`/`ReIDService` blocks to the tracker config and starts the perception app with `--tracker-reid`. Must match **`rtvi.vss-reid-embed.enabled`** — see [ReID](#appearance-reid). |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.reid.serviceAddress`** | **`""`** | Address the tracker queries. Empty derives the in-release `vss-reid-embed` Service, honouring **`global.useReleaseNamePrefix`**. Set only for an external ReID service. |
| **`rtvi.vss-rtvi-cv.standaloneWarehouse.mv3dt.reid.extractionInterval`** | **`8`** | Frames between ReID feature extractions. Raise to cut GPU cost, lower for harder re-association. |
| **`rtvi.vss-reid-embed.enabled`** | **`true`** | Deploys the ReID embedding service and its Milvus/etcd/MinIO backends. |
| **`rtvi.vss-reid-embed.resources`** | `nvidia.com/gpu: 1` | GPU request/limit for the embedding service. |
| **`rtvi.vss-reid-embed.init.enabled`** | **`true`** | One-shot Job staging the SigLIP2 and CLIP-ReID models. Set **`false`** only if the models are already on the CV models claim. |
| **`rtvi.vss-reid-embed.streamType`** | **`kafka`** | Message broker for embeddings, `kafka` or `redis`. Keep in step with **`rtvi.vss-rtvi-cv.standaloneWarehouse.streamType`**. |
| **`rtvi.vss-reid-embed.compression.enabled`** | **`true`** | Publish compressed embeddings on `mdx-compressed-embeddings`. |
| **`rtvi.vss-reid-embed.secondaryEmbedding.enabled`** | **`true`** | SigLIP2 embeddings for the retained samples. This is the service's only GPU workload, and the only writer of `mdx-compressed-embeddings` — see [GPU requirements](#gpu-requirements) for the trade-off in disabling it. |

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

## Scaling: NUM_STREAMS by GPU

The chart ships a fixed `NUM_STREAMS=4` in `bp-configurator.env` with no GPU cap —
unlike Docker Compose, which caps it automatically per `HARDWARE_PROFILE`. Before an initial
install or an upgrade where you want streams sized to your hardware, generate a values-override:

```bash
python3 deploy/helm/industry-profiles/warehouse-operations/scripts/compute_stream_cap.py \
  --mode mv3dt --num-streams <N> -o values-stream-cap.generated.yaml
```

Layer `-f values-stream-cap.generated.yaml` into `helm upgrade --install`, and set
`vios.vss-vios-nvstreamer.syncFileCount` to the effective count it prints. See
[`skills/deployment/vss-deploy-warehouse-helm/references/streams.md`](../../../../../skills/deployment/vss-deploy-warehouse-helm/references/streams.md)
for the full command and GPU→cap table. No skill/agent required — the script runs standalone.

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
