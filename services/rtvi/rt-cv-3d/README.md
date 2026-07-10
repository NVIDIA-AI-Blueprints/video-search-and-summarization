# RT-CV-3D Microservice

**RT-CV-3D** is a Perception microservice that couples the **RT-DETR** 2D
detector with the **Multi-View 3D Tracking (MV3DT)** framework to produce fused
3D Bird's Eye View (BEV) outputs across cameras with overlapping fields of view.

It consists of two components, shipped as two container images:

| Component | Container image | Role |
|---|---|---|
| **Perception** | `vss-rt-cv` | Runs the DeepStream pipeline: RT-DETR detection + MV3DT multi-view 3D tracking per camera. Publishes per-sensor 3D measurements to Kafka (`mdx-raw`) and exchanges cross-camera tracklets over MQTT (`/trck/<cam>`). |
| **BEV Fusion** | `vss-rt-cv-mv3dt-bev-fusion` | Consumes `mdx-raw`, buckets per-sensor measurements by timestamp, merges same-object measurements across camera views, and publishes fused BEV tracks to Kafka (`mdx-bev`). |


## Docker images

| Image | Tags (release) | Source / build |
|---|---|---|
| `nvcr.io/nvidia/vss-core/vss-rt-cv` | `3.2.0` (x86 default), `3.2.0-sbsa` (DGX Spark / SBSA) | Same image as the RT-CV microservice. To build locally, follow the **"Build Docker images"** section of the [RT-CV README](../rt-cv/README.md#build-docker-images-sbsa-arm-x86) (SBSA / ARM / x86 Dockerfiles under `rt-cv/docker/`). |
| `nvcr.io/nvidia/vss-core/vss-rt-cv-mv3dt-bev-fusion` | `3.2.0` | Source lives in [rt-cv-bev-fusion/](rt-cv-bev-fusion); build per its [README](rt-cv-bev-fusion/README.md). |

To pull from NGC: `docker login nvcr.io`.

## Directory layout

| Path | Contents |
|---|---|
| [rt-cv-bev-fusion/](rt-cv-bev-fusion) | BEV Fusion component: source (`src/`), Dockerfile, and tests. |
| [rt-cv-mv3dt/](rt-cv-mv3dt) | Standalone deployment for RT-CV-3D: minimal Docker Compose, sample configs, and utility scripts (config generation from `calibration.json`, dynamic stream registration, Kafka metadata dump, BEV visualizer). |

