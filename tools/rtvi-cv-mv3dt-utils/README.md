# Utility scripts for MV3DT RTVI-CV Pipeline

Two offline utility scripts for pre-generating `warehouse-mv3dt-app` configs on
custom datasets
(`deploy/docker/industry-profiles/warehouse-operations/warehouse-mv3dt-app/`).

Two configuration artifacts are produced:

1. `camInfo/<sensor_id>.yml` — one per camera, containing the flattened 3x4
   projection matrix that maps a 3D world point to a 2D image point, plus
   per-class object-model dimensions used by SV3DT back-projection. Mounted
   into the DeepStream container at `/tmp/camInfo/` and referenced by
   `ds-mv3dt-tracker-config.yml` under `ObjectModelProjection.cameraModelFilepath`.
   Place the generated directory at
   `warehouse-mv3dt-app/calibration/sample-data/<DATASET_NAME>/camInfo/`.
2. `pub_sub_info_config.yml` — the MQTT publish/subscribe graph used by MV3DT
   to exchange 3D tracklets across cameras. The recommended topology is a
   *vision-neighbor* graph in which each camera subscribes only to peers whose
   FOVs overlap with its own. Mounted into the container at
   `/tmp/generated/pub_sub_info_config.yml` and read by the tracker's
   `Communicator` block. Place the generated file at
   `warehouse-mv3dt-app/deepstream/configs/pub_sub_info_config.yml`.

## Requirements

- Python 3.8+
- `numpy`, `opencv-python`, `PyYAML`, `tqdm` (pinned versions in [`requirements.txt`](./requirements.txt))

Create a virtual environment and install the pinned dependencies:

```bash
python -m venv ~/venv
source ~/venv/bin/activate
pip install -r requirements.txt
```

## Sample commands

```bash
# 1. Generate per-camera YAMLs from an Omniverse-style calibration.json
python generate_cam_info_configs.py \
    --calibration-json /path/to/calibration.json \
    --output-dir       ./camInfo \
    --class 0 1.60 0.3 \
    --class 1 1.60 0.3 \
    --class 2 1.60 0.3 \
    --class 3 0.48 0.3 \
    --class 4 0.2  0.52 \
    --class 5 2.2  0.9

# 2. Generate the MQTT pub/sub topology from those camInfo files
python generate_pub_sub_configs.py \
    --cam_info_path ./camInfo \
    --mqtt_brokers  localhost:1883 \
    --output_path   .
```

Notes on the sample commands:

- The `--class` priors above match the RT-DETR detector's class set. If you
  are using a custom detector, adjust the `--class` flags (and add/remove
  classes) accordingly — see the per-script reference below.
- The `generate_pub_sub_configs.py` invocation above emits a dense
  publish/subscribe graph that connects every pair of cameras whose
  field-of-view masks overlap on the world plane. If you want a sparser graph
  (e.g. only the top-K most-overlapping neighbours) or only care about a
  specific world-plane region, use `--neighbor_criteria` and/or
  `--range_of_interest` as documented in the per-script reference below.

## Script reference for advanced usage

### `generate_cam_info_configs.py`

Convert a single calibration JSON (with a top-level `sensors` array of camera
entries, each containing a 3x4 `cameraMatrix`) into one `camInfo/<sensor_id>.yml`
file per camera sensor. Object-class priors (`classID`, `height` in meters,
`radius` in meters) are appended to every generated file and may be repeated
once per class.

**Usage**

```bash
python generate_cam_info_configs.py \
    --calibration-json CALIBRATION_JSON \
    --output-dir       OUTPUT_DIR \
    --class CLASS_ID HEIGHT RADIUS [--class ...]
```

**Arguments**

| Argument | Required | Description |
|----------|----------|-------------|
| `--calibration-json` | yes | Path to the input `calibration.json`. |
| `--output-dir` | yes | Directory where one `<sensor_id>.yml` is written per camera sensor. Created if missing. |
| `--class CLASS_ID HEIGHT RADIUS` | yes (repeatable) | One object class per flag. `CLASS_ID` is an integer; `HEIGHT` and `RADIUS` are numeric (meters). |

**Input `calibration.json` shape**

```json
{
  "sensors": [
    { "id": "Camera_01", "type": "camera",
      "cameraMatrix": [[...4 floats...], [...4 floats...], [...4 floats...]] },
    ...
  ]
}
```

Non-camera sensors and sensors without `cameraMatrix` are skipped; the script
errors out if no camera sensors are found.

**Output `<sensor_id>.yml` shape**

```yaml
projectionMatrix_3x4_w2p:
- <p00>
- <p01>
  ...
- <p23>

modelInfo:
  - classID: 0
    height: 1.60
    radius: 0.3
  - classID: 1
    height: 1.60
    radius: 0.3
  - classID: 2
    height: 1.60
    radius: 0.3
  - classID: 3
    height: 0.48
    radius: 0.3
  - classID: 4
    height: 0.2
    radius: 0.52
  - classID: 5
    height: 2.2
    radius: 0.9
```

### `generate_pub_sub_configs.py`

Compute pairwise field-of-view overlap between every camera in a `camInfo/`
directory on the world (ground) plane, decide which neighbours each camera
should subscribe to, and emit a single `pub_sub_info_config.yml` listing
publish/subscribe MQTT broker+topic strings.

For each camera, the script projects a grid of world-plane samples through its
intrinsics/extrinsics (decomposed from `projectionMatrix_3x4_w2p`) to build an
FOV mask, then ranks other cameras by the overlap fraction of their masks.
Topics are named `/trck/<cam_name>` where `<cam_name>` is the camInfo file
stem.

**Usage**

```bash
python generate_pub_sub_configs.py \
    [--cam_info_path        CAM_INFO_PATH] \
    [--mqtt_brokers         HOST:PORT[,HOST:PORT,...]] \
    [--minimum_object_size  PIXELS] \
    [--neighbor_criteria    {top_N:K | overlap_threshold:T}] \
    [--output_path          OUTPUT_DIR] \
    [--range_of_interest    "x1,y1,x2,y2"]
```

**Arguments**

| Argument | Default | Description |
|----------|---------|-------------|
| `--cam_info_path` | `$PWD/camInfo` | Directory of `*.yml` / `*.yaml` files produced by `generate_cam_info_configs.py`. |
| `--mqtt_brokers` | `127.0.0.1:1883` | Comma-separated broker `host:port` list. For a Docker Compose deployment a single entry is sufficient; if multiple entries are provided, cameras (sorted by filename) are distributed evenly across the brokers. |
| `--minimum_object_size` | `50` | Minimum projected object height in pixels for a world-plane sample to count as visible (used when building per-camera FOV masks). Smaller values make masks broader (more neighbours); larger values make them stricter (fewer, more confidently overlapping neighbours). |
| `--neighbor_criteria` | `overlap_threshold:<2/(1920*1080)>` | Either `top_N:K` (keep the K cameras with the largest overlap) or `overlap_threshold:T` (keep cameras whose overlap fraction is ≥ T). |
| `--output_path` | `./peer_configs` | Output directory; the script writes `pub_sub_info_config.yml` inside it (created if missing). |
| `--range_of_interest` | bounding box of camera positions, padded by 20 units | World-plane sampling region `x1,y1,x2,y2` in the same units as `projectionMatrix_3x4_w2p`. |

**Output `pub_sub_info_config.yml` shape**

```yaml
pubBrokerTopicStr:
  Camera_01: 127.0.0.1:1883;/trck/Camera_01
  Camera_02: 127.0.0.1:1883;/trck/Camera_02
subPeerBrokerTopicStrs:
  Camera_01:
  - 127.0.0.1:1883;/trck/Camera_02
  Camera_02:
  - 127.0.0.1:1883;/trck/Camera_01
```

When multiple brokers are supplied, the `host:port` prefix of each entry
reflects the instance the corresponding camera was assigned to.
