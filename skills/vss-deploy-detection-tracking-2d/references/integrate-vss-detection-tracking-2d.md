# Integration Reference: RT-CV

## Overview

RT-CV (Real-Time Video Intelligence CV) is the DeepStream-based perception service used by VSS for streaming 2D detection and tracking. The composable RT-DETR-based shape is: VIOS-supplied video inputs, RT-CV perception on port `9000`, metadata published into Kafka, and the shared ELK stack available for indexing and query. The authoritative upstream anchors are `references/deploy-vss-detection-tracking-2d.md`, `references/usecases.md`, `references/api-reference.md`, and `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`.

Use this service when the workflow requires per-frame bounding box metadata, class labels, and track IDs for people or other supported objects. This reference covers the RT-CV ingestion and metadata path for 2D object detection and multi-object tracking; downstream analytics such as behavior analytics are separate layers.

## Required Peer Services

- **VIOS** — required. RT-CV consumes RTSP or file-backed sources registered through the VIOS sensor path. In the current repo shape, `perception-2d-fusion` depends on `sensor-ms`, and `nvstreamer-2d-fusion` can publish the canonical warehouse sample videos as RTSP sources for the same path. Source: `developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml` and `integrate-vios-service.md`.
- **Kafka foundational infra** — required. RT-CV publishes frame metadata into the shared broker path consumed by the broader search/analytics stack. Source: `compose.yml`, the DeepStream config mounted by `ds-start.sh`, and the shared infra compose.
- **ELK** — required when detection metadata must be indexed and queryable. Elasticsearch stores the emitted metadata for query, and Kibana is the standard inspection surface. The Logstash / Elasticsearch path is provided by the shared ELK reference, not by extra RT-CV-owned services. Source: `integrate-elk.md`.

### Structured component_services (consumed by `vss-build-vision-agent` Step 4)

See `skills/vss-build-vision-agent/references/component-services-schema.md` for the schema.

```yaml
component_services:
  - key: nvstreamer-2d-fusion
    file: developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml
    role: Optional sample-video RTSP publisher for the canonical warehouse video set.
    required: false
  - key: perception-2d-fusion
    file: developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml
    role: RT-CV perception service using RT-DETR detection with multi-object tracking enabled.
```

The generated `allow-list.yml` must preserve those service keys exactly. In particular, include `perception-2d-fusion` for RT-CV inference; `perception-2d` is not an upstream compose service key and must not be used.

## Integration Interfaces

### Inputs

- **Method:** RTSP / file-backed video inputs via VIOS
  **Address:** VIOS registers the source, and RT-CV consumes it through the `sensor-ms` path. For the canonical sample-data path, `nvstreamer-2d-fusion` publishes the warehouse video set as RTSP sources.
  **Schema:** raw video stream input.
  **Authentication:** none in the default local deployment.

- **Method:** REST — RT-CV health, metrics, and stream lifecycle APIs
  **Endpoint base:** `http://<host>:9000/api/v1`
  **Operations:** `/ready`, `/live`, `/startup`, `/metrics`, `/stream/add`, `/stream/remove`, `/stream/get-stream-info`
  **Schema:** see `references/api-reference.md` and `references/usage-vss-detection-tracking-2d.md`.
  **Authentication:** none by default.

- **Method:** Model and sample-video assets
  **Path:** model and video resources mounted under `/opt/storage/`
  **Expected content:** an RT-DETR ONNX model and, for the canonical evaluation path, the warehouse sample set such as `nv-warehouse-4cams`.
  **Authentication:** NGC credentials are required on the host when pulling the staged defaults from `nvstaging`.

### Outputs

- **Method:** Kafka frame metadata
  **Topic / key:** per-frame RT-CV metadata topics used by the 2D RT-CV Kafka path. The payload includes bounding boxes, class labels, confidence, and track IDs.
  **Schema:** structured detection metadata emitted by RT-CV / DeepStream.
  **Trigger:** per frame while the pipeline is running.

- **Method:** Elasticsearch indexing
  **Path:** RT-CV metadata is routed into the shared Elasticsearch stack for query and inspection.
  **Schema:** index documents derived from the Kafka metadata stream.
  **Trigger:** continuous while Kafka-to-ELK ingestion is enabled.

- **Method:** REST metrics and stream status
  **Endpoint:** `http://<host>:9000/api/v1/metrics` and `.../stream/get-stream-info`
  **Schema:** JSON runtime metrics and stream state payloads.
  **Trigger:** on-demand query.

## API Schema

RT-CV exposes its public REST API under `http://<host>:9000/api/v1`. The authoritative endpoint shapes are documented in:

- `skills/vss-deploy-detection-tracking-2d/references/api-reference.md`
- `skills/vss-deploy-detection-tracking-2d/references/usage-vss-detection-tracking-2d.md`

The important RT-CV API categories are:

- Health: `/live`, `/ready`, `/startup`
- Stream management: `/stream/add`, `/stream/remove`, `/stream/get-stream-info`
- Runtime metrics: `/metrics`

## Environment Variables

| Variable | Purpose | Default | Required? |
|---|---|---|---|
| `VSS_RT_CV_IMAGE` | RT-CV image repository | `ghcr.io/nvidia-ai-blueprints/vss/vss-rt-cv` | Optional |
| `VSS_RT_CV_TAG` | RT-CV image tag used by the base `perception` service | `develop-latest` | Optional |
| `RT_CV_DEVICE_ID` | GPU device id reserved for RT-CV | `0` | Optional |
| `VSS_APPS_DIR` | Root used to resolve the RT-CV compose and config mounts | — | **Yes** |
| `VSS_DATA_DIR` | Root used for model, cache, and sample-video mounts | — | **Yes** |
| `VISION_ENCODER_MODEL` | Vision encoder downloaded by ds-start phase 0 | `siglip_v2` | Optional |
| `VISION_ENCODER_VERSION` | Vision encoder version downloaded by ds-start phase 0 | `v1.1` | Optional |
| `NGC_CLI_API_KEY` | NGC credential used by ds-start phase 0 for model downloads | — | **Yes** for model downloads |
| `NVSTREAMER_IMAGE_TAG` | Image tag for the optional sample-data publisher | deployment-specific | Optional |
| `NVSTREAMER_HTTP_PORT` | HTTP API port for `nvstreamer-2d-fusion` | deployment-specific | Optional |
| `STREAM_TYPE` | Broker-path selector used by the 2D RT-CV Kafka/ELK path | deployment-specific | **Yes (effective)** |

For the standalone RT-CV operator flow, the deploy skill also uses `~/rtvicv-storage`, `~/.ngc/config`, and local model/video overrides; see `references/deploy-vss-detection-tracking-2d.md` and `references/deploy-defaults.yml`.

## Network Requirements

- **Ports exposed**
  - RT-CV REST API: `9000`
  - NvStreamer HTTP API: `${NVSTREAMER_HTTP_PORT}` when the optional sample publisher is included
- **Inbound traffic**
  - Operators or peer services call RT-CV on `:9000`
  - VIOS and optional sample publishers expose RTSP / HTTP sources RT-CV consumes through the sensor path
- **Outbound traffic**
  - RT-CV publishes metadata into the shared broker / Elasticsearch path
  - The host reaches NGC when staging canonical models and example video sets
- **DNS / hostname assumptions**
  - The current 2D RT-CV compose path uses `network_mode: host`
  - Local peers are addressed through host ports or `localhost`
- **`network_mode`**
  - `host` for `nvstreamer-2d-fusion` and the base RT-CV perception service path

## Known Integration Constraints

- **This is the 2D ingestion and metadata path, not the analytics bundle.** This reference covers RT-CV detection and tracking with Kafka/ELK outputs. Behavior analytics, video-analytics API, and 3D fusion are optional higher layers that require their own integration references before `vss-build-vision-agent` can compose them.
- **The warehouse sample flow requires data artifact access.** The warehouse sample video set comes from `nvidia/vss-warehouse/vss-warehouse-app-data:<version>`. RT-DETR and vision encoder models are downloaded automatically by ds-start phase 0. Local overrides can smoke-test the service but do not reproduce the intended warehouse evaluation set.
- **Person filtering is a deployment choice layered on the RT-DETR pipeline.** The model family is `rtdetr-warehouse`; the generated deployment should surface person-only filtering through config or env when the requested workflow requires it.
- **The upstream compose anchor is not yet a standalone RT-CV slice.** The current component services live under `developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`, so generation must patch a local copy rather than treating that path as an existing search deployment. A smaller RT-CV-owned compose slice would make capability composition cleaner.
- **Kafka and Elasticsearch contracts are still shared-stack contracts.** RT-CV publishes metadata into the shared broker path, and indexing depends on the ELK/Logstash reference. If downstream consumers require a stable RT-CV-specific index or topic schema, that contract needs to be captured explicitly in the RT-CV and ELK integration references.
- **First-run engine build cost.** RT-DETR engine generation typically takes several minutes on the first run before steady-state startup.
- **File-backed sample runs can exit on EOS.** A successful sample-data run can become `ready`, process frames, and then exit cleanly when the example videos finish.

## Example Compose Snippet

```yaml
services:
  perception-2d-fusion:
    extends:
      file: $VSS_APPS_DIR/services/rtvi/rtvi-cv/compose.yaml
      service: perception
    profiles:
      - bp_generated_rt_cv_person_detection_rtdetr
    container_name: vss-rtvi-cv
    volumes:
      - $VSS_APPS_DIR/developer-profiles/dev-profile-search/video-analytics-2d-app/deepstream/configs/:/opt/ds-configs-ro:ro
      - $VSS_APPS_DIR/services/rtvi/rtvi-cv/ds-start.sh:/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/ds-start.sh:ro
      - $VSS_DATA_DIR/models/:/opt/storage/
    command:
      - bash
      - -c
      - /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/ds-start.sh
    environment:
      DS_MODE_FLAG: "1"
      DS_MODEL_FAMILY: rtdetr-warehouse
      DS_TRACKER_REID: "true"
      DS_SHOW_SENSOR_ID: "false"
    depends_on:
      sensor-ms:
        condition: service_started
      broker-health-check:
        condition: service_completed_successfully
```
