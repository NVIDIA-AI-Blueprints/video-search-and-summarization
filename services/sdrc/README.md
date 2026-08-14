<!-- SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!--
  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
-->

# SDRC — Sensor Distribution and Routing Controller


SDRC is a coordinator and routing layer that manages which backend worker handles each live stream, and keeps that routing stable, scalable, and recoverable. It is the stream-placement and traffic-routing backbone for VSS pipeline deployments.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Deployment Modes](#deployment-modes)
  - [Docker / Docker Compose](#docker--docker-compose)
  - [Kubernetes / Helm](#kubernetes--helm)
- [Quick Start](#quick-start)
  - [Docker Compose Quick Start](#docker-compose-quick-start)
  - [Kubernetes Quick Start](#kubernetes-quick-start)
- [Configuration Reference](#configuration-reference)
  - [Workload Config (`config.yml`)](#workload-config-configyml)
  - [Minimum Required Parameters](#minimum-required-parameters)
  - [Advanced Parameters](#advanced-parameters)
- [Provisioning Event Payload](#provisioning-event-payload)
- [Config Event Handling](#config-event-handling)
- [Envoy Stream Routing](#envoy-stream-routing)
- [API Reference](#api-reference)
  - [Controller / Router APIs](#controller--router-apis)
  - [Workload Coordinator APIs](#workload-coordinator-apis)
  - [Stream-Routing Listener Behavior](#stream-routing-listener-behavior)
  - [HTTP Status Codes](#http-status-codes)
- [Autonomous Stream Session Restoration](#autonomous-stream-session-restoration)
- [Observability](#observability)
- [Building the Image](#building-the-image)
- [Development](#development)
- [Directory Structure](#directory-structure)

---

## Overview

SDRC solves the stateful stream placement and routing problem in multi-worker deployments:

- **Distribute streams** across multiple workers (Kubernetes StatefulSet pods or Docker containers)
- **Track routing state** in Redis — which worker owns which stream
- **Call worker lifecycle APIs** (`/add`, `/delete`, and optionally `/config`) when streams arrive or leave
- **Optionally apply config events** to allocated workers via `/config`, with per-workload enablement and dedicated transport retries
- **Route client traffic** through a built-in Envoy proxy using a stream-ID header — clients need not discover which pod owns a given stream
- **Recover autonomously** — when a worker fails, SDRC migrates its streams to healthy workers without client changes
- **Support both Kafka and Redis** as the event bus for stream lifecycle events
- **Expose REST APIs and an interactive dashboard** for inspection and management

SDRC is used by VSS profiles to manage two primary workload types:

| Workload object | Purpose |
|---|---|
| `vss-vios-streamprocessing` | VIOS stream processing and proxy-stream workflows |
| `vss-rtvi-cv` | Realtime CV/perception workers |

---

## Architecture

```
                    ┌──────────────────────────────────────────────────────────┐
                    │  SDRC Container / Pod                                    │
                    │                                                          │
  Event bus ───────►│  Message consumers    ┌─────────────────────────────┐   │
  (Redis / Kafka)   │  (Redis stream /      │  Flask coordinator (app.py) │   │
                    │   Kafka topic)   ────►│  - Workload provisioning    │   │
                    │                       │  - Redis state management   │   │
  REST events ─────►│  POST /apply_metadata │  - xDS CDS/RDS endpoints    │   │
                    │  _payload             │  - Dashboard                │   │
                    │                       └─────────────┬───────────────┘   │
                    │                                     │ xDS (REST)        │
                    │  ┌──────────────────────────────────▼───────────────┐   │
                    │  │  Envoy proxy (generated config)                  │   │
                    │  │  - Per-workload stream listener (WDM_MS_LISTENER_│   │
                    │  │    PORT) — routes by stream-ID header via Redis  │   │
                    │  │  - Direct /sdrc listener (8010 default)          │   │
                    │  └──────────────────────────────────────────────────┘   │
                    └──────────────────────────────────────────────────────────┘
                                    │ route by stream header
                                    ▼
                         Worker pods / containers
                         (StatefulSet or Docker)
```

**Key data stores:**

| Store | Purpose |
|---|---|
| Redis hash `{wl_obj_name}` | Maps `stream_id → worker_name` (used by Envoy at request time) |
| Redis hash `{wl_obj_name}-pod` | Maps `worker_name → host` |
| Redis hash `WDM_REDIS_CACHE_OBJECT` | Workload spec cache — event payload per stream per worker |

---

## Deployment Modes

SDRC supports two worker-discovery modes, selected by `WDM_CLUSTER_TYPE` in `config.yml`.

| Mode | `WDM_CLUSTER_TYPE` | Worker discovery | Autoscaling |
|---|---|---|---|
| Docker | `docker` | Static `docker_cluster_config.json` | No |
| Kubernetes | `k8s` | K8s API via mounted ServiceAccount token | Yes (HPA) |

---

## Docker / Docker Compose

In Docker mode, SDRC uses a static worker inventory defined in `docker_cluster_config.json`. This is best for development, testing, or small fixed-capacity deployments.

### How it works

1. Workers are started as Docker containers with known `container_name` values.
2. SDRC reads `docker_cluster_config.json` to learn each worker's provisioning and routing address.
3. SDRC polls each worker's configurable HTTP health-check URL in a background thread and only assigns streams to healthy pods. The same health state drives `PodErrorWatcher` (replacing Docker socket container-status checks when `WDM_WL_HEALTH_CHECK_WAIT_ENABLED=true`).
4. Stream events from Redis or Kafka trigger placement decisions against this static pool.

### `docker_cluster_config.json`

One file per workload group. The top-level key is the Docker `container_name`.

```json
{
  "sdrc-example-app-a-1": {
    "provisioning_address": "<host-ip>:5000",
    "routing_address": "<host-ip>:5000",
    "process_type": "docker"
  },
  "sdrc-example-app-a-2": {
    "provisioning_address": "<host-ip>:5001",
    "routing_address": "<host-ip>:5001",
    "process_type": "docker"
  }
}
```

| Field | Description |
|---|---|
| top-level key | Docker `container_name`. Must match entries in `WDM_CLUSTER_CONTAINER_NAMES`. |
| `provisioning_address` | `host:port` SDRC uses to call `/add`, `/delete`, and HTTP health checks. |
| `routing_address` | `host:port` used for Envoy upstream. May differ from `provisioning_address`. |
| `process_type` | Must be `docker`. |

Health probes are built as `http://<provisioning_address><WDM_WL_HEALTH_CHECK_URL>`. Only the path (`WDM_WL_HEALTH_CHECK_URL`) is configurable; host and port always come from this file.

### `config.yml` workload block (Docker)

```yaml
docker-workload-a:
  wl_obj_name: sdrc-example-app-a
  port: 4000
  WDM_MS_LISTENER_PORT: 9000
  enable: true
  WDM_CLUSTER_TYPE: docker
  WDM_CLUSTER_CONFIG_FILE: /docker_cluster_config-a.json
  WDM_CLUSTER_CONTAINER_NAMES: '["sdrc-example-app-a-1","sdrc-example-app-a-2"]'
  WDM_TARGET_PORT_MAPPING: '{"sdrc-example-app-a-1": 5000, "sdrc-example-app-a-2": 5001}'
  WDM_CONSUMER_GRP_ID: consumer-grp-id-workload-a
  WDM_REDIS_CACHE_OBJECT: sdrc-example-a-data
  WDM_KFK_BOOTSTRAP_URL: "<host-ip>:29092"
  WDM_WL_REDIS_SERVER: <host-ip>
  WDM_WL_REDIS_PORT: 6379
  WDM_WL_ADD_URL: /add
  WDM_WL_CHANGE_ID_ADD: camera_add
  WDM_WL_DELETE_URL: /delete
  WDM_WL_THRESHOLD: 3
```

> Each enabled workload must have a **unique** `WDM_MS_LISTENER_PORT` and `port`.

### Docker Compose service (SDRC)

```yaml
services:
  sdrc:
    image: <sdrc-image>
    network_mode: host
    depends_on:
      wait-for-redis:
        condition: service_completed_successfully
      wait-for-docker-workloads:
        condition: service_completed_successfully
    environment:
      WDM_WORKLOADS_CONFIG: /config.yml
      OTEL_SDK_DISABLED: "true"
    volumes:
      - ./config.yml:/config.yml:ro
      - ./docker_cluster_config-a.json:/docker_cluster_config-a.json:ro
      - ./log:/logs
      - /var/run/docker.sock:/var/run/docker.sock
```

The Docker socket mount (`/var/run/docker.sock`) is required for SDRC to monitor container health.

---

## Kubernetes / Helm

In Kubernetes mode, SDRC discovers workers through the Kubernetes API. Workers run as a **StatefulSet** with stable DNS pod identities. SDRC requires a ServiceAccount with list/watch/scale permissions.

### RBAC (`sdrc-rbac.yaml`)

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: sdrc-svcaccount
  labels:
    app.kubernetes.io/name: sdrc
    app.kubernetes.io/component: coordinator
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: sdrc-api-role
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/status", "services", "configmaps"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "replicasets"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["statefulsets/scale"]
    verbs: ["get", "patch", "update"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: sdrc-api-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: sdrc-api-role
subjects:
  - kind: ServiceAccount
    name: sdrc-svcaccount
```

### ConfigMap (`sdrc-configmap.yaml`)

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sdrc-config
  labels:
    app.kubernetes.io/name: sdrc
data:
  config.yml: |
    k8s-workerset1:
      wl_obj_name: sdrc-example-workload
      port: 4001
      WDM_MS_LISTENER_PORT: 9000
      enable: true
      WDM_CONSUMER_GRP_ID: consumer-grp-id-workload-a
      WDM_REDIS_CACHE_OBJECT: sdrc-example-a-data
      WDM_TARGET_PORT_MAPPING: '{"default": 5000, "grpc": 50051, "websocket": 5050}'
      WDM_CLUSTER_TYPE: k8s
      WDM_KFK_BOOTSTRAP_URL: "kafka:29092"
      WDM_WL_REDIS_SERVER: redis
      WDM_WL_REDIS_PORT: 6379
      WDM_WL_ADD_URL: /add
      WDM_WL_CHANGE_ID_ADD: camera_add
      WDM_WL_DELETE_URL: /delete
      WDM_WL_THRESHOLD: 3
```

### Deployment (`sdrc-deployment.yaml`)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sdrc
  labels:
    app.kubernetes.io/name: sdrc
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: sdrc
  template:
    metadata:
      labels:
        app.kubernetes.io/name: sdrc
    spec:
      serviceAccountName: sdrc-svcaccount
      imagePullSecrets:
        - name: nvcr-io-registry-secret
      containers:
        - name: sdrc
          image: <sdrc-image>
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 5002
          env:
            - name: WDM_WORKLOADS_CONFIG
              value: /config.yml
          volumeMounts:
            - name: config
              mountPath: /config.yml
              subPath: config.yml
              readOnly: true
            - name: logs
              mountPath: /logs
          readinessProbe:
            tcpSocket:
              port: http
            initialDelaySeconds: 15
            periodSeconds: 10
          livenessProbe:
            tcpSocket:
              port: http
            initialDelaySeconds: 30
            periodSeconds: 20
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              memory: 2Gi
      volumes:
        - name: config
          configMap:
            name: sdrc-config
        - name: logs
          emptyDir: {}
```

### Service (`sdrc-service.yaml`)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: sdrc
  labels:
    app.kubernetes.io/name: sdrc
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: sdrc
  ports:
    - name: http
      port: 5002
      targetPort: http
```

### Apply

```bash
kubectl apply -f sdrc-rbac.yaml -f sdrc-configmap.yaml -f sdrc-deployment.yaml -f sdrc-service.yaml
kubectl get deploy,po,svc -l app.kubernetes.io/name=sdrc
```

### Helm Chart

A Helm chart is available under `kubernetes/helm/sdrc/`. Key `values.yaml` defaults:

```yaml
service:
  controller:
    type: ClusterIP
    port: 5002          # SDRC controller API
  sdrcDirectListener:
    type: NodePort
    port: 8011          # Direct /sdrc listener (no stream routing)
    nodePort: 30001
  envoyAdmin:
    type: LoadBalancer
    port: 9902          # Envoy admin/diagnostics

runtimeEnv:
  WDM_WL_REDIS_SERVER: redis
  WDM_WL_REDIS_PORT: "6379"
  OTEL_SDK_DISABLED: "true"
  KUBERNETES_HOST: kubernetes.default.svc
  KUBERNETES_PORT: "443"
```

In Helm / VSS profiles, the controller is typically exposed on port `5003` (mapped from container port `5002`). Per-workload Envoy listener ports are **not** declared in the chart's `service.yaml` — they come from the enabled workload blocks in the mounted `config.yml`. Add a Service or Ingress for generated listener ports only when other components need to reach them through Kubernetes service discovery.

---

## Quick Start

### Docker Compose Quick Start

1. **Start workers:**
   ```bash
   docker compose -f testapp/compose.yaml up -d
   ```

2. **Prepare `docker_cluster_config.json`** with your worker container names and addresses (see [Docker Compose](#docker--docker-compose) above).

3. **Set Redis/Kafka addresses** in `config.yml`.

4. **Start SDRC:**
   ```bash
   docker compose -f docker-compose.yaml up -d
   ```

5. **Send a provisioning event:**
   ```bash
   curl -s -X POST http://localhost:5002/sdrc/<wl_obj_name>/apply_metadata_payload \
     -H "Content-Type: application/json" \
     -d '{
       "alert_type": "camera_status_change",
       "created_at": "2024-01-01T00:00:00Z",
       "event": {
         "camera_id": "stream-001",
         "camera_url": "rtsp://<source>/<stream>",
         "change": "camera_add"
       },
       "source": "test"
     }'
   ```

6. **Route traffic through Envoy:**
   ```bash
   curl -H "x-stream-id: stream-001" http://localhost:9000/hello
   ```

### Kubernetes Quick Start

1. Apply RBAC, ConfigMap, Deployment, and Service (see manifests above).
2. Send provisioning events via `POST /sdrc/<wl_obj_name>/apply_metadata_payload` or the configured message bus.
3. Route traffic through the per-workload Envoy listener with the stream-ID header.

---

## Configuration Reference

### Workload Config (`config.yml`)

`config.yml` is the primary runtime configuration. It is mounted into the container and its path is pointed to by `WDM_WORKLOADS_CONFIG`. Each top-level key is a **workload block**; SDRC starts one coordinator per enabled block.

**Reference config included with the service** (`config.yml`):

```yaml
docker-workload:               # Docker mode, one worker, for local testing
  wl_obj_name: sdrc-example-app-1
  port: 4000
  WDM_MS_LISTENER_PORT: 9000
  enable: true
  WDM_CLUSTER_TYPE: docker
  WDM_CLUSTER_CONTAINER_NAMES: '["sdrc-example-app-1"]'
  WDM_KFK_BOOTSTRAP_URL: "<KAFKA_HOST>:<KAFKA_PORT>"
  WDM_WL_REDIS_SERVER: <REDIS_HOST>
  WDM_WL_REDIS_PORT: 6379
  WDM_WL_ADD_URL: /add
  WDM_WL_CHANGE_ID_ADD: camera_add
  WDM_WL_DELETE_URL: /delete
  WDM_WL_THRESHOLD: 3
  WDM_CONTROLLER_REPROVISION: true

k8s-workerset1:                # Kubernetes mode, StatefulSet workers
  wl_obj_name: sdrc-example-workload
  port: 4001
  WDM_MS_LISTENER_PORT: 9001
  enable: true
  WDM_CLUSTER_TYPE: k8s
  WDM_TARGET_PORT_MAPPING: '{"default": 5000, "grpc": 50051, "websocket": 5050}'
  WDM_KFK_BOOTSTRAP_URL: "<KAFKA_HOST>:<KAFKA_PORT>"
  WDM_WL_REDIS_SERVER: <REDIS_HOST>
  WDM_WL_REDIS_PORT: 6379
  WDM_WL_ADD_URL: /add
  WDM_WL_CHANGE_ID_ADD: camera_add
  WDM_WL_DELETE_URL: /delete
  WDM_WL_THRESHOLD: 3
  WDM_CONTROLLER_REPROVISION: true
```

---

### Minimum Required Parameters

#### Deployment mode and workload discovery

| Parameter | Description |
|---|---|
| `WDM_WORKLOADS_CONFIG` | Path to the mounted workload config file inside the container. |
| `wl_obj_name` | Workload set name. In Kubernetes, must match the worker StatefulSet name. |
| `port` | Local SDRC coordinator port for this workload block. Must be unique per enabled workload. |
| `WDM_CLUSTER_TYPE` | `docker` or `k8s`. |
| `WDM_CLUSTER_CONFIG_FILE` | Docker mode only. Path to the worker inventory JSON. |
| `WDM_CLUSTER_CONTAINER_NAMES` | Docker mode only. JSON array of worker container names SDRC monitors. |
| `WDM_WL_KIND` | Kubernetes mode. K8s owner kind for worker pods (commonly `StatefulSet`). |
| `WDM_WL_CONFIG_PORT` | Worker API port for add/delete in Kubernetes mode. |

#### Stream management

| Parameter | Description |
|---|---|
| `WDM_WL_THRESHOLD` | Maximum concurrent streams per worker. Workers at this limit are skipped. |
| `WDM_WL_REDIS_SERVER` / `WDM_WL_REDIS_PORT` | Redis host and port for stream state and events. |
| `WDM_REDIS_CACHE_OBJECT` | Redis hash name for this workload's stream placement cache. Must be unique per workload. |
| `WDM_REDIS_MSG_KEY` | Redis stream name SDRC consumes for stream lifecycle events. |
| `WDM_WL_REDIS_MSG_FIELD` | Field in each Redis stream item containing the JSON event payload. |
| `WDM_CONSUMER_GRP_ID` | Consumer group ID. Must be unique per workload. |
| `WDM_KFK_ENABLE` | Set `false` for Redis-only input. |
| `WDM_WL_ADD_URL` / `WDM_WL_DELETE_URL` | Worker HTTP paths SDRC calls to add or remove a stream. |
| `WDM_EVENT_OBJECT_FIELD` | Payload field containing the inner stream event object (default `event`). |
| `WDM_WL_ID_FIELD` | Field inside the event object used as the stream ID (default `camera_id`). |
| `WDM_WL_CHANGE_FIELD` | Field inside the event object carrying the lifecycle action (default `change`). |
| `WDM_WL_CHANGE_ID_ADD` / `WDM_WL_CHANGE_ID_DEL` | Values of `WDM_WL_CHANGE_FIELD` meaning add and delete. |

#### Envoy routing

| Parameter | Description |
|---|---|
| `WDM_MS_LISTENER_PORT` | Unique Envoy listener port for this workload. Clients send routed traffic here. |
| `WDM_TARGET_PORT_MAPPING` | JSON map of worker listener ports. Use `default` for HTTP, add `grpc`/`websocket` as needed. |
| `ENVOY_ROUTE_HEADER` | Header name carrying the stream ID. Defaults to `x-stream-id`. VSS profiles use `streamid`. |
| `NOHEADERTARGETROUTE` | Set `true` to route headerless requests to a fallback cluster. |
| `HEADERLESS_SERVICE_ENDPOINTS` | Required when `NOHEADERTARGETROUTE` is `true`. JSON array of fallback `host:port` endpoints. |

---

### Advanced Parameters

#### Workload identity and controller

| Parameter | Default | Description |
|---|---|---|
| `WDM_CONFIG_URL` | `/config` | Worker configuration API path. |
| `WDM_CONFIG_PORT` | `9002` | Worker configuration API port. |
| `WDM_WL_PROXY_URL` | `/hello` | Proxy URL prefix for legacy routing flows. |
| `WDM_INITIATOR_WLOBJ_NAME` | `vms-vms` | Initiator workload object name for recovery logic. |

#### Worker discovery and capacity

| Parameter | Default | Description |
|---|---|---|
| `WDM_MAX_REPLICAS` | `4` | Maximum replicas SDRC may scale to in Kubernetes mode. |
| `WDM_MIN_PODS` | `0` | Minimum number of pods to maintain. |
| `WDM_STANDBY_POD_COUNT` | `2` | Number of standby pods expected by status reporting. |
| `WDM_TIMEOUT` | `300` | Default operation timeout in seconds. |
| `WDM_POD_WATCH_DOCKER_DELAY` | `0.05` | Legacy Docker socket watch poll delay (used only when no health watcher is attached). |
| `WDM_WL_HEALTH_CHECK_WAIT_ENABLED` | `true` | Master switch for HTTP workload health checks. `true`: background polling, prefer healthy pods for placement, wait in `add()` before `/add` (see `WDM_ADD_HEALTH_CHECK_TIMEOUT`), and PodErrorWatcher uses HTTP health. `false`: legacy mode — no HTTP health wait; Docker PodErrorWatcher uses container state. |
| `WDM_WL_HEALTH_CHECK_URL` | `/healthz` | HTTP path polled with GET for per-pod readiness. HTTP 200 means healthy. |
| `WDM_HEALTH_CHECK_INTERVAL` | `2.0` | Background health poll interval in seconds. |
| `WDM_HEALTH_CHECK_TIMEOUT` | `2.0` | Per-probe HTTP timeout in seconds. |
| `WDM_ADD_HEALTH_CHECK_TIMEOUT` | `60` | Max seconds `add()` waits for the selected pod's health before `/add`. `-1` waits forever. On timeout, SDRC defers the event (bus RETRYABLE / HTTP 503) so the consumer is not blocked. |
| `WDM_ENABLE_REGEX_MAPPING` | `False` | Enable regex-based stream allocation. |

#### Event input and stream lifecycle

| Parameter | Default | Description |
|---|---|---|
| `WDM_MSG_BUS` | `kafka` | Message bus type. |
| `WDM_MSG_TOPIC` | `mdx-notification` | Kafka topic for stream lifecycle events. |
| `WDM_KFK_BOOTSTRAP_URL` | `localhost:9092` | Kafka bootstrap address. |
| `WDM_KFK_SESSION_TIME_OUT` | `30000` | Kafka session timeout in milliseconds. |
| `WDM_FORWARD_MSG_TYPE` | `event_message` | Whether to forward the full event envelope or only the inner event to workers. |
| `WDM_WL_CHANGE_ID_POD_CONFIGURE` | `config` | Change value that triggers pod configuration (`/config` flow). See [Config Event Handling](#config-event-handling). |
| `WDM_HANDLE_CONFIG_EVENTS` | `false` | Opt-in enable for config events. Default `false` (skip `/config`). Set `true` only for workloads that must apply config (for example warehouse 3D `rtvi-cv`). |
| `WDM_CONFIG_RETRY_ATTEMPTS` | `3` | Dedicated transport retry budget for `/config` HTTP calls only (does not use `WDM_ADD_REMOVE_RETRY_ATTEMPTS`). If unset at call time, falls back to `min(WDM_ADD_REMOVE_RETRY_ATTEMPTS, 5)`. |
| `WDM_CONFIG_RETRY_DELAY` | `0.5` | Delay in seconds between `/config` transport retries. |
| `WDM_CONFIG_DEFER_ON_FAILURE` | `False` | If `true`, failed config handling returns deferred status and leaves the bus message uncommitted for later retry. Default `false` treats config failures as terminal and commits the offset. |
| `WDM_EVENT_RETRY_LIMIT` | `20` | Max times to retry the **same** Redis/Kafka event on temporary failures before giving up. After the limit, SDRC logs an ERROR (log-based DLQ) and commits/ACKs so the bus is not blocked. |
| `DELETE_API_METHOD` | `POST` | HTTP method used for worker delete calls. |
| `WDM_ADD_REMOVE_RETRY_ATTEMPTS` | `2` | Number of add/remove retry attempts. |
| `WDM_ADD_REMOVE_RETRY_DELAY` | `0.5` | Delay between retries in seconds. |
| `WDM_ADD_REMOVE_REQUEST_TIMEOUT` | `2` | Timeout for add/remove HTTP requests in seconds. |

#### Redis state and cache

| Parameter | Default | Description |
|---|---|---|
| `WDM_CACHE_METHOD` | `redis` | Cache implementation for stream state. |
| `WDM_REDIS_LOCK_TIMEOUT` | `2` | Redis lock timeout in seconds. |
| `WDM_AGENT_EVENT_BUS` | `sdr_agent_event` | Redis pub/sub channel for SDRC agent events. |
| `WDM_ERROR_EVENT_MSG_KEY` | `wdm_error_events` | Redis key prefix for SDRC error events. |

#### Envoy routing (advanced)

| Parameter | Default | Description |
|---|---|---|
| `WDM_SDRC_DIRECT_LISTENER_PORT` | `8010` | Direct `/sdrc` listener port (no stream routing). |
| `ENVOY_REQUEST_TIMEOUT` | `5` | Envoy upstream request timeout in seconds. |
| `WDM_XDS_USE_POD_DNS` | `True` | Prefer pod DNS names in xDS cluster endpoints. |
| `WDM_XDS_USE_IP_ADDRESS` | `False` | Use pod IPs instead of DNS in xDS endpoints. |

#### Startup, preload, and recovery

| Parameter | Default | Description |
|---|---|---|
| `WDM_PRELOAD_WORKLOAD` | unset | Optional preload file path for initial workload events. |
| `WDM_PRELOAD_DELAY_FOR_REDIS` | `False` | Delay preload until Redis listener is connected. |
| `WDM_PRELOAD_DELAY_FOR_DS_API` | `False` | Delay preload until worker API is reachable. |
| `WDM_CONTROLLER_REPROVISION` | `True` | Enable autonomous stream session restoration after worker failure. |
| `WDM_RESET_ON_WLOBJ_CRASH` | `True` | Reset workload state when the managed workload crashes. |
| `WDM_CLEAR_DATA_WL` | `False` | Clear workload stream state on startup. |
| `WDM_INITIALIZE_FROM_VST` | `True` | Initialize streams from VST on startup. |
| `WDM_API_WAIT_MAX_RETRIES_IN_SEC` | `30` | Max seconds to wait for startup/preload APIs to become available. |

#### Observability

| Parameter | Default | Description |
|---|---|---|
| `OTEL_SERVICE_NAME` | `sdr-agent` | OpenTelemetry service name. |
| `WDM_LOG_LEVEL` | `INFO` | Root log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). Use `DEBUG` to restore poll/inventory detail. |
| `WDM_LOG_FORMAT` | `text` | Log format: `text` (human-readable KV) or `json` (one JSON object per line). |
| `WDM_LOG_TO_FILE` | `true` | Write rotating files under `logs/`; set `0`/`false` for stdout-only. |
| `WDM_DISABLE_WERKZEUG_LOGGING` | `False` | Disable Werkzeug HTTP request logging. |
| `WDM_SDR_AGENT_PORT` | `4000` | SDR agent service port reported to an external controller. |
| `CONTROLLER_SERVICE_URL` | `sdr-controller-service.default.svc.cluster.local:4001/report` | Controller report endpoint. |

---

## Provisioning Event Payload

Stream assignment is driven by JSON event payloads published on the message bus (Redis or Kafka) or sent with `POST /sdrc/<wl_obj_name>/apply_metadata_payload`.

### Envelope format

```json
{
  "alert_type": "camera_status_change",
  "created_at": "2024-01-20T21:50:36Z",
  "event": {
    "camera_id": "e0925d6f-9ef0-4cc4-9fc9-b5633d2cbbe1",
    "camera_name": "webcam_stream_01",
    "camera_url": "rtsp://<source>/<stream>",
    "change": "camera_add"
  },
  "source": "vst"
}
```

| Field | Description |
|---|---|
| `alert_type` | Event category (e.g. `camera_status_change`). |
| `created_at` | ISO 8601 timestamp. |
| `event` | Inner object name set by `WDM_EVENT_OBJECT_FIELD` (default `event`; some configs use `value`). |
| `event.camera_id` | Stream or sensor ID. Used as the unique key for allocation and Redis routing (`WDM_WL_ID_FIELD`). |
| `event.camera_url` | Source URL (RTSP, WebRTC, etc.) passed through to the worker. |
| `event.change` | Lifecycle action. Compared against `WDM_WL_CHANGE_ID_ADD`, `WDM_WL_CHANGE_ID_DEL`, etc. |
| `event.metadata` | Optional per-stream runtime configuration (codec, resolution, etc.). |
| `source` | Originating system (e.g. `vst`, `preload`). |

### Change types

| `change` value | SDRC behavior |
|---|---|
| `camera_add` (reference) | Allocate a worker and call `WDM_WL_ADD_URL` with the full envelope. |
| `camera_remove` | Deprovision: call `WDM_WL_DELETE_URL`, clear Redis routing state. |
| `camera_streaming` | Treated as add when `WDM_WL_CHANGE_ID_ADD` is set to this value. |
| `config` (or `WDM_WL_CHANGE_ID_POD_CONFIGURE`) | Worker configuration update via `/config`. Gated by `WDM_HANDLE_CONFIG_EVENTS`; see [Config Event Handling](#config-event-handling). |

### Provisioning pipeline

When an add event arrives, SDRC runs:

1. **Parse** the message: read the inner event object and compare `change` to `WDM_WL_CHANGE_ID_ADD`.
2. **Build candidate pool**: load workers from `docker_cluster_config.json` (Docker) or K8s API (k8s).
3. **Filter by health** (when `WDM_WL_HEALTH_CHECK_WAIT_ENABLED=true`): prefer pods whose latest `WDM_WL_HEALTH_CHECK_URL` probe passed. If none are healthy but capacity remains, still select a pod — `add()` waits for health. When disabled, placement uses legacy pod-down detection (e.g. Docker container Running state).
4. **Select worker**: pick an eligible worker under `WDM_WL_THRESHOLD` (`WDM_WL_ASSIGNING_METHOD`).
5. **Wait for health in `add()`** (when `WDM_WL_HEALTH_CHECK_WAIT_ENABLED=true`): block up to `WDM_ADD_HEALTH_CHECK_TIMEOUT` (`-1` = forever) until the selected pod passes health before the `/add` retry loop. Shared by bus and HTTP. On timeout, defer (bus keeps the message pending / HTTP 503). When disabled, `/add` starts immediately.
6. **POST to worker**: call `http://<provisioning_address><WDM_WL_ADD_URL>` with the full event envelope.
7. **On HTTP 200**: update Redis mappings and workload spec cache.
8. **Publish** an agent event on the configured bus.

Delete events reverse the flow: locate the assigned worker, POST `WDM_WL_DELETE_URL`, remove Redis mappings.

**Worker selection rules:**

- When HTTP health is enabled (`WDM_WL_HEALTH_CHECK_WAIT_ENABLED`): prefer pods that pass `WDM_WL_HEALTH_CHECK_URL`; otherwise use legacy pod-down detection.
- Current stream count must be `< WDM_WL_THRESHOLD`.
- Selection uses `WDM_WL_ASSIGNING_METHOD` (`lru_round_robin` or `sequential`).

If only unhealthy workers have capacity (health mode), SDRC still assigns and waits inside `add()` up to `WDM_ADD_HEALTH_CHECK_TIMEOUT` for health. If every worker is at capacity, it logs capacity exhaustion — increase `WDM_WL_THRESHOLD`, add workers, or scale the StatefulSet.

---

## Config Event Handling

In addition to add/remove stream lifecycle events, SDRC can apply **configuration events** to an already allocated worker by POSTing to the worker `/config` API (`WDM_CONFIG_URL` on `WDM_CONFIG_PORT`). This path is used when the event `change` value matches `WDM_WL_CHANGE_ID_POD_CONFIGURE` (default `config`).

Config handling is **opt-in per workload** so shared Redis/Kafka buses do not force every consumer to call `/config`.

### When config events are handled

Effective handling is controlled by `WDM_HANDLE_CONFIG_EVENTS` (default **`false`**, opt-in):

| `WDM_HANDLE_CONFIG_EVENTS` | Behavior |
|---|---|
| `true` | Handle config events: resolve the target worker and call `/config`. |
| `false` / unset | Skip config events for this workload (`CONFIGURE_NOOP`); do not call `/config`. |

**Deploy guidance:** leave unset or set `WDM_HANDLE_CONFIG_EVENTS: false` on workloads that have no `/config` endpoint (for example streamprocessing, alerts, warehouse 2D/MV3DT RT-CV). Set `WDM_HANDLE_CONFIG_EVENTS: true` only where `/config` is required (for example warehouse 3D `rtvi-cv`).

### Config apply pipeline

When a config event is accepted:

1. **Gate** with `should_handle_config_events()` (see table above). If disabled, return `CONFIGURE_NOOP` and commit the bus message as appropriate.
2. **Validate** the payload (must be an object and include the encoded worker name key from `WDM_POD_ALLOCATION_ENCODED_NAME_KEY`).
3. **Resolve** the target worker allocation; missing allocation is a no-op for remove-shaped config, or a failure for apply-shaped config.
4. **POST** `http://<worker>:<WDM_CONFIG_PORT><WDM_CONFIG_URL>` with the config payload.
5. **Retry transport errors only** using `WDM_CONFIG_RETRY_ATTEMPTS` / `WDM_CONFIG_RETRY_DELAY`. HTTP responses (including 4xx/5xx) are returned without further transport retries.
6. **Persist** allocation/config state only after a successful HTTP 200 response.

### Failure and bus commit behavior

SDRC uses a **safe bus policy** for Redis and Kafka (always on):

| Result | Meaning | Bus behavior |
|---|---|---|
| Success (`OK`) / `CONFIGURE_OK` | Operation succeeded | Commit |
| `NOOP` / `CONFIGURE_NOOP` | Skipped or nothing to do | Commit |
| Terminal failure / `CONFIGURE_FAILED` | Permanent/poison failure (malformed payload, permanent reject, exhausted retries) | **ERROR log** (log-based DLQ) + **Commit** |
| Retryable / `CONFIGURE_DEFERRED` | Temporary failure (unready workers, max replicas, HTTP/Redis/Kubernetes client blips, deferred configure) | Do **not** commit; retry the same event up to `WDM_EVENT_RETRY_LIMIT`, then promote to terminal |

On Kafka retryable failures, SDRC **seeks back** to the failed offset before returning so flask-kafka’s post-handler `commit()` (and any later successful commit) cannot acknowledge and skip that offset. If `seek` fails, SDRC installs a one-shot park commit for that offset; if that also fails, the handler raises so flask-kafka does not commit past the event.

This keeps the bus moving when a message can never succeed, while still retrying temporary conditions. Enable `WDM_CONFIG_DEFER_ON_FAILURE` only when you intentionally want configure redelivery until `/config` succeeds.

### Example workload settings

```yaml
# Workload without /config (skip configure events)
docker-workload-streamprocessing:
  WDM_HANDLE_CONFIG_EVENTS: false

# Workload that applies /config (warehouse 3D rtvi-cv example)
docker-workload-rtvi-cv:
  WDM_CONFIG_PORT: 9003
  WDM_CONFIG_URL: /config
  WDM_HANDLE_CONFIG_EVENTS: true
  WDM_CONFIG_RETRY_ATTEMPTS: 60
  WDM_CONFIG_RETRY_DELAY: 1
```

---

## Envoy Stream Routing

SDRC includes a built-in Envoy proxy. After a stream is provisioned, clients do not call workers directly — they connect to the **Envoy listener port** for the target workload and identify the stream in a request header.

### Listener generation

At startup, SDRC reads `config.yml` and generates one Envoy listener per enabled workload block that defines both `WDM_MS_LISTENER_PORT` and `port`. It also generates:

- A **direct listener** (default port `8010`) that forwards `/sdrc` paths to the SDRC controller.
- **xDS CDS/RDS endpoints** on the controller port so Envoy knows worker addresses from `WDM_TARGET_PORT_MAPPING`.

### Runtime API surfaces

| Surface | Default port | Description |
|---|---|---|
| SDRC controller / router | `5002` (Helm: `5003`) | Multi-workload controller API, dashboard, xDS endpoints, and `/sdrc/<wl_obj_name>/...` proxy. |
| Direct `/sdrc` listener | `8010` (Helm: `8011`) | Envoy listener for `/sdrc` paths without stream routing. Useful when the controller port is not directly exposed. |
| Per-workload listener | `WDM_MS_LISTENER_PORT` | Stream-routing listener per workload. Non-`/sdrc` paths route to the assigned worker using the route header. |
| Envoy admin | `9901` (Helm: `9902`) | Envoy diagnostics. Treat as an internal/operator interface. |

### Request routing

Clients send HTTP requests to the listener port. Envoy reads the stream-ID header, looks up `HGET {wl_obj_name} {stream_id}` in Redis, and forwards the request to the assigned worker.

```
Client → Envoy listener (WDM_MS_LISTENER_PORT)
          → Redis lookup: stream_id → worker
          → Forward to assigned worker
```

The route header name is `ENVOY_ROUTE_HEADER` (default `x-stream-id`; VSS profiles use `streamid`). If the header is absent, the listener can also read the stream ID from a query parameter with the same name.

### Test routing with curl

```bash
# Using the default route header
curl -H "x-stream-id: <stream-id>" http://<sdrc-host>:<WDM_MS_LISTENER_PORT>/hello

# VSS profiles use 'streamid'
curl -H "streamid: <stream-id>" http://<sdrc-host>:<WDM_MS_LISTENER_PORT>/hello
```

**Common issues:**

- `503 / no upstream`: Stream not provisioned, missing route header, wrong listener port, or Redis mapping missing.

### gRPC and WebSocket routing

When `WDM_TARGET_PORT_MAPPING` includes `grpc` or `websocket` entries, Envoy selects the protocol-specific upstream cluster. Configure the appropriate port for each protocol exposed by workers.

---

## API Reference

### URL patterns

```
# Controller API (management, dashboard, xDS)
http://<sdrc-host>:<controller-port>/<controller-endpoint>

# Workload coordinator via controller proxy
http://<sdrc-host>:<controller-port>/sdrc/<wl_obj_name>/<workload-endpoint>

# Workload coordinator via that workload's Envoy listener
http://<sdrc-host>:<WDM_MS_LISTENER_PORT>/sdrc/<workload-endpoint>

# Stream-routed application traffic
http://<sdrc-host>:<WDM_MS_LISTENER_PORT>/<worker-path>
header: <route-header>: <stream-id>
```

### Controller / Router APIs

Served on the controller port. Also reachable through the direct `/sdrc` Envoy listener.
Interactive API docs are available at `/api/docs/` and the OpenAPI document at `/openapi.json`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/docs/` | Interactive Swagger UI. |
| `GET` | `/openapi.json` | OpenAPI 3.0 document. |
| `GET` | `/` | JSON index: enabled workload names, proxied `/sdrc` paths, documentation links. |
| `GET` | `/dashboard` | HTML dashboard for workload state and test payloads. |
| `GET` | `/dashboard/health` | Per-workload health, sensor count, and pod count summary. |
| `GET` | `/dashboard/clusterxds` | Cluster discovery output for inspection. |
| `GET` | `/dashboard/config_yml` | Mounted `config.yml` content. Do not expose where config values should stay private. |
| `POST` | `/dashboard/global_add?transport=redis\|kafka` | Publish a lifecycle payload to Redis or Kafka. Body: standard lifecycle envelope. |
| `POST` | `/v3/discovery:clusters` | Envoy CDS response for enabled workloads. |
| `POST` | `/v3/discovery:routes` | Envoy RDS response. Optional body: `{"resource_names": ["<wl_obj_name>"]}`. |
| `*` | `/sdrc/<wl_obj_name>/<path>` | Proxy all HTTP methods to the named workload coordinator. |

### Workload Coordinator APIs

Each enabled workload exposes a coordinator API accessible through the router at `/sdrc/<wl_obj_name>/<endpoint>`. Interactive docs are at `/sdrc/<wl_obj_name>/api/docs/`.

> **Warning:** `GET /reset` is destructive. Do not link to it from health checks, crawlers, or browser previews.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/docs/` | Interactive Swagger UI for this workload. |
| `GET` | `/openapi.json` | OpenAPI 3.0 document. |
| `GET` | `/` | HTML page with non-sensitive runtime configuration keys. |
| `GET` | `/healthz` | Health check. Returns `OK`. |
| `GET` | `/reset` | **Destructive.** Clears workload spec/cache state. |
| `GET` | `/get_config` | Current allocation configuration from the worker discovery layer. |
| `GET` | `/replicas` | Workload object name and ready/desired replica counts. |
| `GET` | `/getwl?id=<stream-id>` | Cached workload specification for a stream ID. |
| `GET` | `/getpoddns?id=<stream-id>` | Pod/container name and DNS mapping for a stream ID. |
| `GET` | `/stream` | Continuous stream of ready replica counts. Use for diagnostics, not polling. |
| `GET` | `/current_distributed_streams_cache` | List of all cached stream specifications. |
| `GET` | `/current_distributed_streams_name_id_url` | Map of stream ID to the cached event payload for each active stream. |
| `GET` | `/current_streamid_address_mapping` | Redis stream ID to assigned pod/container mapping. |
| `GET` | `/redis_cache_data` | Full workload cache object name and contents. |
| `GET` | `/metrics` | Prometheus metrics, including per-pod stream count. |
| `GET` | `/get_wl_replica_data` | Replica and pod saturation summary: running, standby, engaged, saturated, pending. |
| `GET` | `/pod_list` | All pods/containers for this workload with phase, address, and assigned stream IDs. |
| `GET` | `/down_pods` | Non-running pods/containers with phase, address, and assigned stream IDs. |
| `GET` | `/getpodInfo?id=<stream-id>` | Disaggregated pod details for the pod assigned to a stream ID. |
| `POST` | `/v3/discovery:routes` | Single-workload Envoy RDS response. |
| `POST` | `/v3/discovery:clusters` | Single-workload Envoy CDS response. |
| `POST` | `/apply_metadata_payload` | Apply a lifecycle event payload immediately. |
| `POST` | `/remove_stream` | Deprovision a stream. Body: `{"stream_id": "<stream-id>"}`. |
| `POST` | `/cache_metadata_update` | Merge or overwrite metadata for a cached stream. Body: `{"stream_id": "...", "additional_metadata": {...}, "overwrite": false, "cache_key": "external_metadata"}`. |

### Stream-Routing Listener Behavior

Per-workload Envoy listeners route application traffic:

- `/sdrc/<endpoint>` — reaches the workload coordinator.
- Any other path — routes to the assigned worker using the stream-ID header.
- If `NOHEADERTARGETROUTE` is `true`, headerless non-`/sdrc` requests route to `HEADERLESS_SERVICE_ENDPOINTS`.
- gRPC and WebSocket requests use the same stream-to-worker lookup with protocol-specific clusters.

### HTTP Status Codes

| Status | Meaning |
|---|---|
| `200` | Request succeeded. |
| `400` | Invalid request body, wrong `Content-Type`, unsupported option, or required field missing. |
| `404` | Unknown workload, missing route resource, or stream not found. |
| `500` | Internal processing, Redis, Kafka, Kubernetes, or worker lifecycle error. |
| `502` | Router proxy could not reach the workload coordinator. |
| `503` | Transport not configured, or routed application traffic has no resolvable upstream. |

---

## Autonomous Stream Session Restoration

SDRC runs a background pod/container watcher. When a worker becomes unavailable, SDRC automatically migrates its stream sessions to healthy workers without any client changes.

### Detection

- **Docker mode**: watcher monitors Docker socket; triggers on container state change.
- **Kubernetes mode**: watcher monitors pod phase via K8s API; triggers on failed/terminated pod.

When a failure is detected, SDRC:
1. Publishes a `critical` event on the message bus for the affected `wl_obj_name`.
2. Loads affected stream payloads from the workload spec cache (`WDM_REDIS_CACHE_OBJECT`).
3. If `WDM_CONTROLLER_REPROVISION` is `true` (default), runs autonomous restoration.

### Restoration sequence

For each stream on the failed worker:

1. **Deprovision** on the failed worker (`WDM_WL_DELETE_URL`) and remove Redis entries.
2. **Select a new worker** that is running and under `WDM_WL_THRESHOLD`.
3. **Provision** on the new worker (`WDM_WL_ADD_URL`) with the stored event payload.
4. **Update Redis** so Envoy routes the stream header to the new worker.

Clients continue sending to the same listener port and route header. After restoration, Envoy forwards traffic to the replacement worker.

---

## Observability

SDRC supports OpenTelemetry tracing (see `lib/tracing.py`) and Prometheus metrics.

| Endpoint | Description |
|---|---|
| `GET /metrics` (workload coordinator) | Prometheus metrics including per-pod stream count. |
| `GET /get_wl_replica_data` | Replica saturation summary. |
| Envoy admin (`9901` / `9902`) | Envoy diagnostics and configuration inspection. |

Set `OTEL_SDK_DISABLED=true` to disable OpenTelemetry (useful in environments without a collector). Configure the collector with standard `OTEL_EXPORTER_OTLP_*` environment variables.

Logging is configured at startup via `lib/logging/wdm_logging.py`:

| Variable | Default | Purpose |
|---|---|---|
| `WDM_LOG_LEVEL` | `INFO` | Root level. `INFO` keeps lifecycle/state changes; poll/inventory detail is at `DEBUG`. |
| `WDM_LOG_FORMAT` | `text` | `text` for console skim; `json` for collectors (`jq`, Loki, Fluent Bit). |
| `WDM_LOG_TO_FILE` | `true` | Rotating files under `logs/`; disable for 12-factor stdout-only. |
| `WDM_DISABLE_WERKZEUG_LOGGING` | `false` | Suppress Werkzeug access logs when `true`. |

Noisy third-party loggers (`redis_lock`, `urllib3`, `docker`, `kafka`) are raised to `WARNING` so they do not drown application events at `INFO`. Repeated identical Redis consumer errors are rate-limited (~30s) and report `suppressed_count` when they recur.

Example (`text`):

```text
2026-08-14 13:07:30 INFO [workload:vss-rtvi-cv] __main__ - Committing message id 1786623678634-0 component=workload
2026-08-14 13:07:30 INFO [router] run_workloads - http_request POST /v3/discovery:clusters status=200 elapsed_s=0.05 component=router
[envoy] [2026-08-14 13:07:30.401][1][info][upstream] cds: added/updated 0 cluster(s), skipped 5 unmodified cluster(s)
```

Filter muxed `docker logs` by source:

```bash
docker logs sdr-controller 2>&1 | grep '\[envoy\]'
docker logs sdr-controller 2>&1 | grep '\[router\]'
docker logs sdr-controller 2>&1 | grep '\[workload:'
docker logs sdr-controller 2>&1 | grep '\[workload:vss-rtvi-cv\]'
```

Example (`json`):

```json
{"timestamp":"2026-08-14T13:07:30.443Z","severity":"INFO","logger":"__main__","message":"Committing message id 1786623678634-0","component":"workload","workload":"vss-rtvi-cv","source":"workload:vss-rtvi-cv"}
```

---

## Building the Image

The production image is built from `envoy/Dockerfile.wdm-router`. This produces a single container that includes both the SDRC controller (compiled with PyInstaller as `/sdr-mw`) and the Envoy proxy.

```bash
# Build from repository root
docker build -f envoy/Dockerfile.wdm-router -t wdm-router .
```

**Build stages:**

1. **`pybase`** — Ubuntu Jammy, Python 3.10, `uv`, PyInstaller
2. Installs runtime dependencies via `uv sync --frozen`
3. Builds `sdr` and `sdr-mw` PyInstaller binaries
4. Installs Envoy from the official Envoy apt repository
5. Installs Lua 5.2 / LuaJIT with `luasocket`, `redis-lua`, `lua-cjson` (used by Envoy Lua filter for Redis lookups at routing time)

**Exposed ports:**

| Port | Purpose |
|---|---|
| `5002` | SDRC controller / router API |
| `9000` | Default per-workload Envoy listener (actual port depends on `WDM_MS_LISTENER_PORT`) |
| `9901` | Envoy admin |

**Entrypoint (`wdm-router-entrypoint.sh`):**

At container start:
1. Reads `WDM_WORKLOADS_CONFIG` (default `/config.yml`); exits if the file is missing.
2. Runs `generate_envoy_config_xds_mw.py` to generate `/tmp/envoy-sdrc-generated.yaml` from `config.yml`.
3. Starts `/sdr-mw` (the SDRC controller) in the background.
4. Starts `envoy` with the generated config.

**Required mount:**

```bash
docker run \
  -e WDM_WORKLOADS_CONFIG=/config.yml \
  -v "$PWD/config.yml:/config.yml:ro" \
  wdm-router
```

### Dependencies (`pyproject.toml`)

| Package | Version | Purpose |
|---|---|---|
| `Flask` | 3.1.3 | Web framework for coordinator API |
| `kubernetes` | 31.0.0 | Kubernetes API client |
| `redis` | 4.4.4 | Redis client for stream state |
| `kafka-python` | 2.3.0 | Kafka consumer |
| `envoy-data-plane` | 0.8.1 | Envoy xDS data structures |
| `docker` | 7.1.0 | Docker SDK (Docker mode) |
| `opentelemetry-*` | 1.27.0 | Distributed tracing |
| `prometheus_client` | 0.20.0 | Prometheus metrics |
| `Jinja2` | 3.1.4 | Envoy config template rendering |

---

## Development

### Local development (without Docker)

```bash
# Install dependencies using uv
uv sync

# Run the coordinator (workload 0 in config.yml)
python3 app.py

# Run the controller sub-service
python -m lib.controller
```

The coordinator starts on the `port` defined in the first enabled workload block. The controller sub-service starts on port `4001` by default.

### Running with a reference test app

The `testapp/` directory contains a reference Python worker application for integration testing. Start it before SDRC and reference it in `docker_cluster_config.json`.

### Preload file

To pre-provision streams at startup without waiting for bus events, set `WDM_PRELOAD_WORKLOAD` to a JSON file path. The file should contain an array of standard event envelopes.

---

## Directory Structure

```
services/sdrc/
├── app.py                          # Flask application entry point; imports all subsystems
├── config.py                       # All configuration via environment variables
├── config.yml                      # Reference workload config (placeholders for Redis/Kafka)
├── entrypoint.sh                   # Container entry: python3 app.py
├── run_workloads.py                # Multi-workload runner for SDRC deployments
├── pyproject.toml                  # Python dependencies (uv-managed)
├── uv.lock                         # Locked dependency tree
├── LICENSE                         # Apache-2.0
├── 3rdParty_Licenses.md            # Third-party license summaries
├── ThirdPartyLicences-notices.txt  # Third-party license notices
├── lib/
│   ├── controller/
│   │   ├── __init__.py             # SDR controller Flask service (multi-workload router)
│   │   └── __main__.py             # Entry: python -m lib.controller
│   ├── podprovisioner/
│   │   ├── provisionconfig.py      # Provisioning configuration and worker selection
│   │   ├── prerollconfigs.py       # Preload/pre-roll event handling
│   │   └── kubernetes/
│   │       ├── cluster.py          # Kubernetes cluster discovery and StatefulSet management
│   │       ├── k8sclient.py        # Kubernetes API client (in-cluster / out-of-cluster)
│   │       ├── k8sheadlessclient.py
│   │       └── dockerclient.py     # Docker SDK client (Docker mode)
│   ├── messaging/
│   │   ├── kafka.py                # Kafka consumer integration
│   │   ├── redisMessaging.py       # Redis stream consumer and publisher
│   │   └── redis_subscriber.py     # Redis pub/sub subscriber
│   ├── xDS/
│   │   ├── envoyxDS.py             # Envoy xDS REST CDS/RDS endpoint logic
│   │   └── grpc_xds_server.py      # Envoy gRPC xDS server (optional)
│   ├── parameters/
│   │   ├── configserver.py         # Config server integration
│   │   └── redisconfig.py          # Redis-backed workload configuration
│   ├── lifecycle/                  # HTTP header-based stream lifecycle management
│   ├── logging/
│   │   └── wdm_logging.py          # Structured logging configuration
│   ├── tracing.py                  # OpenTelemetry tracing setup
│   ├── client.py                   # SDRC HTTP client helpers
│   └── wdm_router_openapi.py       # OpenAPI document generation
├── envoy/
│   ├── Dockerfile.wdm-router       # Production image: SDRC + Envoy + Lua
│   ├── generate_envoy_config_xds_mw.py  # Config generator: config.yml → envoy YAML
│   ├── wdm-router-entrypoint.sh    # Container startup script
│   └── templates/
│       ├── envoy_config_xds_mw.yaml.j2  # Jinja2 template for Envoy static config
│       └── envoy_xds_mw.lua.j2          # Jinja2 template for Envoy Lua routing filter
├── kubernetes/
│   ├── helm/sdrc/                  # Helm chart for Kubernetes deployment
│   │   ├── Chart.yaml
│   │   ├── values.yaml
│   │   └── templates/
│   │       ├── deployment.yaml
│   │       ├── service.yaml
│   │       ├── configmap.yaml
│   │       └── rbac.yaml
│   └── test/config.yml             # Example config for Kubernetes test deployment
├── static/
│   ├── swagger.json                # Bundled workload coordinator Swagger (legacy)
│   ├── agent-swagger.json          # Agent API Swagger
│   └── controller-swagger.json     # Controller API Swagger (OpenAPI 3.0)
├── templates/
│   ├── dashboard.html              # Web dashboard template
│   └── swagger_ui_index.html       # Swagger UI template
└── docs/
    ├── sdrc-internal-external-component-flow.md
    ├── wdm-reorganization-design.md
    ├── http-header-lifecycle-srd.md
    ├── http-header-lifecycle-requirements-table.md
    └── header-based-stream-lifecycle-flow.md
```

---

## Security Notes

- The SDRC Flask APIs do not implement application-level authentication or rate limiting. Expose them only on trusted networks or behind an authenticated gateway.
- Treat `/reset`, `/apply_metadata_payload`, `/remove_stream`, `/cache_metadata_update`, and `/dashboard/global_add` as administrative mutation endpoints.
- `/dashboard/config_yml` returns the mounted workload configuration. Do not expose it where hostnames or topology should remain private.
- Envoy admin port should stay private to operators — it can expose runtime configuration and diagnostics.
- In Kubernetes, the mounted ServiceAccount token grants list/watch on pods/StatefulSets and patch on StatefulSet scale. Follow the principle of least privilege and scope the RBAC Role to the minimum required namespace.
