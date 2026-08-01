# Deploy the RTVI-CV-3D (MV3DT) stack

The actual `docker compose up` recipe. Parent: [`../SKILL.md`](../SKILL.md). Run this **after** Q0/Q1/Q2/Q3 in SKILL.md resolved and calibration is on disk. For custom videos, RTSP, or user-supplied calibration, run [`configure-cameras.md`](configure-cameras.md) first so camera names and `NUM_STREAMS` are validated. The bundled sample dataset is already normalized.

## What this brings up

`MODE=mv3dt` + `BP_PROFILE=bp_wh_kafka` (or `bp_wh_redis`) selects the
`COMPOSE_PROFILES_WH_KAFKA_MV3DT` (or `COMPOSE_PROFILES_WH_REDIS_MV3DT`)
service list. With `MINIMAL_PROFILE="true"`, select the corresponding
`*_MV3DT_MINIMAL` list instead of the extended list:

### Always deployed (under either MV3DT service list)

| Container | Image | Role |
|---|---|---|
| `vss-rtvi-cv-mv3dt` | `nvcr.io/nvidia/vss-core/vss-rt-cv:${PERCEPTION_TAG}` | Per-camera DeepStream perception |
| `vss-rtvi-cv-bev-fusion` | `nvcr.io/nvidia/vss-core/vss-rt-cv-mv3dt-bev-fusion:${BEV_FUSION_MV3DT_TAG}` | BEV Fusion — fuses per-camera detections to a single BEV frame |
| `mosquitto` | `eclipse-mosquitto:2` | MQTT bus between perception and fusion |
| `kafka` *or* `redis` | (per `STREAM_TYPE`) | Carries `mdx-raw` (input) and `mdx-bev` (output) |
| `vss-broker-health-check` | (built locally) | Validates broker + creates topics (one-shot, exits 0) |
| `vss-vios-sensor` | VST sensor image | VST sensor microservice |
| `vss-vios-ingress` | VST | VST ingress (healthy) |
| `vss-vios-streamprocessing` | VST | Records streams; serves the VST video wall |
| `vss-vios-postgres` (PostgreSQL) | postgres | Backing store for VST sensor-ms |
| `sdr-controller` | (built locally) | SDR + Envoy consolidation (registers streamprocessing) |
| `vss-configurator-mv3dt` (+ `*-init`) | `nvcr.io/nvidia/vss-core/vss-configurator` | Sensor registration, DeepStream config materialization |
| `vss-vios-nvstreamer-mv3dt` | nvstreamer | RTSP server for sample/videos data |
| **`vss-behavior-analytics-mv3dt`** | analytics | 3D spatial analytics — present in every Kafka/Redis MV3DT service list, **not** gated by `MINIMAL_PROFILE` |

> **Auto-calibration is not part of this deploy.** AMC (`vss-auto-calibration` / `-ui`) is **not** in the Kafka/Redis MV3DT service lists. When calibration is missing, [`calibration-workflow.md`](calibration-workflow.md) delegates AMC setup and RTSP capture to the `vss-generate-video-calibration` skill, then tears AMC down before this deploy. If you see `vss-auto-calibration` running alongside MV3DT, it's from that separate calibration flow, not this one.

### Extra under extended (`MINIMAL_PROFILE=""`) — needed for VST overlays

| Container | Why |
|---|---|
| `elasticsearch` + `vss-elasticsearch-init` | Backing store for the `mdx-bev` index; VST renders overlays only when this is populated |
| `logstash` | Pipes broker metadata → Elasticsearch |
| `kibana` + `vss-kibana-init-mv3dt` | Dashboards (also needed for overlay rendering) |
| `vss-video-analytics-api-mv3dt` | Serves overlay data to VST |
| `vss-import-calibration-output-mv3dt` | Imports the `calibration.json` into Elasticsearch |
| `vss-haproxy-ingress` | Ingress for the extended stack |

These services are included in the extended `COMPOSE_PROFILES_WH_*_MV3DT`
lists and omitted from the corresponding `*_MV3DT_MINIMAL` lists. Select the
appropriate complete list; do not add or remove these service profiles
individually.

**Recommendation: default to extended** for any user who wants a complete e2e experience including overlays. Drop to minimal only when explicitly asked for the smallest footprint (edge / Thor / "just give me the topic data").

## Step 0 — Pre-deploy host-path checks

Don't trust `docker compose config` to catch missing bind-mount sources — it doesn't validate host paths. Run these first:

```bash
ENV_STABLE="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env"
ENV_FILE="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/generated.env"
[ -f "${ENV_FILE}" ] || {
  echo "ERROR: ${ENV_FILE} is missing. Copy overrides.env to generated.env and apply this deployment's settings first."
  exit 1
}

# Re-source stable defaults first, then runtime/profile overrides, so we can check them
set -a; . "${ENV_STABLE}"; . "${ENV_FILE}"; set +a

# 1. App-data layout. RTSP still needs models and data_log, but not dataset MP4s.
for sub in models data_log videos; do
  test -d "${VSS_DATA_DIR}/${sub}" || { echo "ERROR: ${VSS_DATA_DIR}/${sub} missing — VSS_DATA_DIR is not pointing at extracted vss-warehouse-app-data"; exit 1; }
done

# 2. Source-specific input check
if [ "${SENSOR_INFO_SOURCE:-nvstreamer}" = "file" ]; then
  SENSOR_FILE="${SENSOR_FILE_PATH:-${VSS_APPS_DIR}/industry-profiles/warehouse-operations/camera_configs/camera_info.json}"
  test -f "${SENSOR_FILE}" || { echo "ERROR: SENSOR_FILE_PATH=${SENSOR_FILE} missing"; exit 1; }

  if ! jq -e '.sensors | type == "array" and length > 0' "${SENSOR_FILE}" >/dev/null; then
    echo "ERROR: ${SENSOR_FILE} must contain sensors[]"
    exit 1
  fi
  if ! jq -e '.sensors[] | (.camera_name // "") != "" and (.rtsp_url // "") != "" and (.group_id // "") != "" and (.region // "") != ""' "${SENSOR_FILE}" >/dev/null; then
    echo "ERROR: each RTSP sensor needs camera_name, rtsp_url, group_id, and region"
    exit 1
  fi

  SENSOR_COUNT=$(jq '.sensors | length' "${SENSOR_FILE}")
  if [ "${SENSOR_COUNT}" != "${NUM_STREAMS}" ]; then
    echo "ERROR: camera_info sensors (${SENSOR_COUNT}) must equal NUM_STREAMS (${NUM_STREAMS})"
    exit 1
  fi

  # Keeps the compose bind mount explicit even for external RTSP.
  mkdir -p "${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}"
  echo "RTSP sensor file OK: ${SENSOR_FILE} (${SENSOR_COUNT} sensors)"
else
  if [ ! -d "${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}" ]; then
    echo "ERROR: ${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET} missing"
    exit 1
  fi
  VIDEO_COUNT=$(ls "${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}/"*.mp4 2>/dev/null | wc -l)
  echo "Found ${VIDEO_COUNT} videos under ${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}/"
fi

# 3. Calibration mount
CAL_DIR="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}"
test -f "${CAL_DIR}/calibration.json" || { echo "ERROR: ${CAL_DIR}/calibration.json missing"; exit 1; }
CAM_COUNT=$(ls "${CAL_DIR}/camInfo/"*.{yml,yaml} 2>/dev/null | wc -l)
echo "Found ${CAM_COUNT} calibration files under ${CAL_DIR}/camInfo/"
[ "${CAM_COUNT}" = "${NUM_STREAMS}" ] || { echo "ERROR: camInfo count (${CAM_COUNT}) must equal NUM_STREAMS (${NUM_STREAMS})"; exit 1; }

# 4. The configurator enforces min(NUM_STREAMS, HARDWARE_PROFILE.max_streams_supported).
#    For sample/videos it may trim dataset MP4s; for RTSP keep camera_info.json,
#    calibration, and NUM_STREAMS already aligned before deploy.
echo "NUM_STREAMS=${NUM_STREAMS}, HARDWARE_PROFILE=${HARDWARE_PROFILE}"
```

If `NUM_STREAMS` is above the supported MV3DT stream count for the hardware, the deploy may process only a subset of streams. For sample/videos, fix one of: source missing videos, raise the hardware-supported cap, or lower expectations. For RTSP, keep `camera_info.json`, calibration, and `NUM_STREAMS` at the supported count before deploy.

### Step 0a — Detect stale state from a prior deploy (redeploys only)

A prior deploy leaves two kinds of stale state that get silently reused and break the next `up`. On a fresh host both checks are no-ops. On a redeploy, run them **before** `up`.

**(i) Stale `<project>_*` named volumes.** MV3DT's `kafka` / `elastic` / `postgres` data live in Docker **named volumes** (`vss_mdx-kafka`, `vss_vios_pg_data`, … by default) that bind to a host path baked in **at volume-creation time**. If `VSS_DATA_DIR` has changed since the last deploy, the next `up` fails with `failed to mount local volume: … no such file or directory`. This is detectable with nothing running:

```bash
CUR="${VSS_DATA_DIR%/}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-vss}"
STALE_VOL=0
if [ -z "${CUR}" ]; then
  echo "VSS_DATA_DIR is not set — source .env followed by generated.env (Step 0) before running this check."
else
  for v in $(docker volume ls -q | grep -E "^${COMPOSE_PROJECT_NAME}_"); do
    dev=$(docker volume inspect "$v" --format '{{.Options.device}}' 2>/dev/null)
    case "$dev" in
      "${CUR}"/*|"") ;;                               # current path or non-bind — fine
      *) echo "STALE volume ${v} -> ${dev}"; STALE_VOL=1 ;;
    esac
  done
  [ "$STALE_VOL" = 1 ] && echo "Stale ${COMPOSE_PROJECT_NAME}_* volumes point outside VSS_DATA_DIR=${CUR} — reset with 'down -v' below."
fi
```

> **A passing path-check does *not* mean the volumes are state-free.** This check only flags volumes whose baked path points *outside* the current `VSS_DATA_DIR`. On a same-host redeploy with the **same** `VSS_DATA_DIR`, the `<project>_*` volumes pass silently yet still carry the prior deploy's VST Postgres sensor records (`vss_vios_pg_data` by default) and Kafka offsets (`vss_mdx-kafka` by default) — which is a common cause of `Active sources : 0` after an otherwise clean-looking redeploy. So treat this check as "will the volume mount," not "is it empty." For any **clean-redeploy intent** (new dataset, changed camera set/names, or any "stuck at 0 sources" reset), reset the volumes with `down -v` regardless of the path result — see (ii) below and the clean-redeploy callout before Step 3.

**(ii) Stale VST sensor records.** A prior deploy's VST Postgres DB and configurator state survive a plain `docker compose down`, so old sensor records (a different dataset, a removed camera, or empty/offline entries) get reused and perception stalls at `Active sources : 0` while containers still look healthy. Only checkable when VST is already up:

```bash
VST_HOST="${HOST_IP:-localhost}"; VST_PORT="${VST_PORT:-30888}"
CAL_DIR="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}"

if docker ps --format '{{.Names}}' | grep -q '^vss-vios-sensor$'; then
  EXISTING=$(curl -sf "http://${VST_HOST}:${VST_PORT}/vst/api/v1/sensor/list" 2>/dev/null \
    | jq -r '.[].name' 2>/dev/null | sort)
  EXPECTED=$(jq -r '.sensors[].id' "${CAL_DIR}/calibration.json" 2>/dev/null | sort)
  echo "VST already running."
  echo "Registered sensors:"; echo "${EXISTING:-(none)}"
  echo "Expected for ${SAMPLE_VIDEO_DATASET}:"; echo "${EXPECTED:-(unknown)}"
  if [ -z "${EXPECTED}" ]; then
    # calibration.json wasn't readable — skip the comparison rather than flag a
    # false-positive that would recommend a destructive down -v. Fix CAL_DIR /
    # SAMPLE_VIDEO_DATASET first (these come from the Step 0 env-layer sourcing).
    echo "Could not read expected sensors from ${CAL_DIR}/calibration.json — skipping stale-sensor check."
  elif [ "${EXISTING}" != "${EXPECTED}" ]; then
    echo "STALE / MISMATCHED VST state — the registered sensors do not match this dataset."
    echo "A scoped reset is recommended before deploying (resets VST Postgres + named volumes):"
    echo "  docker compose -f compose.yml --env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env down -v"
    echo "  bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/generated.env --skip-revert-from-oldest-backup"
  else
    echo "VST sensor set matches the expected dataset — no reset needed."
  fi
fi
```

`down -v` is destructive (drops the VST DB and broker volumes), so **ask the user for confirmation before running it.** Full discussion of `down -v` semantics is in [`teardown.md`](teardown.md); the targeted sensor-trim alternative is in [`configure-cameras.md`](configure-cameras.md) Step 5.

### Step 0b — Align `streamprocessing` mounts for custom datasets

`services/vios/streamprocessing/docker-compose.yaml` may include bind-mount sources that point at the bundled sample dataset. For custom datasets, update those sources to resolve through `${SAMPLE_VIDEO_DATASET}` so VST overlay configuration uses the same calibration dataset as the rest of the stack.

Under the `streamprocessing-ms-mv3dt:` service block (`streamprocessing-ms-3d:` mirrors the same pattern for MODE=3d), replace only these source fragments:

The fragments appear embedded within the full `${VSS_APPS_DIR}/.../calibration/` source path; the sed block below handles this automatically.

```text
sample-data/warehouse-4cams-20mx20m-synthetic/calibration.json
sample-data/warehouse-4cams-20mx20m-synthetic/images/Top.png
```

with:

```text
sample-data/${SAMPLE_VIDEO_DATASET}/calibration.json
sample-data/${SAMPLE_VIDEO_DATASET}/images/Top.png
```

Keep each mount destination unchanged.

VST reads from its container configuration directory when rendering 3D bbox overlays on each camera stream. If the mounts still point at the bundled sample dataset while `SAMPLE_VIDEO_DATASET` points at a custom dataset, VST overlays may use the sample `cameraMatrix` while perception, behavior-analytics, and video-analytics-api use the custom dataset calibration. Symptom: bbox positions do not align with the VST video wall, the top-view widget shows the sample warehouse layout, and AMC/Kibana overlays look as expected.

Idempotent update — no-op when the sample dataset is in use, no-op after a prior update:

```bash
COMPOSE_SP="${VSS_APPS_DIR}/services/vios/streamprocessing/docker-compose.yaml"

if grep -q 'sample-data/warehouse-4cams-20mx20m-synthetic/calibration\.json' "${COMPOSE_SP}"; then
  sed -i 's|sample-data/warehouse-4cams-20mx20m-synthetic/calibration\.json|sample-data/${SAMPLE_VIDEO_DATASET}/calibration.json|g' "${COMPOSE_SP}"
  sed -i 's|sample-data/warehouse-4cams-20mx20m-synthetic/images/Top\.png|sample-data/${SAMPLE_VIDEO_DATASET}/images/Top.png|g' "${COMPOSE_SP}"
  echo "Patched streamprocessing compose: sample-data path now resolves via \${SAMPLE_VIDEO_DATASET}"
else
  echo "streamprocessing compose already patched (or sample dataset in use) — no change"
fi
```

If the stack is **already running** when you discover this (Step 5 in [`verify-and-view.md`](verify-and-view.md) is showing the sample warehouse layout), apply the patch and recreate the affected container in place — no need to bring the full stack down:

```bash
cd "${VSS_APPS_DIR}"
docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env \
  up -d --no-deps --force-recreate streamprocessing-ms-mv3dt

# VST's per-tab session caches the sensorIds, which change on streamprocessing recreate
# → hard-refresh the VST tab (Ctrl+Shift+R) so the cached streamId is dropped.
```

When the compose source already uses `${SAMPLE_VIDEO_DATASET}`, this step is a no-op and can be skipped.

## Step 1 — Env recipe

Initialize `${VSS_APPS_DIR}/industry-profiles/warehouse-operations/generated.env` from `overrides.env`, then edit `generated.env` for MV3DT runtime/profile overrides. The stable `.env` remains the service-default layer. The shipped `overrides.env` defaults to **2D** (`MODE=2d`, `BP_PROFILE=bp_wh`, `HARDWARE_PROFILE=H100`, paths as placeholders, `NGC_CLI_API_KEY=''`) — you must change at least `MODE`, `BP_PROFILE`, paths, `HOST_IP`, and `NGC_CLI_API_KEY` for MV3DT. Confirm every key below:

> **Also set `LLM_MODE=none` and `VLM_MODE=none`.** MV3DT does not use an LLM or VLM. The inverted MV3DT service lists intentionally contain no `llm_*` or `vlm_*` NIM slice; these values keep the remaining runtime configuration consistent with that deployment shape.

```bash
cd "${VSS_APPS_DIR}"
cp industry-profiles/warehouse-operations/overrides.env industry-profiles/warehouse-operations/generated.env
grep -q '^BP_CONFIGURATOR_ENV_FILE=' industry-profiles/warehouse-operations/generated.env \
  || printf '\nBP_CONFIGURATOR_ENV_FILE=%s/industry-profiles/warehouse-operations/generated.env\n' "${VSS_APPS_DIR}" >> industry-profiles/warehouse-operations/generated.env

# All keys below live in industry-profiles/warehouse-operations/generated.env — locate by name (line numbers drift across releases).
# Deployment selectors
MODE=mv3dt
BP_PROFILE=bp_wh_kafka                      # or bp_wh_redis
STREAM_TYPE=kafka                           # match BP_PROFILE
MINIMAL_PROFILE=""                          # EXTENDED (default for overlays)
# MINIMAL_PROFILE="true"                    # uncomment for minimal (no overlays)

# Dataset + stream count
SAMPLE_VIDEO_DATASET="<your-dataset-slug>"  # see "Slug" note below
NUM_STREAMS=4                               # must equal camInfo count, and RTSP sensor count when used

# RTSP input only. Leave unset/default for sample or local videos.
# SENSOR_INFO_SOURCE=file
# SENSOR_FILE_PATH="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/camera_configs/camera_info.json"

# Hardware — use the slug from SKILL.md Prerequisites §3 (canonical keys live in blueprint_config.yml)
HARDWARE_PROFILE=H100                       # see SKILL.md Prerequisites §3 table
RT_CV_DEVICE_ID='0'                         # GPU for perception
LLM_MODE=none                               # no LLM/VLM for MV3DT
VLM_MODE=none

# Paths (REQUIRED)
VSS_APPS_DIR="<repo>/deploy/docker"         # your checkout's deploy/docker
VSS_DATA_DIR="<extracted-vss-warehouse-app-data>"  # NOT the repo path
HOST_IP='<browser-reachable-IP>'            # not localhost
EXTERNAL_IP="${HOST_IP}"

# MQTT (mv3dt only)
MQTT_HOST=localhost
MQTT_PORT=1883

# NGC credential for image pulls
NGC_CLI_API_KEY='<your-ngc-key>'
```

The checked-in `overrides.env` template defines the explicit service lists.
After copying it to the active per-deployment `generated.env`, set that file's
`COMPOSE_PROFILES` selector to the list for the chosen variant. For MV3DT with
`BP_PROFILE=bp_wh_kafka`, use
`COMPOSE_PROFILES=${COMPOSE_PROFILES_WH_KAFKA_MV3DT}` (or the `_MINIMAL`
variant when requested). MV3DT lists do not include an LLM or VLM slice.

### RTSP input — Sensor Info File

For Q1 = `rtsp`, create a Sensor Info File and set `SENSOR_INFO_SOURCE=file` plus `SENSOR_FILE_PATH` in the active `generated.env` before `docker compose up`. If calibration just ran through [`../../vss-generate-video-calibration/references/rtsp.md`](../../vss-generate-video-calibration/references/rtsp.md), reuse that ordered stream list; only translate `camera_name` to the normalized MV3DT sensor IDs:

```json
{
  "sensors": [
    {
      "camera_name": "Camera",
      "rtsp_url": "rtsp://<host>:<port>/<stream>",
      "group_id": "bev-sensor-1",
      "region": "warehouse"
    },
    {
      "camera_name": "Camera_01",
      "rtsp_url": "rtsp://<host>:<port>/<stream>",
      "group_id": "bev-sensor-1",
      "region": "warehouse"
    }
  ]
}
```

Required fields per sensor: `camera_name`, `rtsp_url`, `group_id`, and `region`. Use the same `camera_name` values that are in the normalized `calibration.json` (`Camera`, `Camera_01`, ...). `NUM_STREAMS`, `camera_info.json` sensor count, `calibration.json` sensor count, and `camInfo/` count must match. Static `NUM_STREAMS` is required for RTSP; the dynamic video-file counting path is for recorded videos only.

For `sample` or `videos`, leave `SENSOR_INFO_SOURCE` unset/default (`nvstreamer`) and keep using the dataset video directory checks below.

### `VSS_DATA_DIR` — what to point it at

This is the warehouse data directory — **separate from the repo**. The extracted `vss-warehouse-app-data` supplies videos/playback/calibration, while ds-start phase 0 downloads RT-CV models into the flattened `models/` tree on first perception startup. Expected active layout:

```
<extracted-dir>/
├── videos/<dataset>/        Camera*.mp4 or cam_*.mp4
├── models/
│   ├── rtdetr_warehouse_v1.0.2.fp16.onnx
│   └── BodyPose3DNet/bodypose3dnet_accuracy.onnx
├── data_log/                 broker / VST log dir (created at deploy)
└── auto-calib/vggt/          optional VGGT model
```

If you need the official sample videos/playback/calibration, use the published warehouse app-data resource from the VSS 3.2.0 manifests. Do not rely on its legacy `models/` subtree; ds-start phase 0 downloads RT-DETR and BodyPose3DNet separately on first perception startup:

```bash
export NGC_CLI_API_KEY='<your-key>'

NGC_CLI_ORG=nvidia ngc registry resource list "nvidia/vss-warehouse/vss-warehouse-app-data:*" --format_type ascii | head -10

ORG=nvidia
TAG=3.2.0
NGC_CLI_ORG="$ORG" ngc registry resource download-version "${ORG}/vss-warehouse/vss-warehouse-app-data:${TAG}"

# The tarball extracts into a nested vss-warehouse-app-data/ directory — flatten it.
cd "vss-warehouse-app-data_v${TAG#v}" || cd "vss-warehouse-app-data_${TAG}"
tar -xvf vss-warehouse-app-data.tar.gz

# Open read perms for container users. Auto-proceed when sudo is passwordless;
# otherwise surface the command for the user to run.
if sudo -n true 2>/dev/null; then
  sudo chmod -R a+rX /path/to/vss-warehouse-app-data
else
  echo "Sudo requires a password on this host. Please run the command below in your shell, then confirm to continue:"
  echo "  sudo chmod -R a+rX /path/to/vss-warehouse-app-data"
fi
# Then point VSS_DATA_DIR at /path/to/vss-warehouse-app-data
```

After extraction, run the `mkdir -p` + scoped-ACL `data_log` permission step from [`../SKILL.md`](../SKILL.md) Prerequisites §4 before deploy — kafka / elasticsearch / redis won't start without it. Also ensure `${VSS_DATA_DIR}/models` exists and is writable; ds-start phase 0 writes downloaded models there.

> For `sample` / `videos`, always verify the video count before deploy — the pre-flight check above prints it. If the count is lower than the dataset name implies (e.g. fewer than the four cameras in `warehouse-4cams-20mx20m-synthetic/`), the GPU's MV3DT cap (SKILL.md Prerequisites §3) determines whether this affects you: if the cap is at or below the present video count, the configurator's `keep_count` op uses what's there; if the cap is higher, source the additional cams separately before deploying. For `rtsp`, validate `camera_info.json` instead of video count.

### `SAMPLE_VIDEO_DATASET` slug

Drives the calibration mount path:

```
${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}/
├── calibration.json
├── camInfo/(Camera*|cam_*).{yml|yaml}
└── images/
```

| User path | Slug to set |
|---|---|
| Sample dataset | `warehouse-4cams-20mx20m-synthetic` (ship-with-repo) |
| User videos (after AMC) | Whatever the user chose in Q3 (e.g. `customer-aisle-4cams`) — [`calibration-workflow.md`](calibration-workflow.md) lands files there |
| User RTSP (after AMC) | Same — Q3 slug |

### SBSA note (DGX-SPARK only)

The only platform that needs an `-sbsa` image tag is **DGX-SPARK**, and only for the **Perception** image. Every other platform uses the shipped non-SBSA tags — including **AGX-THOR / IGX-THOR** (ARM64, but confirmed **not** to need SBSA), GB200, and all x86 GPUs. Do not infer SBSA from the platform being ARM64.

On DGX-SPARK, switch `PERCEPTION_TAG` to its `-sbsa` variant in the active `generated.env`; the checked-in alternatives are documented together in the `overrides.env` template:

```bash
# PERCEPTION_TAG ships an SBSA variant for DGX-SPARK — comment the default, uncomment the -sbsa line:
# PERCEPTION_TAG="3.3.0-26.07.2"
PERCEPTION_TAG="3.3.0-sbsa-26.07.2"
```

The `blueprint-configurator` enforces this: on `HARDWARE_PROFILE=DGX-SPARK` it validates that `PERCEPTION_TAG` contains `sbsa`.

**BEV Fusion needs no SBSA build.** `BEV_FUSION_MV3DT_TAG` is a single image that runs on all platforms including DGX-SPARK — leave it at its shipped tag. There is no `-sbsa` variant for it; don't hand-construct one (the pull would fail).

Treat the generated runtime layer as the edit target — swap only keys that carry a commented `-sbsa` line (currently `PERCEPTION_TAG` / `RTVI_VLM_IMAGE_TAG` in `overrides.env`/`generated.env`). The per-key list also lives in `vss-deploy-profile/references/warehouse.md` (search for "SBSA").

## Step 2 — Dry-run

```bash
cd "${VSS_APPS_DIR}"
docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env \
  config | grep -E '(container_name|profiles:)' | head -80
```

> **Filtering compose noise.** `docker compose config`/`up` prints a `level=warning msg="The \"VAR\" variable is not set. Defaulting to a blank string."` line for every variable that belongs to a profile you're **not** deploying (`EVAL_*`, `LVS_*`, `MILVUS_*`, `GF_*`, `VST_MCP_URL`, …). For MV3DT these are **expected and benign** — they are not a problem. To see only the lines that matter, drop them:
>
> ```bash
> docker compose -f compose.yml --env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env config 2>&1 >/dev/null \
>   | grep -v 'variable is not set'
> # Empty output = no real errors. Anything that still prints here is actionable —
> # e.g. "couldn't find env file: ..." means a path in the combined env layers is wrong; fix generated.env before deploying.
> ```

**Extended** (`MINIMAL_PROFILE=""`) — confirm these are present in addition to the always-deployed core:

- `elasticsearch` + `vss-elasticsearch-init`
- `logstash`
- `kibana` + `vss-kibana-init-mv3dt`
- `vss-video-analytics-api-mv3dt`
- `vss-import-calibration-output-mv3dt`
- `vss-haproxy-ingress`

**Minimal** (`MINIMAL_PROFILE="true"`) — the extended-only services above are absent. Validate service presence rather than relying on a fixed container count, which can change as the service lists evolve.

In both modes, sanity check these MV3DT-core containers are present:

- `vss-rtvi-cv-mv3dt`
- `vss-rtvi-cv-bev-fusion`
- `mosquitto`
- `kafka` *or* `redis`
- `vss-vios-sensor`
- `vss-configurator-mv3dt`
- `vss-vios-nvstreamer-mv3dt`
- `vss-behavior-analytics-mv3dt` (present in both Kafka/Redis MV3DT service lists)

If any of the core are missing, `COMPOSE_PROFILES` is wrong — re-check `MODE` + `BP_PROFILE` + `STREAM_TYPE`.

## Step 3 — Deploy

> **Redeploying? `down -v` alone is not a clean reset.** It resets the named volumes (Kafka log, VST Postgres), but host-side runtime state under `${VSS_DATA_DIR}/data_log` (VST / SDRC / configurator / broker state) is left in place and gets reused — which can leave MV3DT at `Active sources : 0` even though every container is healthy. For a truly fresh redeploy (new dataset, changed camera set/names, or any "stuck at 0 sources" reset), clear **both**:
>
> ```bash
> cd "${VSS_APPS_DIR}"
> # 1. Reset containers + named volumes
> docker compose -f compose.yml --env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env down -v
>
> # 2. Clear host-side data_log — rotate it (non-destructive, keeps a backup):
> ts=$(date +%Y%m%d_%H%M%S)
> mv "${VSS_DATA_DIR}/data_log" "${VSS_DATA_DIR}/data_log.bak.${ts}"
> #    ...or delete in place with the bundled script:
> #    bash scripts/cleanup_all_datalog.sh -e industry-profiles/warehouse-operations/generated.env --skip-revert-from-oldest-backup
>
> # 3. Recreate the data_log subdirs and re-apply the scoped ACLs — see SKILL.md Prerequisites §4
> #    (mkdir the subdirs, then setfacl for UIDs 70/999/1000 — NOT chmod 777).
> ```
>
> Then redeploy (below) and confirm with the **readiness gate** in [`verify-and-view.md`](verify-and-view.md) (Step 4b) — `Active sources == NUM_STREAMS` and growing `mdx-raw`/`mdx-bev` offsets — not just container health. Plain `docker compose down` (no `-v`, no `data_log` clear) is only for restarting against the **same** dataset. Full teardown discussion: [`teardown.md`](teardown.md). For first-time deploys on a clean host, skip this and go straight to the commands below.

```bash
cd "${VSS_APPS_DIR}"

# Re-source .env first, then generated.env, so VSS_DATA_DIR, MINIMAL_PROFILE,
# and NGC_CLI_API_KEY are available to the shell checks below, not only to docker compose.
set -a; . industry-profiles/warehouse-operations/.env; . industry-profiles/warehouse-operations/generated.env; set +a

# NGC login (first time on this host)
docker login --username '$oauthtoken' --password "${NGC_CLI_API_KEY}" nvcr.io

# Fail fast: confirm the key can access the gated vss-core images BEFORE the long background up.
# Refs come from the resolved compose, so this tracks PERCEPTION_TAG / BEV_FUSION_MV3DT_TAG
# (the -sbsa swap, and any PERCEPTION_IMAGE / BEV_FUSION_MV3DT_IMAGE org override) automatically.
# manifest inspect checks registry access only — no layer download — so it stays fast even though
# the perception image is multi-GB (the real pull happens in the backgrounded `up --pull always`).
VSS_CORE_IMAGES=$(docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env config --images \
  | grep -E 'nvcr\.io/.*/vss-core/' | sort -u)
if [ -z "$VSS_CORE_IMAGES" ]; then
  echo "No vss-core images in the resolved compose — confirm MODE=mv3dt and COMPOSE_PROFILES resolved to the MV3DT service list before continuing."
  exit 1
fi
for img in $VSS_CORE_IMAGES; do
  echo "Checking access: $img"
  if ! docker manifest inspect "$img" >/dev/null 2>&1; then
    echo
    echo "NGC login succeeded, but this key does not have access to the required MV3DT image:"
    echo "  $img"
    echo "vss-core is published under nvidia/vss-core for VSS 3.2.0."
    echo "Provide an NGC key with access to the published vss-core artifacts, then retry."
    exit 1
  fi
done

# Extended service list only: create the video-analytics API upload bind before compose
# starts so Docker does not auto-create it with root-only permissions. The import
# one-shot posts calibration.json and Top.png through vss-video-analytics-api-mv3dt,
# which writes them under /web-api-app/files. If this path is not writable, the
# importer can still exit 0 while the API logs EACCES and overlays never appear.
MINIMAL_PROFILE_VAL=$(printf '%s' "${MINIMAL_PROFILE:-}" | tr -d '"')
if [ "${MINIMAL_PROFILE_VAL}" != "true" ]; then
  API_UPLOAD_DIR="${VSS_DATA_DIR:?VSS_DATA_DIR not set}/data_log/vss_video_analytics_api"
  mkdir -p "${API_UPLOAD_DIR}"
  command -v setfacl >/dev/null \
    || { echo "ERROR: setfacl missing; install acl or make ${API_UPLOAD_DIR} writable by container UID 1000"; exit 1; }
  setfacl -R    -m u:1000:rwx "${API_UPLOAD_DIR}"
  setfacl -R -d -m u:1000:rwx "${API_UPLOAD_DIR}"
fi

# Bring up (~10–15 min first run — PERCEPTION image pull + BodyPose3DNet TRT engine build)
LOG=${LOG:-/tmp/mv3dt-deploy.log}
nohup docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env --env-file industry-profiles/warehouse-operations/generated.env \
  up --detach --pull always --force-recreate --build \
  > "$LOG" 2>&1 &
echo "Compose PID $! — logging to $LOG"
```

## Step 4 — Watch the bring-up

Poll every ~60s:

```bash
tail -20 "$LOG"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'mv3dt|mosquitto|kafka|redis|elasticsearch|logstash|kibana|vios|centralizedb|configurator|behavior'
```

Expected first-run timing:

- `vss-rtvi-cv-mv3dt` sits in `(starting)` for 5–10 min while DeepStream builds the BodyPose3DNet TensorRT engine. Tail `docker logs -f vss-rtvi-cv-mv3dt` for `Build engine successfully` lines.
- `vss-rtvi-cv-bev-fusion` reports unhealthy until `/tmp/fusion_ready` is created — the health check probes that sentinel file.
- `vss-broker-health-check` reaches `Exit 0` once the broker is up and topics are seeded. If it stays running, the broker is still booting.
- Under extended: `vss-elasticsearch-init`, `vss-kibana-init-mv3dt`, and `vss-import-calibration-output-mv3dt` are one-shot init containers and reach `Exit 0` after completing — leave them alone.

Once perception logs an FPS line and `/tmp/fusion_ready` exists (check via `docker inspect`), continue to [`verify-and-view.md`](verify-and-view.md).

## When deploy fails

- Image pull 401 / 403 → the Step 3 access check should have caught this before bring-up; if it slips through, re-run `docker login nvcr.io` and verify `ngc registry image list "nvidia/vss-core/*"` returns results.
- `error from registry: Incorrect Repository Format` mid-pull → Docker/Compose version incompatibility with the bare-tag local-build services in `services/infra/compose.yml`. See [`troubleshooting.md`](troubleshooting.md) — "`error from registry: Incorrect Repository Format` during compose pull" for a version-independent pre-build workaround and the Docker-pin alternative.
- `unknown or invalid runtime name: nvidia` → install NVIDIA Container Toolkit (`vss-deploy-profile/references/prerequisites.md` §2.3).
- `redis ... Can't open the log file: Permission denied`, `kafka ... /tmp/kafka-data/cluster_id: Permission denied`, or elasticsearch `AccessDeniedException` → `$VSS_DATA_DIR/data_log` isn't writable by the container UIDs. Run the `mkdir -p` + scoped-ACL permission step from [`../SKILL.md`](../SKILL.md) Prerequisites §4 and redeploy. Don't recursive-chown.
- `vss-configurator-mv3dt` exits 1 immediately → almost always `VSS_DATA_DIR` pointing at the repo instead of the extracted app-data directory. See Step 0 checks.
- Containers in `Created` state forever → almost always the same `VSS_DATA_DIR` issue. Stop everything, fix the active `generated.env`, and redeploy.
- Service-list mismatch (e.g. expected containers not in `docker compose config`) → confirm `MODE=mv3dt`, `BP_PROFILE` is one of `bp_wh_kafka` / `bp_wh_redis`, and `COMPOSE_PROFILES` selects the corresponding MV3DT list. Other failure modes → [`troubleshooting.md`](troubleshooting.md).

When you need to start clean: [`teardown.md`](teardown.md).
