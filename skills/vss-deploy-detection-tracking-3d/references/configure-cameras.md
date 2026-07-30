# Configure Cameras And Stage Configs

Load this reference after the input source is known and before launching or redeploying standalone RT-CV-3D MV3DT.

## Contents

- [Inputs To Resolve](#inputs-to-resolve)
- [Validate Calibration](#validate-calibration)
- [Broker Mode](#broker-mode)
- [Generate Runtime Camera Configs](#generate-runtime-camera-configs)
- [Resolve BEV Assets](#resolve-bev-assets)
- [Configure `docker/.env`](#configure-dockerenv)
- [File Input Checks](#file-input-checks)
- [Display And Save-Video Decision](#display-and-save-video-decision)
- [Stage Configs](#stage-configs)
- [RTSP Stream Registration](#rtsp-stream-registration)

## Inputs To Resolve

- `RTCV3D_APP`: `services/rtvi/rt-cv-3d/rt-cv-mv3dt`.
- `CALIBRATION_JSON`: path to the user's `calibration.json`.
- `INPUT_MODE`: `file` for local MP4s or `stream` for RTSP.
- `VIDEO_DIR`: required only for `INPUT_MODE=file`.
- `NUM_CAMS`: count of valid camera sensors from `calibration.json` where `type == "camera"`.
- Broker mode: bundled brokers by default; external MQTT/Kafka only when explicitly requested.
- Visualization choice: live OSD, saved output, live BEV, saved BEV, both, or neither. Saved output means perception grid plus fused BEV by default.
- `BEV_DATASET_PATH`: required for live/saved BEV and must contain both `map.png` and `transforms.yml`.

If `CALIBRATION_JSON` is missing, stop here and load `calibration-workflow.md`.

## Validate Calibration

The standalone generator processes only sensors whose `type` is `camera`; validation must use that same filtered list.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
CALIBRATION_JSON="${CALIBRATION_JSON:?set path to calibration.json}"
test -f "${CALIBRATION_JSON}" || { echo "ERROR: calibration.json not found: ${CALIBRATION_JSON}"; exit 1; }

mkdir -p generated/run-state
CAMERA_ENV="generated/run-state/cameras.env"
CALIBRATION_JSON="${CALIBRATION_JSON}" python3 - <<'PY' > "${CAMERA_ENV}.tmp"
import json, os, re
path = os.environ['CALIBRATION_JSON']
with open(path, encoding='utf-8') as f:
    d = json.load(f)
sensors = d.get('sensors')
if not isinstance(sensors, list):
    raise SystemExit("ERROR: calibration.json must contain a sensors list")
ids = []
for s in sensors:
    if not isinstance(s, dict) or s.get('type') != 'camera':
        continue
    sid = s.get('id')
    if not isinstance(sid, str) or not sid:
        raise SystemExit("ERROR: every camera sensor needs a non-empty string id")
    if sid in {'.', '..'} or '/' in sid or '\\' in sid:
        raise SystemExit(f"ERROR: unsafe camera id for filename/path use: {sid!r}")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in sid):
        raise SystemExit(f"ERROR: camera id contains control characters: {sid!r}")
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', sid):
        raise SystemExit(f"ERROR: camera id must contain only letters, digits, dot, underscore, or dash: {sid!r}")
    if 'cameraMatrix' not in s:
        raise SystemExit(f"ERROR: camera sensor {sid!r} is missing cameraMatrix")
    ids.append(sid)
if len(ids) < 2:
    raise SystemExit(f"ERROR: MV3DT requires at least 2 camera sensors; found {len(ids)}")
if len(ids) != len(set(ids)):
    raise SystemExit("ERROR: camera sensor ids must be unique")
print("NUM_CAMS=" + str(len(ids)))
print("CAMERA_IDS=" + ",".join(ids))
PY
mv "${CAMERA_ENV}.tmp" "${CAMERA_ENV}"
NUM_CAMS="$(awk -F= '$1 == "NUM_CAMS" {print $2}' "${CAMERA_ENV}")"
CAMERA_IDS="$(awk -F= '$1 == "CAMERA_IDS" {print $2}' "${CAMERA_ENV}")"
test -n "${NUM_CAMS}" && test -n "${CAMERA_IDS}" || { echo "ERROR: failed to derive NUM_CAMS/CAMERA_IDS" >&2; exit 1; }
export NUM_CAMS CAMERA_IDS
set_env_value() {
  key="$1"; value="$2"; file="${RTCV3D_APP}/docker/.env"; tmp="${file}.tmp"
  awk -v key="${key}" -v value="${value}" 'BEGIN{done=0} $0 ~ "^" key "=" {$0=key "=" value; done=1} {print} END{if(!done) print key "=" value}' "${file}" > "${tmp}"
  mv "${tmp}" "${file}"
}
set_env_value NUM_CAMS "${NUM_CAMS}"
printf 'NUM_CAMS=%s\nCAMERA_IDS=%s\n' "${NUM_CAMS}" "${CAMERA_IDS}"
```

Use the exported `NUM_CAMS` and `CAMERA_IDS` from `generated/run-state/cameras.env`. File-mode MP4 basenames and RTSP registration keys must match `CAMERA_IDS` exactly.

## Broker Mode

Default to bundled brokers. Use external brokers only when the user explicitly asks. Initialize broker state before `generate-configs.sh` or `stage-configs.sh` so generated pub/sub config and staged DeepStream Kafka sink use the final selected broker endpoints. If deployment will happen in a later shell, write the same broker values, including `USE_EXTERNAL_BROKERS`, into standalone `docker/.env`.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
USE_EXTERNAL_BROKERS="${USE_EXTERNAL_BROKERS:-$(read_env USE_EXTERNAL_BROKERS)}"; USE_EXTERNAL_BROKERS="${USE_EXTERNAL_BROKERS:-0}"
REQUEST_EXTERNAL_BROKERS="${REQUEST_EXTERNAL_BROKERS:-${USE_EXTERNAL_BROKERS}}"
RAW_TOPIC="${RAW_TOPIC:-$(read_env RAW_TOPIC)}"; RAW_TOPIC="${RAW_TOPIC:-mdx-raw}"
FUSED_TOPIC="${FUSED_TOPIC:-$(read_env FUSED_TOPIC)}"; FUSED_TOPIC="${FUSED_TOPIC:-mdx-bev}"
KAFKA_PORT="${KAFKA_PORT:-$(read_env KAFKA_PORT)}"

if [ "${REQUEST_EXTERNAL_BROKERS}" = 1 ]; then
  MQTT_HOST="${MQTT_HOST:?set external MQTT_HOST}"
  MQTT_PORT="${MQTT_PORT:?set external MQTT_PORT}"
  KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:?set external KAFKA_BOOTSTRAP}"
  USE_EXTERNAL_BROKERS=1
else
  MQTT_HOST="${MQTT_HOST:-$(read_env MQTT_HOST)}"; MQTT_HOST="${MQTT_HOST:-localhost}"
  MQTT_PORT="${MQTT_PORT:-$(read_env MQTT_PORT)}"; MQTT_PORT="${MQTT_PORT:-1883}"
  KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-$(read_env KAFKA_BOOTSTRAP)}"
  KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}"
  USE_EXTERNAL_BROKERS=0
fi
export MQTT_HOST MQTT_PORT KAFKA_BOOTSTRAP RAW_TOPIC FUSED_TOPIC KAFKA_PORT USE_EXTERNAL_BROKERS
printf 'USE_EXTERNAL_BROKERS=%s\nMQTT_HOST=%s\nMQTT_PORT=%s\nKAFKA_BOOTSTRAP=%s\nRAW_TOPIC=%s\nFUSED_TOPIC=%s\n' \
  "${USE_EXTERNAL_BROKERS}" "${MQTT_HOST}" "${MQTT_PORT}" "${KAFKA_BOOTSTRAP}" "${RAW_TOPIC}" "${FUSED_TOPIC}"
```

### Bundled Brokers

Before generating camera configs or staging DeepStream configs, load `references/deploy-rtvi-cv-3d-stack.md` and run its `Bundled Resource Preflight`. That preflight may change `MQTT_PORT`, `KAFKA_PORT`, `KAFKA_CONTROLLER_PORT`, `DS_HTTP_PORT`, and `KAFKA_BOOTSTRAP` in standalone `docker/.env`; running it after staging can leave generated configs pointed at the old defaults.

```bash
cd "${RTCV3D_APP}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
USE_EXTERNAL_BROKERS=0
MQTT_HOST="${MQTT_HOST:-$(read_env MQTT_HOST)}"; MQTT_HOST="${MQTT_HOST:-localhost}"
MQTT_PORT="${MQTT_PORT:-$(read_env MQTT_PORT)}"; MQTT_PORT="${MQTT_PORT:-1883}"
KAFKA_PORT="${KAFKA_PORT:-$(read_env KAFKA_PORT)}"
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-$(read_env KAFKA_BOOTSTRAP)}"; KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}"
export USE_EXTERNAL_BROKERS MQTT_HOST MQTT_PORT KAFKA_BOOTSTRAP
printf 'bundled brokers selected: mqtt=%s:%s kafka=%s\n' "${MQTT_HOST}" "${MQTT_PORT}" "${KAFKA_BOOTSTRAP}"
```

Do not launch file-mode perception from this section. After camera configuration, use `references/deploy-rtvi-cv-3d-stack.md` for the actual launch so file-mode Kafka baselines and optional BEV assignment are established before `perception` starts.

For stream mode only, when no BEV prestart is required, launch later with:

```bash
cd "${RTCV3D_APP}/docker"
COMPOSE_PROFILES=mosquitto,kafka docker compose up -d
```

### External Brokers

For a plain external-host broker request, collect and confirm `MQTT_HOST`, `MQTT_PORT`, and `KAFKA_BOOTSTRAP`, then run the broker initialization block above with `REQUEST_EXTERNAL_BROKERS=1`. Advanced TLS/auth configuration remains delegated to the standalone README custom-broker section.

Validate endpoint reachability before config generation. This is a TCP reachability check; protocol-level TLS/auth validation belongs to the advanced path.

```bash
REQUEST_EXTERNAL_BROKERS=1
MQTT_HOST="${MQTT_HOST:?set external MQTT_HOST}"
MQTT_PORT="${MQTT_PORT:?set external MQTT_PORT}"
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:?set external KAFKA_BOOTSTRAP}"
USE_EXTERNAL_BROKERS=1
export MQTT_HOST MQTT_PORT KAFKA_BOOTSTRAP USE_EXTERNAL_BROKERS
python3 - <<'PY'
import os, socket
endpoints = [(os.environ['MQTT_HOST'], int(os.environ['MQTT_PORT']))]
for item in os.environ['KAFKA_BOOTSTRAP'].split(','):
    host, port = item.rsplit(':', 1)
    endpoints.append((host.strip(), int(port)))
for host, port in endpoints:
    try:
        with socket.create_connection((host, port), timeout=5):
            print(f"reachable: {host}:{port}")
    except OSError as exc:
        raise SystemExit(f"ERROR: cannot reach {host}:{port}: {exc}")
PY
```

Generate the MQTT pub/sub config with the external broker explicitly; `generate-configs.sh` does not read `docker/.env` automatically. Do not rely on the container startup rewrite to replace hostnames later: the generated config must already contain the intended hostname endpoint.

```bash
cd "${RTCV3D_APP}"
MQTT_BROKERS="${MQTT_HOST}:${MQTT_PORT}" ./scripts/generate-configs.sh "${CALIBRATION_JSON}"
```

Confirm the generated config contains the intended broker endpoint using the utility venv Python, where PyYAML is already installed by the standalone helpers:

```bash
cd "${RTCV3D_APP}"
# shellcheck disable=SC1091
source "${RTCV3D_APP}/scripts/ensure-venv.sh"
ensure_venv || { echo "ERROR: could not set up utils/venv" >&2; exit 1; }
MQTT_ENDPOINT="${MQTT_HOST}:${MQTT_PORT}" "${VENV_PY}" - <<'PY'
import os, yaml
path = 'generated/pub_sub_info_config.yml'
endpoint = os.environ['MQTT_ENDPOINT']
with open(path, encoding='utf-8') as f:
    d = yaml.safe_load(f)
values = []
values.extend((d.get('pubBrokerTopicStr') or {}).values())
for peers in (d.get('subPeerBrokerTopicStrs') or {}).values():
    values.extend(peers or [])
bad = [v for v in values if not isinstance(v, str) or not v.startswith(endpoint + ';/trck/')]
if bad:
    raise SystemExit(f"ERROR: generated pub/sub config does not consistently use {endpoint}: {bad[:3]}")
print(f"pub/sub config uses {endpoint}")
PY
```

Do not launch file-mode perception from this section. After camera configuration, use `references/deploy-rtvi-cv-3d-stack.md` so file-mode Kafka baselines and optional BEV assignment are established before `perception` starts.

For stream mode only, when no BEV prestart is required, launch later without bundled broker profiles:

```bash
cd "${RTCV3D_APP}/docker"
docker compose up -d
```

Verify `mdx-raw` and `mdx-bev` with the configured `KAFKA_BOOTSTRAP` using the Kafka CLI offset checks in `verify-and-view.md`; use `timeout` around `scripts/kafka-dump.sh` for bounded samples.

## Generate Runtime Camera Configs

```bash
cd "${RTCV3D_APP}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
MQTT_HOST="${MQTT_HOST:-$(read_env MQTT_HOST)}"; MQTT_HOST="${MQTT_HOST:-localhost}"
MQTT_PORT="${MQTT_PORT:-$(read_env MQTT_PORT)}"; MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_BROKERS="${MQTT_HOST}:${MQTT_PORT}" ./scripts/generate-configs.sh "${CALIBRATION_JSON}"
CAMINFO_COUNT="$(find generated/camInfo -maxdepth 1 -type f -name '*.yml' | wc -l | tr -d ' ')"
test "${CAMINFO_COUNT}" = "${NUM_CAMS}" || { echo "ERROR: generated camInfo count ${CAMINFO_COUNT} != NUM_CAMS ${NUM_CAMS}"; exit 1; }
```

This produces:

- `generated/camInfo/<sensor>.yml`
- `generated/pub_sub_info_config.yml`

## Resolve BEV Assets

Run this section whenever live BEV is requested, saved BEV is requested, or saved output is selected/defaulted. The BEV visualizer cannot run from `calibration.json` alone; `BEV_DATASET_PATH` must be one directory containing both files:

- `map.png`: the BEV/floor-plan image used during calibration
- `transforms.yml`: the world-ground-plane to map-pixel transform used by the visualizer

Resolve assets in this order:

1. Use an explicit `BEV_DATASET_PATH` only if it already contains both files.
2. If calibration came from the AMC handoff, use the `BEV_DATASET_PATH` returned by `calibration-workflow.md` only if both files are present.
3. If the user supplied `MAP_PNG` and `TRANSFORMS_YML`, stage them into `generated/bev-dataset/` with stable names and use that directory.
4. If the user supplied `MAP_PNG` but not `TRANSFORMS_YML`, look for `transforms.yml` near `calibration.json` and AMC output directories before generating it.
5. If the correct calibration map image is available but no transform file exists, generate transforms with `scripts/generate-transforms.sh` and write the result under `generated/bev-dataset/`.
6. If neither path works, request the map image used during calibration or a directory containing both files. Do not run BEV visualization, and do not claim saved BEV output, until both files exist.

```bash
cd "${RTCV3D_APP}"
CALIBRATION_JSON="${CALIBRATION_JSON:?set CALIBRATION_JSON}"
BEV_READY=0
for candidate in   "${BEV_DATASET_PATH:-}"   "$(dirname "${CALIBRATION_JSON}")"   "$(dirname "${CALIBRATION_JSON}")/bev-dataset"   "$(dirname "${CALIBRATION_JSON}")/bev"   "${RTCV3D_APP}/generated/bev-dataset"; do
  if [ -n "${candidate}" ] && [ -f "${candidate}/map.png" ] && [ -f "${candidate}/transforms.yml" ]; then
    BEV_DATASET_PATH="$(cd "${candidate}" && pwd)"
    BEV_READY=1
    break
  fi
done
if [ "${BEV_READY}" = 0 ] && [ -n "${MAP_PNG:-}" ] && [ -z "${TRANSFORMS_YML:-}" ]; then
  for candidate in     "$(dirname "${CALIBRATION_JSON}")/transforms.yml"     "$(dirname "${CALIBRATION_JSON}")/bev-dataset/transforms.yml"     "$(dirname "${CALIBRATION_JSON}")/bev/transforms.yml"     "${RTCV3D_APP}/generated/bev-dataset/transforms.yml"; do
    [ -f "${candidate}" ] && { TRANSFORMS_YML="$(readlink -f "${candidate}")"; break; }
  done
fi
if [ "${BEV_READY}" = 0 ] && [ -n "${MAP_PNG:-}" ] && [ -n "${TRANSFORMS_YML:-}" ]    && [ -f "${MAP_PNG}" ] && [ -f "${TRANSFORMS_YML}" ]; then
  BEV_DATASET_PATH="${RTCV3D_APP}/generated/bev-dataset"
  mkdir -p "${BEV_DATASET_PATH}"
  ln -sfn "$(readlink -f "${MAP_PNG}")" "${BEV_DATASET_PATH}/map.png"
  ln -sfn "$(readlink -f "${TRANSFORMS_YML}")" "${BEV_DATASET_PATH}/transforms.yml"
  BEV_READY=1
fi
if [ "${BEV_READY}" = 0 ] && [ -n "${MAP_PNG:-}" ] && [ -f "${MAP_PNG}" ]; then
  BEV_DATASET_PATH="${RTCV3D_APP}/generated/bev-dataset"
  mkdir -p "${BEV_DATASET_PATH}"
  ln -sfn "$(readlink -f "${MAP_PNG}")" "${BEV_DATASET_PATH}/map.png"
  ./scripts/generate-transforms.sh "${CALIBRATION_JSON}" "${BEV_DATASET_PATH}/map.png" -o "${BEV_DATASET_PATH}/transforms.yml" --force
  BEV_READY=1
fi
if [ "${BEV_READY}" = 1 ]; then
  test -f "${BEV_DATASET_PATH}/map.png" || { echo "ERROR: missing ${BEV_DATASET_PATH}/map.png"; exit 1; }
  test -f "${BEV_DATASET_PATH}/transforms.yml" || { echo "ERROR: missing ${BEV_DATASET_PATH}/transforms.yml"; exit 1; }
  echo "BEV_DATASET_PATH=${BEV_DATASET_PATH}"
else
  echo "BEV assets unresolved: provide BEV_DATASET_PATH with map.png + transforms.yml, or provide MAP_PNG from calibration so transforms.yml can be generated."
fi
```

Do not use `generate-transforms.sh` without the real calibration map image for production BEV output.

## Configure `docker/.env`

Edit only the standalone env file at `docker/.env`. Do not edit warehouse env files.

Required values:

```text
MODELS_DIR=/path/to/app-data/models
NUM_CAMS=<filtered-camera-count-from-calibration>
INPUT_MODE=file        # or stream
VIDEO_DIR=/path/to/videos  # required for file mode
GPU_DEVICE=0
DS_HTTP_PORT=9000
MQTT_HOST=localhost    # or explicit external broker host
MQTT_PORT=1883
KAFKA_BOOTSTRAP=<configured kafka bootstrap>
USE_EXTERNAL_BROKERS=0  # set to 1 only for explicit external broker deployments
RAW_TOPIC=mdx-raw
FUSED_TOPIC=mdx-bev
```

## File Input Checks

Use this for recorded MP4s. There is no `add-streams.sh` step for file input; the container reads one local file per camera from `VIDEO_DIR`.

If the user supplied a directory whose `.mp4` basenames already match generated sensor ids, set `VIDEO_DIR` to that directory. If the user supplied individual files or files with different names, do not rename the source files. Create a generated symlink directory only when each file can be mapped to a sensor id explicitly (`sensor_id=/path/cam.mp4`) or unambiguously by count/order.

```bash
cd "${RTCV3D_APP}"
mkdir -p generated/video-input
ln -sfn /path/to/source_cam_1.mp4 generated/video-input/<sensor_id_1>.mp4
ln -sfn /path/to/source_cam_2.mp4 generated/video-input/<sensor_id_2>.mp4
# Set VIDEO_DIR=${RTCV3D_APP}/generated/video-input in docker/.env
```

Validate file input using the resolved `VIDEO_DIR` value, not by sourcing `.env`:

```bash
cd "${RTCV3D_APP}"
VIDEO_DIR="${VIDEO_DIR:?set VIDEO_DIR from docker/.env or user input}"
test "${INPUT_MODE:-file}" = "file" || { echo "ERROR: INPUT_MODE must be file for recorded MP4 input"; exit 1; }
test -d "${VIDEO_DIR}" || { echo "ERROR: VIDEO_DIR missing: ${VIDEO_DIR}"; exit 1; }
missing=0
for cam_file in generated/camInfo/*.yml; do
  cam="$(basename "${cam_file}" .yml)"
  if [ ! -f "${VIDEO_DIR}/${cam}.mp4" ]; then
    echo "MISSING: ${VIDEO_DIR}/${cam}.mp4"
    missing=1
  fi
done
test "${missing}" = 0 || { echo "ERROR: each camera needs a matching <sensor_id>.mp4"; exit 1; }
```

Recorded clips play once and the perception container exits at end-of-stream.

## Display And Save-Video Decision

Detect display capability before staging configs:

```bash
RTCV3D_DISPLAY_AVAILABLE=0
if [ -n "${DISPLAY:-}" ] && [ -d /tmp/.X11-unix ]; then
  if command -v xdpyinfo >/dev/null 2>&1 && xdpyinfo >/dev/null 2>&1; then
    RTCV3D_DISPLAY_AVAILABLE=1
  elif command -v xset >/dev/null 2>&1 && xset q >/dev/null 2>&1; then
    RTCV3D_DISPLAY_AVAILABLE=1
  fi
fi
echo "RTCV3D_DISPLAY_AVAILABLE=${RTCV3D_DISPLAY_AVAILABLE}"
```

Selection rules:

- If the user asked to save video or save output, set `SAVE_VIDEO=1` regardless of display availability and save fused BEV by default after `BEV_DATASET_PATH` resolves with both required files.
- If the user asked for both live and saved output, set `OSD=1 SAVE_VIDEO=1` when display is available and start saved fused BEV in parallel.
- If display is available and the user did not ask to save, set `OSD=1 SAVE_VIDEO=0`.
- If display is not available, tell the user no working display was detected and use saved output as the fallback: set `SAVE_VIDEO=1` and save both perception grid and fused BEV after BEV assets resolve.
- Do not run broad `xhost +`. Ask before changing X11 access and prefer scoped access.

`SAVE_VIDEO=1` only controls the perception camera-grid sink. Saved BEV is a separate `scripts/bev-visualizer.sh` process with `BEV_SAVE_VIDEO=1 BEV_SOURCE=fused`; start it before data flows.

## Stage Configs

Choose one command after the display/save decision:

```bash
cd "${RTCV3D_APP}"
INPUT_MODE=stream OSD=0 SAVE_VIDEO=0 ./scripts/stage-configs.sh
INPUT_MODE=stream OSD=1 SAVE_VIDEO=0 ./scripts/stage-configs.sh
INPUT_MODE=stream OSD=0 SAVE_VIDEO=1 ./scripts/stage-configs.sh
INPUT_MODE=stream OSD=1 SAVE_VIDEO=1 ./scripts/stage-configs.sh
INPUT_MODE=file OSD=1 SAVE_VIDEO=0 ./scripts/stage-configs.sh
INPUT_MODE=file OSD=0 SAVE_VIDEO=1 ./scripts/stage-configs.sh
INPUT_MODE=file OSD=1 SAVE_VIDEO=1 ./scripts/stage-configs.sh
```

Expected outputs:

- `generated/configs/`
- `generated/configs/ds-main-config-mv3dt.txt`
- `generated/configs/ds-mv3dt-tracker-config.yml`

Assert the staged Kafka sink matches the selected broker after every staging run:

```bash
cd "${RTCV3D_APP}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
KAFKA_BOOTSTRAP_EFFECTIVE="${KAFKA_BOOTSTRAP:-$(read_env KAFKA_BOOTSTRAP)}"; KAFKA_BOOTSTRAP_EFFECTIVE="${KAFKA_BOOTSTRAP_EFFECTIVE:-localhost:${KAFKA_PORT:-9092}}"
RAW_TOPIC_EFFECTIVE="${RAW_TOPIC:-$(read_env RAW_TOPIC)}"; RAW_TOPIC_EFFECTIVE="${RAW_TOPIC_EFFECTIVE:-mdx-raw}"
KAFKA_HOST="${KAFKA_BOOTSTRAP_EFFECTIVE%%:*}"
KAFKA_PORT_ONLY="${KAFKA_BOOTSTRAP_EFFECTIVE##*:}"
EXPECTED_CONN="msg-broker-conn-str=${KAFKA_HOST};${KAFKA_PORT_ONLY};${RAW_TOPIC_EFFECTIVE}"
grep -qxF "${EXPECTED_CONN}" generated/configs/ds-main-config-mv3dt.txt || {
  echo "ERROR: staged Kafka sink does not match selected broker: expected ${EXPECTED_CONN}" >&2
  grep '^msg-broker-conn-str=' generated/configs/ds-main-config-mv3dt.txt >&2 || true
  exit 1
}
```

If saved BEV is selected/defaulted, verify resolved BEV assets before launch:

```bash
test -f "${BEV_DATASET_PATH:?set BEV_DATASET_PATH}/map.png" || { echo "ERROR: BEV map.png missing"; exit 1; }
test -f "${BEV_DATASET_PATH}/transforms.yml" || { echo "ERROR: BEV transforms.yml missing"; exit 1; }
```

If these assets are missing, return to `Resolve BEV Assets`. Continue with perception-grid-only output only when BEV output was not requested or after reporting that saved/live BEV is blocked until the assets are provided.

## RTSP Stream Registration

Use this only after compose is running and `docker logs vss-rtvi-cv-mv3dt` shows `ds-ready: YES`.

If the user supplied RTSP URLs, run this registration step as part of deployment. Use explicit `<sensor_id>=<rtsp_url>` pairs when present. If the user supplied only bare RTSP URLs, map them to calibration sensor ids in order only when the URL count exactly matches the generated camInfo count and the order is clear from the calibration handoff; otherwise ask the user for the sensor-id mapping before registering streams.

```bash
cd "${RTCV3D_APP}"
./scripts/add-streams.sh   '<sensor_id_1>=rtsp://host/path1'   '<sensor_id_2>=rtsp://host/path2'

./scripts/add-streams.sh --list
./scripts/add-streams.sh --remove '<sensor_id_1>=rtsp://host/path1'
```

Validate exact stream count and camera IDs after registration:

```bash
cd "${RTCV3D_APP}"
mkdir -p generated/run-state
EXPECTED_IDS="$(find generated/camInfo -maxdepth 1 -type f -name '*.yml' -printf '%f
' | sed 's/\.yml$//' | LC_ALL=C sort | paste -sd, -)"
./scripts/add-streams.sh --list > generated/run-state/stream-info.txt
EXPECTED_IDS="${EXPECTED_IDS}" python3 - <<'PY'
import os, re
expected = [x for x in os.environ['EXPECTED_IDS'].split(',') if x]
text = open('generated/run-state/stream-info.txt', encoding='utf-8').read()
count_match = re.search(r'stream-count:\s*(\d+)', text)
count = int(count_match.group(1)) if count_match else -1
ids = sorted(re.findall(r'camera_id=([^\s]+)', text))
if count != len(expected):
    raise SystemExit(f"ERROR: registered stream-count {count} != expected {len(expected)}")
if ids != sorted(expected):
    raise SystemExit(f"ERROR: registered camera ids {ids} != expected {sorted(expected)}")
print("registered stream set matches calibration")
PY
```

Each `<sensor_id>` must exactly match a file in `generated/camInfo/<sensor_id>.yml` and an id in `calibration.json`.
