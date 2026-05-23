# Deploy the RTVI-CV-3D (MV3DT) stack

The actual `docker compose up` recipe. Parent: [`../SKILL.md`](../SKILL.md). Run this **after** Q0/Q1/Q2/Q3 in SKILL.md resolved, calibration is on disk (either ship-with-repo for sample, or landed by [`calibration-workflow.md`](calibration-workflow.md), or user-supplied), and [`configure-cameras.md`](configure-cameras.md) has synced `NUM_STREAMS` to the calibration file count.

## What this brings up

`MODE=mv3dt` + `BP_PROFILE=bp_wh_kafka` (or `_redis`) resolves the compose profile to `bp_wh_kafka_mv3dt` (or `bp_wh_redis_mv3dt`). `MINIMAL_PROFILE` then toggles the `_extended` services on top:

### Always deployed (under either profile)

| Container | Image | Role |
|---|---|---|
| `vss-rtvi-cv-mv3dt` | `nvcr.io/nvstaging/vss-core/vss-rt-cv:${PERCEPTION_TAG}` | Per-camera DeepStream perception |
| `vss-rtvi-cv-bev-fusion` | `nvcr.io/nvstaging/vss-core/vss-rt-cv-mv3dt-bev-fusion:${BEV_FUSION_MV3DT_TAG}` | BEV Fusion — fuses per-camera detections to a single BEV frame |
| `mosquitto` | `eclipse-mosquitto:2` | MQTT bus between perception and fusion |
| `kafka` *or* `redis` | (per `STREAM_TYPE`) | Carries `mdx-raw` (input) and `mdx-bev` (output) |
| `broker-health-check` | (built locally) | Validates broker + creates topics |
| `vss-vios-sensor` (`sensor-ms-mv3dt`) | VST sensor image | VST sensor microservice |
| `centralizedb` (PostgreSQL) | postgres | Backing store for VST sensor-ms |
| `vss-configurator-mv3dt` (+ `*-init`) | `nvcr.io/nvstaging/vss-core/vss-configurator` | Sensor registration, DeepStream config materialization |
| `vss-vios-nvstreamer-mv3dt` | nvstreamer | RTSP server for sample/videos data |
| `vss-auto-calibration` (+ `-ui`) | AMC images | Calibration UI on port 5000 (always under `bp_wh_*_mv3dt`) |
| **`vss-behavior-analytics-mv3dt`** | analytics | 3D spatial analytics — always under `bp_wh_*_mv3dt`, **not** gated by `MINIMAL_PROFILE` |

### Extra under extended (`MINIMAL_PROFILE=""`) — needed for VST overlays

| Container | Why |
|---|---|
| `elasticsearch` + `elasticsearch-init-container` | Backing store for the `mdx-bev` index; VST renders overlays only when this is populated |
| `logstash` | Pipes broker metadata → Elasticsearch |
| `kibana` + `vss-kibana-init-mv3dt` | Dashboards (also needed for overlay rendering) |
| `vss-video-analytics-api-mv3dt` | Serves overlay data to VST |
| `vss-import-calibration-output-mv3dt` | Imports the `calibration.json` into Elasticsearch |

In 3.1.0 these were unconditional (no minimal/extended switch existed). In 3.2.0 they share a single `${MINIMAL_PROFILE:+_extended}` gate — i.e. there's no way to enable only a subset.

**Recommendation: default to extended** for any user who wants a complete e2e experience including overlays. Drop to minimal only when explicitly asked for the smallest footprint (edge / Thor / "just give me the topic data").

## Step 0 — Pre-deploy host-path checks

Don't trust `docker compose config` to catch missing bind-mount sources — it doesn't validate host paths. Run these first:

```bash
ENV_FILE="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env"

# Re-source key vars from .env so we can check them
set -a; . "${ENV_FILE}"; set +a

# 1. App-data layout
for sub in videos models data_log; do
  test -d "${VSS_DATA_DIR}/${sub}" || { echo "ERROR: ${VSS_DATA_DIR}/${sub} missing — VSS_DATA_DIR is not pointing at extracted vss-warehouse-app-data"; exit 1; }
done

# 2. Dataset videos
test -d "${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}" \
  || { echo "ERROR: ${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET} missing"; exit 1; }
VIDEO_COUNT=$(ls "${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}/"*.mp4 2>/dev/null | wc -l)
echo "Found ${VIDEO_COUNT} videos under ${VSS_DATA_DIR}/videos/${SAMPLE_VIDEO_DATASET}/"

# 3. Calibration mount
CAL_DIR="${VSS_APPS_DIR}/industry-profiles/warehouse-operations/warehouse-mv3dt-app/calibration/sample-data/${SAMPLE_VIDEO_DATASET}"
test -f "${CAL_DIR}/calibration.json" || { echo "ERROR: ${CAL_DIR}/calibration.json missing"; exit 1; }
CAM_COUNT=$(ls "${CAL_DIR}/camInfo/"*.{yml,yaml} 2>/dev/null | wc -l)
echo "Found ${CAM_COUNT} calibration files under ${CAL_DIR}/camInfo/"

# 4. The configurator enforces min(NUM_STREAMS, HARDWARE_PROFILE.max_streams_supported)
#    and will silently delete excess videos. See SKILL.md Prerequisites §3.
echo "NUM_STREAMS=${NUM_STREAMS}, HARDWARE_PROFILE=${HARDWARE_PROFILE}"
echo "If max_streams_supported for ${HARDWARE_PROFILE}.mv3dt is < ${NUM_STREAMS},"
echo "the configurator will trim videos to that cap at deploy time."
```

If videos < camera count and `HARDWARE_PROFILE.mv3dt.max_streams_supported` < camera count, the deploy will appear to succeed but you'll only get a subset of streams. Fix one of: source missing videos, raise `HARDWARE_PROFILE`-supported cap, or lower expectations.

## Step 1 — Env recipe

Edit `${VSS_APPS_DIR}/industry-profiles/warehouse-operations/.env`. The shipped `.env` defaults to **2D** (`MODE=2d`, `BP_PROFILE=bp_wh`, `HARDWARE_PROFILE=H100`, paths as placeholders, `NGC_CLI_API_KEY=''`) — you must change at least `MODE`, `BP_PROFILE`, paths, `HOST_IP`, and `NGC_CLI_API_KEY` for MV3DT. Confirm every key below:

```bash
# Deployment selectors (line refs are against industry-profiles/warehouse-operations/.env)
MODE=mv3dt                                  # line 45
BP_PROFILE=bp_wh_kafka                      # line 48 — or bp_wh_redis
STREAM_TYPE=kafka                           # line 180 — match BP_PROFILE
MINIMAL_PROFILE=""                          # line 54-55 — EXTENDED (default for overlays)
# MINIMAL_PROFILE="true"                    # uncomment for minimal (no overlays)

# Dataset + stream count
SAMPLE_VIDEO_DATASET="<your-dataset-slug>"  # line 62 — see "Slug" note below
NUM_STREAMS=4                               # line 206 — must equal camInfo count

# Hardware (canonical keys — IGNORE the comment at .env:65 listing "A6000")
HARDWARE_PROFILE=H100                       # line 67 — see SKILL.md Prerequisites §3 table
RT_CV_DEVICE_ID='0'                         # line 69 — GPU for perception
LLM_MODE=none                               # line 81 — no LLM/VLM for MV3DT
VLM_MODE=none                               # line 82

# Paths (REQUIRED)
VSS_APPS_DIR="<repo>/deploy/docker"         # line 131 — your checkout's deploy/docker
VSS_DATA_DIR="<extracted-vss-warehouse-app-data>"  # line 134 — NOT the repo path
HOST_IP='<browser-reachable-IP>'            # line 138 — not localhost
EXTERNAL_IP="${HOST_IP}"

# MQTT (mv3dt only)
MQTT_HOST=localhost                         # line 202
MQTT_PORT=1883                              # line 203

# NGC credential for image pulls
NGC_CLI_API_KEY='<your-ngc-key>'            # line 164
```

`COMPOSE_PROFILES` is computed automatically (line 117): `${BP_PROFILE}_${MODE},llm_${LLM_MODE}_${LLM_NAME_SLUG}` → for MV3DT this resolves to `bp_wh_kafka_mv3dt,llm_none_none`.

### `VSS_DATA_DIR` — what to point it at

This is the directory containing the **extracted** `vss-warehouse-app-data` tarball — **separate from the repo**. Expected layout:

```
<extracted-dir>/
├── videos/<dataset>/        Camera*.mp4 or cam_*.mp4
├── models/mv3dt/BodyPose3DNet/   TRT/onnx weights
├── data_log/                 broker / VST log dir (created at deploy)
└── auto-calib/vggt/          optional VGGT model
```

If you haven't extracted it yet:

```bash
export NGC_CLI_API_KEY='<your-key>'
ngc registry resource download-version "nvidia/vss-warehouse/vss-warehouse-app-data:3.2.0"
# OR: nvstaging/vss-warehouse/vss-warehouse-app-data:v3.2.0-04282026 for staging keys
cd vss-warehouse-app-data_v3.2.0
tar -xvf vss-warehouse-app-data.tar.gz
sudo chmod -R a+rX /path/to/vss-warehouse-app-data
# Then point VSS_DATA_DIR at /path/to/vss-warehouse-app-data
```

> **Known bad-tarball gotcha (2026-05).** Some published versions of `vss-warehouse-app-data` ship `warehouse-4cams-20mx20m-synthetic/` with **fewer videos than the dataset name implies** (e.g. 2 of 4 cameras present). Whether this matters depends on your GPU's `mv3dt` cap (see SKILL.md Prerequisites §3) — if the cap is at or below the present video count, the configurator's `keep_count` op masks the missing files. On a GPU with a higher cap, the missing files become a real deploy issue. Always verify the video count before deploy (the pre-flight check above prints it) and source any missing cams separately if needed.

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

### SBSA (DGX-SPARK / ARM64) note

Swap to `-sbsa` image tags. From the shipped `.env`:

```bash
# PERCEPTION_TAG="3.2.0-sbsa-26.05.1"          # line 192 (uncomment, comment 190)
# BEV_FUSION_MV3DT_TAG="3.2.0-26.05.3-sbsa"     # line 199 (uncomment, comment 197)
```

Apply the same pattern to `RTVI_VLM_IMAGE_TAG`, `VST_*_IMAGE_TAG`, and `NVSTREAMER_IMAGE_TAG` if those keys are set in your `.env`. Per-key list lives in `vss-deploy-profile/references/warehouse.md` (search for "SBSA").

## Step 2 — Dry-run

```bash
cd "${VSS_APPS_DIR}"
docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env \
  config | grep -E '(container_name|profiles:)' | head -80
```

**Extended** (`MINIMAL_PROFILE=""`) — expect ~18–22 `container_name:` entries. Confirm these are present in addition to the always-deployed core:

- `elasticsearch` + `elasticsearch-init-container`
- `logstash`
- `kibana` + `vss-kibana-init-mv3dt`
- `vss-video-analytics-api-mv3dt`
- `vss-import-calibration-output-mv3dt`

**Minimal** (`MINIMAL_PROFILE="true"`) — expect ~12–15 entries; the above five are absent.

In both modes, sanity check these MV3DT-core containers are present:

- `vss-rtvi-cv-mv3dt`
- `vss-rtvi-cv-bev-fusion`
- `mosquitto`
- `kafka` *or* `redis`
- `vss-vios-sensor`
- `vss-configurator-mv3dt`
- `vss-vios-nvstreamer-mv3dt`
- `vss-behavior-analytics-mv3dt` (always under `bp_wh_*_mv3dt`)

If any of the core are missing, `COMPOSE_PROFILES` is wrong — re-check `MODE` + `BP_PROFILE` + `STREAM_TYPE`.

## Step 3 — Deploy

```bash
cd "${VSS_APPS_DIR}"

# NGC login (first time on this host)
docker login --username '$oauthtoken' --password "${NGC_CLI_API_KEY}" nvcr.io

# Bring up (~10–15 min first run — PERCEPTION image pull + BodyPose3DNet TRT engine build)
LOG=${LOG:-/tmp/mv3dt-deploy.log}
nohup docker compose -f compose.yml \
  --env-file industry-profiles/warehouse-operations/.env \
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

First-run gotchas (normal, not bugs):

- `vss-rtvi-cv-mv3dt` sits in `(starting)` for 5–10 min while DeepStream builds the BodyPose3DNet TensorRT engine. Tail `docker logs -f vss-rtvi-cv-mv3dt` for `Build engine successfully` lines.
- `vss-rtvi-cv-bev-fusion` reports unhealthy until `/tmp/fusion_ready` is created (health check on a sentinel file).
- `broker-health-check` should `Exit 0` once the broker is up and topics are seeded. If it stays running, broker is still booting.
- Under extended: `elasticsearch-init-container`, `vss-kibana-init-mv3dt`, `vss-import-calibration-output-mv3dt` all `Exit 0` after one-shot init. That's normal — don't restart them.

Once perception logs an FPS line and `/tmp/fusion_ready` exists (check via `docker inspect`), continue to [`verify-and-view.md`](verify-and-view.md).

## When deploy fails

- Image pull 401 / 403 → re-run `docker login nvcr.io`; verify `ngc registry image list "nvstaging/vss-core/*"` (or `nvidia/vss-core/*`) returns results.
- `unknown or invalid runtime name: nvidia` → install NVIDIA Container Toolkit (`vss-deploy-profile/references/prerequisites.md` §2.3).
- `redis ... Can't open the log file: Permission denied` or `vss-configurator-mv3dt` exits 1 immediately → `VSS_DATA_DIR` is wrong (probably points at the repo dir, not the extracted app-data). See Step 0 checks.
- Containers in `Created` state forever → almost always the same `VSS_DATA_DIR` issue. Stop everything, fix `.env`, redeploy.
- Profile mismatch (e.g. expected containers not in `docker compose config`) → confirm `MODE=mv3dt`, `BP_PROFILE` is one of `bp_wh_kafka` / `bp_wh_redis`. Other failure modes → [`troubleshooting.md`](troubleshooting.md).

When you need to start clean: [`teardown.md`](teardown.md).
