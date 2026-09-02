# RT-CV-3D Standalone Deployment

Sample configs, utility scripts, and a minimal docker compose file for RT-CV-3D microservice standalone deployment.
The stack runs both RT-CV-3D components: **Perception**
(`vss-rt-cv`: RT-DETR detector + MV3DT multi-view 3D tracker) and **BEV Fusion**
(`vss-rt-cv-mv3dt-bev-fusion`: fuses per-camera measurements into BEV tracks).
See the [RT-CV-3D README](../README.md) for the microservice introduction,
container images, and how to build them.

**Assumptions**

- You cameras are already calibrated and you have a **`calibration.json`** for your camera setup. Refer to the
  [VSS Calibration documentation](https://docs.nvidia.com/vss/latest/calibration.html)
  for how to create one.
- **Time-synchronized, 30 FPS multi-camera footage.** Whether you feed recordings or
  live streams, the cameras must be synchronized in two ways: at any moment all cameras
  capture the **same scene moment**, and frames of that moment carry timestamps that
  **agree to within one frame's duration (33 ms at 30 FPS)**, so they bucket into the
  same frame interval. Larger skew degrades cross-camera alignment and BEV fusion
  accuracy. For **live RTSP** that correspondence is carried in-band, as an SEI
  frame ID embedded by the VST proxy — see [§4](#4-add-streams-dynamically-rtsp).

You can feed that footage in one of two ways. The simplest is to point the pipeline at
**recorded per-camera `.mp4` files**: this needs no streaming server, and
each clip plays once and then the app exits, so it is the easiest way to try RT-CV-3D on your
own recordings (see [§2.3](#23-stage-the-deepstream-configs)). The other is **live RTSP
streams**, which you register after launch (see
[§4](#4-add-streams-dynamically-rtsp)). The rest of the setup is identical either way, so it is
best to validate on recordings first and move to live RTSP once the results look good.

If you don't have your own footage yet, the package in [§1.1](#11-download-the-assets-package)
includes a 4-camera warehouse **sample dataset** you can run end-to-end.



## Table of Contents

- [1. Place models and assets](#1-place-models-and-assets)
  - [1.1 Download the assets package](#11-download-the-assets-package)
  - [1.2 Optional: use a different RT-DETR model](#12-optional-use-a-different-rt-detr-model)
- [2. Update configs for your dataset](#2-update-configs-for-your-dataset)
  - [2.1 Set environment variables](#21-set-environment-variables)
  - [2.2 Generate camInfo and MQTT pub/sub configs](#22-generate-caminfo-and-mqtt-pubsub-configs)
  - [2.3 Stage the DeepStream configs](#23-stage-the-deepstream-configs)
  - [2.4 Optional: use your own DeepStream / tracker configs](#24-optional-use-your-own-deepstream--tracker-configs)
- [3. Launch](#3-launch)
  - [3.1 Option A — bundled brokers](#31-option-a--bundled-brokers)
  - [3.2 Option B — your own brokers](#32-option-b--your-own-brokers)
  - [3.3 Verify startup](#33-verify-startup)
- [4. Add streams dynamically (RTSP)](#4-add-streams-dynamically-rtsp)
- [5. Check logs and receive metadata from Kafka](#5-check-logs-and-receive-metadata-from-kafka)
- [6. Visualization](#6-visualization)
  - [6.1 Perception camera view — live window (OSD)](#61-perception-camera-view--live-window-osd)
  - [6.2 Perception camera view — save as video](#62-perception-camera-view--save-as-video)
  - [6.3 BEV visualizer — live window](#63-bev-visualizer--live-window)
  - [6.4 BEV visualizer — save as video](#64-bev-visualizer--save-as-video)
- [Layout](#layout)

## 1. Place models and assets

### 1.1 Download the assets package

Download the `vss-warehouse-app-data` package from NGC (substitute
`<WAREHOUSE_APP_DATA_NGC>` / `<WAREHOUSE_APP_DATA_DIR>` with the resource
reference and extracted directory name from the
[VSS documentation](https://docs.nvidia.com/vss/latest/warehouse-docs/Quickstart-Guide.html#warehouse-app-data)):

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

**Sample dataset (optional).** The same package also ships a 4-camera warehouse sample you
can try RT-CV-3D on without your own footage. It consists of:

- **Per-camera videos** (in the app-data package) — point `VIDEO_DIR` at this directory:
  ```text
  $WAREHOUSE_APP_DATA_DIR/vss-warehouse-app-data/videos/warehouse-4cams-20mx20m-synthetic/
      Camera.mp4  Camera_01.mp4  Camera_02.mp4  Camera_03.mp4
  ```
- **`calibration.json`** and the **BEV map `Top.png`** (in this repo, under
  `deploy/.../warehouse-mv3dt-app/calibration/sample-data/warehouse-4cams-20mx20m-synthetic/`):
  - [`calibration.json`](../../../../deploy/docker/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/warehouse-4cams-20mx20m-synthetic/calibration.json)
  - [`images/Top.png`](../../../../deploy/docker/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/warehouse-4cams-20mx20m-synthetic/images/Top.png)

To test with the sample dataset, set **`VIDEO_DIR`** to the absolute path of the sample videos
directory and **`NUM_CAMS=4`** in [docker/.env](docker/.env), then continue with
[§2.2](#22-generate-caminfo-and-mqtt-pubsub-configs) using its `calibration.json`, and
[§2.3](#23-stage-the-deepstream-configs) with `INPUT_MODE=file`.

### 1.2 Optional: use a different RT-DETR model

For example a smart-city variant:

- place the onnx under `$MODELS_DIR/mtmc/`
- update the `onnx-file` and `model-engine-file` names in
  [configs/ds-pgie-config.yml](configs/ds-pgie-config.yml)
- if the class set differs: update `configs/ds-detector-labels.txt` and the
  `CLASS_SPECS` height/radius priors when generating configs
  ([§2](#2-update-configs-for-your-dataset)) — `CLASS_SPECS` is an env override to
  [`generate-configs.sh`](scripts/generate-configs.sh), whose header documents its format

## 2. Update configs for your dataset

### 2.1 Set environment variables

Settings live in [docker/.env](docker/.env), in two kinds:

| Set in | Variables | Read by |
|---|---|---|
| **`docker/.env`** (needed at launch) | `MODELS_DIR`†, `NUM_CAMS`†, `VIDEO_DIR`, `GPU_DEVICE`, `DS_HTTP_PORT`, `MQTT_HOST`/`MQTT_PORT`, `KAFKA_BOOTSTRAP`/`KAFKA_PORT`/`KAFKA_CONTROLLER_PORT`, `RAW_TOPIC`/`FUSED_TOPIC`, `*_IMAGE`/`*_TAG` | `docker compose` |
| **`docker/.env`** *or* the command line | `INPUT_MODE`, `SAVE_VIDEO`, `OSD`, `TRACKER_CONFIG` | `scripts/stage-configs.sh` |

† required, no default — compose won't start without them.

`docker compose` reads `docker/.env` at launch, so everything it needs must be set in that
file. The four staging knobs in the second row can instead be set **inline** — on the command
line, e.g. `OSD=1 ./scripts/stage-configs.sh` — which overrides `docker/.env` for that run.

### 2.2 Generate camInfo and MQTT pub/sub configs

Generate from your `calibration.json`:

```bash
./scripts/generate-configs.sh /path/to/calibration.json
```

**Expected:** `DONE. Generated:` with one camInfo file per camera. Outputs go
to `generated/` (gitignored):

- `generated/camInfo/<sensor>.yml` — per-camera projection matrices + object-model priors
- `generated/pub_sub_info_config.yml` — sparse MQTT pub/sub neighbor graph
  (tune with `NEIGHBOR_CRITERIA=top_N:<K>` or `overlap_threshold:<T>`)

The `/trck` topic endpoints in the generated pub/sub config default to the MQTT
broker at `localhost:1883`. If your broker is not on localhost or on a different
port, pass its address so the generated config points at it:

```bash
MQTT_BROKERS=<host>:<port> ./scripts/generate-configs.sh /path/to/calibration.json
```

### 2.3 Stage the DeepStream configs

Run the staging script below. It writes `generated/configs/`, the config directory the container
mounts. Any variable you put at the front of the command overrides its value in `.env`.

Pick the command for your case:

```bash
./scripts/stage-configs.sh                                # live RTSP streams (default), headless
xhost + && OSD=1 ./scripts/stage-configs.sh              # + on-screen 3D-box display (needs a host display; xhost + lets the container open it)
INPUT_MODE=file ./scripts/stage-configs.sh                # recorded video files  (also set VIDEO_DIR in .env — see below)
INPUT_MODE=file SAVE_VIDEO=1 ./scripts/stage-configs.sh   # recorded files + save the grid view video with 3D boxes overlaid (see §6.2)
TRACKER_CONFIG=/path/to/tracker.yml ./scripts/stage-configs.sh   # use your own tuned tracker config (see §2.4)
```

**Recorded-file input (`INPUT_MODE=file`):** put one `.mp4` file per camera under `VIDEO_DIR`
(set in `.env`), each named `<sensor_id>.mp4` to match its sensor id in `calibration.json`.
The clips **play once and the container exits** at end of stream, so there is no stream
registration; skip [§4](#4-add-streams-dynamically-rtsp). Everything downstream (Kafka, BEV
fusion, visualizers) is identical to live mode.

### 2.4 Optional: use your own DeepStream / tracker configs

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
  the files in [configs/](configs/) before running `stage-configs.sh` or re-running it.

## 3. Launch

The default images are the NGC release images — run `docker login nvcr.io` first to be able to pull
them (or build them locally instead, see the [RT-CV-3D README](../README.md#docker-images)).

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

**Expected:** the pipeline starts without errors and prints `ds-ready: YES`. With RTSP
input the `**PERF` blocks stay at 0.0 FPS until streams are registered in
[§4](#4-add-streams-dynamically-rtsp); with file input (`INPUT_MODE=file`) the clips start
immediately and the container exits when they end.

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

## 4. Add streams dynamically (RTSP)

*This is the input step for **live RTSP** (`INPUT_MODE=stream`). Skip it when
testing on recorded files — see [§2.3](#23-stage-the-deepstream-configs).*

> **Live RTSP requires SEI-carrying streams.** The staged DeepStream config sets
> `extract-sei-sim-time=1` with `attach-sys-ts-as-ntp=0`, so each frame's
> timestamp is read from `NVDS_CUSTOMMETA` SEI and is never replaced with host
> time. **Every RTSP source must carry that SEI** — it is what lets MV3DT line
> frames up across cameras. If it is missing, streams register successfully and
> `ds-ready` reports `YES`, but no source ever activates and no `mdx-raw`
> metadata is produced.
>
> In this deployment the VST proxy injects it (its upstream NVStreamer sources do
> not: VST unifies their SEI). A source that does not go through VST must supply
> `NVDS_CUSTOMMETA` SEI itself.
>
> `scripts/add-streams.sh` checks this before registering anything and refuses
> with the remedy. To fix it, set `"enable_proxy_server_sei_metadata": true` in
> both the VST and NVStreamer `vst_config.json` your deployment uses, redeploy,
> then confirm inside the containers:
>
> ```bash
> docker exec nvstreamer-1 sh -lc \
>   'grep -n enable_proxy_server_sei_metadata /home/vst/vst_release/configs/vst_config.json'
> docker exec streamprocessing-ms-1 sh -lc \
>   'grep -n enable_proxy_server_sei_metadata /home/vst/vst_release/configs/vst_config.json'
> ```

Register your RTSP streams via the perception REST API — one
`<sensor_id>=<rtsp_url>` pair per camera, for all `NUM_CAMS` cameras.
**The key is the `camera_id` and must exactly match the sensor id in your
  `calibration.json`.**

```bash
./scripts/add-streams.sh \
  <sensor_id_1>=rtsp://<host>:<port>/<path-to-stream-1> \
  <sensor_id_2>=rtsp://<host>:<port>/<path-to-stream-2> \
  ...

# or keep the pairs in a file (one KEY=URL per line, # comments allowed):
./scripts/add-streams.sh --file my-streams.txt

# runtime removal / inspection:
./scripts/add-streams.sh --remove <sensor_id_2>   # one stream, by the id --list shows
./scripts/add-streams.sh --remove-all             # every registered stream, after confirming
./scripts/add-streams.sh --remove-all --yes       # same, unattended
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

- (a) every registered stream running at its frame rate (30 FPS)
- (b) / (c) prints 20 rows of `(frame timestamp, sensorId, frame id, …)` received messages

## 6. Visualization

Two visualizations are available: the **perception app's own camera view** (the tiled
3D-box view, shown on-screen in [§6.1](#61-perception-camera-view--live-window-osd) or saved to file in
[§6.2](#62-perception-camera-view--save-as-video)), and the **BEV track view**, which renders
the object tracks consumed from Kafka onto a top-down, bird's-eye-view map (a live window
in [§6.3](#63-bev-visualizer--live-window) or saved to file in
[§6.4](#64-bev-visualizer--save-as-video)).

### 6.1 Perception camera view — live window (OSD)

**Requires a display on the host.** A tiled per-camera view with 3D bounding
boxes, rendered by the perception container on your display:

```bash
# Run `xhost +` to allow the container to open the display.
# Add the OSD=1 flag to the staging command in §2.3
OSD=1 ./scripts/stage-configs.sh
```

<details>
<summary><b>Troubleshooting: OSD window does not appear</b></summary>

Work through the following checks in order:

1. **`$DISPLAY` is set on the host** — run `echo $DISPLAY`; it should print a non-empty value matching your active display session (e.g. `:0`). If it is empty, find the correct value for your session and export it: `export DISPLAY=<value>`.
2. **X11 access is granted** — run `xhost +` on the host to allow the container to open the display.
3. **Configs were staged with `OSD=1`** — If you didn't already, re-run `OSD=1 ./scripts/stage-configs.sh` and relaunch.
4. **Check the perception container logs**
   ```bash
   docker logs vss-rtvi-cv-mv3dt
   ```
   If it shows this line: `libEGL warning: egl: failed to create dri2 screen`, it often indicates the container cannot access the host DRI device (`/dev/dri`). In most cases the fix is to pass the host DRI device into the container by adding the following to the `perception` service in [docker/compose.yml](docker/compose.yml):
   ```yaml
   services:
     perception:
       devices:
         - /dev/dri
   ```
   Then relaunch the container.

</details>

### 6.2 Perception camera view — save as video

Save the perception app's own annotated camera view (the same 3D-box view as the
OSD) to an encoded video, useful on a headless machine with no display. Enable it with
`SAVE_VIDEO=1`, either set in [docker/.env](docker/.env) or passed inline to the staging
command ([§2.3](#23-stage-the-deepstream-configs)), then launch. It writes
`video-output/grid-view.mkv`: all cameras tiled into one video, with the 3D boxes and the
class/ID labels overlaid.

- **Recorded files** ([§2.3](#23-stage-the-deepstream-configs)) — the clips play
  once and the file finalizes automatically at end-of-stream (the container then exits):
  ```bash
  # Add the SAVE_VIDEO=1 flag to the staging command in §2.3
  INPUT_MODE=file SAVE_VIDEO=1 ./scripts/stage-configs.sh
  ```
- **Live RTSP** — the DeepStream file sink writes one continuous file and does not
  configure segment rotation, size limits, or retention cleanup. Because a live stream
  has no natural end-of-stream, `stage-configs.sh` refuses `INPUT_MODE=stream
  SAVE_VIDEO=1` by default. To explicitly accept an unbounded live recording, opt in:
  ```bash
  ALLOW_UNBOUNDED_RECORDING=1 SAVE_VIDEO=1 ./scripts/stage-configs.sh
  ```

Both supported modes write `video-output/grid-view.mkv`. To convert it to `.mp4` (and, for live RTSP,
produce a finalized/seekable file), run a lossless conversion with no re-encode:

```bash
ffmpeg -i video-output/grid-view.mkv -c copy video-output/grid-view.mp4
# For live RTSP input, a few "Non-monotonic DTS" warnings are expected and harmless.
```

> **NVENC-less GPUs (e.g. A100, H100, H200, GB200, GB300).** The video output from the perception container is encoded with the GPU's NVENC hardware
> encoder by default (`enc-type=0` for `[sink2]` in `configs/ds-main-config-mv3dt.txt`). When `SAVE_VIDEO=1`,
> `stage-configs.sh` detects these GPUs with `nvidia-smi` and stages the software (CPU) encoder
> (`enc-type=1`) instead. First prepare the image once:
> ```bash
> docker exec -it vss-rtvi-cv-mv3dt \
>   bash -c 'cd /opt/nvidia/deepstream/deepstream/ && bash user_additional_install.sh'   # install the software encoder
> docker commit vss-rtvi-cv-mv3dt <your-image>:<tag>    # then set this image in docker/.env
> ```
> Then stage and launch normally.

### 6.3 BEV visualizer — live window

**Requires a display on the host** (without one, use [§6.4](#64-bev-visualizer--save-as-video)).
Reads object tracks from Kafka and renders them on a top-down BEV map.

**Step 1 — prepare `BEV_DATASET_PATH`.** This must be a directory containing:

- `map.png` — the BEV floor-plan image
- `transforms.yml` — 3×3 `T_ov2px` matrix mapping world ground plane (meters) → map pixels

If you don't have `transforms.yml`, generate it from your `calibration.json`:

```bash
# with a map image (reads its size; writes transforms.yml next to it):
./scripts/generate-transforms.sh /path/to/calibration.json /path/to/map.png

# without a map image (assumes 1920×1080; writes ./transforms.yml):
./scripts/generate-transforms.sh /path/to/calibration.json
```

The script warns if the projected reference points don't land on the map, which indicates a mismatch between `map.png` and the floor-plan used during calibration.

> **Sample dataset.** Build the `BEV_DATASET_PATH` directory from the files shipped in this repo:
> ```bash
> SAMPLE_DATA=../../../../deploy/docker/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/warehouse-4cams-20mx20m-synthetic
> mkdir -p bev-dataset
> cp "$SAMPLE_DATA/images/Top.png" bev-dataset/map.png
> ./scripts/generate-transforms.sh "$SAMPLE_DATA/calibration.json" bev-dataset/map.png
> # BEV_DATASET_PATH=bev-dataset
> ```

**Step 2 — launch** after the Kafka brokers are running:

```bash
# per-camera tracks (mdx-raw) — one point per camera view of each object:
BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh

# fused BEV tracks (mdx-bev) — one merged point per object:
BEV_SOURCE=fused BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh
```

In the live window: press `q` to quit. See [scripts/bev-visualizer.sh](scripts/bev-visualizer.sh) for tuning knobs (ID labels, timestamp bucketing).

### 6.4 BEV visualizer — save as video

Same `BEV_DATASET_PATH` and `BEV_SOURCE` as [§6.3](#63-bev-visualizer--live-window). Add
`BEV_SAVE_VIDEO=1` environment variable to save the video to file:

```bash
BEV_SAVE_VIDEO=1 BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh

# fused-track view:
BEV_SAVE_VIDEO=1 BEV_SOURCE=fused BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh
```

**Expected:** a `recorded N frames ...` progress line every ~10 s. With **file input** the
recorder finalizes and exits on its own shortly after the clips end; with **live streams** it
runs until you stop it with Ctrl-C. Either way the mp4 is saved to `./bev-output/`
(`Video saved: .../bev-output/trajectory_video_<stamp>.mp4 (N frames)`).

## Layout

| Path | Purpose |
|---|---|
| [docker/compose.yml](docker/compose.yml) | perception + bev-fusion (+ optional `mosquitto` / `kafka` compose profiles) |
| [docker/.env](docker/.env) | images/tags, `MODELS_DIR`, `NUM_CAMS`, ports, GPU, broker endpoints, `INPUT_MODE`/`VIDEO_DIR`, `SAVE_VIDEO` |
| [docker/init-scripts/](docker/init-scripts/) | `ds-start-mv3dt.sh` — the in-container launch script (mounted into perception) |
| [configs/](configs/) | sample DeepStream configs + `mosquitto.conf` |
| [scripts/](scripts/) | shell utilities (config generation, staging, stream add/remove, visualizer/dump launchers) |
| [utils/](utils/) | Python consumers (`kafka_bev_visualizer.py`, `kafka_fused_bev_visualizer.py`, `kafka-dump.py`, `schema_pb2.py`) + their `requirements.txt` |
| `generated/` | generated + staged per-run files: `camInfo/`, `pub_sub_info_config.yml`, `configs/` (the staged dir the container mounts, incl. the rewritten tracker config) |
| `video-output/` | saved perception video from `SAVE_VIDEO=1` (`grid-view.mkv`, the tiled camera grid) |
| `bev-output/` | saved BEV visualizer videos from `BEV_SAVE_VIDEO=1` ([§6.4](#64-bev-visualizer--save-as-video)) — top-down `trajectory_video_*.mp4` |
