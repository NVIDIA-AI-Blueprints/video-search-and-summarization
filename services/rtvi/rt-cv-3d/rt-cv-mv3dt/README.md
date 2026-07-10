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


## Table of Contents

- [1. Place models and assets](#1-place-models-and-assets)
- [2. Update configs for your dataset](#2-update-configs-for-your-dataset)
- [3. Launch](#3-launch)
- [4. Add streams dynamically](#4-add-streams-dynamically)
- [5. Check logs and receive metadata from Kafka](#5-check-logs-and-receive-metadata-from-kafka)
- [6. Visualization](#6-visualization)
- [Layout](#layout)

## 1. Place models and assets

Download the `vss-warehouse-app-data` package from NGC (substitute
`<WAREHOUSE_APP_DATA_NGC>` / `<WAREHOUSE_APP_DATA_DIR>` with the resource
reference and extracted directory name from your VSS release notes):

```bash
ngc registry resource download-version "<WAREHOUSE_APP_DATA_NGC>"

cd <WAREHOUSE_APP_DATA_DIR>
tar -xvf *.tar.gz
```

Point `MODELS_DIR` in [docker/.env](docker/.env) at its
`vss-warehouse-app-data/models` directory. The stack uses:

```text
$MODELS_DIR/mtmc/                  RT-DETR onnx (+ TensorRT engines, built on first run)
$MODELS_DIR/mv3dt/BodyPose3DNet/   3D pose model
```

Image names/tags are set in [docker/.env](docker/.env) (`PERCEPTION_IMAGE/TAG`,
`BEV_FUSION_IMAGE/TAG`, …) — see the [RT-CV-3D README](../README.md#docker-images)
for the images and how to build them; `docker login nvcr.io` to pull from NGC.

**Using a different RT-DETR model** (for example a smart-city variant): place the
onnx under `$MODELS_DIR/mtmc/`, then update the `onnx-file` and
`model-engine-file` names in [configs/ds-pgie-config.yml](configs/ds-pgie-config.yml)
(and the detector classes in `configs/ds-detector-labels.txt` plus the
`CLASS_SPECS` height/radius priors when generating configs, if the class set
differs).

## 2. Update configs for your dataset

Generate the per-camera configs from your `calibration.json`:

```bash
./scripts/generate-configs.sh /path/to/calibration.json
```

This writes into `generated/` (gitignored):

- `generated/camInfo/<sensor>.yml` — per-camera projection matrices + object-model priors
- `generated/pub_sub_info_config.yml` — sparse MQTT pub/sub neighbour graph
  (tune with `NEIGHBOR_CRITERIA=top_N:<K>` or `overlap_threshold:<T>`)

Then set **`NUM_CAMS`** in [docker/.env](docker/.env) to your camera count (=
DeepStream batch size = BEV-fusion expected sensors), and stage the DeepStream
config dir the container mounts (`generated/configs/`):

```bash
./scripts/stage-configs.sh          # OSD=1 ./scripts/stage-configs.sh for on-screen display
```

Staging also rewrites the tracker config's `cameraModelFilepath` map to the
cameras generated above. Sample configs live in [configs/](configs/) (RT-DETR
pgie, MV3DT tracker, main config, Kafka/MQTT adaptors). You can use your own
DeepStream and tracker configs instead of the samples:

- **Tracker**: pass your own base config when staging —
  `TRACKER_CONFIG=/path/to/my-tracker-config.yml ./scripts/stage-configs.sh`
  (its `cameraModelFilepath` map is still rewritten to your cameras).
- **Other DeepStream configs** (pgie, main config, adaptors): edit or replace
  the files in [configs/](configs/), then re-run `stage-configs.sh`.

## 3. Launch

With the **bundled brokers** (no mosquitto/kafka of your own):

```bash
cd docker
COMPOSE_PROFILES=mosquitto,kafka docker compose up -d
```

With **your own brokers**: set `MQTT_HOST`/`MQTT_PORT` and `KAFKA_BOOTSTRAP` in
[docker/.env](docker/.env) and launch without profiles. If the MQTT broker is not
on localhost, also generate the pub/sub config against it
(`MQTT_BROKERS=<host>:<port> ./scripts/generate-configs.sh ...`) so the `/trck`
topic endpoints point at the right broker:

```bash
cd docker
docker compose up -d
```

To see the per-view 3D bounding boxes on screen while the pipeline runs, stage
the configs with `OSD=1` before launching — see [Visualization](#6-visualization).

Teardown (either way):

```bash
cd docker
docker compose --profile "*" down
```

<details>
<summary><b>Alternative: launch the perception container with <code>docker run</code></b></summary>

Equivalent to the compose `perception` service (brokers and bev-fusion still come
from compose or your own infrastructure):

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


Defaults that make other settings unnecessary here: the output type defaults to
kafka; the MQTT endpoint defaults to `localhost:1883`; the REST port comes from
`http-port` in the staged main config (not an env var). For a remote MQTT broker
add `-e MQTT_HOST=... -e MQTT_PORT=...`; for the on-screen display (`OSD=1`
staging) add `-e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix`.

</details>

## 4. Add streams dynamically

Register your RTSP streams via the perception REST API. **The key of each
`KEY=URL` pair is the `camera_id`, and it must exactly match the sensor id in
your `calibration.json`.** With a mismatched key the MV3DT tracker cannot look
up that camera's model and the stream will not track.

Pass one `<sensor_id>=<rtsp_url>` pair per camera, for all `NUM_CAMS` cameras.
Use the RTSP URLs exactly as your streaming source reports them (each stream may
live on its own host/port/path — the URL does not need to contain the sensor id):

```bash
./scripts/add-streams.sh \
  <sensor_id_1>=rtsp://<host>:<port>/<path-to-stream-1> \
  <sensor_id_2>=rtsp://<host>:<port>/<path-to-stream-2> \
  ...
```

Or keep the pairs in a file (one `KEY=URL` per line, `#` comments allowed):

```bash
./scripts/add-streams.sh --file my-streams.txt
```

Runtime removal / inspection:

```bash
./scripts/add-streams.sh --remove <sensor_id_2>
./scripts/add-streams.sh --list
```

The script waits for `ds-ready: YES` first — on the very first run for a given
batch size, TensorRT builds the RT-DETR engine, which takes several minutes.

## 5. Check logs and receive metadata from Kafka

```bash
# per-source FPS (healthy: all sources at the stream frame rate)
docker logs vss-rtvi-cv-mv3dt 2>&1 | grep -A13 '\*\*PERF' | tail -15

# per-sensor 3D measurements from perception
./scripts/kafka-dump.sh --topic mdx-raw --count 20

# fused BEV tracks from bev-fusion
./scripts/kafka-dump.sh --topic mdx-bev --count 20
```

`kafka-dump.sh` decodes the `nv.Frame` protobuf in-process and prints
`(frame timestamp, sensorId, frame id)` per message — also the quickest way to
verify your streams' timestamps are synchronized across cameras.

## 6. Visualization

**On-screen display (OSD)** — a tiled per-camera view with 3D bounding boxes,
rendered by the perception container on your X display:

```bash
OSD=1 ./scripts/stage-configs.sh && (cd docker && docker compose up -d perception)
```

Requires a host X display (`DISPLAY` is passed through and `/tmp/.X11-unix` is
mounted; run `xhost +local:` if the container cannot open the display). Leave
`OSD=0` (default) for headless operation.

**BEV visualizer** — a top-down trajectory map consumed live from Kafka.
Launch it after your streams are registered ([§4](#4-add-streams-dynamically))
and metadata is flowing (verify with the dumps in
[§5](#5-check-logs-and-receive-metadata-from-kafka)); the live view consumes
from the tail of the topic, so it only shows detections produced after it starts.
Two sources are available:

```bash
# per-camera measurements (mdx-raw): one point per camera view of each object
BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh

# fused BEV tracks (mdx-bev): one merged point per object, as output by BEV Fusion
BEV_SOURCE=fused BEV_DATASET_PATH=/path/to/dataset ./scripts/bev-visualizer.sh
```

`BEV_DATASET_PATH` must contain `map.png` (BEV background) and `transforms.yml`
(3×3 `T_ov2px` overlay→pixel matrix). With a display it opens a live window;
headless (or `BEV_SAVE_VIDEO=1`) it saves an mp4 to `./bev-output/`. See the header
of [scripts/bev-visualizer.sh](scripts/bev-visualizer.sh) for tuning knobs
(ID labels, timestamp bucketing).

## Layout

| Path | Purpose |
|---|---|
| [docker/compose.yml](docker/compose.yml) | perception + bev-fusion (+ optional `mosquitto` / `kafka` compose profiles) |
| [docker/.env](docker/.env) | images/tags, `MODELS_DIR`, `NUM_CAMS`, ports, GPU, broker endpoints |
| [docker/init-scripts/](docker/init-scripts/) | `ds-start-mv3dt.sh` — the in-container launch script (mounted into perception) |
| [configs/](configs/) | sample DeepStream configs + `mosquitto.conf` |
| [scripts/](scripts/) | shell utilities (config generation, staging, stream add/remove, visualizer/dump launchers) |
| [utils/](utils/) | Python consumers (`kafka_bev_visualizer.py`, `kafka_fused_bev_visualizer.py`, `kafka-dump.py`, `schema_pb2.py`) + their `requirements.txt` |
| `generated/` | generated + staged per-run files: `camInfo/`, `pub_sub_info_config.yml`, `configs/` (the staged dir the container mounts, incl. the rewritten tracker config) — gitignored |

Config generation wraps this repo's
[`tools/rtvi-cv-mv3dt-utils`](../../../../tools/rtvi-cv-mv3dt-utils) generators;
the BEV fusion service source lives at [`../rt-cv-bev-fusion`](../rt-cv-bev-fusion).
