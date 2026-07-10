# RT-CV-3D Standalone Deployment

Sample configs, utility scripts, and a minimal docker compose file for deploying
the [RT-CV-3D microservice](../README.md) standalone — outside the full VSS
warehouse blueprint. The stack runs both RT-CV-3D components: **Perception**
(`vss-rt-cv`: RT-DETR detector + MV3DT multi-view 3D tracker) and **BEV Fusion**
(`vss-rt-cv-mv3dt-bev-fusion`: fuses per-camera measurements into BEV tracks).
See the [RT-CV-3D README](../README.md) for the microservice introduction,
container images, and how to build them.

**Assumptions**

- You have your own **time-synchronized RTSP streams** running at 30 FPS, synchronized in two ways:
  at any point in time, all cameras are streaming the **same scene moment**, and
  frames of the same scene moment carry timestamps that **agree to within one
  frame's duration (33 ms at 30 FPS)** on every camera, so they bucket into the
  same frame interval. Larger skew in either degrades cross-camera alignment and
  BEV fusion accuracy.
- You have a **`calibration.json`** for your camera setup before deployment.
  Refer to the [VSS Calibration documentation](https://docs.nvidia.com/vss/latest/calibration.html)
  for how to create one.

**Configuration** — all deployment settings live in one file,
[docker/.env](docker/.env): the models directory (`MODELS_DIR`), camera count
(`NUM_CAMS`), container images/tags (`PERCEPTION_IMAGE/TAG`,
`BEV_FUSION_IMAGE/TAG`, …), GPU device, REST port, and broker endpoints. The
steps below say when to set each; docker compose reads the file at launch, so
make sure it is properly updated before [Launch](#3-launch). The default images
are the NGC release images (`docker login nvcr.io` to pull) — see the
[RT-CV-3D README](../README.md#docker-images) for the image list and how to
build them locally instead.


## Table of Contents

- [1. Place models and assets](#1-place-models-and-assets)
  - [1.1 Download the models package](#11-download-the-models-package)
  - [1.2 Optional: use a different RT-DETR model](#12-optional-use-a-different-rt-detr-model)
- [2. Update configs for your dataset](#2-update-configs-for-your-dataset)
  - [2.1 Generate camInfo and MQTT pub/sub configs](#21-generate-caminfo-and-mqtt-pubsub-configs)
  - [2.2 Stage the DeepStream configs](#22-stage-the-deepstream-configs)
  - [2.3 Optional: use your own DeepStream / tracker configs](#23-optional-use-your-own-deepstream--tracker-configs)
- [3. Launch](#3-launch)
  - [3.1 Option A — bundled brokers](#31-option-a--bundled-brokers)
  - [3.2 Option B — your own brokers](#32-option-b--your-own-brokers)
  - [3.3 Verify startup](#33-verify-startup)
- [4. Add streams dynamically](#4-add-streams-dynamically)
- [5. Check logs and receive metadata from Kafka](#5-check-logs-and-receive-metadata-from-kafka)
- [6. Visualization](#6-visualization)
  - [6.1 On-screen display (OSD)](#61-on-screen-display-osd)
  - [6.2 BEV visualizer — live window](#62-bev-visualizer--live-window)
  - [6.3 BEV visualizer — save as video](#63-bev-visualizer--save-as-video)
- [Layout](#layout)

## 1. Place models and assets

### 1.1 Download the models package

Download the `vss-warehouse-app-data` package from NGC (substitute
`<WAREHOUSE_APP_DATA_NGC>` / `<WAREHOUSE_APP_DATA_DIR>` with the resource
reference and extracted directory name from your VSS release notes):

```bash
ngc registry resource download-version "<WAREHOUSE_APP_DATA_NGC>"

cd <WAREHOUSE_APP_DATA_DIR>
tar -xvf *.tar.gz
```

Then point **`MODELS_DIR`** in [docker/.env](docker/.env) at the extracted
`vss-warehouse-app-data/models` directory. The stack uses:

```text
$MODELS_DIR/mtmc/                  RT-DETR onnx (+ TensorRT engines, built on first run)
$MODELS_DIR/mv3dt/BodyPose3DNet/   3D pose model
```

### 1.2 Optional: use a different RT-DETR model

For example a smart-city variant:

- place the onnx under `$MODELS_DIR/mtmc/`
- update the `onnx-file` and `model-engine-file` names in
  [configs/ds-pgie-config.yml](configs/ds-pgie-config.yml)
- if the class set differs: update `configs/ds-detector-labels.txt` and the
  `CLASS_SPECS` height/radius priors when generating configs
  ([§2](#2-update-configs-for-your-dataset))

## 2. Update configs for your dataset

### 2.1 Generate camInfo and MQTT pub/sub configs

Generate from your `calibration.json`:

```bash
./scripts/generate-configs.sh /path/to/calibration.json
```

**Expected:** `DONE. Generated:` with one camInfo file per camera. Outputs go
to `generated/` (gitignored):

- `generated/camInfo/<sensor>.yml` — per-camera projection matrices + object-model priors
- `generated/pub_sub_info_config.yml` — sparse MQTT pub/sub neighbour graph
  (tune with `NEIGHBOR_CRITERIA=top_N:<K>` or `overlap_threshold:<T>`)

The `/trck` topic endpoints in the pub/sub config point at the MQTT broker,
default `localhost:1883`. If your broker is not on localhost, generate against
it instead:

```bash
MQTT_BROKERS=<host>:<port> ./scripts/generate-configs.sh /path/to/calibration.json
```

### 2.2 Stage the DeepStream configs

Set **`NUM_CAMS`** in [docker/.env](docker/.env) to your camera count (=
DeepStream batch size = BEV-fusion expected sensors), then stage the config dir
the container mounts (`generated/configs/`):

```bash
./scripts/stage-configs.sh          # OSD=1 ./scripts/stage-configs.sh for on-screen display
```

Staging also rewrites the tracker config's `cameraModelFilepath` map to the
cameras generated above.

### 2.3 Optional: use your own DeepStream / tracker configs

The samples live in [configs/](configs/) (RT-DETR pgie, MV3DT tracker, main
config, Kafka/MQTT adaptors). To replace them:

- **Tracker**: a tracker config tuned for your specific dataset usually tracks
  more accurately than the sample config — such a config is typically obtained
  by manual tuning or with
  [PipeTuner](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/Pipetuner-guide.html),
  which is why the `TRACKER_CONFIG` override is supported. Pass your own tracker
  config when staging —
  `TRACKER_CONFIG=/path/to/my-tracker-config.yml ./scripts/stage-configs.sh`
  (its `cameraModelFilepath` map will still be overwritten properly to your generated camInfo files).
- **Other DeepStream configs** (pgie, main config, adaptors): edit
  the files in [configs/](configs/), then re-run `stage-configs.sh`.

## 3. Launch

### 3.1 Option A — bundled brokers

Run the following command to start RT-CV-3D with bundled mosquitto and kafka brokers:

```bash
cd docker
COMPOSE_PROFILES=mosquitto,kafka docker compose up -d

# Tear down when done (from this docker/ directory):
#   docker compose --profile "*" down
```

### 3.2 Option B — your own brokers

If you want to use your own mosquitto and kafka brokers, set `MQTT_HOST`/`MQTT_PORT` and `KAFKA_BOOTSTRAP` in [docker/.env](docker/.env)
and launch without `COMPOSE_PROFILES`:

```bash
cd docker
docker compose up -d

# Tear down when done (from this docker/ directory):
#   docker compose --profile "*" down
```

### 3.3 Verify startup

Either option — follow the perception logs until the pipeline reports ready:

```bash
docker logs -f vss-rtvi-cv-mv3dt      # Ctrl-C to exit
```

**Expected:** the pipeline starts without errors and prints `ds-ready: YES`;
`**PERF` blocks stay at 0.0 FPS until streams are registered in
[§4](#4-add-streams-dynamically).

To see the per-view 3D bounding boxes on screen while the pipeline runs, stage
the configs with `OSD=1` before launching — see [Visualization](#6-visualization).

<details>
<summary><b>Alternative: launch Perception (MV3DT) only with <code>docker run</code> — no BEV Fusion</b></summary>

Runs only the Perception component (`vss-rt-cv`: RT-DETR + MV3DT). 
It still needs the MQTT/Kafka brokers, and
it publishes per-sensor measurements to `mdx-raw` — but without the BEV Fusion
component there are no fused `mdx-bev` tracks. Start the brokers (and
bev-fusion, if wanted) separately.

```bash
source docker/.env    # run from the rt-cv-mv3dt directory
DS_APP=/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app

docker run -d --rm --name vss-rtvi-cv-mv3dt \
  --network host --gpus "device=${GPU_DEVICE:-0}" --shm-size 6g \
  -e DISPLAY=$DISPLAY \
  -e DEEPSTREAM_ENABLE_SENSOR_ID_EXTRACTION=0 \
  -e GST_ENABLE_CUSTOM_PARSER_MODIFICATIONS=1 \
  -v "${MODELS_DIR}/mtmc:/opt/storage" \
  -v "${MODELS_DIR}/mv3dt/BodyPose3DNet:/opt/storage/BodyPose3DNet" \
  -v "$(pwd)/generated/configs:${DS_APP}/configs:ro" \
  -v "$(pwd)/generated/camInfo:/tmp/camInfo:ro" \
  -v "$(pwd)/docker/init-scripts/ds-start-mv3dt.sh:${DS_APP}/ds-start-mv3dt.sh:ro" \
  "${PERCEPTION_IMAGE}:${PERCEPTION_TAG}" \
  bash -c "${DS_APP}/ds-start-mv3dt.sh"
```

</details>

## 4. Add streams dynamically

Register your RTSP streams via the perception REST API — one
`<sensor_id>=<rtsp_url>` pair per camera, for all `NUM_CAMS` cameras.

Two rules:

- **The key is the `camera_id` and must exactly match the sensor id in your
  `calibration.json`.** With a mismatched key the MV3DT tracker cannot look up
  that camera's model and the stream will not track.
- **Use the RTSP URLs exactly as your streaming source reports them** — each
  stream may live on its own host/port/path; the URL does not need to contain
  the sensor id.

```bash
./scripts/add-streams.sh \
  <sensor_id_1>=rtsp://<host>:<port>/<path-to-stream-1> \
  <sensor_id_2>=rtsp://<host>:<port>/<path-to-stream-2> \
  ...

# or keep the pairs in a file (one KEY=URL per line, # comments allowed):
./scripts/add-streams.sh --file my-streams.txt

# runtime removal / inspection:
./scripts/add-streams.sh --remove <sensor_id_2>
./scripts/add-streams.sh --list
```

**Expected:** the script waits for `ds-ready: YES`, then reports each stream as
added. On the very first run for a given batch size, TensorRT builds the
RT-DETR engine — allow several minutes; the script waits automatically.

## 5. Check logs and receive metadata from Kafka

```bash
# a) per-source FPS (Ctrl-C to exit)
docker logs -f vss-rtvi-cv-mv3dt

# b) per-sensor 3D measurements from perception
./scripts/kafka-dump.sh --topic mdx-raw --count 20

# c) fused BEV tracks from bev-fusion
./scripts/kafka-dump.sh --topic mdx-bev --count 20
```

**Expected:**

- (a) every registered source at the stream frame rate (0.0 only during startup)
- (b) rows for all sensor ids; the same scene moment shows matching timestamps
  across sensors (within one frame's duration) — the quickest way to verify
  your streams are synchronized
- (c) rows with `sensorId = bev-sensor-1` at a steady rate

`kafka-dump.sh` decodes the `nv.Frame` protobuf in-process and prints
`(frame timestamp, sensorId, frame id)` per message.

## 6. Visualization

### 6.1 On-screen display (OSD)

**Requires a display on the host.** A tiled per-camera view with 3D bounding
boxes, rendered by the perception container on your display:

```bash
# Run `xhost +` to allow the container to open the display
OSD=1 ./scripts/stage-configs.sh && (cd docker && docker compose up -d perception)
```

### 6.2 BEV visualizer — live window

**Requires a display on the host** (without one, use
[§6.3](#63-bev-visualizer--save-as-video)). A top-down trajectory map consumed
live from Kafka. Launch it after your
streams are registered ([§4](#4-add-streams-dynamically)) and metadata is
flowing; the live view consumes
from the tail of the topic, so it only shows detections produced after it
starts:

```bash
# per-camera tracks with cross-camera consistent IDs (mdx-raw): one point per camera view of each object
BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh

# fused BEV tracks (mdx-bev): one merged point per object, as output by BEV Fusion
BEV_SOURCE=fused BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh
```

`BEV_DATASET_PATH` must contain `map.png` (BEV map image) and `transforms.yml`
(3×3 `T_ov2px` world ground plane (in meters) → BEV map (in pixels) matrix). In the live window: `q` to quit, `r` to start/stop recording the window to an mp4. See the header of
[scripts/bev-visualizer.sh](scripts/bev-visualizer.sh) for tuning knobs
(ID labels, timestamp bucketing).

**Generating `transforms.yml`** — if you don't already have one,
`generate-transforms.sh` derives it from your `calibration.json`:

```bash
# with the map image (reads its size; writes transforms.yml next to it):
./scripts/generate-transforms.sh /path/to/calibration.json /path/to/map.png

# without a map image (assumes 1920x1080; writes ./transforms.yml):
./scripts/generate-transforms.sh /path/to/calibration.json
```

The result is exact only when `map.png` is the same floor-plan image used during
calibration. To catch a mismatch, the script projects the calibration's own
reference points and warns if they don't land on the map.

### 6.3 BEV visualizer — save as video

Set `BEV_SAVE_VIDEO=1` (also the automatic behavior when no display is
available):

```bash
BEV_SAVE_VIDEO=1 BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh

# same for the fused-track view:
BEV_SAVE_VIDEO=1 BEV_SOURCE=fused BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh
```

**Expected:** a `recorded N frames ...` progress line every ~10 s (a one-time
`Waiting for first message ...` right after launch is normal). Stop with
Ctrl-C — the mp4 is finalized and saved to `./bev-output/`
(`Video saved: .../bev-output/trajectory_video_<stamp>.mp4 (N frames)`).

## Layout

| Path | Purpose |
|---|---|
| [docker/compose.yml](docker/compose.yml) | perception + bev-fusion (+ optional `mosquitto` / `kafka` compose profiles) |
| [docker/.env](docker/.env) | images/tags, `MODELS_DIR`, `NUM_CAMS`, ports, GPU, broker endpoints |
| [docker/init-scripts/](docker/init-scripts/) | `ds-start-mv3dt.sh` — the in-container launch script (mounted into perception) |
| [configs/](configs/) | sample DeepStream configs + `mosquitto.conf` |
| [scripts/](scripts/) | shell utilities (config generation, staging, stream add/remove, visualizer/dump launchers) |
| [utils/](utils/) | Python consumers (`kafka_bev_visualizer.py`, `kafka_fused_bev_visualizer.py`, `kafka-dump.py`, `schema_pb2.py`) + their `requirements.txt` |
| `generated/` | generated + staged per-run files: `camInfo/`, `pub_sub_info_config.yml`, `configs/` (the staged dir the container mounts, incl. the rewritten tracker config) |

Config generation wraps this repo's
[`tools/rtvi-cv-mv3dt-utils`](../../../../tools/rtvi-cv-mv3dt-utils) generators;
the BEV fusion service source lives at [`../rt-cv-bev-fusion`](../rt-cv-bev-fusion).
