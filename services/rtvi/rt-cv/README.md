# Standalone Testing Guide for RTVI CV Microservice

Run any supported model inside the RTVI CV microservice Docker container. Config files for each model are provided in the `reference-configs` directory inside the container — you pull the ONNX model from NGC, point the configs at it, set your batch size, and launch.

**Supported models:** [Warehouse 2D](#warehouse-2d) | [Warehouse 3D](#warehouse-3d) | [Smart City](#smart-city) (RT-DETR + GDINO)

**How streaming works:** All configs use dynamic stream addition by default. The pipeline starts with zero streams and exposes a REST server at `http://localhost:9000`. After the app is running, you add streams (up to `max-batch-size`, default 4) via the [stream add API](#dynamic-stream-management). Use the model-specific video/RTSP paths listed in each section as the `camera_url`.

> **Note:** Two kinds of `<PLACEHOLDER>` tokens appear in this guide:
>
> - **In-config tokens** (`<PATH_TO_ONNX_MODEL>`, `<PATH_TO_ENGINE_FILE>`, `<PATH_TO_LABELS_FILE>`, `<PATH_TO_ANCHOR_FILE>`, `<PATH_TO_REID_ONNX>`, `<PATH_TO_REID_ENGINE>`) appear inside the shipped config files. Point them at wherever your model assets live on disk — they don't have to be under `/opt/storage/resources/`, any path the container can read works.
> - **Guide tokens** (`<RTVI_CV_IMAGE>`, `<WAREHOUSE_APP_DATA_NGC>`, `<WAREHOUSE_APP_DATA_DIR>`, `<WAREHOUSE_RTDETR_ONNX>`, …) appear in the NGC download commands and example paths below. Substitute them with the docker image tag, NGC resource reference, extracted directory name, and ONNX filename from your release notes.

## Placeholders

### In-config tokens (inside the shipped config files)

Set these to the absolute paths of your model assets on disk. Any readable path works — no requirement to place them under `/opt/storage/resources/`.

| Token | Used in | Description |
|---|---|---|
| `<PATH_TO_ONNX_MODEL>` | warehouse-2d, warehouse-3d, smartcity-rtdetr PGIE configs | Absolute path to the detector ONNX |
| `<PATH_TO_ENGINE_FILE>` | same as above (optional for 2D/RT-DETR, required for Sparse4D) | Absolute path to the TRT engine file |
| `<PATH_TO_LABELS_FILE>` | warehouse-3d `config.yaml` | Absolute path to Sparse4D `labels.txt` |
| `<PATH_TO_ANCHOR_FILE>` | warehouse-3d `config.yaml` | Absolute path to Sparse4D anchor `.npy` |

### Guide tokens (used in this README's commands and example paths)

| Token | Description |
|---|---|
| `<RTVI_CV_IMAGE>` | Full RTVI-CV docker image reference, e.g. `nvcr.io/<org>/<repo>:<tag>` |
| `<RTVI_CV_IMAGE_SBSA>` | Same as above but with the `-sbsa-` tag variant for SBSA platforms |
| `<WAREHOUSE_APP_DATA_NGC>` | Warehouse NGC resource (`org/team/resource:version`) |
| `<WAREHOUSE_APP_DATA_DIR>` | Extracted warehouse directory under `/opt/storage/resources/` |
| `<WAREHOUSE_RTDETR_ONNX>` | Warehouse RT-DETR ONNX filename |
| `<SPARSE4D_ONNX>` | Sparse4D ONNX filename |
| `<SPARSE4D_ANCHOR>` | Sparse4D anchor .npy filename |
| `<SMARTCITY_APP_DATA_NGC>` | Smart-city videos NGC resource |
| `<SMARTCITY_APP_DATA_DIR>` | Extracted smart-city directory under `/opt/storage/resources/` |
| `<RTDETR_MODEL_NGC>` | TrafficCamNet RT-DETR NGC model reference |
| `<RTDETR_MODEL_DIR>` | Extracted RT-DETR model directory |
| `<RTDETR_ONNX>` | TrafficCamNet RT-DETR ONNX filename |
| `<GDINO_MODEL_NGC>` | Grounding DINO NGC model reference |
| `<GDINO_MODEL_DIR>` | Extracted GDINO model directory |
| `<GDINO_ONNX>` | GDINO ONNX filename |
| `<N>` | Batch size / max stream count |

---

## Table of Contents

- [Build Docker images (SBSA, Arm, x86)](#build-docker-images-sbsa-arm-x86)
- [Start the Docker Container](#start-the-docker-container)
- [In-container sanity test (30 sources)](#in-container-sanity-test-30-sources)
- [Docker Compose with RT-CV and Kafka](#docker-compose-with-rt-cv-and-kafka)
- [Warehouse 2D](#warehouse-2d)
- [Warehouse 3D](#warehouse-3d)
- [Smart City](#smart-city) — RT-DETR + GDINO
- [Dynamic Stream Management](#dynamic-stream-management)
- [Visualization](#visualization)
- [Static Sources (filesrc, RTSP)](#static-sources-filesrc-rtsp)

---

## Build Docker images (SBSA, Arm, x86)

Build local images from the Dockerfiles under `docker/`. Run these from the directory that contains `docker/` (this README’s folder).

If you are at the root of your cloned repository, use a relative path to enter the `services/rtvi/rt-cv` directory:

```bash
cd /path/to/services/rtvi/rt-cv
```

**SBSA** (ARM64 server / Spark-style targets):

```bash
docker build --platform linux/arm64 -f docker/sbsa.Dockerfile -t rtvi-cv:3.2.1-custom-sbsa .
```

**ARM** (aarch64 / Jetson-style; uses `docker/aarch64.Dockerfile`):

```bash
docker build --platform linux/arm64 -f docker/aarch64.Dockerfile -t rtvi-cv:3.2.1-custom-aarch64 .
```

**x86**:

```bash
docker build -f docker/x86.Dockerfile -t rtvi-cv:3.2.1-custom-x86 .
```

Adjust image tags (`rtvi-cv:3.2.1-custom-*`) if you need a different version label. For cross-architecture builds, ensure Docker Buildx and a suitable builder are available.

---

## Start the Docker Container

Pull and launch the VSS RT-CV container. Everything you need (DeepStream libraries, custom parsers) is pre-installed. Reference config files for each model are provided in the `reference-configs` directory.

### x86 / aarch64 (multi-arch)

```bash
sudo docker run --name=perception_docker --network=host \
  --gpus "device=0" --shm-size=6g \
  -v $HOME/rtvicv-storage:/opt/storage \
  -it --user root --rm \
  <RTVI_CV_IMAGE>
```

> Replace `--gpus "device=0"` with `--runtime=nvidia` for aarch64 devices

### SBSA (Spark)

```bash
sudo docker run --name=perception_docker --network=host \
  --gpus "device=0" --privileged --shm-size=6g \
  -v $HOME/rtvicv-storage:/opt/storage \
  -it --user root --rm \
  <RTVI_CV_IMAGE_SBSA>
```

> Replace `device=0` with your target GPU index.
> The `-v` mount persists downloaded models and engines across container restarts — `~/rtvicv-storage` on the host maps to `/opt/storage` inside the container.

### Thor (Jetson) — clock boost before benchmarking

Before running benchmarks on Jetson Thor, boost the CPU/GPU and VIC clocks on the **host** (outside the container):

```bash
sudo nvpmodel -m 0
sudo jetson_clocks

sudo su
echo performance > /sys/class/devfreq/8188050000.vic/governor
```

---

## In-container sanity test (30 sources)

Use this **inside** a running RT-CV container to smoke-test the GPU pipeline with **30 file sources** and DeepStream sample assets. It does **not** use Docker Compose or the host-side `tests/test-scripts/` suite (that stack targets Kafka + REST instead).

| Path (in repo) | Path (in container, after `docker/x86.Dockerfile` build) |
| -------------- | -------------------------------------------------------- |
| `tests/run-sanity.sh` | `.../metropolis_perception_app/tests/run-sanity.sh` |
| `tests/configs/source-30-config.txt` | `.../metropolis_perception_app/tests/configs/source-30-config.txt` |

App root inside the image:

```text
/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/
```

### What the sanity run does

`tests/run-sanity.sh` launches:

```bash
./metropolis_perception_app -c tests/configs/source-30-config.txt
```

`source-30-config.txt` is a fixed **30-stream** workload (two URI sources × 15 each) using the bundled sample video `sample_1080p_h264.mp4`, **TrafficCamNet** primary inference (`config_infer_primary.txt` under DeepStream `samples/`), **FakeSink** (no display), and tiled OSD. Paths point at DeepStream 9.0 sample streams/models shipped in the RT-CV image.

On the **first** run, TensorRT may build the primary detector engine; allow several minutes before expecting steady FPS logs.

### Prerequisites

- A GPU-enabled RT-CV container shell (see [Start the Docker Container](#start-the-docker-container)).
- For the script and config on disk, use an image built from this repo (copies `tests/` into the app tree), for example:

  ```bash
  docker build -f docker/x86.Dockerfile -t rtvi-cv:3.2.1-custom-x86 .
  ```

  Stock NGC images may not include `tests/run-sanity.sh` unless they were built the same way.

### Run from inside the container

1. Start an interactive container (example x86):

   ```bash
   sudo docker run --name=perception_docker --network=host \
     --gpus "device=0" --shm-size=6g \
     -v $HOME/rtvicv-storage:/opt/storage \
     -it --user root --rm \
     rtvi-cv:3.2.1-custom-x86
   ```

2. Inside the container:

   ```bash
   cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app
   chmod +x tests/run-sanity.sh   # only if needed
   ./tests/run-sanity.sh
   ```

3. **Success:** the app stays up, decodes and infers on 30 sources, and prints periodic FPS / perf lines (no crash, no immediate exit with a GStreamer error). Press **Ctrl+C** to stop.

4. **Optional:** pass extra arguments through to the binary:

   ```bash
   ./tests/run-sanity.sh
   # equivalent to:
   ./metropolis_perception_app -c tests/configs/source-30-config.txt
   ```

### One-shot from the host (no interactive shell)

```bash
sudo docker run --rm --network=host \
  --gpus "device=0" --shm-size=6g \
  rtvi-cv:3.2.1-custom-x86 \
  bash -lc 'cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app && ./tests/run-sanity.sh'
```

Stop the container with **Ctrl+C** when you have seen stable processing.

### Relation to Docker Compose tests

| Test | Where it runs | Config | Purpose |
| ---- | ------------- | ------ | ------- |
| `tests/run-sanity.sh` | **Inside** RT-CV container | `source-30-config.txt` | 30 static file sources, inference smoke test |
| `tests/test-scripts/*.sh` | **Host** (calls `docker compose`) | `source_nvmultiurisrcbin_kafka.compose.txt` | Kafka broker, REST add/remove stream, health probes |

Do not run `run-sanity.sh` in the same container process that Compose already uses for the Kafka pipeline unless you have stopped that service or started a separate one-off container.

### Troubleshooting

- **Missing script or config** — rebuild with `docker/x86.Dockerfile` (or mount the repo `tests/` tree into `.../metropolis_perception_app/tests/`).
- **Engine build / CUDA errors** — confirm `--gpus` and a large enough GPU for `batch-size=30` in `source-30-config.txt`; reduce batch sizes in the config only if you intentionally want a lighter test.
- **Cannot open sample URI** — verify the sample file exists in the image:  
  `ls /opt/nvidia/deepstream/deepstream/samples/streams/sample_1080p_h264.mp4`

---

## Docker Compose with RT-CV and Kafka

This path runs **RT-CV** (`metropolis_perception_app`) and **Apache Kafka** on a Docker bridge network so you can exercise **dynamic stream addition** (REST on port **9000**), **Kafka message broker** output, and **health probes** without `--network=host`. For a **30-source in-container smoke test** (no Kafka), see [In-container sanity test (30 sources)](#in-container-sanity-test-30-sources).

| Path | Role |
| ---- | ---- |
| `tests/docker-compose/compose.yaml` | Stack: `kafka` + `rt-cv` (GPU), mounts `tests/configs/` read-only |
| `tests/docker-compose/.env.example` | Template; copy to `.env` and tune image, GPU, topic, timeouts |
| `tests/configs/source_nvmultiurisrcbin_kafka.compose.txt` | Pipeline: `use-nvmultiurisrcbin=1`, `http-ip=0.0.0.0`, Kafka sink to `kafka:9092`, topic `ds-perception` |
| `tests/test-scripts/` | Host-side integration tests (see table below) |

`tests/` layout:

```text
tests/
├── run-sanity.sh                          # in-container 30-source smoke test
├── configs/
│   ├── source-30-config.txt               # used by run-sanity.sh
│   └── source_nvmultiurisrcbin_kafka.compose.txt   # used by compose rt-cv service
├── docker-compose/
│   ├── compose.yaml
│   └── .env.example                       # copy to .env
└── test-scripts/
    ├── deploy.sh
    ├── health-test.sh
    ├── add-stream-test.sh
    ├── kafka-messages-test.sh
    ├── remove-stream-test.sh
    ├── run-all-tests.sh
    └── lib/common.sh                      # shared helpers (not run directly)
```

### Prerequisites

- **Docker Compose** (v2) and **NVIDIA Container Toolkit** so the `rt-cv` service can use `deploy.resources.reservations.devices` (GPU).
- **Images**: Compose uses `pull_policy: if_not_present` so existing local images are reused. Pull when your registry allows (HTTP **429** rate limits are common on Docker Hub / NGC):
  - `docker pull apache/kafka:3.9.0` (or set `KAFKA_IMAGE` in `.env` to a tag you already have, e.g. `apache/kafka:4.1.1`).
  - `ngc registry login` then `docker pull --platform linux/amd64 <RTVI_CV_IMAGE>` for the perception image, or build from this repo: `docker build -f docker/x86.Dockerfile -t rtvi-cv:3.2.1-custom-x86 .` and set `RTVI_CV_IMAGE` accordingly.
- **x86 hosts**: Set `RTVI_CV_PLATFORM=linux/amd64` in `.env` so Docker does not select an `arm64` image manifest by mistake.

### Quick start

From the `services/rtvi/rt-cv` directory (this README’s folder):

```bash
cd tests/docker-compose
cp .env.example .env
# Edit .env: RTVI_CV_IMAGE, RTVI_CV_PLATFORM, NVIDIA_VISIBLE_DEVICES, KAFKA_IMAGE as needed

../test-scripts/run-all-tests.sh   # deploy + health + add stream + Kafka + remove stream
```

Deploy only, or run tests with the stack already up:

```bash
../test-scripts/deploy.sh
SKIP_DEPLOY=1 ../test-scripts/run-all-tests.sh
```

### Configuration (`.env`)

| Variable | Typical value | Notes |
| -------- | ------------- | ----- |
| `KAFKA_IMAGE` | `apache/kafka:3.9.0` | Public image; Bitnami’s `bitnami/kafka` tags are often unavailable on Docker Hub |
| `RTVI_CV_IMAGE` | NGC tag or `rtvi-cv:3.2.1-custom-x86` | Must match your platform |
| `RTVI_CV_PLATFORM` | `linux/amd64` | Recommended on x86 |
| `NVIDIA_VISIBLE_DEVICES` | `0` | GPU index inside the container |
| `KAFKA_TOPIC` | `ds-perception` | Must match `topic=` / broker string in `source_nvmultiurisrcbin_kafka.compose.txt` |
| `REST_URL` | `http://localhost:9000` | Host URL for REST and health tests |
| `TEST_CAMERA_ID` / `TEST_STREAM_FILE` | `camera_0` / `sample_1080p_h264.mp4` | Used by add/remove stream tests (`file://` under `STREAMS_DIR` in container) |
| `PIPELINE_WARMUP_SEC` | `45` | Sleep in `run-all-tests.sh` after add-stream before Kafka consume |
| `CONSUME_TIMEOUT_SEC` / `MIN_MESSAGES` | `90` / `1` | Kafka consumer idle timeout and minimum message count |
| `REST_TIMEOUT_SEC` | `300` | Max wait for REST in `deploy.sh` / stream tests |
| `SKIP_DEPLOY` | *(unset)* | Set to `1` when invoking `run-all-tests.sh` if compose is already up |

### Manual compose (without test scripts)

```bash
cd tests/docker-compose
docker compose up -d
docker compose ps
docker compose logs -f rt-cv
```

Ports published to the host: **9000** (REST / health), **9092** (Kafka, optional for host-side tools).

### Integration test scripts

Run from **`tests/docker-compose/`** (or any path; scripts `cd` to compose via `lib/common.sh`). All test scripts exit **0** on success and **1** on failure; they print `PASS:` or `FAIL:`.

| Script | What it does | Pass criteria |
| ------ | ------------ | ------------- |
| `test-scripts/deploy.sh` | `docker compose up -d`; wait for Kafka healthy and REST | Kafka healthy + REST reachable |
| `test-scripts/health-test.sh` | **GET** `/api/v1/live`, `/ready`, `/startup` | Each returns **HTTP 200** with JSON `status` |
| `test-scripts/add-stream-test.sh` | **POST** `/api/v1/stream/add` with sample `file://` URI | **HTTP 200** |
| `test-scripts/kafka-messages-test.sh` | `kafka-console-consumer` on `KAFKA_TOPIC` | ≥ `MIN_MESSAGES` non-empty lines |
| `test-scripts/remove-stream-test.sh` | **POST** `/api/v1/stream/remove` for `TEST_CAMERA_ID` | **HTTP 200** (run after add-stream) |
| `test-scripts/run-all-tests.sh` | Full suite in order (below) | All steps pass |

`run-all-tests.sh` order:

1. `deploy.sh` (skipped if `SKIP_DEPLOY=1`)
2. `health-test.sh`
3. `add-stream-test.sh`
4. Sleep `PIPELINE_WARMUP_SEC`
5. `kafka-messages-test.sh`
6. `remove-stream-test.sh`

Run a single test:

```bash
cd tests/docker-compose
cp .env.example .env   # first time only
../test-scripts/health-test.sh
../test-scripts/add-stream-test.sh
```

### Health and monitoring endpoints

The app exposes these **GET** routes (used by `health-test.sh`):

| Endpoint | Purpose |
| -------- | ------- |
| `/api/v1/live` | Liveness probe |
| `/api/v1/ready` | Readiness probe |
| `/api/v1/startup` | Startup probe |

Example:

```bash
curl -sS "http://localhost:9000/api/v1/ready" | head -c 400
```

### Streams and Kafka metadata

- Streams are added with **`file://`** URLs that resolve **inside the RT-CV container** (DeepStream sample paths, e.g. `/opt/nvidia/deepstream/deepstream/samples/streams/...`).
- The compose pipeline config is `tests/configs/source_nvmultiurisrcbin_kafka.compose.txt` (Kafka broker host **`kafka`**, not `localhost`, on the compose network).

### Troubleshooting

- **HTTP 429 on image pull** — Compose uses `pull_policy: if_not_present`; pull images when the registry allows, or build/set `RTVI_CV_IMAGE` locally (see Prerequisites).
- **Kafka**: `advertised.listeners cannot use the nonroutable meta-address 0.0.0.0` — use the checked-in `compose.yaml` (Apache Kafka listeners must not advertise `0.0.0.0`).
- **No Kafka messages** — Confirm `rt-cv` is running, run `add-stream-test.sh` first, increase `PIPELINE_WARMUP_SEC` / `CONSUME_TIMEOUT_SEC`, check `docker compose logs rt-cv`.
- **remove-stream-test fails** — Run `add-stream-test.sh` first (same `TEST_CAMERA_ID`).
- **Stale Kafka KRaft data** — `docker compose down -v` in `tests/docker-compose/`, then redeploy.

---

You are now inside the container. All remaining steps are run here.

**Configure NGC CLI** (required before downloading models):

```bash
mkdir -p /opt/storage/resources
ngc config set --serverurl https://api.ngc.nvidia.com
# Enter your NGC API key and org when prompted
```

## Warehouse 2D

2D object detection for warehouse use cases using RT-DETR with NvDCF tracker. Detects 7 classes: Person, Agility\_Digit\_Humanoid, Fourier\_GR1\_T2\_Humanoid, Nova\_Carter, Transporter, Forklift, Pallet.

### Config files

The reference configs are located at:

```text
reference-configs/warehouse-2d/
├── ds-main-config.txt                     # Main pipeline config
├── ds-pgie-config.yml                     # nvinfer PGIE config (RT-DETR, YAML)
├── ds-detector-labels.txt                 # 7 classes
├── ds-nvdcf-accuracy-tracker-config.yml   # NvDCF tracker config
├── ds-kafka-config.txt                    # Kafka broker config
└── ds-redis-config.txt                    # Redis config
```

Set a shorthand:

```bash
export CONFIGS=/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/reference-configs
```

### 1. Pull NGC Resources

Download the NGC resource into `/opt/storage/resources`:

```bash
cd /opt/storage/resources

ngc registry resource download-version <WAREHOUSE_APP_DATA_NGC>

cd <WAREHOUSE_APP_DATA_DIR>
tar -xvf *.tar.gz
```

| Asset | Path |
| ----- | ---- |
| **ONNX model** | `/opt/storage/resources/<WAREHOUSE_APP_DATA_DIR>/vss-warehouse-app-data/models/mtmc/<WAREHOUSE_RTDETR_ONNX>` |
| **Test videos** | `/opt/storage/resources/<WAREHOUSE_APP_DATA_DIR>/vss-warehouse-app-data/videos/nv-warehouse-4cams/` |

### 2. Update Configs (optional)

The default configs are pre-configured for up to **4 streams** (batch-size=4) with dynamic stream addition via the REST API. If you just want a quick test, **skip this step** — download the resource and go straight to [Run](#3-run).

Follow this section if you need to change the maximum number of streams. The default is 4; modify all values below to match your desired limit.

**PGIE config** (`$CONFIGS/warehouse-2d/ds-pgie-config.yml`) — set `onnx-file` to the absolute path of your ONNX on disk. `model-engine-file` is optional: if omitted, DeepStream auto-builds the engine next to the ONNX on first run and reuses it on every subsequent run.

```yaml
property:
  onnx-file: <PATH_TO_ONNX_MODEL>
  # Optional — uncomment only if pointing at a pre-built engine in a different location:
  # model-engine-file: <PATH_TO_ENGINE_FILE>
```

> **Example** — if you pulled the warehouse NGC resource into `/opt/storage/resources/` and want to use the RT-DETR ONNX from it:
>
> `onnx-file: /opt/storage/resources/<WAREHOUSE_APP_DATA_DIR>/vss-warehouse-app-data/models/mtmc/<WAREHOUSE_RTDETR_ONNX>`

**Main pipeline config** (`$CONFIGS/warehouse-2d/ds-main-config.txt`) — batch-size touch points:

| Section / Key | Set to |
| ------------- | ------ |
| `[streammux] batch-size` | N |
| `[primary-gie] batch-size` | N |
| `[source-list] max-batch-size` | N |

### 3. Run

> **Note:** Default uses `type=1` (FakeSink) — no display required. The TensorRT engine is built automatically on first run from the ONNX model. Subsequent runs reuse the cached engine.

```bash
cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app

./metropolis_perception_app -c reference-configs/warehouse-2d/ds-main-config.txt
```

---

## Warehouse 3D

3D object detection for warehouse use cases using **Sparse4D** (multi-camera BEV perception). Detects 6 classes: Person, Fourier\_GR1\_T2\_Humanoid, Agility\_Digit\_Humanoid, Nova\_Carter, Transporter, Forklift. Unlike Warehouse 2D, this pipeline uses a custom `videotemplate` plugin (`libnvdsgst_sparse4d.so`) with a dedicated preprocessing stage instead of `nvinfer`.

> **Important:** Sparse4D requires extra setup steps before running — you must copy the model config into the sparse4d source directory, set environment variables, and generate a TensorRT engine via `sparse4d_setup.sh`.

### Config files

```text
reference-configs/warehouse-3d/
├── ds-main-config.txt                          # Main pipeline config
├── config.yaml                                 # Sparse4D model config (inference, calibration, preprocessing)
├── calibration.json                            # Camera calibration (extrinsics/intrinsics)
├── ds-mtmc-preprocess-config.txt               # nvdspreprocess config
├── ds-mtmc-videotemplate_custom_lib_config.txt  # videotemplate (sparse4d plugin) config
├── ds-kafka-config.txt                         # Kafka broker config
└── ds-redis-config.txt                         # Redis config
```

### 1. Pull NGC Resources

Download the NGC resource into `/opt/storage/resources` (same resource as Warehouse 2D — it includes videos for both 2D and 3D):

```bash
cd /opt/storage/resources

ngc registry resource download-version <WAREHOUSE_APP_DATA_NGC>

cd <WAREHOUSE_APP_DATA_DIR>
tar -xvf *.tar.gz
```

### 2. Set Environment Variables

These variables are required for every terminal session — both when generating the TensorRT engine and when running the app. The `LD_PRELOAD` loads the MSDA custom TensorRT plugin (`libmsda_fp16.so`) which Sparse4D depends on at engine build time and at inference time. Without it, the engine will fail to build or deserialize.

```bash
export CONFIGS=/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/reference-configs
export SPARSE4D_REPO=/opt/nvidia/deepstream/deepstream/sources/sparse4d
export LD_PRELOAD=$SPARSE4D_REPO/libmsda_fp16.so
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$SPARSE4D_REPO:/usr/local/lib/python3/dist-packages/torch/lib
```

### 3. Update Configs

**Sparse4D model config** (`$CONFIGS/warehouse-3d/config.yaml`) — set the four asset paths to absolute locations on disk. Unlike warehouse-2d, `engine_file` is **required** here (not optional) because `sparse4d_setup.sh` writes the TRT engine to that exact path and the videotemplate plugin loads it from there.

```yaml
onnx_file: <PATH_TO_ONNX_MODEL>
engine_file: <PATH_TO_ENGINE_FILE>
labels_file: <PATH_TO_LABELS_FILE>
anchor: <PATH_TO_ANCHOR_FILE>
num_sensors: 4
```

> **Example** — if you pulled the warehouse NGC resource and want the engine cached at `/opt/storage/engines/`:
>
> ```yaml
> onnx_file: /opt/storage/resources/<WAREHOUSE_APP_DATA_DIR>/vss-warehouse-app-data/models/sparse4d/ov/<SPARSE4D_ONNX>
> engine_file: /opt/storage/engines/sparse4d_b4.engine
> labels_file: /opt/storage/resources/<WAREHOUSE_APP_DATA_DIR>/vss-warehouse-app-data/models/sparse4d/ov/labels.txt
> anchor: /opt/storage/resources/<WAREHOUSE_APP_DATA_DIR>/vss-warehouse-app-data/models/sparse4d/ov/<SPARSE4D_ANCHOR>
> ```
>
> The `_b4` suffix in `engine_file` is the batch size — change it to match your `num_sensors` / max-batch-size (e.g. `sparse4d_b8.engine` for 8 streams).

**All batch-size touch points:**

| Config file | Key | Set to |
| ----------- | --- | ------ |
| `ds-main-config.txt` | `[streammux] batch-size` | N |
| `ds-main-config.txt` | `[source-list] max-batch-size` | N |
| `config.yaml` | `num_sensors` | N |
| `ds-mtmc-preprocess-config.txt` | `network-input-shape` | N;3;540;960 |

### 4. Generate TensorRT Engine

Copy the reference config into the sparse4d source directory and run the setup script to build the TensorRT engine:

```bash
cp $CONFIGS/warehouse-3d/config.yaml $SPARSE4D_REPO/configs/config.yaml
cp $CONFIGS/warehouse-3d/calibration.json $SPARSE4D_REPO/calibration.json

bash $SPARSE4D_REPO/configs/sparse4d_setup.sh
```

Engine generation takes a few minutes depending on GPU. Once built, the engine is cached and reused on subsequent runs.

### 5. Run

> **Note:** Default uses `type=1` (FakeSink) — no display required.

```bash
cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app

./metropolis_perception_app -c reference-configs/warehouse-3d/ds-main-config.txt
```

> **Important:** `config.yaml` is copied to `$SPARSE4D_REPO/configs/` in Step 4. If you modify `config.yaml` after the initial copy (e.g. enabling visualization, changing batch size, or updating paths), you must re-copy it before running:
>
> ```bash
> cp $CONFIGS/warehouse-3d/config.yaml $SPARSE4D_REPO/configs/config.yaml
> ```

---

## Smart City

Smart city / ITS detection with two models: **RT-DETR** (TrafficCamNet) and **GDINO** (Grounding DINO). Both share the same test videos and ReID tracker model. Pick one or run both.

| Model | Detection | Classes | Inference |
| ----- | --------- | ------- | --------- |
| **RT-DETR** | 2D object detection | 5 (background, bicycle, car, person, road\_sign) | nvinfer PGIE |
| **GDINO** | Open-vocabulary detection | Prompt-based | Triton nvinferserver (ensemble) |

### Config files

```text
reference-configs/smartcities/
├── rt-detr/
│   ├── run_config-api-rtdetr-protobuf.txt   # Main pipeline config
│   ├── rtdetr-960x544.txt                   # nvinfer PGIE config (INI-style)
│   ├── rtdetr-960x544-labels.txt            # 5 classes
│   ├── cfg_kafka.txt                        # Kafka broker config
│   └── coco_classmap.txt                    # COCO class mapping
└── gdino/
    ├── run_config-api-rtdetr-protobuf.txt            # Main pipeline config
    ├── config_triton_nvinferserver_gdino.txt          # Triton PGIE config
    ├── cfg_kafka.txt                                  # Kafka broker config
    └── coco_classmap.txt                              # COCO class mapping
```

Set shorthands:

```bash
export CONFIGS=/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app/reference-configs
export TRITON_REPO=/opt/nvidia/deepstream/deepstream/sources/TritonGdino/triton_model_repo
```

### 1. Pull NGC Resources (shared)

Download models, test videos, and tracker ReID model. Run once — both RT-DETR and GDINO use the same videos and tracker.

```bash
cd /opt/storage/resources

# RT-DETR model (TrafficCamNet)
ngc registry model download-version <RTDETR_MODEL_NGC>

# GDINO model (Mask Grounding DINO)
ngc registry model download-version <GDINO_MODEL_NGC>

# Smart city test videos (shared by both models)
ngc registry resource download-version <SMARTCITY_APP_DATA_NGC>

cd <SMARTCITY_APP_DATA_DIR>
tar -xvf *.tar.gz
cd /opt/storage/resources

# ReID model for NvDCF tracker (used by both models — stable, version-pinned URL)
mkdir -p /opt/nvidia/deepstream/deepstream/samples/models/Tracker/
wget 'https://api.ngc.nvidia.com/v2/models/nvidia/tao/reidentificationnet/versions/deployable_v1.0/files/resnet50_market1501.etlt' \
  -O /opt/nvidia/deepstream/deepstream/samples/models/Tracker/resnet50_market1501.etlt
```

| Asset | Path |
| ----- | ---- |
| **RT-DETR ONNX** | `/opt/storage/resources/<RTDETR_MODEL_DIR>/<RTDETR_ONNX>` |
| **GDINO ONNX** | `/opt/storage/resources/<GDINO_MODEL_DIR>/<GDINO_ONNX>` |
| **Test videos** | `/opt/storage/resources/<SMARTCITY_APP_DATA_DIR>/videos/smc-app/` |
| **ReID model** | `/opt/nvidia/deepstream/deepstream/samples/models/Tracker/resnet50_market1501.etlt` |

---

### RT-DETR

#### Update Configs (optional)

The default configs are pre-configured for 4 streams. Follow this if you need to change the batch size.

**PGIE config** (`$CONFIGS/smartcities/rt-detr/rtdetr-960x544.txt`) — set `onnx-file` to the absolute path of your ONNX on disk. `model-engine-file` is optional: if omitted, DeepStream auto-builds the engine next to the ONNX on first run and reuses it on every subsequent run.

```ini
[property]
onnx-file=<PATH_TO_ONNX_MODEL>
# Optional — uncomment only if pointing at a pre-built engine in a different location:
# model-engine-file=<PATH_TO_ENGINE_FILE>
batch-size=4
```

> **Example** — if you pulled the RT-DETR NGC model into `/opt/storage/resources/`:
>
> `onnx-file=/opt/storage/resources/<RTDETR_MODEL_DIR>/<RTDETR_ONNX>`

**Main pipeline config** (`$CONFIGS/smartcities/rt-detr/run_config-api-rtdetr-protobuf.txt`) — batch-size touch points:

| Section / Key | Set to |
| ------------- | ------ |
| `[streammux] batch-size` | N |
| `[primary-gie] batch-size` | N |
| `[source-list] max-batch-size` | N |

**PGIE config** (`rtdetr-960x544.txt`):

| Section / Key | Set to |
| ------------- | ------ |
| `[property] batch-size` | N |

#### Run

> **Note:** Default uses `type=1` (FakeSink) — no display required. Engine is built automatically on first run.

```bash
cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app

./metropolis_perception_app -c reference-configs/smartcities/rt-detr/run_config-api-rtdetr-protobuf.txt
```

---

### GDINO (Grounding DINO)

GDINO uses **Triton Inference Server** (nvinferserver) with an ensemble model. Unlike RT-DETR, you must copy the ONNX into the Triton model repo and build the TensorRT engine before running.

#### Copy ONNX and Build TensorRT Engine

```bash
mkdir -p $TRITON_REPO/gdino_trt/1/

# Copy ONNX into Triton model repo (substitute <GDINO_MODEL_DIR> / <GDINO_ONNX>)
cp /opt/storage/resources/<GDINO_MODEL_DIR>/<GDINO_ONNX> \
   $TRITON_REPO/gdino_trt/1/model.onnx

# Build TensorRT engine (default batch=4; replace 4 with <N> for a different batch size)
/usr/src/tensorrt/bin/trtexec \
  --onnx=$TRITON_REPO/gdino_trt/1/model.onnx \
  --minShapes=inputs:1x3x544x960,input_ids:1x256,attention_mask:1x256,position_ids:1x256,token_type_ids:1x256,text_token_mask:1x256x256 \
  --optShapes=inputs:4x3x544x960,input_ids:4x256,attention_mask:4x256,position_ids:4x256,token_type_ids:4x256,text_token_mask:4x256x256 \
  --maxShapes=inputs:4x3x544x960,input_ids:4x256,attention_mask:4x256,position_ids:4x256,token_type_ids:4x256,text_token_mask:4x256x256 \
  --fp16 \
  --useCudaGraph \
  --saveEngine=$TRITON_REPO/gdino_trt/1/model.plan
```

Engine build takes several minutes. Rebuild when changing batch size.

#### Update Configs (optional)

The default configs are pre-configured for 4 streams. Follow this if you need to change the batch size.

**Triton PGIE config** (`$CONFIGS/smartcities/gdino/config_triton_nvinferserver_gdino.txt`):

```text
infer_config {
  max_batch_size: 4
  ...
}
```

**Triton model repo** — update `max_batch_size` in **all four** `config.pbtxt` files:

```bash
# Default batch=4, replace 4 with your batch size if different
for model_dir in ensemble_python_gdino gdino_trt gdino_postprocess gdino_preprocess; do
  sed -i "s/max_batch_size:.*/max_batch_size: 4/" \
    "$TRITON_REPO/$model_dir/config.pbtxt"
done
```

**Main pipeline config** (`$CONFIGS/smartcities/gdino/run_config-api-rtdetr-protobuf.txt`) — batch-size touch points:

| Section / Key | Set to |
| ------------- | ------ |
| `[streammux] batch-size` | N |
| `[primary-gie] batch-size` | N |
| `[source-list] max-batch-size` | N |

**Triton configs** — all must be consistent:

| Config file | Key | Set to |
| ----------- | --- | ------ |
| `config_triton_nvinferserver_gdino.txt` | `max_batch_size` | N |
| `$TRITON_REPO/ensemble_python_gdino/config.pbtxt` | `max_batch_size` | N |
| `$TRITON_REPO/gdino_trt/config.pbtxt` | `max_batch_size` | N |
| `$TRITON_REPO/gdino_postprocess/config.pbtxt` | `max_batch_size` | N |
| `$TRITON_REPO/gdino_preprocess/config.pbtxt` | `max_batch_size` | N |

#### Run

> **Note:** Default uses `type=1` (FakeSink) — no display required.

```bash
cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app

./metropolis_perception_app -c reference-configs/smartcities/gdino/run_config-api-rtdetr-protobuf.txt
```

---

## Common Configuration

Applies to all models above.

### Dynamic Stream Management

All configs use `use-nvmultiurisrcbin=1` by default, which starts a REST server at `http://localhost:9000`. The app launches with **zero streams** — you add streams dynamically after the pipeline is running.

You can add up to `max-batch-size` streams (default 4). The `camera_url` can be a local file path (`file:///...`) or an RTSP URL (`rtsp://...`).

**Add a stream:**

```bash
curl -XPOST 'http://localhost:9000/api/v1/stream/add' -d '{
  "key": "sensor",
  "value": {
      "camera_id": "<unique_camera_id>",
      "camera_name": "<display_name>",
      "camera_url": "<file_or_rtsp_url>",
      "change": "camera_add",
      "metadata": {
          "resolution": "1920 x1080",
          "codec": "h264",
          "framerate": 30
      }
  },
  "headers": {
      "source": "vst",
      "created_at": "2021-06-01T14:34:13.417Z"
  }
}'
```

**Example** — adding a warehouse video stream (substitute `<WAREHOUSE_APP_DATA_DIR>` with your extracted NGC directory):

```bash
curl -XPOST 'http://localhost:9000/api/v1/stream/add' -d '{
  "key": "sensor",
  "value": {
      "camera_id": "Camera",
      "camera_name": "Camera",
      "camera_url": "file:///opt/storage/resources/<WAREHOUSE_APP_DATA_DIR>/vss-warehouse-app-data/videos/nv-warehouse-4cams/Camera.mp4",
      "change": "camera_add",
      "metadata": {
          "resolution": "1920 x1080",
          "codec": "h264",
          "framerate": 30
      }
  },
  "headers": {
      "source": "vst",
      "created_at": "2021-06-01T14:34:13.417Z"
  }
}'
```

Repeat with a different `camera_id` and `camera_url` for each additional stream (`Camera_01`, `Camera_02`, etc.), up to `max-batch-size`.

### Visualization

All configs default to `type=1` (FakeSink) — no display required. To visualize output on screen:

```bash
export DISPLAY=:0    # or :1, depending on your X11 setup
```

In the model's main config, make the following changes:

```ini
[sink0]
type=2

[osd]
enable=1

[tiled-display]
enable=1
```

**Pass `--tiledtext` to the app** so source names appear on each tile of the tiled display — without it, tiles have bounding boxes but no source labels:

```bash
./metropolis_perception_app -c reference-configs/<model>/<main-config> --tiledtext
```

| Sink | Recommended flags |
|------|-------------------|
| FakeSink (`type=1`, benchmark) | `-c <config>` |
| EglSink (`type=2`, display) | `-c <config> --tiledtext` |

**Sparse4D (Warehouse 3D) only:** additionally enable 3D bbox rendering in `config.yaml`:

```yaml
generate_3d_bbox: True
```

> After changing `config.yaml`, re-copy it: `cp $CONFIGS/warehouse-3d/config.yaml $SPARSE4D_REPO/configs/config.yaml`

### Static Sources (filesrc, RTSP)

By default all configs start with empty source lists and use [Dynamic Stream Management](#dynamic-stream-management) to add streams at runtime. If you prefer to launch the pipeline with pre-configured static sources instead, populate the source list directly in the model's main config.

**Local video files (filesrc):**

```ini
[source-list]
num-source-bins=4
list=file:///path/to/video1.mp4;file:///path/to/video2.mp4;file:///path/to/video3.mp4;file:///path/to/video4.mp4
sensor-id-list=cam1;cam2;cam3;cam4
sensor-name-list=cam1;cam2;cam3;cam4
max-batch-size=4
```

**RTSP streams:**

```ini
[source-list]
num-source-bins=4
list=rtsp://ip1:port/stream1;rtsp://ip2:port/stream2;rtsp://ip3:port/stream3;rtsp://ip4:port/stream4
sensor-id-list=cam1;cam2;cam3;cam4
sensor-name-list=cam1;cam2;cam3;cam4
max-batch-size=4
```

Set `num-source-bins`, `max-batch-size`, and all other batch-size touch points (see per-model tables) to match your stream count.
